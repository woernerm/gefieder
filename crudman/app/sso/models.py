# This app stores nothing of its own: a login maps onto Django's users and groups. The
# file exists because Django skips post_migrate for an app without a models module, and
# that signal creates the three role groups (see apps.py).
