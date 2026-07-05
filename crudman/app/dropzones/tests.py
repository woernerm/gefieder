import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import registry, services
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
    def test_checker_decorator_registers(self):
        with patch.dict(registry._checkers):
            @registry.checker("test_check")
            def check(files):
                pass

            self.assertIs(registry.get_checker("test_check"), check)
            self.assertIn("test_check", registry.checker_names())

    def test_converter_decorator_registers(self):
        with patch.dict(registry._converters):
            @registry.converter("test_convert")
            def convert(files, out_dir):
                return files

            self.assertIs(registry.get_converter("test_convert"), convert)
            self.assertIn("test_convert", registry.converter_names())

    def test_duplicate_name_raises(self):
        # Two different functions under one name would make a dropzone ambiguous.
        with patch.dict(registry._checkers):
            @registry.checker("test_dup")
            def first(files):
                pass

            with self.assertRaises(ImproperlyConfigured):
                @registry.checker("test_dup")
                def second(files):
                    pass

    def test_reregistering_same_function_is_tolerated(self):
        # A module imported twice re-runs its decorators; that must not fail.
        with patch.dict(registry._checkers):
            def check(files):
                pass

            registry.checker("test_again")(check)
            registry.checker("test_again")(check)
            self.assertIs(registry.get_checker("test_again"), check)

    def test_unknown_names_raise_lookup_error(self):
        with self.assertRaises(LookupError):
            registry.get_checker("no_such_checker")
        with self.assertRaises(LookupError):
            registry.get_converter("no_such_converter")

    def test_autodiscover_registered_the_example_functions(self):
        # DropzonesConfig.ready ran autodiscover at startup, importing the modules in
        # the functions folder; the examples must therefore be selectable.
        self.assertIn("reject_empty_files", registry.checker_names())
        self.assertIn("csv_to_parquet", registry.converter_names())


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
            output = out_dir / "combined.parquet"
            output.write_bytes(b"parquet")
            return [output]

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

    def test_converter_returning_nothing_is_rejected(self):
        zone = Dropzone.objects.create(name="converted", converter="test_empty")
        with patch.dict(registry._converters, {"test_empty": lambda f, o: []}):
            with self.assertRaises(UploadError):
                process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)

    def test_converter_returning_missing_files_is_rejected(self):
        def liar(files, out_dir):
            return [out_dir / "never-written.parquet"]

        zone = Dropzone.objects.create(name="converted", converter="test_liar")
        with patch.dict(registry._converters, {"test_liar": liar}):
            with self.assertRaises(UploadError):
                process_upload(zone, [upload_file()])
        self.assertEqual(Upload.objects.count(), 0)

    def test_unknown_converter_is_rejected(self):
        zone = Dropzone.objects.create(name="stale", converter="test_gone")
        with self.assertRaises(UploadError):
            process_upload(zone, [upload_file()])

    def test_example_csv_to_parquet_converter_works_end_to_end(self):
        import polars as pl

        zone = Dropzone.objects.create(name="polars", converter="csv_to_parquet")
        upload = process_upload(zone, [upload_file("numbers.csv", b"a,b\n1,2\n")])
        stored = upload.files.get()
        self.assertTrue(stored.file.name.endswith("numbers.parquet"))
        frame = pl.read_parquet(self.media_root / stored.file.name)
        self.assertEqual(frame.shape, (1, 2))

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
            with patch.dict(registry._converters, {"test_zz_conv": lambda f, o: f}):
                form = DropzoneForm()
                self.assertIn(
                    ("test_zz_check", "test_zz_check"), form.fields["checker"].choices
                )
                self.assertIn(
                    ("test_zz_conv", "test_zz_conv"), form.fields["converter"].choices
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
