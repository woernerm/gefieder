# Bronze models: one folder per tenant

The bronze layer presents each tenant's raw data inside its own `bronze_<tenant>` schema.
Raw data is acquired by external tools into shared source schemas (`jira`, `sap`, `alm`,
`github`, ...); a bronze model is usually a **view** over one of those sources that

- selects only the columns the tenant actually needs, and
- filters to the rows that belong to the tenant,

so the data is officially present in the tenant's bronze schema without being copied and
without exposing other tenants' rows. A bronze model can also be a real **table** when a
tenant has a bespoke source that nobody else uses.

## Layout

```
bronze/
  <tenant_slug>/        # one folder per tenant (e.g. project_a, project_b, project_c)
    issues_raw.sql      # a view over a source schema (or a bespoke table)
    issues_raw.py       # or a Python model, when the transform is easier in Python
```

`project_a`, `project_b` and `project_c` are worked examples. To keep the example runnable
without any external source they read example data from `seeds/`; a real tenant's bronze
model is a view over a shared source schema instead.

`project_a` and `project_b` use SQLMesh `SEED` models and leave the transform to silver.
`project_c` instead uses a [Python model](https://sqlmesh.readthedocs.io/en/latest/concepts/models/python_models/)
that reads its CSV and harmonizes it with [polars](https://pola.rs/) right here in bronze,
so silver only has to union it in -- a worked example of bronze data shaped in Python
rather than SQL. The `polars` dependency is declared in `sqlmesh/pyproject.toml`.

## Adding a real bronze model

SQLMesh owns the bronze views: `sqlmesh plan` creates them, the view runs with sqlmesh's
own privileges, and its `SELECT` list and `WHERE` clause are what scope a tenant's access
(only the needed columns, only that tenant's rows). Create the model in `bronze/<slug>/`
with a name in that tenant's schema, e.g.:

```sql
MODEL (
  name bronze_acme.issues,
  kind VIEW
);
SELECT id, summary, status, created
FROM jira.issues            -- a shared source schema, declared in external_models.yaml
WHERE project_key = 'ACME'; -- only this tenant's rows
```

The silver staging model in `silver/<slug>/` then reads from this bronze model and
harmonizes it into the canonical silver shape.

> Source access: the shared source schemas (`jira`, `sap`, ...) are read by the sqlmesh
> role only; an admin grants sqlmesh read on a source when attaching it to the project.
> Tenants and Grafana never read the source directly -- they read the bronze view, which
> runs as sqlmesh. Grant the source access first, then add and plan the bronze model
> (a model selecting from a source sqlmesh cannot yet read fails the plan).
