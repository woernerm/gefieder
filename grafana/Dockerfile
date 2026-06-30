FROM docker.io/grafana/grafana:latest

# Bake the provisioning (the read-only PostgreSQL data source) into the image so the
# server needs no checkout. The data-source password still comes from a podman secret
# at runtime, and the admin login and sub-path are set via GF_* env in the quadlet.
COPY grafana/provisioning/ /etc/grafana/provisioning/

# Bake the static, non-secret configuration (logging, optional SSO/SMTP) into the image.
# Secret values are referenced from podman secrets via $__file{}, never stored in the file.
COPY grafana/custom.ini /etc/grafana/grafana.ini
