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

# Each render step: the script carrying the allowlist, what it renders -- a tree or a single
# file -- what to skip inside it, and whether the source is ours.
#
# grafana/render.sh renders two sources: the provisioning tree, and custom.ini, which names
# the podman secret Grafana reads the single sign-on client secret from. custom.ini is the
# only one of the three that is not ours: it is Grafana's own sample.ini with our edits, and
# it carries ${...} tokens Grafana expands itself (";instance_name = ${HOSTNAME}"). An
# unlisted token there is therefore normal, so only the ones buildtime.env declares -- the
# ones we put in -- have to be substituted.
RENDERERS = [
    ("grafana/render.sh", "grafana/provisioning", ("*.md",), True),
    ("grafana/render.sh", "grafana/custom.ini", (), False),
    ("postgresql/render.sh", "postgresql/initdb", (), True),
]


def allowlisted(script):
    """The names in the script's VARS='${A} ${B}' allowlist."""
    text = (REPO / script).read_text()
    line = re.search(r"^\s*VARS='([^']*)'", text, re.MULTILINE)
    assert line, f"{script} has no VARS allowlist"
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line.group(1)))


def declared_settings():
    """The names buildtime.env declares, which is what makes a token one of ours."""
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=",
                          (REPO / "buildtime.env").read_text(), re.MULTILINE))


def referenced(tree, skip):
    """Every ${TOKEN} the rendered files use, mapped to the files using it."""
    root = REPO / tree
    uses = {}
    for path in [root] if root.is_file() else sorted(root.rglob("*")):
        if not path.is_file() or any(path.match(p) for p in skip):
            continue
        for name in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", path.read_text()):
            uses.setdefault(name, set()).add(path.relative_to(REPO).as_posix())
    return uses


@pytest.mark.parametrize("script,tree,skip,ours", RENDERERS, ids=[r[1] for r in RENDERERS])
def test_every_referenced_token_shall_be_substituted(script, tree, skip, ours):
    names = allowlisted(script)
    if not ours:
        names |= referenced(tree, skip).keys() - declared_settings()
    unlisted = {n: sorted(f) for n, f in referenced(tree, skip).items() if n not in names}
    assert not unlisted, f"{script} does not substitute: {unlisted}"


@pytest.mark.parametrize("script,tree,skip,ours", RENDERERS, ids=[r[1] for r in RENDERERS])
def test_every_substituted_token_shall_be_declared(script, tree, skip, ours):
    # A name in the allowlist that buildtime.env does not declare renders as empty, which
    # is the same silent failure from the other direction.
    missing = allowlisted(script) - declared_settings()
    assert not missing, f"{script} substitutes settings buildtime.env lacks: {sorted(missing)}"


# The third render step, and the only one whose allowlist exists twice: run-tests.sh renders
# the quadlets and the two serverstats units for the test stack, and publish.yml renders the
# same files for the release. serverstats/collect.sh is not among them -- it ships verbatim,
# and its own shell variables ($CG_ROOT, ...) must survive.
#
# A token here has one legitimate reason to be missing from the allowlist that the two trees
# above do not have: the pod's ports are deliberately left for systemd to expand from the
# operator's runtime.env at start, so "unlisted" is correct for those and only those.
UNIT_TREES = ["quadlets", "serverstats"]
UNIT_SUFFIXES = (".pod", ".container", ".volume", ".service", ".timer")
UNIT_ALLOWLISTS = ["run-tests.sh", ".github/workflows/publish.yml"]


def runtime_settings():
    """The names runtime.env declares, which systemd expands in the installed unit."""
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=",
                          (REPO / "runtime.env").read_text(), re.MULTILINE))


def unit_tokens():
    """Every ${TOKEN} the shipped units use, mapped to the files using it."""
    uses = {}
    for tree in UNIT_TREES:
        for path in sorted((REPO / tree).rglob("*")):
            if not path.is_file() or not path.name.endswith(UNIT_SUFFIXES):
                continue
            for name in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", path.read_text()):
                uses.setdefault(name, set()).add(path.relative_to(REPO).as_posix())
    return uses


def test_the_unit_tokens_shall_be_discovered():
    assert unit_tokens(), "no ${...} tokens found in the units -- the parsing broke"


@pytest.mark.parametrize("script", UNIT_ALLOWLISTS)
def test_every_unit_token_shall_be_substituted_or_expanded(script):
    # Neither one nor the other means the token reaches the target machine as literal text:
    # "Environment=POSTGRES_DB=${PG_DATABASE}", and the service connects to a database of
    # that name.
    resolved = allowlisted(script) | runtime_settings()
    unlisted = {n: sorted(f) for n, f in unit_tokens().items() if n not in resolved}
    assert not unlisted, f"{script} leaves these as literal text: {unlisted}"


def test_the_two_unit_allowlists_shall_agree():
    # The release and the test stack render the same files from separate copies of the list.
    # A name added to one only would pass the whole suite and ship broken.
    first, second = (allowlisted(s) for s in UNIT_ALLOWLISTS)
    assert first == second, (
        f"only in {UNIT_ALLOWLISTS[0]}: {sorted(first - second)}; "
        f"only in {UNIT_ALLOWLISTS[1]}: {sorted(second - first)}"
    )


@pytest.mark.parametrize("script", UNIT_ALLOWLISTS)
def test_every_substituted_unit_token_shall_be_declared(script):
    missing = allowlisted(script) - declared_settings()
    assert not missing, f"{script} substitutes settings buildtime.env lacks: {sorted(missing)}"
