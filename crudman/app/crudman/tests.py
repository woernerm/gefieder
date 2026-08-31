from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import Resolver404, reverse

from .settings import _site_url


class ReturnToSiteLinkTests(TestCase):
    """Unfold's "Return to site" link follows whether a site root exists."""

    def test_site_url_is_none_without_a_root_route(self):
        with patch("crudman.settings.resolve", side_effect=Resolver404):
            self.assertIsNone(_site_url(request=None))

    def test_site_url_is_root_when_it_resolves(self):
        # A project that later adds a homepage: the link comes back on its own.
        with patch("crudman.settings.resolve", return_value=object()):
            self.assertEqual(_site_url(request=None), "/")

    @override_settings(OIDC_ENABLED=False)
    def test_login_page_omits_the_link_when_no_root_route(self):
        # End to end against the real URLconf. Single sign-on is pinned off because it
        # replaces this page with a redirect to the identity provider.
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Return to site")
