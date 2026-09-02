---
name: grafana-mcp-docs
description: Grafana MCP server documentation to consult before answering questions about the Grafana MCP server (mcp-grafana), or configuring, installing or securing it.
---

# Grafana MCP server documentation

The Grafana MCP server changes quickly and its flag and environment variable names have
churned — `GRAFANA_API_KEY` is deprecated in favour of `GRAFANA_SERVICE_ACCOUNT_TOKEN`,
and tool categories move between the enabled-by-default and opt-in lists. 

One reference lives outside these pages: `mcp-grafana --help` prints the exact flag list
for an installed build, which the documentation itself defers to.

Note that most pages sit under a `grafana-cloud/ai-tools/` path but document the open
source server; the two under `developer-resources/` are the conceptual introduction.

## Index

- https://grafana.com/docs/grafana/latest/developer-resources/mcp/introduction/ —
  **Start here for "what can this thing do".** What MCP is, the tool families exposed
  (dashboards, datasources such as Prometheus and Loki, alerting, incidents, OnCall),
  and how service account tokens map onto RBAC permissions like `dashboards:read`.
  Recommends lighter tools such as `get_dashboard_summary` to limit context use.
- https://grafana.com/docs/grafana/latest/developer-resources/mcp/ — Navigation hub
  only. Useful for one decision: open source server versus the Grafana Cloud hosted one,
  and which MCP clients can connect. Everything else is links out; no configuration
  detail of its own.

## Configuration

- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/command-line-flags/
  — **The exhaustive flag reference; start here for any configuration question.**
  Transports, security defaults and `Host` header validation, authentication, tool
  categories, TLS, and Loki guardrails. Explains how `--disable-write`,
  `--disable-query` and `--enable-query` interact, and defers to `mcp-grafana --help`
  for the installed build's exact list.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/authentication/
  — `GRAFANA_URL` plus either a service account token or basic auth
  (`GRAFANA_USERNAME`/`GRAFANA_PASSWORD`). Covers why `GRAFANA_SERVICE_ACCOUNT_TOKEN`
  supersedes the deprecated `GRAFANA_API_KEY`, and `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE`,
  re-read per request so a rotated token needs no restart.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/enable-and-disable-tools/
  — **Read before exposing the server to an assistant.** Which tool categories are on by
  default, adding opt-in ones (`runpanelquery`, `examples`, `clickhouse`) through
  `--enabled-tools`, the `--disable-<category>` family, and the two safety modes:
  `--disable-write` for read-only, `--disable-query` to drop query execution while
  keeping metadata discovery.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/transports-and-addresses/
  — Choosing between `stdio` (default), `sse` and `streamable-http` with `-t/--transport`,
  their default addresses and ports, and overriding host, port, `--base-path` and
  `--endpoint-path`. Carries no security flags — `--allowed-hosts`, `--allowed-origins`
  and `--server-auth-token` are on the flags page.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/multi-organization-and-headers/
  — `GRAFANA_ORG_ID` to pin one organization, `--dynamic-multi-org` to let each call
  pass its own `orgId`, and `GRAFANA_EXTRA_HEADERS` as a JSON object for tenant routing
  or auth headers. Worth reading before any multi-tenant deployment: dynamic mode makes
  org selection caller-controlled.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/server-tls-streamable-http/
  — HTTPS for connections *into* the server, through `--server.tls-cert-file` and
  `--server.tls-key-file`. Applies to the streamable-http transport only. Distinct from
  the client TLS page below, which secures the opposite leg.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/client-tls-grafana-connection/
  — TLS for the server's own calls *out to* Grafana: `--tls-cert-file`, `--tls-key-file`
  and `--tls-ca-file` for mutual TLS or a private CA, plus the insecure
  `--tls-skip-verify` escape hatch.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/proxied-tools/
  — Tools borrowed from another MCP server (currently Grafana Tempo) and surfaced
  through this one as `tempo_<remote-tool-name>`, without the client connecting to it
  directly. Covers the credentials they need, how discovery varies by transport and
  multi-org setup, and `--disable-proxied`.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/configure/health-check-endpoint/
  — The `/healthz` endpoint, available on the SSE and streamable-http transports, and
  the response to expect. Short; fetch it when wiring a container health probe or a
  readiness check.

## Installation and clients

- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/install-with-docker/
  — **The page for a container deployment.** Running the image in stdio, SSE,
  streamable-http and HTTPS modes, with the port mappings each needs (`-p 8000:8000`,
  `-p 8443:8443`), environment variables, and mounting certificates as a read-only
  volume.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/ — Index of
  the four installation routes — uvx, Docker, binary and a Helm chart — with enough on
  each to choose. No configuration detail; skip unless deciding between them.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/install-with-uvx/
  — Running the server through `uvx` with no global install, and the environment it
  needs to reach Grafana. The lightest route for trying it out.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/install-the-binary/
  — Obtaining the executable three ways: Homebrew, the pre-built release downloads, or
  `go install` from source. Installation mechanics only.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/set-up/client-configuration-examples/
  — Worked client configurations across the installation methods, cross-cutting
  authentication, multi-organization, TLS and debugging. Useful when a client-specific
  page above doesn't match the chosen install route.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/clients/claude-code/
  — Wiring the server into Claude Code: the exact `claude mcp add-json` invocation, the
  local/project/user configuration scopes, and turning on read-only and debug mode.
  Includes prerequisites and troubleshooting steps.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/clients/claude-desktop/
  — Claude Desktop: the `.mcpb` bundle from GitHub Releases and the Go, binary and
  Docker alternatives; per-platform config file locations for macOS, Windows and Linux;
  verifying the connection; `--disable-write`; TLS client certificates; and where the
  debug logs live.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/clients/vscode-copilot/
  — VS Code with GitHub Copilot, which requires SSE transport rather than stdio.
  Configuration examples for that pairing plus running the server as a persistent
  background service.

## Tool reference and guides

- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/reference/mcp-tools-table/
  — **Every tool with its category and the RBAC permission and scope it requires.**
  Check it before granting a service account anything: it shows both wildcard scopes
  (`dashboards:*`) and UID-specific ones, and marks with `*` the categories that stay
  off until named in `--enabled-tools`.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/guides/search-and-inspect-dashboards/
  — Searching dashboards by title and reading them without pulling full JSON: the
  summary tool, panel queries, and `get_dashboard_property` with a JSONPath expression.
  The practical guide to keeping dashboard inspection inside the context window.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/guides/run-a-dashboard-panel-query/
  — Executing a panel's own query with time range and template variable overrides,
  instead of hand-writing PromQL or LogQL. Notes the required service account
  permissions, and that the `runpanelquery` tool is disabled by default.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/guides/manage-alert-rules/
  — Listing, fetching, creating, updating and deleting alert rules, plus contact points
  and notification policies, and the scopes each operation needs. Everything here is a
  write path, so it is unavailable under `--disable-write`.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/guides/generate-deeplinks-to-grafana/
  — Having the server build correct URLs to dashboards, panels and Explore, optionally
  carrying a time range or shortened, rather than assembling them by hand and getting
  the query parameters wrong.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/guides/use-grafana-incident-and-sift/
  — Grafana Incident and Sift: listing and creating incidents, adding notes, and running
  investigations that look for error patterns or slow requests. Relevant only if those
  products are in use.
- https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/oss-mcp/troubleshooting/grafana-version-compatibility/
  — Why `/datasources/uid/{uid}` calls fail against older Grafana, and the resulting
  **9.0 minimum** for full datasource functionality. Fetch when a datasource tool errors
  against an older instance.
