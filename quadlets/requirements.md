# Requirements for deployment, build and installation

The units here describe the whole system to systemd: `main.pod`, one `*.container` per
service (`postgresql`, `crudman`, `sqlmesh`, `grafana`, `grafana_mcp`, `proxy`, `sftp`, `flight`) and one
`*_data.volume` per volume. They are shipped and installed as one set, so they live in this
one directory rather than one directory per service. The reasoning behind the choices below
is in `CLAUDE.md`.

# Deployment
- The system shall be deployed using podman quadlets and shall run with rootless podman.
- Each service that keeps state shall have its own data volume for persistence.
- All services shall run in the same pod and be able to reach each other.
- The system shall support at least Ubuntu and Red Hat.
- The README.md file shall include installation instructions.
- The services shall log to stdout/stderr only, carry no second timestamp of their own, and
  be readable with `journalctl` on the host by the host user. The proxy's `visits.log` is
  exempt.

# AI assistant access
- The system shall expose a Model Context Protocol endpoint through the proxy, so an AI
  assistant can work the Grafana instance through its API.
- A caller shall be granted exactly the permissions the person behind it holds in Grafana,
  and no others: its credential shall be carried through to Grafana, which decides. The
  server shall therefore hold no Grafana credential of its own, and a call carrying none
  shall be refused rather than served with a shared account's rights.
- Which capabilities exist at all shall be a build-time setting, bounding every caller
  from above regardless of role.

# Configuration
- There shall be a `buildtime.env` configuration file for all variables that need to be
  known before the images are built, including the company proxy settings and an extra
  index URL for uv or pip. If the index URL is not empty, uv/pip shall use it.
- There shall be a `runtime.env` configuration file for all variables that need to be known
  before the images are run. These shall be made available as environment variables in the
  images requiring them, not in every image.
- PostgreSQL extensions shall be downloaded during the image build, so the target machine
  needs no internet connection to install them.

# Build
- Each service directory shall have a Dockerfile.
- The github workflow shall build the images with docker.
- The github workflow shall read the proxy settings from `buildtime.env` and pass them as
  command line arguments to the docker build command, so packages from public repositories
  such as pypi or dockerhub install even when building from behind a company proxy.
- The github release shall consist of separate files: one file for each quadlet file, one
  file for each docker image, plus the installer, the host-side collector, the default
  `runtime.env` and the manifest the installer sources.
- The build script shall use full commit hashes for actions.
- The build script shall be triggered by a commit to the main branch.

# Install script
- The install script shall be reachable as a curl one-liner from the latest github release,
  with the repository taken from the `REPO` setting in `buildtime.env`.
- It shall test whether subuid and subgid mappings are available for the current user
  before continuing with the installation.
- It shall use separate curl commands for downloading all files related to a github release.
- It shall create the data volumes up front, so their directories belong to the rootless
  podman user from the start.
- It shall create podman secrets for the crudman, grafana and django users as well as the
  django_secret_key, based on `openssl rand -hex 32`. It shall omit the creation of secrets
  for human users such as the superuser.
- It shall store a helpfile in the rootless podman user's home directory.
- It shall output a cheat sheet with control commands for
    - starting the system right now,
    - starting the backup procedure right now,
    - viewing a live log of the whole system as one stream. One `journalctl` command covers
      both the live and the persistent log: the journal is where the log lives, so there is
      no file to `cat`, and dropping the `-f` reads the history back. Per-component
      commands belong in the README, not in the cheat sheet, which stays short enough to be
      useful.
    - the path of each volume, so the user can cd into the respective directories, naming
      the ones whose contents need `podman unshare` to read,
    - opening the `runtime.env` configuration file with the host system's default editor,
      or nano if there is none,
    - setting the single sign-on client secret. It belongs here rather than in the README
      because the identity provider expires it, so the command is needed again long after
      the installation.
