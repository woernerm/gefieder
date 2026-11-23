"""The dropzones SFTP endpoint: sessions become uploads, bad credentials bounce.

The dropzones are created directly in the database as the crudman role, then reached on
the published port exactly as an uploader would: username = dropzone name, password = its
secret, put files, disconnect. They use the default checker and converter baked into the
image, so the whole pipeline is exercised.
"""
from pathlib import Path

import paramiko
import pytest

from conftest import SFTP_PORT, dropzone_uploads, poll, volume_mountpoint

SECRET = "sftp-suite-secret"

# name -> (checker, converter), all created as enabled SFTP dropzones.
DROPZONES = {
    "sftp-suite-checked": ("reject_empty_files", ""),
    "sftp-suite-parquet": ("reject_empty_files", "csv_to_parquet"),
    "sftp-suite-strict": ("reject_empty_files", ""),
}


@pytest.fixture(scope="module")
def sftp_dropzones(make_dropzones):
    """The suite's SFTP dropzones, created for this module and removed afterwards."""
    return make_dropzones("sftp", SECRET, DROPZONES)


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


def _wait_for_uploads(db, dropzone_id, count=1, timeout=60):
    # Stored asynchronously after the disconnect, so poll; a row and its files are
    # committed together, so a visible row is complete.
    poll(lambda: len(dropzone_uploads(db, dropzone_id)) >= count, timeout)
    return dropzone_uploads(db, dropzone_id)


class TestSftpUpload:
    """Connect, put files, disconnect — nothing else is required of the uploader."""

    def test_two_valid_files_shall_both_be_stored(self, sftp_dropzones, crudman_db):
        files = {"alpha.csv": b"a,b\n1,2\n", "beta.csv": b"c,d\n3,4\n"}
        _upload("sftp-suite-checked", files)

        uploads = _wait_for_uploads(crudman_db, sftp_dropzones["sftp-suite-checked"])
        assert len(uploads) == 1, f"expected exactly one upload, got {uploads}"
        _, stored = uploads[0]
        assert sorted(Path(f).name for f in stored) == sorted(files)

        # The stored paths resolve on the uploads volume.
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
        # Only the converted files are stored.
        assert sorted(Path(f).name for f in stored) == ["feb.parquet", "jan.parquet"]

        mountpoint = Path(volume_mountpoint("uploads_data"))
        for stored_file in stored:
            assert (mountpoint / stored_file).read_bytes().startswith(b"PAR1"), (
                f"{stored_file} is not a parquet file"
            )

    def test_a_rejected_session_shall_store_neither_file(
        self, sftp_dropzones, crudman_db
    ):
        # reject_empty_files rejects the whole set, so the valid file of the same session
        # must not be stored either.
        _upload("sftp-suite-strict", {"empty.csv": b"", "good.csv": b"a,b\n1,2\n"})
        # Sessions are processed in disconnect order, so a later stored session makes the
        # rejection final.
        _upload("sftp-suite-strict", {"marker.csv": b"m\n1\n"})

        uploads = _wait_for_uploads(crudman_db, sftp_dropzones["sftp-suite-strict"])
        assert [
            sorted(Path(f).name for f in stored) for _, stored in uploads
        ] == [["marker.csv"]], f"the rejected session left uploads behind: {uploads}"

    def test_a_wrong_secret_shall_be_denied(self, sftp_dropzones):
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-checked", {}, password="wrong-secret")

    def test_a_wrong_address_shall_be_denied(self, sftp_dropzones):
        # The dropzone name is the login, so an unknown one and a non-SFTP one must both
        # be refused even with the right secret.
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-unknown", {})
        with pytest.raises(paramiko.AuthenticationException):
            _upload("sftp-suite-browser", {})
