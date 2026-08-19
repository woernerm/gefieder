# sqlmesh — the analytics engine

SQLMesh transforms raw data into bronze, silver and gold models (DB tables) that Grafana 
reads. Container runs `sqlmesh plan --auto-apply --no-prompts` at startup, then 
`sqlmesh run` on a loop, so a model's `cron` decides when it is executed.

## Layers

- `models/bronze/<tenant or other>/` — one folder per tenant or other categorization 
  schemes like organizational entities. Models are a *VIEW* over a shared source schema 
  (`jira`, `sap`, `alm`, …) selecting only that tenant's columns and rows.
- `models/silver/<tenant or other>/` — a specific transform into a canonical shape in
  a staging layer (`silver_staging.*`); `models/silver/` is a thin `UNION ALL` of 
  staging models into `silver.*`.
- `models/gold/` — materialized (kind FULL, INCREMENTAL_BY_TIME_RANGE or similar) 
  metrics over silver only, organization wide, no per-tenant logic.

## Rules

- Adding a tenant means adding a bronze folder, a silver staging model if the shape needs
  harmonizing, and one `UNION ALL` line in `models/silver/`. Nothing in gold changes.
- Keep tenant-specific quirks upstream. If a gold model needs to know which tenant it is
  looking at, the transform belongs in silver instead.
- `seeds/` exists only so the worked examples run without an external source. A real
  tenant's bronze model reads a source schema.
- Python models use polars — see `models/bronze/project_c/`.

Project A, B and C are examples created at first start (`postgresql/initdb/gf_0006`), 
meant to be deleted for production. Layer details: `models/bronze/README.md`,
`models/silver/README.md`. For SQLMesh itself use the `sqlmesh-docs` skill.
