# Instructions for Gefieder

## Purpose
- The repository is named "Gefieder".
- The repository is a template for data analytics systems. The included software 
  services are already configured and properly wired together. It automatically updates
  and restarts itself after power loss or failure.
- It is well suited for organizations which might have multiple projects with 
  different data sources (issue trackers, version control systems, ERP systems, other 
  bespoke applications) as well as different workflows or processes. 
- It is applicable across a range of industries that have a strong focus on reporting.
- Reporting is typically done to provide evidence of quality, improve efficiency or 
  satisfy reporting requirements of third parties.

## Technical context
- The application is a multi-tenant data analytics application. A tenant can refer to a 
  real person or a project that shall be kept separate from other projects. 
- The application is deployed as podman quadlets to a linux server.
- It supports at least Ubuntu and RHEL. 
- There is a django service `crudman` for administration and data entry (CRUD).
- There is a PostgreSQL service for storing the analytics and django application data.
- There is a SQLMesh analytics engine for running queries and generating reports.
- There is a Grafana instance for visualizing the data and reports. 
- There is a proxy service that terminates TLS and routes requests to the services.
- All services are running in the same network and can communicate with each other.
- All services have their own data volumes for persistence.
- All services have their own directory in the repository.

### Database
- Engineering data is acquired by external tools that write directly to the database. 
- The database uses multiple schemas in a medallion architecture (bronze, silver, gold).
- The bronze schema contains raw data. There can be multiple bronze schemas, one for 
  each tenant/project, because the raw data can be in vastly different formats and 
  models.
- The silver schema contains data in a standardized model. 
- The gold schema contains materialized tables with precomputed metrics and statistics
  derived from standardized model data in the silver schema.  

### Administration Panel
- `crudman` mainly uses Django's "free" admin feature, styled with the Unfold package.
- It is used to add contextual/organizational data to the database, such as knowledge of 
  teams, projects, users, metadata or institutional knowledge that has not been 
  documented and may vary from project to project. If this data was adequately 
  documented, it would be extracted by another tool and stored in the database like the 
  engineering data. 
- Data is added by filling out custom forms or by importing it from files.
- It is exposed to non-admin users as well, so data entry can be shared rather than 
  relying solely on admin users.

### Analytics
- Users of the application can write queries using SQLMesh that are executed according 
  to a schedule.

# Deployment
- The system shall be deployed using podman quadlets. 
- Each software component shall have a `quadlets/` directory with the corresponding 
  quadlet files.
- The system shall run with rootless podman.
- The README.md file shall include installation instructions.
- All build artifacts, the quadlet files, as well as the install script shall be 
  uploaded as github release.
- The system shall be installable from a github release using a curl command similar to 
  this: `curl -fsSL https://github.com/woernerm/gefieder/releases/latest/install.sh | bash`
- The system shall use the entrypoint.sh scripts to write persistent logs to the volume
  (e.g. using `tee`). The logs shall be owned by the rootless podman user.

## Configuration
- There shall be a buildtime.env configuration file for all variables that need to be
  known before the images are build.
- There shall be a runtime.env configuration file for all variables that need to be 
  known before the images are run. These shall be made available as environment 
  variables in the images requiring them (not every variable in every image).
- The buildtime.env configuration file shall have entries for company proxy settings.

## Build
- Each service directory shall have a Dockerfile; 
- The github workflow shall use docker to build the images. 
- The github workflow shall read the proxy settings from the .env file and provide
  then as command line arguments to the docker build command. This is intended to
  allow the installation of packages from public repositories like pypi or dockerhub
  even when building from behind a company proxy.
- The github release shall consist of separate files: One file for each quadlet file.
  One file for each docker image. 

## Install Script
- The install script shall test whether subuid and subgid mappings are available for the 
  current user before continuing with the installation.
- The install script shall make sure that the rootless podman user always owns all files 
  in a volume so that `podman unshare ...` is not necessary.
- The install script shall use separate curl commands for downloading all files related
  to a github release.
- The install script shall create podman secrets for the crudman, grafana and django
  users as well as the django_secret_key based on `openssl rand -hex 32`. It shall omit 
  the creation of secrets for human users like the superuser. 
- The install script shall output a cheat sheet with control commands for
    - Control command for starting the system right now.
    - Control command for starting the backup procedure right now.
    - Control command for viewing a life log the combined log of the system.
    - Control command for viewing a life log of each software component.
    - The path of each volume (so that the user can cd into the respective directories).
    - Control command for opening the runtime.env configuration file with the host
      system's default editor (or nano if there is no default).
    - A `cat` command for viewing the persistent logs of each software component.
- The install script shall store a helpfile in the rootless podman user's home 
  directory.

# Style
- Follow podman/container deployment best practices.
- Keep changes minimal compared to the current version.
- Keep the code beautiful and simple. Do not add unnecessary complexity. 
- Python packages are only installed using uv.
- Always explain briefly the main changes you have done and why they were necessary.
- Simplifying or making the code more concise means removing code, not removing 
  comments, newlines or whitespace.
- Comments first and foremost explain why something is done.
- Filenames and folder structure should look clean and professional, following best 
  practices.