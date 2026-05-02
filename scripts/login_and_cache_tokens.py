#!/usr/bin/env python3
"""Interactive Polestar OAuth login → caches both tokens for later scripts.

Goes through the full OAuth + PKCE + OTP flow for both clients (web token
and PCCS 2SV token) and writes the resulting access + refresh tokens to a
JSON file under your system temp directory. Other scripts in this folder
(``diagnose_apis.py --use-cached``, ``test_cep_live.py``, etc.) read from
the same file.

Usage::

    .venv/bin/python scripts/login_and_cache_tokens.py

Reads credentials from environment variables, falling back to interactive
prompts:

* ``POLESTAR_EMAIL``    — Polestar ID email
* ``POLESTAR_PASSWORD`` — Polestar ID password

You will be prompted for the OTP code that Polestar emails to you for the
PCCS 2SV step. Leave it blank to skip PCCS login (the web token alone is
enough for diagnose_apis.py to probe the read-only endpoints).

The cache file path is printed on success. **It contains live OAuth
tokens** — treat it as a credential. Delete it (or run this script again)
when you're done.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import tempfile

# Add project root to path so we can import the custom component.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.polestar_soc.const import (  # noqa: E402
    PCCS_ACR_VALUES,
    PCCS_CLIENT_ID,
    PCCS_REDIRECT_URI,
    PCCS_SCOPE,
)
from custom_components.polestar_soc.coordinator import PolestarAPI  # noqa: E402

TOKEN_CACHE = os.path.join(tempfile.gettempdir(), "polestar_tokens.json")


def _save_tokens(
    *,
    web_token: str | None,
    web_refresh: str | None,
    pccs_token: str | None,
    pccs_refresh: str | None,
) -> None:
    data = {
        "web_token": web_token,
        "web_refresh": web_refresh,
        "pccs_token": pccs_token,
        "pccs_refresh": pccs_refresh,
    }
    # 0o600 — readable only by the current user.
    fd = os.open(TOKEN_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def main() -> int:
    email = os.environ.get("POLESTAR_EMAIL") or input("Polestar email: ").strip()
    password = os.environ.get("POLESTAR_PASSWORD") or getpass.getpass(
        "Polestar password: "
    )

    # ---- Web token (no OTP) ----
    print("\n[1/2] Logging in for web token...", file=sys.stderr)
    web_api = PolestarAPI()
    try:
        web_api.login(email, password)
    except Exception as err:
        print(f"  FAILED: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print("  OK", file=sys.stderr)

    # ---- PCCS 2SV token (OTP) ----
    print("\n[2/2] Logging in for PCCS 2SV token (will trigger OTP email)...",
          file=sys.stderr)
    pccs_api = PolestarAPI(client_id=PCCS_CLIENT_ID, redirect_uri=PCCS_REDIRECT_URI)

    try:
        result = pccs_api.login_start_2fa(
            email, password, scope=PCCS_SCOPE, acr_values=PCCS_ACR_VALUES
        )
    except Exception as err:
        print(f"  FAILED at OTP request: {type(err).__name__}: {err}",
              file=sys.stderr)
        result = None

    pccs_token: str | None = None
    pccs_refresh: str | None = None

    if result is None:
        print("  Skipping PCCS token (web token will be saved alone).",
              file=sys.stderr)
    elif result.get("needs_otp"):
        otp = input("  OTP code (from Polestar email, blank to skip): ").strip()
        if otp:
            try:
                tokens = pccs_api.login_complete_2fa(result["_session_state"], otp)
                pccs_token = tokens.get("access_token")
                pccs_refresh = tokens.get("refresh_token")
                print("  OK", file=sys.stderr)
            except Exception as err:
                print(f"  FAILED at OTP submit: {type(err).__name__}: {err}",
                      file=sys.stderr)
        else:
            print("  Skipped (no OTP entered).", file=sys.stderr)
    else:
        # Server completed without OTP (some accounts skip 2SV).
        pccs_token = result.get("access_token")
        pccs_refresh = result.get("refresh_token")
        print("  OK (no OTP needed)", file=sys.stderr)

    _save_tokens(
        web_token=web_api.access_token,
        web_refresh=web_api.refresh_token,
        pccs_token=pccs_token,
        pccs_refresh=pccs_refresh,
    )

    print(f"\nSaved tokens to {TOKEN_CACHE} (mode 0600).", file=sys.stderr)
    print("Delete this file when you're done — it holds live OAuth tokens.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
