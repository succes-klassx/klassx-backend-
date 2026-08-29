"""
Django settings for the KLASSX project.

This is a starting-point configuration meant to be reviewed and hardened
before going to production (see README.md, section "Before production").
"""
from datetime import timedelta
from pathlib import Path
import json
import os

# Loads variables from a local .env file if python-dotenv is installed.
# Not required, but convenient for local development.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "insecure-dev-key-change-me-before-deploying",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# ---------------------------------------------------------------------------
# HTTPS / cookies — s'activent automatiquement dès que DEBUG=False (donc en
# prod, une fois DJANGO_DEBUG=False positionné comme indiqué dans
# .env.production.example). Restent désactivés en dev pour ne pas casser le
# test en http://localhost.
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
# HSTS piloté par sa PROPRE variable d'env, séparée de DEBUG (0 par défaut =
# désactivé, y compris en prod). Le navigateur retient "toujours HTTPS pour
# ce site" pendant SECURE_HSTS_SECONDS — une mauvaise valeur peut donc
# rendre le site inaccessible jusqu'à expiration, indépendamment de tout
# nouveau déploiement. Montez la valeur progressivement une fois le HTTPS
# vérifié stable :
#   0 (défaut) → 300 (5 min) → 86400 (1 jour) → 2592000 (30 jours) → 31536000 (1 an)
# Ne définissez SECURE_HSTS_SECONDS dans le .env qu'après avoir confirmé
# que le HTTPS fonctionne sans accroc sur le domaine ET tous ses
# sous-domaines utilisés (www. inclus).
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0
# Nécessaire derrière la plupart des hébergeurs (Heroku, Render, Railway...)
# qui terminent le TLS en amont et transmettent la requête en HTTP interne
# avec cet en-tête — sans ça, Django croit à tort que la requête est en HTTP
# et boucle sur la redirection HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
   "cloudinary_storage",
    "django.contrib.staticfiles",
    "cloudinary",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    # Local
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sert les fichiers statiques en prod, doit rester juste après SecurityMiddleware
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "klassx.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "klassx.wsgi.application"
ASGI_APPLICATION = "klassx.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Defaults to SQLite for local development. On Render (and most other
# hosts), the filesystem is wiped on every redeploy — so without a real
# database configured here, every deploy silently resets to an empty
# SQLite file and any data added since the last deploy (admin edits, real
# signups, payments...) is lost. Two ways to point this at PostgreSQL:
#   1. DATABASE_URL — a single connection string, e.g.
#      postgres://user:password@host:port/dbname (this is what Render's
#      "Internal Database URL" gives you when you create a Postgres
#      instance — the simplest option).
#   2. DATABASE_NAME/USER/PASSWORD/HOST/PORT — the same thing split into
#      separate variables, for hosts that don't provide a single URL.
# DATABASE_URL takes priority if both are set.
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)
    }
elif os.environ.get("DATABASE_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DATABASE_NAME"),
            "USER": os.environ.get("DATABASE_USER", "postgres"),
            "PASSWORD": os.environ.get("DATABASE_PASSWORD", ""),
            "HOST": os.environ.get("DATABASE_HOST", "localhost"),
            "PORT": os.environ.get("DATABASE_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------------
# Custom user model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fr-fr"
# All datetimes are stored in UTC and converted for display client-side
# (see spec section 5.7 — Time Zones).
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
# STATIC_ROOT : dossier où `manage.py collectstatic` rassemble tous les
# fichiers statiques avant le déploiement (nécessaire en prod — pas utilisé
# en dev où Django sert les fichiers directement).
STATIC_ROOT = BASE_DIR / "staticfiles"
# CompressedManifestStaticFilesStorage exige que collectstatic ait tourné
# (sinon "ManifestFileNotFoundError" au premier chargement) — on ne
# l'active donc qu'en prod (DEBUG=False) ; en dev Django garde son
# comportement par défaut.
if not DEBUG:
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# CORS (adjust before production — this allows the frontend dev server)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Anti brute-force / anti-spam (spec: pas de limite avant, ajouté après
    # revue de sécurité). "anon"/"user" s'appliquent à toute l'API par IP ou
    # par compte ; les scopes ci-dessous sont volontairement plus stricts
    # pour login/register/password-reset, ciblés par ScopedRateThrottle sur
    # les vues correspondantes dans views.py.
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "2000/hour",
        "login": "10/minute",
        "register": "10/hour",
        "password_reset": "5/hour",
        "newsletter": "20/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# ---------------------------------------------------------------------------
# Stripe (Module 3 — payments & subscriptions)
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# Stripe Price ID for the recurring Maths video capsule subscription —
# create this in the Stripe dashboard and paste the price_... id here.
STRIPE_SUBSCRIPTION_PRICE_ID = os.environ.get("STRIPE_SUBSCRIPTION_PRICE_ID", "")
# Where Stripe Checkout redirects back to after payment.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# ---------------------------------------------------------------------------
# Konnect (paiement en Tunisie — Stripe ne prend pas en charge le dinar
# tunisien, devise à change restreint ; Konnect est une passerelle locale
# agréée par la Banque Centrale de Tunisie. Voir core/services/konnect.py.
# Sandbox : https://dashboard.sandbox.konnect.network — Prod :
# https://dashboard.konnect.network. IMPORTANT : Konnect ne gère aucun
# prélèvement récurrent (contrairement à Stripe) — chaque paiement est
# ponctuel, y compris pour un abonnement mensuel de groupe. Les tarifs
# tunisiens (DT) sont indépendants des tarifs EUR, pas convertis via un
# taux de change — voir PricingRate.price_per_hour_millimes_tnd.
# ---------------------------------------------------------------------------
KONNECT_API_KEY = os.environ.get("KONNECT_API_KEY", "")
KONNECT_WALLET_ID = os.environ.get("KONNECT_WALLET_ID", "")
KONNECT_SANDBOX = os.environ.get("KONNECT_SANDBOX", "True") == "True"

# Paiement Tunisie : décision produit — pas d'intermédiaire (Konnect, Flouci
# ou autre), paiement 100% manuel. L'élève tunisien contacte l'admin par
# e-mail (CONTACT_EMAIL) au moment de payer, l'admin lui communique son RIB
# hors plateforme, puis approuve manuellement dans l'admin Django une fois
# le virement reçu (EnrollmentAdmin / SeriesMembershipAdmin — action
# "Marquer comme payé"). L'intégration Konnect ci-dessus reste dans le code
# mais n'est plus appelée pour la Tunisie (voir EnrollmentViewSet
# .create_checkout_session / SeriesMembershipViewSet.checkout).
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "succes@reussir-mon-bac.com")

# ---------------------------------------------------------------------------
# Brevo (ex-Sendinblue) — inscription à la newsletter depuis le footer de la
# page d'accueil (voir core/services/brevo.py / PublicNewsletterSubscribeView).
# BREVO_API_KEY : clé API v3, récupérable dans Brevo > Paramètres > Clés API.
# BREVO_NEWSLETTER_LIST_ID : identifiant numérique de la liste Brevo à
# laquelle rattacher les inscrits (Brevo > Contacts > Listes) — facultatif,
# laisse le contact créé sans liste si vide.
# ---------------------------------------------------------------------------
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_NEWSLETTER_LIST_ID = os.environ.get("BREVO_NEWSLETTER_LIST_ID", "")

# ---------------------------------------------------------------------------
# Email (booking confirmations, reminders, cancellations — spec 5.6)
# ---------------------------------------------------------------------------
# Defaults to printing emails to the console — zero setup for local dev.
# Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the
# EMAIL_HOST_* vars below to send real emails (e.g. via SendGrid's SMTP relay).
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp-relay.brevo.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')

# ---------------------------------------------------------------------------
# Video conferencing — provider priority is Google Meet > Daily.co > Jitsi.
# See core/services/video.py and core/services/google_meet.py.
# ---------------------------------------------------------------------------
DAILY_API_KEY = os.environ.get("DAILY_API_KEY", "")

# Google Meet (via Calendar API + a Workspace service account with
# domain-wide delegation, or an OAuth Client as a fallback). Auto-detected
# below from a `google_credentials.json` file dropped at the project root
# (next to manage.py) — no env var required for that part. Explicit env
# vars, if set, always win over the auto-detected values.
# GOOGLE_SERVICE_ACCOUNT_FILE: path to the JSON key downloaded for the
#   service account (keep it out of version control).
# GOOGLE_WORKSPACE_ORGANIZER_EMAIL: the Workspace mailbox that will own
#   every session's calendar event/Meet room (e.g. cours@your-domain.fr).
#   The service account impersonates this mailbox via domain-wide
#   delegation — it must be a real, licensed user in your Workspace.
#   This one can't be auto-detected from the credentials file — it must
#   always be set explicitly, either here or in your .env.
_GOOGLE_CREDENTIALS_FILE = BASE_DIR / "google_credentials.json"
_google_credentials_data = {}
if _GOOGLE_CREDENTIALS_FILE.exists():
    try:
        _google_credentials_data = json.loads(_GOOGLE_CREDENTIALS_FILE.read_text())
    except (OSError, ValueError):
        # Unreadable or not valid JSON — ignored here; googleapiclient will
        # raise a clearer error later if a call actually needs this file.
        _google_credentials_data = {}

GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
if not GOOGLE_SERVICE_ACCOUNT_FILE and _google_credentials_data.get("type") == "service_account":
    # google_credentials.json is a service-account key (has a "type" key
    # equal to "service_account") — use it directly.
    GOOGLE_SERVICE_ACCOUNT_FILE = str(_GOOGLE_CREDENTIALS_FILE)

GOOGLE_WORKSPACE_ORGANIZER_EMAIL = os.environ.get("GOOGLE_WORKSPACE_ORGANIZER_EMAIL", "")

# Fallback auth mode, used instead of the service account above when your
# Google Cloud org blocks service-account-key creation (the
# `iam.managed.disableServiceAccountKeyCreation` org policy), or when you
# don't have Google Workspace admin access to set up domain-wide
# delegation at all (e.g. testing with a plain @gmail.com account).
# GOOGLE_OAUTH_CLIENT_ID/SECRET are auto-filled below from
# google_credentials.json when it's an OAuth Client file (has an
# "installed" or "web" key instead of "type": "service_account") — that's
# what Google Cloud Console downloads when you create an "OAuth client ID"
# instead of a "Service account". GOOGLE_OAUTH_REFRESH_TOKEN can NOT be
# auto-detected — get it by running, once:
#   python manage.py get_google_oauth_token
# (it defaults to reading google_credentials.json; pass a path explicitly
# if yours is named differently) — see README.md.
_oauth_client_block = _google_credentials_data.get("installed") or _google_credentials_data.get("web") or {}
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or _oauth_client_block.get("client_id", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or _oauth_client_block.get("client_secret", "")
GOOGLE_OAUTH_REFRESH_TOKEN = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY'),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
}

# Configuration universelle (Django 3 et Django 4+)
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

STORAGES = {
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}