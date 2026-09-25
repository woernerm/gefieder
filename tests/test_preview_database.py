"""The preview database: production's schema names, pointed wherever a review says.

A version put up for review is planned into a SQLMesh environment of its own, whose
schemas are named <layer>__<environment> -- so only what the commit changes is built and
everything else stays the production table. The dashboards cannot follow that, because
they name `gold.x` literally and a convention for writing them differently is one an
author can forget.

So the names are what moves. This second database holds nothing but foreign tables over
the first, one schema per production schema and named the same, each pointing at the
environment's copy where there is one and at production where there is none. A panel's SQL
is then identical in both, and showing somebody a reviewed version is Grafana being handed
a different data source uid -- which the proxy does, from the admin panel's answer about
who is asking.
"""

from pathlib import Path

import psycopg2
import pytest

from conftest import (
    BRONZE_SCHEMA_PREFIX,
    DB_PASSWORDS,
    GOLD_SCHEMA,
    GRAFANA_DB_USER,
    PG_DATABASE,
    PG_PORT,
    SILVER_SCHEMA,
    SQLMESH_DB_USER,
)

PREVIEW_ENV = "preview"
"""One review at a time, so one environment; gf_0009 names the database after it."""

REPO = Path(__file__).resolve().parents[1]
"""For the class below, which compares files rather than the running stack."""


@pytest.fixture(scope="module")
def preview_db():
    """A connection to the preview database as the read-only Grafana role."""
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=f"{PG_DATABASE}_{PREVIEW_ENV}",
        user=GRAFANA_DB_USER, password=DB_PASSWORDS[GRAFANA_DB_USER],
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def published(preview_db):
    """The names published, as the engine publishes them after planning a review."""
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=f"{PG_DATABASE}_{PREVIEW_ENV}",
        user=SQLMESH_DB_USER, password=DB_PASSWORDS[SQLMESH_DB_USER],
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT refresh_preview(%s)", (PREVIEW_ENV,))
    conn.close()
    return preview_db


def schemas(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('information_schema', 'public')"
        )
        return {row[0] for row in cur.fetchall()}


class TestNames:
    def test_the_medallion_layers_are_there_under_their_own_names(self, published):
        """A dashboard writes gold.x, so gold has to be a schema here and hold x."""
        assert {SILVER_SCHEMA, GOLD_SCHEMA} <= schemas(published)

    def test_every_bronze_schema_comes_along(self, published):
        """Bronze is one schema per project and nothing announces a new one, so the
        publication reads the list off production rather than being told it."""
        assert any(name.startswith(BRONZE_SCHEMA_PREFIX) for name in schemas(published))

    def test_what_sqlmesh_keeps_to_itself_stays_out(self, published):
        """Its physical schemas and its state: churning objects, not something to query,
        and the same rule production's grants draw."""
        assert not any(name.startswith("sqlmesh") for name in schemas(published))

    def test_no_environment_copy_is_published_under_its_own_name(self, published):
        """The suffix is the mechanism, never a name anybody reads: a dashboard that
        could name gold__preview would be one written for a review."""
        assert not any(name.endswith(f"__{PREVIEW_ENV}") for name in schemas(published))


class TestContents:
    def test_it_holds_no_data_of_its_own(self, published):
        """Every table is foreign. That is what makes a preview free: an unchanged model
        is production's table read through a name, not a copy of it."""
        with published.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = 'r' AND n.nspname IN (%s, %s)",
                (SILVER_SCHEMA, GOLD_SCHEMA),
            )
            assert cur.fetchone()[0] == 0

    def test_a_layer_reads_the_same_rows_as_production(self, published, db):
        """With no review planned, every name points at production -- so the preview data
        source shows exactly what the live one does, and a review is the only difference
        anybody ever sees."""
        with db.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name LIMIT 1",
                (GOLD_SCHEMA,),
            )
            row = cur.fetchone()
        if row is None:
            pytest.skip("no gold model deployed yet")
        table = row[0]

        counts = []
        for conn in (db, published):
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) FROM "{GOLD_SCHEMA}"."{table}"')
                counts.append(cur.fetchone()[0])

        assert counts[0] == counts[1]


class TestBoundary:
    def test_the_reader_cannot_write_through_it(self, published):
        """The foreign server is reached as the read-only Grafana role, so the preview is
        a way of looking at production and never a way into it."""
        with published.cursor() as cur:
            with pytest.raises(psycopg2.Error):
                cur.execute(f'CREATE TABLE "{GOLD_SCHEMA}".sneaked (x int)')

    def test_publishing_is_not_something_a_reader_may_do(self, preview_db):
        """It creates schemas; the engine calls it because the engine is what knows a
        plan has finished."""
        with preview_db.cursor() as cur:
            with pytest.raises(psycopg2.Error):
                cur.execute("SELECT refresh_preview(%s)", (PREVIEW_ENV,))


class TestOneSpelling:
    """The uid is written in three files and nothing fails loudly when one drifts.

    A dashboard carries the production uid; the proxy replaces that literal with the one
    crudman names; the data source answering to it is declared in Grafana's provisioning.
    Get any of the three wrong and a reviewer quietly reads production -- the one failure
    this whole arrangement exists to rule out.
    """

    PROVISIONING = REPO / "grafana/provisioning/datasources/postgresql.yaml"
    MAPS = REPO / "proxy/maps.conf.template"
    LOCATIONS = REPO / "proxy/locations.conf.template"
    CRUDMAN = REPO / "crudman/app/notebooks/views.py"

    def test_grafana_declares_both_data_sources(self):
        declared = self.PROVISIONING.read_text()

        assert "uid: ${APP_NAME}-postgresql" in declared
        assert f"uid: ${{APP_NAME}}-{PREVIEW_ENV}" in declared

    def test_the_preview_data_source_reads_the_preview_database(self):
        assert f"database: ${{PG_DATABASE}}_{PREVIEW_ENV}" in self.PROVISIONING.read_text()

    def test_the_proxy_substitutes_the_uid_a_dashboard_carries(self):
        """On answers only: the browser sends back what it was given, so a query names the
        data source the dashboard was served with."""
        assert (
            """sub_filter '"${APP_NAME}-postgresql"' '"$datasource_uid"';"""
            in self.LOCATIONS.read_text()
        )

    def test_a_visitor_with_no_review_gets_the_production_uid_back(self):
        """The substitution runs for everybody; for everybody but a reviewer it replaces
        the uid with itself."""
        assert '"${APP_NAME}-postgresql"' in self.MAPS.read_text()

    def test_crudman_names_the_data_source_after_the_environment(self):
        """Which is what makes a second review a second data source and no new rule."""
        assert (
            'PREVIEW_DATASOURCE = f"{settings.APP_NAME}-{Deployment.PREVIEW}"'
            in self.CRUDMAN.read_text()
        )
