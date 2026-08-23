"""Django settings for the crudman project.

Values come from the environment: buildtime.env through the quadlet for what is fixed at
build time, runtime.env on the target machine for the rest. Credentials are read from the
podman secrets mounted under /run/secrets.

See https://docs.djangoproject.com/en/6.0/ref/settings/ for the settings themselves.
"""

import os
import secrets
from pathlib import Path

from django.templatetags.static import static
from django.urls import Resolver404, resolve

from sso.scopes import scopes_for

APP_NAME = os.environ.get("APP_NAME", "app").capitalize()


def secret_path(setting, default):
    """The file podman mounts one of our secrets at.

    Args:
        setting: Environment variable naming the secret, since a machine that already
            held a secret of that name will have had it renamed in buildtime.env.
        default: The name this repository ships, used when the variable is unset.

    Returns:
        The path to read the secret from.
    """
    return Path("/run/secrets") / os.environ.get(setting, default)


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY_FILE = secret_path("SECRET_DJANGO_KEY", "django_secret_key")

# A throwaway key when the secret is missing: it keeps a checkout runnable and invalidates
# every session on restart, rather than shipping a known key.
SECRET_KEY = (
    SECRET_KEY_FILE.read_text().strip()
    if SECRET_KEY_FILE.exists()
    else secrets.token_hex(100)
)

DEBUG = os.environ.get("DEBUG", "false").strip().lower() == "true"

# The public host name of the server, set via runtime.env on the host.
SERVER_NAME = os.environ.get("SERVER_NAME", "localhost")

# Must match CRUDMAN_PATH of the proxy service and the URL configuration in urls.py.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", SERVER_NAME]

CSRF_TRUSTED_ORIGINS = [f"http://{SERVER_NAME}", f"https://{SERVER_NAME}"]

# Django matches the Origin including scheme and port, so a dev setup reached on a
# non-default port needs that exact origin listed; the local dev runner sets the variable
# to the address it publishes. Only in DEBUG, so production trusts SERVER_NAME alone.
if DEBUG:
    CSRF_TRUSTED_ORIGINS += [
        origin.strip()
        for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
        if origin.strip()
    ]

# In production the TLS-terminating reverse proxy is the only way in, so cookies are
# restricted to HTTPS and the forwarded protocol header is trusted. In development
# (DEBUG=true) the proxy serves plain HTTP instead.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# Application definition

INSTALLED_APPS = [
    "unfold",  # before django.contrib.admin
    "unfold.contrib.filters",  # optional, if special filters are needed
    "unfold.contrib.forms",  # optional, if special form elements are needed
    "unfold.contrib.inlines",  # optional, if special inlines are needed
    "unfold.contrib.import_export",  # optional, if django-import-export package is used
    "unfold.contrib.guardian",  # optional, if django-guardian package is used
    "unfold.contrib.simple_history",  # optional, if django-simple-history package is used
    "unfold.contrib.location_field",  # optional, if django-location-field package is used
    "unfold.contrib.constance",  # optional, if django-constance package is used
    'django.contrib.admin',
    # django.contrib.auth itself, under a heading of its own; see sso/apps.py.
    'sso.apps.AccessConfig',
    'django.contrib.postgres',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # An empty app as "manage.py startapp" leaves it, and the place to start when this
    # template grows a data model of its own. Nothing else refers to it.
    'example.apps.ExampleConfig',
    'tenants.apps.TenantsConfig',
    'dropzones.apps.DropzonesConfig',
    # Chart panels whose SQL is stored rather than written here, so they can be created
    # at runtime. Their queries run on the analytics connection below, never this app's.
    'analytics.apps.AnalyticsConfig',
    # After sso, whose role groups decide the database rank a person is provisioned with.
    'dbusers.apps.DbUsersConfig',
    # Always installed, even with single sign-on off, so its post_migrate receiver keeps
    # the three role groups present and assignable by hand.
    'sso.apps.SsoConfig',
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
        # Project templates win over every app's, which is the only way to override
        # one Unfold ships: it has to stay first in INSTALLED_APPS, so an app template of
        # the same name would never be reached. templates/admin/index.html is the
        # dashboard carrying the chart panels.
        'DIRS': [BASE_DIR / 'templates'],
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

# The connection the chart panels query the medallion layers on. It authenticates as the
# analytics role -- the one Grafana uses -- for two reasons: the read grants on silver,
# gold and the per-tenant bronze schemas already exist on it, and a query written here
# therefore returns exactly what the same query returns in a Grafana dashboard. The role
# holds no write grant on anything it can read, which is what makes a stored, editable
# SQL statement safe to run; analytics.query adds a read-only transaction on top.
PANELS_PASSWORD_FILE = secret_path("SECRET_GRAFANA_PASSWORD", "grafana_password")

if PANELS_PASSWORD_FILE.exists():
    DATABASES['analytics'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'postgres'),
        'USER': os.environ.get('GRAFANA_DB_USER', 'grafana'),
        'PASSWORD': PANELS_PASSWORD_FILE.read_text().strip(),
        'HOST': os.environ.get('POSTGRES_HOST', 'postgresql'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }

# Nothing is ever migrated onto the analytics connection, and no model reads or writes
# through it; see analytics/routers.py.
DATABASE_ROUTERS = ['analytics.routers.AnalyticsRouter']


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

# Where the dropzones app stores uploaded files: the uploads volume, which the sqlmesh
# container mounts read-only at the same path so analytics models can open the files
# under the very paths the database rows point at.
MEDIA_ROOT = Path(os.environ.get('UPLOADS_DIR', BASE_DIR / 'media'))

# The dropzones SFTP endpoint (manage.py sftpserver). SFTP_DIR holds its generated host
# key. SFTP_PORT is both the port the server listens on and the one the admin shows in a
# dropzone's SFTP address, so it must match the port main.pod publishes.
SFTP_DIR = Path(os.environ.get('SFTP_DIR', BASE_DIR / 'sftp'))
SFTP_PORT = int(os.environ.get('SFTP_PORT', '2222'))

# The dropzones Arrow Flight endpoint (manage.py flightserver). FLIGHT_PORT must match
# the port main.pod publishes, like SFTP_PORT. FLIGHT_SESSION_TIMEOUT is how long an
# upload may stay open between calls before it is abandoned and discarded.
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
        "/" once a root route exists, otherwise None to hide the link; no site root is
        served today, so the default "/" would lead to a broken page.
    """
    try:
        resolve("/")
        return "/"
    except Resolver404:
        return None


# Single sign-on
# https://docs.allauth.org/en/latest/socialaccount/providers/openid_connect.html

# All read from the operator's runtime.env. Off by default: a fresh installation has the
# local login.
OIDC_ENABLED = os.environ.get("OIDC_ENABLED", "false").strip().lower() == "true"
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").strip()
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "").strip()

# Where signing out sends the browser afterwards, so the provider's own session ends too.
# Empty means it keeps that session, and the next page view signs the person back in.
OIDC_LOGOUT_URL = os.environ.get("OIDC_LOGOUT_URL", "").strip()

# Not a setting: runtime.env values cannot contain spaces, and with the redirect below
# there is no page left that shows this to anyone.
OIDC_PROVIDER_NAME = "Single sign-on"

# Names the provider in URLs, so it appears in the redirect URI registered with the
# provider. A constant, because changing it would invalidate that registration.
OIDC_PROVIDER_ID = "sso"

# Worked out from the issuer rather than configured; see sso/scopes.py for why it cannot
# be discovered. The OIDC_SCOPES variable overrules it, and is empty unless there is a
# reason for it not to be.
OIDC_SCOPES = scopes_for(OIDC_ISSUER, os.environ.get("OIDC_SCOPES", ""))

# The provider's client secret, mounted by the quadlet. The placeholder the installer
# creates is inert while OIDC_ENABLED is false.
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
        # profile picture the provider published.
        'sso.avatars.middleware',
    ]

    # ModelBackend stays first and enabled: it is how the local superuser gets in when
    # the provider is unreachable or misconfigured.
    AUTHENTICATION_BACKENDS = [
        'django.contrib.auth.backends.ModelBackend',
        'allauth.account.auth_backends.AuthenticationBackend',
    ]

    # A list of issuers rather than a single one, so a second identity provider is an
    # entry here rather than another dependency.
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

    # Where a sign-in that named no destination ends up; allauth would send it to
    # /accounts/profile/, which this project does not serve.
    LOGIN_REDIRECT_URL = f'/{CRUDMAN_PATH}/'

    # Roles are applied on every login, in the adapter.
    SOCIALACCOUNT_ADAPTER = 'sso.adapters.SSOAccountAdapter'

    # Follow the provider on a GET instead of showing allauth's "continue" page, which
    # would defeat the point of sending people straight through.
    SOCIALACCOUNT_LOGIN_ON_GET = True

    # The provider vouches for the address; confirming it by mail would be a second
    # identity check on top of the one that just succeeded.
    SOCIALACCOUNT_EMAIL_VERIFICATION = 'none'
    SOCIALACCOUNT_EMAIL_REQUIRED = False

    # Nothing calls the provider's API on the user's behalf, so the tokens are of no use
    # after login and are not worth storing.
    SOCIALACCOUNT_STORE_TOKENS = False


UNFOLD = {
    # Browser tab title
    "SITE_TITLE": f"{APP_NAME} Administration",

    # ECharts and the code that turns a panel fragment into a chart. Vendored rather than
    # loaded from a CDN: a target machine need not reach the internet. Unfold already
    # loads HTMX itself, which is what fetches the fragments.
    "SCRIPTS": [
        lambda request: static("analytics/echarts.min.js"),
        lambda request: static("analytics/analytics.init.js"),
    ],

    # Header text in the admin
    "SITE_HEADER": APP_NAME,

    # The "Return to site" link, hidden until there is a site root to return to.
    "SITE_URL": _site_url,

    # Puts the panels flagged for the dashboard onto it; see analytics/dashboard.py.
    "DASHBOARD_CALLBACK": "analytics.dashboard.dashboard_callback",
}