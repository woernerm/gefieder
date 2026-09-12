"""Unit tests for the shell: which page requests it answers, and what it draws for whom."""
import re
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from notebooks.tests import enrolled
from notebooks.tests import make_user as _make_user
from sso.roles import GROUP_FOR_RANK

from .models import ONLINE, RECENT, Presence, lately, seen
from .stages import stage_of

# What the proxy adds to a request a browser made for its address bar.
PAGE = {"HTTP_SEC_FETCH_DEST": "document", "HTTP_X_FORWARDED_FOR": "203.0.113.7"}
FRAMED = {**PAGE, "HTTP_SEC_FETCH_DEST": "iframe"}


def make_user(username, rank=None, **flags):
    """A staff user of one rank: every rank is staff, or the admin would turn them away."""
    return _make_user(username, rank and GROUP_FOR_RANK[rank], is_staff=True, **flags)


class PageRequestTests(TestCase):
    """A page asked for by address gets the shell; everything else gets the app."""

    def setUp(self):
        cache.clear()
        self.client.force_login(make_user("editor", "editor"))
        self.url = reverse("admin:dropzones_dropzone_changelist")

    def test_a_page_of_its_own_is_the_shell_around_that_address(self):
        response = self.client.get(self.url + "?q=x", **PAGE)

        self.assertContains(response, f'<iframe src="{self.url}?q=x"')

    def test_the_frames_request_reaches_the_app(self):
        response = self.client.get(self.url, **FRAMED)

        self.assertNotContains(response, 'id="frame"')
        self.assertContains(response, "Dropzones")

    def test_a_request_not_through_the_proxy_is_answered_as_before(self):
        """The proxy's identity check for Grafana copies the browser's headers; its
        answer has to stay the identity, not a page."""
        response = self.client.get(self.url, HTTP_SEC_FETCH_DEST="document")

        self.assertNotContains(response, 'id="frame"')

    def test_a_related_object_popup_is_not_framed(self):
        response = self.client.get(self.url + "?_popup=1", **PAGE)

        self.assertNotContains(response, 'id="frame"')

    def test_a_form_post_is_not_a_page(self):
        response = self.client.post(reverse("admin:logout"), **PAGE)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="frame"')

    def test_another_services_address_leads_a_visitor_to_the_sign_in(self):
        """The admin panel's pages sign a visitor in themselves; a notebook address has
        no route here to do it."""
        self.client.logout()

        response = self.client.get(f"/{settings.NOTEBOOK_PATH}/lab", **PAGE)

        self.assertRedirects(
            response, f"{reverse('login')}?next=/{settings.NOTEBOOK_PATH}/lab",
            fetch_redirect_response=False,
        )

    def test_the_admin_panels_pages_still_sign_a_visitor_in_themselves(self):
        self.client.logout()

        response = self.client.get(self.url, **PAGE)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_the_sign_in_hands_itself_to_the_window(self):
        """A session that ran out while the bar was up: the provider refuses to be
        framed, and the bar has to be drawn afresh for the new session."""
        response = self.client.get(reverse("login"), **FRAMED)

        self.assertContains(response, "top.location.replace")


class StageTests(TestCase):
    """The bar offers a stage only to someone who may do something in it."""

    def setUp(self):
        cache.clear()
        # Signing a superuser in reconciles their database role, which the unit test
        # database has no function for.
        self.enterContext(patch("dbusers.utils.sync"))

    def landing(self, user):
        """Where each stage the bar offers sends this person, by label."""
        self.client.force_login(user)
        page = self.client.get(reverse("admin:index"), **PAGE).content.decode()
        links = re.findall(r'href="([^"]*)" data-prefixes="[^"]*"[^>]*>\s*<span[^>]*>\w+</span>\s*(\w+)', page)
        return {label: url for url, label in links}

    def stages(self, user):
        return list(self.landing(user))

    def test_a_viewer_gets_the_dashboards_alone(self):
        self.assertEqual(self.stages(make_user("viewer", "viewer")), ["Dashboards"])

    def test_an_editor_with_a_database_account_gets_every_stage(self):
        user = make_user("editor", "editor")
        enrolled(user)

        self.assertEqual(self.stages(user), ["Dashboards", "Load", "Model", "System"])

    def test_the_notebooks_need_the_database_account(self):
        self.assertEqual(self.stages(make_user("editor", "editor")), ["Dashboards", "Load", "System"])

    def test_a_superuser_gets_every_stage_without_a_rank(self):
        user = make_user("root", is_superuser=True)
        enrolled(user)

        self.assertEqual(self.stages(user), ["Dashboards", "Load", "Model", "System"])

    def test_a_right_granted_by_hand_opens_the_stage(self):
        user = make_user("viewer", "viewer")
        user.user_permissions.add(Permission.objects.get(codename="add_upload"))

        self.assertEqual(self.stages(user), ["Dashboards", "Load"])

    def test_the_dashboards_open_in_kiosk_mode(self):
        self.assertEqual(self.landing(make_user("viewer", "viewer"))["Dashboards"], f"/{settings.GRAFANA_PATH}/?kiosk")

    def test_the_stages_are_served_afresh_for_the_bar(self):
        """What the bar fetches after every page load, so a right granted meanwhile shows."""
        user = make_user("editor", "editor")
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("stages")), "Model")

        enrolled(user)

        self.assertContains(self.client.get(reverse("stages")), "Model")

    def test_a_stage_lands_on_the_first_list_the_person_may_open(self):
        """An editor holds no right on users; the system stage is still theirs, for the
        model versions."""
        self.assertEqual(
            self.landing(make_user("editor", "editor"))["System"],
            reverse("admin:system_deployment_changelist"),
        )
        self.assertEqual(
            self.landing(make_user("root", is_superuser=True))["System"],
            reverse("admin:auth_user_changelist"),
        )


class SidebarTests(TestCase):
    """The admin's sidebar shows the stage the page belongs to."""

    def setUp(self):
        cache.clear()
        self.enterContext(patch("dbusers.utils.sync"))
        self.client.force_login(make_user("root", is_superuser=True))

    def test_a_load_page_lists_the_load_apps_alone(self):
        page = self.client.get(reverse("admin:dropzones_dropzone_changelist")).content.decode()

        self.assertIn("Dropzones", page)
        self.assertNotIn(reverse("admin:auth_user_changelist"), page)

    def test_a_system_page_lists_the_system_apps_alone(self):
        page = self.client.get(reverse("admin:auth_user_changelist")).content.decode()

        self.assertIn(reverse("admin:auth_group_changelist"), page)
        self.assertNotIn(reverse("admin:dropzones_dropzone_changelist"), page)

    def test_the_index_lists_everything(self):
        page = self.client.get(reverse("admin:index")).content.decode()

        self.assertIn(reverse("admin:auth_user_changelist"), page)
        self.assertIn(reverse("admin:dropzones_dropzone_changelist"), page)

    def test_a_page_outside_every_stage_has_none(self):
        self.assertIsNone(stage_of(reverse("docs:index")))


class PresenceTests(TestCase):
    """Who is online is read off the last request each person made."""

    def setUp(self):
        cache.clear()
        self.user = make_user("editor", "editor")

    def test_a_request_records_the_person(self):
        self.client.force_login(self.user)
        self.client.get(reverse("admin:index"))

        self.assertTrue(Presence.objects.filter(user=self.user).exists())

    def test_a_second_request_within_the_minute_is_not_written(self):
        seen(self.user)
        Presence.objects.filter(user=self.user).update(last_seen=timezone.now() - RECENT)

        seen(self.user)

        self.assertLess(Presence.objects.get(user=self.user).last_seen, timezone.now() - ONLINE)

    def test_online_and_recent_are_told_apart(self):
        now = timezone.now()
        Presence.objects.create(user=self.user, last_seen=now)
        Presence.objects.create(user=make_user("yesterday"), last_seen=now - timedelta(days=1))
        Presence.objects.create(user=make_user("long_ago"), last_seen=now - RECENT - timedelta(days=1))

        rows = lately()

        self.assertEqual([row.user.username for row in rows["online"]], ["editor"])
        self.assertEqual([row.user.username for row in rows["recent"]], ["yesterday"])

    def test_the_bar_counts_and_the_list_names(self):
        Presence.objects.create(user=make_user("jon", first_name="Jón", last_name="Jónsson"), last_seen=timezone.now())
        self.client.force_login(self.user)

        bar = self.client.get(reverse("admin:index"), **PAGE)
        people = self.client.get(reverse("presence"))

        self.assertContains(bar, 'id="online">2<')
        self.assertContains(people, "Jón Jónsson")
        self.assertContains(people, 'id="online" hx-swap-oob="true">2<')
