"""The DuckDB community extensions ship inside the sqlmesh image, so no internet is needed.

The duckdb gateway (sqlmesh/config.py) loads every extension in DUCKDB_EXTENSIONS when it
connects. They are fetched during the image build rather than on first connect, which
would fail on a machine without internet access.
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


def test_the_suite_shall_know_which_extensions_to_expect():
    """An empty list would drop every parametrized test below and leave the suite green."""
    assert EXTENSIONS, (
        "DUCKDB_EXTENSIONS is empty; run-tests.sh exports it from buildtime.env"
    )


def in_sqlmesh_duckdb(sql):
    """Run sql in the sqlmesh container's own DuckDB — the one behind the duckdb gateway.

    Returns stdout, or '' on a non-zero exit.
    """
    result = subprocess.run(
        ["podman", "exec", "sqlmesh", "uv", "run", "--project", "/sqlmesh", "python", "-c",
         f"import duckdb; print(duckdb.connect().execute({sql!r}).fetchone()[0])"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("extension", EXTENSIONS)
def test_extension_shall_load_in_the_gateway_without_a_download(extension):
    # LOAD without INSTALL fails unless the build put the extension where this DuckDB
    # looks for it.
    loaded = in_sqlmesh_duckdb(f"LOAD {extension}; SELECT '{extension}'")
    assert loaded == extension, (
        f"{extension} does not load in the sqlmesh image; it was not pre-installed "
        "for the gateway's DuckDB version"
    )


@pytest.mark.skipif("yaml" not in EXTENSIONS, reason="yaml is not in DUCKDB_EXTENSIONS")
def test_loaded_extension_shall_run_its_functions():
    # yaml stands in for the set: one extension doing real work shows the shipped files
    # are complete and version-compatible.
    value = in_sqlmesh_duckdb("LOAD yaml; SELECT value_to_yaml({'offline': true})")
    assert "offline" in value
