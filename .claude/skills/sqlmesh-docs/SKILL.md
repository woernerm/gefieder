---
name: sqlmesh-docs
description: SQLMesh documentation to consult before answering questions about SQLMesh.
---

# SQLMesh documentation

SQLMesh changes quickly, so check current documentation rather than answering from
memory. Fetch only the pages below that cover the question — the summaries exist so you
don't have to fetch all of them. Follow a page's own links to subpages when the summary
says the detail lives there.

Base URL for the pages below: `https://sqlmesh.readthedocs.io/en/stable/`

## Orientation

- `concepts/overview/` — **Start here for "what does SQLMesh call this".** Conceptual map
  of models, plans, environments, the virtual layer, tests vs audits. Its subpages
  (`models/*` incl. `model_kinds`, `plans/`, `environments/`, `macros/*`, `audits/`,
  `tests/`, `metrics/*`, `state/`, `glossary/`) are the reference for model syntax.
- `faq/faq/` — Short answers to the recurring questions: `plan` vs `run`, the `cron`
  parameter, start/end dates, why SQLMesh creates schemas, test vs audit, reprocessing
  data, forcing a model to run, reusing an existing table, dbt differences, and the two
  common warnings (missing schema, nesting level).
- `quick_start/` — Landing page only: links to the CLI/notebook/UI quickstarts and a
  5-minute video. The runnable walkthrough is on its `quickstart/cli/` subpage. Fetch
  only to scaffold a first DuckDB example project.
- `guides/projects/` — Thin: creating a project (virtualenv, scaffolding with `sqlmesh
  init`, the resulting folder layout), editing an existing one, and importing a dbt
  project. Most of the detail is links out to other pages.
- `examples/overview/` — Index of learning material: the incremental-time walkthrough,
  the CLI crash course, and the `sqlmesh-examples` GitHub repo of runnable DuckDB
  projects. No reference content.
- `integrations/overview/` — Two lists, nothing more: integrated tools (dbt, dlt, GitHub
  Actions, Kestra) and the ~17 supported engines with their pip extras. Engine-specific
  settings live on `integrations/engines/<name>/`.

## Configuration and deployment

- `guides/configuration/` — **The main configuration page.** How `config.yaml`/`config.py`,
  environment variables and `.env` files combine and override each other; cache
  directory, physical table naming, auto-categorization of changes, gateways,
  connections, scheduler, model defaults, `before_all`/`after_all`, linting, debug mode,
  extra Python dependencies.
- `guides/connections/` — Gateway anatomy: `connection`, `state_connection` (give state
  its own PostgreSQL; Spark and Trino cannot host it), `test_connection`, the
  `default_connection` / `default_test_connection` / `default_gateway` keys, and
  `sqlmesh --gateway <name>`.
- `guides/scheduling/#built-in-scheduler` — Very short: `sqlmesh run` evaluates whichever
  intervals are missing and then exits, so an external cron or CI must call it
  repeatedly; recommends a separate transactional state database. Tobiko Cloud is the
  alternative.
- `guides/multi_engine/` — One project across several engines: per-model `gateway`,
  shared vs gateway-managed virtual layer, and the open table formats (Iceberg, Delta,
  Hive) that make the split possible.
- `guides/isolated_systems/` — Production and development in warehouses that cannot reach
  each other: separate state, multiple gateways, gateway-specific schemas, and how to
  link systems so a plan built in one can be applied in the other.
- `guides/notifications/` — Slack (webhook or API) and email targets in the project
  config, global vs per-user, the event types they fire on, and development-only
  overrides.
- `guides/migrations/` — SQLMesh's *own* state format only: the version-mismatch errors
  and `sqlmesh migrate`, which one person runs manually and never from CI/CD. Not about
  moving data.

## Models and data loading

- `guides/models/` — Day-to-day workflow: add, edit, evaluate, preview with `plan`,
  revert, virtual update, automatic and manual validation, delete, and view the DAG.
  Model *syntax* and kinds are under `concepts/models/`.
- `guides/incremental_time/` — Deep dive on `INCREMENTAL_BY_TIME_RANGE`: how intervals
  are calculated, `@start_ds`/`@end_ds`, what `cron` and `run` actually schedule, model
  time vs wall clock, and forward-only models with their schema changes.
- `guides/model_selection/` — Selector syntax (`+model+`, `tag:`, `git:`, wildcards,
  `&`/`|`) shared by `plan --select-model` / `--backfill-model` /
  `--allow-destructive-model` / `--allow-additive-model` and by `table_diff`. Mostly
  worked examples.
- `guides/table_migration/` — Bringing an externally built table under SQLMesh: first why
  you usually should not (prefer external models), then the "stage and union" and
  "snapshot replacement" methods.

## Validation

- `guides/testing/` — Just the commands: `sqlmesh test` (whole suite,
  `file.yaml::test_name`, glob patterns) and `sqlmesh audit`, plus non-blocking audits.
  Writing tests and audits is covered under `concepts/tests/` and `concepts/audits/`.
- `guides/tablediff/` — `sqlmesh table_diff`: schema and row-level comparison of a model
  across two environments, of arbitrary tables or views, across gateways, or of many
  models at once via selectors. Both objects must already exist.
- `guides/linter/` — Lint rules checked when a plan is created: built-in and user-defined
  rules, enabling all or a specific set, excluding a model, and whether a violation warns
  or errors.

## Extending SQLMesh

- `guides/signals/` — Extra readiness criteria for the built-in scheduler: an
  `@signal`-decorated function in `signals/__init__.py` receives a batch of
  `DatetimeRanges` and returns `True`/`False` or just the ready intervals. The answer to
  data that lands late.
- `guides/custom_materializations/` — Last resort when no built-in model kind fits:
  writing a Python materialization class, extending `CustomKind`, and sharing it by
  copying files or as a Python package.
- `guides/customizing_sqlmesh/` — Narrow: subclassing `SqlMeshLoader` to modify every
  model as the project is loaded.
- `guides/vscode/` — The preview VSCode extension: installation, picking the Python
  interpreter, the lineage/render/editor/command features, and troubleshooting (DuckDB
  concurrent access, environment variables, missing dependencies, version mismatch).

How this project uses SQLMesh is in `sqlmesh/CLAUDE.md`.
