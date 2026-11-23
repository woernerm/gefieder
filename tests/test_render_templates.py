"""Every ${TOKEN} in a rendered template is one its render script substitutes.

Two build steps template a tree with envsubst and an explicit allowlist: grafana/render.sh
over grafana/provisioning/, and postgresql/render.sh over postgresql/initdb/. The allowlist
keeps envsubst off the tokens that must survive verbatim -- nginx's $host, Grafana's
$__file{}, psql's shell variables, SQL's own $$ quoting.

What it cannot notice is the opposite: a ${TOKEN} the allowlist does not name simply stays
as literal text, and produces a syntax error at first start, hours after the build passed.

These read the scripts rather than the rendered output, so a missing name is caught even
when the value happens to equal the default the repository ships.
"""
import re

import pytest

from conftest import REPO, allowlisted, declared

# Each render step: the script carrying the allowlist, what it renders, what to skip
# inside it, and whether the source is ours.
#
# custom.ini is the one source that is not ours -- Grafana's own sample.ini with our edits
# -- and it carries ${...} tokens Grafana expands itself (";instance_name = ${HOSTNAME}").
# An unlisted token there is normal, so only the ones buildtime.env declares are checked.
RENDERERS = [
    ("grafana/render.sh", "grafana/provisioning", ("*.md",), True),
    ("grafana/render.sh", "grafana/custom.ini", (), False),
    ("postgresql/render.sh", "postgresql/initdb", (), True),
]


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
        names |= referenced(tree, skip).keys() - declared("buildtime.env")
    unlisted = {n: sorted(f) for n, f in referenced(tree, skip).items() if n not in names}
    assert not unlisted, f"{script} does not substitute: {unlisted}"


@pytest.mark.parametrize("script,tree,skip,ours", RENDERERS, ids=[r[1] for r in RENDERERS])
def test_every_substituted_token_shall_be_declared(script, tree, skip, ours):
    # A listed name buildtime.env does not declare renders as empty: the same silent
    # failure from the other direction. A name the script derives itself counts too.
    derived = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", (REPO / script).read_text(), re.MULTILINE))
    missing = allowlisted(script) - declared("buildtime.env") - derived
    assert not missing, f"{script} substitutes settings buildtime.env lacks: {sorted(missing)}"


# The third render step, its allowlist in build-lib.sh: run-tests.sh renders the quadlets
# and serverstats units for the test stack, publish.yml the same files for the release.
# serverstats/collect.sh ships verbatim, its own shell variables surviving.
#
# One token may legitimately be unlisted here: the pod's ports are left for systemd to
# expand from the operator's runtime.env at start.
UNIT_TREES = ["quadlets", "serverstats"]
UNIT_SUFFIXES = (".pod", ".container", ".volume", ".service", ".timer")
UNIT_ALLOWLISTS = ["build-lib.sh"]


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
    # Neither one nor the other leaves the token literal on the target machine:
    # "Environment=POSTGRES_DB=${PG_DATABASE}", and the service connects to that name.
    resolved = allowlisted(script) | declared("runtime.env")
    unlisted = {n: sorted(f) for n, f in unit_tokens().items() if n not in resolved}
    assert not unlisted, f"{script} leaves these as literal text: {unlisted}"


@pytest.mark.parametrize("script", UNIT_ALLOWLISTS)
def test_every_substituted_unit_token_shall_be_declared(script):
    missing = allowlisted(script) - declared("buildtime.env")
    assert not missing, f"{script} substitutes settings buildtime.env lacks: {sorted(missing)}"


# --- provisioned dashboards -------------------------------------------------------------
# Grafana identifies a dashboard by its uid, not its filename, and two provisioning
# providers scanning different directories will happily load the same uid from both. There
# is no error: whichever the scan reaches last wins, so the page served is decided by scan
# order rather than by intent.
#
# That is not hypothetical. ai-assistant.json is the one dashboard whose text is rewritten
# at container start (grafana/entrypoint.sh substitutes @@MCP_URL@@ into a copy under
# /var/lib/grafana), and a stale second copy left in the build-time tree served the
# unsubstituted placeholder instead -- on a fresh volume, with the suite green.
DASHBOARD_TREE = REPO / "grafana/provisioning"


def dashboard_files():
    return sorted(DASHBOARD_TREE.rglob("*.json"))


def test_the_suite_shall_find_the_provisioned_dashboards():
    """An empty list would leave the check below asserting nothing."""
    assert dashboard_files(), f"no dashboard JSON under {DASHBOARD_TREE}"


def test_no_dashboard_uid_shall_be_provisioned_twice():
    import json

    seen = {}
    for path in dashboard_files():
        uid = json.loads(path.read_text()).get("uid")
        if uid is None:
            continue
        seen.setdefault(uid, []).append(str(path.relative_to(REPO)))

    duplicates = {uid: paths for uid, paths in seen.items() if len(paths) > 1}
    assert not duplicates, (
        "the same dashboard uid is provisioned from more than one file, and which one "
        f"Grafana serves is decided by scan order: {duplicates}"
    )
