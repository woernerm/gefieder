# This app stores nothing of its own: a login maps onto Django's own users and groups.
#
# The file is still here on purpose: Django skips post_migrate for any app without a
# models module, and that signal creates the three role groups (see apps.py).
