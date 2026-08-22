"""Keeping Django's own writes off the analytics connection.

The ``panels`` connection authenticates as the analytics role, which may only read. It
exists to run panel queries and nothing else, so every model operation -- reads included
-- is steered to the default connection and no migration is ever applied to it.
"""

from .query import PANELS_CONNECTION


class PanelsRouter:
    """Routes every model to the default connection and blocks migrating ``panels``."""

    def db_for_read(self, model, **hints):
        return "default"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Refuse to migrate the analytics connection.

        Args:
            db: The connection alias a migration is about to run against.
            app_label: The app being migrated, unused.
            model_name: The model being migrated, unused.
            **hints: The router hints, unused.

        Returns:
            False for the panels connection, so ``migrate`` skips it; None elsewhere, to
            leave the decision to Django.
        """
        return False if db == PANELS_CONNECTION else None
