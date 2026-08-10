# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach docker.io (see DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/library/nginx:alpine

# Company proxy for the package install below, passed as --build-arg by CI so the build
# works from behind a corporate proxy. apk reads it from the environment on its own.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# The nginx image ships no openssl binary. The entrypoint needs one to put the operator's
# fullchain.pem into the order nginx requires (leaf certificate first) regardless of how
# it was assembled, e.g. from a converted p7b file that lists the root CA first.
RUN apk add --no-cache openssl

# Bake the proxy configuration (the two templates and the entrypoint) into the image so
# the server needs no checkout to run it. The certificate is the only piece that stays
# host-local: it is a per-host secret and is bind-mounted at /etc/nginx/proxy/certs.
COPY proxy/http.conf.template proxy/https.conf.template /etc/nginx/proxy/
# The entrypoint is committed executable, so a plain COPY (no BuildKit --chmod) keeps it.
COPY proxy/entrypoint.sh /etc/nginx/proxy/entrypoint.sh

# The base paths the apps are served under, baked as defaults. DEBUG selects HTTP vs
# HTTPS and is passed at runtime (the only dev/prod difference).
ENV CRUDMAN_PATH=crudman
ENV GRAFANA_PATH=grafana

CMD ["/etc/nginx/proxy/entrypoint.sh"]
