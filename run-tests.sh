#!/bin/sh
# Bring up a throwaway stack from the quadlets, run the integration test suite
# against it and tear it down again. Meant to run in isolation, far from any production
# system: it builds fresh images, starts a stack with an empty database under the project
# pod name and isolated ports, and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
#   ./run-tests.sh dev -k logs run only the matching integration tests
set -e

# The profile is the optional first argument; everything after it is handed to pytest, so
# one failing test can be re-run without waiting for the whole suite. The image build is
# cached, so a repeat run costs the stack start and the selected tests.
PROFILE=dev
case "${1:-}" in
  dev|production) PROFILE="$1"; shift ;;
esac
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Check the prerequisites up front so a missing one fails in a second with a clear message,
# rather than after the multi-minute image build or with an opaque error mid-run.

# podman runs the whole stack.
if ! command -v podman >/dev/null 2>&1; then
  echo "podman is not installed; it is required to build and run the test stack." >&2
  exit 1
fi

# envsubst renders the quadlet and server-stats templates below. It comes from gettext-base,
# which minimal Ubuntu and RHEL images do not install, and the rendering happens only after
# the image build -- without this check a missing package costs a full build before the
# templates silently render empty.
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

# node runs the shipped ECharts bundle in test_lineage_chart.py. The library is fetched
# unpinned at build time, so that test is what stands between a new release and a
# documentation page whose lineage silently never draws.
if ! command -v node >/dev/null 2>&1; then
  echo "node is not installed; it renders the lineage chart in the test suite." >&2
  echo "Install it with: sudo apt install nodejs   (RHEL: sudo dnf install nodejs)" >&2
  exit 1
fi

# Rootless podman needs a usable subuid/subgid range for the current user (same requirement
# the install script checks, and checked the same way). Rather than grep /etc/subuid, which
# misses realm-joined users like name@domain whose ranges come from SSSD/nss and are not
# listed there, ask podman itself: `unshare` only succeeds when a real user namespace with
# the range can be set up.
if ! podman unshare sh -c 'true' >/dev/null 2>&1; then
  echo "Rootless podman cannot set up a user namespace for '$(id -un)'." >&2
  echo "This usually means no subuid/subgid range is mapped. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  echo "Realm-joined users (name@domain) may need the range added to their directory." >&2
  exit 1
fi

# The whole run drives "systemd --user", and a run takes minutes: the image build and the
# healthcheck-blocked starts below are long, silent waits. Without lingering, systemd stops
# the user manager the moment the user's last session ends -- taking its D-Bus socket with
# it -- and every systemctl call in flight dies with "D-Bus connection terminated while
# waiting for jobs" / "Connection reset by peer", leaving the pod and volumes behind for the
# next run to trip over. install.sh enables lingering for a deployment; do the same here.
# Check the result rather than the exit status: where polkit denies the request without a way
# to prompt, loginctl still reports success but the setting does not stick.
linger_enabled() { [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = "yes" ]; }
if ! linger_enabled; then
  # Unprivileged first, escalating only if polkit denies it, exactly as install.sh does.
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

# Load the build-time settings so the suite tests the configured stack (CRUDMAN_PATH,
# GRAFANA_PATH, APP_NAME, SUPERUSER_NAME, ...) rather than assuming the defaults.
# runtime.env supplies SERVER_NAME; DEBUG comes from the profile selected below.
set -a
. ./buildtime.env
. ./runtime.env
set +a

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

# Scratch space for the throwaway files below, under TEMPDIR_TESTS from buildtime.env when
# set and the system default otherwise. TEMPDIR_TESTS names the parent, so what the cleanup
# trap removes is always a directory this script created.
make_tempdir() {
  if [ -n "$TEMPDIR_TESTS" ]; then
    mkdir -p "$TEMPDIR_TESTS" || { echo "TEMPDIR_TESTS '$TEMPDIR_TESTS' is not usable." >&2; exit 1; }
    mktemp -d -p "$TEMPDIR_TESTS"
  else
    mktemp -d
  fi
}

# --- secrets ----------------------------------------------------------------------------
# The stack does not start without them and the suite reads them back below to connect as
# each role. install.sh creates them on a deployment; a fresh checkout has none, so create
# whatever is missing here, exactly as dev.sh does. Existing secrets are left alone: the
# test host may already hold the credentials of the database volume being reused.
create_secret() {  # name, value
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"
# Single sign-on is off in the suite, but the placeholder still has to exist: the crudman
# and grafana quadlets name it in a Secret=, and podman will not start them without it.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"
# The superuser gets the well-known build-time default rather than a random value, so the
# tests run unattended without a prompt (install.sh asks for it interactively).
create_secret "$SECRET_SUPERUSER_PASSWORD" "$SUPERUSER_DEFAULT_PASSWORD"

# Isolated host ports so a running stack on the default ports is not disturbed, one per
# port the production pod publishes. Grafana is not among them: the suite reaches it
# through the proxy, as a browser does.
HTTP_PORT=18080
HTTPS_PORT=18443
PG_PORT=15432
SFTP_PORT=12222
FLIGHT_PORT=18815

# The stand-in identity provider the single sign-on tests authenticate against. It runs in
# the pod, so the services reach it on localhost, and the pod publishes the same port
# number it listens on -- the issuer it advertises is built from the address it was asked
# on, and a sign-in only validates if the browser and the services see one identical URL.
# 127.0.0.1 rather than localhost for the same reason, and because the server crashes on
# the IPv6 address localhost resolves to first.
OIDC_PORT=18099
OIDC_ISSUER="http://127.0.0.1:${OIDC_PORT}/default"
OIDC_CLIENT_ID="${APP_NAME}-test"

# Test runs use their own certificate directory rather than the configured CERTIFICATE_PATH:
# that value may be tailored to the target server's PKI (e.g. /etc/pki/${APP_NAME}) and neither
# exist nor be writable by whoever runs the suite on a dev machine. Overriding it here decouples
# the suite from it entirely; the envsubst rendering below picks up this value too, so the
# quadlet mounts exactly the directory created here. Written for systemd, so "%h" is spelled
# as $HOME below to resolve it host-side, before any container exists to do so itself.
CERTIFICATE_PATH="%h/.config/${APP_NAME}-test/certs"
CERT_DIR="$(printf '%s' "${CERTIFICATE_PATH}" | sed "s|^%h|${HOME}|")"
mkdir -p "$CERT_DIR"

# Build the custom images with podman, the engine that runs the quadlets, so the suite
# exercises exactly what a deployment runs. (build.sh builds with docker for the release
# workflow; docker and podman keep separate image stores, so a docker build would not be
# visible to the podman-run stack here.) Built from the working tree, not pulled; the
# build arguments are in build-lib.sh.
render_build_templates
for svc in $SERVICES; do
  build_image podman "$svc"
done

# The suite connects to the database as each role to check its access boundary; the
# passwords come from the podman secrets created above.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_GRAFANA_PASSWORD")"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SUPERUSER_PASSWORD")"
CRUDMAN_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_CRUDMAN_PASSWORD")"
SQLMESH_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SQLMESH_PASSWORD")"

if [ "$PROFILE" = "production" ]; then
  DEBUG=false
  SCHEME=https
  APP_PORT="$HTTPS_PORT"
  # A self-signed certificate just for this test run, in the host-local cert dir the
  # proxy quadlet mounts; removed on teardown.
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" >/dev/null 2>&1
else
  DEBUG=true
  SCHEME=http
  APP_PORT="$HTTP_PORT"
fi
export DEBUG

# A running "systemd --user" only scans this fixed path with its generator, so the test
# must install where the real deployment installs. So the test never silently clobbers a
# deployment already there, it asks for confirmation before removing it (below).
QUADLET_DIR="$HOME/.config/containers/systemd"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$QUADLET_DIR"

UNITS="postgresql crudman sftp flight sqlmesh grafana proxy"
# The volumes the current deployment uses, plus crudman_data/sqlmesh_data: those held only
# the persistent logs that now go to journald, and listing them here still removes them
# from a deployment installed before that change.
VOLUMES="postgresql_data grafana_data sftp_data proxy_data uploads_data \
  crudman_data sqlmesh_data"

# Stop the stack's services and drop its pod, volumes and unit files. Shared by the
# teardown of our own test stack and by the removal of a pre-existing deployment, which
# are the same operation -- both are installed in the same place under the same names.
remove_deployment() {
  stack_stop="$(date +%s)"
  # Stop the whole stack in one go, the order uninstall.sh uses too. Quadlet binds every
  # container unit to main-pod.service, so stopping the pod takes them down together, and a
  # stop systemd ordered is not a failure -- Restart=always does not fire. Unit by unit
  # would stop the database first and leave the rest running against a gone dependency:
  # those fail their healthcheck and exit on their own, which Restart=always *does* answer.
  printf '  stopping the pod ... '
  systemctl --user stop main-pod.service >/dev/null 2>&1 || true
  printf 'stopped (%ss)\n' "$(( $(date +%s) - stack_stop ))"
  # The units are stopped explicitly afterwards so systemd does not hold them "active"
  # with a dead container underneath, and so a unit that outlived the pod still goes.
  for u in $UNITS; do systemctl --user stop "${u}.service" >/dev/null 2>&1 || true; done
  systemctl --user stop server-stats.timer >/dev/null 2>&1 || true
  printf '  removing pod and volumes ... '
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
  podman volume rm -f $VOLUMES >/dev/null 2>&1 || true
  printf 'removed (%ss)\n' "$(( $(date +%s) - stack_stop ))"
  # Glob rather than iterate quadlets/, so a deployment installed from a release is
  # cleared even where it holds unit files this checkout does not know about.
  rm -f "$QUADLET_DIR"/*.pod "$QUADLET_DIR"/*.container "$QUADLET_DIR"/*.volume
  rm -f "$SYSTEMD_USER_DIR/server-stats.service" "$SYSTEMD_USER_DIR/server-stats.timer"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}

# The test installs over the same paths, so an existing deployment has to go first. Ask
# rather than refuse: on a machine kept for testing, re-deploying by hand every run is
# tedious. The data loss is spelled out because the volumes go with it.
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

# Render the quadlet templates the same way the release workflow does: substitute only
# the known tokens so nginx's $host and Grafana's %(domain)s are left untouched.
VARS='${REGISTRY} ${IMAGE_TAG} ${APP_NAME} ${SUPERUSER_NAME} ${SUPERUSER_EMAIL} ${CRUDMAN_PATH} ${GRAFANA_PATH} ${CERTIFICATE_PATH} ${SERVER_STATS_INTERVAL} ${SERVER_STATS_SCHEMA} ${PG_DATABASE} ${CRUDMAN_DB_USER} ${SQLMESH_DB_USER} ${DB_USER_PREFIX} ${ROLE_PREFIX} ${BRONZE_SCHEMA_PREFIX} ${SECRET_SUPERUSER_PASSWORD} ${SECRET_CRUDMAN_PASSWORD} ${SECRET_SQLMESH_PASSWORD} ${SECRET_GRAFANA_PASSWORD} ${SECRET_DJANGO_KEY} ${SECRET_OIDC_CLIENT}'
for f in quadlets/*; do
  envsubst "$VARS" < "$f" > "$QUADLET_DIR/$(basename "$f")"
done

# The isolated test ports arrive through the same path a deployment's do: envsubst leaves
# the ${*_PORT} tokens alone, quadlet copies them into the generated unit, and systemd
# expands them from the EnvironmentFile. Editing the file here would leave that untested.
#
# One line is added, for the stand-in identity provider that is no part of a deployment.
# Appended straight after the [Pod] header so it stays in [Pod] however the rest is
# arranged: a PublishPort under [Service] is silently ignored.
sed -i "/^\[Pod\]/a PublishPort=${OIDC_PORT}:${OIDC_PORT}" "$QUADLET_DIR/main.pod"

# Grafana builds its own absolute URLs from root_url rather than from the request, so on a
# port other than the standard one it has to be told -- exactly the step the README asks a
# custom-port installation to take, down to the added line. The entrypoint settles the rest
# of the address itself, but no container can see the port it is published on.
sed -i "/^Environment=GF_SERVER_SERVE_FROM_SUB_PATH=/i Environment=GF_SERVER_ROOT_URL=${SCHEME}://localhost:${APP_PORT}/${GRAFANA_PATH}/" \
  "$QUADLET_DIR/grafana.container"

# Holds the password file the crudman unit tests mount; created just before they run,
# declared here so the cleanup trap below can always refer to it.
UNIT_SECRET_DIR=""

# Install the server-statistics collector the way the release installer does, so the
# suite exercises the real host-side sampler: render its units into the systemd user dir
# and drop the collector and a runtime.env under ~/.config/<APP_NAME>/. The suite triggers
# a sample itself (rather than waiting for the timer) and asserts rows appear.
APP_CONFIG_DIR="$HOME/.config/${APP_NAME}"
mkdir -p "$SYSTEMD_USER_DIR" "$APP_CONFIG_DIR/serverstats"
for u in serverstats/server-stats.service serverstats/server-stats.timer; do
  envsubst "$VARS" < "$u" > "$SYSTEMD_USER_DIR/$(basename "$u")"
done
install -m 0755 serverstats/collect.sh "$APP_CONFIG_DIR/serverstats/collect.sh"
# The quadlets read SERVER_NAME and DEBUG from here, so write the profile's values rather
# than the repository defaults, which would put the production profile back into DEBUG.
# The ports go the same way: the repository's 80/443 would collide with a running
# deployment and be unbindable for a rootless run. Everything else is copied unchanged;
# "|| true" keeps a file without those keys from aborting the run under set -e.
#
# The single sign-on settings point at the stand-in provider but arrive switched off, the
# state every installation runs in. The sign-in tests turn OIDC_ENABLED on themselves.
#
# ERROR_LOGGING_PROBE adds the route test_logs.py uses to make the server raise. Set only
# here, so no deployment serves it.
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
  # Runs on EXIT after pytest prints its summary: stops the services, removes the pod and
  # deletes the throwaway volumes. The teardown is silenced, so announce it -- this is the
  # pause between the test result and the prompt returning.
  echo "Tearing down the test stack ..."
  remove_deployment
  rm -rf "$CERT_DIR"
  # The unit tests' mounted password file, in case they failed before removing it.
  # Unset until they run, so guard against rm -rf on an empty path.
  if [ -n "$UNIT_SECRET_DIR" ]; then rm -rf "$UNIT_SECRET_DIR"; fi
}
trap cleanup EXIT INT TERM

systemctl --user daemon-reload
# Starting the proxy pulls in the rest of the pod (After=/Requires=), but start every
# unit explicitly so a failure in any one surfaces here rather than being masked. Each
# start blocks on the service's healthcheck (Notify=healthy), so this waits for the
# database to run its init scripts and the apps to come up -- the long, silent pause.
echo "Starting the test stack and waiting for every service to become healthy ..."
stack_start="$(date +%s)"
for u in $UNITS; do
  # The label goes out first (unterminated, so it shows while the start blocks) and the
  # result completes the same line. No \r: it cannot shorten an already-printed line, so
  # rewriting the label in place would leave the tail of the longer text behind.
  printf '  %-12s ' "$u"
  systemctl --user start "${u}.service"
  printf 'healthy (%ss)\n' "$(( $(date +%s) - stack_start ))"
done

# --- the stand-in identity provider ---------------------------------------------------
# A throwaway OpenID Connect server, so a sign-in can be tested end to end without a real
# directory. It joins the pod rather than getting a network of its own, which is what puts
# it on the same localhost the services already use; removing the pod takes it with it.
#
# interactiveLogin false makes it usable from a test: the authorization endpoint answers
# with the redirect straight away instead of presenting a login form. Of the claims below,
# "roles" is what an Entra ID app role arrives in, and Editor is the middle of the three so
# both a granted and a withheld permission can be asserted.
#
# The mapping is keyed on grant_type because it is matched against the request for the
# token, not the one for the code: a parameter appearing only on the authorization request
# never matches, and the claims are then silently left out.
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

# The crudman unit tests cover the application logic the integration suite only sees from
# outside. They run first, in a throwaway container from the crudman image, so a broken
# pipeline fails here rather than as a puzzling HTTP result later, and from the image
# rather than a host virtualenv so the dependencies are the ones the deployment ships.
#
# Django's test runner creates and drops its own test_<db> database, so it needs CREATEDB,
# which the deployed crudman role deliberately lacks — hence the superuser connection. Its
# password is mounted where settings.py reads the database password from, the only way in.
echo "Running the crudman unit tests ..."
UNIT_SECRET_DIR="$(make_tempdir)"
podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SUPERUSER_PASSWORD" \
  > "$UNIT_SECRET_DIR/$SECRET_CRUDMAN_PASSWORD"
# collectstatic first: the tests render admin pages, and the manifest static files
# storage resolves every asset through the manifest the entrypoint builds at startup —
# a fresh container has none. UPLOADS_DIR/SFTP_DIR point into the container's own
# filesystem, which dies with it, so no test writes anywhere persistent.
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

# Again with single sign-on configured. Switching it on changes which apps are installed
# and which URLs exist, so the settings module takes a different path through itself and
# needs covering as its own configuration -- and the adapter that maps a provider's roles
# cannot even be imported without it, so its tests skip themselves in the run above.
echo "Running the crudman unit tests with single sign-on configured ..."
unit_tests \
  -e OIDC_ENABLED=true \
  -e OIDC_ISSUER="$OIDC_ISSUER" \
  -e OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
rm -rf "$UNIT_SECRET_DIR"

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
echo "Running the integration test suite ..."
uv run --project tests pytest tests/ -v "$@"
