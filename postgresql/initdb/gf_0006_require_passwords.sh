# initdb trusts every connection from the loopback, which the whole pod shares: without
# this, any model or notebook is the superuser by asking for it. The unix socket stays
# trusted, being reachable only from inside this container.
#
# An edit rather than POSTGRES_INITDB_ARGS, which would not reach an existing volume. No
# reload, as the server running these scripts is stopped before anything can connect.
sed -i -E 's/^(host.*[[:space:]])trust[[:space:]]*$/\1scram-sha-256/' "$PGDATA/pg_hba.conf"
