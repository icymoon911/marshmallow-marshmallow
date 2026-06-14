"""Utilities for storing collections of error messages.

.. warning::

    This module is treated as private API.
    Users should not need to use this module directly.
"""

from marshmallow.exceptions import SCHEMA


class ErrorStore:
    def __init__(self):
        #: Dictionary of errors stored during serialization
        self.errors = {}

    def store_error(self, messages, field_name=SCHEMA, index=None):
        # field error  -> store/merge error messages under field name key
        # schema error -> if string or list, store/merge under _schema key
        #              -> if dict, store/merge with other top-level keys
        messages = copy_containers(messages)
        if field_name != SCHEMA or not isinstance(messages, dict):
            messages = {field_name: messages}
        if index is not None:
            messages = {index: messages}
        self.errors = merge_errors(self.errors, messages)


def copy_containers(errors):
    if isinstance(errors, list):
        return [copy_containers(val) for val in errors]
    if isinstance(errors, dict):
        return {key: copy_containers(val) for key, val in errors.items()}
    return errors


def merge_errors(errors1, errors2):
    """Deeply merge two error messages.

    The format of ``errors1`` and ``errors2`` matches the ``message``
    parameter of :exc:`marshmallow.exceptions.ValidationError`.

    The merging strategy follows these rules:
    - If either side is falsy, return the other.
    - Two dicts: recursively merge by key.
    - Two lists: concatenate.
    - One dict, one non-dict: wrap non-dict under the SCHEMA key and merge.
    - One list, one scalar: append/prepend the scalar to the list.
    - Two scalars: wrap both in a list.
    """
    # Early exit for falsy values
    if not errors1:
        return errors2
    if not errors2:
        return errors1

    # Both are dicts: recursively merge by key
    if isinstance(errors1, dict) and isinstance(errors2, dict):
        for key, val in errors2.items():
            if key in errors1:
                errors1[key] = merge_errors(errors1[key], val)
            else:
                errors1[key] = val
        return errors1

    # Both are lists: concatenate
    if isinstance(errors1, list) and isinstance(errors2, list):
        errors1.extend(errors2)
        return errors1

    # One is dict, other is non-dict: put non-dict under SCHEMA key and merge
    if isinstance(errors1, dict):
        errors1[SCHEMA] = merge_errors(errors1.get(SCHEMA), errors2)
        return errors1
    if isinstance(errors2, dict):
        errors2[SCHEMA] = merge_errors(errors1, errors2.get(SCHEMA))
        return errors2

    # Neither is a dict. Handle list + scalar and scalar + scalar combinations.
    if isinstance(errors1, list):
        errors1.append(errors2)
        return errors1
    if isinstance(errors2, list):
        return [errors1, *errors2]

    # Both are scalars (strings, custom error objects, etc.)
    return [errors1, errors2]
