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
`systemd`. For building the images you also need either `docker` (what CI uses) or
`podman` itself.

```bash
# RHEL 9.5+ / Fedora:
sudo dnf install podman
# Ubuntu 25.04+:
sudo apt install podman
```

Check the version with `podman --version`. All commands are run from the repository
root and work the same on Linux and on WSL.


## Run it locally

The settings come from `buildtime.env` in the repository root; the default `DEBUG=false`
serves HTTPS, so for a quick local try-out switch it to development mode (plain HTTP, no
certificate) first. The simplest way to bring up a working stack on your machine is the
test runner, which builds the images, renders the quadlets and starts the pod:

```bash
sed -i 's/^DEBUG=.*/DEBUG=true/' buildtime.env   # development mode (plain HTTP)
./run-tests.sh                                    # builds, starts the pod, runs the suite
```

`run-tests.sh` tears its stack down again at the end. To keep a stack running for manual
use, build and install it the same way a release does (see [Deploy](#deploy-it-on-a-server))
but with locally built images. The credentials are podman secrets; create them once with
`openssl rand -hex 32 | podman secret create <name> -` (see [Secrets](#secrets)).

- Administration panel: <http://localhost/crudman/>
- Grafana dashboards: <http://localhost/grafana/>


## Deploy it on a server

A deployment needs **no checkout** — everything comes from a GitHub release built by the
[workflow](#automate-deployments): one tarball per image, one file per quadlet, and the
installer. The only host-local pieces are the podman secrets (the installer creates them)
and the TLS certificate.

**1. Add the TLS certificate** for your host to `~/.config/gefieder/certs/`
(`~/.config/<APP_NAME>/certs/` if you renamed the project): `fullchain.pem` (certificate
incl. intermediates) and `privkey.pem` (private key).

**2. Allow the ports** — let rootless podman bind 80/443 and open the firewall:

```bash
echo net.ipv4.ip_unprivileged_port_start=80 | sudo tee /etc/sysctl.d/99-gefieder.conf
sudo sysctl --system

# RHEL:
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
# Ubuntu:
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

**3. Install from the release.** The installer downloads each asset, loads the image
tarballs into rootless podman, installs the quadlets, creates the machine secrets
(prompting once for the superuser password), and prints a control cheat sheet:

```bash
curl -fsSL https://github.com/woernerm/gefieder/releases/latest/download/install.sh | bash
```

Then start the pod and verify in a browser that `http://` redirects to `https://`:

```bash
systemctl --user start main-pod.service
```

**Updating** is the same step again with a newer release: re-run the installer, which
loads the new image tarballs, then restart the services. (There is no registry
auto-update; the release tarballs are the unit of delivery.)


## Automate deployments

The **Build and release** workflow (`.github/workflows/publish.yml`) runs on every push
to `main` (or manually with a tag). It builds the five images with docker, renders the
quadlets, and publishes a GitHub release containing one tarball per image, one file per
quadlet, and `install.sh`. Company proxy settings in `buildtime.env`
(`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`) are passed to the build so package installs work
from behind a corporate proxy.

The actions are pinned to full commit hashes. Building with docker is shared with local
builds through `build.sh`, so CI and a developer build identically.


## The containers
The system is a single pod (named after `APP_NAME`, `gefieder` by default; the pod file
is `main.pod`, so the systemd unit is `main-pod.service`) of five containers:

- `postgresql` — the database holding the engineering, analytics and application data
- `crudman` — the Django administration panel, reachable through the proxy
- `sqlmesh` — the SQLMesh analytics engine, running models on their cron schedules
- `grafana` — the Grafana dashboards, with the database pre-configured as a read-only
  data source
- `proxy` — an nginx reverse proxy that serves the admin panel and Grafana under
  `SERVER_NAME` and publishes the pod's ports 80/443

The unit files live in `quadlets/` as templates with `${...}` tokens. The release
workflow renders them (substituting the `buildtime.env` values) and the installer places
the rendered files in `~/.config/containers/systemd/`.

## Settings
The build reads its settings from `buildtime.env` in the repository root. These values
are baked into the images and the rendered quadlets at build time, so a deployed server
needs neither the file nor a checkout. It is committed with these defaults, which you can
adjust:

| Setting | Meaning |
| --- | --- |
| `APP_NAME` | the name of the project (pod name, volume prefix, cert dir) |
| `REGISTRY` | the path the images are named under, e.g. `ghcr.io/your-org/gefieder` → `…/gefieder/crudman` |
| `IMAGE_TAG` | the image tag, e.g. `latest` |
| `SUPERUSER_NAME` | the name of the PostgreSQL, Django and Grafana superuser |
| `SUPERUSER_EMAIL` | the email address of the Django superuser |
| `SERVER_NAME` | the public host name, e.g. `mysite.com`; the admin panel also accepts `localhost` |
| `CRUDMAN_PATH` | the base path of the admin panel, e.g. `crudman` → `https://SERVER_NAME/crudman/` |
| `GRAFANA_PATH` | the base path of Grafana, e.g. `grafana` → `https://SERVER_NAME/grafana/` |
| `SERVER_STATS_SCHEMA` | the schema that holds the server-usage and query statistics (see [Server statistics](#server-statistics)) |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | company proxy for image builds (empty = direct) |
| `DEBUG` | development vs. production mode (see below) |

A second file, `runtime.env`, holds settings read when the system runs rather than when
it is built, so changing one takes effect on the next run without a rebuild. The installer
places it at `~/.config/<APP_NAME>/runtime.env`.

| Setting | Meaning |
| --- | --- |
| `SERVER_STATS_INTERVAL` | how often, in seconds, the server-statistics collector samples (default 60) |

## Development vs. production mode
The `DEBUG` setting in `buildtime.env` decides how the system runs:

- `DEBUG=true` — development mode: the proxy serves plain HTTP without certificates and
  Django shows debug pages.
- `DEBUG=false` — production mode (the default): the proxy serves HTTPS only, redirects
  HTTP to HTTPS, and needs a certificate (see below).

`DEBUG` is rendered into the quadlets at build time, so changing it means rebuilding and
re-installing (or, for a local stack, re-running the renderer and `daemon-reload`).

## Secrets
All passwords and keys are podman secrets, so they never appear in the quadlets or the
images. The installer creates the machine secrets automatically (and prompts once for the
superuser password). A secret cannot be overwritten; to replace one, `podman secret rm
<name>` it and create it again.

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
`~/.config/gefieder/certs/` (`<APP_NAME>` if renamed) and bind-mounted into the proxy:

- `~/.config/gefieder/certs/fullchain.pem` — the certificate including intermediates
- `~/.config/gefieder/certs/privkey.pem` — the private key

## Storage
Persistent data lives in named volumes, one per service, that the quadlets create
automatically on the first start (the installer also pre-creates them so the rootless
user owns their contents):

- `postgresql_data` — the database (all engineering, analytics and application data)
- `grafana_data` — the Grafana dashboards, users and settings
- `crudman_data`, `sqlmesh_data`, `proxy_data` — currently the persistent logs of each
  service (see [Logs](#logs))

They survive stopping the stack. Inspect them with `podman volume ls`. To delete the
data, remove the volume explicitly, e.g. `podman volume rm postgresql_data`.

## Logs
Every service keeps a persistent log on its volume, so a crash leaves its cause on disk
(not just in `podman logs`, which is lost when the container is replaced):

- `postgresql` and `grafana` are configured to log into a `log/` subdirectory of their
  data volume.
- `crudman`, `sqlmesh` and `proxy` tee their entrypoint output to a log file on their
  data volume; those files are owned by the rootless podman user, so you can read them
  without `podman unshare`.

Follow the live logs through journald, or read the persistent files from the volumes:

```bash
journalctl --user -f -u crudman.service                       # live log of one service
cat "$(podman volume inspect crudman_data -f '{{.Mountpoint}}')/crudman.log"
```

## Server statistics
Gefieder records how much of the server it actually uses, so you can right-size the next
one (up or down) instead of guessing. A small collector samples the system once a minute
and stores the numbers in the database, next to the per-query statistics it also records:

- **For sizing**: CPU, memory, disk space, the temporary/spill storage that wants fast
  disk, disk read/write speed and IOPS, and outgoing network traffic. Each is kept as a
  fine-grained recent history and a long-term hourly trend, so after a few months you can
  read off the sustained load and the peaks.
- **For tuning**: which queries cost the most time and I/O, and which tables are scanned
  often enough to deserve an index.
- **For usage**: which dashboard gets visited, how often, and at what time of day and day
  of the week. The proxy records each page view (filtering out the background requests a
  dashboard makes), so you can see what people actually look at — for the admin panel too.
  Visitors are grouped by a hashed session, never by name, and the raw session cookie is
  never stored.

It starts automatically after installation. The data lives in the `server_stats` schema;
the dashboards that present it are added separately. A few controls:

```bash
systemctl --user status server-stats.timer    # is sampling running?
systemctl --user start server-stats.service    # take a sample right now
journalctl --user -u server-stats.service      # see what the collector did
```

The sampling interval is the `SERVER_STATS_INTERVAL` value in `runtime.env`; the default
of 60 seconds is plenty for sizing. Disk read/write speed and IOPS need the `io` control
group, which the installer delegates for you (it asks for `sudo` once); without it those
two figures stay blank while everything else is still recorded.

## Scripts
| Script | What it does |
| --- | --- |
| `./build.sh` | build the five images with docker (REGISTRY/IMAGE_TAG from `buildtime.env`) |
| `./install.sh` | install from a GitHub release: load the image tarballs, install the quadlets, create secrets |
| `./run-tests.sh [production]` | build a throwaway stack, run the integration suite, tear it down |
| `./dev.sh serverstats` | take one server-statistics sample against the local dev stack |

## Everyday commands
```bash
systemctl --user start main-pod.service   # start the pod (or start an individual service)
systemctl --user stop  main-pod.service   # stop the whole pod
systemctl --user restart crudman.service  # restart a single service
podman pod ps                             # show the pod and its containers
podman logs -f sqlmesh                    # follow a container's live log
```

## Connecting directly
- **Admin panel / Grafana**: log in with `SUPERUSER_NAME` and the `superuser_password`.
- **PostgreSQL** (with the same password): the database port is not published in
  production; add `PublishPort=5432:5432` to the pod file
  (`~/.config/containers/systemd/main.pod`, then `daemon-reload`) if you need it, or
  connect from inside the pod:

  ```bash
  podman exec -it postgresql psql -U admin -d postgres
  ```

## Using custom ports
The pod publishes ports 80 and 443. To serve on different ports (and skip the sysctl
step above), edit the two `PublishPort` lines in the installed pod file and reload:

```bash
sed -i 's/^PublishPort=80:80/PublishPort=8080:80/; s/^PublishPort=443:443/PublishPort=8443:443/' \
  ~/.config/containers/systemd/main.pod
systemctl --user daemon-reload
systemctl --user restart main-pod.service
```

## Testing
The integration test suite spins up a throwaway stack and asserts the behaviour the
system promises: containers start and stay healthy, the apps are reachable and serve
their static files, the schemas exist with the right per-role access, each service writes
a persistent log owned by the rootless user, a killed container is restarted, volume data
survives a restart, and no secret value leaks into an image or quadlet. It tears the
stack down again. Run it away from any production system; the secrets must already exist.

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
