"""HTTP routing through the nginx proxy: both apps are reachable and serve assets."""
import re

import pytest

from conftest import (
    CRUDMAN_LOGIN, CRUDMAN_PATH, GRAFANA_LOGIN, GRAFANA_PATH,
    HTTP_BASE_URL, VERIFY_TLS,
)

import httpx


class TestRouting:
    """The proxy routes requests to the right application."""

    @pytest.mark.parametrize("path", [CRUDMAN_LOGIN, GRAFANA_LOGIN])
    def test_all_apps_shall_be_reachable_through_the_proxy(self, http_follow, path):
        assert http_follow.get(path).status_code == 200

    def test_root_shall_redirect_to_the_admin_panel(self, http):
        resp = http.get("/")
        assert resp.status_code in (301, 302)
        # The Location has to be the bare path: nginx builds an absolute URL from the
        # port it listens on inside the pod, not the one the request came in on.
        assert resp.headers["location"] == f"/{CRUDMAN_PATH}/", (
            "the redirect is absolute; it drops the port the client is talking to"
        )


class TestStaticFiles:
    """Both applications serve their static assets through the proxy."""

    def test_crudman_static_files_shall_be_served(self, http_follow):
        # A real hashed asset the login page references.
        html = http_follow.get(CRUDMAN_LOGIN).text
        match = re.search(rf"/{CRUDMAN_PATH}/static/[^\"']+\.css", html)
        assert match, "no crudman static asset referenced on the login page"
        resp = http_follow.get(match.group(0))
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_grafana_static_files_shall_be_served(self, http_follow):
        # Grafana references assets relative to its <base href="/GRAFANA_PATH/">, and the
        # bundle names change between releases, so match any of them: what is under test
        # is the proxy serving the asset.
        html = http_follow.get(GRAFANA_LOGIN).text
        match = re.search(r"public/build/grafana\.[^\"']+\.css", html)
        assert match, "no grafana static asset referenced on the login page"
        resp = http_follow.get(f"/{GRAFANA_PATH}/" + match.group(0))
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]


class TestTransportSecurity:
    """Production serves over HTTPS only."""

    @pytest.mark.production
    def test_plain_http_shall_redirect_to_https(self):
        with httpx.Client(verify=VERIFY_TLS, follow_redirects=False, timeout=10) as client:
            resp = client.get(f"{HTTP_BASE_URL}/{CRUDMAN_PATH}/")
        assert resp.status_code == 301
        assert resp.headers["location"].startswith("https://")
