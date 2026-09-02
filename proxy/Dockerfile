# The registry the base image is pulled from (DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/library/nginx:alpine

# For the package install below, so the build works from behind a corporate proxy; apk
# reads it from the environment on its own.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# The nginx image ships none, and the entrypoint needs one to put the operator's
# fullchain.pem into the leaf-first order nginx requires.
RUN apk add --no-cache openssl

# Baked in so the server needs no checkout. The certificate is the only host-local piece,
# bind-mounted at /etc/nginx/proxy/certs. Two profile templates plus their shared
# fragments.
COPY proxy/http.conf.template proxy/https.conf.template \
     proxy/maps.conf.template proxy/locations.conf.template /etc/nginx/proxy/
# Committed executable, so a plain COPY keeps the mode without BuildKit's --chmod.
COPY proxy/entrypoint.sh /etc/nginx/proxy/entrypoint.sh

# Baked as defaults. DEBUG selects HTTP or HTTPS and is passed at runtime.
ENV CRUDMAN_PATH=crudman
ENV GRAFANA_PATH=grafana
ENV MCP_PATH=ai/grafana_mcp

CMD ["/etc/nginx/proxy/entrypoint.sh"]
