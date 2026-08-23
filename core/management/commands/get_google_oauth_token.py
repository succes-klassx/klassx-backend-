"""
One-time interactive authorization for Google Meet's OAuth fallback mode
(used when your Google Cloud org blocks service-account key creation —
see core/services/google_meet.py). Works with either an "installed"
(Desktop app) or a "web" (Web application) OAuth Client JSON — KLASSX
auto-detects which one you have.

Run this ONCE, from a computer with a web browser (not a headless server):

    python manage.py get_google_oauth_token

(reads google_credentials.json at the project root by default; pass a
path explicitly if yours is named/located differently:
    python manage.py get_google_oauth_token /path/to/client_secret_....json
)

--- If your client is type "web" (Web application) ---
Unlike "installed" (Desktop app) clients, Google requires the redirect
URI to be pre-registered exactly — a random port won't be accepted. So:

  1. In Google Cloud Console > APIs & Services > Credentials, open your
     OAuth 2.0 Client ID (the "web" one).
  2. Under "Authorized redirect URIs", add:
         http://localhost:8080/
     (or any port you like — just pass --port to match, e.g. --port 8081).
  3. Save, then run this command (add --port if you used a different one).

This command detects your client type automatically and tells you if a
redirect URI is missing before it opens the browser.

--- After authorizing ---
It opens a browser window asking you to sign in with the Workspace
mailbox that should own every KLASSX session's Calendar event/Meet link
(the same address you'll put in GOOGLE_WORKSPACE_ORGANIZER_EMAIL) and to
approve access. Once approved, it prints a **refresh token** to the
terminal — copy that, plus the client ID/secret, into your .env:

    GOOGLE_OAUTH_CLIENT_ID=...
    GOOGLE_OAUTH_CLIENT_SECRET=...
    GOOGLE_OAUTH_REFRESH_TOKEN=...

The refresh token does not expire on its own, so this is a true one-time
step — you won't need to re-run it unless you revoke access or change the
organizer mailbox.
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.google_meet import SCOPES


class Command(BaseCommand):
    help = "One-time interactive OAuth authorization for the Google Meet integration."

    def add_arguments(self, parser):
        parser.add_argument(
            "client_secret_path",
            nargs="?",
            default=None,
            help="Path to the OAuth Client JSON file downloaded from Google Cloud Console. "
                 "Defaults to google_credentials.json at the project root if omitted.",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8080,
            help="Local port to receive the OAuth redirect on (default: 8080). For a 'web' "
                 "type client, this MUST match a redirect URI you registered in Cloud "
                 "Console as http://localhost:<port>/. Ignored for 'installed' clients, "
                 "which accept any port automatically.",
        )

    def handle(self, *args, **options):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise CommandError(
                "google-auth-oauthlib is not installed. Run: "
                "pip install google-auth-oauthlib --break-system-packages"
            )

        client_secret_path = options["client_secret_path"] or str(settings.BASE_DIR / "google_credentials.json")
        if not os.path.exists(client_secret_path):
            raise CommandError(
                f"No file found at {client_secret_path}. Pass the path to your OAuth Client "
                "JSON file explicitly, or drop it at the project root as google_credentials.json."
            )

        with open(client_secret_path) as f:
            client_config = json.load(f)

        port = options["port"]
        client_type = "web" if "web" in client_config else "installed" if "installed" in client_config else None

        if client_type is None:
            raise CommandError(
                f"{client_secret_path} doesn't look like an OAuth Client JSON (expected a "
                "top-level 'web' or 'installed' key — got a service account key instead?). "
                "See README.md \"Google Meet / Google Workspace setup\"."
            )

        if client_type == "web":
            registered = client_config["web"].get("redirect_uris", [])
            expected = f"http://localhost:{port}/"
            # Google matches redirect URIs exactly, trailing slash included,
            # so check for both forms before failing loudly.
            if expected not in registered and expected.rstrip("/") not in registered:
                raise CommandError(
                    "Your OAuth Client is a 'web' type, which requires the redirect URI to be "
                    f"registered in advance. {expected} is not in your client's Authorized "
                    "redirect URIs. Go to Google Cloud Console > APIs & Services > Credentials, "
                    "open this OAuth Client, add:\n\n"
                    f"    {expected}\n\n"
                    "under 'Authorized redirect URIs', save, wait a minute for it to propagate, "
                    "then re-run this command (add --port N if you registered a different port)."
                )
            self.stdout.write(f"Web application client detected — using redirect URI {expected}")
        else:
            self.stdout.write("Desktop app client detected — any local port is accepted automatically.")

        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        # For "web" clients this binds to the exact registered port; for
        # "installed" clients Google accepts any loopback port, so the
        # fixed port here is harmless (and keeps the command's behavior
        # predictable either way).
        credentials = flow.run_local_server(port=port)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Authorization successful! Add these to your .env:"))
        self.stdout.write("")
        self.stdout.write(f"GOOGLE_OAUTH_CLIENT_ID={credentials.client_id}")
        self.stdout.write(f"GOOGLE_OAUTH_CLIENT_SECRET={credentials.client_secret}")
        self.stdout.write(f"GOOGLE_OAUTH_REFRESH_TOKEN={credentials.refresh_token}")
        self.stdout.write("")
