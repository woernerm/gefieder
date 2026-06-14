"""Database structure: the expected schemas exist and are reachable."""
import pytest


class TestSchemas:
    """The database exposes the expected schemas and is reachable by grafana."""

    def test_grafana_user_shall_connect_to_the_database(self, db):
        with db.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    @pytest.mark.parametrize("schema", ["crudman", "sqlmesh", "silver", "gold", "public"])
    def test_database_shall_have_all_default_schemas(self, db, schema):
        with db.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
            assert cur.fetchone() is not None, f"schema {schema} is missing"
