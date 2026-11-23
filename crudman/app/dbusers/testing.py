"""Test support shared by the apps whose tests provision database accounts."""
from unittest.mock import patch

from sso.roles import GROUP_FOR_RANK

from .models import DatabaseUser


def enrolled(user):
    """The database account row a person needs before a notebook credential exists."""
    return DatabaseUser.objects.create(
        user=user,
        role_name=f"gf_{user.username}",
        group_role=GROUP_FOR_RANK["editor"],
        is_enabled=True,
    )


class MockedConnectionMixin:
    """Replaces the connection dbusers.utils provisions through, for the whole test.

    The provisioning functions live in the stack's database, not the test database, so
    what is asserted is what they are asked: ``self.cursor`` records every statement.
    """

    def setUp(self):
        super().setUp()
        patcher = patch("dbusers.utils.connection")
        self.connection = patcher.start()
        self.addCleanup(patcher.stop)
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
