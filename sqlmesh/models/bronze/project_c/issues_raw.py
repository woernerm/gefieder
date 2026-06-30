# Bronze for the "Project C" tenant -- a Python model instead of SQL.
#
# Project A and Project B keep their bronze layer as a raw SEED passthrough and do the
# transform later in SQL (see their silver/<tenant>/issues.sql). Project C is the worked
# example of the *Python model* path SQLMesh also supports
# (https://sqlmesh.readthedocs.io/en/latest/concepts/models/python_models/): the
# bronze -> canonical transform is written in polars here, and silver only unions the
# result together with the other tenants in SQL.
#
# As with the other two tenants this reads an example CSV from seeds/ so the pipeline has
# data out of the box. The raw columns ("ticket", "headline", "phase", ...) are this
# tenant's own flavour again, different from Project A's Jira-style and Project B's
# GitHub-style columns, which is why every tenant decodes its raw data separately.
#
# The output column list IS the harmonization contract: it must match the other tenants'
# silver staging models and the silver.issues union exactly
# (tenant_id, issue_id, title, state, created_on, effort).
from pathlib import Path

import pandas as pd
import polars as pl
from sqlmesh import ExecutionContext, model


@model(
    "bronze_project_c.issues",
    kind="FULL",
    columns={
        "tenant_id": "TEXT",
        "issue_id": "TEXT",
        "title": "TEXT",
        "state": "TEXT",
        "created_on": "DATE",
        "effort": "INT",
    },
    grain=("tenant_id", "issue_id"),
    audits=["assert_known_tenant"],
)
def execute(
    context: ExecutionContext,
    **kwargs,
) -> pd.DataFrame:
    # The seed lives next to the other tenants' CSVs. Resolve it relative to this file so
    # the path holds whether SQLMesh runs from the host or from inside the container image.
    # Kept inside the function (not a module global) because SQLMesh serializes a Python
    # model's globals into its state, and a Path object there is not serializable.
    seed_path = Path(__file__).resolve().parents[3] / "seeds" / "project_c_issues.csv"

    # Read the raw CSV and harmonize it with polars. This tenant's phase vocabulary
    # ("resolved"/"active"/"backlog") is mapped onto the canonical open/closed states, and
    # its "weight" field carries the effort estimate -- the same kind of per-tenant quirk
    # the SQL tenants resolve in their staging models, expressed here as polars operations.
    harmonized = pl.read_csv(seed_path).select(
        pl.lit("project_c").alias("tenant_id"),
        pl.col("ticket").alias("issue_id"),
        pl.col("headline").alias("title"),
        pl.when(pl.col("phase") == "resolved")
        .then(pl.lit("closed"))
        .otherwise(pl.lit("open"))
        .alias("state"),
        pl.col("logged").str.to_date().alias("created_on"),
        pl.col("weight").alias("effort"),
    )

    # SQLMesh consumes Pandas (the universally supported return type for Python models);
    # polars does the work, then we hand off with a one-line conversion.
    return harmonized.to_pandas()
