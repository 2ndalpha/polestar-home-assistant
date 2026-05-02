#!/usr/bin/env python3
"""Polestar API diagnostic probe — gather data for issue triage.

Probes every API layer (GraphQL, PCCS gRPC, CEP gRPC) for one VIN and
prints a redacted, table-formatted report suitable for pasting into a
GitHub issue.  Built specifically to help narrow down whether an outage
is account-specific or fleet-wide (see GitHub issue #20 for the
canonical PCCS PERMISSION_DENIED scenario).

Usage::

    .venv/bin/python scripts/diagnose_apis.py
    .venv/bin/python scripts/diagnose_apis.py --vin YS3...
    .venv/bin/python scripts/diagnose_apis.py --unredacted --out report.txt

Reads credentials from environment variables, falling back to interactive
prompts:
    POLESTAR_EMAIL    — Polestar ID email
    POLESTAR_PASSWORD — Polestar ID password

Dependency note: this script imports from
``custom_components.polestar_soc.*`` which depends on
``homeassistant.exceptions``.  Run it from a Python environment that
has Home Assistant installed (the project's ``.venv`` is fine).

The report is **redacted by default**: the email, JWT ``sub`` claim,
and the last 6 characters of the VIN are masked.  Pass ``--unredacted``
to see the raw values (and a warning is printed first).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Add project root to path so we can import the custom component.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grpc  # noqa: E402

from custom_components.polestar_soc.cep import CepClient  # noqa: E402
from custom_components.polestar_soc.const import (  # noqa: E402
    PCCS_ACR_VALUES,
    PCCS_CLIENT_ID,
    PCCS_REDIRECT_URI,
    PCCS_SCOPE,
)
from custom_components.polestar_soc.coordinator import PolestarAPI  # noqa: E402
from custom_components.polestar_soc.pccs import PccsClient  # noqa: E402

# --- Probe machinery -------------------------------------------------------


@dataclass
class ProbeResult:
    """Single endpoint probe outcome for the report table."""

    layer: str
    endpoint: str
    status: str  # "OK" or a gRPC status code name or exception type
    latency_ms: int
    message: str  # short error message (empty on success)


def _probe(layer: str, endpoint: str, fn: Callable[[], Any]) -> ProbeResult:
    start = time.monotonic()
    try:
        fn()
    except grpc.RpcError as err:
        latency = int((time.monotonic() - start) * 1000)
        try:
            code = err.code()
            status = code.name if code else "RPC_ERROR"
        except Exception:
            status = "RPC_ERROR"
        try:
            details = err.details() or ""
        except Exception:
            details = ""
        return ProbeResult(layer, endpoint, status, latency, details)
    except Exception as err:
        latency = int((time.monotonic() - start) * 1000)
        return ProbeResult(layer, endpoint, type(err).__name__, latency, str(err))
    latency = int((time.monotonic() - start) * 1000)
    return ProbeResult(layer, endpoint, "OK", latency, "")


# --- Auth ------------------------------------------------------------------


@dataclass
class Tokens:
    """Container for the two OAuth tokens this integration uses."""

    web_token: str | None
    pccs_token: str | None
    web_login_error: str | None
    pccs_login_error: str | None


def _login(email: str, password: str, otp_provider: Callable[[], str | None] | None) -> Tokens:
    """Perform both the web-token and PCCS-token logins.

    Returns a ``Tokens`` containing whichever tokens were obtained.
    Login failures are captured per-token so we can distinguish
    "auth failed at login" from "auth ok, endpoints rejected".
    """
    web_err: str | None = None
    pccs_err: str | None = None
    web_api = PolestarAPI()
    pccs_api = PolestarAPI(client_id=PCCS_CLIENT_ID, redirect_uri=PCCS_REDIRECT_URI)
    pccs_api._otp_callback = otp_provider  # type: ignore[attr-defined]

    try:
        web_api.login(email, password)
    except Exception as err:
        web_err = f"{type(err).__name__}: {err}"

    try:
        pccs_api.login(email, password, scope=PCCS_SCOPE, acr_values=PCCS_ACR_VALUES)
    except Exception as err:
        pccs_err = f"{type(err).__name__}: {err}"

    return Tokens(
        web_token=web_api.access_token,
        pccs_token=pccs_api.access_token,
        web_login_error=web_err,
        pccs_login_error=pccs_err,
    )


# --- JWT helpers -----------------------------------------------------------


def _b64url_decode(data: str) -> bytes:
    """Decode URL-safe base64 with padding tolerance."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _decode_jwt_claims(token: str) -> dict:
    """Parse a JWT and return ``{"header": {...}, "payload": {...}}``.

    Returns an error dict on parse failure rather than raising so the
    diagnostic flow continues.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {"error": "not a JWT (too few segments)"}
        return {
            "header": json.loads(_b64url_decode(parts[0])),
            "payload": json.loads(_b64url_decode(parts[1])),
        }
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}


# --- Redaction -------------------------------------------------------------


def _redact_email(email: str, redact: bool) -> str:
    if not redact or not email:
        return email
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    visible = local[:1]
    return f"{visible}***@{domain}"


def _redact_vin(vin: str, redact: bool) -> str:
    if not redact or not vin:
        return vin
    if len(vin) <= 6:
        return "*" * len(vin)
    return vin[:-6] + "******"


def _redact_claims(claims: dict, redact: bool) -> dict:
    """Mask PII in a JWT claims dict."""
    if not redact or "payload" not in claims:
        return claims
    payload = dict(claims["payload"])
    if "sub" in payload and isinstance(payload["sub"], str):
        sub = payload["sub"]
        payload["sub"] = sub[:4] + "***" if len(sub) > 4 else "***"
    if "email" in payload and isinstance(payload["email"], str):
        payload["email"] = _redact_email(payload["email"], True)
    return {**claims, "payload": payload}


# --- Report writer ---------------------------------------------------------


class _Report:
    """Buffered report writer that mirrors output to stdout and an optional file."""

    def __init__(self, out_path: str | None) -> None:
        self._lines: list[str] = []
        self._out_path = out_path

    def line(self, text: str = "") -> None:
        self._lines.append(text)
        print(text)

    def section(self, title: str) -> None:
        self.line()
        self.line(f"== {title} ==")

    def flush(self) -> None:
        if self._out_path:
            with open(self._out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._lines) + "\n")


def _format_table(results: list[ProbeResult]) -> list[str]:
    headers = ("Layer", "Endpoint", "Status", "Latency", "Message")
    rows = [
        (r.layer, r.endpoint, r.status, f"{r.latency_ms}ms", r.message)
        for r in results
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    sep = "  ".join("-" * w for w in widths)
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)),
        sep,
    ]
    for row in rows:
        lines.append(
            "  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True))
        )
    return lines


# --- Probes ----------------------------------------------------------------


def _probes_for(
    web_token: str,
    pccs_token: str | None,
    web_api: PolestarAPI,
    vin: str,
) -> list[ProbeResult]:
    """Run every read RPC and collect ProbeResult rows."""
    results: list[ProbeResult] = []

    # GraphQL probes ------------------------------------------------------
    results.append(_probe("graphql", "getConsumerCarsV2", web_api.get_vehicles))
    # carTelematicsV2.battery and .odometer are both queried by
    # get_telematics; probe each via direct query so we can report them
    # individually.
    from custom_components.polestar_soc.const import (
        QUERY_TELEMATICS_BATTERY,
        QUERY_TELEMATICS_ODOMETER,
    )
    results.append(
        _probe(
            "graphql",
            "carTelematicsV2.battery",
            lambda: web_api._graphql(QUERY_TELEMATICS_BATTERY, {"vins": [vin]}),
        )
    )
    results.append(
        _probe(
            "graphql",
            "carTelematicsV2.odometer",
            lambda: web_api._graphql(QUERY_TELEMATICS_ODOMETER, {"vins": [vin]}),
        )
    )

    # PCCS probes ---------------------------------------------------------
    pccs_client = PccsClient(access_token=web_token, write_access_token=pccs_token)
    try:
        results.append(_probe("pccs", "TargetSoc", lambda: pccs_client.get_target_soc(vin)))
        results.append(_probe("pccs", "AmpLimit", lambda: pccs_client.get_amp_limit(vin)))
        results.append(
            _probe(
                "pccs",
                "GlobalChargeTimer",
                lambda: pccs_client.get_global_charge_timer(vin),
            )
        )
        results.append(
            _probe(
                "pccs",
                "ParkingClimateTimers",
                lambda: pccs_client.get_parking_climate_timers(vin),
            )
        )
        results.append(
            _probe(
                "pccs",
                "ParkingClimateTimerSettings",
                lambda: pccs_client.get_parking_climate_timer_settings(vin),
            )
        )
    finally:
        pccs_client.close()

    # CEP probes ----------------------------------------------------------
    cep_client = CepClient(access_token=web_token, write_access_token=pccs_token)
    try:
        results.append(
            _probe("cep", "Battery", lambda: cep_client.get_battery(vin))
        )
        results.append(
            _probe(
                "cep",
                "ParkingClimatization",
                lambda: cep_client.get_parking_climatization(vin),
            )
        )
        results.append(
            _probe("cep", "Exterior", lambda: cep_client.get_exterior(vin))
        )
        results.append(
            _probe("cep", "Availability", lambda: cep_client.get_availability(vin))
        )
        results.append(_probe("cep", "Health", lambda: cep_client.get_health(vin)))
        results.append(
            _probe("cep", "Location", lambda: cep_client.get_location(vin))
        )
    finally:
        cep_client.close()

    return results


def _summary_line(results: list[ProbeResult], tokens: Tokens) -> str:
    """One-line summary used to distinguish auth-failure from API-failure."""
    if tokens.web_login_error and tokens.pccs_login_error:
        return "AUTH FAILED at both web + PCCS login — check credentials."
    if tokens.web_login_error:
        return "AUTH FAILED at web login — check credentials."

    by_layer: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_layer.setdefault(r.layer, []).append(r)

    bad: list[str] = []
    for layer, rs in by_layer.items():
        failed = [r for r in rs if r.status != "OK"]
        if failed:
            codes = sorted({r.status for r in failed})
            bad.append(f"{layer}={','.join(codes)}")
    if not bad:
        return "All API layers OK."
    return "API rejected (auth ok, endpoints failing): " + "; ".join(bad)


# --- Main ------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Polestar API diagnostic probe.")
    parser.add_argument(
        "--vin",
        help="VIN to probe (default: first vehicle on the account).",
    )
    parser.add_argument(
        "--unredacted",
        action="store_true",
        help="Show email, JWT sub claim, and full VIN in output. "
        "Default: redacted.",
    )
    parser.add_argument(
        "--out",
        help="Write the report to this file (in addition to stdout).",
    )
    parser.add_argument(
        "--use-cached",
        action="store_true",
        help="Skip the OAuth login and read tokens from "
        "$TMPDIR/polestar_tokens.json (run scripts/login_and_cache_tokens.py "
        "first to populate it).",
    )
    args = parser.parse_args()
    redact = not args.unredacted

    if args.unredacted:
        print("WARNING: --unredacted will print PII (email, JWT sub, VIN). "
              "Review before sharing.\n", file=sys.stderr)

    report = _Report(args.out)

    if args.use_cached:
        cache_path = os.path.join(tempfile.gettempdir(), "polestar_tokens.json")
        try:
            with open(cache_path) as f:
                cached = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as err:
            print(
                f"ERROR: cannot read token cache {cache_path}: {err}\n"
                f"Run scripts/login_and_cache_tokens.py first.",
                file=sys.stderr,
            )
            return 1
        tokens = Tokens(
            web_token=cached.get("web_token"),
            pccs_token=cached.get("pccs_token"),
            web_login_error=None,
            pccs_login_error=None,
        )
        report.line("Polestar API diagnostic — gathered for issue triage")
        report.line(f"Tokens loaded from {cache_path}")
    else:
        email = os.environ.get("POLESTAR_EMAIL") or input("Polestar email: ").strip()
        password = os.environ.get("POLESTAR_PASSWORD") or getpass.getpass(
            "Polestar password: "
        )

        def otp_provider() -> str | None:
            return input(
                "PCCS 2SV OTP code (or empty to skip PCCS login): "
            ).strip() or None

        report.line("Polestar API diagnostic — gathered for issue triage")
        report.line(f"User: {_redact_email(email, redact)}")
        tokens = _login(email, password, otp_provider)

    report.section("Login outcome")
    report.line(
        f"  web token  : {'OK' if tokens.web_token else 'FAILED'}"
        + (f" — {tokens.web_login_error}" if tokens.web_login_error else "")
    )
    report.line(
        f"  PCCS token : {'OK' if tokens.pccs_token else 'FAILED'}"
        + (f" — {tokens.pccs_login_error}" if tokens.pccs_login_error else "")
    )

    if not tokens.web_token:
        report.line()
        report.line(_summary_line([], tokens))
        report.flush()
        return 1

    # Vehicle list ------------------------------------------------------------
    web_api = PolestarAPI()
    web_api.access_token = tokens.web_token

    try:
        vehicles = web_api.get_vehicles()
    except Exception as err:
        report.section("Vehicle list")
        report.line(f"  FAILED: {type(err).__name__}: {err}")
        report.line()
        report.line("Cannot probe further — no vehicle to probe against.")
        report.flush()
        return 1

    if args.vin:
        vin = args.vin
    elif vehicles:
        vin = vehicles[0]["vin"]
    else:
        report.line("No vehicles found on this account; nothing to probe.")
        report.flush()
        return 1

    report.section("Vehicle")
    report.line(f"  VIN          : {_redact_vin(vin, redact)}")
    if vehicles:
        v = next((v for v in vehicles if v["vin"] == vin), vehicles[0])
        report.line(f"  Model        : {v.get('modelName', 'unknown')}")
        report.line(f"  Model year   : {v.get('modelYear', 'unknown')}")
        # remoteControlType would indicate backend reassignment if present.
        rct = v.get("remoteControlType")
        if rct:
            report.line(f"  remoteControlType: {rct}")

    # Probes ------------------------------------------------------------------
    results = _probes_for(tokens.web_token, tokens.pccs_token, web_api, vin)

    report.section("Endpoint probes")
    for line in _format_table(results):
        report.line(f"  {line}")

    # JWT claims --------------------------------------------------------------
    report.section("JWT claims (web token)")
    claims = _redact_claims(_decode_jwt_claims(tokens.web_token), redact)
    report.line(json.dumps(claims, indent=2, sort_keys=True, default=str))

    if tokens.pccs_token:
        report.section("JWT claims (PCCS token)")
        pccs_claims = _redact_claims(_decode_jwt_claims(tokens.pccs_token), redact)
        report.line(json.dumps(pccs_claims, indent=2, sort_keys=True, default=str))

    # Summary -----------------------------------------------------------------
    report.section("Summary")
    report.line(_summary_line(results, tokens))

    report.flush()
    return 0 if all(r.status == "OK" for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(2)
