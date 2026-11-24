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
# Not REPO: buildtime.env carries a REPO of its own (the GitHub URL) and is sourced below.
CHECKOUT="$(cd "$(dirname "$0")" && pwd)"
cd "$CHECKOUT"

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
# create_service_secrets is in build-lib.sh, shared with dev.sh. The suite reads them back
# to connect as each role.
create_service_secrets
# The build-time default rather than a random value, so the tests run unattended.
create_secret "$SECRET_SUPERUSER_PASSWORD" "$SUPERUSER_DEFAULT_PASSWORD"
create_secret "$SECRET_PG_SUPERUSER_PASSWORD" "$SUPERUSER_DEFAULT_PASSWORD"

# Isolated host ports, so a stack on the default ports is undisturbed. Grafana is not
# among them: the suite reaches it through the proxy, as a browser does.
HTTP_PORT=18080
HTTPS_PORT=18443
PG_PORT=15432
SFTP_PORT=12222
FLIGHT_PORT=18815

# The stand-in identity provider for the single sign-on tests. Published on the port it
# listens on, as it builds its issuer from the address asked, and browser and services must
# see one identical URL. 127.0.0.1, as the server also crashes on the IPv6 address.
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
# In parallel, the images being independent; a log is shown only for a failed build.

# A background job ignores Ctrl-C and outlives the script, so it is ended explicitly, with
# the podman it runs. Asked of the process table, as a remembered pid may be reused by now.
stop_jobs() {
  for job in $(pgrep -P $$); do
    kill "$job" $(pgrep -P "$job") 2>/dev/null || true
  done
}

render_build_templates
BUILD_DIR="$(make_tempdir)"
builds=""
trap 'stop_jobs; rm -rf "$BUILD_DIR"' EXIT
trap 'exit 130' INT TERM
for svc in $SERVICES; do
  build_image podman "$svc" >"$BUILD_DIR/$svc.log" 2>&1 &
  builds="$builds $svc:$!"
done
for build in $builds; do
  svc="${build%%:*}"
  if ! wait "${build#*:}"; then
    cat "$BUILD_DIR/$svc.log" >&2
    echo "Building the $svc image failed." >&2
    exit 1
  fi
  echo "  built $svc"
done
trap - EXIT INT TERM
rm -rf "$BUILD_DIR"

# The suite connects as each role to check its access boundary.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_GRAFANA_PASSWORD")"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_SUPERUSER_PASSWORD")"
PG_SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_PG_SUPERUSER_PASSWORD")"
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

VOLUMES="$(basename -s .volume quadlets/*.volume)"

# Stop the services and drop the pod, volumes and unit files. Shared by our own teardown
# and by the removal of a pre-existing deployment, as both use the same names.
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

for f in quadlets/*; do
  envsubst "$QUADLET_VARS" < "$f" > "$QUADLET_DIR/$(basename "$f")"
done

# Without quadlet's wait for network-online.target, which a host may never reach when
# nothing pulls that target in. The wait then fails after 90 s, and each "systemctl start"
# below or in the suite begins it anew, a unit's failed Wants= being retried every time. A
# deployment keeps it, as it starts at boot; here the network is already up.
for f in "$QUADLET_DIR"/*.pod "$QUADLET_DIR"/*.container "$QUADLET_DIR"/*.volume; do
  printf '\n[Quadlet]\nDefaultDependencies=false\n' >> "$f"
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

# The unit tests' password file and logs, and their containers, declared here for the
# cleanup trap.
UNIT_DIR=""
UNIT_CONTAINERS="${APP_NAME}-unit ${APP_NAME}-unit-db ${APP_NAME}-unit-sso ${APP_NAME}-unit-sso-db ${APP_NAME}-notebook-unit"

# As the release installer does, so the suite exercises the real host-side sampler. The
# suite triggers a sample itself rather than waiting for the timer.
APP_CONFIG_DIR="$HOME/.config/${APP_NAME}"
mkdir -p "$SYSTEMD_USER_DIR" "$APP_CONFIG_DIR/serverstats"
for u in serverstats/server-stats.service serverstats/server-stats.timer; do
  envsubst "$QUADLET_VARS" < "$u" > "$SYSTEMD_USER_DIR/$(basename "$u")"
done
install -m 0755 serverstats/collect.sh "$APP_CONFIG_DIR/serverstats/collect.sh"
# The profile's SERVER_NAME and DEBUG and the isolated ports replace the repository
# defaults; everything else is copied, "|| true" keeping a file without those keys from
# aborting under set -e. Single sign-on points at the stand-in provider but starts off, as
# in every installation; the sign-in tests turn it on. ERROR_LOGGING_PROBE adds the route
# test_logs.py makes the server raise on.
{
  echo "SERVER_NAME=${SERVER_NAME}"
  echo "DEBUG=${DEBUG}"
  echo "ERROR_LOGGING_PROBE=true"
  # Faster than a deployment's, so test_models_repository does not wait out a real poll.
  echo "MODELS_POLL_INTERVAL=5"
  echo "HTTP_PORT=${HTTP_PORT}"
  echo "HTTPS_PORT=${HTTPS_PORT}"
  echo "PG_PORT=${PG_PORT}"
  echo "SFTP_PORT=${SFTP_PORT}"
  echo "FLIGHT_PORT=${FLIGHT_PORT}"
  echo "OIDC_ENABLED=false"
  echo "OIDC_ISSUER=${OIDC_ISSUER}"
  echo "OIDC_LOGOUT_URL=${OIDC_ISSUER}/endsession"
  echo "OIDC_CLIENT_ID=${OIDC_CLIENT_ID}"
  grep -v -e '^SERVER_NAME=' -e '^DEBUG=' -e '^OIDC_' -e '^HTTP_PORT=' \
          -e '^HTTPS_PORT=' -e '^PG_PORT=' -e '^SFTP_PORT=' -e '^FLIGHT_PORT=' \
          -e '^ERROR_LOGGING_PROBE=' -e '^MODELS_POLL_INTERVAL=' \
          runtime.env || true
} > "$APP_CONFIG_DIR/runtime.env"

cleanup() {
  # The status the script is exiting with, saved before anything here overwrites it: the
  # trap's own last command is what the shell would otherwise report, which turned a failed
  # test run into a successful script.
  status=$?
  # Once only: the exit below would run it again after a signal.
  trap - EXIT INT TERM
  # First, or a job could start a container after the removal below.
  stop_jobs
  # On EXIT, after pytest's summary. Silenced, so it is announced: this is the pause
  # between the result and the prompt returning.
  echo "Tearing down the test stack ..."
  remove_deployment
  rm -rf "$CERT_DIR"
  # With -v, as the database image declares a volume that would stay behind.
  podman rm -f -v $UNIT_CONTAINERS >/dev/null 2>&1 || true
  # Unset until the unit tests start, so guard against rm -rf on an empty path.
  if [ -n "$UNIT_DIR" ]; then rm -rf "$UNIT_DIR"; fi
  exit "$status"
}
trap cleanup EXIT INT TERM

# --- unit tests -------------------------------------------------------------------------
# In the background while the stack starts and the integration suite runs, as they need
# nothing from the stack and took three minutes one after the other. Each crudman run gets
# a database of its own: test_resilience restarts the stack's, and both runs create the
# same test database and roles. Results are reported after the integration suite.
UNIT_DIR="$(make_tempdir)"
podman rm -f -v $UNIT_CONTAINERS >/dev/null 2>&1 || true
podman secret inspect --showsecret -f '{{.SecretData}}' "$SECRET_PG_SUPERUSER_PASSWORD" \
  > "$UNIT_DIR/$SECRET_CRUDMAN_PASSWORD"

# The project's image, as the migrations need the roles its init scripts create. Probed
# over TCP: the init scripts run on a server listening only on its socket.
unit_db() {  # container name, host port
  podman run -d --rm --name "$1" -p "127.0.0.1:$2:5432" \
    -e POSTGRES_DB="$PG_DATABASE" -e POSTGRES_USER="$PG_SUPERUSER_ROLE" \
    --secret "$SECRET_PG_SUPERUSER_PASSWORD" --secret "$SECRET_CRUDMAN_PASSWORD" \
    --secret "$SECRET_SQLMESH_PASSWORD" --secret "$SECRET_GRAFANA_PASSWORD" \
    "${REGISTRY}/postgresql:${IMAGE_TAG}" >/dev/null || return 1
  for _ in $(seq 60); do
    podman exec "$1" pg_isready -q -h 127.0.0.1 -U "$PG_SUPERUSER_ROLE" -d "$PG_DATABASE" \
      && return 0
    sleep 1
  done
  echo "The unit test database $1 did not come up." >&2
  return 1
}

# A throwaway container from the crudman image, as the superuser, as Django's test runner
# needs the CREATEDB the crudman role lacks. collectstatic first: the tests render admin
# pages, and the manifest storage resolves every asset through the manifest the entrypoint
# builds at startup. UPLOADS_DIR and SFTP_DIR point into the container's own filesystem.
# The password file is mounted with the shared :z, as both runs mount it at the same time
# and SELinux would deny it to the first once the second relabelled it.
crudman_unit_tests() {  # container name, database port, extra podman arguments
  name="$1"
  port="$2"
  shift 2
  unit_db "$name-db" "$port" || return 1
  result=0
  podman run --rm --name "$name" --network host \
    -v "$UNIT_DIR/$SECRET_CRUDMAN_PASSWORD:/run/secrets/$SECRET_CRUDMAN_PASSWORD:ro,z" \
    -e SECRET_CRUDMAN_PASSWORD="$SECRET_CRUDMAN_PASSWORD" \
    -e POSTGRES_HOST=localhost -e POSTGRES_PORT="$port" \
    -e POSTGRES_USER="$PG_SUPERUSER_ROLE" -e POSTGRES_DB="$PG_DATABASE" \
    -e DB_USER_PREFIX="$DB_USER_PREFIX" \
    -e ROLE_PREFIX="$ROLE_PREFIX" \
    -e UPLOADS_DIR=/tmp/uploads -e SFTP_DIR=/tmp/sftp \
    "$@" \
    --entrypoint sh "${REGISTRY}/crudman:${IMAGE_TAG}" -c \
    'uv run --project /crudman python manage.py collectstatic --noinput >/dev/null \
     && uv run --project /crudman python manage.py test --noinput' || result=$?
  # Not left to the teardown, so it does not compete with the integration suite.
  podman rm -f -v "$name-db" >/dev/null 2>&1 || true
  return "$result"
}

# The notebook translation, from the jupyter image: it is what stands between a model file
# and being rewritten the first time somebody opens it, and it runs against the models this
# release ships.
notebook_unit_tests() {
  podman run --rm --name "${APP_NAME}-notebook-unit" \
    -v "${CHECKOUT}/jupyter/tests:/jupyter/tests:ro,Z" \
    -v "${CHECKOUT}/sqlmesh/models:/sqlmesh/models:ro,Z" \
    -e TEST_MODELS_DIR=/sqlmesh/models \
    --entrypoint sh "${REGISTRY}/jupyter:${IMAGE_TAG}" -c \
    'python -m pytest /jupyter/tests -q -p no:cacheprovider'
}

unit_jobs=""
unit_job() {  # label, command...
  label="$1"
  shift
  "$@" >"$UNIT_DIR/$label.log" 2>&1 &
  unit_jobs="$unit_jobs $label:$!"
}
echo "Starting the unit tests in the background ..."
unit_job crudman crudman_unit_tests "${APP_NAME}-unit" 15433
# Again with single sign-on configured, which changes the installed apps and the URLs, so
# the settings module needs covering as its own configuration. The adapter cannot even be
# imported without it, so its tests skip themselves in the run above.
unit_job crudman-sso crudman_unit_tests "${APP_NAME}-unit-sso" 15434 \
  -e OIDC_ENABLED=true \
  -e OIDC_ISSUER="$OIDC_ISSUER" \
  -e OIDC_CLIENT_ID="$OIDC_CLIENT_ID"
unit_job notebook notebook_unit_tests

systemctl --user daemon-reload
# Starting the proxy pulls in the rest of the pod, but every unit is started explicitly so
# a failure surfaces rather than being masked. Each start blocks on the healthcheck
# (Notify=healthy), which is the long, silent pause.
echo "Starting the test stack and waiting for every service to become healthy ..."
stack_start="$(date +%s)"
# All queued at once, so units not ordered after one another start together; the loop's
# start then waits for the queued job instead of adding one.
systemctl --user start --no-block $(printf '%s.service ' $UNITS)
for u in $UNITS; do
  # The label goes out unterminated so it shows while the start blocks, and the result
  # completes the line. No \r: it cannot shorten a printed line, leaving a tail behind.
  printf '  %-12s ' "$u"
  systemctl --user start "${u}.service"
  printf 'healthy (%ss)\n' "$(( $(date +%s) - stack_start ))"
done
# Queued at once, a unit that failed and came back by Restart=always before the loop reached
# it looks healthy there. podman's own count stays 0, as systemd recreates the container.
for u in $UNITS; do
  restarts="$(systemctl --user show -p NRestarts --value "${u}.service")"
  if [ "$restarts" != "0" ]; then
    echo "$u failed and was restarted $restarts time(s) while the stack came up." >&2
    journalctl --user -u "${u}.service" -n 50 --no-pager >&2 || true
    exit 1
  fi
done

# --- the stand-in identity provider ---------------------------------------------------
# A throwaway OpenID Connect server in the pod, so a sign-in can be tested end to end.
# interactiveLogin false answers the authorization request with the redirect instead of a
# form. "roles" is where an Entra ID app role arrives, and Editor is the middle rank, so
# both granted and withheld permissions can be asserted. The mapping is keyed on grant_type,
# as it is matched against the token request; other keys silently drop the claims.
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
export TEST_PG_SUPERUSER_PASSWORD="$PG_SUPERUSER_PASSWORD"
export TEST_CRUDMAN_PASSWORD="$CRUDMAN_PASSWORD"
export TEST_SQLMESH_PASSWORD="$SQLMESH_PASSWORD"

# The server-statistics schema name and the path of the collector the suite triggers.
export TEST_SERVER_STATS_SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}"
export TEST_COLLECTOR="$APP_CONFIG_DIR/serverstats/collect.sh"

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
echo "Running the integration test suite ..."
suite_status=0
uv run --project tests pytest tests/ -v "$@" || suite_status=$?

# The unit tests, long finished by now as a rule; a failure's whole log is shown.
echo "Unit tests:"
for job in $unit_jobs; do
  label="${job%%:*}"
  if wait "${job#*:}"; then
    echo "  $label passed"
  else
    cat "$UNIT_DIR/$label.log"
    echo "  $label FAILED"
    suite_status=1
  fi
done
exit "$suite_status"
