"""Every filter, defined once. A dashboard shows the ones its queries read.

A query reads a filter by its name after a colon. What it receives depends on the kind:

    Select   a list, so a query writes   column = ANY(:name)
             Nothing picked means everything, so no query needs a case for "All".
    Since    the moment the chosen period starts, so a query writes   time >= :name
"""
from dashboards import Select, Since

# The options are a query of their own: its first column is what the list offers.
project = Select("SELECT DISTINCT tenant_id FROM silver.issues ORDER BY 1")
state = Select("SELECT DISTINCT state FROM silver.issues ORDER BY 1")

since = Since("6 hours", label="Time range")
