#!/bin/sh
# The image build, shared by build.sh (release, docker), dev.sh and run-tests.sh (podman).
# Sourced, not executed, after buildtime.env is in the environment:
#
#   . ./build-lib.sh
#   render_build_templates
#   for svc in $SERVICES; do build_image podman "$svc"; done
#
# Every --build-arg below is a buildtime.env setting the image bakes in, and one left out
# falls back to the Dockerfile's ARG default, building an image the rest of the stack
# disagrees with. Keeping the list here means one place to add the next one.
#
# What differs between the callers stays with them: the engine, and what to do with the
# output.

# The services with a Dockerfile. Each builds independently, so the order is arbitrary.
SERVICES="postgresql crudman sqlmesh proxy grafana grafana_mcp"

# The templated parts of the two images whose Dockerfiles COPY them in: Grafana's dashboard
# JSON, which Grafana cannot interpolate, and the psql init scripts, whose role names sit
# inside function bodies. Deterministic, so an unchanged dashboard keeps its layer cached.
render_build_templates() {
  ./grafana/render.sh grafana/.render
  ./postgresql/render.sh postgresql/.initdb
}

# Tagged REGISTRY/<svc>:IMAGE_TAG to match the quadlets' Image= lines. The proxy settings
# go only to docker: podman copies them from its own environment, docker does not.
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
    --build-arg "GRAFANA_MCP_TOOLS=${GRAFANA_MCP_TOOLS}"
  if [ "$engine" = "docker" ]; then
    set -- "$@" \
      --build-arg "http_proxy=${HTTP_PROXY}" \
      --build-arg "https_proxy=${HTTPS_PROXY}" \
      --build-arg "no_proxy=${NO_PROXY}"
  fi
  "$engine" build "$@" -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "${svc}/Dockerfile" .
}

# --- podman secrets ---------------------------------------------------------------------
# The stack does not start without them: a quadlet whose Secret= names a missing secret
# refuses to run. Only what is absent is created -- an existing secret may hold the
# credentials of a database volume being reused, and rotating crudman_password would lock
# the app out of it.
#
# Shared by dev.sh and run-tests.sh so the set cannot drift between them; install.sh keeps
# its own copy, being shipped standalone as a release asset with no build-lib.sh beside it.
# The superuser password is each caller's own business -- install.sh prompts for it, the
# other two take the build-time default -- so it is not created here.
create_secret() {  # name, value
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}

create_service_secrets() {
  create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
  create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
  create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
  create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"
  # A placeholder for the single sign-on a development or test stack leaves off. It still
  # has to exist: the crudman and grafana quadlets name it in a Secret=.
  create_secret "$SECRET_OIDC_CLIENT" "unconfigured"
}

# --- running the stack from the quadlets ------------------------------------------------
# dev.sh runs the stack with plain `podman run` rather than through systemd, so it works on
# a machine without a user manager and never touches the deployment in
# ~/.config/containers/systemd. What it must not do is state the wiring a second time, since
# a copy of the quadlets drifts silently.
#
# So quadlet_run_args translates one *.container file into the `podman run` arguments that
# mean the same thing, and the caller supplies only what a development stack genuinely does
# differently. An unknown key aborts rather than being dropped, so a quadlet gaining a
# setting cannot silently fail to reach the dev stack.

# Expand ${VAR} as systemd expands the quadlet, through the shell rather than envsubst so
# this needs no gettext.
quadlet_expand() {
  eval "printf '%s' \"$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/`/\\`/g')\""
}

# Printed one per line, so the caller can read them into "$@" without a splitting rule.
quadlet_run_args() {  # path to a *.container file
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*|'['*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    # The only systemd specifier these files use.
    value="$(quadlet_expand "$value")"
    value="$(printf '%s' "$value" | sed "s|%h|${HOME}|g")"
    case "$key" in
      ContainerName)   printf '%s\n--name\n%s\n' "--replace" "$value" ;;
      Image)           echo "$value" > "$_quadlet_image" ;;
      Exec)            printf '%s\n' $value > "$_quadlet_exec" ;;
      Environment)     printf -- '-e\n%s\n' "$value" ;;
      Secret)          printf -- '--secret\n%s\n' "$value" ;;
      # A quadlet names its volume by unit file; podman wants the VolumeName= the
      # *.volume unit declares, identical here by convention. A host path is passed on.
      Volume)          printf -- '-v\n%s\n' "$(printf '%s' "$value" | sed 's/\.volume:/:/')" ;;
      HealthCmd)       printf -- '--health-cmd\n%s\n' "$value" ;;
      HealthInterval)  printf -- '--health-interval\n%s\n' "$value" ;;
      HealthTimeout)   printf -- '--health-timeout\n%s\n' "$value" ;;
      HealthRetries)   printf -- '--health-retries\n%s\n' "$value" ;;
      HealthStartPeriod) printf -- '--health-start-period\n%s\n' "$value" ;;
      RunInit)         [ "$value" = "true" ] && printf -- '--init\n' ;;
      Timezone)        printf -- '--tz=%s\n' "$value" ;;
      Restart)         printf -- '--restart\n%s\n' "$value" ;;
      # EnvironmentFile names the deployment's runtime.env, which dev.sh replaces with -e.
      # The startup healthcheck is only a faster first poll, and Notify=healthy needs
      # systemd, so neither has a `podman run` counterpart. The rest is systemd's own.
      EnvironmentFile|Notify|HealthStartupCmd|HealthStartupInterval|HealthStartupTimeout) ;;
      Pod|Description|After|WantedBy|ExecStartPre) ;;
      *) echo "quadlet_run_args: unhandled key '$key' in $1" >&2; return 1 ;;
    esac
  done < "$1"
}

# Extra arguments come after the quadlet's own, so a development override wins.
run_quadlet() {  # service name, then any extra podman run arguments
  svc="$1"; shift
  _quadlet_image="$(mktemp)"; _quadlet_exec="$(mktemp)"
  args="$(quadlet_run_args "quadlets/${svc}.container")" || return 1
  # Quadlet arguments, caller overrides, then image and command.
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
# dollar-quoted function bodies. Both use envsubst with an explicit allowlist, so every
# other $-token (nginx's $host, Grafana's $__file{}, SQL's $$ quoting) survives.
#
# The allowlist doubles as the list of settings that must be present: rendering with one
# empty produces a file that fails at first start, so a missing value aborts the build. The
# _rt prefix stands in for the `local` POSIX sh lacks, grafana/render.sh calling this twice
# with an $out of its own.
render_tree() {  # allowlist, output dir, source, then optional -name patterns to skip
  _rt_vars="$1"; _rt_out="$2"; _rt_src="$3"; shift 3

  for _rt_name in $(printf '%s' "$_rt_vars" | tr -d '${}' ); do
    eval "_rt_value=\${$_rt_name-}"
    [ -n "$_rt_value" ] || {
      echo "$_rt_name must be set (source buildtime.env first)" >&2
      return 1
    }
  done

  # Only a tree replaces a whole directory, so two renders into one output directory do
  # not delete each other.
  if [ -f "$_rt_src" ]; then
    mkdir -p "$(dirname "$_rt_out")"
    envsubst "$_rt_vars" < "$_rt_src" > "$_rt_out"
    return
  fi
  rm -rf "$_rt_out"
  mkdir -p "$_rt_out"
  # The layout first, so a nested tree arrives in the shape the consumer expects.
  find "$_rt_src" -type d | while read -r d; do mkdir -p "$_rt_out/${d#"$_rt_src"}"; done
  find "$_rt_src" -type f "$@" | while read -r f; do
    envsubst "$_rt_vars" < "$f" > "$_rt_out/${f#"$_rt_src"}"
  done
}
