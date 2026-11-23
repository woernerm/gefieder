from django.test import TestCase, override_settings
from django.urls import reverse


class ReturnToSiteLinkTests(TestCase):
    """Unfold's "Return to site" link stays off: the bar's home link is that."""

    @override_settings(OIDC_ENABLED=False)
    def test_login_page_omits_the_link(self):
        # Single sign-on is pinned off because it replaces this page with a redirect to
        # the identity provider.
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Return to site")
