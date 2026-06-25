# Requirments for generating the "tenants" django app.

The "Tenants" django app manages tenants in the PostgreSQL database. While it has
a Tenant model, the instances shown in the admin interface are fake. They don't refer
to rows in table but are obtained by extracting the information from the tenant schemas
in the database (see `Claude.md` for details on the tenant model and the medaillon 
architecture). Creating a tenant also means calling the respective PostgreSQL function
instead of actually saving a row in the database representing the tenant.


# Models
- There shall be a django model called `Tenant`.
- Users shall be able to create instances of `Tenant` in the admin interface only.
- The create_tenant function of the Postgresql database shall be wrapped in a Python
  utility function with the same name and parameters. It just calls the create_tenant
  function in the database with the given parameters and returns a boolean for success 
  or failure.
- Creating a `Tenant` in the admin interface shall call the create_tenant python utlity 
  function of in order to create the tenant and not actually save the Tenant model
  instance.
- There shall be a get_tenants python utility function that returns `Tenant` model
  instances based on the tenant schemas in PostgreSQL database.
- The list of Tenants in the admin interface shall be obtained using the get_tenants
  python utility function.
- The `Tenant` model shall have at least the following fields:
    - `name`: The name of the tenant, e.g. "max_mustermann" or "customer_a_project".
    - `connection_limit`: The maximum number of simultaneous database connections.
    - `statement_timeout`: The maximum amount of time a statement run by the tenant can
       run for.
    - `work_mem`: The maximum amount of memory the tenant may consume.
    - `temp_file_limit`: The maximum size of a temp file for the tenant.
- The default values for the attributes related to limits shall be infinite, e.g. None
  for no limit.
- The utility functions and models shall be testable.
- The fake display, creation and editing of `Tenant` model instances shall use django 
  standard hooks, functions and methods officially intended for overriding.
- Simple, concise and typical solutions are preferred. Avoid overriding private and 
  undocument django details.
