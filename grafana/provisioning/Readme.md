# Grafana provisioning

Grafana can configure itself from files instead of clicks. On startup it scans this folder
and applies whatever it finds, so the running instance comes up with its data source and
dashboards already in place — no manual import after a fresh install.

```
provisioning/
  datasources/
    postgresql.yaml         # the read-only PostgreSQL data source
  dashboards/
    dashboards.yaml         # tells Grafana to load every dashboard JSON in this folder
    server-monitoring.json  # a shipped dashboard (host CPU, memory, disk, storage)
```

`datasources/` and `dashboards/` are the folder names Grafana looks for; keep them.

## These files are templates

The whole folder is baked into the image, but not verbatim: `grafana/render.sh` renders it
first (run by `build.sh`, `dev.sh` and `run-tests.sh`), substituting `${APP_NAME}` and
`${SERVER_STATS_SCHEMA}` from `buildtime.env`. This is why the dashboard JSON can reference
the data source by its real uid and read from the configured server-stats schema even though
Grafana itself does not expand variables inside dashboard JSON. Only those two variables are
substituted; Grafana's own tokens (`$__file{...}`, `$__timeFilter`, `%(...)s`) pass through
untouched.

## Adding a dashboard

Drop a `.json` file into `dashboards/`. Build a dashboard in the Grafana UI, export its JSON
(Share → Export), and save it here. To make it project-agnostic, reference the data source as
`"${APP_NAME}-postgresql"` and the schema as `${SERVER_STATS_SCHEMA}` so `render.sh` fills in
the configured values at build time. `server-monitoring.json` is a worked example.

Edits made in the UI are kept (`allowUiUpdates` is on), but the files here are the defaults
restored whenever the image is reinstalled — treat them as the source of truth.
