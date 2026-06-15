FROM docker.io/grafana/grafana:latest

# Bake the provisioning (the read-only PostgreSQL data source) into the image so the
# server needs no checkout. The data-source password still comes from a podman secret
# at runtime, and the admin login and sub-path are set via GF_* env in the quadlet.
COPY grafana/provisioning/ /etc/grafana/provisioning/
