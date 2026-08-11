from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SsoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sso'
    verbose_name = 'Single sign-on'

    def ready(self):
        from .roles import create_role_groups

        # post_migrate fires once per installed app, so the receiver is bound to this one
        # to run the work a single time. This app is listed last, after the apps whose
        # permissions the groups hand out, so by then those permissions exist.
        post_migrate.connect(create_role_groups, sender=self)
