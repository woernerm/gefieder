"""grafana/palette.css says the same colours Grafana's own theme does.

The palette is a transcription: Grafana's chrome cannot be recoloured without an
Enterprise licence, so the admin panel and the notebooks are drawn in Grafana's colours
instead, copied by hand from its dark and light themes. A Grafana upgrade that moves a
colour would leave the other two applications a shade behind, and nothing but a careful
eye would notice. This reads the theme definitions out of the running Grafana's frontend
bundle and compares every value the palette claims to have taken from it.

The bundle is minified and its file name carries a hash, so it is found by a marker
rather than named, and the theme is parsed by shape: one class per mode whose fields hold
a hex literal (``canvas:"#111217"``), name a minified constant declared just before the
classes (``page:o``), or build a colour from the mode's base tint
(``rgba(${this.whiteBase}, 0.12)``).
"""
import re
import subprocess

import pytest

from conftest import REPO

PALETTE = REPO / "grafana" / "palette.css"

# What each palette variable transcribes: the mode, and the theme field, spelled as
# Grafana's own classes spell it. --app-*-on-accent is left out: Grafana derives its text-
# on-primary by contrast at runtime rather than declaring it.
TRANSCRIBED = {
    "bg-0": "background.canvas",
    "bg-1": "background.primary",
    "bg-2": "background.secondary",
    "bg-3": "secondary.main",
    "border-0": "border.weak",
    "border-1": "border.medium",
    "border-2": "border.strong",
    "text-0": "text.primary",
    "text-1": "text.secondary",
    "text-2": "text.disabled",
    "text-max": "text.maxContrast",
    "accent": "primary.main",
    "accent-text": "primary.text",
    "hover": "action.hover",
    "selected": "action.selected",
    "success": "success.main",
    "success-text": "success.text",
    "warning": "warning.main",
    "warning-text": "warning.text",
    "error": "error.main",
    "error-text": "error.text",
}


def rgba(value):
    """A colour as (r, g, b, a), whichever way it was written.

    Grafana and the palette spell the same colour differently -- ``rgba(36, 41, 46, 1)``
    against ``rgb(36, 41, 46)``, ``#cf0e5B`` against ``#cf0e5b`` -- so strings are no
    basis for comparison.
    """
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(2 * d for d in digits)
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 1.0)
    match = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    assert match, f"not a colour this test can read: {value!r}"
    r, g, b, a = match.groups()
    return (int(float(r)), int(float(g)), int(float(b)), float(a) if a is not None else 1.0)


@pytest.fixture(scope="module")
def grafana_theme():
    """Both of Grafana's themes, as {"dark": {"background.canvas": "#111217", ...}, ...}."""
    bundle = subprocess.run(
        ["podman", "exec", "grafana", "sh", "-c",
         'cat "$(grep -l \'whiteBase=\' /usr/share/grafana/public/build/*.js | head -1)"'],
        capture_output=True, text=True, check=True,
    ).stdout
    assert 'mode="dark"' in bundle and 'mode="light"' in bundle, "theme classes not found"

    dark = bundle.index('mode="dark"')
    light = bundle.index('mode="light"')

    # The minified names the classes use for shared colours (let s="#ffffff",o=...),
    # declared shortly before the first class.
    constants = dict(re.findall(r'\b(\w+)="(#[0-9a-fA-F]{6})"', bundle[dark - 2000:dark]))

    # Each class runs from its mode marker to the next class's, the light one to its
    # closing brace; cut there, or the later class's fields overwrite the earlier's.
    light_end = bundle.index("tonalOffset=", light)
    bodies = {"dark": bundle[dark:light], "light": bundle[light:bundle.index("}", light_end)]}

    # A field block is {...} whose only nested braces are the ${...} of a template literal.
    block_pattern = r"(?<![\w.$])(\w+)=\{((?:[^{}]|\$\{[^{}]*\})*)\}"

    themes = {}
    for mode, body in bodies.items():
        base = re.search(r'(?:white|black)Base="([^"]+)"', body).group(1)
        fields = {}
        for name, block in re.findall(block_pattern, body):
            for key, value in re.findall(r'(\w+):"(#[0-9a-fA-F]{6})"', block):
                fields[f"{name}.{key}"] = value
            for key, ref in re.findall(r"(\w+):(\w+)(?=[,}]|$)", block):
                if ref in constants:
                    fields[f"{name}.{key}"] = constants[ref]
            for key, alpha in re.findall(r"(\w+):`rgba\(\$\{this\.\w+Base\}, *([\d.]+)\)`", block):
                fields[f"{name}.{key}"] = f"rgba({base}, {alpha})"
            for key in re.findall(r"(\w+):`rgb\(\$\{this\.\w+Base\}\)`", block):
                fields[f"{name}.{key}"] = f"rgb({base})"
        themes[mode] = fields
    return themes


def palette():
    """Every --app-<mode>-<name> and --app-gray-<step> the palette defines, raw."""
    return dict(re.findall(r"^\s*(--app-(?:dark|light|gray)-[a-z0-9-]+):\s*([^;]+);", PALETTE.read_text(), re.MULTILINE))


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("name", sorted(TRANSCRIBED))
def test_the_palette_matches_grafanas_theme(grafana_theme, mode, name):
    field = TRANSCRIBED[name]
    assert field in grafana_theme[mode], (
        f"Grafana's {mode} theme no longer declares {field}; the bundle's shape has changed"
    )
    expected = rgba(grafana_theme[mode][field])
    actual = rgba(palette()[f"--app-{mode}-{name}"])
    assert actual == expected, (
        f"--app-{mode}-{name} is {actual}, but Grafana's {mode} {field} is {expected}"
    )


def test_every_transcribed_variable_is_in_the_palette():
    """The mapping above and the palette name the same things, or a test asserts on air."""
    names = set(palette())
    for mode in ("dark", "light"):
        for name in TRANSCRIBED:
            assert f"--app-{mode}-{name}" in names
