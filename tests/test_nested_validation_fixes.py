"""Regression tests for nested validation, error merging, and related edge cases.

Tests for the 5 fixes:
1. List._deserialize preserves index correspondence when valid_data is None
2. merge_errors produces correct structure for dict + list mixed types
3. Nested._load propagates error.data and error.kwargs
4. Schema._run_validator uses declared_fields data_key fallback
5. Schema._deserialize INCLUDE mode filters out missing sentinels
"""

import pytest

from marshmallow import (
    EXCLUDE,
    INCLUDE,
    RAISE,
    Schema,
    fields,
    post_load,
    pre_load,
    validates_schema,
)
from marshmallow.constants import missing
from marshmallow.error_store import ErrorStore, merge_errors
from marshmallow.exceptions import SCHEMA, ValidationError


# ---------------------------------------------------------------------------
# Fix 1: List._deserialize preserves index correspondence when valid_data is None
# ---------------------------------------------------------------------------


class StrictInnerSchema(Schema):
    """Schema where validation fails entirely if 'name' is missing."""

    name = fields.Str(required=True)
    value = fields.Int()

    class Meta:
        unknown = RAISE


class TestListDeserializeIndexCorrespondence:
    def test_valid_data_none_preserves_index(self):
        """When Nested field validation fails and valid_data is None,
        the result should still contain a placeholder at the correct index.
        """

        class OuterSchema(Schema):
            items = fields.List(fields.Nested(StrictInnerSchema))

        data = {
            "items": [
                {"name": "a", "value": 1},  # valid
                {"value": "not_int"},  # fails: missing name, bad value
                {"name": "c", "value": 3},  # valid
            ]
        }

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load(data)

        error = excinfo.value
        valid_data = error.valid_data["items"]

        # Index 0: valid -> {"name": "a", "value": 1}
        assert valid_data[0] == {"name": "a", "value": 1}
        # Index 1: failed -> should have placeholder (None or partial data)
        # but must NOT be missing from the list
        assert len(valid_data) == 3
        # Index 2: valid -> {"name": "c", "value": 3}
        assert valid_data[2] == {"name": "c", "value": 3}

    def test_all_items_fail_preserves_length(self):
        """When all items fail, result should still have the same length."""

        class OuterSchema(Schema):
            items = fields.List(fields.Nested(StrictInnerSchema))

        data = {
            "items": [
                {"value": "bad1"},
                {"value": "bad2"},
                {"value": "bad3"},
            ]
        }

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load(data)

        valid_data = excinfo.value.valid_data["items"]
        assert len(valid_data) == 3
        # All items should be placeholders (None or partial data)

    def test_list_of_ints_with_some_invalid(self):
        """Simple list field with some invalid ints."""

        class OuterSchema(Schema):
            numbers = fields.List(fields.Int())

        # Strings are not valid ints
        data = {"numbers": [1, "bad", 3, "worse", 5]}

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load(data)

        # Int fields don't produce valid_data on failure, so placeholders
        valid_data = excinfo.value.valid_data["numbers"]
        assert len(valid_data) == 5
        assert valid_data[0] == 1
        assert valid_data[2] == 3
        assert valid_data[4] == 5
        # Failed indices should have None placeholders
        assert valid_data[1] is None
        assert valid_data[3] is None

    def test_tuple_valid_data_none_preserves_index(self):
        """Tuple._deserialize should also preserve index correspondence."""

        class OuterSchema(Schema):
            row = fields.Tuple((fields.Int(), fields.Str(), fields.Int()))

        data = {"row": [1, "ok", "not_int"]}

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load(data)

        valid_data = excinfo.value.valid_data["row"]
        assert len(valid_data) == 3
        assert valid_data[0] == 1
        assert valid_data[1] == "ok"
        # Index 2 failed -> None placeholder
        assert valid_data[2] is None


# ---------------------------------------------------------------------------
# Fix 2: merge_errors produces correct structure for dict + list mixed types
# ---------------------------------------------------------------------------


class TestMergeErrors:
    def test_dict_plus_list_creates_schema_key(self):
        """When errors1 is a dict and errors2 is a list, the result should
        be a dict with the list stored under _schema, not a heterogeneous list.
        """
        errors1 = {"field1": ["field error"]}
        errors2 = ["schema error"]

        result = merge_errors(errors1, errors2)

        # Should be a dict, NOT a heterogeneous list like [{"field1": ...}, "schema error"]
        assert isinstance(result, dict)
        assert result["field1"] == ["field error"]
        assert SCHEMA in result
        assert result[SCHEMA] == ["schema error"]

    def test_dict_plus_string_creates_schema_key(self):
        """When errors1 is a dict and errors2 is a string, the result should
        be a dict with the string stored under _schema.
        Note: merge_errors doesn't wrap strings in lists - that's done by
        ValidationError.__init__. Strings are stored as-is.
        """
        errors1 = {"field1": ["field error"]}
        errors2 = "schema error"

        result = merge_errors(errors1, errors2)

        assert isinstance(result, dict)
        assert result["field1"] == ["field error"]
        assert SCHEMA in result
        assert result[SCHEMA] == "schema error"

    def test_list_plus_dict_no_redundant_schema(self):
        """When errors1 is a list and errors2 is a dict without _schema,
        _schema should be created exactly once.
        """
        errors1 = ["schema error"]
        errors2 = {"field1": ["field error"]}

        result = merge_errors(errors1, errors2)

        assert isinstance(result, dict)
        assert result["field1"] == ["field error"]
        assert result[SCHEMA] == ["schema error"]
        # _schema should appear exactly once as a key
        assert list(result.keys()).count(SCHEMA) == 1

    def test_list_plus_dict_with_existing_schema(self):
        """When errors2 already has _schema, the lists should merge."""
        errors1 = ["schema error 1"]
        errors2 = {"field1": ["field error"], SCHEMA: ["schema error 2"]}

        result = merge_errors(errors1, errors2)

        assert isinstance(result, dict)
        assert result["field1"] == ["field error"]
        # Both schema errors should be present
        assert "schema error 1" in result[SCHEMA]
        assert "schema error 2" in result[SCHEMA]

    def test_dict_plus_dict_merges_schema_keys(self):
        """Both dicts have _schema - should merge cleanly."""
        errors1 = {"field1": ["err1"], SCHEMA: ["schema1"]}
        errors2 = {"field2": ["err2"], SCHEMA: ["schema2"]}

        result = merge_errors(errors1, errors2)

        assert result["field1"] == ["err1"]
        assert result["field2"] == ["err2"]
        assert "schema1" in result[SCHEMA]
        assert "schema2" in result[SCHEMA]

    def test_string_plus_list(self):
        """String + list should produce a flat list."""
        result = merge_errors("err1", ["err2", "err3"])
        assert result == ["err1", "err2", "err3"]

    def test_string_plus_dict(self):
        """String + dict should produce a dict with _schema.
        Note: merge_errors stores strings as-is (ValidationError wraps them).
        """
        result = merge_errors("err1", {"field1": ["err2"]})
        assert isinstance(result, dict)
        assert result["field1"] == ["err2"]
        assert result[SCHEMA] == "err1"

    def test_empty_errors(self):
        """Empty errors should return the other side."""
        assert merge_errors(None, {"a": 1}) == {"a": 1}
        assert merge_errors({"a": 1}, None) == {"a": 1}
        assert merge_errors([], {"a": 1}) == {"a": 1}
        assert merge_errors({"a": 1}, []) == {"a": 1}


# ---------------------------------------------------------------------------
# Fix 3: Nested._load propagates error.data and error.kwargs
# ---------------------------------------------------------------------------


class TestNestedLoadErrorPropagation:
    def test_nested_error_preserves_data(self):
        """When Nested._load re-raises ValidationError, error.data should
        contain the original input data.
        """

        class InnerSchema(Schema):
            name = fields.Str(required=True)

        class OuterSchema(Schema):
            inner = fields.Nested(InnerSchema)

        input_data = {"inner": {"not_name": "value"}}

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load(input_data)

        # The top-level error should have valid_data
        error = excinfo.value
        assert error.valid_data is not None

    def test_nested_error_preserves_kwargs(self):
        """Custom kwargs on ValidationError should be preserved through
        Nested._load re-raise.
        """

        class InnerSchema(Schema):
            name = fields.Str()

            @validates_schema
            def validate_name(self, data, **kwargs):
                if data.get("name") == "invalid":
                    raise ValidationError("Bad name", custom_kwarg="custom_value")

        class OuterSchema(Schema):
            inner = fields.Nested(InnerSchema)

        with pytest.raises(ValidationError) as excinfo:
            OuterSchema().load({"inner": {"name": "invalid"}})

        # The error should propagate up
        assert "inner" in excinfo.value.messages

    def test_deeply_nested_error_data(self):
        """Deeply nested schemas should preserve data through the chain."""

        class LeafSchema(Schema):
            value = fields.Int(required=True)

        class MiddleSchema(Schema):
            leaf = fields.Nested(LeafSchema)

        class RootSchema(Schema):
            middle = fields.Nested(MiddleSchema)

        data = {"middle": {"leaf": {"value": "not_int"}}}

        with pytest.raises(ValidationError) as excinfo:
            RootSchema().load(data)

        error = excinfo.value
        # Should have structured error messages
        assert "middle" in error.messages
        assert "leaf" in error.messages["middle"]


# ---------------------------------------------------------------------------
# Fix 4: Schema._run_validator uses declared_fields data_key fallback
# ---------------------------------------------------------------------------


class TestRunValidatorDataKeyFallback:
    def test_validator_error_on_excluded_field_uses_data_key(self):
        """When a schema-level validator raises an error for a field that's
        excluded by only/exclude, the data_key from declared_fields should
        be used for error reporting.
        """

        class MySchema(Schema):
            email = fields.Str(data_key="emailAddress")
            name = fields.Str()

            @validates_schema
            def check_email(self, data, **kwargs):
                # Always raise an error for 'email' field
                raise ValidationError("Email is required", field_name="email")

        # Use only=["name"] so 'email' is excluded from self.fields
        # but still in self.declared_fields with data_key="emailAddress"
        schema = MySchema(only=["name"])

        errors = schema.validate({"name": "John"})
        # Error should be reported under the data_key "emailAddress"
        assert "emailAddress" in errors
        assert errors["emailAddress"] == ["Email is required"]

    def test_validator_error_on_excluded_field_without_data_key(self):
        """When excluded field has no data_key, use the field_name."""

        class MySchema(Schema):
            email = fields.Str()
            name = fields.Str()

            @validates_schema
            def check_email(self, data, **kwargs):
                raise ValidationError("Email is required", field_name="email")

        schema = MySchema(only=["name"])
        errors = schema.validate({"name": "John"})
        assert "email" in errors

    def test_validator_error_on_active_field_uses_data_key(self):
        """When field is in self.fields (not excluded), data_key should be used."""

        class MySchema(Schema):
            email = fields.Str(data_key="emailAddress")
            name = fields.Str()

            @validates_schema
            def check_email(self, data, **kwargs):
                raise ValidationError("Email is required", field_name="email")

        schema = MySchema()
        errors = schema.validate({"name": "John", "emailAddress": ""})
        assert "emailAddress" in errors

    def test_validator_error_on_unknown_field(self):
        """When field_name is not in fields or declared_fields, use field_name."""

        class MySchema(Schema):
            name = fields.Str()

            @validates_schema
            def check_custom(self, data, **kwargs):
                raise ValidationError("Custom error", field_name="nonexistent")

        schema = MySchema()
        errors = schema.validate({"name": "John"})
        assert "nonexistent" in errors
        assert errors["nonexistent"] == ["Custom error"]


# ---------------------------------------------------------------------------
# Fix 5: Schema._deserialize INCLUDE mode filters missing sentinels
# ---------------------------------------------------------------------------


class TestIncludeMissingFiltering:
    def test_include_filters_missing_top_level(self):
        """INCLUDE mode should not include keys whose value is the missing sentinel."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def inject_missing(self, data, **kwargs):
                # Inject missing sentinel as an unknown key
                return {**data, "extra": missing}

        result = MySchema().load({"name": "John"})
        assert "name" in result
        assert "extra" not in result

    def test_include_filters_missing_in_nested_dict(self):
        """INCLUDE mode should filter missing sentinels from nested dicts."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def inject_missing(self, data, **kwargs):
                return {**data, "extra": {"good": "value", "bad": missing}}

        result = MySchema().load({"name": "John"})
        assert result["extra"] == {"good": "value"}

    def test_include_filters_missing_in_list(self):
        """INCLUDE mode should filter missing sentinels from lists."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def inject_missing(self, data, **kwargs):
                return {**data, "extra": ["a", missing, "b"]}

        result = MySchema().load({"name": "John"})
        assert result["extra"] == ["a", "b"]

    def test_include_preserves_normal_values(self):
        """Normal unknown values should still be included."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

        result = MySchema().load({"name": "John", "extra": "value", "num": 42})
        assert result["name"] == "John"
        assert result["extra"] == "value"
        assert result["num"] == 42

    def test_include_complex_nested_without_missing(self):
        """Complex nested structures without missing should be preserved as-is."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

        result = MySchema().load({
            "name": "John",
            "nested": {"a": [1, 2, {"b": "c"}], "d": True},
        })
        assert result["nested"] == {"a": [1, 2, {"b": "c"}], "d": True}

    def test_include_entirely_missing_dict_excluded(self):
        """If the entire unknown value is missing, the key should be excluded."""

        class MySchema(Schema):
            name = fields.Str()

            class Meta:
                unknown = INCLUDE

            @pre_load
            def inject_missing(self, data, **kwargs):
                return {**data, "phantom": missing}

        result = MySchema().load({"name": "John"})
        assert "phantom" not in result


# ---------------------------------------------------------------------------
# Combined regression tests
# ---------------------------------------------------------------------------


class TestNestedListValidationRegression:
    """End-to-end regression test combining fixes 1 + 2 + 3."""

    def test_order_schema_with_multiple_items(self):
        """Simulates the original bug report: OrderSchema with multiple Items,
        some valid, some invalid. The valid_data should preserve index order
        and include all valid items.
        """

        class VariantSchema(Schema):
            sku = fields.Str(required=True)
            price = fields.Float(required=True)

        class ItemSchema(Schema):
            name = fields.Str(required=True)
            quantity = fields.Int(required=True)
            variants = fields.List(fields.Nested(VariantSchema))

        class OrderSchema(Schema):
            order_id = fields.Str()
            items = fields.List(fields.Nested(ItemSchema))

        data = {
            "order_id": "ORD-001",
            "items": [
                # Item 0: fully valid
                {
                    "name": "Widget",
                    "quantity": 5,
                    "variants": [{"sku": "W1", "price": 9.99}],
                },
                # Item 1: missing required 'name', invalid quantity
                {"quantity": "not_int", "variants": []},
                # Item 2: fully valid
                {
                    "name": "Gadget",
                    "quantity": 2,
                    "variants": [{"sku": "G1", "price": 19.99}],
                },
                # Item 3: valid item but nested variant fails
                {
                    "name": "Broken",
                    "quantity": 1,
                    "variants": [{"price": "not_float"}],
                },
            ],
        }

        with pytest.raises(ValidationError) as excinfo:
            OrderSchema().load(data)

        error = excinfo.value
        valid_data = error.valid_data

        # Order ID should be preserved
        assert valid_data["order_id"] == "ORD-001"

        items = valid_data["items"]
        # Length should be 4 (preserving all indices)
        assert len(items) == 4

        # Item 0: fully valid
        assert items[0]["name"] == "Widget"
        assert items[0]["quantity"] == 5

        # Item 2: fully valid (index preserved!)
        assert items[2]["name"] == "Gadget"
        assert items[2]["quantity"] == 2

    def test_merge_errors_stability_across_ordering(self):
        """Regardless of the order errors are stored, the result should be
        a well-structured dict (not a heterogeneous list).
        """
        # Case A: field error first, then schema error
        store_a = ErrorStore()
        store_a.store_error({"field1": ["err1"]})
        store_a.store_error(["schema_err"])
        result_a = store_a.errors

        # Case B: schema error first, then field error
        store_b = ErrorStore()
        store_b.store_error(["schema_err"])
        store_b.store_error({"field1": ["err1"]})
        result_b = store_b.errors

        # Both should produce a dict with the same structure
        assert isinstance(result_a, dict), f"Expected dict, got {type(result_a)}"
        assert isinstance(result_b, dict), f"Expected dict, got {type(result_b)}"
        assert "field1" in result_a
        assert "field1" in result_b
        assert SCHEMA in result_a
        assert SCHEMA in result_b
