from django.apps import AppConfig
from django.contrib.auth.apps import AuthConfig
from django.db.models.signals import post_migrate


class AccessConfig(AuthConfig):
    """django.contrib.auth under a heading that says what the section is for.

    Django calls it "Authentication and Authorization", which with single sign-on on sat
    beside two allauth headings meaning much the same thing. One heading, and a short one:
    it must not read like the "Users" link below it, the same reason the tenants app is
    labelled "System".

    Lives in this app because this app is the rest of the same story -- the role groups
    that decide what a signed-in person may do. Only the display name changes; the app
    label stays "auth", so every permission and admin URL is untouched.
    """

    verbose_name = 'Access'


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
