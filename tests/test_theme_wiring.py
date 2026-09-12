"""The three applications are themed from one palette and one default, in step.

grafana/palette.css holds the colours every application is drawn in; the admin and the
notebook images copy it in at build time, and each maps its framework's variables onto it.
DEFAULT_THEME in runtime.env picks the mode a browser starts in, and reaches Grafana, the
admin panel and the hub by three different routes. Nothing fails loudly when one copy
lags: a missing COPY is a stylesheet whose @import answers 404, and a container without
EnvironmentFile= starts fine in the built-in default.

These read the sources rather than the built images, as tests/test_build_args.py does.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PALETTE = REPO / "grafana" / "palette.css"

# Every Dockerfile that themes its image from the palette.
CONSUMERS = ["crudman/Dockerfile", "jupyter/Dockerfile"]

# Every file that maps a framework's variables onto the palette's.
ADAPTERS = [
    "jupyter/custom/custom.css",
    "jupyter/templates/page.html",
    "crudman/app/crudman/settings.py",
]

# Every quadlet whose container reads DEFAULT_THEME.
THEMED_CONTAINERS = ["grafana", "crudman", "jupyter"]

# The bar below every page, whose switch carries a choice into each app's own store.
SHELL = REPO / "crudman/app/shell/templates/shell/shell.html"


def palette_variables():
    """The names the palette defines."""
    return set(re.findall(r"^\s*(--app-[a-z0-9-]+):", PALETTE.read_text(), re.MULTILINE))


def test_the_palette_names_both_modes_alike():
    """A colour one mode has and the other lacks is a var() that resolves to nothing."""
    names = palette_variables()
    dark = {n.replace("--app-dark-", "") for n in names if n.startswith("--app-dark-")}
    light = {n.replace("--app-light-", "") for n in names if n.startswith("--app-light-")}
    assert dark == light, f"only in dark: {dark - light}; only in light: {light - dark}"


def test_every_consumer_copies_the_palette():
    for dockerfile in CONSUMERS:
        text = (REPO / dockerfile).read_text()
        assert re.search(r"^COPY\s+.*grafana/palette\.css", text, re.MULTILINE), (
            f"{dockerfile} does not copy grafana/palette.css"
        )


def test_every_adapter_refers_only_to_names_the_palette_defines():
    """A typo in an adapter is a variable that silently falls back to the browser default."""
    defined = palette_variables()
    for adapter in ADAPTERS:
        used = set(re.findall(r"var\((--app-(?:dark|light|gray)-[a-z0-9-]+)", (REPO / adapter).read_text()))
        assert used <= defined, f"{adapter} uses undefined: {sorted(used - defined)}"
        assert used, f"{adapter} maps nothing onto the palette"


def test_default_theme_reaches_every_themed_container():
    """A container without the env file starts in its framework's own default instead."""
    for name in THEMED_CONTAINERS:
        text = (REPO / "quadlets" / f"{name}.container").read_text()
        assert "EnvironmentFile=" in text, f"{name}.container does not read runtime.env"
    assert re.search(r"^DEFAULT_THEME=(dark|light)$", (REPO / "runtime.env").read_text(), re.MULTILINE)


def test_dev_stack_passes_default_theme_to_every_themed_container():
    """dev.sh skips EnvironmentFile= and passes runtime values as -e, one by one."""
    text = (REPO / "dev.sh").read_text()
    for name in THEMED_CONTAINERS:
        # The call and every line it continues onto with a trailing backslash.
        block = re.search(rf"run_quadlet {name}\b(?:[^\n]*\\\n)*[^\n]*", text)
        assert block and "DEFAULT_THEME" in block.group(0), f"dev.sh's {name} lacks DEFAULT_THEME"


def test_the_bars_switch_names_what_the_apps_read():
    """The bar writes the choice where each app keeps its own: the storage key the hub's
    pages read before their dark-mode script runs, and the Lab themes by the names the
    entrypoint sets as the default. A rename in one place is a switch that no longer
    reaches that app."""
    shell = SHELL.read_text()
    hub = (REPO / "jupyter/templates/page.html").read_text()
    key = re.search(r'localStorage\.setItem\("([^"]+)"', hub).group(1)
    assert key in shell
    names = re.findall(r'THEME_NAME="([^"]+)"', (REPO / "jupyter/entrypoint.sh").read_text())
    assert names and all(name in shell for name in names)
