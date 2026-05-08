PostgreSQL comes with a hook called `/docker-entrypoint-initdb.d`. It is a directory 
where you can place scripts that will be executed when the container starts for the 
first time. This is a convenient way to set up your PostgreSQL instance with custom 
configurations, extensions, or initial data.