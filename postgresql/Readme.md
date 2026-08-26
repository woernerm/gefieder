PostgreSQL comes with a hook called `/docker-entrypoint-initdb.d`. It is a directory 
where you can place scripts that will be executed when the container starts for the 
first time. This is a convenient way to set up your PostgreSQL instance with custom 
configurations, extensions, or initial data.

Running them only on the first start is not enough here: a schema, grant or function
added to these scripts later would never reach a deployment that already has a data
volume, and anything dropped by hand would stay dropped. So `entrypoint.sh` runs them
again on every start, against the live database, before handing over to the base image's
entrypoint. That is what repairs a missing schema and what carries a newly added grant
onto an existing deployment.

**Every script here therefore runs repeatedly against production data, and must be
idempotent.** Use `IF NOT EXISTS`, `CREATE OR REPLACE`, or an explicit catalogue check
(`CREATE ROLE` and `CREATE EVENT TRIGGER` have no `IF NOT EXISTS`; the latter is dropped
first). Grants are deliberately re-applied unconditionally, because that is what repairs
a tampered one. Never write a statement here that appends, seeds rows, or otherwise
changes the database when the intended state is already in place.

A script that must run only once carries `once` in its filename, which keeps it out of
the re-applied set: `gf_0001_once_configure_settings.sh` appends to `postgresql.conf`,
and `gf_0006_once_create_example_tenants.sql` seeds the example tenants an administrator
is meant to be able to delete for good.
