# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach docker.io (see DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/grafana/grafana:latest

# The panel plugins to ship. Deliberately without a default: the list lives in
# GRAFANA_PLUGINS in buildtime.env (which also explains the separator) and the build
# scripts pass it as a --build-arg, so a copy here would be a second place to edit.
ARG GRAFANA_PLUGINS

# Downloading them here rather than letting Grafana fetch them on first start keeps the
# server offline-installable, the same arrangement as the DuckDB extensions. They cannot go
# into Grafana's default plugin directory, /var/lib/grafana/plugins: the quadlet mounts the
# data volume over /var/lib/grafana, which would hide anything baked in underneath it. So
# they live in /opt, and GF_PATHS_PLUGINS points Grafana there -- an environment variable
# rather than a custom.ini setting, because the image's entrypoint passes paths.plugins on
# grafana-server's command line, the same reason GF_LOG_MODE lives in the quadlet.
# Root because the image runs as the unprivileged grafana user, which cannot write /opt.
# An empty list is fine and installs nothing; unlike the database, Grafana works without.
USER root
RUN mkdir -p /opt/grafana/plugins \
    && for plugin in $(echo "${GRAFANA_PLUGINS}" | tr ',' ' '); do \
         grafana cli --pluginsDir /opt/grafana/plugins plugins install "${plugin}" || exit 1; \
       done \
    && chown -R grafana:root /opt/grafana
USER grafana
ENV GF_PATHS_PLUGINS=/opt/grafana/plugins

# Bake the provisioning (the read-only PostgreSQL data source and the shipped dashboards)
# and the configuration into the image so the server needs no checkout. build.sh runs
# grafana/render.sh, which renders grafana/provisioning/ and grafana/custom.ini into
# grafana/.render, substituting the buildtime.env values Grafana does not interpolate
# itself -- APP_NAME and SERVER_STATS_SCHEMA inside the dashboard JSON, and the names of
# the podman secrets both files read a password from.
COPY grafana/.render/provisioning/ /etc/grafana/provisioning/

# The configuration is Grafana's own sample.ini at the defaults, where a static setting
# (SSO, SMTP) is turned on by uncommenting it and rebuilding. Not logging, though -- the
# image's entrypoint passes log.mode on the command line, which overrides this file, so it
# is GF_LOG_MODE in the quadlet instead. Secret values are referenced from podman secrets
# via $__file{}, never stored in the file.
COPY grafana/.render/grafana.ini /etc/grafana/grafana.ini

# The base path Grafana is served under, as a default the quadlet overrides with the
# build-time GRAFANA_PATH. The entrypoint builds the public address out of it.
ENV GRAFANA_PATH=grafana

# The entrypoint settles that address and hands over to the image's own /run.sh. It is
# committed executable, so a plain COPY (no BuildKit --chmod) keeps it.
COPY grafana/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
