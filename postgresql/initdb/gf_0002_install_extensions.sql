--- Register pg_stat_statements (preloaded in gf_0001) so its view exists in this
--- database. It records per-query execution statistics the collector snapshots to find
--- queries worth an index. The extension ships with PostgreSQL, so no community install.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

--- The DuckDB community extensions tenants may use in their analytics queries are already
--- installed: the image build downloaded the ones listed in DUCKDB_EXTENSIONS
--- (buildtime.env) into duckdb.extension_directory, so there is no
--- duckdb.install_extension() call here and the server needs no internet access.
---
--- A community extension is not autoloaded, so a query that needs one loads it first:
---
---   SELECT duckdb.load_extension('yaml');
