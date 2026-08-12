# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach docker.io (see DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/grafana/grafana:latest

# Bake the provisioning (the read-only PostgreSQL data source and the shipped dashboards)
# into the image so the server needs no checkout. build.sh runs grafana/render.sh, which
# renders grafana/provisioning/ into grafana/.provisioning, substituting APP_NAME and SERVER_STATS_SCHEMA from
# buildtime.env, because Grafana does not interpolate those inside dashboard JSON. The
# data-source password still comes from a podman secret at runtime, and the admin login
# and sub-path are set via GF_* env in the quadlet.
COPY grafana/.provisioning/ /etc/grafana/provisioning/

# Bake the configuration into the image: Grafana's own sample.ini at the defaults, where a
# static setting (SSO, SMTP) is turned on by uncommenting it and rebuilding. Not logging,
# though -- the image's entrypoint passes log.mode on the command line, which overrides
# this file, so it is GF_LOG_MODE in the quadlet instead.
# Secret values are referenced from podman secrets via $__file{}, never stored in the file.
COPY grafana/custom.ini /etc/grafana/grafana.ini

# The base path Grafana is served under, as a default the quadlet overrides with the
# build-time GRAFANA_PATH. The entrypoint builds the public address out of it.
ENV GRAFANA_PATH=grafana

# The entrypoint settles that address and hands over to the image's own /run.sh. It is
# committed executable, so a plain COPY (no BuildKit --chmod) keeps it.
COPY grafana/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
