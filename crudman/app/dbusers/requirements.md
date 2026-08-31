# dbusers — per-person database accounts

## Why this exists

Querying the warehouse, and developing SQLMesh models against it, needs a database
connection. Without this app there is exactly one: the `sqlmesh` podman secret, which is
also the credential the deployed engine uses for production. Sharing it with a team has
three consequences worth naming:

- Every developer holds a credential that reaches production data.
- No query is attributable. `pg_stat_activity` and the `server_stats` schema record the
  role, and if everyone is `sqlmesh` there is nothing to trace a slow or destructive query
  back to.
- Offboarding means rotating a secret that everyone else also holds, on every departure.

Each person therefore gets their own PostgreSQL login role. The shared secret stays where
it belongs: in the container and in CI.

Database access does not depend on administering anything: an analyst may query the
warehouse without reaching the admin at all, so the switch is independent of `is_staff`
and the rank alone decides what the role may do. A viewer connects read-only; an editor
and an admin may write.

## Where it appears

Nowhere of its own. The app registers no admin page, because the question an operator
asks is "who may reach the database", which belongs on the person rather than in a list
of role names beside them:

- A **Database access** switch on the user's change page, next to active and staff
  status. Turning it on and saving provisions the role; turning it off and saving drops
  it. The switch shows the account that exists rather than a stored intention, so a save
  that could not reach the database reports the failure and the next save retries.
- A **database access** column and filter on the user list, beside staff status.

The switch is disabled for someone holding no rank group, since there is no privilege set
to grant them — except a superuser, who ranks as `admin` whatever their groups say.
Single sign-on is what assigns the rank groups, and it may be switched off entirely; the
local administrator would otherwise be the one person unable to reach the database. It
lives in `sso/admin.py`, with the user admin it belongs to; this app keeps the model, the
PostgreSQL bridge and the login-time reconciliation.

## What it must do

- Provision a login role per Django user, named `<prefix><slug>` from their
  username, with privileges from exactly one `<prefix>`-group role. The prefix is
  `DB_ROLE_PREFIX` in `buildtime.env` (`gf_` by default), so the names this app derives
  and the roles the database created come from one setting.
- Derive the rank from the single sign-on groups in `sso/roles.py`, so the identity provider
  stays the source of truth for who may do what. Both sides share the three rank names and
  differ only in their prefix (`SSO_GROUP_PREFIX`, `DB_ROLE_PREFIX`), so neither set is
  listed twice.
- Reconcile that rank on every login: a promotion or demotion in the provider reaches the
  database on the person's next sign-in.
- Issue the credential once and never store it. A lost password is reset, not recovered.
- Show the password to its owner and to nobody else. An administrator switches access on;
  the password is generated on that person's next sign-in and shown only to them.
  Enrolling therefore creates a role with no password, which under scram-sha-256 cannot
  connect at all until it is claimed. This is why provisioning is split in two (`enroll` /
  `issue_credential`): the alternative is either an administrator relaying a secret that is
  not theirs, or storing a readable password until it is collected.
- Disable, rather than drop, when someone is offboarded through the identity provider or
  their Django account is deleted — so objects a departed person created keep their owner
  and the audit trail survives. Switching access off is the deliberate exception and does
  drop the role, because PostgreSQL cannot drop one that still owns anything: it takes
  that data with it.
- Refuse to touch the service roles — the superuser plus the three named by
  `CRUDMAN_DB_USER`, `SQLMESH_DB_USER` and `GRAFANA_DB_USER` — which the
  `is_protected_role` database function derives rather than lists, because the
  superuser's name is configurable (`SUPERUSER_NAME`, `admin` by default). And refuse to
  drop anything that is not a `<prefix>` account, the group roles excepted — a tenant
  role owns a bronze schema, and dropping one by mistake would take a tenant's data with
  it.

## What it deliberately does not do

**It does not prevent `sqlmesh plan prod`.** Promoting to production is a view swap in the
same schemas a developer must be able to write to plan anything at all, so PostgreSQL
cannot separate the two. The only arrangement that could is a private state schema and
physical layer per developer, which costs a full backfill per person — at this system's
data volumes that is not worth it. **This is an accepted risk, not an oversight.** The
control is that production is normally reached the deployed way — a push to main builds the
release, and the sqlmesh container applies the plan on start; a hand-run `sqlmesh plan prod`
is a deliberate break-glass action.

**It does not use the person's single sign-on password.** It cannot: see below.

## Authentication, and why it is a separate password

The obvious design — the administrator's Entra ID credentials work for the database too —
is not reachable today, and the reasons are worth recording so the question is not reopened
from scratch:

- **Entra ID has no LDAP endpoint.** LDAP requires Microsoft Entra Domain Services, a
  separate paid managed domain with its own VNet and LDAPS certificate rotation. Its simple
  bind also depends on NTLM password hash synchronisation, which security-conscious tenants
  routinely disable.
- **PostgreSQL had no OIDC/JWT authentication before version 18.** Version 18 adds OAuth 2.0
  bearer tokens, but ships the framework without a validator: the server needs a third-party
  library in `oauth_validator_libraries` to check a token.
- **The clients cannot use it yet.** `psycopg2-binary` bundles its own libpq (currently 17),
  which predates the feature — so SQLMesh could not authenticate that way even against an 18
  server.

So the database credential is separate from the single sign-on one. It is generated at
provisioning, shown once, and stored nowhere: PostgreSQL keeps only a SCRAM verifier.

## Swapping the method later

`backends.py` is the seam. A backend answers one question — what credential, if any, does
the role carry — and everything else (role naming, ranks, reconciliation, offboarding) is
independent of it. When a validator and an OAuth-capable driver are both available, adding
an `OAuthBackend` that reports `issues_secret = False` removes the password from the flow;
the one-time-password message disappears on its own, and `create_db_user` already accepts
a NULL password to mean "this role authenticates elsewhere". The switch does not change:
it still says whether the person has an account.

The remaining work at that point is outside this app: a validator in the postgresql image,
`oauth_issuer`/`oauth_client_id` in `postgresql.conf`, an `oauth` line in `pg_hba.conf`, and
a claim-to-role map in `pg_ident.conf`.
