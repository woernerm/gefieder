#!/bin/sh
# Bring up a throwaway stack from the quadlets, run the integration test suite
# against it and tear it down again. Meant to run in isolation, far from any production
# system: it builds fresh images, starts a stack with an empty database under the project
# pod name and isolated ports, and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
set -e

PROFILE="${1:-dev}"
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
create_secret django_secret_key "$(openssl rand -hex 32)"
create_secret crudman_password  "$(openssl rand -hex 32)"
create_secret sqlmesh_password  "$(openssl rand -hex 32)"
create_secret grafana_password  "$(openssl rand -hex 32)"
# The superuser gets the well-known build-time default rather than a random value, so the
# tests run unattended without a prompt (install.sh asks for it interactively).
create_secret superuser_password "$SUPERUSER_DEFAULT_PASSWORD"

# Isolated host ports so a running stack on the default ports is not disturbed.
HTTP_PORT=18080
HTTPS_PORT=18443
GRAFANA_PORT=13000
PG_PORT=15432
SFTP_PORT=12222
FLIGHT_PORT=18815

# The production profile writes its throwaway certificate into the directory the proxy
# quadlet mounts, before any container exists to resolve the path. CERTIFICATE_PATH is
# written for systemd, so "%h" is spelled as $HOME here; a path the default does not use
# would need its own translation, which is fine for a test script but is the reason the
# installer never does this.
CERT_DIR="$(printf '%s' "${CERTIFICATE_PATH}" | sed "s|^%h|${HOME}|")"
mkdir -p "$CERT_DIR"

# Build the custom images with podman, the engine that runs the quadlets, so the suite
# exercises exactly what a deployment runs. (build.sh builds with docker for the release
# workflow; docker and podman keep separate image stores, so a docker build would not be
# visible to the podman-run stack here.) Tagged REGISTRY/<svc>:IMAGE_TAG to match the
# Image= lines in the quadlets; built from the working tree, not pulled.
# Render the Grafana provisioning templates the grafana Dockerfile COPYs in first, as
# build.sh/dev.sh do; otherwise the COPY of grafana/.provisioning/ has no source.
./grafana/render.sh grafana/.provisioning
for svc in postgresql crudman sqlmesh proxy grafana; do
  podman build \
    --build-arg "DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}" \
    -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "${svc}/Dockerfile" .
done

# The suite connects to the database as each role to check its access boundary; the
# passwords come from the podman secrets created above.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' grafana_password)"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' superuser_password)"
CRUDMAN_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' crudman_password)"
SQLMESH_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' sqlmesh_password)"

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
  # Stop the whole stack in one go rather than unit by unit. Every container carries
  # Restart=always, so stopping them individually makes systemd restart the ones whose
  # dependencies just went away -- stopping postgresql first fails the healthchecks of
  # everything behind it, and those units queue an auto-restart while this loop is still
  # trying to shut the stack down. Stopping main-pod.service takes the pod and all its
  # containers down together, and the per-unit stops below then only confirm what is
  # already gone.
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
VARS='${REGISTRY} ${IMAGE_TAG} ${APP_NAME} ${SUPERUSER_NAME} ${SUPERUSER_EMAIL} ${CRUDMAN_PATH} ${GRAFANA_PATH} ${CERTIFICATE_PATH} ${SERVER_STATS_INTERVAL}'
for f in quadlets/*; do
  envsubst "$VARS" < "$f" > "$QUADLET_DIR/$(basename "$f")"
done

# Quadlet does not expand variables in PublishPort, so overwrite the rendered pod file
# with the isolated test ports. The test pod also publishes the database and Grafana
# ports (the production pod publishes only 80/443) so the suite reaches them on
# localhost directly. PodName stays ${APP_NAME} so podman shows the project name.
cat > "$QUADLET_DIR/main.pod" <<EOF
[Pod]
PodName=${APP_NAME}
PublishPort=${HTTP_PORT}:80
PublishPort=${HTTPS_PORT}:443
PublishPort=${PG_PORT}:5432
PublishPort=${GRAFANA_PORT}:3000
PublishPort=${SFTP_PORT}:2222
PublishPort=${FLIGHT_PORT}:8815

[Install]
WantedBy=default.target
EOF

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
# The quadlets read SERVER_NAME and DEBUG from here (EnvironmentFile=), so write the
# profile's values rather than copying the repository defaults, which would put the
# production profile back into DEBUG mode. Everything else is copied over unchanged; the
# "|| true" keeps a repository file without those keys from aborting the run under set -e.
{
  echo "SERVER_NAME=${SERVER_NAME}"
  echo "DEBUG=${DEBUG}"
  grep -v -e '^SERVER_NAME=' -e '^DEBUG=' runtime.env || true
} > "$APP_CONFIG_DIR/runtime.env"

cleanup() {
  # Runs on EXIT after pytest prints its summary: stops the services, removes the pod and
  # deletes the throwaway volumes. The teardown is silenced, so announce it -- this is the
  # pause between the test result and the prompt returning.
  echo "Tearing down the test stack ..."
  remove_deployment
  rm -f "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem"
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

export TEST_PROFILE="$PROFILE"
export TEST_BASE_URL="$SCHEME://localhost:$APP_PORT"
export TEST_HTTP_BASE_URL="http://localhost:$HTTP_PORT"
export TEST_PG_PORT="$PG_PORT"
export TEST_SFTP_PORT="$SFTP_PORT"
export TEST_FLIGHT_PORT="$FLIGHT_PORT"
export TEST_GRAFANA_PASSWORD="$GRAFANA_PASSWORD"
export TEST_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD"
export TEST_CRUDMAN_PASSWORD="$CRUDMAN_PASSWORD"
export TEST_SQLMESH_PASSWORD="$SQLMESH_PASSWORD"

# The server-statistics schema name and the path of the collector the suite triggers.
export TEST_SERVER_STATS_SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}"
export TEST_COLLECTOR="$APP_CONFIG_DIR/serverstats/collect.sh"

# The crudman unit tests (crudman/app/*/tests.py) cover the application logic the
# integration suite only sees from the outside: the upload pipeline, the check/convert
# registry, the validity clipping and the SFTP session handling. They run first, in a
# throwaway container from the crudman image, so a broken pipeline fails here rather
# than as a puzzling HTTP result later.
#
# Run from the image rather than a host virtualenv so the dependencies are exactly the
# ones the deployment ships (a stale host venv would test something nobody runs).
# Django's test runner creates and drops its own test_<db> database, so it needs a role
# with CREATEDB: the deployed crudman role deliberately has none, hence the connection
# as the database superuser. Its password is mounted where settings.py reads the
# database password from, the only way in (settings.py takes it from the secret file,
# not from the environment).
echo "Running the crudman unit tests ..."
UNIT_SECRET_DIR="$(make_tempdir)"
podman secret inspect --showsecret -f '{{.SecretData}}' superuser_password \
  > "$UNIT_SECRET_DIR/crudman_password"
# collectstatic first: the tests render admin pages, and the manifest static files
# storage resolves every asset through the manifest the entrypoint builds at startup —
# a fresh container has none. UPLOADS_DIR/SFTP_DIR point into the container's own
# filesystem, which dies with it, so no test writes anywhere persistent.
podman run --rm --network host \
  -v "$UNIT_SECRET_DIR/crudman_password:/run/secrets/crudman_password:ro,Z" \
  -e POSTGRES_HOST=localhost -e POSTGRES_PORT="$PG_PORT" \
  -e POSTGRES_USER="$SUPERUSER_NAME" -e POSTGRES_DB=postgres \
  -e UPLOADS_DIR=/tmp/uploads -e SFTP_DIR=/tmp/sftp \
  --entrypoint sh "${REGISTRY}/crudman:${IMAGE_TAG}" -c \
  'uv run --project /crudman python manage.py collectstatic --noinput >/dev/null \
   && uv run --project /crudman python manage.py test --noinput'
rm -rf "$UNIT_SECRET_DIR"

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
echo "Running the integration test suite ..."
uv run --project tests pytest tests/ -v
