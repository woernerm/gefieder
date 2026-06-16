# Gefieder

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Gefieder is a multi-tenant data analytics platform for engineering teams, built on
PostgreSQL, DuckDB, SQLMesh and Django (with the Unfold admin interface).

It runs as a small pod of containers managed by podman. Once it is up you get two web
interfaces:

- an **administration panel** (Django) for entering and editing organizational data
- **Grafana dashboards** with the database already wired up as a read-only data source

This README walks you from nothing to a running system: first locally on your own
machine, then deployed on a server, followed by reference sections for the settings,
scripts and commands.


## What you need

[Podman](https://podman.io/) **5.0 or newer** (for the `.pod` quadlet support) and
`systemd`. 

```bash
# RHEL 9.5+ / Fedora:
sudo dnf install podman
# Ubuntu 25.04+:
sudo apt install podman
```

Check the version with `podman --version`. All commands are run from the repository
root and work the same on Linux and on WSL.


## Run it locally

`run-dev.sh` brings up the whole system in development mode (plain HTTP, no
certificates) so you can try it out. It creates any missing secrets (prompting once for
the superuser password), builds the images, switches `.env` to `DEBUG=true` and starts
the pod:

```bash
./run-dev.sh
```

Then log in as `admin` with that password:

- Administration panel: <http://localhost/crudman/>
- Grafana dashboards: <http://localhost/grafana/>

Stop it with `./run-dev.sh down`; your data is kept in named volumes (see
[Storage](#storage)). The dev stack reads its settings from this repository's `.env`, so
run the script from the checkout; the checkout itself can live anywhere.


## Deploy it on a server

The server needs **no checkout and no `.env`** — everything is pulled from the registry.
The only host-local pieces are the podman secrets and the TLS certificate. First publish
a build (see [Automate deployments](#automate-deployments) or run `./build.sh push` from
a checkout).

**1. Add the TLS certificate** for your host to `~/.config/gefieder/certs/`:
`fullchain.pem` (certificate incl. intermediates) and `privkey.pem` (private key).

**2. Allow the ports** — let rootless podman bind 80/443 and open the firewall:

```bash
echo net.ipv4.ip_unprivileged_port_start=80 | sudo tee /etc/sysctl.d/99-gefieder.conf
sudo sysctl --system

# RHEL:
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
# Ubuntu:
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

**3. Deploy.** Fetch the one-file `deploy.sh` and run it with your registry. It creates
any missing secrets, pulls and extracts the quadlet artifact, starts the pod, and
enables lingering plus the auto-update and backup timers:

```bash
curl -fsSLO <repository-raw-url>/deploy.sh && sh deploy.sh ghcr.io/your-org/gefieder
```

(If the registry is private, `podman login <registry-host>` first.) Verify in a browser
that `http://` is redirected to `https://`.


## Automate deployments

The **Build and publish** workflow (`.github/workflows/publish.yml`) builds and pushes
new versions; the server picks them up on its own. Trigger it manually, give it the git
ref to build, and it runs the [test suite](#testing) and then publishes if the tests
pass. A `dry_run` checkbox runs only the tests.

For ghcr.io the built-in `GITHUB_TOKEN` is used; for another registry set
`REGISTRY_USERNAME` / `REGISTRY_PASSWORD` repository secrets (*Settings → Secrets and
variables → Actions*). `SUPERUSER_PASSWORD` is needed so the test job can create the
superuser secret.

On the server there is nothing to trigger: the new images are pulled and applied within
a day. To apply immediately, run `podman auto-update` by hand.


## The containers
The system is a single pod (named after `APP_NAME`, `gefieder` by default) of five
containers:

- `postgresql` — the database holding the engineering, analytics and application data
- `crudman` — the Django administration panel, reachable through the proxy
- `sqlmesh` — the SQLMesh analytics engine, running models on their cron schedules
- `grafana` — the Grafana dashboards, with the database pre-configured as a read-only
  data source
- `proxy` — an nginx reverse proxy that serves the admin panel and Grafana under
  `SERVER_NAME` and publishes the pod's ports 80/443

The unit files live in `quadlets/`. Locally, `install.sh` renders them from `.env` into
`~/.config/containers/systemd/`; a server deployment pulls them ready-made instead.

## Settings
The build and the local dev stack read their settings from the `.env` file in the
repository root. It is committed with these defaults, which you can adjust:

| Setting | Meaning |
| --- | --- |
| `APP_NAME` | the name of the project |
| `REGISTRY` | the registry path the images live under, e.g. `ghcr.io/your-org/gefieder` → `…/gefieder/crudman` |
| `IMAGE_TAG` | the image tag to run, e.g. `latest` |
| `SUPERUSER_NAME` | the name of the PostgreSQL, Django and Grafana superuser |
| `SUPERUSER_EMAIL` | the email address of the Django superuser |
| `SERVER_NAME` | the public host name, e.g. `mysite.com`; the admin panel also accepts `localhost` |
| `CRUDMAN_PATH` | the base path of the admin panel, e.g. `crudman` → `https://SERVER_NAME/crudman/` |
| `GRAFANA_PATH` | the base path of Grafana, e.g. `grafana` → `https://SERVER_NAME/grafana/` |
| `DEBUG` | development vs. production mode (see below) |

## Development vs. production mode
The `DEBUG` setting in `.env` decides how the system runs:

- `DEBUG=true` — development mode: the proxy serves plain HTTP without certificates and
  Django shows debug pages.
- `DEBUG=false` — production mode (the default): the proxy serves HTTPS only, redirects
  HTTP to HTTPS, and needs a certificate (see below).

Changing the mode requires recreating the containers, e.g. with
`systemctl --user restart proxy.service` after editing `.env`.

## Secrets
All passwords and keys are podman secrets, so they never appear in the quadlets or the
images. A secret cannot be overwritten; to replace one, `podman secret rm <name>` it and
create it again.

| Secret | Used for |
| --- | --- |
| `django_secret_key` | Django's cryptographic signing key |
| `superuser_password` | the PostgreSQL, Django and Grafana admin login |
| `crudman_password` | the `crudman` database user the Django app connects with |
| `sqlmesh_password` | the `sqlmesh` database user the analytics engine connects with |
| `grafana_password` | the read-only `grafana` database user for the Grafana data source |

## Certificates
In production mode the proxy needs a TLS certificate for `SERVER_NAME`. It is the only
host-local config (it is a secret, so it is never baked into an image), placed in
`~/.config/gefieder/certs/` and bind-mounted into the proxy:

- `~/.config/gefieder/certs/fullchain.pem` — the certificate including intermediates
- `~/.config/gefieder/certs/privkey.pem` — the private key

## Storage
Persistent data lives in named volumes that the quadlets create automatically on the
first start, so there is no manual step. They are prefixed with `APP_NAME` (so
`gefieder-*` by default):

- `gefieder-postgresql` — the database (all engineering, analytics and application data)
- `gefieder-grafana` — the Grafana dashboards, users and settings
- `gefieder-backup` — the scheduled database dumps (see Backups)

They survive stopping the stack. Inspect them with `podman volume ls`. To delete the
data, remove the volume explicitly, e.g. `podman volume rm gefieder-postgresql`.

## Auto-update and rollback
The server pulls new images on a daily timer and restarts the affected services. If a
new image fails its healthcheck, podman **rolls back** to the previous one
automatically. Check or force it with:

```bash
podman auto-update --dry-run   # show what would change
podman auto-update             # pull, restart, and roll back on failure
```

## Backups
A daily timer runs `pg_dumpall` into the `gefieder-backup` volume. Rollback only covers
images, not data, so these dumps are your safeguard against a bad migration. They are
not pruned or copied off the host, so rotate them and replicate them elsewhere if you
rely on them.

Find the dumps on the host and restore one by piping it into the `postgresql` container:

```bash
ls "$(podman volume inspect gefieder-backup -f '{{.Mountpoint}}')"

DUMP="$(podman volume inspect gefieder-backup -f '{{.Mountpoint}}')/<file>.sql"
podman exec -i postgresql sh -c \
  'PGPASSWORD=$(cat /run/secrets/superuser_password) psql -h localhost -U "$POSTGRES_USER" -d postgres' \
  < "$DUMP"
```

## Scripts
| Script | What it does |
| --- | --- |
| `./build.sh [push]` | build (and optionally push) the five images and the quadlet artifact |
| `./run-dev.sh [down]` | start (or stop) the full system locally in development mode |
| `./deploy.sh <registry>` | clone-free server deploy: pull the artifact, start the stack, enable auto-update |
| `./install.sh [dir]` | render the quadlet templates from `.env` into the user quadlet dir |
| `./run-tests.sh [production]` | build a throwaway stack, run the integration suite, tear it down |

## Everyday commands
```bash
systemctl --user start proxy.service     # start the pod (the proxy pulls in the rest)
systemctl --user stop  proxy.service     # stop one service; stop all five to stop the pod
systemctl --user restart crudman.service # restart a service (e.g. after editing .env)
podman pod ps                                      # show the pod and its containers
podman logs -f sqlmesh                             # follow a container's logs
podman auto-update                                 # pull and apply new images now
```

## Connecting directly
- **Admin panel / Grafana**: log in with `SUPERUSER_NAME` and the `superuser_password`.
- **PostgreSQL** (with the same password): the database port is not published in
  production; add `PublishPort=5432:5432` to the pod file
  (`~/.config/containers/systemd/gefieder.pod`, then `daemon-reload`) if you need it, or
  connect from inside the pod:

  ```bash
  podman exec -it postgresql psql -U admin -d postgres
  ```

## Using custom ports
The pod publishes ports 80 and 443. To serve on different ports (and skip the sysctl
step above), edit the two `PublishPort` lines in the rendered pod file and reload:

```bash
sed -i 's/^PublishPort=80:80/PublishPort=8080:80/; s/^PublishPort=443:443/PublishPort=8443:443/' \
  ~/.config/containers/systemd/gefieder.pod
systemctl --user daemon-reload
systemctl --user restart proxy.service
```

## Testing
The integration test suite spins up a throwaway stack, checks that the system works
(containers start, the apps are reachable, static files load, the schemas exist and the
`grafana` role has the access it should) and tears it down again. Run it away from any
production system; the secrets must already exist.

```bash
./run-tests.sh             # development profile: plain HTTP
./run-tests.sh production  # production profile: HTTPS with a self-signed certificate
```

## Licensing
The code in this repo (the Dockerfiles, scripts, quadlets, Django app and SQL) is
Apache-2.0 — use it freely, no warranty. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

The software it builds on keeps its own license. Two cases to be aware of:

- **Grafana is AGPL-3.0.** This is a copyleft license: if you run a modified Grafana as
  a network service, you have to make your modified source available to its users.
  Shipping the stock image as-is is fine; just don't patch Grafana and keep the changes
  private. This says nothing about the rest of the project, which stays Apache-2.0.
- **The DuckDB extensions** in `postgresql/initdb/` are just examples, pulled from the
  community repo at runtime. Licenses and quality vary, so trim the list to what you
  actually use before going to production.

Everything else — the base images (PostgreSQL/pgduckdb, nginx, Python) and the Python
dependencies (Django, gunicorn, SQLMesh, ...) — is permissively licensed; check the
individual projects if you need the details.
