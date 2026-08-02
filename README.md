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

The settings come from `buildtime.env` (image names, paths) and `runtime.env`
(`SERVER_NAME`, `DEBUG`) in the repository root. The simplest way to bring up a working
stack on your machine is the test runner, which builds the images, renders the quadlets
and starts the pod. It picks the mode itself, so neither file needs editing first:

```bash
./run-tests.sh              # development mode (plain HTTP), builds and runs the suite
./run-tests.sh production   # production mode (HTTPS with a throwaway certificate)
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
incl. intermediates) and `privkey.pem` (private key). The proxy does not start without
them and says so; see [Certificates](#certificates) to keep them elsewhere.

**2. Allow the ports** — let rootless podman bind 80/443 and open the firewall:

```bash
echo net.ipv4.ip_unprivileged_port_start=80 | sudo tee /etc/sysctl.d/99-gefieder.conf
sudo sysctl --system

# RHEL:
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
# Ubuntu:
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

You can skip this and go straight to the installer: it checks the ports it needs (80, 443,
5432, 2222 and 8815) and, for anything reserved or blocked, prints the exact command for
the firewall your server actually runs. The install itself carries on either way, and the
commands are repeated in the cheat sheet, so you can hand them to an admin afterwards.
The database, SFTP and Arrow Flight ports are yours to decide on — you may prefer to open
them only to a VPN or a specific subnet rather than to everyone.

**3. Install from the release.** The installer downloads each asset, loads the image
tarballs into rootless podman, installs the quadlets, creates the machine secrets
(prompting once for the superuser password, or leave it empty to take the
`SUPERUSER_DEFAULT_PASSWORD` from `buildtime.env`), starts the system, and prints a control
cheat sheet with the addresses and the everyday commands. Point the command at your own
repository (the `REPO` you set in `buildtime.env`, which may be an enterprise GitHub
instance):

```bash
curl -fsSL https://github.example.com/myorg/myrepo/releases/latest/download/install.sh | bash
```

The stack is up when it finishes (the database takes a few seconds to initialise on the
first run). Open the admin panel address it printed and verify that `http://` redirects
to `https://`.

**Updating** is the same step again with a newer release: re-run the installer, which
loads the new image tarballs and restarts the system. (There is no registry auto-update;
the release tarballs are the unit of delivery.)


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
is `main.pod`, so the systemd unit is `main-pod.service`) of seven containers:

- `postgresql` — the database holding the engineering, analytics and application data,
  published on port 5432 so external tools can read and write it
- `crudman` — the Django administration panel, reachable through the proxy
- `sftp` — the SFTP endpoint for dropzone uploads (the crudman image in a second role),
  published on port 2222
- `flight` — the Arrow Flight endpoint for dropzone uploads (the crudman image in a
  third role), published on port 8815
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
| `SUPERUSER_DEFAULT_PASSWORD` | the password used when the installer's password prompt is left empty |
| `CRUDMAN_PATH` | the base path of the admin panel, e.g. `crudman` → `https://SERVER_NAME/crudman/` |
| `GRAFANA_PATH` | the base path of Grafana, e.g. `grafana` → `https://SERVER_NAME/grafana/` |
| `SERVER_STATS_SCHEMA` | the schema that holds the server-usage and query statistics (see [Server statistics](#server-statistics)) |
| `DUCKDB_EXTENSIONS` | the DuckDB extensions baked into the database image, comma-separated; they are downloaded at build time, so the server needs no internet access to use them |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | company proxy for image builds (empty = direct) |
| `TEMPDIR` | where the installer puts its scratch files (empty = `/tmp`); set it if `/tmp` is too small for the downloaded images or is cleared while the installer runs |

A second file, `runtime.env`, holds settings read when the system runs rather than when
it is built, so changing one takes effect on the next restart without a rebuild. The
installer places it at `~/.config/<APP_NAME>/runtime.env` and keeps the one already there
on a reinstall, so your edits survive an upgrade.

| Setting | Meaning |
| --- | --- |
| `SERVER_NAME` | the full public host name, e.g. `abc123.mycompany.com` or `mysite.com`; a local development system uses `localhost` |
| `DEBUG` | development vs. production mode (see below) |
| `SERVER_STATS_INTERVAL` | how often, in seconds, the server-statistics collector samples (default 60) |

On a company network, give the server its full name in `SERVER_NAME`, domain included —
`abc123.mycompany.com` rather than just `abc123`. A bare machine name usually does not work
from a colleague's browser: it hands the short name to the company web proxy instead of
resolving it, and the proxy answers with an error page rather than reaching the server. The
installer warns about an unqualified name and checks that both applications answer at the
address before it finishes.

After changing either, restart the system so the services pick the new value up:
`systemctl --user restart main-pod.service`.

## Development vs. production mode
The `DEBUG` setting in `runtime.env` decides how the system runs:

- `DEBUG=true` — development mode: the proxy serves plain HTTP without certificates and
  Django shows debug pages.
- `DEBUG=false` — production mode (the default): the proxy serves HTTPS only, redirects
  HTTP to HTTPS, and needs a certificate (see below).

Leave it `false` on a server other people reach: the debug pages show tracebacks, file
paths and settings to anyone who triggers an error. Changing it needs no rebuild, only
`systemctl --user restart main-pod.service`.

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

Both files have to be there before you start the system: without them the proxy stops
with a message naming what it did not find, and the installer says the same. Nothing is
generated for you, so a browser never sees a certificate you did not choose. Renewing one
is a copy over the two files followed by
`systemctl --user restart proxy.service`.

To keep the certificate somewhere else — a directory your PKI already fills, for instance
— set `CERTIFICATE_PATH` in `buildtime.env` and rebuild. It is written in systemd syntax,
where `%h` is the home directory of the user running the containers, and an absolute path
such as `/etc/pki/gefieder` works as well.

## Storage
Persistent data lives in named volumes, one per service, that the quadlets create
automatically on the first start (the installer also pre-creates them so the rootless
user owns their contents):

- `postgresql_data` — the database (all engineering, analytics and application data)
- `grafana_data` — the Grafana dashboards, users and settings
- `uploads_data` — the files uploaded through dropzones (see
  [Uploading files](#uploading-files-with-dropzones))
- `sftp_data` — the host key of the SFTP upload endpoint, so uploaders' SFTP clients
  keep trusting the server across updates
- `proxy_data` — the page-visit records the server statistics are built from

They survive stopping the stack. Inspect them with `podman volume ls`. To delete the
data, remove the volume explicitly, e.g. `podman volume rm postgresql_data`.

The `postgresql` and `grafana` volumes are written by a user inside the container, so
listing their contents from the host needs `podman unshare ls <path>`.

## Logs
Every service logs to the journal, which keeps the logs across restarts and updates, so a
crash leaves its cause on disk. Old logs are rotated away automatically, and you need no
special permissions to read them:

```bash
journalctl --user -f -u crudman                   # follow one component
journalctl --user -u crudman --since '2 hours ago'
journalctl --user -f -u main-pod -u postgresql -u crudman -u sftp -u flight -u sqlmesh -u grafana -u proxy
```

Use `postgresql`, `crudman`, `sftp`, `flight`, `sqlmesh`, `grafana` or `proxy` as the
component name. The SFTP and Arrow Flight endpoints are part of the crudman application
but run as their own services, so they have their own logs. The last command combines all
of them into one stream, and the cheat sheet the installer prints repeats it.

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

## Uploading files with dropzones
Not all data arrives through tools writing to the database. For files that people
produce by hand — a mapping someone maintains in Excel, a CSV export from another
system — create a *dropzone* in the admin panel (Dropzones → Add). A dropzone stands
for one kind of file: what it is for, which format it comes in, who may upload it, and
what happens right after the upload: an optional check that rejects bad files, and an
optional conversion (say, CSV to Parquet). Rejected files are never stored; the
uploader sees the reason immediately and can fix the files and try again.

Every dropzone has a secret upload link, shown on its admin page. Hand it to the person
providing the files: they get a simple page where they drop one or more files and state
how long the set is valid — always, until they replace it with a new upload, or for a
fixed period (the dropzone's default validity is preselected). When a replacement
arrives, the previous upload's validity is shortened to end where the new one begins.

Files can also arrive without a person at a browser. A dropzone with the *API endpoint*
method takes a plain HTTP POST (the admin page shows a ready-to-run `curl` line), and
one with the *SFTP* method takes uploads from any SFTP or scp client on port 2222 —
handy for other systems that can only "drop files somewhere". SFTP uploaders log in
with the dropzone's name and secret, put one or more files and disconnect; that's the
whole protocol. Everything sent in one session is stored as one upload with the
dropzone's default validity, an interrupted transfer stores nothing, and uploaders
only ever see their own session — never each other's files.

Analysts who already work with data frames can skip files altogether. A dropzone with
the *Arrow Flight* method takes Arrow tables straight over the network on port 8815 —
each table is stored as its own Parquet file, named after the table. The admin page
shows the address together with a complete client script to copy, so sending a Polars
or DuckDB result is a handful of lines. Several tables can go into one upload; nothing
is stored until the client commits at the end, so an upload that breaks off halfway
leaves nothing behind.

Even devices that cannot produce a file are covered: a dropzone with the *Webhook*
method takes a plain HTTP GET and stores whatever values ride along in the URL — say
a temperature a sensor reports — as a small CSV file, one per call, through the same
pipeline. This suits IoT devices like Shelly relays, which can call a URL with
measured values filled in whenever something happens.

Each upload is recorded in the `crudman.dropzones_upload` table together with its file
paths, and the files land on the `uploads_data` volume, which the analytics engine sees
read-only under the same path. An analytics model therefore just selects the upload
valid at the timestamp it is computing and reads the files. Converters for the usual
formats ship ready to use — CSV, Excel and JSON to Parquet, where every Excel sheet
becomes its own file. The check and convert functions are plain Python functions in
`crudman/app/dropzones/functions/`; the shipped ones double as the pattern for
writing your own.

## Scripts
| Script | What it does |
| --- | --- |
| `./build.sh` | build the five images with docker (REGISTRY/IMAGE_TAG from `buildtime.env`) |
| `./install.sh` | install from a GitHub release: load the image tarballs, install the quadlets, create secrets, start the system |
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
- **PostgreSQL** (with the same password): the pod publishes port 5432, so reporting
  tools and the tools that fill the bronze schemas connect straight to `SERVER_NAME`:

  ```bash
  psql "host=SERVER_NAME port=5432 dbname=postgres user=admin"
  ```

  On the server itself you can also skip the network:

  ```bash
  podman exec -it postgresql psql -U admin -d postgres
  ```

## Using custom ports
The pod publishes ports 80 and 443 for the web services, 5432 for the database, and 2222
and 8815 for the dropzone endpoints. To serve on different ports (and skip the sysctl
step above), edit the `PublishPort` lines in the installed pod file and reload:

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
- **The DuckDB extensions** listed in `DUCKDB_EXTENSIONS` (`buildtime.env`) are just
  examples, taken from the community repo and baked into the image at build time.
  Licenses and quality vary, so trim the list to what you actually use before going to
  production.

Everything else — the base images (PostgreSQL/pgduckdb, nginx, Python) and the Python
dependencies (Django, gunicorn, SQLMesh, ...) — is permissively licensed; check the
individual projects if you need the details.
