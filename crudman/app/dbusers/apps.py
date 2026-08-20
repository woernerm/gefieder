from django.apps import AppConfig


class DbUsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dbusers'
    verbose_name = 'Database access'

    def ready(self):
        from django.contrib.auth.models import User
        from django.contrib.auth.signals import user_logged_in
        from django.db.models.signals import pre_delete

        from .signals import disable_on_user_delete, sync_on_login

        # A rank granted or taken away in the identity provider reaches the database on the
        # person's next sign-in. Bound to the login signal rather than to the single
        # sign-on adapter so a local account, changed by hand in the admin, is reconciled
        # the same way -- sso.roles.apply_roles has already run by this point either way.
        user_logged_in.connect(sync_on_login, dispatch_uid="dbusers.sync_on_login")

        # pre_delete rather than post_delete: the role name is read from the row being
        # deleted, and after the cascade there is nothing left to read it from.
        pre_delete.connect(
            disable_on_user_delete,
            sender=User,
            dispatch_uid="dbusers.disable_on_user_delete",
        )
