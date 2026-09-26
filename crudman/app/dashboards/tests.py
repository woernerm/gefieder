"""Who reaches the dashboards, and what crudman makes of the service's answers.

The service itself is stubbed: what is under test here is the page around its answers,
the address the reader's filters and version travel in, and the access rule. The drawing is
covered against the live stack in tests/test_dashboards.py.
"""
import json
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from sso.roles import GROUP_FOR_RANK
from system.models import Approval, Deployment

from . import service

LISTING = {"dashboards": [{"name": "issues", "title": "Issues", "description": "Open ones."}],
           "problems": {"broken": "boards/broken.py defines no dashboard"}}

LAYOUT = {
    "title": "Issues", "description": "Open ones.", "refresh": None,
    "filters": [{"name": "project", "label": "Project", "everything": True,
                 "choices": ["project_a", "project_b"], "picked": ["project_b"]}],
    "panels": [{"index": 0, "title": "Per project", "wide": False, "filters": ["project"], "kind": "echarts"}],
}


class Service:
    """The service's answers, and the addresses crudman asked it for."""

    def __init__(self):
        self.asked = []

    def __call__(self, url, timeout):
        self.asked.append(url)
        path = url.split("/dashboards/", 1)[1].split("?")[0]
        body = {"": LISTING, "issues/": LAYOUT, "issues/0/": {"kind": "stat", "value": 3}}.get(path)

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"xlsx bytes" if path.endswith(".xlsx") else json.dumps(body).encode()

        return Response()


class DashboardPagesTest(TestCase):
    def setUp(self):
        self.service = Service()
        patcher = patch.object(service, "urlopen", self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _viewer(self, name="viewer"):
        user = User.objects.create_user(name, password="x")
        user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK["viewer"])[0])
        self.client.force_login(user)
        return user

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get(reverse("dashboards:index"))
        self.assertEqual(response.status_code, 302)

    def test_someone_without_a_rank_is_refused(self):
        self.client.force_login(User.objects.create_user("nobody", password="x"))
        self.assertEqual(self.client.get(reverse("dashboards:index")).status_code, 403)

    def test_the_list_names_every_dashboard_and_what_could_not_be_shown(self):
        self._viewer()
        response = self.client.get(reverse("dashboards:index"))
        self.assertContains(response, reverse("dashboards:dashboard", args=["issues"]))
        self.assertContains(response, "boards/broken.py defines no dashboard")

    def test_a_dashboard_offers_its_filters_with_the_picked_value_selected(self):
        self._viewer()
        response = self.client.get(reverse("dashboards:dashboard", args=["issues"]) + "?project=project_b")
        self.assertContains(response, '<option value="">All</option>', html=True)
        self.assertContains(response, "<option selected>project_b</option>", html=True)
        # The panel asks again when the one filter its query reads changes.
        self.assertContains(response, "change from:[name='project']")

    def test_the_filters_and_the_version_reach_the_service(self):
        self._viewer()
        self.client.get(reverse("dashboards:panel", args=["issues", 0]) + "?project=project_b")
        self.assertIn("project=project_b", self.service.asked[-1])
        self.assertIn("environment=prod", self.service.asked[-1])

    def test_someone_owing_a_decision_reads_the_version_under_review(self):
        user = self._viewer()
        deployment = Deployment.objects.create(sha="a" * 40, main_sha="a" * 40,
                                               environment=Deployment.PREVIEW)
        Approval.objects.create(deployment=deployment, user=user)
        self.client.get(reverse("dashboards:panel", args=["issues", 0]))
        self.assertIn("environment=preview", self.service.asked[-1])

    def test_a_panel_is_its_payload_for_the_page_to_draw(self):
        self._viewer()
        response = self.client.get(reverse("dashboards:panel", args=["issues", 0]))
        self.assertContains(response, '{"kind": "stat", "value": 3}')

    def test_a_download_is_a_workbook(self):
        self._viewer()
        response = self.client.get(reverse("dashboards:download", args=["issues", 0]))
        self.assertEqual(response.content, b"xlsx bytes")
        self.assertIn("issues-1.xlsx", response["Content-Disposition"])

    def test_a_service_that_does_not_answer_is_said_so(self):
        self._viewer()
        with patch.object(service, "urlopen", side_effect=service.URLError("refused")):
            response = self.client.get(reverse("dashboards:index"))
        self.assertContains(response, "not available right now")
