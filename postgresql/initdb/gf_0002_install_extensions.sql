--- Register pg_stat_statements, preloaded in gf_0001, so its view exists in this database.
--- It ships with PostgreSQL, so there is no community install.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

--- The DuckDB community extensions are installed already: the image build downloaded the
--- ones listed in DUCKDB_EXTENSIONS into duckdb.extension_directory, so there is no
--- duckdb.install_extension() call here and the server needs no internet access.
---
--- None is autoloaded, so a query that needs one loads it first:
---
---   SELECT duckdb.load_extension('yaml');
