"""The DuckDB community extensions ship inside the image, so no internet is needed.

The extensions are fetched during the image build into duckdb.extension_directory rather
than downloaded on first start, which used to make provisioning fail on a machine without
internet access. Pinned down from both ends: the files are in the image, and the server
reports them installed from there and can run one.
"""
import os
import subprocess

import pytest

# Straight from DUCKDB_EXTENSIONS in buildtime.env, so trimming that list does not leave
# these tests asserting on extensions the image no longer contains.
EXTENSIONS = [
    ext.strip()
    for ext in os.environ.get("DUCKDB_EXTENSIONS", "").split(",")
    if ext.strip()
]

EXTENSION_DIR = "/opt/duckdb-extensions"


def test_the_suite_shall_know_which_extensions_to_expect():
    """An empty list would drop every parametrized test below and leave the suite green."""
    assert EXTENSIONS, (
        "DUCKDB_EXTENSIONS is empty; run-tests.sh exports it from buildtime.env"
    )


def in_sqlmesh(*args):
    """Run a command in the sqlmesh container, returning stdout ('' on non-zero exit)."""
    result = subprocess.run(
        ["podman", "exec", "sqlmesh", *args], capture_output=True, text=True,
    )
    return result.stdout.strip()


def in_sqlmesh_duckdb(sql):
    """Run sql in the sqlmesh container's own DuckDB — the one behind the duckdb gateway."""
    return in_sqlmesh(
        "uv", "run", "--project", "/sqlmesh", "python", "-c",
        f"import duckdb; print(duckdb.connect().execute({sql!r}).fetchone()[0])",
    )


def in_postgresql(*args):
    """Run a command in the postgresql container, returning stdout ('' on non-zero exit).

    Unlike conftest's podman(), a non-zero exit is no error: the callers run find and grep,
    where "no match" is an answer the assertion reports itself.
    """
    result = subprocess.run(
        ["podman", "exec", "postgresql", *args], capture_output=True, text=True,
    )
    return result.stdout.strip()


def duckdb_query(cur, sql):
    """Run sql inside DuckDB via pg_duckdb and return the single scalar it produces."""
    cur.execute("SELECT * FROM duckdb.query(%s)", (sql,))
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def installed(admin_db):
    """Map of extension name -> install_path, as DuckDB itself reports it."""
    with admin_db.cursor() as cur:
        cur.execute(
            "SELECT * FROM duckdb.query("
            "'SELECT extension_name || ''|'' || COALESCE(install_path, '''') "
            "FROM duckdb_extensions() WHERE installed')"
        )
        rows = [r[0] for r in cur.fetchall()]
    return dict(row.split("|", 1) for row in rows)


class TestExtensionsShipInTheImage:
    """The build downloaded the extensions into the image, not into the data volume."""

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_file_shall_be_in_the_image(self, extension):
        # The container filesystem rather than the database: this is what makes the
        # deployment independent of the network.
        found = in_postgresql("find", EXTENSION_DIR, "-name", f"{extension}.duckdb_extension")
        assert found, f"{extension} was not pre-downloaded into {EXTENSION_DIR}"

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_shall_have_its_info_sidecar(self, extension):
        # Without the .info metadata file DuckDB treats it as not installed and downloads
        # it again.
        found = in_postgresql(
            "find", EXTENSION_DIR, "-name", f"{extension}.duckdb_extension.info"
        )
        assert found, f"{extension} is missing its .info sidecar"


class TestServerReadsExtensionsFromTheImage:
    """The running server is configured to use the image directory."""

    def test_extension_directory_shall_point_into_the_image(self, admin_db):
        with admin_db.cursor() as cur:
            cur.execute("SHOW duckdb.extension_directory")
            assert cur.fetchone()[0] == EXTENSION_DIR

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_shall_be_installed_from_the_image(self, extension, installed):
        assert extension in installed, (
            f"DuckDB does not report {extension} as installed"
        )
        # A path under the data volume would mean a runtime download.
        assert installed[extension].startswith(EXTENSION_DIR), (
            f"{extension} is served from {installed[extension]}, not from the image"
        )


class TestVersionsMatch:
    """The shipped extension tree matches the DuckDB that pg_duckdb actually runs."""

    def test_shipped_tree_shall_match_the_running_duckdb_version(self, admin_db):
        # The tree is version-scoped, so extensions built for another version are
        # invisible and get downloaded instead. The Dockerfile derives the version from
        # the base image's libduckdb.so; this asserts that derivation held.
        with admin_db.cursor() as cur:
            running = duckdb_query(cur, "SELECT version()")
        shipped = in_postgresql("ls", EXTENSION_DIR)
        assert shipped == running, (
            f"extensions were built for DuckDB {shipped}, but pg_duckdb runs {running}"
        )


class TestSqlmeshGatewayCarriesTheSameExtensions:
    """The duckdb gateway (sqlmesh/config.py) is the second DuckDB in the system.

    It runs inside the sqlmesh container with its own copy of the extensions and its own
    DuckDB version. A community extension is built per DuckDB release, so if the gateway's
    DuckDB drifts from pg_duckdb's, DUCKDB_EXTENSIONS stops being installable for both.
    """

    def test_the_gateway_duckdb_shall_match_pg_duckdb(self):
        # sqlmesh/pyproject.toml pins duckdb to what the pgduckdb base image runs; when a
        # base image bump moves one and not the other, bump the pin to the version here.
        gateway = in_sqlmesh_duckdb("SELECT version()")
        shipped = in_postgresql("ls", EXTENSION_DIR)
        assert gateway == shipped, (
            f"the duckdb gateway runs DuckDB {gateway}, but pg_duckdb's extensions are "
            f"built for {shipped}; pin duckdb=={shipped.lstrip('v')} in sqlmesh/pyproject.toml"
        )

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_shall_load_in_the_gateway_without_a_download(self, extension):
        # LOAD without INSTALL fails unless the build put the extension where this DuckDB
        # looks for it.
        loaded = in_sqlmesh_duckdb(f"LOAD {extension}; SELECT '{extension}'")
        assert loaded == extension, (
            f"{extension} does not load in the sqlmesh image; it was not pre-installed "
            "for the gateway's DuckDB version"
        )


class TestExtensionsAreUsable:
    """A pre-downloaded extension loads and runs, rather than merely being present."""

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_shall_load(self, admin_db, extension):
        with admin_db.cursor() as cur:
            cur.execute("SELECT duckdb.load_extension(%s)", (extension,))
            loaded = duckdb_query(
                cur,
                "SELECT loaded FROM duckdb_extensions() "
                f"WHERE extension_name = '{extension}'",
            )
        assert loaded, f"{extension} did not load"

    @pytest.mark.skipif(
        "yaml" not in EXTENSIONS, reason="yaml is not in DUCKDB_EXTENSIONS"
    )
    def test_loaded_extension_shall_run_its_functions(self, admin_db):
        # yaml stands in for the set: one extension doing real work shows the shipped
        # files are complete and version-compatible.
        with admin_db.cursor() as cur:
            cur.execute("SELECT duckdb.load_extension('yaml')")
            value = duckdb_query(cur, "SELECT value_to_yaml({'offline': true})")
        assert "offline" in value


class TestNoRuntimeDownload:
    """Nothing in the init scripts reaches out to the extension repository."""

    def test_initdb_shall_not_install_extensions_at_runtime(self):
        # duckdb.install_extension() downloads from community-extensions.duckdb.org on
        # first start, which is the internet dependency to avoid.
        offenders = in_postgresql(
            "grep", "-rn", "--exclude-dir=.*",
            # Anchored to a statement, so gf_0002's prose about the call it no longer
            # makes is not mistaken for one.
            "-E", r"^[[:space:]]*(SELECT|PERFORM).*duckdb\.install_extension",
            "/docker-entrypoint-initdb.d/",
        )
        assert not offenders, (
            f"init scripts still download extensions at runtime:\n{offenders}"
        )

    def test_data_volume_shall_hold_no_downloaded_extensions(self):
        # A runtime download lands in the default directory under $PGDATA, so an
        # extension there means a machine without internet access would have failed.
        downloaded = in_postgresql(
            "find", "/var/lib/postgresql/data/pg_duckdb", "-name", "*.duckdb_extension",
        )
        assert not downloaded, (
            f"extensions were downloaded into the data volume at runtime: {downloaded}"
        )
