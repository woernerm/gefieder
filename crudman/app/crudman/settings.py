"""Django settings for the crudman project.

Values come from the environment: buildtime.env for what is fixed at build time,
runtime.env for the rest. Credentials are read from the podman secrets under
/run/secrets.
"""

import os
import secrets
from pathlib import Path

from django.urls import Resolver404, resolve

from sso.scopes import scopes_for

APP_NAME = os.environ.get("APP_NAME", "app").capitalize()


def secret_path(setting, default):
    """The file podman mounts one of our secrets at.

    Args:
        setting: Environment variable naming the secret, which buildtime.env may rename.
        default: The name this repository ships, used when the variable is unset.

    Returns:
        The path to read the secret from.
    """
    return Path("/run/secrets") / os.environ.get(setting, default)


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY_FILE = secret_path("SECRET_DJANGO_KEY", "django_secret_key")

# A throwaway key when the secret is missing: it keeps a checkout runnable and
# invalidates every session on restart, rather than shipping a known key.
SECRET_KEY = (
    SECRET_KEY_FILE.read_text().strip()
    if SECRET_KEY_FILE.exists()
    else secrets.token_hex(100)
)

DEBUG = os.environ.get("DEBUG", "false").strip().lower() == "true"

# Public host name of the server, from runtime.env.
SERVER_NAME = os.environ.get("SERVER_NAME", "localhost")

# Must match CRUDMAN_PATH of the proxy service and the URL configuration in urls.py.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", SERVER_NAME]

CSRF_TRUSTED_ORIGINS = [f"http://{SERVER_NAME}", f"https://{SERVER_NAME}"]

# Django matches the Origin including scheme and port, so a dev setup on a non-default
# port needs that exact origin listed. DEBUG only, so production trusts SERVER_NAME.
if DEBUG:
    CSRF_TRUSTED_ORIGINS += [
        origin.strip()
        for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
        if origin.strip()
    ]

# In production the TLS-terminating proxy is the only way in, so its forwarded protocol
# header can be trusted. In development the proxy serves plain HTTP instead.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Django's default sends handled exceptions to "mail_admins" alone once DEBUG is off, so
# without an ADMINS address a 500 leaves no trace. Route every record to stderr instead,
# where journald picks the container's log up.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # No timestamp: journald stamps every line it captures.
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Tracebacks of reported 500s; propagate=False keeps them from being logged
        # twice.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}


# Application definition

INSTALLED_APPS = [
    "unfold",  # before django.contrib.admin
    # Optional, each needed only for the feature or package it names.
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "unfold.contrib.import_export",
    "unfold.contrib.guardian",
    "unfold.contrib.simple_history",
    "unfold.contrib.location_field",
    "unfold.contrib.constance",
    'django.contrib.admin',
    # django.contrib.auth itself, under a heading of its own; see sso/apps.py.
    'sso.apps.AccessConfig',
    'django.contrib.postgres',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # An empty "manage.py startapp" scaffold: where a data model of this template's own
    # would go. Nothing else refers to it.
    'example.apps.ExampleConfig',
    'tenants.apps.TenantsConfig',
    'dropzones.apps.DropzonesConfig',
    # After sso, whose role groups decide the database rank a person is provisioned with.
    'dbusers.apps.DbUsersConfig',
    # Installed even with single sign-on off, so its post_migrate receiver keeps the
    # three role groups present and assignable by hand.
    'sso.apps.SsoConfig',
    # The SQLMesh model documentation, open from the viewer rank up.
    'docs.apps.DocsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'crudman.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'crudman.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

PG_PASSWORD_FILE = secret_path("SECRET_CRUDMAN_PASSWORD", "crudman_password")

PG_PASSWORD = (
    PG_PASSWORD_FILE.read_text().strip()
    if PG_PASSWORD_FILE.exists()
    else secrets.token_hex(100)
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'postgres'),
        'USER': os.environ.get('POSTGRES_USER', 'crudman'),
        'PASSWORD': PG_PASSWORD,
        'HOST': os.environ.get('POSTGRES_HOST', 'postgresql'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'OPTIONS': {
            'options': '-c search_path=crudman,public',
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = f'/{CRUDMAN_PATH}/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded files go to the uploads volume, which sqlmesh mounts read-only at the same
# path so models can open them under the paths the database rows point at.
MEDIA_ROOT = Path(os.environ.get('UPLOADS_DIR', BASE_DIR / 'media'))

# The dropzones SFTP endpoint (manage.py sftpserver). SFTP_DIR holds its generated host
# key. SFTP_PORT also appears in the address the admin shows, so it must match the port
# main.pod publishes.
SFTP_DIR = Path(os.environ.get('SFTP_DIR', BASE_DIR / 'sftp'))
SFTP_PORT = int(os.environ.get('SFTP_PORT', '2222'))

# The dropzones Arrow Flight endpoint (manage.py flightserver). FLIGHT_PORT must match
# the port main.pod publishes. FLIGHT_SESSION_TIMEOUT is how long an upload may stay open
# between calls before it is discarded.
FLIGHT_PORT = int(os.environ.get('FLIGHT_PORT', '8815'))
FLIGHT_SESSION_TIMEOUT = int(os.environ.get('FLIGHT_SESSION_TIMEOUT', '1800'))

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}


def _site_url(request):
    """Target for Unfold's "Return to site" link, evaluated per request.

    Args:
        request: The request Unfold is rendering for, unused.

    Returns:
        "/" once a root route exists, otherwise None to hide the link. Nothing serves a
        site root today, so the default "/" would lead to a broken page.
    """
    try:
        resolve("/")
        return "/"
    except Resolver404:
        return None


# Single sign-on
# https://docs.allauth.org/en/latest/socialaccount/providers/openid_connect.html

# All read from runtime.env. Off by default: a fresh installation has the local login.
OIDC_ENABLED = os.environ.get("OIDC_ENABLED", "false").strip().lower() == "true"
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").strip()
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "").strip()

# Where signing out sends the browser, so the provider's session ends too. Empty means it
# keeps that session and the next page view signs the person back in.
OIDC_LOGOUT_URL = os.environ.get("OIDC_LOGOUT_URL", "").strip()

# Not configurable: runtime.env values cannot contain spaces, and no page shows it.
OIDC_PROVIDER_NAME = "Single sign-on"

# Appears in the redirect URI registered with the provider, so changing it would
# invalidate that registration.
OIDC_PROVIDER_ID = "sso"

# Derived from the issuer; see sso/scopes.py for why it cannot be discovered. The
# OIDC_SCOPES variable overrules it.
OIDC_SCOPES = scopes_for(OIDC_ISSUER, os.environ.get("OIDC_SCOPES", ""))

# The placeholder the installer creates is inert while OIDC_ENABLED is false.
OIDC_CLIENT_SECRET_FILE = secret_path("SECRET_OIDC_CLIENT", "oidc_client_secret")

OIDC_CLIENT_SECRET = (
    OIDC_CLIENT_SECRET_FILE.read_text().strip()
    if OIDC_CLIENT_SECRET_FILE.exists()
    else ""
)

if OIDC_ENABLED:
    INSTALLED_APPS += [
        'allauth',
        'allauth.account',
        'allauth.socialaccount',
        'allauth.socialaccount.providers.openid_connect',
    ]
    MIDDLEWARE += [
        'allauth.account.middleware.AccountMiddleware',
        # After the authentication middleware, whose user object it decorates with the
        # provider's profile picture.
        'sso.avatars.middleware',
    ]

    # ModelBackend stays first: it is how the local superuser gets in when the provider
    # is unreachable or misconfigured.
    AUTHENTICATION_BACKENDS = [
        'django.contrib.auth.backends.ModelBackend',
        'allauth.account.auth_backends.AuthenticationBackend',
    ]

    # A list, so a second identity provider is an entry here rather than a dependency.
    SOCIALACCOUNT_PROVIDERS = {
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": OIDC_PROVIDER_ID,
                    "name": OIDC_PROVIDER_NAME,
                    "client_id": OIDC_CLIENT_ID,
                    "secret": OIDC_CLIENT_SECRET,
                    "settings": {"server_url": OIDC_ISSUER, "scope": OIDC_SCOPES},
                },
            ],
        },
    }

    # Where a sign-in that named no destination ends up; allauth's default
    # /accounts/profile/ is not served here.
    LOGIN_REDIRECT_URL = f'/{CRUDMAN_PATH}/'

    # Roles are applied on every login, in the adapter.
    SOCIALACCOUNT_ADAPTER = 'sso.adapters.SSOAccountAdapter'

    # Follow the provider on a GET instead of showing allauth's "continue" page.
    SOCIALACCOUNT_LOGIN_ON_GET = True

    # The provider vouches for the address, so a confirmation mail adds nothing.
    SOCIALACCOUNT_EMAIL_VERIFICATION = 'none'
    SOCIALACCOUNT_EMAIL_REQUIRED = False

    # Nothing calls the provider's API on the user's behalf, so the tokens are unused.
    SOCIALACCOUNT_STORE_TOKENS = False


UNFOLD = {
    # Browser tab title
    "SITE_TITLE": f"{APP_NAME} Administration",

    # Header text in the admin
    "SITE_HEADER": APP_NAME,

    # The "Return to site" link, hidden until there is a site root to return to.
    "SITE_URL": _site_url,
}