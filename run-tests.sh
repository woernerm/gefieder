#!/bin/sh
# Bring up a throwaway stack from the quadlets, run the integration suite against it and
# tear it down. Meant to run far from any production system: it builds fresh images,
# starts a stack with an empty database on isolated ports, and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
#   ./run-tests.sh dev -k logs run only the matching integration tests
set -e

# The profile is the optional first argument; everything after it goes to pytest, so one
# failing test can be re-run. The image build is cached.
PROFILE=dev
case "${1:-}" in
  dev|production) PROFILE="$1"; shift ;;
esac
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Checked up front, so a missing one fails in a second rather than after the image build.

# podman runs the whole stack.
if ! command -v podman >/dev/null 2>&1; then
  echo "podman is not installed; it is required to build and run the test stack." >&2
  exit 1
fi

# envsubst renders the quadlet and server-stats templates below. It comes from
# gettext-base, which minimal Ubuntu and RHEL images do not install, and without this check
# a missing package costs a full build before the templates render empty.
if ! command -v envsubst >/dev/null 2>&1; then
  echo "envsubst is not installed; it renders the quadlet templates." >&2
  echo "Install it with: sudo apt install gettext-base   (RHEL: sudo dnf install gettext)" >&2
  exit 1
fi

# uv runs the test suites at the end of the script, likewise long after the build.
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed; it provides the test dependencies." >&2
  echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# node runs the shipped ECharts bundle in test_lineage_chart.py, which is what stands
# between an unpinned library upgrade and a lineage that never draws.
if ! command -v node >/dev/null 2>&1; then
  echo "node is not installed; it renders the lineage chart in the test suite." >&2
  echo "Install it with: sudo apt install nodejs   (RHEL: sudo dnf install nodejs)" >&2
  exit 1
fi

# Rootless podman needs a usable subuid/subgid range, checked as install.sh checks it:
# asking podman rather than grepping /etc/subuid, which misses realm-joined users whose
# ranges come from SSSD/nss.
if ! podman unshare sh -c 'true' >/dev/null 2>&1; then
  echo "Rootless podman cannot set up a user namespace for '$(id -un)'." >&2
  echo "This usually means no subuid/subgid range is mapped. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  echo "Realm-joined users (name@domain) may need the range added to their directory." >&2
  exit 1
fi

# The whole run drives "systemd --user" for minutes. Without lingering, systemd stops the
# user manager at the last logout, taking its D-Bus socket with it, and every systemctl
# call in flight dies, leaving the pod and volumes for the next run to trip over. Check the
# result rather than the exit status: where polkit denies the request without a way to
# prompt, loginctl reports success but the setting does not stick.
linger_enabled() { [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = "yes" ]; }
if ! linger_enabled; then
  # Unprivileged first, escalating only if polkit denies it, as install.sh does.
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
  if ! linger_enabled && command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null || [ -t 0 ]; then
      sudo loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
    fi
  fi
  if ! linger_enabled; then
    echo "Could not enable lingering for $(id -un); the user manager may be torn down mid-run," >&2
    echo "failing systemctl with \"D-Bus connection terminated while waiting for jobs\"." >&2
    exit 1
  fi
fi

# The build-time settings, so the suite tests the configured stack rather than the
# defaults. runtime.env supplies SERVER_NAME; DEBUG comes from the profile below.
set -a
. ./buildtime.env
. ./runtime.env
set +a

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

# Scratch space, under TEMPDIR_TESTS from buildtime.env when set. That names the parent,
# so the cleanup trap only removes a directory this script created.
make_tempdir() {
  if [ -n "$TEMPDIR_TESTS" ]; then
    mkdir -p "$TEMPDIR_TESTS" || { echo "TEMPDIR_TESTS '$TEMPDIR_TESTS' is not usable." >&2; exit 1; }
    mktemp -d -p "$TEMPDIR_TESTS"
  else
    mktemp -d
  fi
}

# --- secrets ----------------------------------------------------------------------------
# The stack does not start without them, and the suite reads them back to connect as each
# role. Whatever is missing is created here, as dev.sh does; an existing secret is left
# alone, the host possibly holding the credentials of a reused database volume.
create_secret() {  # name, value
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"
# Single sign-on is off, but the placeholder must exist: the crudman and grafana quadlets
# name it in a Secret=, and podman will not start them without it.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"
# The build-time default rather than a random value, so the tests run unattended.
create_secret "$SECRET_SUPERUSER_PASSWORD" "$SUPERUSER_DEFAULT_PASSWORD"

# Isolated host ports, so a stack on the default ports is undisturbed. Grafana is not
# among them: the suite reaches it through the proxy, as a browser does.
HTTP_PORT=18080
HTTPS_PORT=18443
PG_PORT=15432
SFTP_PORT=12222
FLIGHT_PORT=18815

# The stand-in identity provider the single sign-on tests authenticate against. It runs in
# the pod, so the services reach it on localhost, and the pod publishes the same port
# number it listens on: it advertises an issuer built from the address it was asked on, and
# a sign-in validates only if the browser and the services see one identical URL. 127.0.0.1
# for the same reason, and because the server crashes on the IPv6 address.
OIDC_PORT=18099
OIDC_ISSUER="http://127.0.0.1:${OIDC_PORT}/default"
OIDC_CLIENT_ID="${APP_NAME}-test"

# A certificate directory of the run's own: the configured CERTIFICATE_PATH may be tailored
# to the target server's PKI and neither exist nor be writable here. The envsubst rendering
# below picks this value up, so the quadlet mounts the directory created here. Written for
# systemd, so "%h" is spelled as $HOME to resolve it host-side.
CERTIFICATE_PATH="%h/.config/${APP_NAME}-test/certs"
CERT_DIR="$(printf '%s' "${CERTIFICATE_PATH}" | sed "s|^%h|${HOME}|")"
mkdir -p "$CERT_DIR"

# Built with podman, the engine that runs the quadlets, so the suite exercises what a
# deployment runs: docker and podman keep separate image stores, so build.sh's docker build
# would be invisible here. From the working tree, not pulled; build args in build-lib.sh.
render_build_templates
for svc in $SERVICES; do
  build_image podman "$svc"
done

# The suite connects as each role to check its access boundary.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_GRAFANA_PASSWORD")"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SUPERUSER_PASSWORD")"
CRUDMAN_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_CRUDMAN_PASSWORD")"
SQLMESH_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SQLMESH_PASSWORD")"

if [ "$PROFILE" = "production" ]; then
  DEBUG=false
  SCHEME=https
  APP_PORT="$HTTPS_PORT"
  # A self-signed certificate for this run, in the cert dir the proxy quadlet mounts.
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" >/dev/null 2>&1
else
  DEBUG=true
  SCHEME=http
  APP_PORT="$HTTP_PORT"
fi
export DEBUG

# The generator scans only this fixed path, so the test installs where a deployment does.
# It asks before removing one already there.
QUADLET_DIR="$HOME/.config/containers/systemd"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$QUADLET_DIR"

UNITS="postgresql crudman sftp flight sqlmesh grafana proxy"
# The volumes the current deployment uses, plus crudman_data/sqlmesh_data, which held the
# logs that now go to journald and linger on an older installation.
VOLUMES="postgresql_data grafana_data sftp_data proxy_data uploads_data \
  crudman_data sqlmesh_data"

# Stop the services and drop the pod, volumes and unit files. Shared by our own teardown
# and by the removal of a pre-existing deployment, both living under the same names.
remove_deployment() {
  stack_stop="$(date +%s)"
  # In one go, as uninstall.sh does. Quadlet binds every container unit to
  # main-pod.service, so stopping the pod takes them down together and Restart=always does
  # not fire. Unit by unit would stop the database first and leave the rest failing their
  # healthcheck, which Restart=always *does* answer.
  printf '  stopping the pod ... '
  systemctl --user stop main-pod.service >/dev/null 2>&1 || true
  printf 'stopped (%ss)\n' "$(( $(date +%s) - stack_stop ))"
  # Explicitly afterwards, so systemd holds no unit "active" over a dead container.
  for u in $UNITS; do systemctl --user stop "${u}.service" >/dev/null 2>&1 || true; done
  systemctl --user stop server-stats.timer >/dev/null 2>&1 || true
  printf '  removing pod and volumes ... '
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
  podman volume rm -f $VOLUMES >/dev/null 2>&1 || true
  printf 'removed (%ss)\n' "$(( $(date +%s) - stack_stop ))"
  # A glob rather than quadlets/, so a release's unit files this checkout does not know
  # about are cleared too.
  rm -f "$QUADLET_DIR"/*.pod "$QUADLET_DIR"/*.container "$QUADLET_DIR"/*.volume
  rm -f "$SYSTEMD_USER_DIR/server-stats.service" "$SYSTEMD_USER_DIR/server-stats.timer"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}

# The test installs over the same paths, so an existing deployment goes first. Asked
# rather than refused: on a machine kept for testing, redeploying by hand is tedious.
if [ -e "$QUADLET_DIR/main.pod" ]; then
  echo "A deployment is already installed in $QUADLET_DIR."
  echo "Running the tests requires removing it, including its volumes and all data in them."
  printf "Delete the deployment and continue? [y/N] "
  if [ -t 0 ] && [ -t 1 ]; then
    read -r reply
  elif [ -r /dev/tty ]; then
    read -r reply </dev/tty
  else
    reply="n"
  fi
  case "$reply" in
    y|Y|yes|YES|Yes)
      echo "Removing the existing deployment ..."
      remove_deployment
      ;;
    *)
      echo "Keeping the deployment; not running the tests." >&2
      exit 1
      ;;
  esac
fi

# As the release workflow does: only the known tokens, so nginx's $host and Grafana's
# %(domain)s survive.
VARS='${REGISTRY} ${IMAGE_TAG} ${APP_NAME} ${SUPERUSER_NAME} ${SUPERUSER_EMAIL} ${CRUDMAN_PATH} ${GRAFANA_PATH} ${CERTIFICATE_PATH} ${SERVER_STATS_INTERVAL} ${SERVER_STATS_SCHEMA} ${PG_DATABASE} ${CRUDMAN_DB_USER} ${SQLMESH_DB_USER} ${DB_USER_PREFIX} ${ROLE_PREFIX} ${BRONZE_SCHEMA_PREFIX} ${SECRET_SUPERUSER_PASSWORD} ${SECRET_CRUDMAN_PASSWORD} ${SECRET_SQLMESH_PASSWORD} ${SECRET_GRAFANA_PASSWORD} ${SECRET_DJANGO_KEY} ${SECRET_OIDC_CLIENT}'
for f in quadlets/*; do
  envsubst "$VARS" < "$f" > "$QUADLET_DIR/$(basename "$f")"
done

# The isolated ports arrive by the path a deployment's do: envsubst leaves the ${*_PORT}
# tokens alone, quadlet copies them into the unit and systemd expands them from the
# EnvironmentFile. Editing the file here would leave that untested.
#
# One line is added, for the stand-in identity provider no deployment has. Appended after
# the [Pod] header, a PublishPort under [Service] being silently ignored.
sed -i "/^\[Pod\]/a PublishPort=${OIDC_PORT}:${OIDC_PORT}" "$QUADLET_DIR/main.pod"

# Grafana builds its absolute URLs from root_url rather than from the request, so a
# non-standard port has to be spelled out -- the step the README asks a custom-port
# installation to take. No container can see the port it is published on.
sed -i "/^Environment=GF_SERVER_SERVE_FROM_SUB_PATH=/i Environment=GF_SERVER_ROOT_URL=${SCHEME}://localhost:${APP_PORT}/${GRAFANA_PATH}/" \
  "$QUADLET_DIR/grafana.container"

# The password file the crudman unit tests mount, declared here for the cleanup trap.
UNIT_SECRET_DIR=""

# As the release installer does, so the suite exercises the real host-side sampler. The
# suite triggers a sample itself rather than waiting for the timer.
APP_CONFIG_DIR="$HOME/.config/${APP_NAME}"
mkdir -p "$SYSTEMD_USER_DIR" "$APP_CONFIG_DIR/serverstats"
for u in serverstats/server-stats.service serverstats/server-stats.timer; do
  envsubst "$VARS" < "$u" > "$SYSTEMD_USER_DIR/$(basename "$u")"
done
install -m 0755 serverstats/collect.sh "$APP_CONFIG_DIR/serverstats/collect.sh"
# The quadlets read SERVER_NAME and DEBUG from here, so the profile's values are written
# rather than the repository defaults, which would put the production profile back into
# DEBUG. The ports likewise: 80/443 would collide with a deployment and be unbindable for a
# rootless run. Everything else is copied unchanged, "|| true" keeping a file without those
# keys from aborting under set -e.
#
# The single sign-on settings point at the stand-in provider but arrive switched off, the
# state every installation runs in; the sign-in tests turn OIDC_ENABLED on themselves.
# ERROR_LOGGING_PROBE adds the route test_logs.py makes the server raise on, set only here
# so no deployment serves it.
{
  echo "SERVER_NAME=${SERVER_NAME}"
  echo "DEBUG=${DEBUG}"
  echo "ERROR_LOGGING_PROBE=true"
  echo "HTTP_PORT=${HTTP_PORT}"
  echo "HTTPS_PORT=${HTTPS_PORT}"
  echo "PG_PORT=${PG_PORT}"
  echo "SFTP_PORT=${SFTP_PORT}"
  echo "FLIGHT_PORT=${FLIGHT_PORT}"
  echo "OIDC_ENABLED=false"
  echo "OIDC_ISSUER=${OIDC_ISSUER}"
  echo "OIDC_AUTH_URL=${OIDC_ISSUER}/authorize"
  echo "OIDC_TOKEN_URL=${OIDC_ISSUER}/token"
  echo "OIDC_USERINFO_URL=${OIDC_ISSUER}/userinfo"
  echo "OIDC_LOGOUT_URL=${OIDC_ISSUER}/endsession"
  echo "OIDC_CLIENT_ID=${OIDC_CLIENT_ID}"
  grep -v -e '^SERVER_NAME=' -e '^DEBUG=' -e '^OIDC_' -e '^HTTP_PORT=' \
          -e '^HTTPS_PORT=' -e '^PG_PORT=' -e '^SFTP_PORT=' -e '^FLIGHT_PORT=' \
          -e '^ERROR_LOGGING_PROBE=' \
          runtime.env || true
} > "$APP_CONFIG_DIR/runtime.env"

cleanup() {
  # On EXIT, after pytest's summary. Silenced, so it is announced: this is the pause
  # between the result and the prompt returning.
  echo "Tearing down the test stack ..."
  remove_deployment
  rm -rf "$CERT_DIR"
  # The unit tests' password file, should they have failed before removing it. Unset
  # until they run, so guard against rm -rf on an empty path.
  if [ -n "$UNIT_SECRET_DIR" ]; then rm -rf "$UNIT_SECRET_DIR"; fi
}
trap cleanup EXIT INT TERM

systemctl --user daemon-reload
# Starting the proxy pulls in the rest of the pod, but every unit is started explicitly so
# a failure surfaces rather than being masked. Each start blocks on the healthcheck
# (Notify=healthy), which is the long, silent pause.
echo "Starting the test stack and waiting for every service to become healthy ..."
stack_start="$(date +%s)"
for u in $UNITS; do
  # The label goes out unterminated so it shows while the start blocks, and the result
  # completes the line. No \r: it cannot shorten a printed line, leaving a tail behind.
  printf '  %-12s ' "$u"
  systemctl --user start "${u}.service"
  printf 'healthy (%ss)\n' "$(( $(date +%s) - stack_start ))"
done

# --- the stand-in identity provider ---------------------------------------------------
# A throwaway OpenID Connect server, so a sign-in can be tested end to end without a real
# directory. It joins the pod rather than taking a network of its own, which puts it on the
# localhost the services already use.
#
# interactiveLogin false makes it usable from a test: the authorization endpoint answers
# with the redirect instead of a login form. "roles" is what an Entra ID app role arrives
# in, and Editor is the middle rank, so both a granted and a withheld permission can be
# asserted.
#
# The mapping is keyed on grant_type because it is matched against the token request, not
# the code request: a parameter appearing only on the latter never matches, and the claims
# are silently left out.
printf '  %-12s ' "identity"
podman run -d --pod "$APP_NAME" --name mock-oidc \
  -e SERVER_PORT="$OIDC_PORT" \
  -e JSON_CONFIG="$(cat <<EOF
{
  "interactiveLogin": false,
  "tokenCallbacks": [{
    "issuerId": "default",
    "tokenExpiry": 3600,
    "requestMappings": [{
      "requestParam": "grant_type",
      "match": "authorization_code",
      "claims": {
        "sub": "kim",
        "preferred_username": "kim",
        "email": "kim@example.com",
        "name": "Kim Tester",
        "roles": ["Editor"]
      }
    }]
  }]
}
EOF
)" ghcr.io/navikt/mock-oauth2-server:2.1.10 >/dev/null
# Wait for it to answer its own discovery document before any test relies on it.
oidc_ready=""
oidc_deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$oidc_deadline" ]; do
  if curl -fsS --max-time 5 "${OIDC_ISSUER}/.well-known/openid-configuration" >/dev/null 2>&1; then
    oidc_ready="yes"
    break
  fi
  sleep 1
done
[ -n "$oidc_ready" ] || { echo "the stand-in identity provider did not come up" >&2; exit 1; }
printf 'ready (%ss)\n' "$(( $(date +%s) - stack_start ))"

export TEST_PROFILE="$PROFILE"
export TEST_BASE_URL="$SCHEME://localhost:$APP_PORT"
export TEST_HTTP_BASE_URL="http://localhost:$HTTP_PORT"
export TEST_OIDC_ISSUER="$OIDC_ISSUER"
export TEST_OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
export TEST_APP_CONFIG_DIR="$APP_CONFIG_DIR"
export TEST_PG_PORT="$PG_PORT"
export TEST_PG_DATABASE="$PG_DATABASE"
export TEST_SFTP_PORT="$SFTP_PORT"
export TEST_FLIGHT_PORT="$FLIGHT_PORT"
export TEST_GRAFANA_PASSWORD="$GRAFANA_PASSWORD"
export TEST_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD"
export TEST_CRUDMAN_PASSWORD="$CRUDMAN_PASSWORD"
export TEST_SQLMESH_PASSWORD="$SQLMESH_PASSWORD"

# The server-statistics schema name and the path of the collector the suite triggers.
export TEST_SERVER_STATS_SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}"
export TEST_COLLECTOR="$APP_CONFIG_DIR/serverstats/collect.sh"

# The application logic the integration suite only sees from outside. Run first, in a
# throwaway container from the crudman image, so a broken pipeline fails here rather than
# as a puzzling HTTP result and against the dependencies the deployment ships.
#
# Django's test runner creates and drops its own test_<db> database, needing the CREATEDB
# the deployed crudman role deliberately lacks -- hence the superuser connection, its
# password mounted where settings.py reads the database password from.
echo "Running the crudman unit tests ..."
UNIT_SECRET_DIR="$(make_tempdir)"
podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SUPERUSER_PASSWORD" \
  > "$UNIT_SECRET_DIR/$SECRET_CRUDMAN_PASSWORD"
# collectstatic first: the tests render admin pages, and the manifest storage resolves
# every asset through the manifest the entrypoint builds at startup. UPLOADS_DIR and
# SFTP_DIR point into the container's own filesystem, which dies with it.
unit_tests() {  # extra podman arguments, e.g. the single sign-on settings
  podman run --rm --network host \
    -v "$UNIT_SECRET_DIR/$SECRET_CRUDMAN_PASSWORD:/run/secrets/$SECRET_CRUDMAN_PASSWORD:ro,Z" \
    -e SECRET_CRUDMAN_PASSWORD="$SECRET_CRUDMAN_PASSWORD" \
    -e POSTGRES_HOST=localhost -e POSTGRES_PORT="$PG_PORT" \
    -e POSTGRES_USER="$SUPERUSER_NAME" -e POSTGRES_DB="$PG_DATABASE" \
    -e DB_USER_PREFIX="$DB_USER_PREFIX" \
    -e ROLE_PREFIX="$ROLE_PREFIX" \
    -e BRONZE_SCHEMA_PREFIX="$BRONZE_SCHEMA_PREFIX" \
    -e UPLOADS_DIR=/tmp/uploads -e SFTP_DIR=/tmp/sftp \
    "$@" \
    --entrypoint sh "${REGISTRY}/crudman:${IMAGE_TAG}" -c \
    'uv run --project /crudman python manage.py collectstatic --noinput >/dev/null \
     && uv run --project /crudman python manage.py test --noinput'
}

unit_tests

# Again with single sign-on configured, which changes the installed apps and the URLs, so
# the settings module needs covering as its own configuration. The adapter cannot even be
# imported without it, so its tests skip themselves in the run above.
echo "Running the crudman unit tests with single sign-on configured ..."
unit_tests \
  -e OIDC_ENABLED=true \
  -e OIDC_ISSUER="$OIDC_ISSUER" \
  -e OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
rm -rf "$UNIT_SECRET_DIR"

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
echo "Running the integration test suite ..."
uv run --project tests pytest tests/ -v "$@"
