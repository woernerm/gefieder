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
- `macros/` — SQL a model cannot express, written once: `@temporal_join` joins two
  change histories on the union of their timestamps (PostgreSQL has no ASOF JOIN).
  Worked example plus audits and a unit test: `models/silver/project_a/issue_risk_history.sql`,
  `audits/assert_every_row_is_a_change.sql`, `tests/test_issue_risk_history.yaml`.

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

## Developing models with your own account

Developers connect as themselves, not as the deployed engine: the shared `sqlmesh_password`
secret belongs to the container and to CI. An administrator provisions a personal database
account in crudman (Database access → select the user → "Create database account"), which
issues a password once. Put it in `sqlmesh/.env` as `SQLMESH_PASSWORD`; `config.py` derives
the role name from your local username, or takes `SQLMESH_USER` if it differs.

A bare `sqlmesh plan` targets a `dev` environment, so the easiest command is the safe one.
`sqlmesh plan prod` is *not* blocked — PostgreSQL cannot separate promoting from planning,
since both write the same schemas (see `crudman/app/dbusers/requirements.md`). Production is
normally deployed from CI on merge to main; running it by hand is a deliberate exception.
