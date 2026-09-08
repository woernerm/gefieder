# sqlmesh — the analytics engine

SQLMesh transforms raw data into bronze, silver and gold models (DB tables) that Grafana 
reads. 

This folder is not copied into the image. It is the seed of the *models repository*: crudman
keeps that repository on the `models_data` volume, checks a commit out under `deployed/` and
publishes the sha in `deployed.sha`. The container watches that file, runs
`sqlmesh plan --auto-apply --no-prompts` whenever it changes, exports the documentation the
crudman docs pages render, then `sqlmesh run` on a loop so a model's `cron` decides when it
is executed. So a model ships as a commit, not as a release; see
`crudman/app/repository/requirements.md`.

`Dockerfile`, `entrypoint.sh`, `sqlmesh.sh`, `docs_export.py` and `status.py` belong to the
image and are stripped from the seed. Everything else here is what a developer clones.

`sqlmesh.sh` is installed as `/usr/local/bin/sqlmesh`, so `podman exec sqlmesh sqlmesh test`
runs against the deployed checkout. Without it the CLI writes `logs/` into that checkout,
which is mounted read-only.

## Layers

- `models/bronze/<project or other>/` — one folder per project or other categorization 
  schemes like organizational entities. Models are a *VIEW* over a shared source schema 
  (`jira`, `sap`, `alm`, …) selecting only that project's columns and rows.
- `models/silver/<project or other>/` — a specific transform into a canonical shape in
  a staging layer (`silver_staging.*`); `models/silver/` is a thin `UNION ALL` of 
  staging models into `silver.*`.
- `models/gold/` — materialized (kind FULL, INCREMENTAL_BY_TIME_RANGE or similar) 
  metrics over silver only, organization wide, no per-project logic.
- `macros/` — SQL a model cannot express, written once: `@temporal_join` joins two
  change histories on the union of their timestamps, emitting an ASOF JOIN on the duckdb
  gateway and a LATERAL lookup where there is none. Worked examples plus audits and tests:
  `models/silver/project_{a,b}/issue_risk_history.sql`,
  `audits/assert_every_row_is_a_change.sql`, `tests/test_issue_risk_history__project_{a,b}.yaml`,
  `tests/test_temporal_join.py` (the macro itself, both branches over one fixture).

## Gateways

`config.py` defines two. `postgres` is the default and holds the state. `duckdb` is DuckDB
as the compute engine over the *same* PostgreSQL storage: it attaches this database as its
only catalog, so a model with `gateway duckdb` reads and writes PostgreSQL tables like any
other and nothing downstream can tell which engine built it. It buys DuckDB's grammar —
`ASOF JOIN`, `QUALIFY`, `PIVOT` — which pg_duckdb cannot offer however hard it accelerates
execution, because PostgreSQL parses the statement first. It costs a second engine and a
round trip per row, so it is for a query the grammar makes simpler or faster, not a
default. Such a model also needs `dialect duckdb`. `models/silver/project_a` and
`models/silver/project_b` build the same history from the same macro, one per gateway: the
`gateway` line is what decides which join `@temporal_join` writes for them.

The extensions in `DUCKDB_EXTENSIONS` (buildtime.env) are installed into the sqlmesh image
too, so the gateway offers offline what a session reaches through `use_duckdb()`.

## Rules

- Adding a project means adding a bronze folder, a silver staging model if the shape needs
  harmonizing, and one `UNION ALL` line in `models/silver/`. Nothing in gold changes.
- Keep project-specific quirks upstream. If a gold model needs to know which project it is
  looking at, the transform belongs in silver instead.
- `seeds/` exists only so the worked examples run without an external source. A real
  project's bronze model reads a source schema.
- The layer names are configuration (`BRONZE_SCHEMA_PREFIX`, `SILVER_SCHEMA`,
  `GOLD_SCHEMA` in `buildtime.env`; the staging layer is derived from `SILVER_SCHEMA`), but
  a model name is parsed by SQLMesh, which never reads that file — so renaming a layer means
  renaming it in these models too. `tests/test_medallion_schemas.py` fails when the two
  disagree.
- Python models use polars — see `models/bronze/project_c/`.

Project A, B and C are worked examples shipped in the seed repository, meant to be
deleted for production. Layer details: `models/bronze/README.md`,
`models/silver/README.md`. For SQLMesh itself use the `sqlmesh-docs` skill.

## Developing models in a notebook

The `jupyter` container serves a JupyterLab per person at `/${NOTEBOOK_PATH}/`, where a
`.sql` model opens *as* a notebook: the file is the cell, running it validates and previews
the model, saving writes the same `.sql` back. There is no generated file and no second copy
-- see `jupyter/requirements.md`. Cells are separated by `-- %%` when somebody splits one,
which stays valid SQL. Analysis notebooks with saved output live in `notebooks/`.

## Developing models with your own account

Developers connect as themselves, not as the deployed engine: the shared `sqlmesh_password`
secret belongs to the container and to CI. An administrator provisions a personal database
account in crudman (Database access → select the user → "Create database account"), which
issues a password once. Put it in `sqlmesh/.env` as `SQLMESH_PASSWORD`; `config.py` derives
the role name from your local username, or takes `SQLMESH_USER` if it differs.

A clone of the models repository carries `sqlmesh/server.env`, written when the repository
was created, so `config.py` finds the database without gefieder's own env files beside it.
A checkout of this repository has those files instead; either way an exported variable wins.

A bare `sqlmesh plan` targets a `dev` environment, so the easiest command is the safe one.
`sqlmesh plan prod` is *not* blocked — PostgreSQL cannot separate promoting from planning,
since both write the same schemas (see `crudman/app/dbusers/requirements.md`). Production is
normally reached the deployed way — a commit on the models repository's `main`, which the
container applies within `MODELS_POLL_INTERVAL` seconds; running it by hand is a deliberate
exception.
