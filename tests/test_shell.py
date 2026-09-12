"""The bar below every page, and the frame it puts the apps in.

What a browser puts in its address bar is crudman's to answer whichever app the path
names: the proxy tells that request (Sec-Fetch-Dest: document) from the one the shell's
frame then makes for the same path (iframe), which reaches the app. So every address
lands inside the bar, and each app has to allow being framed from its own origin.
"""
import time

import pytest

from conftest import (
    CRUDMAN_LOGIN, CRUDMAN_PATH, GRAFANA_PATH, NOTEBOOK_PATH, SUPERUSER_NAME, django,
)

PAGE = {"Sec-Fetch-Dest": "document"}
FRAMED = {"Sec-Fetch-Dest": "iframe"}

SHELL_MARK = 'id="frame"'


@pytest.fixture(scope="module")
def notebook_server(admin_session):
    """The superuser's own notebook server, started by the hub, and its address.

    A database account is what the hub spawns on, so one is provisioned for the module
    and removed after it.
    """
    account = (
        "from django.contrib.auth.models import User; from dbusers.utils import enroll, remove; "
        f"{{}}(User.objects.get(username='{SUPERUSER_NAME}'))"
    )
    django(account.format("enroll"))
    # A visit to a server that is not running only offers to start it; this is the
    # address that does. The hub answers with its "starting" page until the server is up.
    admin_session.get(f"/{NOTEBOOK_PATH}/hub/spawn/{SUPERUSER_NAME}")
    path = f"/{NOTEBOOK_PATH}/user/{SUPERUSER_NAME}/lab"
    deadline = time.time() + 120
    while time.time() < deadline:
        resp = admin_session.get(path, headers=FRAMED)
        if resp.status_code == 200 and f"/user/{SUPERUSER_NAME}/" in str(resp.url):
            break
        time.sleep(3)
    else:
        raise AssertionError(f"the notebook server never answered on {path}")
    yield path
    django(account.format("remove"))


class TestAPageOfItsOwn:
    @pytest.mark.parametrize("path", [
        f"/{CRUDMAN_PATH}/",
        f"/{GRAFANA_PATH}/?kiosk",
        f"/{NOTEBOOK_PATH}/",
    ])
    def test_shall_be_the_shell_around_that_address(self, admin_session, path):
        resp = admin_session.get(path, headers=PAGE)

        assert resp.status_code == 200
        assert f'name="frame" src="{path}"' in resp.text

    def test_shall_send_an_anonymous_visitor_to_the_sign_in(self, http):
        """A notebook address in particular: the hub would do it for its frame, but the
        page of its own is crudman's, which has no route there."""
        resp = http.get(f"/{NOTEBOOK_PATH}/", headers=PAGE)

        assert resp.status_code == 302
        assert resp.headers["location"] == f"{CRUDMAN_LOGIN}?next=/{NOTEBOOK_PATH}/"

    def test_shall_offer_the_dashboards_and_the_user_menu(self, admin_session):
        page = admin_session.get(f"/{CRUDMAN_PATH}/", headers=PAGE).text

        assert f'href="/{GRAFANA_PATH}/?kiosk"' in page
        assert "Log out" in page


class TestTheFrame:
    def test_shall_get_grafana(self, admin_session):
        resp = admin_session.get(f"/{GRAFANA_PATH}/", headers=FRAMED)

        assert resp.status_code == 200
        assert SHELL_MARK not in resp.text
        assert "grafana" in resp.text.lower()

    def test_shall_get_the_admin_panel(self, admin_session):
        resp = admin_session.get(f"/{CRUDMAN_PATH}/", headers=FRAMED)

        assert SHELL_MARK not in resp.text
        assert "nav-sidebar" in resp.text

    @pytest.mark.parametrize("path", [f"/{CRUDMAN_PATH}/", f"/{GRAFANA_PATH}/", f"/{NOTEBOOK_PATH}/hub/"])
    def test_shall_be_allowed_by_every_app(self, admin_session, path):
        """A frame from the same origin, which Django's, Grafana's and the hub's
        defaults all refuse."""
        resp = admin_session.get(path, headers=FRAMED)

        assert resp.headers.get("x-frame-options", "sameorigin").lower() == "sameorigin"
        assert "frame-ancestors 'none'" not in resp.headers.get("content-security-policy", "")

    def test_shall_be_allowed_by_a_notebook_server_too(self, admin_session, notebook_server):
        """The server the hub spawns refuses every frame as well, on its own, whatever
        the hub itself allows."""
        resp = admin_session.get(notebook_server, headers=FRAMED)

        assert "frame-ancestors 'self'" in resp.headers.get("content-security-policy", "")

    def test_the_sign_in_shall_hand_itself_to_the_window(self, http):
        """A session that ran out while the bar was up."""
        resp = http.get(CRUDMAN_LOGIN, headers=FRAMED)

        assert resp.status_code == 200
        assert "top.location.replace" in resp.text
