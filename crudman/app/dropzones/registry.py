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


class Registry:
    """The functions of one kind, each under its own name, with optional dropdown labels."""

    def get(self, name):
        try:
            return self.functions[name]
        except KeyError:
            raise LookupError(
                f"No {self.kind} function named '{name}' is registered."
            ) from None

    def names(self):
        return sorted(self.functions)

    def choices(self):
        """``(name, label)`` pairs for form dropdowns, sorted by what the user sees."""
        pairs = ((name, self.labels.get(name, name)) for name in self.functions)
        return sorted(pairs, key=lambda pair: pair[1].lower())

    def register(self, func, label=None):
        name = func.__name__
        registered = self.functions.get(name)
        if registered is not None and registered is not func:
            raise ImproperlyConfigured(f"Duplicate {self.kind} function name '{name}'.")
        self.functions[name] = func
        if label is not None:
            self.labels[name] = label
        return func

    def __init__(self, kind):
        self.kind = kind
        self.functions = {}
        # Without a label, the dropdown shows the function name.
        self.labels = {}

    def __call__(self, label=None):
        """Register the decorated function, named after itself.

        Args:
            label: Dropdown label for the admin, or the decorated function when used bare.
                Defaults to the function name.

        Returns:
            The decorator, or the registered function when used bare.
        """
        if callable(label):
            return self.register(label)
        return lambda func: self.register(func, label)


checker = Registry("checker")
converter = Registry("converter")


def autodiscover():
    """Import every module in the functions folder so the decorators register."""
    package = importlib.import_module(FUNCTIONS_PACKAGE)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{FUNCTIONS_PACKAGE}.{module.name}")
