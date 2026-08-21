"""The SQLMesh example models sit in the schemas buildtime.env configures.

The medallion schema names are configurable (BRONZE_SCHEMA_PREFIX, SILVER_SCHEMA and
GOLD_SCHEMA in buildtime.env, plus the staging layer conftest derives from SILVER_SCHEMA);
postgresql/render.sh bakes them into the init scripts that create and grant on them. The
example models under sqlmesh/models/ cannot join in: a model name is parsed by SQLMesh,
which never reads buildtime.env, so those names are written out in full.

That leaves one way for the two to drift. Rename a layer in buildtime.env and the database
creates the new schema, grants on it and hands it to grafana -- while every shipped model
still writes to the old name, which nothing granted and nothing reads. Nothing fails: the
plan succeeds, the dashboards are simply empty.

So the agreement is asserted here instead. A failure means one of two edits is missing, not
that anything is broken: either finish the rename in sqlmesh/models/, or put the schema name
in buildtime.env back.

Read from the repository rather than the running stack, so it reports the mismatch before
the stack is built on it.
"""
import re
from pathlib import Path

import pytest

from conftest import (
    BRONZE_SCHEMA_PREFIX,
    GOLD_SCHEMA,
    SILVER_SCHEMA,
    SILVER_STAGING_SCHEMA,
)

MODELS = Path(__file__).resolve().parents[1] / "sqlmesh" / "models"

# `MODEL ( name <schema>.<table>, ...)` in a SQL model, and the first argument of the
# @model decorator in a Python one.
SQL_NAME = re.compile(r"^\s*name\s+([a-z0-9_]+)\.[a-z0-9_]+\s*,", re.MULTILINE)
PY_NAME = re.compile(r"@model\(\s*[\"']([a-z0-9_]+)\.[a-z0-9_]+[\"']")


def declared_schemas():
    """Every schema a shipped model writes into, mapped to the files writing there."""
    found = {}
    for path in sorted(MODELS.rglob("*")):
        if path.suffix not in (".sql", ".py"):
            continue
        pattern = PY_NAME if path.suffix == ".py" else SQL_NAME
        for schema in pattern.findall(path.read_text()):
            found.setdefault(schema, set()).add(path.name)
    return found


def configured(schema):
    """Whether `schema` is one of the four the medallion settings describe."""
    return (
        schema in (SILVER_SCHEMA, SILVER_STAGING_SCHEMA, GOLD_SCHEMA)
        or schema.startswith(BRONZE_SCHEMA_PREFIX)
    )


def test_the_models_shall_be_discovered():
    # A regex that stopped matching would make the check below an assertion over an empty
    # set, which passes and proves nothing.
    assert declared_schemas(), "no model names found -- the sqlmesh/models parsing broke"


def test_every_model_shall_live_in_a_configured_schema():
    stray = {s: sorted(f) for s, f in declared_schemas().items() if not configured(s)}
    assert not stray, (
        f"these models write to schemas buildtime.env does not configure: {stray}. "
        f"Configured: {BRONZE_SCHEMA_PREFIX}*, {SILVER_SCHEMA}, {SILVER_STAGING_SCHEMA}, "
        f"{GOLD_SCHEMA}"
    )


@pytest.mark.parametrize("layer", ["silver", "staging", "gold"])
def test_each_layer_shall_be_used_by_a_shipped_model(layer):
    # The reverse direction: a configured name nothing writes to is the other half of the
    # same drift, and would leave the layer empty just as quietly.
    wanted = {"silver": SILVER_SCHEMA, "staging": SILVER_STAGING_SCHEMA, "gold": GOLD_SCHEMA}[layer]
    assert wanted in declared_schemas(), f"no shipped model writes to {wanted}"
