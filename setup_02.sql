SET duckdb.allow_community_extensions = true;
SET duckdb.allow_unsigned_extensions = true;

SELECT duckdb.install_extension('duckpgq', 'community');
SELECT duckdb.install_extension('dash', 'community');
SELECT duckdb.install_extension('flock', 'community');
SELECT duckdb.install_extension('minijinja', 'community');
SELECT duckdb.install_extension('stochastic', 'community');
SELECT duckdb.install_extension('yaml', 'community');
SELECT duckdb.install_extension('zipfs', 'community');