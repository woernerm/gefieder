from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import TenantChangeForm, TenantCreationForm
from .models import Tenant
from . import utils


class TenantModelTests(TestCase):
    def test_limits_default_to_unlimited_sentinels(self):
        """Limit fields default to the values that stand for "no limit" (infinite)."""
        tenant = Tenant(name="acme")
        self.assertEqual(tenant.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(tenant.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.temp_file_limit, Tenant.UNLIMITED_SIZE)

    def test_str_is_name(self):
        self.assertEqual(str(Tenant(name="acme")), "acme")


class CreateTenantUtilTests(TestCase):
    @patch("tenants.utils.connection")
    def test_calls_database_function_with_same_parameters(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        result = utils.create_tenant("acme", "supersecret")

        self.assertTrue(result)
        cursor.execute.assert_called_once_with(
            "SELECT create_tenant(%s, %s)", ["acme", "supersecret"]
        )

    @patch("tenants.utils.connection")
    def test_returns_false_on_failure(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = Exception("boom")

        self.assertFalse(utils.create_tenant("acme", "supersecret"))


class SetTenantLimitsUtilTests(TestCase):
    @patch("tenants.utils.connection")
    def test_none_limits_map_to_unlimited_sentinels(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        utils.set_tenant_limits("acme")

        # Unlimited connections is -1; unlimited memory/time is "0" in PostgreSQL.
        cursor.execute.assert_called_once_with(
            "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
            ["acme", -1, "0", "0", "0"],
        )

    @patch("tenants.utils.connection")
    def test_explicit_limits_are_forwarded(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        utils.set_tenant_limits("acme", 5, "5min", "256MB", "1GB")

        cursor.execute.assert_called_once_with(
            "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
            ["acme", 5, "5min", "256MB", "1GB"],
        )


class DeleteTenantUtilTests(TestCase):
    @patch("tenants.utils.connection")
    def test_calls_database_function(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        self.assertTrue(utils.delete_tenant("acme"))
        cursor.execute.assert_called_once_with("SELECT delete_tenant(%s)", ["acme"])


class GetTenantsUtilTests(TestCase):
    @patch("tenants.utils.connection")
    def test_builds_tenant_instances_from_rows(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            ("acme", 5, "5min", "256MB", "1GB"),
            ("globex", -1, "0", "0", "0"),
        ]

        tenants = utils.get_tenants()

        self.assertEqual([t.name for t in tenants], ["acme", "globex"])

        acme = tenants[0]
        self.assertEqual(acme.connection_limit, 5)
        self.assertEqual(acme.statement_timeout, "5min")
        self.assertEqual(acme.work_mem, "256MB")
        self.assertEqual(acme.temp_file_limit, "1GB")

        # PostgreSQL's unlimited sentinels (-1 and "0") are kept as-is, the same
        # representation the add form and set_tenant_limits use for "no limit".
        globex = tenants[1]
        self.assertEqual(globex.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(globex.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(globex.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(globex.temp_file_limit, Tenant.UNLIMITED_SIZE)

    @patch("tenants.utils.connection")
    def test_unset_catalog_values_become_unlimited_sentinels(self, connection):
        # A role with no per-role settings (NULLs) still reads as "no limit".
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("acme", None, None, None, None)]

        tenant = utils.get_tenants()[0]
        self.assertIsInstance(tenant, Tenant)
        self.assertEqual(tenant.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(tenant.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.temp_file_limit, Tenant.UNLIMITED_SIZE)


class SyncTenantsUtilTests(TestCase):
    """sync_tenants mirrors the live PostgreSQL tenants into the cache table."""

    @patch("tenants.utils.get_tenants")
    def test_inserts_updates_and_removes_rows(self, get_tenants):
        # A stale row that no longer exists in PostgreSQL.
        Tenant.objects.create(name="old", connection_limit=1)

        get_tenants.return_value = [
            Tenant(name="acme", connection_limit=5, statement_timeout="5min"),
        ]
        utils.sync_tenants()

        self.assertEqual(list(Tenant.objects.values_list("name", flat=True)), ["acme"])
        acme = Tenant.objects.get(name="acme")
        self.assertEqual(acme.connection_limit, 5)
        self.assertEqual(acme.statement_timeout, "5min")


class TenantFormTests(TestCase):
    def test_creation_form_requires_password(self):
        form = TenantCreationForm(data={"name": "acme"})
        self.assertIn("password", form.errors)

    def test_creation_form_is_valid_with_password(self):
        form = TenantCreationForm(data={"name": "acme", "password": "supersecret"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_change_form_name_is_disabled(self):
        form = TenantChangeForm(instance=Tenant(name="acme"))
        self.assertTrue(form.fields["name"].disabled)


class TenantAdminTests(TestCase):
    """The admin drives PostgreSQL via the database functions; the cache row is only
    written once the database side has succeeded."""

    def setUp(self):
        from django.contrib import admin

        self.admin = admin.site._registry[Tenant]
        self.request = MagicMock()

    @patch("tenants.admin.set_tenant_limits", return_value=True)
    @patch("tenants.admin.create_tenant", return_value=True)
    def test_save_model_creates_tenant_and_caches_row(self, create, set_limits):
        obj = Tenant(name="acme", connection_limit=5)
        form = MagicMock()
        form.cleaned_data = {"password": "supersecret"}

        self.admin.save_model(self.request, obj, form, change=False)

        create.assert_called_once_with("acme", "supersecret")
        # The unset size limits keep their unlimited-sentinel defaults.
        set_limits.assert_called_once_with("acme", 5, "0", "0", "0")
        # The cache row is written only after the database functions succeed.
        self.assertTrue(Tenant.objects.filter(name="acme").exists())

    @patch("tenants.admin.set_tenant_limits", return_value=True)
    @patch("tenants.admin.create_tenant", return_value=False)
    def test_save_model_does_not_cache_when_create_fails(self, create, set_limits):
        obj = Tenant(name="acme")
        form = MagicMock()
        form.cleaned_data = {"password": "supersecret"}

        self.admin.save_model(self.request, obj, form, change=False)

        set_limits.assert_not_called()
        self.assertFalse(Tenant.objects.filter(name="acme").exists())

    @patch("tenants.admin.set_tenant_limits", return_value=True)
    @patch("tenants.admin.create_tenant", return_value=True)
    def test_save_model_on_edit_only_sets_limits(self, create, set_limits):
        obj = Tenant.objects.create(name="acme", connection_limit=5)
        obj.connection_limit = 10
        form = MagicMock()
        form.cleaned_data = {}

        self.admin.save_model(self.request, obj, form, change=True)

        create.assert_not_called()
        set_limits.assert_called_once_with("acme", 10, "0", "0", "0")
        self.assertEqual(Tenant.objects.get(name="acme").connection_limit, 10)

    @patch("tenants.admin.delete_tenant", return_value=True)
    def test_delete_model_calls_delete_tenant_and_removes_row(self, delete):
        obj = Tenant.objects.create(name="acme")
        self.admin.delete_model(self.request, obj)
        delete.assert_called_once_with("acme")
        self.assertFalse(Tenant.objects.filter(name="acme").exists())

    @patch("tenants.admin.delete_tenant", return_value=False)
    def test_delete_model_keeps_row_when_delete_fails(self, delete):
        obj = Tenant.objects.create(name="acme")
        self.admin.delete_model(self.request, obj)
        self.assertTrue(Tenant.objects.filter(name="acme").exists())


class TenantAdminViewTests(TestCase):
    """End-to-end checks that the admin pages render and that the database functions,
    not the ORM, drive create, edit and delete."""

    def setUp(self):
        admin_user = User.objects.create_superuser("admin", "a@example.com", "password")
        self.client.force_login(admin_user)

    @patch("tenants.admin.sync_tenants")
    def test_changelist_syncs_and_lists_tenants(self, sync):
        # sync_tenants would normally populate the table from PostgreSQL; seed it directly.
        Tenant.objects.create(name="acme")
        response = self.client.get(reverse("admin:tenants_tenant_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "acme")
        sync.assert_called_once()

    @patch("tenants.admin.set_tenant_limits", return_value=True)
    @patch("tenants.admin.create_tenant", return_value=True)
    def test_add_view_calls_create_tenant(self, create, set_limits):
        response = self.client.post(
            reverse("admin:tenants_tenant_add"),
            {
                "name": "acme",
                "password": "supersecret",
                "connection_limit": "",
                "statement_timeout": "",
                "work_mem": "",
                "temp_file_limit": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        create.assert_called_once_with("acme", "supersecret")
        self.assertTrue(Tenant.objects.filter(name="acme").exists())

    @patch("tenants.admin.delete_tenant", return_value=True)
    @patch("tenants.admin.sync_tenants")
    def test_delete_view_calls_delete_tenant(self, sync, delete):
        Tenant.objects.create(name="acme")
        response = self.client.post(
            reverse("admin:tenants_tenant_delete", args=["acme"]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        delete.assert_called_once_with("acme")
        self.assertFalse(Tenant.objects.filter(name="acme").exists())
