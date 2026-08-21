"""Every ${TOKEN} in a rendered template is one its render script substitutes.

Two build steps template a tree with envsubst and an explicit allowlist: grafana/render.sh
over grafana/provisioning/, and postgresql/render.sh over postgresql/initdb/. The allowlist
is what keeps envsubst off the tokens that must survive verbatim -- nginx's $host, Grafana's
$__file{}, psql's shell variables, SQL's own $$ quoting.

The failure it cannot notice is the opposite one: a ${TOKEN} the allowlist does not name is
not an error, it simply stays as literal text in the image. A role name that renders as the
seven characters "${GRAFANA_DB_USER}" produces a syntax error at first start, on a machine
nobody is watching, hours after the build passed.

These read the scripts rather than the rendered output, so a missing name is caught even
when the value happens to equal the default the repository ships.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Each render step: the script carrying the allowlist, and the tree it renders.
RENDERERS = [
    ("grafana/render.sh", "grafana/provisioning", ("*.md",)),
    ("postgresql/render.sh", "postgresql/initdb", ()),
]


def allowlisted(script):
    """The names in the script's VARS='${A} ${B}' allowlist."""
    text = (REPO / script).read_text()
    line = re.search(r"^VARS='([^']*)'", text, re.MULTILINE)
    assert line, f"{script} has no VARS allowlist"
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line.group(1)))


def referenced(tree, skip):
    """Every ${TOKEN} the rendered files use, mapped to the files using it."""
    uses = {}
    for path in sorted((REPO / tree).rglob("*")):
        if not path.is_file() or any(path.match(p) for p in skip):
            continue
        for name in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", path.read_text()):
            uses.setdefault(name, set()).add(path.relative_to(REPO).as_posix())
    return uses


@pytest.mark.parametrize("script,tree,skip", RENDERERS, ids=[r[0] for r in RENDERERS])
def test_every_referenced_token_shall_be_substituted(script, tree, skip):
    names = allowlisted(script)
    unlisted = {n: sorted(f) for n, f in referenced(tree, skip).items() if n not in names}
    assert not unlisted, f"{script} does not substitute: {unlisted}"


@pytest.mark.parametrize("script,tree,skip", RENDERERS, ids=[r[0] for r in RENDERERS])
def test_every_substituted_token_shall_be_declared(script, tree, skip):
    # A name in the allowlist that buildtime.env does not declare renders as empty, which
    # is the same silent failure from the other direction.
    declared = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=",
                              (REPO / "buildtime.env").read_text(), re.MULTILINE))
    missing = allowlisted(script) - declared
    assert not missing, f"{script} substitutes settings buildtime.env lacks: {sorted(missing)}"
