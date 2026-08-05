"""One-time Google OAuth2 login for the conversation registry (google_sheets).

Authenticates with YOUR Google account (no service account needed) and saves a
refresh token so the bot can append rows to the spreadsheet.

Prerequisites
-------------
* ``CONVERSATION_LOG_GOOGLE_AUTH_MODE=oauth_user`` in ``.env``.
* ``CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE`` points to an OAuth 2.0 client
  secret of type **Desktop app**, downloaded from Google Cloud Console
  (the Google Sheets API must be enabled for the project).
* ``CONVERSATION_LOG_GOOGLE_TOKEN_FILE`` is where the token will be stored.

Run inside the container (interactive terminal):

    docker compose exec -it chatbot python scripts/sheets_oauth_setup.py

It prints a URL: open it in any browser, log in with the account that owns (or
is shared on) the spreadsheet, copy the authorization code back into the
terminal. The token is saved and reused automatically from then on.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings

SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def main() -> int:
    config = load_settings().conversation_log

    if config.google_auth_mode != "oauth_user":
        print("CONVERSATION_LOG_GOOGLE_AUTH_MODE must be 'oauth_user' in .env")
        return 1
    if not config.google_client_secret_file:
        print("Set CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE in .env")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "Missing dependency. Rebuild the container (or pip install "
            "gspread google-auth google-auth-oauthlib)."
        )
        return 1

    token_path = Path(config.google_token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Client secret : {config.google_client_secret_file}")
    print(f"Token will be : {token_path}")
    if config.spreadsheet_id:
        print(f"Spreadsheet   : {config.spreadsheet_id}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(
        config.google_client_secret_file, [SCOPE]
    )
    creds = flow.run_console()

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nToken saved to {token_path}")

    # Quick verification that the credentials can open the spreadsheet.
    try:
        import gspread

        client = gspread.oauth(
            credentials_filename=config.google_client_secret_file,
            authorized_user_filename=str(token_path),
        )
        if config.spreadsheet_id:
            client.open_by_key(config.spreadsheet_id)
            print(f"Access OK - the bot can open spreadsheet {config.spreadsheet_id}.")
        else:
            print("Access OK (no spreadsheet_id configured yet).")
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        print(f"WARNING: token saved, but the spreadsheet check failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
