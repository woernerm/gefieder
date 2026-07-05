"""Registry for the file checking and conversion functions of dropzones.

The functions live in the designated folder named by ``FUNCTIONS_PACKAGE`` and register
themselves with the ``@checker`` and ``@converter`` decorators. ``autodiscover()`` runs
once at startup (``DropzonesConfig.ready``) and imports every module in that folder, so
adding a function is just adding a decorated function to a module there and rebuilding
the image.

Signatures:

    @checker("my_check")
    def my_check(files: list[Path]) -> None:
        # Raise any exception to reject the upload; the message is shown to the
        # uploading user and nothing is stored.

    @converter("my_convert")
    def my_convert(files: list[Path], out_dir: Path) -> list[Path]:
        # Write the files to store into out_dir and return their paths. Returning the
        # input paths unchanged means "store the files as uploaded".
"""

import importlib
import pkgutil

from django.core.exceptions import ImproperlyConfigured

# The designated folder holding all check/convert functions, as a dotted module path so
# discovery can import from it. Change this constant to relocate the folder.
FUNCTIONS_PACKAGE = "dropzones.functions"

_checkers = {}
_converters = {}


def _register(table, kind, name, func):
    # Re-registering the same function (e.g. a module imported twice) is harmless, but
    # two different functions under one name would make a dropzone ambiguous.
    registered = table.get(name)
    if registered is not None and registered is not func:
        raise ImproperlyConfigured(f"Duplicate {kind} function name '{name}'.")
    table[name] = func
    return func


def checker(name):
    """Register the decorated function as a file checker under ``name``."""
    return lambda func: _register(_checkers, "checker", name, func)


def converter(name):
    """Register the decorated function as a file converter under ``name``."""
    return lambda func: _register(_converters, "converter", name, func)


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


def autodiscover():
    """Import every module in the functions folder so the decorators register."""
    package = importlib.import_module(FUNCTIONS_PACKAGE)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{FUNCTIONS_PACKAGE}.{module.name}")
