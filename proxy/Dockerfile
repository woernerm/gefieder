# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach docker.io (see DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/library/nginx:alpine

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
