# Silver models: one folder per project

The silver layer harmonizes every project's raw bronze data into a single canonical
model. Because projects record the same things in very different ways — and may even
misuse fields for purposes they were never designed for — the bronze → silver transform
is kept **separate per project**.

## Layout

```
silver/
  <project_slug>/       # one folder per SQL project (e.g. project_a, project_b)
    issues.sql          # this project's transform -> silver_staging.issues__<project>
  issues.sql            # thin UNION ALL of all projects -> silver.issues
```

One canonical model per entity, each with the same two levels. Besides `issues` there is
`issue_risk_history`: for every issue, the periods during which its state, its effort and
the safety classification of its component all stayed the same. `project_a` and
`project_b` both record the two histories it needs, so `silver/issue_risk_history.sql`
unions the two staging models. It is also the worked example for `@temporal_join`
(`macros/temporal_join.py`), the answer to "these two tables both have a history and I
need to follow both timelines" -- project_a on the PostgreSQL gateway, project_b on the
duckdb one, which is where the macro's two lookups are compared.

`project_a` and `project_b` are worked examples. They deliberately start from different
raw column names and different status vocabularies, yet both produce the same canonical
columns. Compare the two `issues.sql` staging models to see how the per-project quirks are
quarantined.

A project does not need a staging model here at all if it already emits the canonical
columns in bronze: `project_c` harmonizes its data in a polars Python model
(`bronze/project_c/issues_raw.py`), so `issues.sql` below unions its bronze model directly,
with no `silver/project_c/` folder.

## Adding a real project

1. Add the project's bronze model(s) under `bronze/<slug>/` (see `bronze/README.md`):
   usually a view over a shared source schema, selecting and filtering the rows for this
   project. `sqlmesh plan` creates the `bronze_<slug>` schema the model names.
2. Decode that project's bronze data into the canonical columns
   (`tenant_id, issue_id, title, state, created_on, effort`), either by copying one of the
   example folders to `silver/<slug>/` and rewriting its staging model (the SQL path, like
   `project_a`/`project_b`), or by emitting the canonical columns straight from bronze (the
   Python path, like `project_c`, which then needs no folder here).
3. Add one `UNION ALL` line for the new project to `silver/issues.sql`.

`tenant_id` names the project a row came from. It is a plain column and a naming
convention, nothing the database enforces.

Gold models read `silver.*` only and need no changes when projects come and go.

The bronze layer for the example projects lives in `bronze/project_a`, `bronze/project_b`
and `bronze/project_c`. There it reads example data from `seeds/` so the pipeline produces
data out of the box; real projects get their bronze data from views over source schemas
instead.
