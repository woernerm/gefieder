"""The Grafana MCP server: reachable through the proxy, and bounded by the caller's role.

The server holds no Grafana credential of its own. Every call carries the caller's own --
a service-account token in Authorization, or the session cookie a signed-in browser holds
-- which the server copies onto the Grafana API requests it makes (GRAFANA_FORWARD_HEADERS
in quadlets/mcp.container). So Grafana decides what a call may do, and an Admin, an Editor
and a Viewer differ here exactly as they do in the user interface.

That is the property worth testing, because the alternative configuration -- one shared
service-account token in the server's environment -- looks identical until someone without
rights asks it to write, and then succeeds. TestPermissionsFollowTheCaller is what tells
the two apart: the same call, made with three different credentials, has to give three
different answers.
"""
import json
import uuid

import httpx
import pytest

from conftest import BASE_URL, GRAFANA_PATH, MCP_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD, VERIFY_TLS

# The endpoint the streamable-http transport serves, below the prefix the proxy strips.
MCP_URL = f"{BASE_URL}/{MCP_PATH}/mcp"

# Both are required: the transport answers a request that accepts only JSON with 406, and
# replies with an SSE frame whenever it has a stream to send.
ACCEPT = "application/json, text/event-stream"

# The protocol version the handshake negotiates. Pinned rather than taken from the server,
# so a client speaking this version keeps working.
PROTOCOL_VERSION = "2024-11-05"


def parse_rpc(response):
    """The JSON-RPC object out of a reply, which may be a bare body or an SSE frame.

    Which of the two comes back is the transport's choice, so a test that only handled
    one would fail on a server behaving correctly.
    """
    body = response.text
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(f"no data frame in the event stream: {body!r}")
    return json.loads(body)


class McpClient:
    """One MCP session, authenticated as whoever the token belongs to.

    A session is opened by the initialize handshake and named by the Mcp-Session-Id the
    server returns; every later call carries it back.
    """

    def __init__(self, token=None, cookies=None):
        self.token = token
        self.cookies = cookies or {}
        self.session_id = None

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": ACCEPT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload):
        with httpx.Client(verify=VERIFY_TLS, trust_env=False, timeout=60,
                          cookies=self.cookies) as client:
            return client.post(MCP_URL, headers=self._headers(), json=payload)

    def initialize(self):
        response = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "gefieder-tests", "version": "1"}},
        })
        assert response.status_code == 200, (
            f"the mcp handshake failed: {response.status_code} {response.text[:200]}"
        )
        self.session_id = response.headers.get("Mcp-Session-Id")
        assert self.session_id, "the server returned no Mcp-Session-Id"
        # Without it the server considers the handshake unfinished and refuses calls.
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return parse_rpc(response)["result"]

    def list_tools(self):
        reply = parse_rpc(self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        return {tool["name"]: tool for tool in reply["result"]["tools"]}

    def call(self, name, **arguments):
        """Invoke a tool, returning (text, is_error).

        A tool that fails reports it in the result with isError rather than as a JSON-RPC
        error, so a refusal by Grafana arrives here as ordinary content.
        """
        reply = parse_rpc(self._post({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }))
        assert "error" not in reply, f"{name} failed at the protocol level: {reply['error']}"
        result = reply["result"]
        text = "".join(part.get("text", "") for part in result.get("content", []))
        return text, result.get("isError", False)


@pytest.fixture(scope="module")
def grafana_api():
    """Grafana's API as the admin, used to mint the tokens the MCP calls then carry."""
    with httpx.Client(base_url=f"{BASE_URL}/{GRAFANA_PATH}", verify=VERIFY_TLS,
                      auth=(SUPERUSER_NAME, SUPERUSER_PASSWORD),
                      follow_redirects=True, timeout=30) as client:
        yield client


def service_account_token(grafana_api, role):
    """Create a service account with a basic role and return a token for it.

    A service account, not a person: it is the same credential a human's API token is, it
    carries one of the three basic roles, and it needs no password to be set up.
    """
    name = f"mcp-test-{role.lower()}-{uuid.uuid4().hex[:8]}"
    created = grafana_api.post("/api/serviceaccounts", json={"name": name, "role": role})
    assert created.status_code in (200, 201), (
        f"creating the {role} service account failed: "
        f"{created.status_code} {created.text[:200]}"
    )
    account_id = created.json()["id"]
    # A token name has to be unique across the whole organization, not just within its
    # service account, so the generated name is reused rather than a fixed one.
    token = grafana_api.post(f"/api/serviceaccounts/{account_id}/tokens",
                             json={"name": name})
    assert token.status_code in (200, 201), (
        f"minting the {role} token failed: {token.status_code} {token.text[:200]}"
    )
    return token.json()["key"], account_id


@pytest.fixture(scope="module")
def roles(grafana_api):
    """One token per basic role, removed again when the module is done."""
    created = {}
    for role in ("Admin", "Editor", "Viewer"):
        created[role] = service_account_token(grafana_api, role)
    yield {role: token for role, (token, _) in created.items()}
    for _, account_id in created.values():
        grafana_api.delete(f"/api/serviceaccounts/{account_id}")


@pytest.fixture(scope="module")
def admin_client(roles):
    client = McpClient(token=roles["Admin"])
    client.initialize()
    return client


class TestReachability:
    """The server answers through the proxy, on the path the deployment publishes."""

    def test_the_health_endpoint_shall_answer(self):
        # Below the same prefix, the proxy stripping it before the server sees it. Proves
        # the container is up and the proxy route reaches it, without a credential.
        with httpx.Client(verify=VERIFY_TLS, trust_env=False, timeout=30) as client:
            response = client.get(f"{BASE_URL}/{MCP_PATH}/healthz")
        assert response.status_code == 200, (
            f"the mcp health endpoint answered {response.status_code}; "
            "the container is down or the proxy does not route to it"
        )

    def test_the_handshake_shall_succeed(self):
        # No credential: the handshake is between client and server and reaches Grafana
        # not at all, so it has to work before any question of permissions arises.
        result = McpClient().initialize()
        assert result["protocolVersion"] == PROTOCOL_VERSION, (
            f"the server negotiated {result['protocolVersion']!r}"
        )
        assert result["serverInfo"]["name"] == "mcp-grafana", (
            f"an unexpected server answered: {result['serverInfo']}"
        )

    def test_the_enabled_tool_categories_shall_be_offered(self, admin_client):
        """The opt-in categories buildtime.env names, which are off by default."""
        tools = admin_client.list_tools()
        # One tool per category that would be missing had --enabled-tools not named it:
        # admin, runpanelquery and examples are all off in the server's own default.
        for tool in ("list_users_by_org", "run_panel_query", "get_query_examples"):
            assert tool in tools, (
                f"{tool} is missing; GRAFANA_MCP_TOOLS did not reach the server "
                f"(it offers {len(tools)} tools)"
            )

    def test_the_write_tools_shall_be_offered(self, admin_client):
        """--disable-write is deliberately not set: the server may act, not only read."""
        assert "update_dashboard" in admin_client.list_tools(), (
            "update_dashboard is missing; the server is running read-only"
        )


class TestDummyRequests:
    """Ordinary calls against the running Grafana, as an assistant would make them."""

    def test_the_datasource_shall_be_listed(self, admin_client):
        # The provisioned PostgreSQL data source, so this covers the whole path: proxy,
        # MCP server, forwarded credential, Grafana API.
        text, is_error = admin_client.call("list_datasources")
        assert not is_error, f"list_datasources failed: {text[:300]}"
        assert "postgres" in text.lower(), (
            f"the provisioned postgresql data source is not in the reply: {text[:300]}"
        )

    def test_the_provisioned_dashboard_shall_be_found(self, admin_client):
        text, is_error = admin_client.call("search_dashboards", query="")
        assert not is_error, f"search_dashboards failed: {text[:300]}"
        assert "server-monitoring" in text, (
            f"the shipped server-monitoring dashboard is not in the reply: {text[:300]}"
        )

    def test_a_dashboard_summary_shall_be_returned(self, admin_client, grafana_api):
        """The lighter of the two ways to read a dashboard, and the one to prefer."""
        found = grafana_api.get("/api/search", params={"type": "dash-db"}).json()
        assert found, "no dashboard is provisioned to summarise"
        uid = found[0]["uid"]
        text, is_error = admin_client.call("get_dashboard_summary", uid=uid)
        assert not is_error, f"get_dashboard_summary failed: {text[:300]}"
        assert "panels" in text.lower(), f"the summary carries no panels: {text[:300]}"


class TestPermissionsFollowTheCaller:
    """A call may do exactly what the credential it carries may do -- nothing more.

    The point of the whole configuration: no shared account, so the answer to "may this
    call write?" is Grafana's to give, per caller.
    """

    @pytest.mark.parametrize("role", ["Admin", "Editor", "Viewer"])
    def test_the_server_shall_report_the_callers_own_identity(self, roles, role):
        # Were a shared service-account token configured, all three would come back as
        # that one account instead.
        client = McpClient(token=roles[role])
        client.initialize()
        text, is_error = client.call("user_info")
        assert not is_error, f"user_info failed as {role}: {text[:300]}"
        assert role.lower() in text.lower(), (
            f"the {role} credential was reported as somebody else: {text[:300]}"
        )

    def test_an_unauthenticated_call_shall_be_refused(self):
        """No credential means no access: the server has none of its own to fall back on."""
        client = McpClient()
        client.initialize()
        text, is_error = client.call("user_info")
        assert is_error, (
            f"an unauthenticated call reached Grafana anyway: {text[:300]} -- the server "
            "has a credential of its own, which would give every caller its rights"
        )
        assert "401" in text, f"expected Grafana's 401, got: {text[:300]}"

    def test_a_viewer_shall_be_refused_a_write(self, roles):
        """The refusal is Grafana's own, on the same rule that governs the web interface."""
        client = McpClient(token=roles["Viewer"])
        client.initialize()
        text, is_error = client.call(
            "update_dashboard",
            dashboard={"title": f"mcp-test-{uuid.uuid4().hex[:8]}", "panels": []},
            message="written by a viewer, which must not be allowed",
        )
        assert is_error, f"a Viewer wrote a dashboard through the mcp server: {text[:300]}"
        assert "403" in text or "permission" in text.lower(), (
            f"the write failed, but not for lack of permission: {text[:300]}"
        )

    def test_an_editor_shall_be_allowed_the_same_write(self, roles, grafana_api):
        """The other half: the refusal above is the Viewer's role, not a broken tool."""
        client = McpClient(token=roles["Editor"])
        client.initialize()
        title = f"mcp-test-{uuid.uuid4().hex[:8]}"
        text, is_error = client.call(
            "update_dashboard",
            dashboard={"title": title, "panels": []},
            message="written by an editor, which is allowed",
        )
        assert not is_error, f"an Editor could not write a dashboard: {text[:300]}"
        uid = json.loads(text)["uid"]
        try:
            # Grafana itself has to show it, not only the tool's own reply.
            stored = grafana_api.get(f"/api/dashboards/uid/{uid}")
            assert stored.status_code == 200, (
                f"the dashboard the mcp server reported writing is not in grafana: "
                f"{stored.status_code}"
            )
            assert stored.json()["dashboard"]["title"] == title
        finally:
            grafana_api.delete(f"/api/dashboards/uid/{uid}")
