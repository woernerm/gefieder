# Copilot Instructions for Gefieder

## Purpose
- The repository is named "Gefieder" and is a data analytics application.
- It is ideal for engineering related organizations which have multiple R&D projects 
  that use different tools (e.g. Jira, Github, SAP, bespoke application lifecycle and 
  requirements management tools) and processes (in the sense of workflows and 
  data models). Sometimes the differences are intentional, sometimes they are just the 
  result of corporate history or differing agendas. 
- Typical industries include automotive, space, defense, robotics and others that 
  develop mechatronic products and have a strong focus on reporting.
- Reporting is typically done to provide quality evidence for safety critical products,
  increasing the efficiency of the engineering process or satisfy arbitrary reporting 
  requirements of large business customers with strong buying power.

## Technical context
- The application is a multi-tenant data analytics application. A tenant can refer to a 
  real person or a project that shall be kept separate from other projects. 
- The application is deployed as a container image to a linux server. It can be run
  using Podman or Docker. It shall be built using Podman.
- There is a django application for administration purposes.
- There is a PostgreSQL database for storing the analytics and django application data.

### Database
- The engineering data to be analysed is stored in a PostgreSQL database.
- Scripts for initializing the PostgreSQL database are available in the `postgresql/` 
  directory of the repository. 
- Engineering data is acquired by external tools that write directly to the database. 
  This is not part of this repository.
- The database uses multiple schemas in a medallion architecture (bronze, silver, gold).
- The bronze schema contains raw data. There can be multiple bronze schemas, one for 
  each tenant/project, because the raw data can be in vastly different formats and 
  models.
- The silver schema contains data in a standardized model. 
- The gold schema contains materialized tables with precomputed metrics and statistics
  derived from standardized model data in the silver schema.  

### Administration Panel
- There is a django application that provides an adminstration panel by using Django's 
  "free" admin feature. It is called `crudman`.
- The django application is available in the `crudman` directory of the repository.
- The administration panel allows to add contextual/organizational data to the database 
  like knowledge of teams, projects, users, metadata or instituational knowledge that
  has not been documented that may vary from project to project. If this data was
  adequately documented, it would be extracted by another tool and stored in the 
  database like the engineering data. 
- The administration panel allows to add data by filling out custom forms or by 
  importing data from files.
- The administration panel is exposed to non-admin users as well so that they can add
  and edit data. This keeps admin users from having to do all the data entry work 
  themselves.
- The administration panel uses the Unfold package to provide a better user experience.

### Analytics
- Users of the application can write queries using SQLMesh that are executed according 
  to a schedule.




## Style
- Keep changes minimal compared to the current version.
- Keep the code beautiful and simple. Do not add unnecessary complexity. 
- There is just one Docker/Podman container. It is defined by the Dockerfile in the root 
  of the repository. It contains both the django application, the PostgreSQL database
  as well as the SQLMesh analytics engine. The container is built using Podman.
- Python packages are only installed using uv.
- Always explain briefly the main changes you have done and why they were necessary.
- Simplifying or making the code more concise means removing code, 
  not removing comments, newlines or whitespace.

## Container Image
- The container image is built using Podman. 
- The container image is built using the Dockerfile in the root of the repository.
- The container image contains both the django application, the PostgreSQL database
  as well as the SQLMesh analytics engine.
- The build, run and connect instructions for the container image are described in the 
  Readme file in the root of the repository and shall be updated if necessary.
- The build, run and connect instructions shall be compatible with 
  https://hub.docker.com/r/pgduckdb/pgduckdb, which is the base image used for the 
  container image.
- The commands for building, running and connecting to the container image shall be
  compatible with running both in WSL on Windows as well as on a linux host machine.
