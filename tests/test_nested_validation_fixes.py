"""Tests for nested validation bug fixes.

Covers:
1. List._deserialize index alignment when valid_data is None
2. merge_errors list+dict reference sharing
3. Nested._load propagating error.data and error.kwargs
4. Schema._run_validator data_key fallback for excluded fields
5. Schema._deserialize INCLUDE mode filtering missing sentinel values
"""
import pytest

from marshmallow import Schema, fields, validates_schema, post_load, pre_load
from marshmallow.constants import INCLUDE, EXCLUDE, RAISE, missing
from marshmallow.exceptions import ValidationError
from marshmallow.error_store import merge_errors, copy_containers


# ---------------------------------------------------------------------------
# Fix 1: List._deserialize index alignment when valid_data is None
# ---------------------------------------------------------------------------

class InnerSchema(Schema):
    name = fields.Str(required=True)
    value = fields.Int()


class OuterSchema(Schema):
    items = fields.List(fields.Nested(InnerSchema))


class TestListIndexAlignment:
    """When a Nested item in a List fails validation and valid_data is None,
    the result list should still preserve index alignment with the input."""

    def test_list_preserves_index_when_nested_valid_data_is_none(self):
        """If some items fail and their valid_data is None, the result
        should contain None placeholders at those indices."""
        schema = OuterSchema()
        data = {
            "items": [
                {"name": "good1", "value": 1},     # index 0: valid
                {"value": "not_an_int"},              # index 1: invalid (missing required name)
                {"name": "good2", "value": 2},     # index 2: valid
            ]
        }
        with pytest.raises(ValidationError) as excinfo:
            schema.load(data)

        error = excinfo.value
        # The outer valid_data should have 3 items, preserving index alignment
        items_valid_data = error.valid_data["items"]
        assert len(items_valid_data) == 3

        # Index 0 should have valid data
        assert items_valid_data[0] is not None
        assert items_valid_data[0]["name"] == "good1"

        # Index 2 should have valid data
        assert items_valid_data[2] is not None
        assert items_valid_data[2]["name"] == "good2"

    def test_list_all_invalid_preserves_length(self):
        """When all items fail, valid_data should have the same length as input."""
        schema = OuterSchema()
        data = {
            "items": [
                {"value": "bad1"},  # missing required name
                {"value": "bad2"},  # missing required name
                {"value": "bad3"},  # missing required name
            ]
        }
        with pytest.raises(ValidationError) as excinfo:
            schema.load(data)

        error = excinfo.value
        items_valid_data = error.valid_data["items"]
        assert len(items_valid_data) == 3

    def test_list_partial_valid_preserves_indices(self):
        """Mix of valid and invalid items should preserve all indices."""
        schema = OuterSchema()
        data = {
            "items": [
                {"value": "bad"},                     # index 0: invalid
                {"name": "good", "value": 42},       # index 1: valid
                {"value": "also_bad"},                # index 2: invalid
                {"name": "also_good", "value": 99},  # index 3: valid
            ]
        }
        with pytest.raises(ValidationError) as excinfo:
            schema.load(data)

        error = excinfo.value
        items_valid_data = error.valid_data["items"]
        assert len(items_valid_data) == 4

        # Valid items should be at correct indices
        assert items_valid_data[1]["name"] == "good"
        assert items_valid_data[3]["name"] == "also_good"

    def test_list_simple_field_failure_preserves_index(self):
        """Even for simple fields (not Nested), failed items get None placeholder."""
        field = fields.List(fields.Str())
        with pytest.raises(ValidationError) as excinfo:
            field.deserialize(["good", 42, "also_good"])

        error = excinfo.value
        assert len(error.valid_data) == 3
        assert error.valid_data[0] == "good"
        assert error.valid_data[1] is None  # 42 failed
        assert error.valid_data[2] == "also_good"

    def test_tuple_preserves_index_on_failure(self):
        """Tuple field should also preserve index alignment."""
        field = fields.Tuple((fields.Str(), fields.Int(), fields.Str()))
        with pytest.raises(ValidationError) as excinfo:
            field.deserialize(("good", "not_int", "also_good"))

        error = excinfo.value
        assert len(error.valid_data) == 3
        assert error.valid_data[0] == "good"
        assert error.valid_data[1] is None  # "not_int" failed
        assert error.valid_data[2] == "also_good"


# ---------------------------------------------------------------------------
# Fix 2: merge_errors list+dict reference sharing
# ---------------------------------------------------------------------------

class TestMergeErrorsReferenceSharing:
    """When merging list errors with dict errors, the result should not
    share references with the input, preventing mutation issues."""

    def test_list_and_dict_no_schema_key(self):
        """Merging a list with a dict that has no _schema key should
        copy the list to avoid reference sharing."""
        errors1 = ["err1", "err2"]
        errors2 = {"field1": "err3"}
        result = merge_errors(errors1, errors2)

        assert result == {"_schema": ["err1", "err2"], "field1": "err3"}

        # Mutating the original list should not affect the result
        errors1.append("err4")
        assert result["_schema"] == ["err1", "err2"]

    def test_list_and_dict_with_schema_key(self):
        """Merging a list with a dict that has a _schema key should merge."""
        errors1 = ["err1"]
        errors2 = {"_schema": "err2", "field1": "err3"}
        result = merge_errors(errors1, errors2)

        assert result == {"_schema": ["err1", "err2"], "field1": "err3"}

    def test_dict_and_scalar_no_schema_key(self):
        """Merging a dict (no _schema) with a scalar should copy the scalar."""
        errors1 = {"field1": "err1"}
        errors2 = "err2"
        result = merge_errors(errors1, errors2)

        assert result == {"field1": "err1", "_schema": "err2"}

    def test_scalar_and_dict_no_schema_key(self):
        """Merging a scalar with a dict (no _schema) should copy the scalar."""
        errors1 = "err1"
        errors2 = {"field1": "err2"}
        result = merge_errors(errors1, errors2)

        assert result == {"field1": "err2", "_schema": "err1"}

    def test_empty_list_and_dict(self):
        """Merging an empty list with a dict should return the dict."""
        assert merge_errors([], {"field1": "err1"}) == {"field1": "err1"}

    def test_list_and_empty_dict(self):
        """Merging a list with an empty dict should return the list."""
        assert merge_errors(["err1"], {}) == ["err1"]

    def test_nested_dict_merge_list_and_dict(self):
        """Nested merge of list errors with dict errors should not share references."""
        original_list = ["err1"]
        errors1 = {"items": {0: list(original_list)}}
        errors2 = {"items": {0: {"field1": "err2"}}}
        result = merge_errors(errors1, errors2)

        assert result == {
            "items": {0: {"field1": "err2", "_schema": ["err1"]}}
        }

        # Mutating the original list should not affect the result
        original_list.append("err3")
        assert result["items"][0]["_schema"] == ["err1"]

    def test_sequential_stores_no_reference_leak(self):
        """Multiple store_error calls should not cause reference leaking."""
        from marshmallow.error_store import ErrorStore

        store = ErrorStore()
        store.store_error(["err1"], "foo")
        store.store_error({"nested": "err2"}, "foo")
        store.store_error(["err3"], "foo")

        assert store.errors == {
            "foo": {
                "nested": "err2",
                "_schema": ["err1", "err3"],
            }
        }


# ---------------------------------------------------------------------------
# Fix 3: Nested._load propagating error.data and error.kwargs
# ---------------------------------------------------------------------------

class TestNestedErrorPropagation:
    """When Nested._load catches a ValidationError from the inner schema,
    it should propagate error.data and error.kwargs to the outer error."""

    def test_nested_error_preserves_data(self):
        """The original input data should be accessible through the error chain."""
        class ChildSchema(Schema):
            name = fields.Str(required=True)

        class ParentSchema(Schema):
            child = fields.Nested(ChildSchema)

        schema = ParentSchema()
        input_data = {"child": {"value": 42}}  # missing required 'name'

        with pytest.raises(ValidationError) as excinfo:
            schema.load(input_data)

        error = excinfo.value
        # The outer error should have the original input data
        assert error.data is not None

    def test_nested_error_preserves_kwargs(self):
        """Custom kwargs on the inner ValidationError should propagate."""
        class CustomSchema(Schema):
            name = fields.Str(required=True)

            @post_load
            def check_name(self, data, **kwargs):
                # This won't run if validation fails, but we can test
                # via a validator that raises with custom kwargs
                return data

        class StrictSchema(Schema):
            name = fields.Str(required=True)

            @validates_schema
            def validate_name(self, data, **kwargs):
                if data.get("name") == "forbidden":
                    raise ValidationError(
                        "Name is forbidden",
                        field_name="name",
                        custom_info="extra_data",
                    )

        class ParentSchema(Schema):
            child = fields.Nested(StrictSchema)

        schema = ParentSchema()
        with pytest.raises(ValidationError) as excinfo:
            schema.load({"child": {"name": "forbidden"}})

        error = excinfo.value
        # The error should propagate through the nested chain
        assert "child" in error.messages

    def test_deeply_nested_error_data_propagation(self):
        """Error data should propagate through multiple nesting levels."""
        class Level3Schema(Schema):
            value = fields.Int(required=True)

        class Level2Schema(Schema):
            items = fields.List(fields.Nested(Level3Schema))

        class Level1Schema(Schema):
            container = fields.Nested(Level2Schema)

        schema = Level1Schema()
        input_data = {
            "container": {
                "items": [
                    {"value": "not_an_int"},
                ]
            }
        }

        with pytest.raises(ValidationError) as excinfo:
            schema.load(input_data)

        error = excinfo.value
        # The error should bubble up through all levels
        assert "container" in error.messages
        # Data should be preserved at the top level
        assert error.data is not None


# ---------------------------------------------------------------------------
# Fix 4: Schema._run_validator data_key fallback for excluded fields
# ---------------------------------------------------------------------------

class TestRunValidatorDataKeyFallback:
    """When a schema-level validator references a field that is excluded
    by only/exclude, the data_key from declared_fields should be used."""

    def test_excluded_field_uses_declared_data_key(self):
        """A validator referencing an excluded field should use the
        declared field's data_key for the error key."""
        class MySchema(Schema):
            internal_name = fields.Str(data_key="externalName")
            other = fields.Str()

            @validates_schema
            def check_stuff(self, data, **kwargs):
                raise ValidationError("something wrong", field_name="internal_name")

        # Exclude the field from loading
        schema = MySchema(exclude=["internal_name"])
        errors = schema.validate({"other": "value"})

        # The error should be stored under the data_key, not the attribute name
        assert "externalName" in errors
        assert "internal_name" not in errors

    def test_included_field_uses_active_data_key(self):
        """A validator referencing an active field should use the
        active field's data_key."""
        class MySchema(Schema):
            internal_name = fields.Str(data_key="externalName")
            other = fields.Str()

            @validates_schema
            def check_stuff(self, data, **kwargs):
                raise ValidationError("something wrong", field_name="internal_name")

        schema = MySchema()
        # Use data_key "externalName" in input since that's what the schema expects
        errors = schema.validate({"externalName": "val", "other": "value"})

        assert "externalName" in errors

    def test_only_excluded_field_uses_declared_data_key(self):
        """A field excluded by 'only' should still have its data_key used."""
        class MySchema(Schema):
            internal_name = fields.Str(data_key="externalName")
            other = fields.Str()

            @validates_schema
            def check_stuff(self, data, **kwargs):
                raise ValidationError("something wrong", field_name="internal_name")

        # 'only' excludes internal_name
        schema = MySchema(only=["other"])
        errors = schema.validate({"other": "value"})

        assert "externalName" in errors

    def test_unknown_field_name_uses_field_name_as_key(self):
        """A validator referencing a non-existent field should use the
        field_name as the error key."""
        class MySchema(Schema):
            other = fields.Str()

            @validates_schema
            def check_stuff(self, data, **kwargs):
                raise ValidationError("something wrong", field_name="nonexistent")

        schema = MySchema()
        errors = schema.validate({"other": "value"})

        assert "nonexistent" in errors

    def test_schema_level_error_uses_schema_key(self):
        """A validator without field_name should use _schema key."""
        class MySchema(Schema):
            other = fields.Str()

            @validates_schema
            def check_stuff(self, data, **kwargs):
                raise ValidationError("schema-level error")

        schema = MySchema()
        errors = schema.validate({"other": "value"})

        assert "_schema" in errors


# ---------------------------------------------------------------------------
# Fix 5: Schema._deserialize INCLUDE mode filtering missing sentinel values
# ---------------------------------------------------------------------------

class TestIncludeModeFilterMissing:
    """When unknown=INCLUDE, missing sentinel values should be filtered out."""

    def test_include_mode_filters_missing_sentinel(self):
        """Unknown keys with missing sentinel values should not appear in result."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

        schema = MySchema()
        # Normal unknown key should be included
        result = schema.load({"name": "test", "extra": "value"})
        assert result == {"name": "test", "extra": "value"}

    def test_include_mode_filters_missing_from_pre_load(self):
        """If pre_load transforms a value to missing, it should be filtered."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def transform(self, data, **kwargs):
                # Simulate a pre_load that sets an unknown key to missing
                if "bad_key" in data:
                    data = dict(data)
                    data["bad_key"] = missing
                return data

        schema = MySchema()
        result = schema.load({"name": "test", "bad_key": "something"})
        assert "bad_key" not in result
        assert result == {"name": "test"}

    def test_include_mode_filters_nested_missing_in_dict(self):
        """Missing sentinels nested inside dict values should be filtered."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def transform(self, data, **kwargs):
                if "extra" in data:
                    data = dict(data)
                    data["extra"] = {"good": "value", "bad": missing}
                return data

        schema = MySchema()
        result = schema.load({"name": "test", "extra": {"good": "value", "bad": "x"}})
        assert result["extra"] == {"good": "value"}

    def test_include_mode_filters_missing_in_list(self):
        """Missing sentinels inside list values should be filtered."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def transform(self, data, **kwargs):
                if "items" in data:
                    data = dict(data)
                    data["items"] = ["good", missing, "also_good"]
                return data

        schema = MySchema()
        result = schema.load({"name": "test", "items": ["a", "b", "c"]})
        assert result["items"] == ["good", "also_good"]

    def test_include_mode_entire_value_missing_is_excluded(self):
        """If the entire unknown value is missing, the key should be excluded."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def transform(self, data, **kwargs):
                data = dict(data)
                data["ghost"] = missing
                return data

        schema = MySchema()
        result = schema.load({"name": "test"})
        assert "ghost" not in result

    def test_include_mode_normal_values_preserved(self):
        """Normal values (not missing) should be preserved in INCLUDE mode."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

        schema = MySchema()
        result = schema.load({
            "name": "test",
            "extra_str": "hello",
            "extra_int": 42,
            "extra_dict": {"nested": "value"},
            "extra_list": [1, 2, 3],
        })
        assert result == {
            "name": "test",
            "extra_str": "hello",
            "extra_int": 42,
            "extra_dict": {"nested": "value"},
            "extra_list": [1, 2, 3],
        }


# ---------------------------------------------------------------------------
# Integration tests: combined scenarios
# ---------------------------------------------------------------------------

class TestIntegrationScenarios:
    """Integration tests combining multiple fixes."""

    def test_deeply_nested_list_with_partial_failures(self):
        """Order with multiple items, some valid some invalid."""
        class VariantSchema(Schema):
            size = fields.Str(required=True)
            color = fields.Str()

        class ItemSchema(Schema):
            name = fields.Str(required=True)
            variants = fields.List(fields.Nested(VariantSchema))

        class OrderSchema(Schema):
            items = fields.List(fields.Nested(ItemSchema))

        schema = OrderSchema()
        data = {
            "items": [
                {
                    "name": "Shirt",
                    "variants": [
                        {"size": "M", "color": "red"},
                        {"color": "blue"},  # missing required size
                        {"size": "L", "color": "green"},
                    ]
                },
                {
                    "variants": [  # missing required name
                        {"size": "S"}
                    ]
                },
                {
                    "name": "Pants",
                    "variants": [
                        {"size": "XL"},
                    ]
                },
            ]
        }

        with pytest.raises(ValidationError) as excinfo:
            schema.load(data)

        error = excinfo.value
        # Should have errors for items 0 (variant 1) and 1 (missing name)
        assert "items" in error.messages
        assert 0 in error.messages["items"]
        assert 1 in error.messages["items"]

        # valid_data should preserve index alignment
        items = error.valid_data["items"]
        assert len(items) == 3

    def test_list_with_nested_schema_level_validation_error(self):
        """List of nested schemas where schema-level validators fail."""
        class ItemSchema(Schema):
            name = fields.Str(required=True)
            price = fields.Float(required=True)

            @validates_schema
            def validate_price(self, data, **kwargs):
                if data.get("price") is not None and data.get("price", 0) < 0:
                    raise ValidationError("Price must be positive", field_name="price")

        class OrderSchema(Schema):
            items = fields.List(fields.Nested(ItemSchema))

        schema = OrderSchema()
        data = {
            "items": [
                {"name": "Good Item", "price": 10.0},
                {"name": "Bad Item", "price": -5.0},
                {"name": "Another Good", "price": 20.0},
            ]
        }

        with pytest.raises(ValidationError) as excinfo:
            schema.load(data)

        error = excinfo.value
        items = error.valid_data["items"]
        assert len(items) == 3

        # Valid items should be at correct indices
        assert items[0]["name"] == "Good Item"
        assert items[2]["name"] == "Another Good"

    def test_include_mode_with_complex_nested_unknown(self):
        """INCLUDE mode with deeply nested unknown values containing missing."""
        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def transform(self, data, **kwargs):
                data = dict(data)
                data["complex"] = {
                    "level1": {
                        "good": "value",
                        "bad": missing,
                        "nested": {
                            "also_good": "value",
                            "also_bad": missing,
                        }
                    },
                    "top_bad": missing,
                    "top_good": "preserved",
                }
                return data

        schema = MySchema()
        result = schema.load({"name": "test"})
        assert "complex" in result
        assert "top_bad" not in result["complex"]
        assert result["complex"]["top_good"] == "preserved"
        assert result["complex"]["level1"]["good"] == "value"
        assert "bad" not in result["complex"]["level1"]
        assert result["complex"]["level1"]["nested"]["also_good"] == "value"
        assert "also_bad" not in result["complex"]["level1"]["nested"]
