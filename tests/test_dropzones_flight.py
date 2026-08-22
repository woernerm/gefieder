"""The dropzones Arrow Flight endpoint: committed uploads are stored, the rest is not.

The suite creates Arrow Flight dropzones directly in the database (as the crudman role,
which owns the tables) and then connects to the published Flight port exactly like an
uploader would: authenticate with the dropzone's name and secret, send one table per
DoPut, close the upload with the commit action. The dropzones use the default
checker/converter functions baked into the image, so the whole pipeline is exercised.
"""
import uuid
from pathlib import Path

import pyarrow
import pyarrow.flight as flight
import pytest

from conftest import FLIGHT_PORT, volume_mountpoint

SECRET = "flight-suite-secret"

# name -> (checker, converter); all created as enabled Arrow Flight dropzones.
DROPZONES = {
    "flight-suite-plain": ("", ""),
    "flight-suite-multi": ("", ""),
    "flight-suite-abandoned": ("", ""),
}


@pytest.fixture(scope="module")
def flight_dropzones(crudman_db):
    """The suite's Flight dropzones, created for this module and removed afterwards."""
    ids = {}
    with crudman_db.cursor() as cur:
        for name, (checker, converter) in DROPZONES.items():
            cur.execute(
                """
                INSERT INTO crudman.dropzones_dropzone
                    (name, description, upload_method, file_format, checker,
                     converter, default_validity, token, secret, require_login,
                     enabled, created_at)
                VALUES (%s, '', 'flight', '', %s, %s, 'until_replaced', %s, %s, true,
                        true, now())
                RETURNING id
                """,
                (name, checker, converter, str(uuid.uuid4()), SECRET),
            )
            ids[name] = cur.fetchone()[0]
        # A browser dropzone whose name must NOT work as a Flight login.
        cur.execute(
            """
            INSERT INTO crudman.dropzones_dropzone
                (name, description, upload_method, file_format, checker, converter,
                 default_validity, token, secret, require_login, enabled, created_at)
            VALUES ('flight-suite-browser', '', 'browser', '', '', '', 'until_replaced',
                    %s, %s, true, true, now())
            RETURNING id
            """,
            (str(uuid.uuid4()), SECRET),
        )
        ids["flight-suite-browser"] = cur.fetchone()[0]
    yield ids
    # Raw cleanup (no Django signals): the stack and its volumes are throwaway.
    with crudman_db.cursor() as cur:
        cur.execute(
            "DELETE FROM crudman.dropzones_uploadfile WHERE upload_id IN "
            "(SELECT id FROM crudman.dropzones_upload WHERE dropzone_id = ANY(%s))",
            (list(ids.values()),),
        )
        cur.execute(
            "DELETE FROM crudman.dropzones_upload WHERE dropzone_id = ANY(%s)",
            (list(ids.values()),),
        )
        cur.execute(
            "DELETE FROM crudman.dropzones_dropzone WHERE id = ANY(%s)",
            (list(ids.values()),),
        )


def _connect(name, secret=SECRET):
    """An authenticated client and its call options, like an uploader's first lines."""
    client = flight.connect(f"grpc://localhost:{FLIGHT_PORT}")
    options = flight.FlightCallOptions(
        headers=[client.authenticate_basic_token(name.encode(), secret.encode())]
    )
    return client, options


def _send(client, options, dropzone, tables):
    for table_name, table in tables.items():
        writer, _ = client.do_put(
            flight.FlightDescriptor.for_path(dropzone, table_name),
            table.schema,
            options,
        )
        writer.write_table(table)
        writer.close()


def _upload(name, tables, commit=True, secret=SECRET):
    """One uploader session: authenticate, send the tables, commit, disconnect."""
    client, options = _connect(name, secret)
    try:
        _send(client, options, name, tables)
        if commit:
            return next(
                client.do_action(flight.Action("commit", b""), options)
            ).body.to_pybytes().decode()
    finally:
        client.close()


def _uploads(db, dropzone_id):
    """The (upload id, stored file paths) pairs recorded for a dropzone."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, array_agg(f.file ORDER BY f.file)
            FROM crudman.dropzones_upload u
            JOIN crudman.dropzones_uploadfile f ON f.upload_id = u.id
            WHERE u.dropzone_id = %s
            GROUP BY u.id ORDER BY u.id
            """,
            (dropzone_id,),
        )
        return cur.fetchall()


class TestFlightUpload:
    """Authenticate, send tables, commit — the commit is what stores the upload."""

    def test_a_single_table_shall_be_stored_as_one_parquet_file(
        self, flight_dropzones, crudman_db
    ):
        _upload(
            "flight-suite-plain",
            {"issues": pyarrow.table({"id": [1, 2, 3], "state": ["a", "b", "c"]})},
        )

        uploads = _uploads(crudman_db, flight_dropzones["flight-suite-plain"])
        assert len(uploads) == 1, f"expected exactly one upload, got {uploads}"
        _, stored = uploads[0]
        assert [Path(f).name for f in stored] == ["issues.parquet"]

        # The stored path resolves on the uploads volume and holds a parquet file.
        mountpoint = Path(volume_mountpoint("uploads_data"))
        assert (mountpoint / stored[0]).read_bytes().startswith(b"PAR1")

    def test_several_tables_shall_become_one_upload_of_several_files(
        self, flight_dropzones, crudman_db
    ):
        _upload(
            "flight-suite-multi",
            {
                "issues": pyarrow.table({"id": [1, 2]}),
                "commits": pyarrow.table({"sha": ["a1", "b2"]}),
                "builds": pyarrow.table({"n": [7]}),
            },
        )

        uploads = _uploads(crudman_db, flight_dropzones["flight-suite-multi"])
        assert len(uploads) == 1, f"expected exactly one upload, got {uploads}"
        _, stored = uploads[0]
        # One upload, one file per table, each named after its table.
        assert sorted(Path(f).name for f in stored) == [
            "builds.parquet",
            "commits.parquet",
            "issues.parquet",
        ]

        mountpoint = Path(volume_mountpoint("uploads_data"))
        for stored_file in stored:
            assert (mountpoint / stored_file).read_bytes().startswith(b"PAR1"), (
                f"{stored_file} is not a parquet file"
            )

    def test_a_disconnect_without_commit_shall_store_nothing(
        self, flight_dropzones, crudman_db
    ):
        """Nothing is stored until the commit.

        A client that sends tables and then disconnects must leave no upload row
        and no files on the volume."""
        mountpoint = Path(volume_mountpoint("uploads_data"))
        dropzone_id = flight_dropzones["flight-suite-abandoned"]
        before = set(mountpoint.rglob("*"))

        _upload(
            "flight-suite-abandoned",
            {"issues": pyarrow.table({"id": [1, 2]}),
             "commits": pyarrow.table({"sha": ["a1"]})},
            commit=False,
        )

        # A committed upload to the same dropzone afterwards proves the endpoint kept
        # working and that the abandoned session's verdict is final, not merely late.
        _upload("flight-suite-abandoned", {"marker": pyarrow.table({"m": [1]})})

        uploads = _uploads(crudman_db, dropzone_id)
        assert [
            sorted(Path(f).name for f in stored) for _, stored in uploads
        ] == [["marker.parquet"]], (
            f"the abandoned session left uploads behind: {uploads}"
        )
        # No stray directory or file from the abandoned session either: the only new
        # paths on the volume belong to the marker upload.
        new = set(mountpoint.rglob("*")) - before
        assert all("marker.parquet" in str(p) or p.is_dir() for p in new), (
            f"the abandoned session left files behind: {sorted(map(str, new))}"
        )
        assert sum(1 for p in new if p.is_file()) == 1, (
            f"expected only the marker file, got {sorted(map(str, new))}"
        )

    def test_a_wrong_secret_shall_be_denied(self, flight_dropzones):
        with pytest.raises(flight.FlightUnauthenticatedError):
            _connect("flight-suite-plain", secret="wrong-secret")

    def test_a_wrong_dropzone_name_shall_be_denied(self, flight_dropzones):
        # The dropzone name is the Flight login: an unknown name and the name of a
        # non-Flight dropzone must both be refused, even with the right secret.
        with pytest.raises(flight.FlightUnauthenticatedError):
            _connect("flight-suite-unknown")
        with pytest.raises(flight.FlightUnauthenticatedError):
            _connect("flight-suite-browser")

    def test_a_call_without_credentials_shall_be_denied(self, flight_dropzones):
        client = flight.connect(f"grpc://localhost:{FLIGHT_PORT}")
        try:
            with pytest.raises(flight.FlightUnauthenticatedError):
                _send(client, None, "flight-suite-plain",
                      {"issues": pyarrow.table({"id": [1]})})
        finally:
            client.close()
