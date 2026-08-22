"""WSGI config for the crudman project.

Exposes the WSGI callable as a module-level variable named ``application``.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crudman.settings')

application = get_wsgi_application()
