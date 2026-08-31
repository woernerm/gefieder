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

        # Bound to the login signal rather than to the single sign-on adapter, so an
        # account changed by hand in the admin is reconciled the same way.
        user_logged_in.connect(sync_on_login, dispatch_uid="dbusers.sync_on_login")

        # pre_delete: the role name is read from the row the cascade is about to take.
        pre_delete.connect(
            disable_on_user_delete,
            sender=User,
            dispatch_uid="dbusers.disable_on_user_delete",
        )
