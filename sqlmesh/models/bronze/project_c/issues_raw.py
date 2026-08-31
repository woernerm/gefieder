# Bronze for the "Project C" tenant, the worked example of SQLMesh's Python model path
# (https://sqlmesh.readthedocs.io/en/latest/concepts/models/python_models/). Project A
# and Project B keep bronze as a raw SEED passthrough and transform later in SQL; here
# the bronze -> canonical transform is polars, and silver only unions the result.
#
# The raw columns ("ticket", "headline", "phase", ...) are this tenant's own flavour,
# which is why every tenant decodes its raw data separately.
#
# The output column list IS the harmonization contract: it must match the other tenants'
# silver staging models and the silver.issues union exactly.
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
    # Resolved relative to this file, so the path holds whether SQLMesh runs from the
    # host or the image. Inside the function because SQLMesh serializes a Python model's
    # globals into its state, where a Path is not serializable.
    seed_path = Path(__file__).resolve().parents[3] / "seeds" / "project_c_issues.csv"

    # This tenant's phase vocabulary maps onto the canonical open/closed states and its
    # "weight" field carries the effort estimate -- the per-tenant quirks the SQL tenants
    # resolve in their staging models, as polars operations.
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

    # Pandas is the return type every Python model supports.
    return harmonized.to_pandas()
