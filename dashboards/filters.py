"""Every filter, defined once. A dashboard shows the ones its queries read.

A query reads a filter by its name after a colon. A Select binds a list, so a query writes
``column = ANY(:name)``; nothing picked means everything, so no query needs a case for "All".

The time range needs no filter: every dashboard has one, and a query reads it as
``time >= :from AND time < :to``.
"""
from dashboards import Select

# The options are a query of their own: its first column is what the list offers.
project = Select("SELECT DISTINCT tenant_id FROM silver.issues ORDER BY 1")
state = Select("SELECT DISTINCT state FROM silver.issues ORDER BY 1")
