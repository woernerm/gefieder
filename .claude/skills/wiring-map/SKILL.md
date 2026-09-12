---
name: wiring-map
description: Fan-out map for this repo — where a change tends to have a second home. Worth a look before adding or renaming an env var, container, volume, podman secret, port, database user or initdb script, and before calling such a change done.
---

# Wiring map

The same wiring is spelled out in several places on purpose, and nothing fails loudly when
one copy is missed: a forgotten `--build-arg` falls back to the Dockerfile's `ARG` default,
and a forgotten envsubst token renders as empty text.

Each row below names a file and the mechanism that ties it to the change. The mechanism is
the point: where it does not apply to what you are doing, the row does not either.

**The one worth remembering:** `dev.sh` *derives* its containers from the quadlets
(`run_quadlet` in `build-lib.sh`), so a quadlet change reaches it by itself — but only for
the keys that function handles. An unhandled key aborts the run rather than being dropped
silently, which is the signal to add a case for it there.

## Build-time setting (`buildtime.env`)

| Also touch | Because |
|---|---|
| `buildtime.env` | it is where the value is declared, with a comment saying why it is build-time rather than runtime |
| `<svc>/Dockerfile` | an image sees a value only if it arrives as an `ARG` — and declaring one is what brings the next two rows into play |
| `build_image` in `build-lib.sh` | the one `--build-arg` list, shared by all three builders; docker forwards nothing from its environment, so a missing argument ships the `ARG` default in the release image. The `*_proxy` values are passed only to docker, podman copying them from its own environment |
| `VARS=` in `run-tests.sh` and in `.github/workflows/publish.yml` | envsubst substitutes only the tokens in the allowlist; an unlisted `${TOKEN}` in a quadlet or serverstats unit renders empty. Two separate copies of the list |
| `VARS=` in `grafana/render.sh` | Grafana never expands `${}` inside dashboard JSON or its own config file, so the render step is the only chance the value gets. It renders two sources: `provisioning/` and `custom.ini` |
| `VARS=` in `postgresql/render.sh` | psql expands nothing inside a plpgsql function body, so the render step is the only chance there too. Both scripts share `render_tree` in `build-lib.sh`, which also refuses to render with any listed value empty; all three builders call them |
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
| `dev.sh` | it reads no env file: the quadlet's `EnvironmentFile=` is skipped, so a value the dev stack needs reaches the container as an explicit `-e` override there |
| `run-tests.sh` | it writes `SERVER_NAME`, `DEBUG` and the `OIDC_*` settings itself and copies the rest of `runtime.env` through — so only a setting the test profile needs a value of its own for |
| `crudman/app/crudman/settings.py`, `grafana/custom.ini` | Django reads `os.environ`, Grafana reads `$__env{}`; a value neither names is inert |
| `README.md` | an operator who has to set it has to read about it |

## Container

`quadlets/<svc>.container` (`Pod=main.pod`, `[Install] WantedBy=default.target`) is the
definition, and `dev.sh` reads it rather than restating it. Its twins: `IMAGES=`,
`QUADLETS=` and `UNITS=` in `install.sh`, `UNITS=` in `run-tests.sh`, and `CONTAINERS` and
`LOGGING_UNITS` in `tests/conftest.py`. A new container also needs its `run_quadlet` call in
`dev.sh`, with whatever development override it needs and nothing more.

The three build loops — `SERVICES=` in `build-lib.sh` (used by `build.sh`, `dev.sh` and
`run-tests.sh`) and the `docker save` loop in `publish.yml` — concern a service only if it
ships an image of its own.
`sftp` and `flight` run the crudman image in a different role and appear in none of them.

## Notebooks and Grafana sign-in

Neither the `jupyter` container nor Grafana has accounts of its own: both ask crudman who
a visitor is (`crudman/app/notebooks/`). The hub asks `whoami` itself and crudman rotates
the database login its servers connect with; for Grafana the proxy asks `grafana` as an
nginx `auth_request` before every Grafana request and passes the answer on in
`X-WEBAUTH-*` headers, which Grafana's `[auth.proxy]` trusts from the loopback. So the
three move together.

| Also touch | Because |
|---|---|
| `crudman/app/notebooks/views.py` | the hub reads exactly these fields out of `whoami`'s answer, so a renamed key is a spawn that fails with a KeyError; Grafana reads exactly these headers out of `grafana`'s |
| `jupyter/crudman.py` | the other half of the hub's contract, and the only place the session cookie is presented back |
| `proxy/locations.conf.template` | the other half of Grafana's: the `auth_request` location, the `auth_request_set`/`proxy_set_header` pairs that carry each header, the nested `auth-tokens/rotate` location that sends a rotation Grafana refuses (a stale token beside a good admin session) through `/login` rather than letting the frontend reload forever, and the `X-WEBAUTH-USER ""` on the MCP location, which forwards a caller's headers to Grafana from the same loopback Grafana trusts |
| `grafana/custom.ini` `[auth.proxy]` | `header_name` and `headers` name the same headers; `whitelist` is why only the proxy may set them; `enable_login_token` stays on, and the proxy's sign-in redirect leads through `/login` because that route alone issues the token Grafana's frontend then rotates on every page -- without it the page reloads forever |
| `tests/test_grafana_auth.py` | asserts the chain end to end, the forgery guards included |
| `postgresql/initdb/gf_0003_*.sql` | `issue_db_user_password` is what a spawn calls: it puts an expiring password on the person's *own* role, so a notebook session simply is them. `crudman/app/dbusers/views.py` calls it too, for a developer's checkout |
| `jupyter/spawn.py` | the Unix account is named as `dbusers.utils.role_name_for` names the database role — that is what lets `sqlmesh/config.py` derive the connection unchanged. A change to either derivation is a change to both |
| `jupyter/requirements.txt` | what the workflow itself depends on; `JUPYTER_EXTENSIONS` in `buildtime.env` is the operator's list and a **build-time setting**, so that table applies |
| `jupyter/tests/` | run inside the image by `run-tests.sh`, against `sqlmesh/models/` — the round trip that keeps opening a model from rewriting it |

`NOTEBOOK_PATH` is a **build-time setting** and reaches three places: the proxy (template,
`entrypoint.sh` envsubst list, `proxy.container`), the hub's `base_url`, and
`crudman.container`, which passes it to Django only so the bar can link to it.
`GRAFANA_PATH` reaches `crudman.container` for the same reason alone.

## The shell

Every page a browser asks for by address is crudman's to answer, whichever app the path
names: the proxy tells that request (`Sec-Fetch-Dest: document`) from the one the shell's
frame then makes for the same path (`iframe`) and sends it to port 8000, where
`crudman/app/shell/middleware.py` answers with the bar and the frame. So the apps never
know they are framed, and each has to allow being framed from its own origin.

| Also touch | Because |
|---|---|
| `proxy/maps.conf.template` | the `$grafana_port` and `$notebook_port` maps; a new app behind the bar needs one, and its location a `proxy_pass` through it |
| `crudman/app/shell/middleware.py` | `is_page` is the other half of that classification; it also refuses a request without `X-Forwarded-For`, which is how the proxy's Grafana identity subrequest (headers copied from the browser's) keeps answering with the identity rather than a page |
| `crudman/app/shell/stages.py` | the stages, where each leads, who may enter it, and which admin apps belong to it -- the sidebar (`templates/unfold/helpers/navigation.html`, via `templatetags/shell.py`) shows the apps of the stage the page is under. A new admin app a rank should reach is named here as well as in `MANAGED_APPS` |
| `X_FRAME_OPTIONS` in `settings.py`, `allow_embedding` in `grafana/custom.ini`, `tornado_settings` in both `jupyter/jupyterhub_config.py` and `jupyter/jupyter_server_config.py` | Django's, Grafana's, the hub's and (under the hub) a notebook server's defaults all refuse every frame |
| `sso/views.py` `login` | asked for inside the frame, hands itself to the window: the provider refuses to be framed |
| `crudman/app/templates/unfold/helpers/navigation.html`, `docs/templates/docs/navigation.html` | draw the sidebar's user menu only outside a frame, the bar carrying it inside one. Copies of Unfold's template; an Unfold upgrade that changes it needs them re-based |
| `tests/test_shell.py`, `tests/test_proxy_config.py` | the chain end to end, and the proxy's classification on its own |

`uninstall.sh` derives its unit list from the quadlet directory, so it needs nothing.

## Models repository

The SQLMesh project is not in an image. `crudman/app/system/repo.py` clones
`REPO_MODELS` onto the `models_data` volume, checks a commit out under `deployed/` and
writes the sha to `deployed.sha`; `sqlmesh/entrypoint.sh` watches that one file. So the
handoff between the two containers is a path and a marker, not an API.

| Also touch | Because |
|---|---|
| `crudman/Dockerfile` | `/seed` is what a repository is created from, and the container-side tools are stripped from it there — a file added to `sqlmesh/` reaches a developer's clone through this COPY |
| `sqlmesh/Dockerfile` | the mirror image: it copies only `entrypoint.sh`, `sqlmesh.sh`, `docs_export.py` and `status.py`, so a new container-side tool is named in *both* Dockerfiles or it ends up in the clone |
| `crudman/app/system/migrations/` | the engine writes one table from another container; the grant to `SQLMESH_DB_USER` lives in the initial migration, so a new column it has to write is a new grant |
| `sqlmesh/status.py` | raw SQL against `crudman.system_deployment`, Django being absent from that image: a renamed field is a renamed column here |
| `crudman/app/docs/views.py` | the docs pages read the export out of the deployment row rather than a file, so the export's shape is a contract between `docs_export.py` and these pages |
| `tests/test_models_repository.py` | the end-to-end path — push, poll, plan, document — and the refusal that protects a running engine |
| `SEED_PROJECTS` in `crudman/app/system/repo.py` | the seed becomes one commit per example project, and a file belongs to a project by having the name in its path. Renaming or adding an example means this list; `tests/test_models_repository.py` carries a second copy for its parameterized version tests and guards the two against drifting |

`REPO_MODELS` is a **build-time setting**, so that table applies too. `MODELS_POLL_INTERVAL`
is a **runtime setting**, so that one does.

## Volume

`quadlets/<name>_data.volume` carries the `VolumeName=`. `QUADLETS=` in `install.sh` ships
the file, and the `VOLUMES=` in its `# --- create the volumes up front ---` block is a
hardcoded list, as is the volume loop in `dev.sh` — both create the directories up front so
the rootless user owns them. `dev.sh` takes the mount itself from the quadlet, dropping only
the `.volume` unit suffix from the name.
`uninstall.sh` reads `VolumeName=` back out of the quadlets.

## Podman secret

The name is a **build-time setting** (`SECRET_*` in `buildtime.env`), so that table applies
too — including both `VARS=` allowlists and the `manifest.env` block, without which
`install.sh` cannot name the secret it creates. `create_secret` appears in `install.sh`,
`dev.sh` and `run-tests.sh`; `uninstall.sh` reads the names back out of the installed
quadlets' `Secret=` lines, so it needs nothing. A quadlet naming it needs `Secret=`, which
`dev.sh` turns into `--secret` by itself.

Whatever reads the file back out of `/run/secrets/` needs the name as well, which is what
the `Environment=SECRET_*=` lines in the quadlets are for: `settings.py` (`secret_path()`),
`crudman/entrypoint.sh`, `sqlmesh/entrypoint.sh` and `sqlmesh/config.py` each take it from
the environment with the shipped name as the fallback. `postgresql/Dockerfile` takes it as an
`ARG` instead, because its `ENV POSTGRES_PASSWORD_FILE` is baked in; `gf_0004` and the two
Grafana files get it from their render script's allowlist. `tests/test_secrets.py` is where
the coverage lives, keyed off `conftest.SECRETS`.

Podman refuses to start a container whose `Secret=` names something that does not exist,
which is why a secret nobody has configured yet gets a placeholder value the way
`oidc_client_secret` does. Credentials are secrets, never files in a volume.

## Port

The published ports are `runtime.env` settings, so a port is also a **runtime setting**
(above) and everything in that table applies. `PublishPort` in `quadlets/main.pod` names them
as `${TOKEN}`s: quadlet expands nothing itself but copies the line into the generated unit,
where systemd expands it against the `[Service] EnvironmentFile=` the pod file carries for
exactly this reason. So a port token must stay *out* of the `VARS=` allowlists — listed
there, envsubst freezes it into the shipped unit and the operator's file is ignored.

The preflight list in `# --- preflight: the published ports ---` of `install.sh` reads the
same settings (it sources `runtime.env` first) and checks each against the unprivileged-port
floor, other listeners and the firewall. `run-tests.sh` writes its isolated ports into the
test `runtime.env` rather than rewriting the pod file, `dev.sh` captures its own before
sourcing `runtime.env` over them, and `README.md` tells the operator which to open.

`SFTP_PORT` and `FLIGHT_PORT` are one setting each for both sides: the endpoints listen on
them, the pod publishes them unchanged, their healthchecks read them out of the container's
environment, and `crudman` builds the address a dropzone's admin page shows from them.
`tests/test_published_ports.py` is where the coverage lives.

## Database user, role or schema

`postgresql/initdb/gf_000N_*.{sh,sql}` runs in filename order, so a script granting on a
schema needs a higher number than the one creating it. A new role's password is a secret
(above), its connection belongs in the `tests/conftest.py` fixtures, and its boundary is
what `tests/test_access_control.py` and `tests/test_db_users.py` assert.

The init scripts are templates, not the files that reach the image: `postgresql/render.sh`
substitutes the role names from `buildtime.env` (`CRUDMAN_DB_USER`, `SQLMESH_DB_USER`,
`GRAFANA_DB_USER`, `DB_USER_PREFIX`, `ROLE_PREFIX`) into `postgresql/.initdb/`, which the
Dockerfile COPYs.
The medallion schemas ride along: `BRONZE_SCHEMA_PREFIX`, `SILVER_SCHEMA`, `GOLD_SCHEMA`.
The silver staging layer is not among them — nothing outside the SQLMesh models names it, so
`tests/conftest.py` derives it from `SILVER_SCHEMA`. So a role or
schema name is written once there and never spelled out again — in the quadlet that connects
as it (`POSTGRES_USER=`), the Grafana data source, the `dbusers` role
derivation, `sqlmesh/config.py`, or the tests. A schema, a container and a
podman secret keep the component's name instead, so `SECRET_CRUDMAN_PASSWORD` does not move
when `CRUDMAN_DB_USER` does. `tests/test_render_templates.py` guards both allowlists: an
unlisted `${TOKEN}` renders as literal text rather than failing.

The SQLMesh models under `sqlmesh/models/` are the one place that cannot follow, because a
model name is parsed by SQLMesh, which never reads `buildtime.env`. Renaming a layer means
renaming it there too; `tests/test_medallion_schemas.py` fails when the two disagree.

An event trigger matching a configured prefix uses `starts_with()`, not `LIKE` — a name
ending in `_` would otherwise be read as a single-character wildcard.

## Identity-provider rank

`MANAGED_APPS` in `sso/roles.py` is which apps a rank may hold permissions for, so an app
whose admin pages a rank should reach is named there — and its `AppConfig` has to be listed
*before* `sso` in `INSTALLED_APPS`, since Django creates an app's permissions when that
app's own `post_migrate` fires and `create_role_groups` hands them out when `sso`'s does.

The three ranks (`viewer`, `editor`, `admin`) are named once, in `sso.roles.RANKS`, and
`ROLE_PREFIX` goes in front of all of them: it names the Django group that carries the
permissions and the database group role `gf_0008` creates, which is why the two are spelled
the same. `DB_USER_PREFIX` is unrelated — it prefixes the login role of a person, and the
two must stay distinct: a person named after a rank would otherwise be provisioned the
rank's own group role, which `gf_0008` refuses to start on at the next boot.
Both are in `buildtime.env`; `crudman.container` passes them in, dev included.

`GROUP_ACTIONS` in `sso/roles.py` is what a rank may do, so adding a rank is an edit there
and in `gf_0008` — not a configuration change.

## Documentation

`crudman/app/docs/` renders what the *deployed* models describe about themselves, exported
by the engine at deploy time. It follows the commit, not the release.

`README.md` addresses someone *running* the system: novice level, no technical details. The
cheat sheet `install.sh` prints at the end tells the same story, so the two tend to move
together. `CLAUDE.md` (root, `crudman/`, `sqlmesh/`) and `.github/copilot-instructions.md`
describe the repo to agents. The `requirements.md` files (in `quadlets/` and in
`crudman/app/{dropzones,system,dbusers}/`) state what a component must do and why — they
follow a changed requirement, not a changed implementation.

## Before calling it done

`./run-tests.sh`. And when a change creates a *new* place that has to be kept in sync, a
guard test beside `tests/test_build_args.py` outlasts a note telling the next reader to look.
