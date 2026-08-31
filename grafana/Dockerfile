# The registry the base image is pulled from (DOCKER_IO_MIRROR in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
FROM ${DOCKER_IO_MIRROR}/grafana/grafana:latest

# The panel plugins to ship. Without a default, the list living in GRAFANA_PLUGINS in
# buildtime.env: a copy here would be a second place to edit.
ARG GRAFANA_PLUGINS

# Downloaded here rather than fetched on first start, so the server is offline-installable.
# Not into /var/lib/grafana/plugins: the quadlet mounts the data volume over it and would
# hide them. So /opt, with GF_PATHS_PLUGINS pointing Grafana there -- an environment
# variable, because the image's entrypoint passes paths.plugins on the command line, as
# with GF_LOG_MODE. Root, because the grafana user cannot write /opt. An empty list is
# fine.
USER root
RUN mkdir -p /opt/grafana/plugins \
    && for plugin in $(echo "${GRAFANA_PLUGINS}" | tr ',' ' '); do \
         grafana cli --pluginsDir /opt/grafana/plugins plugins install "${plugin}" || exit 1; \
       done \
    && chown -R grafana:root /opt/grafana
USER grafana
ENV GF_PATHS_PLUGINS=/opt/grafana/plugins

# The provisioning and the configuration, baked in so the server needs no checkout.
# build.sh runs grafana/render.sh, which substitutes the buildtime.env values Grafana does
# not interpolate itself: APP_NAME and SERVER_STATS_SCHEMA inside the dashboard JSON, and
# the names of the podman secrets both files read a password from.
COPY grafana/.render/provisioning/ /etc/grafana/provisioning/

# Grafana's own sample.ini at the defaults, where a static setting is turned on by
# uncommenting it and rebuilding. Not logging: the image's entrypoint passes log.mode on
# the command line, overriding this file, so it is GF_LOG_MODE in the quadlet. Secret
# values are referenced through $__file{}, never stored here.
COPY grafana/.render/grafana.ini /etc/grafana/grafana.ini

# A default the quadlet overrides with the build-time GRAFANA_PATH; the entrypoint builds
# the public address out of it.
ENV GRAFANA_PATH=grafana

# Settles that address and hands over to the image's own /run.sh. Committed executable, so
# a plain COPY keeps the mode without BuildKit's --chmod.
COPY grafana/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
