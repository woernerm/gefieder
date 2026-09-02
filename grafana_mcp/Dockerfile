# The Grafana MCP server: an assistant reaches Grafana's own API through it, as the person
# driving the assistant rather than as a shared account.
#
# Shipped as an image of our own, only re-tagging the upstream one, so install.sh loads it
# from the release tarball like every other image and the target machine pulls from no
# registry.
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/grafana/mcp-grafana:latest

# The tool categories to offer, from GRAFANA_MCP_TOOLS in buildtime.env. Build-time: it
# decides what the server can do, not who may ask it. Baked in rather than written into the
# quadlet's Exec= so the list has one home; the entrypoint passes it on, an ARG not
# reaching an exec-form CMD.
ARG GRAFANA_MCP_TOOLS
ENV GRAFANA_MCP_TOOLS=${GRAFANA_MCP_TOOLS}

# Root only to place the file: the image runs as mcp-grafana, and the server needs nothing
# more. Committed executable, so a plain COPY keeps the mode without BuildKit's --chmod.
USER root
COPY grafana_mcp/entrypoint.sh /entrypoint.sh
USER mcp-grafana
ENTRYPOINT ["/entrypoint.sh"]
