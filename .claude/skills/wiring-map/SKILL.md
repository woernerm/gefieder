---
name: wiring-map
description: Fan-out map for this repo — where a change tends to have a second home. Worth a look before adding or renaming an env var, container, volume, podman secret, port, database user or initdb script, and before calling such a change done.
---

# Wiring map

The same wiring is spelled out in several places on purpose, and nothing fails loudly when
one copy is missed: a forgotten `--build-arg` falls back to the Dockerfile's `ARG` default,
a forgotten envsubst token renders as empty text, a forgotten `dev.sh` line diverges from
production only in dev.

Each row below names a file and the mechanism that ties it to the change. The mechanism is
the point: where it does not apply to what you are doing, the row does not either.

**The one worth remembering:** `dev.sh` does not read the quadlets. It hand-writes the same
wiring as `podman run` flags, so a quadlet change usually has a twin there.

## Build-time setting (`buildtime.env`)

| Also touch | Because |
|---|---|
| `buildtime.env` | it is where the value is declared, with a comment saying why it is build-time rather than runtime |
| `<svc>/Dockerfile` | an image sees a value only if it arrives as an `ARG` — and declaring one is what brings the next two rows into play |
| `build.sh` | docker forwards nothing from its environment, so a missing `--build-arg` ships the `ARG` default in the release image |
| `run-tests.sh`, `dev.sh` | the same, except for the `*_proxy` values podman copies from its own environment |
| `VARS=` in `run-tests.sh` and in `.github/workflows/publish.yml` | envsubst substitutes only the tokens in the allowlist; an unlisted `${TOKEN}` in a quadlet or serverstats unit renders empty. Two separate copies of the list |
| `VARS=` in `grafana/render.sh` | Grafana never expands `${}` inside dashboard JSON, so the render step is the only chance the value gets |
| `VARS=` in `postgresql/render.sh` | psql expands nothing inside a plpgsql function body, so the render step is the only chance there too. Both render scripts are called from all three builders (`build.sh`, `dev.sh`, `run-tests.sh`) |
| the `manifest.env` block in `publish.yml` | `install.sh` runs from a release without a checkout; `manifest.env` is all it learns about the build |
| `envsubst '${REPO} ${TEMPDIR}'` in `publish.yml` | those two are needed before `manifest.env` has been downloaded, so they are baked into the installer instead |

`tests/test_build_args.py` covers the three builders: every `ARG` a Dockerfile declares whose
name also appears in `buildtime.env` has to be passed by all of them.

## Runtime setting (`runtime.env`)

| Also touch | Because |
|---|---|
| `runtime.env` | it is the file the operator edits; the scripts source it as shell, so a value cannot contain a space or an `&` |
| the `# --- runtime configuration ---` block in `install.sh` | a reinstall keeps the file already on the host, so a setting this release adds arrives only if that block appends it |
| the consuming quadlet | a container without `EnvironmentFile=` sees nothing of `runtime.env` — `postgresql` and `sqlmesh` are the two |
| `dev.sh` | it reads no env file; every value reaches a container as an explicit `-e` |
| `run-tests.sh` | it writes `SERVER_NAME`, `DEBUG` and the `OIDC_*` settings itself and copies the rest of `runtime.env` through — so only a setting the test profile needs a value of its own for |
| `crudman/app/crudman/settings.py`, `grafana/custom.ini` | Django reads `os.environ`, Grafana reads `$__env{}`; a value neither names is inert |
| `README.md` | an operator who has to set it has to read about it |

## Container

`quadlets/<svc>.container` (`Pod=main.pod`, `[Install] WantedBy=default.target`) is the
definition. Its twins: the `podman run` block in `dev.sh`, `IMAGES=`, `QUADLETS=` and
`UNITS=` in `install.sh`, `UNITS=` in `run-tests.sh`, and `CONTAINERS` and `LOGGING_UNITS`
in `tests/conftest.py`.

The four build loops — `build.sh`, `SERVICES=` in `dev.sh`, the loop in `run-tests.sh`, the
`docker save` loop in `publish.yml` — concern a service only if it ships an image of its own.
`sftp` and `flight` run the crudman image in a different role and appear in none of them.

`uninstall.sh` derives its unit list from the quadlet directory, so it needs nothing.

## Volume

`quadlets/<name>_data.volume` carries the `VolumeName=`. `QUADLETS=` in `install.sh` ships
the file, and the `VOLUMES=` in its `# --- create the volumes up front ---` block is a
hardcoded list, as is the volume loop in `dev.sh` — both create the directories up front so
the rootless user owns them. Quadlet mounts carry `:z`; the `-v` flags in `dev.sh` do not.
`uninstall.sh` reads `VolumeName=` back out of the quadlets.

## Podman secret

`create_secret` appears in `install.sh`, `dev.sh` and `run-tests.sh`, and the list in
`# --- podman secrets ---` of `uninstall.sh` is hardcoded — it offers to delete only the
secrets `install.sh` creates. A quadlet naming it needs `Secret=`, and the `dev.sh` twin of
that quadlet needs `--secret`. `tests/test_secrets.py` is where the coverage lives.

Podman refuses to start a container whose `Secret=` names something that does not exist,
which is why a secret nobody has configured yet gets a placeholder value the way
`oidc_client_secret` does. Credentials are secrets, never files in a volume.

## Port

`PublishPort` in `quadlets/main.pod` is literal — quadlet expands no variables there. The
preflight list in `# --- preflight: the published ports ---` of `install.sh` checks each one
against the unprivileged-port floor, other listeners and the firewall; `run-tests.sh` moves
them out of the way for its throwaway stack, `dev.sh` publishes its own, and `README.md`
tells the operator which to open.

The dropzone endpoints listen on whatever `SFTP_PORT` and `FLIGHT_PORT` say in
`crudman.container`, and only there: the same variables build the address a dropzone's admin
page shows, while the sftp and flight healthchecks probe 2222 and 8815 literally.

## Database user, role or schema

`postgresql/initdb/gf_000N_*.{sh,sql}` runs in filename order, so a script granting on a
schema needs a higher number than the one creating it. A new role's password is a secret
(above), its connection belongs in the `tests/conftest.py` fixtures, and its boundary is
what `tests/test_access_control.py` and `tests/test_db_users.py` assert.

The init scripts are templates, not the files that reach the image: `postgresql/render.sh`
substitutes the role names from `buildtime.env` (`CRUDMAN_DB_USER`, `SQLMESH_DB_USER`,
`GRAFANA_DB_USER`, `DB_ROLE_PREFIX`) into `postgresql/.initdb/`, which the Dockerfile COPYs.
The medallion schemas ride along: `BRONZE_SCHEMA_PREFIX`, `SILVER_SCHEMA`, `GOLD_SCHEMA`.
The silver staging layer is not among them — nothing outside the SQLMesh models names it, so
`tests/conftest.py` derives it from `SILVER_SCHEMA`. So a role or
schema name is written once there and never spelled out again — in the quadlet that connects
as it (`POSTGRES_USER=`, and its `dev.sh` twin), the Grafana data source, the `dbusers` role
derivation, `tenants/utils.py`'s tenant discovery, `sqlmesh/config.py`, or the tests. A schema, a container and a
podman secret keep the component's name instead, so `crudman_password` does not move when
`CRUDMAN_DB_USER` does. `tests/test_render_templates.py` guards both allowlists: an
unlisted `${TOKEN}` renders as literal text rather than failing.

The SQLMesh models under `sqlmesh/models/` are the one place that cannot follow, because a
model name is parsed by SQLMesh, which never reads `buildtime.env`. Renaming a layer means
renaming it there too; `tests/test_medallion_schemas.py` fails when the two disagree.

An event trigger matching a configured prefix uses `starts_with()`, not `LIKE` — a name
ending in `_` would otherwise be read as a single-character wildcard.

## Identity-provider rank

The three ranks (`viewer`, `editor`, `admin`) are named once, in `sso.roles.RANKS`, and each
system puts its own prefix in front: `SSO_GROUP_PREFIX` makes the Django group that carries
the permissions, `DB_ROLE_PREFIX` makes the database group role `gf_0008` creates. Both
prefixes are in `buildtime.env`; `crudman.container` (and its `dev.sh` twin) passes them in.
`dbusers/utils.py` is where the two meet, and it re-lists neither.

`GROUP_ACTIONS` in `sso/roles.py` is what a rank may do, so adding a rank is an edit there
and in `gf_0008` — not a configuration change.

## Documentation

`README.md` addresses someone *running* the system: novice level, no technical details. The
cheat sheet `install.sh` prints at the end tells the same story, so the two tend to move
together. `CLAUDE.md` (root, `crudman/`, `sqlmesh/`) and `.github/copilot-instructions.md`
describe the repo to agents. The `requirements.md` files (in `quadlets/` and in
`crudman/app/{dropzones,tenants,dbusers}/`) state what a component must do and why — they
follow a changed requirement, not a changed implementation.

## Before calling it done

`./run-tests.sh`. And when a change creates a *new* place that has to be kept in sync, a
guard test beside `tests/test_build_args.py` outlasts a note telling the next reader to look.
