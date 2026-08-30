#!/bin/sh
# The image build, shared by build.sh (release, docker), dev.sh and run-tests.sh (podman).
# Sourced, not executed, after buildtime.env is in the environment:
#
#   . ./build-lib.sh
#   render_build_templates
#   for svc in $SERVICES; do build_image podman "$svc"; done
#
# Every --build-arg below is a buildtime.env setting the image bakes in, and one left out
# falls back to the Dockerfile's ARG default -- building an image the rest of the stack
# disagrees with (SERVER_STATS_SCHEMA decides which schema the database creates). Keeping
# the list here means there is one place to add the next one.
#
# What differs between the callers stays with them: which engine, and what to do with the
# output. dev.sh hides a successful build and replays a failed one; the others let it stream.

# The services with a Dockerfile. Each builds independently, so the order is arbitrary.
SERVICES="postgresql crudman sqlmesh proxy grafana"

# Render the templated parts of the two images whose Dockerfiles COPY them in. Grafana gets
# APP_NAME and SERVER_STATS_SCHEMA baked into its dashboard JSON, which Grafana itself
# cannot interpolate; PostgreSQL gets the database role names baked into its init scripts,
# which psql cannot interpolate inside a function body. Without this the COPY of
# grafana/.render/ and postgresql/.initdb/ has no source. The output is deterministic, so an
# unchanged dashboard keeps the COPY layer cached.
render_build_templates() {
  ./grafana/render.sh grafana/.render
  ./postgresql/render.sh postgresql/.initdb
}

# Build one service image, tagged REGISTRY/<svc>:IMAGE_TAG to match the Image= lines in the
# quadlets. The proxy settings are passed only to docker: podman copies them from its own
# environment, where the caller's `set -a` put them, while docker does not.
build_image() {  # engine, service
  engine="$1"
  svc="$2"
  set -- \
    --build-arg "PYTHON_INDEX=${PYTHON_INDEX}" \
    --build-arg "DOCKER_IO_MIRROR=${DOCKER_IO_MIRROR}" \
    --build-arg "GHCR_IO_MIRROR=${GHCR_IO_MIRROR}" \
    --build-arg "SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}" \
    --build-arg "SECRET_SUPERUSER_PASSWORD=${SECRET_SUPERUSER_PASSWORD}" \
    --build-arg "DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}" \
    --build-arg "GRAFANA_PLUGINS=${GRAFANA_PLUGINS}" \
    --build-arg "ECHARTS_VERSION=${ECHARTS_VERSION}"
  if [ "$engine" = "docker" ]; then
    set -- "$@" \
      --build-arg "http_proxy=${HTTP_PROXY}" \
      --build-arg "https_proxy=${HTTPS_PROXY}" \
      --build-arg "no_proxy=${NO_PROXY}"
  fi
  "$engine" build "$@" -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "${svc}/Dockerfile" .
}

# --- running the stack from the quadlets ------------------------------------------------
# dev.sh runs the stack with plain `podman run` rather than through systemd, so that it
# works on a machine without a user manager and never touches the deployment installed in
# ~/.config/containers/systemd. What it must not do is state the wiring a second time: the
# quadlets in quadlets/ are the single description of every image, environment variable,
# secret, volume and healthcheck, and a copy of that in dev.sh drifts silently.
#
# So the quadlets are read instead. quadlet_run_args translates one *.container file into
# the `podman run` arguments that mean the same thing, and the caller supplies only what a
# development stack genuinely does differently (DEBUG, the published ports, the addresses
# that name them). The keys handled below are the ones the quadlets use; an unknown key
# aborts rather than being dropped, so a quadlet gaining a setting cannot silently fail to
# reach the dev stack.

# Expand ${VAR} references against the environment, the way systemd expands the quadlet.
# Done with a here-doc through the shell rather than envsubst so this needs no gettext.
quadlet_expand() {
  eval "printf '%s' \"$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/`/\\`/g')\""
}

# The `podman run` arguments for one quadlet, printed one per line so the caller can read
# them into "$@" without a word-splitting rule of its own.
quadlet_run_args() {  # path to a *.container file
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*|'['*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    # Quadlet resolves "%h" itself; nothing else in these files uses a specifier.
    value="$(quadlet_expand "$value")"
    value="$(printf '%s' "$value" | sed "s|%h|${HOME}|g")"
    case "$key" in
      ContainerName)   printf '%s\n--name\n%s\n' "--replace" "$value" ;;
      Image)           echo "$value" > "$_quadlet_image" ;;
      Exec)            printf '%s\n' $value > "$_quadlet_exec" ;;
      Environment)     printf -- '-e\n%s\n' "$value" ;;
      Secret)          printf -- '--secret\n%s\n' "$value" ;;
      # A quadlet names its volume by unit file (foo_data.volume); podman wants the volume
      # name, which the *.volume unit declares as VolumeName= -- identical here by
      # convention, so the ".volume" suffix is simply dropped. A host path is passed on.
      Volume)          printf -- '-v\n%s\n' "$(printf '%s' "$value" | sed 's/\.volume:/:/')" ;;
      HealthCmd)       printf -- '--health-cmd\n%s\n' "$value" ;;
      HealthInterval)  printf -- '--health-interval\n%s\n' "$value" ;;
      HealthTimeout)   printf -- '--health-timeout\n%s\n' "$value" ;;
      HealthRetries)   printf -- '--health-retries\n%s\n' "$value" ;;
      HealthStartPeriod) printf -- '--health-start-period\n%s\n' "$value" ;;
      RunInit)         [ "$value" = "true" ] && printf -- '--init\n' ;;
      Timezone)        printf -- '--tz=%s\n' "$value" ;;
      Restart)         printf -- '--restart\n%s\n' "$value" ;;
      # EnvironmentFile points at the deployment's runtime.env, which a dev stack does not
      # have; dev.sh passes the same settings with -e instead. The startup healthcheck is
      # only a faster first poll of the regular one, and Notify=healthy needs systemd, so
      # neither has a `podman run` counterpart. The rest are systemd's own bookkeeping.
      EnvironmentFile|Notify|HealthStartupCmd|HealthStartupInterval|HealthStartupTimeout) ;;
      Pod|Description|After|WantedBy|ExecStartPre) ;;
      *) echo "quadlet_run_args: unhandled key '$key' in $1" >&2; return 1 ;;
    esac
  done < "$1"
}

# Start one service from its quadlet. Extra arguments are added after the quadlet's own, so
# a development override (an -e that names a published port, say) wins over the file.
run_quadlet() {  # service name, then any extra podman run arguments
  svc="$1"; shift
  _quadlet_image="$(mktemp)"; _quadlet_exec="$(mktemp)"
  args="$(quadlet_run_args "quadlets/${svc}.container")" || return 1
  # The quadlet's arguments, then the caller's overrides, then image and command.
  OLDIFS="$IFS"; IFS='
'
  set -- --pod "$POD" $args "$@"
  IFS="$OLDIFS"
  set -- "$@" "$(cat "$_quadlet_image")" $(cat "$_quadlet_exec")
  rm -f "$_quadlet_image" "$_quadlet_exec"
  podman run -d "$@" >/dev/null
}

# --- rendering the templated build inputs -----------------------------------------------
# Two images bake settings into files their own tooling cannot interpolate: Grafana's
# dashboard JSON and configuration, and the psql init scripts, whose role names sit inside
# dollar-quoted function bodies. Both are rendered with envsubst and an explicit allowlist,
# so every other $-token (nginx's $host, Grafana's $__file{}, SQL's $$ quoting) survives.
#
# render_tree is that shared step. The allowlist doubles as the list of settings that must
# be present: rendering with one of them empty produces a file that fails at first start on
# a machine nobody is watching, so a missing value aborts the build here instead.
# The variables carry an _rt prefix because POSIX sh has no `local`: a bare "out" would
# overwrite the caller's own, and grafana/render.sh calls this twice with an $out of its own.
render_tree() {  # allowlist, output dir, source, then optional -name patterns to skip
  _rt_vars="$1"; _rt_out="$2"; _rt_src="$3"; shift 3

  for _rt_name in $(printf '%s' "$_rt_vars" | tr -d '${}' ); do
    eval "_rt_value=\${$_rt_name-}"
    [ -n "$_rt_value" ] || {
      echo "$_rt_name must be set (source buildtime.env first)" >&2
      return 1
    }
  done

  # A single source file renders to a single output file; only a tree replaces a whole
  # directory, so that two renders into one output directory (Grafana's provisioning tree
  # and its grafana.ini) do not delete each other.
  if [ -f "$_rt_src" ]; then
    mkdir -p "$(dirname "$_rt_out")"
    envsubst "$_rt_vars" < "$_rt_src" > "$_rt_out"
    return
  fi
  rm -rf "$_rt_out"
  mkdir -p "$_rt_out"
  # Recreate the directory layout first, so a nested tree (Grafana's datasources/ and
  # dashboards/) arrives in the shape the consumer expects.
  find "$_rt_src" -type d | while read -r d; do mkdir -p "$_rt_out/${d#"$_rt_src"}"; done
  find "$_rt_src" -type f "$@" | while read -r f; do
    envsubst "$_rt_vars" < "$f" > "$_rt_out/${f#"$_rt_src"}"
  done
}
