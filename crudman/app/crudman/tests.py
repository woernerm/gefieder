from unittest.mock import patch

from django.test import TestCase
from django.urls import Resolver404, reverse

from .settings import _site_url


class ReturnToSiteLinkTests(TestCase):
    """Unfold's "Return to site" link points at UNFOLD["SITE_URL"]. We serve no site
    root, so the link is hidden by returning None, but it must re-enable automatically
    once a root URL exists (i.e. once "/" resolves)."""

    def test_site_url_is_none_without_a_root_route(self):
        # The project has no "/" route, so the link target is None (link hidden).
        with patch("crudman.settings.resolve", side_effect=Resolver404):
            self.assertIsNone(_site_url(request=None))

    def test_site_url_is_root_when_it_resolves(self):
        # Simulate a project that later adds a homepage: "/" now resolves, so the link
        # comes back on its own without any change to this code.
        with patch("crudman.settings.resolve", return_value=object()):
            self.assertEqual(_site_url(request=None), "/")

    def test_login_page_omits_the_link_when_no_root_route(self):
        # End to end against the real URLconf: with no "/" route the rendered login
        # page must not offer the (broken) link.
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Return to site")
