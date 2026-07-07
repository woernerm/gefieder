"""Registry for the file checking and conversion functions of dropzones.

The functions live in the designated folder named by ``FUNCTIONS_PACKAGE`` and register
themselves with the ``@checker`` and ``@converter`` decorators. ``autodiscover()`` runs
once at startup (``DropzonesConfig.ready``) and imports every module in that folder, so
adding a function is just adding a decorated function to a module there and rebuilding
the image.

Signatures:

    @checker("My check")
    def my_check(files: list[Path]) -> None:
        # Raise any exception to reject the upload; the message is shown to the
        # uploading user and nothing is stored.

    @converter("My conversion")
    def my_convert(files: list[Path], out_dir: Path) -> None:
        # Write the files to store into out_dir; everything found there afterwards is
        # stored. To store files as uploaded, leave the dropzone's converter empty.

A function registers under its own name — the identifier a dropzone stores, so
renaming a function orphans dropzones still referencing the old name. The decorator's
optional argument is the human-readable label shown in the admin dropdowns; used bare
(``@checker``), the label defaults to the name.
"""

import importlib
import pkgutil

from django.core.exceptions import ImproperlyConfigured

# The designated folder holding all check/convert functions, as a dotted module path so
# discovery can import from it. Change this constant to relocate the folder.
FUNCTIONS_PACKAGE = "dropzones.functions"

_checkers = {}
_converters = {}
# The dropdown labels, keyed like the function tables; a function registered without
# a label falls back to its name wherever a label is displayed.
_checker_labels = {}
_converter_labels = {}


def _register(table, labels, kind, label, func):
    # Re-registering the same function (e.g. a module imported twice) is harmless, but
    # two different functions under one name would make a dropzone ambiguous.
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

    Used bare (``@checker``) or with the dropdown label (``@checker("My check")``).
    """
    if callable(label):
        return _register(_checkers, _checker_labels, "checker", None, label)
    return lambda func: _register(_checkers, _checker_labels, "checker", label, func)


def converter(label=None):
    """Register the decorated function as a file converter, named after itself.

    Used bare (``@converter``) or with the dropdown label
    (``@converter("My conversion")``).
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
