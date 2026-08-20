---
name: pg-duckdb-docs
description: pg_duckdb documentation to consult before answering questions about pg_duckdb, the DuckDB extension in this project's PostgreSQL image.
---

# pg_duckdb documentation

pg_duckdb changes quickly, so check current documentation rather than answering from
memory. Fetch only the pages below that cover the question; the summaries exist so you
don't have to fetch all of them.

Base URL for the pages below: `https://raw.githubusercontent.com/duckdb/pg_duckdb/refs/heads/main/docs/`

- `gotchas_and_syntax.md` — **Start here for "how do I write this query".** Standard SQL
  on Postgres tables is accelerated once `duckdb.force_execution=true`; external files
  need the `r['column']` alias syntax, and `duckdb.query()` is only for DuckDB-only
  syntax such as `PIVOT`. Also lists the traps: separate transactions for Postgres vs
  DuckDB writes, CTE aliasing, restricted filesystem access.
- `functions.md` — Reference for every function, with an index of tables at the top:
  data lake readers (`read_parquet`, `read_csv`, `read_json`, `iceberg_scan`,
  `delta_scan`, `read_vortex`, `read_text`, `read_blob`), MAP/union functions,
  time functions (`time_bucket`, `strftime`, `strptime`, `epoch*`, `make_timestamp*`),
  `approx_count_distinct`, `TABLESAMPLE`, and the `duckdb.*` admin, secret and
  MotherDuck functions. Large file — jump to the relevant anchor.
- `types.md` — Which Postgres types survive the round trip, DuckDB-only `struct`/`map`/
  `union`, and the known limitations (no `enum`, `numeric` may degrade to `double
  precision`, `jsonb` becomes `json`, multi-dimensional array mismatches). Explains the
  special types `duckdb.row` and `duckdb.unresolved_type`, and why `INSERT INTO ...
  SELECT r['col']` needs an explicit cast.
- `settings.md` — All `duckdb.*` GUCs grouped by area: general (`force_execution`,
  `default_collation`), security (`postgres_role`, `allowed_directories`,
  `disabled_filesystems`, `enable_external_access`, extension autoinstall/autoload,
  community extensions), resource limits (`max_memory`, `threads`,
  `max_workers_per_postgres_scan`), temp directory and extension directory, MotherDuck,
  plus `unsafe_allow_mixed_transactions` and `convert_unsupported_numeric_to_double`.
- `extensions.md` — `httpfs` and `json` ship pre-installed; `iceberg`, `delta`, `vortex`,
  `azure` and community extensions are installed with `duckdb.install_extension()`
  (superuser) and tracked in the `duckdb.extensions` table.
- `secrets.md` — Credentials for S3/GCS/R2 via `duckdb.create_simple_secret()` and Azure
  via `duckdb.create_azure_secret()`, or a `SERVER` + `USER MAPPING` on the `duckdb`
  foreign data wrapper for `credential_chain`. Never grant `USAGE` on that FDW to
  regular users.
- `transactions.md` — Multi-statement transactions work, but a single transaction may not
  write (or run DDL) against both Postgres and DuckDB objects;
  `duckdb.unsafe_allow_mixed_transactions` lifts this at the risk of data loss.
- `compilation.md` — Building from source: supported Postgres versions (14–18), build
  dependencies, `DUCKDB_BUILD` / `PG_CONFIG` options, and the mandatory
  `shared_preload_libraries = 'pg_duckdb'`. Only needed for image-build problems.
- `README.md` — Just a table of contents for the pages above; skip it. `motherduck.md`
  covers the cloud service, which this project does not use.
- `https://raw.githubusercontent.com/duckdb/pg_duckdb/refs/heads/main/README.md`
  (the project README, one level above `docs/`) — Feature overview with worked examples:
  accelerating plain Postgres queries, a first data lake query, joining Postgres with
  Parquet, Iceberg/Delta scans, MotherDuck. Also carries the facts not in `docs/`: the
  `pgduckdb/pgduckdb` Docker image and its tag scheme (e.g. `18-v1.1.1`), supported
  Postgres versions (14-18), MIT license, and the note that STRUCT/MAP/UNION need a
  DuckDB execution context. Fetch it for orientation or image/version questions, not for
  reference detail.

How this project builds and initializes the pgduckdb image is in `postgresql/`
(`Dockerfile`, `initdb/`, `Readme.md`).
