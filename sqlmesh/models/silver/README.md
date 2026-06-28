# Silver models: one folder per tenant

The silver layer harmonizes every tenant's raw bronze data into a single canonical
model. Because tenants record the same things in very different ways — and may even
misuse fields for purposes they were never designed for — the bronze → silver transform
is kept **separate per tenant**.

## Layout

```
silver/
  <tenant_slug>/        # one folder per tenant (e.g. project_a, project_b)
    issues.sql          # this tenant's transform -> silver_staging.issues__<tenant>
  issues.sql            # thin UNION ALL of all tenants -> silver.issues
```

`project_a` and `project_b` are worked examples. They deliberately start from different
raw column names and different status vocabularies, yet both produce the same canonical
columns. Compare the two `issues.sql` staging models to see how the per-tenant quirks are
quarantined.

## Adding a real tenant

1. Create the tenant in the admin panel (this creates its `bronze_<slug>` schema in
   PostgreSQL). The folder name here must match that slug.
2. Add the tenant's bronze model(s) under `bronze/<slug>/` (see `bronze/README.md`):
   usually a view over a shared source schema, selecting and filtering the rows for this
   tenant.
3. Copy one of the example folders to `silver/<slug>/` and rewrite its staging model to
   decode that tenant's bronze data into the canonical columns
   (`tenant_id, issue_id, title, state, created_on, effort`).
4. Add one `UNION ALL` line for the new tenant to `silver/issues.sql`.

Gold models read `silver.*` only and need no changes when tenants come and go.

The bronze layer for the example tenants lives in `bronze/project_a` and
`bronze/project_b`. There it uses SQLMesh `SEED` models so the pipeline produces data out
of the box; real tenants get their bronze data from views over source schemas instead.
