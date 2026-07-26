import re
import shlex
import shutil
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pyarrow
import pyarrow.flight as pyarrow_flight
import pyarrow.parquet as pyarrow_parquet
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone

from . import examples, flight, registry, services, sftp
from .admin import DropzoneAdmin, DropzoneForm, UploadAdmin
from .forms import UploadForm
from .models import Dropzone, Upload, UploadFile, remove_upload_directory
from .services import UploadError, process_upload


class TempMediaMixin:
    """Route MEDIA_ROOT into a per-test throwaway directory.

    Per test rather than per class because the database rolls back between tests but
    the filesystem would not; a shared directory would leak one test's stored files
    into another's assertions.
    """

    def setUp(self):
        super().setUp()
        self.media_root = Path(tempfile.mkdtemp(prefix="dropzones-test-media"))
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)


def upload_file(name="data.csv", content=b"a,b\n1,2\n"):
    return SimpleUploadedFile(name, content)


class RegistryTests(SimpleTestCase):
    def test_checker_decorator_registers_under_the_function_name(self):
        with patch.dict(registry._checkers):
            @registry.checker
            def test_check(files):
                pass

            self.assertIs(registry.get_checker("test_check"), test_check)
            self.assertIn("test_check", registry.checker_names())

    def test_converter_decorator_registers_under_the_function_name(self):
        with patch.dict(registry._converters):
            @registry.converter
            def test_convert(files, out_dir):
                pass

            self.assertIs(registry.get_converter("test_convert"), test_convert)
            self.assertIn("test_convert", registry.converter_names())

    def test_duplicate_name_raises(self):
        # Two different functions under one name (e.g. defined in two modules of the
        # functions folder) would make a dropzone ambiguous.
        def make_test_dup():
            def test_dup(files):
                pass

            return test_dup

        with patch.dict(registry._checkers):
            registry.checker(make_test_dup())
            with self.assertRaises(ImproperlyConfigured):
                registry.checker(make_test_dup())

    def test_reregistering_same_function_is_tolerated(self):
        # A module imported twice re-runs its decorators; that must not fail.
        with patch.dict(registry._checkers):
            def test_again(files):
                pass

            registry.checker(test_again)
            registry.checker(test_again)
            self.assertIs(registry.get_checker("test_again"), test_again)

    def test_unknown_names_raise_lookup_error(self):
        with self.assertRaises(LookupError):
            registry.get_checker("no_such_checker")
        with self.assertRaises(LookupError):
            registry.get_converter("no_such_converter")

    def test_labels_show_in_choices_and_default_to_the_name(self):
        with patch.dict(registry._checkers), patch.dict(registry._checker_labels):

            @registry.checker
            def test_plain(files):
                pass

            @registry.checker("A nice label")
            def test_labeled(files):
                pass

            choices = dict(registry.checker_choices())
            self.assertEqual(choices["test_plain"], "test_plain")
            self.assertEqual(choices["test_labeled"], "A nice label")

    def test_autodiscover_registered_the_default_functions(self):
        # DropzonesConfig.ready ran autodiscover at startup, importing the modules in
        # the functions folder; the shipped defaults must therefore be selectable.
        self.assertIn("reject_empty_files", registry.checker_names())
        for name in ("csv_to_parquet", "excel_to_parquet", "json_to_parquet"):
            self.assertIn(name, registry.converter_names())


class DropzoneModelTests(TestCase):
    def test_str_is_name(self):
        self.assertEqual(str(Dropzone(name="bank-exports")), "bank-exports")

    def test_upload_path_contains_token(self):
        zone = Dropzone.objects.create(name="zone")
        self.assertIn(str(zone.token), zone.upload_path())

    @override_settings(DEBUG=False, SERVER_NAME="reports.example.com")
    def test_upload_url_uses_https_and_server_name_in_production(self):
        zone = Dropzone.objects.create(name="zone")
        self.assertEqual(
            zone.upload_url(), f"https://reports.example.com{zone.upload_path()}"
        )

    @override_settings(DEBUG=True, SERVER_NAME="localhost")
    def test_upload_url_uses_http_in_debug(self):
        zone = Dropzone.objects.create(name="zone")
        self.assertTrue(zone.upload_url().startswith("http://localhost/"))

    def test_anonymous_may_upload_without_login_requirement(self):
        zone = Dropzone.objects.create(name="zone", require_login=False)
        self.assertTrue(zone.user_may_upload(AnonymousUser()))

    def test_anonymous_may_not_upload_with_login_requirement(self):
        zone = Dropzone.objects.create(name="zone", require_login=True)
        self.assertFalse(zone.user_may_upload(AnonymousUser()))

    def test_any_authenticated_user_may_upload_without_user_list(self):
        zone = Dropzone.objects.create(name="zone", require_login=True)
        user = User.objects.create_user("uploader")
        self.assertTrue(zone.user_may_upload(user))

    def test_only_listed_users_may_upload_with_user_list(self):
        zone = Dropzone.objects.create(name="zone", require_login=True)
        member = User.objects.create_user("member")
        outsider = User.objects.create_user("outsider")
        zone.allowed_users.add(member)
        self.assertTrue(zone.user_may_upload(member))
        self.assertFalse(zone.user_may_upload(outsider))

    def test_superuser_may_always_upload(self):
        zone = Dropzone.objects.create(name="zone", require_login=True)
        zone.allowed_users.add(User.objects.create_user("member"))
        boss = User.objects.create_superuser("boss")
        self.assertTrue(zone.user_may_upload(boss))

    def test_api_secret_open_when_no_login_required_and_no_secret(self):
        zone = Dropzone(require_login=False, secret="")
        self.assertTrue(zone.api_secret_matches(""))
        self.assertTrue(zone.api_secret_matches("anything"))

    def test_api_secret_must_match_when_login_required(self):
        zone = Dropzone(require_login=True, secret="right")
        self.assertTrue(zone.api_secret_matches("right"))
        self.assertFalse(zone.api_secret_matches("wrong"))
        self.assertFalse(zone.api_secret_matches(""))

    def test_api_secret_fails_closed_when_login_required_but_unset(self):
        zone = Dropzone(require_login=True, secret="")
        self.assertFalse(zone.api_secret_matches(""))
        self.assertFalse(zone.api_secret_matches("anything"))

    def test_sftp_secret_must_match(self):
        zone = Dropzone(secret="right")
        self.assertTrue(zone.sftp_secret_matches("right"))
        self.assertFalse(zone.sftp_secret_matches("wrong"))
        self.assertFalse(zone.sftp_secret_matches(""))

    def test_sftp_secret_fails_closed_when_unset(self):
        # Unlike the API there is no unguessable URL token, so an empty secret must
        # never mean "open" — not even without a login requirement.
        zone = Dropzone(require_login=False, secret="")
        self.assertFalse(zone.sftp_secret_matches(""))
        self.assertFalse(zone.sftp_secret_matches("anything"))

    @override_settings(SERVER_NAME="reports.example.com", SFTP_PORT=2222)
    def test_sftp_address_uses_name_and_port(self):
        zone = Dropzone(name="bank-exports")
        self.assertEqual(
            zone.sftp_address(), "sftp://bank-exports@reports.example.com:2222"
        )

    @override_settings(DEBUG=False, SERVER_NAME="reports.example.com")
    def test_api_upload_url_uses_https_and_server_name(self):
        zone = Dropzone.objects.create(name="api-zone")
        self.assertTrue(
            zone.api_upload_url().startswith("https://reports.example.com/")
        )
        self.assertIn(str(zone.token), zone.api_upload_url())

    @override_settings(DEBUG=False, SERVER_NAME="reports.example.com")
    def test_webhook_url_uses_https_and_server_name(self):
        zone = Dropzone.objects.create(name="webhook-zone")
        self.assertTrue(zone.webhook_url().startswith("https://reports.example.com/"))
        self.assertIn(str(zone.token), zone.webhook_url())


class UploadModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="zone")

    def make_upload(self, uploaded_at, valid_from=None, valid_until=None):
        upload = Upload.objects.create(
            dropzone=self.zone,
            valid_from=valid_from,
            valid_until=valid_until,
            directory="dropzones/x/y/",
            sha256="0" * 64,
        )
        # uploaded_at is auto_now_add, so backdating needs a queryset update.
        Upload.objects.filter(pk=upload.pk).update(uploaded_at=uploaded_at)
        upload.refresh_from_db()
        return upload

    def test_str_mentions_dropzone(self):
        self.assertIn("zone", str(Upload(dropzone=self.zone)))

    def test_uploadfile_str_is_the_bare_file_name(self):
        # The bare name, because the admin shows it wherever str() is used and the
        # directory is Upload-level information.
        self.assertEqual(str(UploadFile(file="dropzones/x/y/a.csv")), "a.csv")
        self.assertEqual(str(UploadFile()), "(no file)")

    def test_valid_at_open_bounds_cover_everything(self):
        now = timezone.now()
        always = self.make_upload(now)
        self.assertEqual(list(Upload.objects.valid_at(now - timedelta(days=999))), [always])
        self.assertEqual(list(Upload.objects.valid_at(now + timedelta(days=999))), [always])

    def test_valid_at_respects_both_bounds(self):
        now = timezone.now()
        upload = self.make_upload(
            now, valid_from=now, valid_until=now + timedelta(days=1)
        )
        self.assertEqual(list(Upload.objects.valid_at(now)), [upload])  # start inclusive
        self.assertEqual(list(Upload.objects.valid_at(now - timedelta(seconds=1))), [])
        # The end is exclusive, so the next upload's start does not overlap.
        self.assertEqual(list(Upload.objects.valid_at(now + timedelta(days=1))), [])

    def test_valid_at_orders_newest_first(self):
        now = timezone.now()
        older = self.make_upload(now - timedelta(hours=2))
        newer = self.make_upload(now - timedelta(hours=1))
        self.assertEqual(list(Upload.objects.valid_at(now)), [newer, older])


class RemoveUploadDirectoryTests(TempMediaMixin, SimpleTestCase):
    def test_removes_directory_below_media_root(self):
        target = self.media_root / "dropzones" / "1" / "abc"
        target.mkdir(parents=True)
        (target / "file.txt").write_text("x")
        remove_upload_directory("dropzones/1/abc/")
        self.assertFalse(target.exists())

    def test_ignores_empty_and_missing_directories(self):
        remove_upload_directory("")
        remove_upload_directory("dropzones/1/never-existed/")

    def test_refuses_paths_escaping_media_root(self):
        outside = Path(tempfile.mkdtemp(prefix="dropzones-test-outside"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        remove_upload_directory(f"../{outside.name}/")
        self.assertTrue(outside.exists())

    def test_refuses_media_root_itself(self):
        (self.media_root / "keep.txt").write_text("x")
        remove_upload_directory(".")
        self.assertTrue((self.media_root / "keep.txt").exists())


class CombinedHashTests(SimpleTestCase):
    def write(self, directory, name, content):
        path = Path(directory) / name
        path.write_bytes(content)
        return path

    def test_hash_is_order_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.write(tmp, "a", b"first")
            b = self.write(tmp, "b", b"second")
            self.assertEqual(
                services._combined_hash([a, b]), services._combined_hash([b, a])
            )

    def test_hash_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.write(tmp, "a", b"first")
            b = self.write(tmp, "b", b"second")
            c = self.write(tmp, "c", b"third")
            self.assertNotEqual(
                services._combined_hash([a, b]), services._combined_hash([a, c])
            )


class ProcessUploadTests(TempMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="zone")
        cls.user = User.objects.create_user("uploader")

    def stored_files(self):
        return sorted(p for p in self.media_root.rglob("*") if p.is_file())

    def test_stores_files_row_and_metadata(self):
        start = timezone.now()
        end = start + timedelta(days=30)
        upload = process_upload(
            self.zone,
            [upload_file("a.csv"), upload_file("b.csv", b"c,d\n3,4\n")],
            valid_from=start,
            valid_until=end,
            user=self.user,
        )
        self.assertEqual(upload.dropzone, self.zone)
        self.assertEqual(upload.uploaded_by, self.user)
        self.assertEqual((upload.valid_from, upload.valid_until), (start, end))
        self.assertEqual(len(upload.sha256), 64)
        self.assertTrue(upload.directory.startswith(f"dropzones/{self.zone.pk}/"))
        self.assertTrue(upload.directory.endswith("/"))
        names = sorted(f.file.name for f in upload.files.all())
        self.assertEqual(
            names, [upload.directory + "a.csv", upload.directory + "b.csv"]
        )
        for name in names:
            self.assertTrue((self.media_root / name).is_file())

    def test_anonymous_upload_stores_no_user(self):
        for anonymous in (None, AnonymousUser()):
            upload = process_upload(self.zone, [upload_file()], user=anonymous)
            self.assertIsNone(upload.uploaded_by)

    def test_no_files_is_rejected(self):
        with self.assertRaises(UploadError):
            process_upload(self.zone, [])

    def test_duplicate_file_names_are_kept_apart(self):
        upload = process_upload(
            self.zone, [upload_file("same.csv", b"one"), upload_file("same.csv", b"two")]
        )
        self.assertEqual(upload.files.count(), 2)

    def test_client_directory_parts_are_stripped(self):
        upload = process_upload(self.zone, [upload_file("../../evil.txt", b"x")])
        self.assertEqual(upload.files.get().file.name, upload.directory + "evil.txt")

    def test_checker_rejection_stores_nothing(self):
        def angry(files):
            raise ValueError("Column 'amount' is missing.")

        zone = Dropzone.objects.create(name="checked", checker="test_angry")
        with patch.dict(registry._checkers, {"test_angry": angry}):
            with self.assertRaisesMessage(UploadError, "Column 'amount' is missing."):
                process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_checker_receives_the_spooled_files(self):
        seen = []

        def collect(files):
            seen.extend(files)

        zone = Dropzone.objects.create(name="checked", checker="test_collect")
        with patch.dict(registry._checkers, {"test_collect": collect}):
            process_upload(zone, [upload_file("a.csv", b"a"), upload_file("b.csv", b"b")])
        self.assertEqual([p.name for p in seen], ["a.csv", "b.csv"])

    def test_unknown_checker_is_rejected(self):
        # e.g. a dropzone kept its checker name while the function was removed from a
        # newer image.
        zone = Dropzone.objects.create(name="stale", checker="test_gone")
        with self.assertRaises(UploadError):
            process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)

    def test_converter_output_is_stored_instead_of_originals(self):
        def convert(files, out_dir):
            (out_dir / "combined.parquet").write_bytes(b"parquet")

        zone = Dropzone.objects.create(name="converted", converter="test_convert")
        with patch.dict(registry._converters, {"test_convert": convert}):
            upload = process_upload(zone, [upload_file("a.csv"), upload_file("b.csv")])
        self.assertEqual(
            [f.file.name for f in upload.files.all()],
            [upload.directory + "combined.parquet"],
        )
        stored = self.stored_files()
        self.assertEqual([p.name for p in stored], ["combined.parquet"])

    def test_converter_exception_stores_nothing(self):
        def broken(files, out_dir):
            raise RuntimeError("Sheet 'Mapping' not found.")

        zone = Dropzone.objects.create(name="converted", converter="test_broken")
        with patch.dict(registry._converters, {"test_broken": broken}):
            with self.assertRaisesMessage(UploadError, "Sheet 'Mapping' not found."):
                process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_converter_producing_nothing_is_rejected(self):
        zone = Dropzone.objects.create(name="converted", converter="test_empty")
        with patch.dict(registry._converters, {"test_empty": lambda f, o: None}):
            with self.assertRaises(UploadError):
                process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)

    def test_unknown_converter_is_rejected(self):
        zone = Dropzone.objects.create(name="stale", converter="test_gone")
        with self.assertRaises(UploadError):
            process_upload(zone, [upload_file()])

    def test_default_csv_to_parquet_converter_works_end_to_end(self):
        import polars as pl

        zone = Dropzone.objects.create(name="polars", converter="csv_to_parquet")
        upload = process_upload(zone, [upload_file("numbers.csv", b"a,b\n1,2\n")])
        stored = upload.files.get()
        self.assertTrue(stored.file.name.endswith("numbers.parquet"))
        frame = pl.read_parquet(self.media_root / stored.file.name)
        self.assertEqual(frame.shape, (1, 2))

    def test_default_excel_to_parquet_stores_one_parquet_per_sheet(self):
        import polars as pl

        workbook = (Path(__file__).parent / "testdata" / "sheets.xlsx").read_bytes()
        zone = Dropzone.objects.create(name="excel", converter="excel_to_parquet")
        upload = process_upload(zone, [upload_file("report.xlsx", workbook)])
        stored = {Path(f.file.name).name: f.file.name for f in upload.files.all()}
        self.assertEqual(
            sorted(stored), ["report_Costs.parquet", "report_Hours.parquet"]
        )
        hours = pl.read_parquet(self.media_root / stored["report_Hours.parquet"])
        self.assertEqual(hours["person"].to_list(), ["ann", "bob"])

    def test_default_json_to_parquet_converter_works_end_to_end(self):
        import polars as pl

        zone = Dropzone.objects.create(name="json", converter="json_to_parquet")
        upload = process_upload(
            zone, [upload_file("data.json", b'[{"a": 1, "b": 2}, {"a": 3, "b": 4}]')]
        )
        stored = upload.files.get()
        self.assertTrue(stored.file.name.endswith("data.parquet"))
        frame = pl.read_parquet(self.media_root / stored.file.name)
        self.assertEqual(frame.shape, (2, 2))

    def test_failure_after_storing_files_cleans_them_up(self):
        # If anything fails while the transaction is open, the rows roll back and the
        # already-written files must be removed again.
        with patch.object(services, "_clip_replaced", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                process_upload(self.zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_hash_matches_uploaded_content_regardless_of_order(self):
        first = process_upload(
            self.zone, [upload_file("a.csv", b"one"), upload_file("b.csv", b"two")]
        )
        second = process_upload(
            self.zone, [upload_file("b.csv", b"two"), upload_file("a.csv", b"one")]
        )
        self.assertEqual(first.sha256, second.sha256)


class ValidityClippingTests(TempMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="zone")
        cls.other_zone = Dropzone.objects.create(name="other")

    def upload_with(self, zone=None, **validity):
        return process_upload(zone or self.zone, [upload_file()], **validity)

    def test_until_replacement_is_clipped_by_next_upload(self):
        start = timezone.now()
        previous = self.upload_with(valid_from=start)
        replacement_start = start + timedelta(days=7)
        self.upload_with(valid_from=replacement_start)
        previous.refresh_from_db()
        self.assertEqual(previous.valid_until, replacement_start)

    def test_always_valid_stays_untouched(self):
        eternal = self.upload_with()  # no dates: always valid
        self.upload_with(valid_from=timezone.now())
        eternal.refresh_from_db()
        self.assertIsNone(eternal.valid_from)
        self.assertIsNone(eternal.valid_until)

    def test_fixed_period_stays_untouched(self):
        start = timezone.now()
        fixed = self.upload_with(valid_from=start, valid_until=start + timedelta(days=3))
        self.upload_with(valid_from=start + timedelta(days=1))
        fixed.refresh_from_db()
        self.assertEqual(fixed.valid_until, start + timedelta(days=3))

    def test_retroactive_upload_does_not_clip_later_starts(self):
        start = timezone.now()
        current = self.upload_with(valid_from=start)
        # A back-dated correction must not shorten an upload that starts after it.
        self.upload_with(valid_from=start - timedelta(days=7))
        current.refresh_from_db()
        self.assertIsNone(current.valid_until)

    def test_always_valid_upload_clips_nothing(self):
        open_ended = self.upload_with(valid_from=timezone.now())
        self.upload_with()  # always valid
        open_ended.refresh_from_db()
        self.assertIsNone(open_ended.valid_until)

    def test_other_dropzones_are_not_clipped(self):
        foreign = self.upload_with(zone=self.other_zone, valid_from=timezone.now())
        self.upload_with(valid_from=timezone.now() + timedelta(days=1))
        foreign.refresh_from_db()
        self.assertIsNone(foreign.valid_until)

    def test_resolution_over_a_sequence_of_uploads(self):
        # The headline scenario: an eternal fallback, a mapping valid until replaced,
        # and its replacement. valid_at must pick the right one for any timestamp.
        base = timezone.now()
        fallback = self.upload_with()
        first = self.upload_with(valid_from=base)
        second = self.upload_with(valid_from=base + timedelta(days=10))
        lookup = lambda ts: Upload.objects.filter(dropzone=self.zone).valid_at(ts).first()
        self.assertEqual(lookup(base - timedelta(days=1)), fallback)
        self.assertEqual(lookup(base + timedelta(days=5)), first)
        self.assertEqual(lookup(base + timedelta(days=20)), second)


class DeletionCleanupTests(TempMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="zone")

    def test_deleting_an_upload_removes_its_files_and_directory(self):
        upload = process_upload(self.zone, [upload_file("a.csv"), upload_file("b.csv")])
        directory = self.media_root / upload.directory
        self.assertTrue(directory.is_dir())
        upload.delete()
        self.assertFalse(directory.exists())

    def test_deleting_a_single_file_row_removes_only_that_file(self):
        upload = process_upload(self.zone, [upload_file("a.csv"), upload_file("b.csv")])
        first, second = list(upload.files.all())
        first_name = first.file.name  # cleared by the deletion signal
        first.delete()
        self.assertFalse((self.media_root / first_name).exists())
        self.assertTrue((self.media_root / second.file.name).exists())

    def test_dropzone_with_uploads_is_protected(self):
        from django.db.models import ProtectedError

        process_upload(self.zone, [upload_file()])
        with self.assertRaises(ProtectedError):
            self.zone.delete()


class UploadFormTests(SimpleTestCase):
    def form(self, data, files=None, dropzone=None):
        return UploadForm(data, files or {"files": upload_file()}, dropzone=dropzone)

    def multi_file_data(self):
        return {"files": [upload_file("a.csv"), upload_file("b.csv")]}

    def test_always_valid_clears_both_dates(self):
        form = self.form(
            {
                "validity": UploadForm.ALWAYS,
                "valid_from": "2026-01-01T00:00",
                "valid_until": "2026-02-01T00:00",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["valid_from"])
        self.assertIsNone(form.cleaned_data["valid_until"])

    def test_until_replaced_defaults_start_to_now(self):
        before = timezone.now()
        form = self.form({"validity": UploadForm.UNTIL_REPLACED})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertGreaterEqual(form.cleaned_data["valid_from"], before)
        self.assertIsNone(form.cleaned_data["valid_until"])

    def test_until_replaced_ignores_a_submitted_start(self):
        # The option reads "from now on" and hides the date fields, so a stray
        # submitted value must not sneak in as the start.
        before = timezone.now()
        form = self.form(
            {"validity": UploadForm.UNTIL_REPLACED, "valid_from": "2020-01-01T08:00"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertGreaterEqual(form.cleaned_data["valid_from"], before)
        self.assertIsNone(form.cleaned_data["valid_until"])

    def test_period_empty_end_means_forever(self):
        form = self.form({"validity": UploadForm.PERIOD, "valid_from": "2026-07-01T08:00"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["valid_from"].year, 2026)
        self.assertIsNone(form.cleaned_data["valid_until"])

    def test_period_empty_start_defaults_to_now(self):
        before = timezone.now()
        form = self.form({"validity": UploadForm.PERIOD, "valid_until": "2200-01-01T00:00"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertGreaterEqual(form.cleaned_data["valid_from"], before)
        self.assertEqual(form.cleaned_data["valid_until"].year, 2200)

    def test_period_requires_end_after_start(self):
        form = self.form(
            {
                "validity": UploadForm.PERIOD,
                "valid_from": "2026-07-02T08:00",
                "valid_until": "2026-07-01T08:00",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("after its start", str(form.errors))

    def test_period_keeps_valid_dates(self):
        form = self.form(
            {
                "validity": UploadForm.PERIOD,
                "valid_from": "2026-07-01T08:00",
                "valid_until": "2026-07-31T08:00",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertLess(form.cleaned_data["valid_from"], form.cleaned_data["valid_until"])

    def test_dropzone_default_validity_preselects_the_mode(self):
        zone = Dropzone(default_validity=Dropzone.Validity.ALWAYS)
        form = UploadForm(dropzone=zone)
        self.assertEqual(form.fields["validity"].initial, Dropzone.Validity.ALWAYS)

    def test_multiple_files_clean_to_a_list(self):
        form = self.form({"validity": UploadForm.ALWAYS}, files=self.multi_file_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual([f.name for f in form.cleaned_data["files"]], ["a.csv", "b.csv"])

    def test_single_file_cleans_to_a_list_too(self):
        form = self.form({"validity": UploadForm.ALWAYS})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["files"]), 1)

    def test_files_are_required(self):
        form = UploadForm({"validity": UploadForm.ALWAYS}, {})
        self.assertFalse(form.is_valid())
        self.assertIn("files", form.errors)

    def test_accept_attribute_from_extension_list(self):
        zone = Dropzone(name="zone", file_format=".csv, .xlsx")
        form = UploadForm(dropzone=zone)
        self.assertEqual(form.fields["files"].widget.attrs["accept"], ".csv,.xlsx")

    def test_no_accept_attribute_for_prose_formats(self):
        zone = Dropzone(name="zone", file_format="Excel files as agreed")
        form = UploadForm(dropzone=zone)
        self.assertNotIn("accept", form.fields["files"].widget.attrs)


class UploadViewTests(TempMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="open-zone", require_login=False)

    def url(self, zone=None):
        return reverse("dropzones:upload", kwargs={"token": (zone or self.zone).token})

    def test_unknown_token_is_404(self):
        import uuid

        response = self.client.get(
            reverse("dropzones:upload", kwargs={"token": uuid.uuid4()})
        )
        self.assertEqual(response.status_code, 404)

    def test_disabled_dropzone_is_404(self):
        zone = Dropzone.objects.create(name="off", require_login=False, enabled=False)
        self.assertEqual(self.client.get(self.url(zone)).status_code, 404)

    def test_non_browser_dropzone_is_404(self):
        zone = Dropzone.objects.create(
            name="sftp-only", require_login=False, upload_method=Dropzone.Method.SFTP
        )
        self.assertEqual(self.client.get(self.url(zone)).status_code, 404)

    def test_anonymous_gets_page_when_no_login_required(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "open-zone")

    def test_anonymous_is_sent_to_login_when_required(self):
        zone = Dropzone.objects.create(name="closed", require_login=True)
        response = self.client.get(self.url(zone))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
        self.assertIn(self.url(zone), response.url)

    def test_unlisted_user_is_forbidden(self):
        zone = Dropzone.objects.create(name="closed", require_login=True)
        zone.allowed_users.add(User.objects.create_user("member"))
        self.client.force_login(User.objects.create_user("outsider"))
        self.assertEqual(self.client.get(self.url(zone)).status_code, 403)

    def test_listed_user_gets_page(self):
        zone = Dropzone.objects.create(name="closed", require_login=True)
        member = User.objects.create_user("member")
        zone.allowed_users.add(member)
        self.client.force_login(member)
        self.assertEqual(self.client.get(self.url(zone)).status_code, 200)

    def test_upload_stores_files_and_redirects(self):
        # follow=True: the success message is consumed by the redirected-to page.
        response = self.client.post(
            self.url(),
            {
                "files": [upload_file("a.csv"), upload_file("b.csv")],
                "validity": UploadForm.UNTIL_REPLACED,
            },
            follow=True,
        )
        self.assertRedirects(response, self.url())
        upload = Upload.objects.get()
        self.assertEqual(upload.files.count(), 2)
        self.assertIsNone(upload.uploaded_by)
        self.assertContains(response, "2 file(s) stored")

    def test_upload_records_the_logged_in_user(self):
        user = User.objects.create_user("uploader")
        self.client.force_login(user)
        self.client.post(
            self.url(), {"files": upload_file(), "validity": UploadForm.ALWAYS}
        )
        self.assertEqual(Upload.objects.get().uploaded_by, user)

    def test_rejected_upload_shows_the_checker_message(self):
        def angry(files):
            raise ValueError("Bad header row.")

        zone = Dropzone.objects.create(
            name="strict", require_login=False, checker="test_angry"
        )
        with patch.dict(registry._checkers, {"test_angry": angry}):
            response = self.client.post(
                self.url(zone),
                {"files": upload_file(), "validity": UploadForm.ALWAYS},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bad header row.")
        self.assertEqual(Upload.objects.count(), 0)

    def test_invalid_form_is_re_rendered(self):
        response = self.client.post(
            self.url(),
            {
                "files": upload_file(),
                "validity": UploadForm.PERIOD,
                "valid_from": "2026-07-02T08:00",
                "valid_until": "2026-07-01T08:00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "after its start")
        self.assertEqual(Upload.objects.count(), 0)

    def test_page_headline_is_the_app_name(self):
        from django.conf import settings as project_settings

        # The headline block mirrors the Unfold login screen: the app name carries the
        # login title's utility classes, the dropzone name sits below it.
        response = self.client.get(self.url())
        self.assertContains(
            response,
            '<span class="block font-semibold text-primary-600 tracking-tight '
            f'text-xl dark:text-primary-500">{project_settings.APP_NAME}</span>',
            html=True,
        )
        self.assertContains(
            response,
            '<span class="block mt-3 text-font-important-light '
            'dark:text-font-important-dark">open-zone</span>',
            html=True,
        )


class APIUploadTests(TempMediaMixin, TestCase):
    """The POST endpoint an unattended client uses; feeds the same pipeline as the
    browser upload but authenticates with a bearer token instead of a session."""

    @classmethod
    def setUpTestData(cls):
        cls.open_zone = Dropzone.objects.create(
            name="open-api", upload_method=Dropzone.Method.API, require_login=False
        )
        cls.secured_zone = Dropzone.objects.create(
            name="secured-api",
            upload_method=Dropzone.Method.API,
            require_login=True,
            secret="s3cret-token",
        )

    def url(self, zone):
        return reverse("dropzones:api_upload", kwargs={"token": zone.token})

    def post(self, zone, token=None, files=None, **data):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        payload = {"files": files if files is not None else upload_file(), **data}
        return self.client.post(self.url(zone), payload, **headers)

    def test_open_dropzone_accepts_without_a_token(self):
        response = self.post(self.open_zone, files=[upload_file("a.csv"), upload_file("b.csv")])
        self.assertEqual(response.status_code, 201)
        body = response.json()
        upload = Upload.objects.get()
        self.assertEqual(body["upload_id"], upload.pk)
        self.assertEqual(body["files"], 2)
        self.assertEqual(body["sha256"], upload.sha256)
        self.assertIsNone(upload.uploaded_by)

    def test_secured_dropzone_accepts_the_matching_token(self):
        response = self.post(self.secured_zone, token="s3cret-token")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Upload.objects.count(), 1)

    def test_secured_dropzone_rejects_a_wrong_token(self):
        response = self.post(self.secured_zone, token="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Upload.objects.count(), 0)

    def test_secured_dropzone_rejects_a_missing_token(self):
        response = self.post(self.secured_zone)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Upload.objects.count(), 0)

    def test_login_required_without_a_token_configured_rejects_everyone(self):
        # A misconfiguration (require_login but no secret) must fail closed, not open.
        zone = Dropzone.objects.create(
            name="misconfigured", upload_method=Dropzone.Method.API, require_login=True
        )
        self.assertEqual(self.post(zone).status_code, 401)
        self.assertEqual(self.post(zone, token="anything").status_code, 401)

    def test_unknown_token_is_404(self):
        import uuid

        url = reverse("dropzones:api_upload", kwargs={"token": uuid.uuid4()})
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_disabled_dropzone_is_404(self):
        self.open_zone.enabled = False
        self.open_zone.save()
        self.assertEqual(self.post(self.open_zone).status_code, 404)

    def test_browser_dropzone_is_404_on_the_api_endpoint(self):
        # The browser and API routes both key on the token but are separate methods.
        zone = Dropzone.objects.create(name="browser-only", require_login=False)
        self.assertEqual(self.post(zone).status_code, 404)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(self.url(self.open_zone)).status_code, 405)

    def test_no_files_is_a_400(self):
        response = self.post(self.open_zone, files=[])
        self.assertEqual(response.status_code, 400)
        self.assertIn("no files", response.json()["error"])
        self.assertEqual(Upload.objects.count(), 0)

    def test_checker_rejection_is_a_400_with_the_message(self):
        def angry(files):
            raise ValueError("Bad header row.")

        zone = Dropzone.objects.create(
            name="strict-api", upload_method=Dropzone.Method.API, require_login=False,
            checker="test_angry",
        )
        with patch.dict(registry._checkers, {"test_angry": angry}):
            response = self.post(zone)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Bad header row.")
        self.assertEqual(Upload.objects.count(), 0)

    def test_default_validity_is_until_replaced(self):
        before = timezone.now()
        self.post(self.open_zone)
        upload = Upload.objects.get()
        self.assertGreaterEqual(upload.valid_from, before)
        self.assertIsNone(upload.valid_until)

    def test_always_validity_leaves_both_bounds_open(self):
        self.post(self.open_zone, validity=UploadForm.ALWAYS)
        upload = Upload.objects.get()
        self.assertIsNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)

    def test_dropzone_default_validity_applies_without_an_explicit_mode(self):
        zone = Dropzone.objects.create(
            name="always-api",
            upload_method=Dropzone.Method.API,
            require_login=False,
            default_validity=Dropzone.Validity.ALWAYS,
        )
        self.post(zone)
        upload = Upload.objects.get()
        self.assertIsNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)

    def test_an_explicit_mode_overrides_the_dropzone_default(self):
        zone = Dropzone.objects.create(
            name="always-api-overridden",
            upload_method=Dropzone.Method.API,
            require_login=False,
            default_validity=Dropzone.Validity.ALWAYS,
        )
        before = timezone.now()
        self.post(zone, validity=UploadForm.UNTIL_REPLACED)
        upload = Upload.objects.get()
        self.assertGreaterEqual(upload.valid_from, before)
        self.assertIsNone(upload.valid_until)

    def test_period_validity_parses_iso_dates(self):
        self.post(
            self.open_zone,
            validity=UploadForm.PERIOD,
            valid_from="2026-07-01T08:00:00",
            valid_until="2026-07-31T08:00:00",
        )
        upload = Upload.objects.get()
        self.assertEqual(upload.valid_from.year, 2026)
        self.assertEqual(upload.valid_until.day, 31)

    def test_period_end_before_start_is_a_400(self):
        response = self.post(
            self.open_zone,
            validity=UploadForm.PERIOD,
            valid_from="2026-07-02T08:00:00",
            valid_until="2026-07-01T08:00:00",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)

    def test_unparseable_date_is_a_400(self):
        response = self.post(
            self.open_zone, validity=UploadForm.PERIOD, valid_from="not-a-date"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)

    def test_unknown_validity_mode_is_a_400(self):
        response = self.post(self.open_zone, validity="whenever")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)


class WebhookUploadTests(TempMediaMixin, TestCase):
    """The GET endpoint for devices that can only call a URL with values substituted
    into it (e.g. a Shelly relay reporting a temperature): the query parameters are
    stored as a one-row CSV through the same pipeline as every other method."""

    @classmethod
    def setUpTestData(cls):
        cls.open_zone = Dropzone.objects.create(
            name="open-webhook",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=False,
        )
        cls.secured_zone = Dropzone.objects.create(
            name="secured-webhook",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=True,
            secret="s3cret-token",
        )

    def url(self, zone):
        return reverse("dropzones:webhook_upload", kwargs={"token": zone.token})

    def get(self, zone, query="temperature=21.5&humidity=60", token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(f"{self.url(zone)}?{query}", **headers)

    def stored_text(self, upload):
        # Raw bytes, so the assertions see the csv module's \r\n line endings.
        return (self.media_root / upload.files.get().file.name).read_bytes().decode()

    def test_readings_are_stored_as_a_one_row_csv(self):
        response = self.get(self.open_zone)
        self.assertEqual(response.status_code, 201)
        upload = Upload.objects.get()
        self.assertEqual(response.json()["upload_id"], upload.pk)
        self.assertEqual(response.json()["files"], 1)
        self.assertIsNone(upload.uploaded_by)
        # The header is sorted, so the column order does not depend on the device.
        self.assertEqual(
            self.stored_text(upload), "humidity,temperature\r\n60,21.5\r\n"
        )

    def test_values_with_commas_stay_one_column(self):
        self.get(self.open_zone, query="note=a,b")
        self.assertEqual(self.stored_text(Upload.objects.get()), 'note\r\n"a,b"\r\n')

    def test_the_example_converter_turns_the_reading_into_parquet(self):
        import polars as pl

        zone = Dropzone.objects.create(
            name="parquet-webhook",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=False,
            converter="csv_to_parquet",
        )
        self.assertEqual(self.get(zone).status_code, 201)
        upload = Upload.objects.get()
        stored = upload.files.get()
        self.assertTrue(stored.file.name.endswith("webhook.parquet"))
        frame = pl.read_parquet(self.media_root / stored.file.name)
        self.assertEqual(frame.shape, (1, 2))
        self.assertEqual(frame["temperature"][0], 21.5)

    def test_secured_dropzone_accepts_the_matching_token(self):
        response = self.get(self.secured_zone, token="s3cret-token")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Upload.objects.count(), 1)

    def test_secured_dropzone_rejects_a_wrong_or_missing_token(self):
        self.assertEqual(self.get(self.secured_zone, token="wrong").status_code, 401)
        self.assertEqual(self.get(self.secured_zone).status_code, 401)
        self.assertEqual(Upload.objects.count(), 0)

    def test_unknown_token_is_404(self):
        import uuid

        url = reverse("dropzones:webhook_upload", kwargs={"token": uuid.uuid4()})
        self.assertEqual(self.client.get(f"{url}?t=1").status_code, 404)

    def test_disabled_dropzone_is_404(self):
        self.open_zone.enabled = False
        self.open_zone.save()
        self.assertEqual(self.get(self.open_zone).status_code, 404)

    def test_browser_dropzone_is_404_on_the_webhook_endpoint(self):
        zone = Dropzone.objects.create(name="browser-only", require_login=False)
        self.assertEqual(self.get(zone).status_code, 404)

    def test_post_is_rejected(self):
        response = self.client.post(f"{self.url(self.open_zone)}?t=1")
        self.assertEqual(response.status_code, 405)

    def test_no_parameters_is_a_400(self):
        response = self.client.get(self.url(self.open_zone))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)

    def test_a_duplicate_parameter_is_a_400(self):
        response = self.get(self.open_zone, query="t=1&t=2")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Duplicate", response.json()["error"])

    def test_an_invalid_parameter_name_is_a_400(self):
        # The names become column names downstream, so anything beyond letters,
        # digits and underscores is rejected.
        response = self.get(self.open_zone, query="bad-name=1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("bad-name", response.json()["error"])

    def test_an_overlong_value_is_a_400(self):
        response = self.get(self.open_zone, query="t=" + "9" * 1001)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)

    def test_too_many_parameters_is_a_400(self):
        query = "&".join(f"p{i}=1" for i in range(101))
        response = self.get(self.open_zone, query=query)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Upload.objects.count(), 0)

    def test_checker_rejection_is_a_400_with_the_message(self):
        def angry(files):
            raise ValueError("Out of range.")

        zone = Dropzone.objects.create(
            name="strict-webhook",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=False,
            checker="test_angry",
        )
        with patch.dict(registry._checkers, {"test_angry": angry}):
            response = self.get(zone)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Out of range.")
        self.assertEqual(Upload.objects.count(), 0)

    def test_default_validity_is_until_replaced(self):
        before = timezone.now()
        self.get(self.open_zone)
        upload = Upload.objects.get()
        self.assertGreaterEqual(upload.valid_from, before)
        self.assertIsNone(upload.valid_until)

    def test_always_default_leaves_both_bounds_open(self):
        zone = Dropzone.objects.create(
            name="always-webhook",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=False,
            default_validity=Dropzone.Validity.ALWAYS,
        )
        self.get(zone)
        upload = Upload.objects.get()
        self.assertIsNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)

    def test_a_new_reading_clips_the_previous_one(self):
        # With the default validity, each reading is valid exactly while it is the
        # newest, so "the reading in effect at a timestamp" is one validity query.
        self.get(self.open_zone, query="temperature=20")
        self.get(self.open_zone, query="temperature=21")
        first, second = Upload.objects.order_by("uploaded_at")
        self.assertEqual(first.valid_until, second.valid_from)
        self.assertIsNone(second.valid_until)


class DownloadTests(TempMediaMixin, TestCase):
    """Stored files are downloaded through an authenticated view linked from the
    admin; bare storage paths are never served."""

    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(name="zone")
        cls.staff = User.objects.create_user("clerk", is_staff=True)

    def setUp(self):
        super().setUp()
        self.upload = process_upload(
            self.zone, [upload_file("report.xlsx", b"xlsx-bytes")]
        )
        self.stored = self.upload.files.get()
        self.url = reverse("dropzones:download", kwargs={"pk": self.stored.pk})

    def test_staff_user_downloads_the_stored_file(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"xlsx-bytes")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("report.xlsx", response.headers["Content-Disposition"])

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_non_staff_user_is_forbidden(self):
        # Uploaders are not automatically readers; the download stays admin-side.
        self.client.force_login(User.objects.create_user("outsider"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_unknown_file_is_404(self):
        self.client.force_login(self.staff)
        url = reverse("dropzones:download", kwargs={"pk": self.stored.pk + 999})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_missing_file_on_disk_is_404(self):
        (self.media_root / self.stored.file.name).unlink()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_admin_upload_page_links_to_the_download(self):
        self.client.force_login(User.objects.create_superuser("downloader-boss"))
        page = self.client.get(
            reverse("admin:dropzones_upload_change", args=[self.upload.pk])
        )
        # A single download link per file, styled with the same text-link class Unfold
        # uses for its own readonly links (e.g. the uploaded_by user); the full storage
        # path appears nowhere (the directory has its own field).
        self.assertContains(
            page,
            f'<a href="{self.url}" class="text-link">Click to download ⤓</a>',
            html=True,
        )
        self.assertNotContains(page, self.stored.file.name)


class SftpTests(TempMediaMixin, TestCase):
    """The database-facing pieces of the SFTP endpoint. The SSH plumbing itself
    (login, chroot, disconnect handling) is exercised by the integration suite."""

    @classmethod
    def setUpTestData(cls):
        cls.zone = Dropzone.objects.create(
            name="sftp-zone", upload_method=Dropzone.Method.SFTP, secret="pw"
        )

    def session_dir(self, files):
        directory = Path(tempfile.mkdtemp(prefix="sftp-test-session"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for name, content in files.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(content)
        return directory

    def test_authenticate_returns_the_dropzone_for_valid_credentials(self):
        self.assertEqual(sftp._authenticate("sftp-zone", "pw"), self.zone)

    def test_authenticate_rejects_wrong_or_missing_credentials(self):
        self.assertIsNone(sftp._authenticate("sftp-zone", "wrong"))
        self.assertIsNone(sftp._authenticate("sftp-zone", ""))
        self.assertIsNone(sftp._authenticate("unknown", "pw"))

    def test_authenticate_rejects_non_sftp_and_disabled_dropzones(self):
        Dropzone.objects.create(name="browser-zone", secret="pw")
        self.assertIsNone(sftp._authenticate("browser-zone", "pw"))
        self.zone.enabled = False
        self.zone.save()
        self.assertIsNone(sftp._authenticate("sftp-zone", "pw"))

    def test_store_session_groups_all_files_into_one_upload(self):
        directory = self.session_dir(
            {"b.csv": b"3,4\n", "a.csv": b"1,2\n", "sub/c.csv": b"5,6\n"}
        )
        upload = sftp._store_session(self.zone.pk, directory)
        self.assertEqual(Upload.objects.count(), 1)
        self.assertEqual(
            sorted(str(f) for f in upload.files.all()), ["a.csv", "b.csv", "c.csv"]
        )
        # The dropzone default "until replaced": concrete start, open end.
        self.assertIsNotNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)

    def test_store_session_applies_an_always_default_validity(self):
        zone = Dropzone.objects.create(
            name="always-sftp",
            upload_method=Dropzone.Method.SFTP,
            secret="pw",
            default_validity=Dropzone.Validity.ALWAYS,
        )
        upload = sftp._store_session(zone.pk, self.session_dir({"a.csv": b"1\n"}))
        self.assertIsNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)

    def test_store_session_without_files_stores_nothing(self):
        self.assertIsNone(sftp._store_session(self.zone.pk, self.session_dir({})))
        self.assertEqual(Upload.objects.count(), 0)

    def test_store_session_runs_the_checker(self):
        def angry(files):
            raise ValueError("Bad header row.")

        zone = Dropzone.objects.create(
            name="strict-sftp",
            upload_method=Dropzone.Method.SFTP,
            secret="pw",
            checker="test_angry",
        )
        directory = self.session_dir({"a.csv": b"1\n"})
        with patch.dict(registry._checkers, {"test_angry": angry}):
            with self.assertRaisesMessage(UploadError, "Bad header row."):
                sftp._store_session(zone.pk, directory)
        self.assertEqual(Upload.objects.count(), 0)

    def test_store_session_converts_csv_to_parquet(self):
        # The example converter, end to end: the parquet files are stored under the
        # source names, the uploaded CSVs themselves are not kept.
        zone = Dropzone.objects.create(
            name="parquet-sftp",
            upload_method=Dropzone.Method.SFTP,
            secret="pw",
            converter="csv_to_parquet",
        )
        directory = self.session_dir({"a.csv": b"x,y\n1,2\n", "b.csv": b"x,y\n3,4\n"})
        upload = sftp._store_session(zone.pk, directory)
        self.assertEqual(
            sorted(str(f) for f in upload.files.all()), ["a.parquet", "b.parquet"]
        )

    def test_store_session_is_all_or_nothing_on_rejection(self):
        # The example checker rejects the empty file; the valid one of the same
        # session must not be stored either.
        zone = Dropzone.objects.create(
            name="no-empties-sftp",
            upload_method=Dropzone.Method.SFTP,
            secret="pw",
            checker="reject_empty_files",
        )
        directory = self.session_dir({"empty.csv": b"", "good.csv": b"x\n1\n"})
        with self.assertRaisesMessage(UploadError, "empty"):
            sftp._store_session(zone.pk, directory)
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(UploadFile.objects.count(), 0)


class FlightTests(TempMediaMixin, TransactionTestCase):
    """The Arrow Flight endpoint, driven through a real client.

    A server is started on an ephemeral port and talked to exactly as an uploader
    would, because the parts worth testing — the token that ties several DoPuts into
    one upload, and the commit that decides whether anything is stored at all — only
    exist at that level. TransactionTestCase rather than TestCase: the server answers
    on its own threads, which do not share the test's open transaction.
    """

    def setUp(self):
        super().setUp()
        self.zone = Dropzone.objects.create(
            name="flight-zone", upload_method=Dropzone.Method.FLIGHT, secret="pw"
        )
        # A long timeout: the sweeper must not collect a session mid-test.
        self.server = flight.FlightEndpoint(
            location="grpc://127.0.0.1:0",
            auth_handler=flight.NoOpAuthHandler(),
            middleware={"auth": flight.AuthMiddlewareFactory()},
        )
        self.port = self.server.port
        thread = threading.Thread(target=self.server.serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(flight._sessions.clear)

    def connect(self, name="flight-zone", secret="pw"):
        """An authenticated client and its call options, like an uploader's first lines."""
        client = pyarrow_flight.connect(f"grpc://127.0.0.1:{self.port}")
        self.addCleanup(client.close)
        options = pyarrow_flight.FlightCallOptions(
            headers=[client.authenticate_basic_token(name.encode(), secret.encode())]
        )
        return client, options

    def send(self, client, options, name, table, dropzone="flight-zone"):
        writer, _ = client.do_put(
            pyarrow_flight.FlightDescriptor.for_path(dropzone, name),
            table.schema,
            options,
        )
        writer.write_table(table)
        writer.close()

    def commit(self, client, options):
        return next(
            client.do_action(pyarrow_flight.Action("commit", b""), options)
        ).body.to_pybytes().decode()

    @staticmethod
    def table(**columns):
        return pyarrow.table(columns or {"id": [1, 2]})

    def stored_names(self, upload):
        return sorted(str(f) for f in upload.files.all())

    def test_a_single_table_shall_be_stored_as_one_parquet_file(self):
        client, options = self.connect()
        self.send(client, options, "issues", self.table(id=[1, 2, 3]))
        self.commit(client, options)

        upload = Upload.objects.get()
        self.assertEqual(self.stored_names(upload), ["issues.parquet"])
        stored = Path(settings.MEDIA_ROOT) / upload.files.get().file.name
        self.assertEqual(pyarrow_parquet.read_table(stored).num_rows, 3)

    def test_several_tables_shall_become_one_upload_of_several_files(self):
        client, options = self.connect()
        self.send(client, options, "issues", self.table(id=[1, 2]))
        self.send(client, options, "commits", self.table(sha=["a1", "b2"]))
        self.send(client, options, "builds", self.table(n=[7]))
        self.commit(client, options)

        upload = Upload.objects.get()
        self.assertEqual(
            self.stored_names(upload),
            ["builds.parquet", "commits.parquet", "issues.parquet"],
        )

    def test_the_table_name_shall_come_from_the_descriptor_not_the_content(self):
        client, options = self.connect()
        self.send(client, options, "quarterly-figures", self.table())
        self.commit(client, options)
        self.assertEqual(
            self.stored_names(Upload.objects.get()), ["quarterly-figures.parquet"]
        )

    def test_a_disconnect_without_commit_shall_store_nothing(self):
        """The point of the explicit commit: an upload that was never committed is
        incomplete, so no row, no directory and no file may survive it."""
        client, options = self.connect()
        self.send(client, options, "issues", self.table())
        self.send(client, options, "commits", self.table())
        # The uploader vanishes: the client goes away without ever committing.
        client.close()

        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(UploadFile.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*")), [])

    def test_an_abandoned_session_shall_be_swept_without_storing_anything(self):
        client, options = self.connect()
        self.send(client, options, "issues", self.table())
        session = next(iter(flight._sessions.values()))
        directory = session.directory
        self.assertTrue(any(directory.iterdir()))

        # Everything older than the timeout is abandoned; nothing is stored for it.
        flight._sweep(timeout=-1)

        self.assertEqual(flight._sessions, {})
        self.assertFalse(directory.exists())
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*")), [])

    def test_a_second_upload_shall_get_its_own_session(self):
        first, first_options = self.connect()
        self.send(first, first_options, "issues", self.table())
        second, second_options = self.connect()
        self.send(second, second_options, "commits", self.table())
        # Each client commits only its own tables, even though both are open at once.
        self.commit(first, first_options)
        self.commit(second, second_options)

        uploads = Upload.objects.order_by("pk")
        self.assertEqual(
            [self.stored_names(u) for u in uploads],
            [["issues.parquet"], ["commits.parquet"]],
        )

    def test_a_commit_without_tables_shall_be_refused(self):
        client, options = self.connect()
        with self.assertRaises(pyarrow_flight.FlightServerError) as caught:
            self.commit(client, options)
        self.assertIn("no tables", str(caught.exception))
        self.assertEqual(Upload.objects.count(), 0)

    def test_a_truncated_table_shall_poison_the_commit(self):
        """A client killed mid-table ends its stream without an error, so the
        cancellation flag is the only thing that keeps the partial table out."""
        client, options = self.connect()
        self.send(client, options, "issues", self.table())
        session = next(iter(flight._sessions.values()))
        session.truncated = True

        with self.assertRaises(pyarrow_flight.FlightServerError) as caught:
            self.commit(client, options)
        self.assertIn("broke off", str(caught.exception))
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*")), [])

    def test_a_checker_rejection_shall_reach_the_uploader(self):
        self.zone.checker = "reject_empty_files"
        self.zone.save()
        client, options = self.connect()
        # An empty table still writes a parquet file with a header, so the rejection
        # is provoked with a checker that refuses everything instead.
        with patch.dict(registry._checkers):
            @registry.checker
            def flight_reject(files):
                raise ValueError("not the agreed columns")

            self.zone.checker = "flight_reject"
            self.zone.save()
            self.send(client, options, "issues", self.table())
            with self.assertRaises(pyarrow_flight.FlightServerError) as caught:
                self.commit(client, options)

        self.assertIn("not the agreed columns", str(caught.exception))
        self.assertEqual(Upload.objects.count(), 0)
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*")), [])

    def test_a_wrong_secret_shall_be_denied(self):
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.connect(secret="wrong")

    def test_an_unknown_or_non_flight_dropzone_shall_be_denied(self):
        Dropzone.objects.create(name="browser-zone", secret="pw")
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.connect(name="unknown")
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.connect(name="browser-zone")

    def test_a_disabled_dropzone_shall_be_denied(self):
        self.zone.enabled = False
        self.zone.save()
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.connect()

    def test_a_call_without_credentials_shall_be_denied(self):
        client = pyarrow_flight.connect(f"grpc://127.0.0.1:{self.port}")
        self.addCleanup(client.close)
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.send(client, None, "issues", self.table())

    def test_an_unknown_token_shall_be_denied(self):
        client, options = self.connect()
        forged = pyarrow_flight.FlightCallOptions(
            headers=[(b"authorization", b"Bearer not-a-real-token")]
        )
        with self.assertRaises(pyarrow_flight.FlightUnauthenticatedError):
            self.send(client, forged, "issues", self.table())

    def test_an_unknown_action_shall_be_refused(self):
        client, options = self.connect()
        self.send(client, options, "issues", self.table())
        with self.assertRaises(pyarrow_flight.FlightServerError):
            list(client.do_action(pyarrow_flight.Action("drop", b""), options))

    def test_the_example_from_the_admin_shall_perform_a_real_upload(self):
        """Execute the stored template verbatim against the running endpoint.

        This is what keeps the admin's example honest: it is run as an uploader would
        run it, with only the address pointed at the test server's ephemeral port.
        """
        self.zone.secret = "pw"
        self.zone.save()
        with override_settings(
            SERVER_NAME="127.0.0.1", FLIGHT_PORT=self.port
        ):
            example = self.zone.upload_example()
        self.assertIn(f"grpc://127.0.0.1:{self.port}", example)

        exec(compile(example, "<flight example>", "exec"), {})

        upload = Upload.objects.get()
        # The two tables the example sends, each stored as its own parquet file.
        self.assertEqual(
            sorted(str(f) for f in upload.files.all()),
            ["commits.parquet", "issues.parquet"],
        )
        stored = Path(settings.MEDIA_ROOT) / upload.files.get(
            file__endswith="issues.parquet"
        ).file.name
        self.assertEqual(
            pyarrow_parquet.read_table(stored).column("state").to_pylist(),
            ["open", "done"],
        )

    def test_the_dropzone_default_validity_shall_apply(self):
        self.zone.default_validity = Dropzone.Validity.ALWAYS
        self.zone.save()
        client, options = self.connect()
        self.send(client, options, "issues", self.table())
        self.commit(client, options)

        upload = Upload.objects.get()
        self.assertIsNone(upload.valid_from)
        self.assertIsNone(upload.valid_until)


class FlightModelTests(TestCase):
    """The Flight-specific parts of the Dropzone model."""

    def setUp(self):
        self.zone = Dropzone.objects.create(
            name="flight-zone", upload_method=Dropzone.Method.FLIGHT, secret="pw"
        )

    def test_flight_secret_must_match(self):
        self.assertTrue(self.zone.flight_secret_matches("pw"))
        self.assertFalse(self.zone.flight_secret_matches("wrong"))
        self.assertFalse(self.zone.flight_secret_matches(""))

    def test_flight_secret_fails_closed_when_unset(self):
        # Like SFTP: the dropzone name is not a secret, so no secret means no logins.
        self.zone.secret = ""
        self.assertFalse(self.zone.flight_secret_matches(""))
        self.assertFalse(self.zone.flight_secret_matches("anything"))

    @override_settings(SERVER_NAME="reports.example.com", FLIGHT_PORT=8815)
    def test_flight_address_uses_server_name_and_port(self):
        self.assertEqual(
            self.zone.flight_address(), "grpc://reports.example.com:8815"
        )

    @override_settings(SERVER_NAME="reports.example.com", FLIGHT_PORT=8815)
    def test_flight_example_is_a_complete_client_for_this_dropzone(self):
        example = self.zone.upload_example()
        self.assertIn("grpc://reports.example.com:8815", example)
        self.assertIn('authenticate_basic_token(b"flight-zone"', example)
        self.assertIn('for_path("flight-zone", table_name)', example)
        self.assertIn('Action("commit"', example)


class UploadExampleTests(TestCase):
    """The templates themselves: filled in correctly and complete.

    That an example actually works is proven by running it — see
    :class:`ExecutedExampleTests` for the HTTP methods and
    ``FlightTests.test_the_example_from_the_admin_shall_perform_a_real_upload``
    for Arrow Flight.
    """

    def zone(self, method, **kwargs):
        # A name per method, because the field is unique and these tests build one
        # dropzone per method within a single test.
        return Dropzone.objects.create(
            name=f"example-{method}", upload_method=method, **kwargs
        )

    def test_every_method_has_an_example(self):
        # A method without a template would leave its dropzone page with an empty
        # example box, so the mapping must cover the choices exhaustively.
        self.assertEqual(
            sorted(examples.TEMPLATES), sorted(m.value for m in Dropzone.Method)
        )

    def test_no_template_keeps_an_unfilled_placeholder(self):
        # The placeholder names the templates may use; anything else would raise in
        # str.format, and a leftover "{name}" here would reach the uploader as-is.
        placeholders = re.compile(
            r"\{(url|address|name|secret|port|host)[!:}]"
        )
        for method in Dropzone.Method:
            with self.subTest(method=method):
                example = self.zone(method, secret="s3cret").upload_example()
                self.assertIsNone(placeholders.search(example))

    def test_the_secret_is_filled_in_and_marked_when_missing(self):
        zone = self.zone(Dropzone.Method.SFTP, secret="s3cret")
        self.assertIn("s3cret", zone.upload_example())
        zone.secret = ""
        self.assertIn("<secret>", zone.upload_example())

    def test_each_example_names_the_endpoint_it_uploads_to(self):
        # The URL methods show the address verbatim; SFTP and Arrow Flight show the
        # host and port of theirs, in the shape their client expects.
        for method in (
            Dropzone.Method.BROWSER, Dropzone.Method.API, Dropzone.Method.WEBHOOK
        ):
            with self.subTest(method=method):
                zone = self.zone(method, secret="s3cret")
                self.assertIn(zone.upload_address(), zone.upload_example())
        flight_zone = self.zone(Dropzone.Method.FLIGHT, secret="s3cret")
        self.assertIn(flight_zone.flight_address(), flight_zone.upload_example())
        sftp_zone = self.zone(Dropzone.Method.SFTP, secret="s3cret")
        self.assertIn(
            f"{sftp_zone.name}@{settings.SERVER_NAME}", sftp_zone.upload_example()
        )

    def test_upload_address_follows_the_method(self):
        cases = {
            Dropzone.Method.BROWSER: "upload_url",
            Dropzone.Method.API: "api_upload_url",
            Dropzone.Method.WEBHOOK: "webhook_url",
            Dropzone.Method.SFTP: "sftp_address",
            Dropzone.Method.FLIGHT: "flight_address",
        }
        for method, attribute in cases.items():
            with self.subTest(method=method):
                zone = self.zone(method)
                self.assertEqual(zone.upload_address(), getattr(zone, attribute)())

    def test_the_flight_example_is_valid_python(self):
        zone = self.zone(Dropzone.Method.FLIGHT, secret="s3cret")
        compile(zone.upload_example(), "<flight example>", "exec")


class ExecutedExampleTests(TempMediaMixin, TestCase):
    """Run the stored examples against live dropzones.

    The point is that an example a user copies actually uploads something: the curl
    commands are parsed into the request they describe and replayed through the real
    view, so a template that drifts away from its endpoint fails here rather than
    wasting an uploader's afternoon.
    """

    def curl_parts(self, command):
        """The URL, headers and form fields of a ``curl`` example, as written."""
        tokens = shlex.split(command.replace("\\\n", " "))
        url, headers, fields = None, {}, []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("-H", "--header"):
                name, _, value = tokens[index + 1].partition(":")
                headers[name.strip()] = value.strip()
                index += 2
            elif token in ("-F", "--form"):
                fields.append(tokens[index + 1])
                index += 2
            elif token in ("-X", "--request"):
                index += 2
            elif token.startswith("http"):
                url = token
                index += 1
            else:
                index += 1
        return url, headers, fields

    @staticmethod
    def strip_comments(example):
        return "\n".join(
            line for line in example.splitlines() if not line.startswith("#")
        ).strip()

    @override_settings(DEBUG=True, SERVER_NAME="testserver")
    def test_the_api_example_uploads_a_file(self):
        zone = Dropzone.objects.create(
            name="api-example", upload_method=Dropzone.Method.API, secret="s3cret"
        )
        url, headers, fields = self.curl_parts(
            self.strip_comments(zone.upload_example())
        )
        self.assertEqual(url, zone.api_upload_url())
        # -F files=@yourfile.csv means "post this file under the name files".
        self.assertEqual([field.split("=@")[0] for field in fields], ["files"])
        filename = fields[0].split("=@")[1]

        response = self.client.post(
            url,
            {"files": SimpleUploadedFile(filename, b"a,b\n1,2\n")},
            headers={"authorization": headers["Authorization"]},
        )

        self.assertEqual(response.status_code, 201, response.content)
        upload = Upload.objects.get(dropzone=zone)
        self.assertEqual([str(f) for f in upload.files.all()], [filename])

    @override_settings(DEBUG=True, SERVER_NAME="testserver")
    def test_the_webhook_example_stores_its_readings(self):
        zone = Dropzone.objects.create(
            name="webhook-example",
            upload_method=Dropzone.Method.WEBHOOK,
            secret="s3cret",
        )
        url, headers, _ = self.curl_parts(self.strip_comments(zone.upload_example()))
        self.assertTrue(url.startswith(zone.webhook_url()))

        response = self.client.get(
            url, headers={"authorization": headers["Authorization"]}
        )

        self.assertEqual(response.status_code, 201, response.content)
        upload = Upload.objects.get(dropzone=zone)
        stored = (Path(settings.MEDIA_ROOT) / upload.files.get().file.name).read_text()
        # The example's own readings, as the columns of the stored one-row CSV.
        self.assertEqual(stored.splitlines()[0], "humidity,temperature")
        self.assertEqual(stored.splitlines()[1], "48,21.5")

    @override_settings(DEBUG=True, SERVER_NAME="testserver")
    def test_the_browser_example_points_at_the_working_upload_page(self):
        zone = Dropzone.objects.create(
            name="browser-example", require_login=False
        )
        example = zone.upload_example()
        self.assertIn(zone.upload_url(), example)
        # The link in the example is the page an uploader lands on.
        self.assertEqual(self.client.get(zone.upload_path()).status_code, 200)

    @override_settings(SERVER_NAME="reports.example.com", SFTP_PORT=2222)
    def test_the_sftp_example_uses_the_real_login_and_port(self):
        zone = Dropzone.objects.create(
            name="sftp-example", upload_method=Dropzone.Method.SFTP, secret="s3cret"
        )
        example = zone.upload_example()
        # The credentials the example tells the uploader to use must be the ones the
        # endpoint accepts; the transfer itself is covered by the integration suite.
        self.assertIn("sftp -P 2222 sftp-example@reports.example.com", example)
        self.assertTrue(zone.sftp_secret_matches("s3cret"))
        self.assertIn("s3cret", example)


class AdminTests(TempMediaMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("boss")
        cls.zone = Dropzone.objects.create(name="zone")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_function_dropdowns_follow_the_registry(self):
        with patch.dict(registry._checkers, {"test_zz_check": lambda f: None}):
            with patch.dict(registry._converters, {"test_zz_conv": lambda f, o: None}):
                form = DropzoneForm()
                self.assertIn(
                    ("test_zz_check", "test_zz_check"), form.fields["checker"].choices
                )
                self.assertIn(
                    ("test_zz_conv", "test_zz_conv"), form.fields["converter"].choices
                )
                # The shipped defaults appear under their human-readable labels.
                self.assertIn(
                    ("csv_to_parquet", "CSV to Parquet"),
                    form.fields["converter"].choices,
                )
        # Both fields must be optional, and an unregistered name must not validate.
        form = DropzoneForm(
            {"name": "n", "upload_method": "browser", "checker": "test_zz_check"}
        )
        self.assertIn("checker", form.errors)

    def test_uploads_cannot_be_added_by_hand(self):
        self.assertFalse(UploadAdmin(Upload, admin.site).has_add_permission(None))

    def test_upload_link_display(self):
        admin_instance = DropzoneAdmin(Dropzone, admin.site)
        self.assertEqual(
            admin_instance.upload_link(None), "Available after saving."
        )
        self.assertIn(str(self.zone.token), admin_instance.upload_link(self.zone))

    def test_upload_link_shows_the_sftp_address(self):
        zone = Dropzone.objects.create(
            name="sftp-zone", upload_method=Dropzone.Method.SFTP, secret="pw"
        )
        self.assertIn(
            zone.sftp_address(), DropzoneAdmin(Dropzone, admin.site).upload_link(zone)
        )

    def test_upload_link_shows_the_webhook_address(self):
        zone = Dropzone.objects.create(
            name="webhook-zone",
            upload_method=Dropzone.Method.WEBHOOK,
            require_login=False,
        )
        # The link field carries the address alone; how to call it is the example.
        self.assertIn(
            zone.webhook_url(), DropzoneAdmin(Dropzone, admin.site).upload_link(zone)
        )

    def test_example_field_shows_the_example_of_the_chosen_method(self):
        admin_instance = DropzoneAdmin(Dropzone, admin.site)
        self.assertEqual(admin_instance.example(None), "Available after saving.")
        for method, expected in (
            (Dropzone.Method.API, "curl"),
            (Dropzone.Method.WEBHOOK, "temperature=21.5"),
            (Dropzone.Method.SFTP, "sftp -P"),
            (Dropzone.Method.FLIGHT, "do_put"),
            (Dropzone.Method.BROWSER, "browser"),
        ):
            with self.subTest(method=method):
                zone = Dropzone.objects.create(
                    name=f"example-field-{method}",
                    upload_method=method,
                    secret="pw",
                )
                self.assertIn(expected, admin_instance.example(zone))

    def dropzone_form(self, **overrides):
        data = {
            "name": "new-zone",
            "description": "",
            "upload_method": Dropzone.Method.SFTP,
            "file_format": "",
            "checker": "",
            "converter": "",
            "default_validity": Dropzone.Validity.UNTIL_REPLACED,
            "secret": "pw",
            "require_login": "on",
            "enabled": "on",
        }
        data.update(overrides)
        return DropzoneForm(data)

    def test_an_sftp_dropzone_requires_a_secret(self):
        # Without a secret the endpoint would accept no logins; the form catches the
        # misconfiguration when the dropzone is created rather than at upload time.
        complete = self.dropzone_form()
        self.assertTrue(complete.is_valid(), complete.errors)
        self.assertIn("secret", self.dropzone_form(secret="").errors)

    def test_a_period_default_validity_needs_the_browser_upload(self):
        form = self.dropzone_form(default_validity=Dropzone.Validity.PERIOD)
        self.assertIn("default_validity", form.errors)
        browser = self.dropzone_form(
            upload_method=Dropzone.Method.BROWSER,
            default_validity=Dropzone.Validity.PERIOD,
            secret="",
        )
        self.assertTrue(browser.is_valid(), browser.errors)

    def test_upload_changelist_offers_a_delete_button_per_row(self):
        process_upload(self.zone, [upload_file()])
        upload = Upload.objects.get()
        page = self.client.get(reverse("admin:dropzones_upload_changelist"))
        self.assertContains(
            page, reverse("admin:dropzones_upload_delete", args=[upload.pk])
        )

    def test_admin_pages_render(self):
        process_upload(self.zone, [upload_file()])
        upload = Upload.objects.get()
        for url in (
            reverse("admin:dropzones_dropzone_changelist"),
            reverse("admin:dropzones_dropzone_add"),
            reverse("admin:dropzones_dropzone_change", args=[self.zone.pk]),
            reverse("admin:dropzones_upload_changelist"),
            reverse("admin:dropzones_upload_change", args=[upload.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
