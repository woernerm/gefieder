"""Unit tests for the @temporal_join macro itself (macros/temporal_join.py).

The yaml tests beside this file cover the macro through a model, one per branch, but over
different tenants and different data, so they never say "both branches agree". This file
renders the macro twice over one fixture, once per gateway, and compares row for row.

Both run on DuckDB, which understands either form; the integration suite is what proves
the PostgreSQL branch on PostgreSQL.

A plain script rather than a pytest module, so it runs on the dependencies the sqlmesh
image already ships:

    uv run --project /sqlmesh python /sqlmesh/app/tests/test_temporal_join.py
"""

import sys
from pathlib import Path

import duckdb
from sqlglot import exp
from sqlglot.schema import MappingSchema
from sqlmesh.core import constants as c
from sqlmesh.core.dialect import parse_one
from sqlmesh.core.macros import MacroEvaluator, RuntimeStage
from sqlmesh.utils.errors import SQLMeshError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from macros.temporal_join import DUCKDB_GATEWAY  # noqa: E402

COLUMNS = {
    "issue_history": {
        "issue_key": "text",
        "changed_at": "date",
        "status": "text",
        "story_points": "int",
        "component_key": "text",
    },
    "component_history": {
        "component_key": "text",
        "changed_at": "date",
        "safety_class": "text",
        "owner_team": "text",
    },
}

# One item per awkward case, the same set the yaml tests state as rows: PA-1 moves to
# another component, PA-2 has no component, PA-3's component is classified only later,
# and PA-4 is reopened into a state it already held.
ISSUES = [
    ("PA-1", "2026-06-10", "To Do", 5, "C-NAV"),
    ("PA-1", "2026-06-14", "In Progress", 5, "C-NAV"),
    ("PA-1", "2026-06-18", "In Progress", 8, "C-PWR"),
    ("PA-1", "2026-06-22", "Done", 8, "C-PWR"),
    ("PA-2", "2026-06-11", "To Do", 3, None),
    ("PA-2", "2026-06-15", "Done", 3, None),
    ("PA-3", "2026-06-13", "To Do", 2, "C-CAB"),
    ("PA-4", "2026-06-10", "To Do", 1, "C-NAV"),
    ("PA-4", "2026-06-12", "Done", 1, "C-NAV"),
    ("PA-4", "2026-06-14", "To Do", 1, "C-NAV"),
]
COMPONENTS = [
    ("C-NAV", "2026-06-09", "B", "Navigation"),
    ("C-NAV", "2026-06-16", "C", "Navigation"),
    ("C-PWR", "2026-06-12", "D", "Power"),
    ("C-PWR", "2026-06-20", "D", "Power"),
    ("C-CAB", "2026-06-17", "A", "Cabin"),
]

EXPECTED = [
    ("PA-1", "2026-06-10", "To Do", 5, "C-NAV", "B", "Navigation"),
    ("PA-1", "2026-06-14", "In Progress", 5, "C-NAV", "B", "Navigation"),
    ("PA-1", "2026-06-16", "In Progress", 5, "C-NAV", "C", "Navigation"),
    ("PA-1", "2026-06-18", "In Progress", 8, "C-PWR", "D", "Power"),
    ("PA-1", "2026-06-22", "Done", 8, "C-PWR", "D", "Power"),
    ("PA-2", "2026-06-11", "To Do", 3, None, None, None),
    ("PA-2", "2026-06-15", "Done", 3, None, None, None),
    ("PA-3", "2026-06-13", "To Do", 2, "C-CAB", None, None),
    ("PA-3", "2026-06-17", "To Do", 2, "C-CAB", "A", "Cabin"),
    ("PA-4", "2026-06-10", "To Do", 1, "C-NAV", "B", "Navigation"),
    ("PA-4", "2026-06-12", "Done", 1, "C-NAV", "B", "Navigation"),
    ("PA-4", "2026-06-14", "To Do", 1, "C-NAV", "B", "Navigation"),
    ("PA-4", "2026-06-16", "To Do", 1, "C-NAV", "C", "Navigation"),
]

MODEL = """
SELECT tck.issue_key, tck.changed_at, lhs.status, lhs.story_points,
       lhs.component_key, rhs.safety_class, rhs.owner_team
FROM @temporal_join(
  lhs_ts := main.issue_history.changed_at,
  rhs_ts := main.component_history.changed_at,
  key    := issue_key,
  on     := (rhs.component_key = lhs.component_key)
)
"""


def render(gateway, dialect, sql=MODEL):
    """Render the macro as the given gateway would see it, in that gateway's dialect."""
    schema = MappingSchema(
        {"main": {t: {n: exp.DataType.build(d) for n, d in cs.items()} for t, cs in COLUMNS.items()}},
        dialect=dialect,
        normalize=True,
    )
    evaluator = MacroEvaluator(
        dialect=dialect, schema=schema, runtime_stage=RuntimeStage.EVALUATING
    )
    # Where SQLMesh puts a model's variables and evaluator.gateway reads them back.
    evaluator.locals[c.SQLMESH_VARS] = {c.GATEWAY: gateway}
    return evaluator.transform(parse_one(sql, dialect=dialect)).sql(dialect="duckdb")


def fixture():
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE issue_history (issue_key TEXT, changed_at DATE, status TEXT,"
        " story_points INT, component_key TEXT)"
    )
    connection.execute(
        "CREATE TABLE component_history (component_key TEXT, changed_at DATE,"
        " safety_class TEXT, owner_team TEXT)"
    )
    connection.executemany("INSERT INTO issue_history VALUES (?, ?, ?, ?, ?)", ISSUES)
    connection.executemany("INSERT INTO component_history VALUES (?, ?, ?, ?)", COMPONENTS)
    return connection


def rows(connection, query):
    result = connection.execute(f"{query} ORDER BY 1, 2").fetchall()
    return [(k, str(ts), *rest) for k, ts, *rest in result]


def test_each_gateway_emits_the_lookup_its_engine_has():
    postgres, duck = render("postgres", "postgres"), render(DUCKDB_GATEWAY, "duckdb")
    assert "LATERAL" in postgres and "ASOF" not in postgres, postgres
    assert "ASOF LEFT JOIN" in duck and "LATERAL" not in duck, duck


def test_both_gateways_produce_the_same_history():
    connection = fixture()
    postgres = rows(connection, render("postgres", "postgres"))
    duck = rows(connection, render(DUCKDB_GATEWAY, "duckdb"))
    assert postgres == duck, (
        "the two branches disagree:\n"
        + "\n".join(f"  pg {p}\n  db {d}" for p, d in zip(postgres, duck) if p != d)
    )
    assert postgres == EXPECTED, postgres


def test_a_duckdb_gateway_model_must_be_written_in_duckdb():
    # ASOF in any other dialect parses as a table alias, so the query would quietly
    # return the wrong rows.
    try:
        render(DUCKDB_GATEWAY, "postgres")
    except SQLMeshError as error:
        assert "dialect duckdb" in str(error), error
    else:
        raise AssertionError("a duckdb-gateway model in postgres dialect was accepted")


def test_the_call_form_is_checked():
    for argument, call in (
        ("lhs_ts", MODEL.replace("main.issue_history.changed_at", "changed_at")),
        ("key", MODEL.replace("key    := issue_key", "key    := lhs.issue_key")),
    ):
        try:
            render("postgres", "postgres", call)
        except SQLMeshError as error:
            assert argument in str(error), error
        else:
            raise AssertionError(f"an invalid '{argument}' was accepted")


if __name__ == "__main__":
    for name, test in sorted(vars().items()):
        if name.startswith("test_"):
            test()
            print(f"ok  {name}")
    print("all temporal_join macro tests passed")
