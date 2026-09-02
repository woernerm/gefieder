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
(`SERVER_NAME`, `DEBUG`) in the repository root. Neither needs editing first.

`dev.sh` brings up a stack and leaves it running. It builds the images, starts the pod,
creates the credentials it needs and prints the addresses and the login:

```bash
./dev.sh          # build and (re)start the stack
./dev.sh logs     # follow the combined log of all containers
./dev.sh down     # stop and remove the pod (the volumes and secrets are kept)
```

It always runs in development mode, so it serves plain HTTP and needs no certificate:

- Administration panel: <http://127.0.0.1:8080/crudman/>
- Model documentation: <http://127.0.0.1:8080/crudman/docs/>
- Grafana dashboards: <http://127.0.0.1:8080/grafana/>

Log in as the superuser with the password `dev.sh` prints. To run the test suite instead,
use `./run-tests.sh`, which builds a stack of its own on isolated ports and removes it
again when the suite finishes — see [Testing](#testing).


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
  published on `PG_PORT` (5432 by default) so external tools can read and write it
- `crudman` — the Django administration panel, reachable through the proxy
- `sftp` — the SFTP endpoint for dropzone uploads (the crudman image in a second role),
  published on `SFTP_PORT` (2222 by default)
- `flight` — the Arrow Flight endpoint for dropzone uploads (the crudman image in a
  third role), published on `FLIGHT_PORT` (8815 by default)
- `sqlmesh` — the SQLMesh analytics engine, running models on their cron schedules
- `grafana` — the Grafana dashboards, with the database pre-configured as a read-only
  data source and the extra panel types from `GRAFANA_PLUGINS` ready to use
- `grafana_mcp` — the Grafana MCP server, which lets an AI assistant read and change Grafana on
  your behalf (see [AI assistant access](#ai-assistant-access))
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
| `REPO` | your repository's full URL; the release workflow bakes it into `install.sh`, which downloads from `<REPO>/releases/latest/download`. It has no default — a release cannot be built without it |
| `REGISTRY` | the path the images are named under, e.g. `ghcr.io/your-org/gefieder` → `…/gefieder/crudman` |
| `IMAGE_TAG` | the image tag, e.g. `latest` |
| `SUPERUSER_NAME` | the name of the PostgreSQL, Django and Grafana superuser |
| `SUPERUSER_EMAIL` | the email address of the Django superuser |
| `SUPERUSER_DEFAULT_PASSWORD` | the password used when the installer's password prompt is left empty |
| `CRUDMAN_PATH` | the base path of the admin panel, e.g. `crudman` → `https://SERVER_NAME/crudman/` |
| `GRAFANA_PATH` | the base path of Grafana, e.g. `grafana` → `https://SERVER_NAME/grafana/` |
| `MCP_PATH` | the base path of the AI assistant endpoint, e.g. `ai/grafana_mcp` → `https://SERVER_NAME/ai/grafana_mcp/mcp`. The `/ai/` prefix leaves room for further assistant endpoints beside it |
| `PG_DATABASE` | the database everything lives in; change it if the cluster already has one named `postgres` |
| `SERVER_STATS_SCHEMA` | the schema that holds the server-usage and query statistics (see [Server statistics](#server-statistics)) |
| `SERVER_STATS_INTERVAL` | how often, in seconds, the server statistics are sampled (default 60) |
| `DUCKDB_EXTENSIONS` | the DuckDB extensions baked into the database image, comma-separated; they are downloaded at build time, so the server needs no internet access to use them |
| `GRAFANA_PLUGINS` | the extra panel types baked into the Grafana image, comma-separated plugin ids; downloaded at build time as well, so the dashboards can use them offline |
| `GRAFANA_MCP_TOOLS` | what an AI assistant may ask the system to do, comma-separated (see [AI assistant access](#ai-assistant-access)) |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | company proxy for image builds (empty = direct) |
| `PYTHON_INDEX` | additional Python package index for the build, e.g. a company mirror (empty = PyPI) |
| `DOCKER_IO_MIRROR`, `GHCR_IO_MIRROR` | where the build pulls its base images from; set them to a company mirror if `docker.io` and `ghcr.io` are slow to reach |
| `TEMPDIR` | where the installer puts its scratch files (empty = `/tmp`); set it if `/tmp` is too small for the downloaded images or is cleared while the installer runs |
| `TEMPDIR_TESTS` | the same for `run-tests.sh`, which needs far more scratch space than an installation and may have to put it on another filesystem |

A second file, `runtime.env`, holds settings read when the system runs rather than when
it is built, so changing one takes effect on the next restart without a rebuild. The
installer places it at `~/.config/<APP_NAME>/runtime.env` and keeps the one already there
on a reinstall, so your edits survive an upgrade.

| Setting | Meaning |
| --- | --- |
| `SERVER_NAME` | the full public host name, e.g. `abc123.mycompany.com` or `mysite.com`; a local development system uses `localhost` |
| `DEBUG` | development vs. production mode (see below) |
| `HTTP_PORT`, `HTTPS_PORT` | the ports the two web interfaces are reached on; `80` and `443` (see [Using custom ports](#using-custom-ports)) |
| `PG_PORT` | the port PostgreSQL is reached on; `5432` |
| `SFTP_PORT`, `FLIGHT_PORT` | the ports the two dropzone upload endpoints are reached on; `2222` and `8815` |
| `OIDC_ENABLED` | whether people sign in with their company account (see [Single sign-on](#single-sign-on)); `false` by default |
| `OIDC_ISSUER` | the address of your identity provider |
| `OIDC_AUTH_URL`, `OIDC_TOKEN_URL`, `OIDC_USERINFO_URL` | the three addresses Grafana needs spelled out; your provider lists them |
| `OIDC_LOGOUT_URL` | where signing out sends people, so their session at the provider ends too |
| `OIDC_CLIENT_ID` | the application ID your provider issued |
| `OIDC_SCOPES` | optional; leave it empty and what to ask your provider for is worked out from `OIDC_ISSUER` |

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
superuser password). To replace one, create it again with `--replace`:

```bash
printf '%s' '<new value>' | podman secret create --replace <name> -
systemctl --user restart main-pod.service
```

The services read their secrets when they start, so the restart is what makes a new value
take effect.

| Secret | Used for |
| --- | --- |
| `django_secret_key` | Django's cryptographic signing key |
| `superuser_password` | the PostgreSQL, Django and Grafana admin login |
| `crudman_password` | the database user the Django app connects with |
| `sqlmesh_password` | the database user the analytics engine connects with |
| `grafana_password` | the read-only database user for the Grafana data source |
| `oidc_client_secret` | the single sign-on client secret, if you use it (see below) |

These are the names as shipped. If one of them collides with a podman secret your server
already has, rename it in `buildtime.env` (the `SECRET_*` settings) and rebuild — the
commands below then use your name instead.

## Single sign-on
People can sign in with their company account instead of a separate password here. Once it
is on, opening the admin panel or Grafana sends them to your identity provider and straight
back — someone who is already signed in elsewhere never sees a login page at all. It works
with Entra ID, Keycloak, Authentik, Okta and Google, and is off until you configure it.

Their access is decided by three roles, which you assign to people at the provider:

| Role | In Grafana | In the admin panel |
| --- | --- | --- |
| `Viewer` | may look at dashboards | may look at the data |
| `Editor` | may build dashboards | may add and change data |
| `Admin` | full access | full access |

Someone who signs in successfully but holds none of the three is refused rather than let in
with a default role. What the provider actually said about a person is shown on their entry
under **Access → Users** in the admin panel, which is the place to look when their role and
what they can do disagree.

**Set it up at your provider.** Register one application for the whole system and give it
these two sign-in redirect addresses (with your own host name, and your own paths if you
changed `CRUDMAN_PATH` or `GRAFANA_PATH`):

```
https://SERVER_NAME/crudman/accounts/oidc/sso/login/callback/
https://SERVER_NAME/grafana/login/generic_oauth
```

Then define `Viewer`, `Editor` and `Admin` as the application's roles, assign your people to
them, and create a client secret. In Entra ID these are "app roles" — use those rather than
security groups, which arrive as unreadable identifiers.

**Set it up here.** Fill in the `OIDC_*` settings in `runtime.env`, store the client secret,
and restart:

```bash
printf '%s' '<the client secret>' | podman secret create --replace oidc_client_secret -
systemctl --user restart main-pod.service
```

**Signing out.** Fill in `OIDC_LOGOUT_URL` along with the rest. Signing out of either
service then ends the session at your provider as well, and people land on its page. Leave
it empty and signing out appears to do nothing: only the local session ends, the provider
still has one, and the next page view quietly signs the person straight back in.

**Profile pictures.** If your provider publishes one, people see their own photo beside
their name in the admin panel instead of the first letter of it. For most providers there
is nothing to set up: the address of the photo arrives with the rest of someone's details
and their browser fetches it.

Entra ID keeps photos behind Microsoft Graph, which will not hand one to a browser. The
admin panel therefore collects the photo itself while signing someone in, and holds it
only for as long as they stay signed in — sign out and it is gone. That needs one
permission more than signing in alone, `User.Read`, which the admin panel recognises Entra
ID and asks for by itself. It is one Entra ID grants applications by default, so there is
usually nothing to approve.

If your tenant does not allow it, signing in fails with a consent error rather than merely
losing the picture. Asking for nothing beyond the essentials puts it back:

```
OIDC_SCOPES=openid profile email
```

A provider that publishes no photo simply leaves people with their initial, as before.

**Give individuals more than their role.** The role is a starting point, not the whole
story. Anything you grant someone by hand — extra groups in the admin panel, permission on
a particular Grafana folder — stays with them. Signing in only ever updates their role, so
your additions are not overwritten.

**If you get locked out.** Both applications keep their own login for the admin account, in
case the provider is unreachable or misconfigured:

```
https://SERVER_NAME/crudman/login/?local
https://SERVER_NAME/grafana/login?disableAutoLogin
```

Client secrets expire — Entra ID allows two years at most. When one does, every sign-in
fails at once, so note the date somewhere and replace the secret with the command above
before it arrives.

## AI assistant access
An AI assistant — Claude, Copilot, Cursor or anything else that speaks the Model Context
Protocol — can work with your Grafana instance directly: find a dashboard, explain what a
panel measures, run a panel's query, build a new dashboard, check which alerts are firing.
It is reachable at:

```
https://SERVER_NAME/ai/grafana_mcp/mcp
```

**It gives whoever uses it exactly the access they already have, and nothing more.** Each
request carries that person's own credential, and Grafana answers it the same way it
answers them in the browser: a `Viewer` asking the assistant to change a dashboard is
refused, an `Editor` is not. There is no shared account behind it, so nobody gains rights
by going through the assistant, and a request that carries no credential is refused
outright.

To connect one, each person creates a token for themselves in Grafana under
**Administration → Users and access → Service accounts**, giving it their own role, and
puts it in their assistant's configuration. In Claude Code that is one command:

```bash
claude mcp add --transport http gefieder https://SERVER_NAME/ai/grafana_mcp/mcp \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Other assistants take the same two values as a configuration file instead:

```json
{
  "mcpServers": {
    "gefieder": {
      "type": "http",
      "url": "https://SERVER_NAME/ai/grafana_mcp/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

Treat that token like a password: it carries the holder's access. Delete it in the same
screen when it is no longer needed, and prefer one token per person over a shared one, so
a single one can be withdrawn without disturbing anyone else.

`GRAFANA_MCP_TOOLS` in `buildtime.env` bounds what any assistant can be asked to do, whoever
is using it — trim the list to leave a capability out entirely.

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
journalctl --user -f -u main-pod -u postgresql -u crudman -u sftp -u flight -u sqlmesh -u grafana -u grafana_mcp -u proxy
```

Use `postgresql`, `crudman`, `sftp`, `flight`, `sqlmesh`, `grafana`, `grafana_mcp` or `proxy` as the
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

The sampling interval is the `SERVER_STATS_INTERVAL` value in `buildtime.env`; the default
of 60 seconds is plenty for sizing. It is baked into the collector's systemd timer, so on a
server that is already installed you change it by editing `OnUnitActiveSec` in
`~/.config/systemd/user/server-stats.timer` and running `systemctl --user daemon-reload`.
Disk read/write speed and IOPS need the `io` control
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

## Writing analytics models
This walks you through changing a model and getting it into production. It follows the
example tenants that ship with the system, so you can do every step on a fresh
installation before writing anything of your own.

The models live in `sqlmesh/`, and they are ordinary files in your repository: bronze
models per tenant under `models/bronze/`, the per-tenant transforms and the harmonized
`silver.issues` under `models/silver/`, and the precomputed metrics under `models/gold/`.
The example data comes from the CSVs in `seeds/`, so the pipeline runs without any
external tooling.

### 1. Set up your machine
You work on your own machine and connect to the server's database over the network. Get
the password — it is the `sqlmesh_password` secret, so on the server run:

```bash
podman secret inspect --showsecret sqlmesh_password
```

Then, in your checkout:

```bash
uv sync --project sqlmesh                     # creates sqlmesh/.venv
echo "SQLMESH_PASSWORD=<the secret>" > sqlmesh/.env
```

`sqlmesh/.env` is gitignored and never reaches an image; exporting `SQLMESH_PASSWORD`
works just as well. Nothing else needs configuring: `sqlmesh/config.py` notices where it
is running and connects to the database next to it in the container, or to `SERVER_NAME`
from `runtime.env` on port 5432 from your machine.

For the editor, install the **SQLMesh** extension, then run *Python: Select Interpreter*
from the command palette and pick `sqlmesh/.venv/bin/python` — the extension needs an
interpreter that has SQLMesh installed. Its `sqlmesh` output channel tells you which one
it found. After changing the config, run *SQLMesh: Restart Servers*. You then get column
completion, lineage and errors as you type; the commands below stay the same either way.

### 2. Change a model
Open `sqlmesh/models/gold/issue_metrics.sql` and add a column to the query — say
`AVG(effort) AS average_effort`. Models are plain SQL wrapped in a `MODEL (...)` block
that names the model and says how it is materialized: `VIEW` for the thin harmonizing
layer, `FULL` for the gold tables dashboards read, `SEED` for the example CSVs.

### 3. Plan it into your own environment
A *plan* compares your files against a target environment and shows what would change
before anything happens:

```bash
cd sqlmesh
uv run sqlmesh plan
```

It classifies each change. Adding a column is **non-breaking**, so only that model is
rebuilt; something that changes existing rows, like a new `WHERE` clause, is **breaking**
and everything downstream is rebuilt too. You confirm, and it runs.

Notice you did not name an environment. A bare `plan` targets `dev` on your machine, and
`prod` in the container — so the easiest command to type is also the safe one, and
touching production takes deliberately typing `sqlmesh plan prod`.

### 4. Environments
An environment is a set of views, not a copy of the data. Your `dev` environment appears
as `gold__dev.issue_metrics` alongside the real `gold.issue_metrics`, and only the models
you actually changed get built; everything else points straight at the production tables.
That makes an environment cheap to create and impossible to confuse with production. Use
one per piece of work if you like — `uv run sqlmesh plan feature_x`. Unused development
environments are cleaned up after a week, so nothing accumulates.

Query yours from Grafana or `psql` exactly like the real thing:

```sql
SELECT * FROM gold__dev.issue_metrics;
```

### 5. Test it
Unit tests check a model's logic against rows you write by hand, without touching the
database — they run on an in-memory engine. Put them in `sqlmesh/tests/` as YAML:

```yaml
# sqlmesh/tests/test_issue_metrics.yaml
test_issue_metrics_counts_by_state:
  model: gold.issue_metrics
  inputs:
    silver.issues:
      rows:
        - {tenant_id: project_a, state: open, effort: 3}
        - {tenant_id: project_a, state: closed, effort: 5}
  outputs:
    query:
      rows:
        - {tenant_id: project_a, total_issues: 2, open_issues: 1, closed_issues: 1, total_effort: 8}
```

```bash
uv run sqlmesh test
```

Tests also run on their own every time you make a plan, so a broken model cannot reach
an environment unnoticed.

### 6. Audit it
Where tests check the logic, *audits* check the data each time a model runs. Built-in
ones cover the usual cases and go straight into the model:

```sql
MODEL (
  name gold.issue_metrics,
  audits (not_null(columns := (tenant_id)))
);
```

For anything else, write a query that selects the rows that should not exist. Gefieder
ships one: `sqlmesh/audits/assert_known_tenant.sql` returns any row missing `tenant_id`
or `issue_id`, and every per-tenant staging model references it, so a transform that
forgets the canonical key columns fails the plan instead of quietly polluting
`silver.issues`. Audits block by default — the run stops rather than passing bad data on.

### 7. Ship it
Commit your models. The server rebuilds its image from the repository, and the engine
applies whatever it finds to `prod` when it starts, so committing is what makes a change
permanent. Running `uv run sqlmesh plan prod` yourself applies it to production right
away, which is useful when you cannot wait for a deployment — but if you skip the commit,
the next deployment puts the old version back.

### A note on state
SQLMesh keeps its own record of every model version, what has been built and which
version each environment points at. In Gefieder that lives in the `sqlmesh` schema of the
same PostgreSQL database, which is why your machine and the server agree about
environments at all, and why the ordinary backup of `postgresql_data` already covers it.
Two consequences worth knowing: dashboards have no business reading that schema (Grafana
is denied it), and dropping it loses the history that lets SQLMesh rebuild only what
changed — the data survives, but the next plan wants to rebuild everything.

## Scripts
| Script | What it does |
| --- | --- |
| `./build.sh` | build the five images with docker (REGISTRY/IMAGE_TAG from `buildtime.env`) |
| `./dev.sh` | build and (re)start a local development stack; `down`, `logs` and `serverstats` are its subcommands |
| `./run-tests.sh [dev\|production] [pytest args]` | build a throwaway stack, run the integration suite, tear it down; extra arguments go to pytest |
| `./install.sh` | install from a GitHub release: load the image tarballs, install the quadlets, create secrets, start the system |
| `./uninstall.sh` | remove a deployment again, asking first before it deletes the data volumes and the secrets |

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
- **PostgreSQL** (with the same password): the pod publishes `PG_PORT` (5432), so reporting
  tools and the tools that fill the bronze schemas connect straight to `SERVER_NAME`:

  ```bash
  psql "host=SERVER_NAME port=PG_PORT dbname=PG_DATABASE user=SUPERUSER_NAME"
  ```

  On the server itself you can also skip the network:

  ```bash
  podman exec -it postgresql psql -U SUPERUSER_NAME -d PG_DATABASE
  ```

  `SUPERUSER_NAME` is `admin` and `PG_DATABASE` is `postgres` unless you changed them
  in `buildtime.env`.

## Using custom ports
The system is published on ports 80 and 443 for the web services, 5432 for the database,
and 2222 and 8815 for the dropzone endpoints. Each of them is a setting in `runtime.env`,
so serving on different ports (and skipping the sysctl step above) is an edit there and a
restart:

```bash
$EDITOR ~/.config/gefieder/runtime.env   # HTTP_PORT=8080, HTTPS_PORT=8443
systemctl --user restart main-pod.service
```

The dropzone ports need nothing further: each dropzone's admin page reads `SFTP_PORT` and
`FLIGHT_PORT` for the address it shows uploaders, so the page follows the change by itself.

The two web ports have one exception. Opening the system over plain `http://` still sends
the browser to the standard HTTPS port rather than yours, because the proxy cannot know
which port you published it on — reach it over `https://` directly.

And if you use single sign-on, tell Grafana the address it is reached at, by adding this
line to `~/.config/containers/systemd/grafana.container` and restarting. Grafana builds the
sign-in return address from it, and left alone it would leave your port out and send people
somewhere that does not answer. The admin panel takes the port from the browser and needs
nothing:

```
Environment=GF_SERVER_ROOT_URL=https://SERVER_NAME:8443/grafana/
```

## Testing
The integration test suite spins up a throwaway stack and asserts the behaviour the
system promises: containers start and stay healthy, the apps are reachable and serve
their static files, the schemas exist with the right per-role access, each service's log
reaches the journal, a killed container is restarted, volume data
survives a restart, and no secret value leaks into an image or quadlet. It creates any
credentials it is missing and tears the stack down again afterwards.

Run it away from any production system: it installs over the same paths a deployment uses,
so it asks to remove an installed one first — and that takes its data volumes with it.

```bash
./run-tests.sh             # development profile: plain HTTP
./run-tests.sh production  # production profile: HTTPS with a self-signed certificate
./run-tests.sh dev -k logs # only the integration tests whose name matches
```

## Licensing
The code in this repo (the Dockerfiles, scripts, quadlets, Django app and SQL) is
Apache-2.0 — use it freely, no warranty. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

The software it builds on keeps its own license. Two cases to be aware of:

- **Grafana is AGPL-3.0.** This is a copyleft license: if you run a modified Grafana as
  a network service, you have to make your modified source available to its users.
  Shipping the stock image as-is is fine; just don't patch Grafana and keep the changes
  private. This says nothing about the rest of the project, which stays Apache-2.0.
- **The DuckDB extensions** listed in `DUCKDB_EXTENSIONS` and the **Grafana panel
  plugins** listed in `GRAFANA_PLUGINS` (both in `buildtime.env`) are just examples,
  taken from their community repositories and baked into the images at build time.
  Licenses and quality vary, so trim both lists to what you actually use before going to
  production.

Everything else — the base images (PostgreSQL/pgduckdb, nginx, Python) and the Python
dependencies (Django, gunicorn, SQLMesh, ...) — is permissively licensed; check the
individual projects if you need the details.
