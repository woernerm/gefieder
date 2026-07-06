"""The dropzones SFTP endpoint: sessions become uploads, bad credentials bounce.

The suite creates SFTP dropzones directly in the database (as the crudman role, which
owns the tables) and then connects to the published SFTP port exactly like an uploader
would: username = dropzone name, password = the dropzone's secret, put files,
disconnect. The dropzones use the example checker/converter functions baked into the
image (reject_empty_files, csv_to_parquet), so the whole pipeline is exercised.
"""
import time
import uuid
from pathlib import Path

import paramiko
import pytest

from conftest import SFTP_PORT, volume_mountpoint

SECRET = "sftp-suite-secret"

# name -> (checker, converter); all created as enabled SFTP dropzones by the fixture.
DROPZONES = {
    "sftp-suite-checked": ("reject_empty_files", ""),
    "sftp-suite-parquet": ("reject_empty_files", "csv_to_parquet"),
    "sftp-suite-strict": ("reject_empty_files", ""),
}


@pytest.fixture(scope="module")
def sftp_dropzones(crudman_db):
    """The suite's SFTP dropzones, created for this module and removed afterwards."""
    ids = {}
    with crudman_db.cursor() as cur:
        for name, (checker, converter) in DROPZONES.items():
            cur.execute(
                """
                INSERT INTO crudman.dropzones_dropzone
                    (name, description, upload_method, file_format, checker,
                     converter, default_validity, token, secret, require_login,
                     enabled, created_at)
                VALUES (%s, '', 'sftp', '', %s, %s, 'until_replaced', %s, %s, true,
                        true, now())
                RETURNING id
                """,
                (name, checker, converter, str(uuid.uuid4()), SECRET),
            )
            ids[name] = cur.fetchone()[0]
        # A browser dropzone whose name must NOT work as an SFTP login.
        cur.execute(
            """
            INSERT INTO crudman.dropzones_dropzone
                (name, description, upload_method, file_format, checker, converter,
                 default_validity, token, secret, require_login, enabled, created_at)
            VALUES ('sftp-suite-browser', '', 'browser', '', '', '', 'until_replaced',
                    %s, %s, true, true, now())
            RETURNING id
            """,
            (str(uuid.uuid4()), SECRET),
        )
        ids["sftp-suite-browser"] = cur.fetchone()[0]
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


def _upload(name, files, password=SECRET):
    """One uploader session: connect, put the given files, disconnect."""
    transport = paramiko.Transport(("localhost", SFTP_PORT))
    try:
        transport.connect(username=name, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        for filename, content in files.items():
            with sftp.open(filename, "wb") as handle:
                handle.write(content)
        sftp.close()
    finally:
        transport.close()


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


def _wait_for_uploads(db, dropzone_id, count=1, timeout=60):
    # The upload is stored asynchronously after the disconnect, so poll for the rows;
    # each row and its files are committed together, so a visible row is complete.
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _uploads(db, dropzone_id)
        if len(rows) >= count:
            return rows
        time.sleep(2)
    return _uploads(db, dropzone_id)


class TestSftpUpload:
    """Connect, put files, disconnect — nothing else is required of the uploader."""

    def test_two_valid_files_shall_both_be_stored(self, sftp_dropzones, crudman_db):
        files = {"alpha.csv": b"a,b\n1,2\n", "beta.csv": b"c,d\n3,4\n"}
        _upload("sftp-suite-checked", files)

        uploads = _wait_for_uploads(crudman_db, sftp_dropzones["sftp-suite-checked"])
        assert len(uploads) == 1, f"expected exactly one upload, got {uploads}"
        _, stored = uploads[0]
        assert sorted(Path(f).name for f in stored) == sorted(files)

        # The stored paths resolve on the uploads volume with the uploaded content.
        mountpoint = Path(volume_mountpoint("uploads_data"))
        for stored_file in stored:
            assert (mountpoint / stored_file).read_bytes() == files[
                Path(stored_file).name
            ]

    def test_csv_files_shall_be_stored_converted_to_parquet(
        self, sftp_dropzones, crudman_db
    ):
        _upload(
            "sftp-suite-parquet",
            {"jan.csv": b"x,y\n1,2\n", "feb.csv": b"x,y\n3,4\n"},
        )

        uploads = _wait_for_uploads(crudman_db, sftp_dropzones["sftp-suite-parquet"])
        assert len(uploads) == 1, f"expected exactly one upload, got {uploads}"
        _, stored = uploads[0]
        # Only the converted files are stored; the uploaded CSVs are discarded.
        assert sorted(Path(f).name for f in stored) == ["feb.parquet", "jan.parquet"]

        mountpoint = Path(volume_mountpoint("uploads_data"))
        for stored_file in stored:
            assert (mountpoint / stored_file).read_bytes().startswith(b"PAR1"), (
                f"{stored_file} is not a parquet file"
            )

    def test_a_rejected_session_shall_store_neither_file(
        self, sftp_dropzones, crudman_db
    ):
        # The empty file makes reject_empty_files reject the whole set, so the valid
        # file of the same session must not be stored either (all or nothing).
        _upload("sftp-suite-strict", {"empty.csv": b"", "good.csv": b"a,b\n1,2\n"})
        # Sessions are processed in disconnect order, so once a later marker session
        # is stored, the rejected one's verdict is final.
        _upload("sftp-suite-strict", {"marker.csv": b"m\n1\n"})

        uploads = _wait_for_uploads(crudman_db, sftp_dropzones["sftp-suite-strict"])
        assert [
            sorted(Path(f).name for f in stored) for _, stored in uploads
        ] == [["marker.csv"]], f"the rejected session left uploads behind: {uploads}"

    def test_a_wrong_secret_shall_be_denied(self, sftp_dropzones):
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-checked", {}, password="wrong-secret")

    def test_a_wrong_address_shall_be_denied(self, sftp_dropzones):
        # The dropzone name is the SFTP "address" (the login): an unknown name and
        # the name of a non-SFTP dropzone must both be refused, even with the right
        # secret, exactly like an unknown token answers 404 on the web routes.
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-unknown", {})
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-browser", {})
