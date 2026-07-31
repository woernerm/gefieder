"""The DuckDB community extensions ship inside the image, so no internet is needed.

The extensions used to be downloaded by gf_0002 on first start, which made provisioning
fail on a machine without internet access. They are now fetched during the image build
into duckdb.extension_directory. These tests pin that down from both ends: the files are
in the image, and the server reports them installed from there and can actually run one.
"""
import os
import subprocess

import pytest

# The extensions the build was told to ship, straight from DUCKDB_EXTENSIONS in
# buildtime.env (run-tests.sh exports it), so trimming that list does not leave these
# tests asserting on extensions the image no longer contains.
EXTENSIONS = [
    ext.strip()
    for ext in os.environ.get("DUCKDB_EXTENSIONS", "").split(",")
    if ext.strip()
]

EXTENSION_DIR = "/opt/duckdb-extensions"


def test_the_suite_shall_know_which_extensions_to_expect():
    """An empty list would drop every parametrized test below and leave the suite green
    without having checked anything, so assert the setting reached pytest at all."""
    assert EXTENSIONS, (
        "DUCKDB_EXTENSIONS is empty; run-tests.sh exports it from buildtime.env"
    )


def in_postgresql(*args):
    """Run a command in the postgresql container, returning stdout ('' on non-zero exit).

    Unlike conftest's podman(), a non-zero exit is not an error here: the callers run
    find and grep, where "no match" is a meaningful answer the assertion reports itself.
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
        # Look in the container filesystem rather than the database: this is what makes
        # the deployment independent of the network, regardless of what DuckDB reports.
        found = in_postgresql("find", EXTENSION_DIR, "-name", f"{extension}.duckdb_extension")
        assert found, f"{extension} was not pre-downloaded into {EXTENSION_DIR}"

    @pytest.mark.parametrize("extension", EXTENSIONS)
    def test_extension_shall_have_its_info_sidecar(self, extension):
        # DuckDB treats an extension without the .info metadata file as not installed and
        # silently tries to download it again, which is exactly what must not happen.
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
        # A path under the data volume would mean the server downloaded it at runtime.
        assert installed[extension].startswith(EXTENSION_DIR), (
            f"{extension} is served from {installed[extension]}, not from the image"
        )


class TestVersionsMatch:
    """The shipped extension tree matches the DuckDB that pg_duckdb actually runs."""

    def test_shipped_tree_shall_match_the_running_duckdb_version(self, admin_db):
        # The tree is version-scoped (<version>/<platform>/), so extensions built for
        # another version are invisible and the server would fall back to downloading
        # them. The Dockerfile derives the version from the base image's libduckdb.so to
        # keep these two in step; this asserts that derivation actually held.
        with admin_db.cursor() as cur:
            running = duckdb_query(cur, "SELECT version()")
        shipped = in_postgresql("ls", EXTENSION_DIR)
        assert shipped == running, (
            f"extensions were built for DuckDB {shipped}, but pg_duckdb runs {running}"
        )


class TestExtensionsAreUsable:
    """A pre-downloaded extension actually loads and runs; the files are not just present."""

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
        # yaml stands in for the whole set: proving one loaded extension does real work
        # shows the shipped files are complete and version-compatible, not just present.
        with admin_db.cursor() as cur:
            cur.execute("SELECT duckdb.load_extension('yaml')")
            value = duckdb_query(cur, "SELECT value_to_yaml({'offline': true})")
        assert "offline" in value


class TestNoRuntimeDownload:
    """Nothing in the init scripts reaches out to the extension repository."""

    def test_initdb_shall_not_install_extensions_at_runtime(self):
        # duckdb.install_extension() downloads from community-extensions.duckdb.org on
        # first start, which is the internet dependency this change removes.
        offenders = in_postgresql(
            "grep", "-rn", "--exclude-dir=.*",
            # Anchored to a statement, so the "---" prose in gf_0002 that documents the
            # call it no longer makes is not mistaken for an executed one.
            "-E", r"^[[:space:]]*(SELECT|PERFORM).*duckdb\.install_extension",
            "/docker-entrypoint-initdb.d/",
        )
        assert not offenders, (
            f"init scripts still download extensions at runtime:\n{offenders}"
        )

    def test_data_volume_shall_hold_no_downloaded_extensions(self):
        # A runtime download lands in the default directory under $PGDATA. Finding an
        # extension there means the server fetched it itself, so a machine without
        # internet access would have failed here -- regardless of what the image ships.
        downloaded = in_postgresql(
            "find", "/var/lib/postgresql/data/pg_duckdb", "-name", "*.duckdb_extension",
        )
        assert not downloaded, (
            f"extensions were downloaded into the data volume at runtime: {downloaded}"
        )
