# Copilot Instructions for Gefieder

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

## Style
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

## Build & Deployment
- Each service directory has a Dockerfile; images are built with Podman and deployed via 
  the quadlets in the `quadlets/` directory.
- The postgresql service is based on https://hub.docker.com/r/pgduckdb/pgduckdb.
- The build, run and connect instructions are described in the README.md file in the 
  root of the repository and shall be updated if necessary.
- These instructions shall be compatible with running both in WSL on Windows as well as 
  on a linux host machine.
