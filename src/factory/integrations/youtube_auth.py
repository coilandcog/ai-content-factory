"""Google sign-in (OAuth 2.0, "Desktop app" client) for the channel's YouTube account
(INTEGRATIONS.md §4, ROADMAP 2.2, D-102).

The client file from task 0.4 and the token this writes both live in secrets/<slug>/ (git-ignored).
The token lets the factory act for the channel without the password; it is never printed or
logged, and the file is readable only by the owner (chmod 600).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from factory.errors import ConfigError

# Every YouTube permission the factory will need, asked once: upload (2.2/2.3), read the channel
# (2.2), read analytics (2.5). Google refuses YouTube and Drive scopes in one request ("scopes that
# cannot be requested together"), so the Drive backup (2.10) gets its own sign-in and token.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
CLIENT_FILE = "google_client_secret.json"
TOKEN_FILE = "google_token.json"
PORT = 8765  # the sign-in page sends the browser back to http://localhost:8765 (WSL forwards it)


def client_path(secrets_dir: Path, slug: str) -> Path:
    return secrets_dir / slug / CLIENT_FILE


def token_path(secrets_dir: Path, slug: str) -> Path:
    return secrets_dir / slug / TOKEN_FILE


def sign_in(
    client_file: Path,
    *,
    flow_from_file: Callable[..., Any] = InstalledAppFlow.from_client_secrets_file,
    port: int = PORT,
) -> Credentials:
    """Print Google's sign-in link and wait until the owner has allowed access in the browser."""
    if not client_file.is_file():
        raise ConfigError(
            f"{client_file} is missing: download the Desktop OAuth client from Google Cloud "
            "Console (task 0.4) and save it there"
        )
    flow = flow_from_file(str(client_file), scopes=list(SCOPES))
    return flow.run_local_server(
        port=port,
        open_browser=False,  # WSL can't open the Windows browser itself
        authorization_prompt_message=(
            "\nOpen this link in your Windows browser, choose the Coil & Cog channel and allow "
            "access:\n\n{url}\n"
        ),
        success_message="Coil & Cog factory: signed in. You can close this tab.",
    )


def save_token(credentials: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json())
    os.chmod(path, 0o600)  # owner-only: the token acts as the channel


def load_credentials(
    path: Path, *, refresh: Callable[[Credentials], None] | None = None
) -> Credentials:
    """The saved token, refreshed (and saved again) when it has expired."""
    if not path.is_file():
        raise ConfigError("no YouTube sign-in yet: run `uv run factory auth youtube`")
    # No scopes argument: it would replace the saved ones and hide a token that lacks permissions.
    credentials = Credentials.from_authorized_user_file(str(path))
    if not set(SCOPES) <= set(credentials.scopes or ()):
        raise ConfigError("the saved sign-in lacks permissions: run `uv run factory auth youtube`")
    if not credentials.valid:
        if not credentials.refresh_token:
            raise ConfigError(
                "the saved sign-in can't be renewed: run `uv run factory auth youtube`"
            )
        try:
            (refresh or (lambda c: c.refresh(Request())))(credentials)
        except RefreshError as exc:
            # e.g. access revoked in the Google account, or the consent screen is in "Testing"
            raise ConfigError(
                "Google refused to renew the sign-in: run `uv run factory auth youtube` again"
            ) from exc
        save_token(credentials, path)
    return credentials
