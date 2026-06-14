# Nested Validation Bug Fixes - Summary

## Overview
Fixed 5 bugs related to nested schema validation in marshmallow, focusing on index alignment, error propagation, and data integrity.

## Changes Made

### 1. List._deserialize Index Alignment (fields.py)
**Problem**: When list items failed validation and `valid_data` was None, those items were skipped, breaking index alignment between input and output.

**Fix**: Always append `valid_data` (even if None) to preserve index correspondence.

**Files Changed**:
- `src/marshmallow/fields.py`: Lines 790-791 (List._deserialize), Lines 884-885 (Tuple._deserialize)

**Code Change**:
```python
# Before
if error.valid_data is not None:
    result.append(error.valid_data)

# After
result.append(error.valid_data)  # Always append, even if None
```

### 2. merge_errors Reference Sharing (error_store.py)
**Problem**: When merging list errors with dict errors (without _schema key), the code used `errors2.get(SCHEMA)` which could return None, then called `merge_errors(errors1, None)` which returned the same reference, causing potential mutation issues.

**Fix**: Check if SCHEMA key exists before merging, and use `copy_containers()` to avoid reference sharing.

**Files Changed**:
- `src/marshmallow/error_store.py`: Lines 52-53, 64-65, 69-70

**Code Change**:
```python
# Before
errors2[SCHEMA] = merge_errors(errors1, errors2.get(SCHEMA))

# After
if SCHEMA in errors2:
    errors2[SCHEMA] = merge_errors(errors1, errors2[SCHEMA])
else:
    errors2[SCHEMA] = copy_containers(errors1)
```

### 3. Nested._load Error Propagation (fields.py)
**Problem**: When Nested._load caught a ValidationError, it only re-raised with `messages` and `valid_data`, losing the original `data` and `kwargs`.

**Fix**: Propagate all error attributes: `data`, `valid_data`, and `kwargs`.

**Files Changed**:
- `src/marshmallow/fields.py`: Lines 639-641

**Code Change**:
```python
# Before
raise ValidationError(error.messages, valid_data=error.valid_data) from error

# After
raise ValidationError(
    error.messages, 
    data=error.data, 
    valid_data=error.valid_data, 
    **error.kwargs
) from error
```

### 4. Schema._run_validator data_key Fallback (schema.py)
**Problem**: When a schema-level validator referenced an excluded field, the try/except pattern could fail in edge cases, falling back to field_name instead of using declared_fields' data_key.

**Fix**: Use `.get()` method instead of try/except for cleaner, more robust field lookup.

**Files Changed**:
- `src/marshmallow/schema.py`: Lines 798-810

**Code Change**:
```python
# Before
field_obj = None
try:
    field_obj = self.fields[field_name]
except KeyError:
    if field_name in self.declared_fields:
        field_obj = self.declared_fields[field_name]

# After
field_obj = self.fields.get(field_name) or self.declared_fields.get(field_name)
```

### 5. Schema._deserialize INCLUDE Mode Missing Sentinel (schema.py)
**Problem**: When `unknown=INCLUDE`, unknown fields containing `missing` sentinel values were included as-is, polluting the result with sentinel objects.

**Fix**: Added `_filter_missing()` helper function to recursively filter out `missing` sentinels from nested structures.

**Files Changed**:
- `src/marshmallow/schema.py`: Lines 200-224 (new helper function), Lines 697-702 (INCLUDE logic)

**Code Change**:
```python
# New helper function
def _filter_missing(value):
    """Recursively filter out missing sentinel values."""
    if value is missing:
        return missing
    if isinstance(value, dict):
        return {k: _filter_missing(v) for k, v in value.items() if v is not missing}
    if isinstance(value, list):
        return [_filter_missing(v) for v in value if v is not missing]
    return value

# Updated INCLUDE logic
if unknown == INCLUDE:
    filtered_value = _filter_missing(value)
    if filtered_value is not missing:
        ret_d[key] = filtered_value
```

## Test Coverage

### New Test File
- `tests/test_nested_validation_fixes.py`: 30 comprehensive tests covering all 5 fixes

### Updated Test
- `tests/test_deserialization.py`: Updated `test_structured_dict_value_deserialization` to expect `[None, None]` instead of `[]` for failed list items (reflecting new index-preserving behavior)

### Test Results
- **Total tests**: 1208 (1178 existing + 30 new)
- **Status**: All passing ✓

## Test Categories

1. **List Index Alignment** (5 tests)
   - Nested schema failures preserve indices
   - All-invalid lists maintain length
   - Mixed valid/invalid items preserve positions
   - Simple field failures use None placeholders
   - Tuple field index preservation

2. **merge_errors Reference Safety** (8 tests)
   - List + dict merging without reference sharing
   - Dict + scalar merging
   - Scalar + dict merging
   - Empty container handling
   - Nested dict merging
   - Sequential store operations

3. **Nested Error Propagation** (3 tests)
   - Data preservation through error chain
   - Custom kwargs propagation
   - Deep nesting error propagation

4. **Validator data_key Fallback** (5 tests)
   - Excluded field uses declared data_key
   - Included field uses active data_key
   - Only-excluded field handling
   - Unknown field name fallback
   - Schema-level error handling

5. **INCLUDE Mode Missing Filtering** (6 tests)
   - Basic missing sentinel filtering
   - Pre-load transformation filtering
   - Nested dict filtering
   - List filtering
   - Entire value missing exclusion
   - Normal value preservation

6. **Integration Tests** (3 tests)
   - Deeply nested lists with partial failures
   - Schema-level validation in nested lists
   - Complex nested unknown values

## Impact

### Breaking Changes
- **List._deserialize**: Failed items now produce `None` placeholders instead of being omitted. This changes the structure of `valid_data` in ValidationError, but ensures index alignment which is critical for form re-population.

### Benefits
1. **Index Alignment**: Frontend form re-population now works correctly with nested validation errors
2. **Error Integrity**: No reference sharing prevents mutation bugs in error structures
3. **Complete Error Context**: Original data and kwargs preserved through nested validation chains
4. **Consistent data_key**: Schema-level validators correctly use data_key for excluded fields
5. **Clean Results**: INCLUDE mode no longer pollutes results with missing sentinels

## Files Modified
1. `src/marshmallow/fields.py` (3 changes)
2. `src/marshmallow/error_store.py` (3 changes)
3. `src/marshmallow/schema.py` (2 changes)
4. `tests/test_deserialization.py` (1 update)
5. `tests/test_nested_validation_fixes.py` (new file, 30 tests)
