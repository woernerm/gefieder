# Instructions for this repository

- Purpose: Whitelabel template for multi-tenant data analytics systems (user configures 
  `APP_NAME` in `buildtime.env`). It solves the wireing, configuration and 
  initialization of its components. It does not provide data models or metrics apart 
  from examples (since it is a template).
- Target audience: Organizations with multiple projects using an inhomogenius landscape 
  of tools (issue trackers, version control systems, ERP systems, other bespoke apps), 
  workflows and processes. Expected volume: 20 - 500 GB of low quality data. Mostly 
  historic meta data (e.g. workflow state, categories, tags, effort and cost estimates, 
  risk levels, etc.) with modification timestamps. Small amount of IOT data. Faced 
  with high reporting requirements about the development of safety critical and highly 
  legislated products.

- Deployment: Rootless podman (>5.0) quadlets to Ubuntu or Red Hat. Single install cmd:
  `curl -fsSL https://github.com/your-org/your-repo/releases/latest/install.sh | bash`
- Components: PostgreSQL, Django admin, SQLMesh, Grafana, nginx proxy, dropzone SFTP and
  Arrow Flight endpoints.
- Reachable from outside: Grafana and Django admin through the nginx proxy (80/443);
  PostgreSQL (5432), SFTP (2222) and Flight (8815) publish their own port.


## Where things are

- `crudman/` — Django admin (Unfold); apps in `app/`: tenants, dropzones, sso, dbusers,
  example.
- `sqlmesh/` — SQLMesh project: `config.py`, `macros/`, `models/{bronze,silver,gold}`.
- `postgresql/` — pgduckdb image; `initdb/gf_000N_*.{sh,sql}` for initialization.
- `grafana/` — `custom.ini`: configuration, `provisioning/`: default dashboards, 
  `render.sh`: replace template placeholders at build time.
- `proxy/` — nginx; `http.conf.template` and `https.conf.template`.
- `serverstats/` — `collect.sh` plus a systemd service and timer. Runs on target machine
  to collect server statistics, used to determine the right VM size in the cloud.
- `quadlets/` — every unit, centrally: `main.pod`, `*.container`, `*_data.volume`.
- `tests/` — the pytest integration suite; `run-tests.sh` starts a throwaway stack for it.
- `*/requirements.md` — what a component must do and why: `quadlets/`,
  `crudman/app/dropzones/`, `crudman/app/tenants/`, `crudman/app/dbusers/`.
- `build.sh` builds release images; `install.sh` and `uninstall.sh` run on target
  machine. Both distributed as assets of the GitHub Release.

# Medallion architecture
- Bronze: Raw source data, one schema per tenant. Best for urgent metric requests and
  tool-centric metrics (like error checking of source data).
- Silver: Standardized model. Independence of tools and projects. Best for 
  knowledge-domain focused metrics (e.g. project & resource planning, forecasting, agile 
  methods, management).
- Gold: Materialized metrics derived from silver. Best for long-term metrics that are 
  used across projects. Quick load times. Easy to embed in external HTML documentation.

## Checking your work

- `./dev.sh up | down | logs | serverstats` runs a local stack. Does not read quadlets.
- `./run-tests.sh [dev|production] [pytest args]` builds images, starts a throwaway
  stack, runs crudman unit tests and integration suite. **Run it before calling a
  code change done**, comment-only changes excepted. Dev: Debug on, served via http. 
  Production: Debug off, served via https.

## Conventions

- Install Python packages with uv, never pip.
- Prefer `str | None` and builtin generics, not `typing.Optional` or `typing.List`. 
- Comments explain *why* only, never *what*. If the logic is hard to understand, it may
  explain *how*. 
- Simpler code means removing code, not comments or blank lines.
- Keep changes minimal against the current version, and explain briefly what changed and
  why it was necessary.
- Services log to stdout/stderr only and add no timestamp of their own — journald stamps
  every line. Avoid writing log files to a volume. Only SQLMesh stamps its own lines, as
  its format is hardcoded; its container runs `Timezone=local` so both stamps agree.
- `buildtime.env`: Neither stored in images nor target machine (APP_NAME, proxy, mirrors, 
  extensions).
- `runtime.env`: Storage and final edits on target machine (SERVER_NAME,
  DEBUG, OIDC_*) 
- Credentials are podman secrets, never files in a volume.
- README.md addresses someone *running* the system, not developing it: concise, 
  novice user level. No technical details.
- Push to origin/main triggers `.github/workflows/publish.yml` (builds images & creates 
  GitHub release).
- Make your responses 300 words or less.