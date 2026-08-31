"""Registry for the file checking and conversion functions of dropzones.

Functions live in the package named by ``FUNCTIONS_PACKAGE`` and register themselves
with the ``@checker`` and ``@converter`` decorators. ``autodiscover()`` imports every
module there at startup (``DropzonesConfig.ready``).

A function registers under its own name, the identifier a dropzone stores, so renaming
one orphans the dropzones referencing the old name.

Typical usage example:

    @checker("My check")
    def my_check(files: list[Path]) -> None:
        # Raise any exception to reject the upload; nothing is stored.

    @converter("My conversion")
    def my_convert(files: list[Path], out_dir: Path) -> None:
        # Write the files to store into out_dir.
"""

import importlib
import pkgutil

from django.core.exceptions import ImproperlyConfigured

FUNCTIONS_PACKAGE = "dropzones.functions"
"""Dotted path of the package holding all check and convert functions."""

_checkers = {}
_converters = {}
# Dropdown labels, keyed like the function tables; without one, the name is used.
_checker_labels = {}
_converter_labels = {}


def _register(table, labels, kind, label, func):
    # Re-registering one function is harmless, but two under one name would make a
    # dropzone ambiguous.
    name = func.__name__
    registered = table.get(name)
    if registered is not None and registered is not func:
        raise ImproperlyConfigured(f"Duplicate {kind} function name '{name}'.")
    table[name] = func
    if label is not None:
        labels[name] = label
    return func


def checker(label=None):
    """Register the decorated function as a file checker, named after itself.

    Args:
        label: Dropdown label for the admin, or the decorated function when used bare as
            ``@checker``. Defaults to the function name.

    Returns:
        The decorator, or the registered function when used bare.
    """
    if callable(label):
        return _register(_checkers, _checker_labels, "checker", None, label)
    return lambda func: _register(_checkers, _checker_labels, "checker", label, func)


def converter(label=None):
    """Register the decorated function as a file converter, named after itself.

    Args:
        label: Dropdown label for the admin, or the decorated function when used bare as
            ``@converter``. Defaults to the function name.

    Returns:
        The decorator, or the registered function when used bare.
    """
    if callable(label):
        return _register(_converters, _converter_labels, "converter", None, label)
    return lambda func: _register(
        _converters, _converter_labels, "converter", label, func
    )


def get_checker(name):
    try:
        return _checkers[name]
    except KeyError:
        raise LookupError(f"No checker function named '{name}' is registered.") from None


def get_converter(name):
    try:
        return _converters[name]
    except KeyError:
        raise LookupError(
            f"No converter function named '{name}' is registered."
        ) from None


def checker_names():
    return sorted(_checkers)


def converter_names():
    return sorted(_converters)


def _choices(table, labels):
    """``(name, label)`` pairs for form dropdowns, sorted by what the user sees."""
    pairs = ((name, labels.get(name, name)) for name in table)
    return sorted(pairs, key=lambda pair: pair[1].lower())


def checker_choices():
    return _choices(_checkers, _checker_labels)


def converter_choices():
    return _choices(_converters, _converter_labels)


def autodiscover():
    """Import every module in the functions folder so the decorators register."""
    package = importlib.import_module(FUNCTIONS_PACKAGE)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{FUNCTIONS_PACKAGE}.{module.name}")
