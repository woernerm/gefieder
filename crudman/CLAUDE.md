# Crudman — the Django administration panel

Crudman allows non-technical users to add contextual and organizational data that no 
other tool records. Mostly Django's own admin, styled with Unfold, and exposed to 
non-admin, non-technical users. 

## Layout

- `app/crudman/` — settings, urls, wsgi/asgi
- `app/tenants/` — tenants and their database roles and schemas
- `app/dropzones/` — Multi-file uploads using one of sftp, HTTP POST, Browser upload,
   Apache Arrow Flight protocol and others, pipeline for checking and converting data.
- `app/sso/` — OpenID Connect login via allauth
- `app/example/` — an empty scaffold; Perfect to quickly spin up forms for non-technical 
   users to input their data.

## Rules

- Use Django's documented public API and override only what Django intends to be
  overridden. The same goes for Unfold.
- Every upload method saves data to files that are feed into a single pipeline: 
  `services.process_upload`. 
- Check and convert functions live in `dropzones/functions/`, registered with `@checker`
  and `@converter` and autodiscovered from `registry.FUNCTIONS_PACKAGE` at
  `DropzonesConfig.ready`.
- Functions register under their own name. Do not rename check or conversion functions.
  Use sentence case for decorator label and snake_case for functions.
- Single sign-on assumes Azure EntraID. Other providers and local accounts shall work
  as well. Provider assigns role to user; this project assigns permissions to roles 
  (`sso/roles.py`). Keep allauth imports in `sso/adapters.py` so role logic stays 
  testable with single sign-on switched off.

## Tests

`app/*/tests.py` run inside the built image, twice: `run-tests.sh` runs them once 
plainly and once with single sign-on configured.

Requirements: `app/dropzones/requirements.md`, `app/tenants/requirements.md`.
