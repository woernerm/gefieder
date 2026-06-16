--- Install the DuckDB community extensions tenants may use in their analytics queries.
--- This template ships a broad set so projects can adopt them without re-provisioning
--- the database; trim this list to what a given deployment actually needs.
SELECT duckdb.install_extension('duckpgq', 'community');     -- graph queries over traceability/requirement links
SELECT duckdb.install_extension('dash', 'community');        -- serve query results as lightweight dashboards
SELECT duckdb.install_extension('flock', 'community');       -- call LLMs from SQL (e.g. classify free-text fields)
SELECT duckdb.install_extension('minijinja', 'community');   -- Jinja templating to parameterize report queries
SELECT duckdb.install_extension('stochastic', 'community');  -- sampling and distributions for statistical metrics
SELECT duckdb.install_extension('yaml', 'community');        -- read YAML exports from engineering tools
SELECT duckdb.install_extension('zipfs', 'community');       -- read files inside zipped tool exports