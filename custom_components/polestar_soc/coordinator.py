"""DataUpdateCoordinator and API client for Polestar."""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import os
import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import grpc
import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cep import CepClient
from .const import (
    API_URL,
    CLIENT_ID,
    DOMAIN,
    OIDC_AUTH_URL,
    OIDC_BASE_URL,
    OIDC_TOKEN_URL,
    PCCS_CLIENT_ID,
    PCCS_REDIRECT_URI,
    QUERY_GET_CARS,
    QUERY_TELEMATICS_BATTERY,
    QUERY_TELEMATICS_ODOMETER,
    REDIRECT_URI,
    SCAN_INTERVAL,
    SCOPE,
)
from .pccs import PccsClient

_LOGGER = logging.getLogger(__name__)

HTTP_TIMEOUT = 30

# Layers tracked by _LayerHealth.
LAYER_PCCS = "pccs"
LAYER_CEP = "cep"
LAYER_GRAPHQL = "graphql"
_ALL_LAYERS = (LAYER_PCCS, LAYER_CEP, LAYER_GRAPHQL)

# gRPC status codes that indicate the token was rejected by the backend
# and warrant a single refresh-and-retry attempt.
_AUTH_GRPC_CODES: frozenset[grpc.StatusCode] = frozenset(
    {grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.UNAUTHENTICATED}
)

# Sentinel returned by the per-call wrapper to mark "call failed" so the
# caller can distinguish failure from a getter that legitimately returns
# None (defensive — currently no getter does, but the contract is now
# explicit).
_FAILED: object = object()


class _GrpcAuthError(Exception):
    """Internal signal: a gRPC call returned PERMISSION_DENIED / UNAUTHENTICATED.

    Raised by ``_do_fetch`` when the *first* such failure of an update cycle
    is observed.  Caught in ``_async_update_data`` to trigger one token
    refresh + channel reset + retry.  Carries the layer name so the retry
    can record which layer triggered it.
    """

    def __init__(self, layer: str) -> None:
        super().__init__(f"gRPC auth failure on layer={layer}")
        self.layer = layer


class _LayerHealth:
    """Per-layer health tracker for the coordinator.

    Tracks, for each API layer (``pccs``, ``cep``, ``graphql``):
      * ``consecutive_failures`` — cycles since last successful call.  A
        cycle counts as a failure only if **no** call to the layer
        succeeded; partial success resets the counter to 0.
      * ``last_code`` — string representation of the most recent failure
        code (gRPC status name or ``"error"`` for non-gRPC).
      * ``last_success_at`` — ISO 8601 UTC timestamp of the last
        successful call, or ``None``.
      * ``failing_endpoints`` — set of endpoint names that failed during
        the most recent cycle.  Cleared at the start of each cycle.
      * ``schema_endpoints`` — endpoints whose most recent failure was a
        GraphQL schema/validation error.  **Not** cleared per cycle: such a
        break never self-heals, so the entry survives until that endpoint
        succeeds again.  While it is non-empty the layer reports at least
        ``degraded``, even though ``consecutive_failures`` may read 0 — the
        counter is per-cycle and a partial-success cycle resets it, which is
        exactly how a permanently broken sub-query used to keep reporting
        ``ok`` (issue #22).  The flag is in-memory only, so a Home Assistant
        restart clears it until the next failing poll.

    Warning policy: the first failure with a given ``(layer, code)`` tuple
    logs at ``WARNING``; identical repeats log at ``DEBUG``.  The
    warned-set is cleared only at the end of a *fully clean* cycle (at
    least one success, zero failures) — partial-failure cycles keep the
    warned-set so a persistent broken endpoint does not re-warn every
    poll.  This matches the spec intent ("warn once per outage") while
    handling the realistic partial-failure case (e.g. one GraphQL
    sub-query permanently broken, others working).
    """

    def __init__(self) -> None:
        self._state: dict[str, dict] = {
            layer: {
                "consecutive_failures": 0,
                "last_code": None,
                "last_success_at": None,
                "failing_endpoints": set(),
                "_schema_endpoints": set(),
                "_warned_codes": set(),
                "_had_success_this_cycle": False,
                "_had_failure_this_cycle": False,
            }
            for layer in _ALL_LAYERS
        }

    def start_cycle(self) -> None:
        """Reset per-cycle state.  Called at the top of ``_do_fetch``."""
        for state in self._state.values():
            state["failing_endpoints"] = set()
            state["_had_success_this_cycle"] = False
            state["_had_failure_this_cycle"] = False

    def end_cycle(self) -> None:
        """Update consecutive-failure counters and warned-set based on the cycle.

        - A cycle with at least one success and zero failures = full recovery
          → reset ``consecutive_failures`` to 0 and clear the warned-set so a
          future outage warns fresh.
        - A cycle with at least one success and at least one failure = partial
          → reset ``consecutive_failures`` to 0 but keep the warned-set so the
          persistent failing endpoint does not re-warn every cycle.
        - A cycle with only failures → increment ``consecutive_failures``,
          keep warned-set.
        - A cycle with no calls → unchanged.
        """
        for state in self._state.values():
            had_success = state["_had_success_this_cycle"]
            had_failure = state["_had_failure_this_cycle"]
            if had_success and not had_failure:
                state["consecutive_failures"] = 0
                state["_warned_codes"] = set()
            elif had_success and had_failure:
                state["consecutive_failures"] = 0
            elif had_failure:
                state["consecutive_failures"] += 1

    def record_success(self, layer: str, endpoint: str) -> None:
        """Record a successful call."""
        state = self._state[layer]
        state["_had_success_this_cycle"] = True
        state["last_success_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        state["failing_endpoints"].discard(endpoint)
        state["_schema_endpoints"].discard(endpoint)

    def record_failure(self, layer: str, endpoint: str, err: BaseException) -> str:
        """Record a failed call.  Warns once per ``(layer, code)`` tuple.

        Returns the classified code string (e.g. ``"PERMISSION_DENIED"``
        or ``"error"``) so callers can use it for the auth-retry decision.
        """
        state = self._state[layer]
        state["_had_failure_this_cycle"] = True
        state["failing_endpoints"].add(endpoint)

        code = _classify_error(err)
        state["last_code"] = code
        if code == _SCHEMA_ERROR:
            state["_schema_endpoints"].add(endpoint)

        msg = "%s call %s failed: %s (%s)"
        if code in state["_warned_codes"]:
            _LOGGER.debug(msg, layer, endpoint, code, err)
        else:
            state["_warned_codes"].add(code)
            _LOGGER.warning(msg, layer, endpoint, code, err)

        return code

    def to_dict(self) -> dict:
        """Snapshot the current health into a serializable dict for sensors."""
        result: dict[str, dict] = {}
        for layer in _ALL_LAYERS:
            state = self._state[layer]
            failures = state["consecutive_failures"]
            schema_endpoints = sorted(state["_schema_endpoints"])
            if failures >= 2:
                status = "down"
            elif failures == 1 or schema_endpoints:
                status = "degraded"
            else:
                status = "ok"
            result[layer] = {
                "status": status,
                "last_code": state["last_code"],
                "last_success_at": state["last_success_at"],
                "failing_endpoints": sorted(state["failing_endpoints"]),
                # Reported separately from failing_endpoints, which start_cycle
                # wipes — without this nothing would name the culprit on a later
                # cycle where the layer is still sticky-degraded.
                "schema_endpoints": schema_endpoints,
                "consecutive_failures": failures,
            }
        return result


class _NonGrpcError(Exception):
    """Marker for failures that are not gRPC errors (used by ``_classify_error``)."""


# Code assigned to GraphQL schema/query validation failures.  Unlike an outage
# these never self-heal, so _LayerHealth keeps the endpoint flagged until it
# succeeds again.
_SCHEMA_ERROR = "SCHEMA_ERROR"


def _classify_error(err: BaseException) -> str:
    """Classify an exception into a stable code string for log dedup + UI."""
    if isinstance(err, grpc.RpcError):
        try:
            code = err.code()
        except Exception:
            return "RPC_ERROR"
        if code is None:
            return "RPC_ERROR"
        return code.name
    if isinstance(err, UpdateFailed) and "validation error" in str(err).lower():
        # graphql-java prefixes every schema/query validation failure this way
        # (FieldUndefined, UnknownType, SubselectionRequired, …).  They are
        # permanent rather than transient, so they earn a code of their own:
        # it keys the warn-once dedup, drives last_graphql_code, and marks the
        # endpoint schema-broken in _LayerHealth.
        return _SCHEMA_ERROR
    return type(err).__name__


def _drop_none(d: dict) -> dict:
    """Return a copy of ``d`` with ``None`` values removed.

    Used so sensors see a missing key (returning ``None`` and rendering
    ``unavailable``) only when there is genuinely no data — not when a
    failed call fell through to the previous-cycle fallback that also
    had no value.
    """
    return {k: v for k, v in d.items() if v is not None}


def _b64urlencode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class PolestarAPI:
    """Handle Polestar OAuth2 PKCE authentication and GraphQL queries."""

    def __init__(
        self,
        client_id: str = CLIENT_ID,
        redirect_uri: str = REDIRECT_URI,
        otp_callback: Callable[[], str | None] | None = None,
    ) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._otp_callback = otp_callback

    @property
    def client_id(self) -> str:
        """Return the OAuth2 client_id for this API instance."""
        return self._client_id

    def _get_otp_code(self) -> str | None:
        """Get OTP code for 2FA via callback."""
        if self._otp_callback:
            return self._otp_callback()
        return None

    # -- Private auth helpers ------------------------------------------------

    def _start_auth_session(
        self,
        scope: str,
        acr_values: str | None = None,
    ) -> tuple[requests.Session, str, str]:
        """Start OAuth2 PKCE session: auth request + extract resume URL.

        Returns (session, resume_url, code_verifier).
        """
        client_id = self._client_id
        redirect_uri = self._redirect_uri
        code_verifier = _b64urlencode(os.urandom(32))
        code_challenge = _b64urlencode(hashlib.sha256(code_verifier.encode()).digest())
        state = _b64urlencode(os.urandom(12))

        session = requests.Session()

        auth_params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if acr_values:
            auth_params["acr_values"] = acr_values
        resp = session.get(
            OIDC_AUTH_URL,
            params=auth_params,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

        match = re.search(r"(/as/[^/]+/resume/as/authorization\.ping)", resp.text)
        if not match:
            raise UpdateFailed("Could not find login form endpoint")

        resume_url = OIDC_BASE_URL + match.group(1)
        return session, resume_url, code_verifier

    def _submit_credentials(
        self,
        session: requests.Session,
        resume_url: str,
        email: str,
        password: str,
    ) -> requests.Response:
        """POST credentials to the login form. Returns the response."""
        return session.post(
            resume_url,
            data={
                "pf.username": email,
                "pf.pass": password,
                "client_id": self._client_id,
            },
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )

    @staticmethod
    def _submit_otp(
        session: requests.Session,
        otp_resume: str,
        otp_code: str,
        csrf_token: str = "",
    ) -> requests.Response:
        """Submit OTP code and handle the success-form continuation.

        The PingFederate OTP page requires a CSRF token alongside the OTP
        code.  The success page that follows also contains its own CSRF
        token which must be sent with the continuation POST.
        """
        otp_data: dict[str, str] = {"otp": otp_code}
        if csrf_token:
            otp_data["cSRFToken"] = csrf_token
        resp = session.post(
            otp_resume,
            data=otp_data,
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )
        _LOGGER.debug(
            "OTP submit response: status=%s, location=%s, has_success_form=%s",
            resp.status_code,
            resp.headers.get("Location", ""),
            "otp-success-form" in resp.text,
        )
        # OTP success returns a page with auto-submit form
        if "otp-success-form" in resp.text:
            action_match = re.search(
                r'action="(/as/[^"]+/resume/as/authorization\.ping)"',
                resp.text,
            )
            continue_url = OIDC_BASE_URL + action_match.group(1) if action_match else otp_resume
            # Extract the CSRF token from the success page
            continue_csrf = ""
            csrf_match = re.search(r'name="cSRFToken"\s+value="([^"]+)"', resp.text)
            if csrf_match:
                continue_csrf = csrf_match.group(1)
            continue_data: dict[str, str] = {"continue.authentication": "true"}
            if continue_csrf:
                continue_data["cSRFToken"] = continue_csrf
            resp = session.post(
                continue_url,
                data=continue_data,
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
            _LOGGER.debug(
                "OTP continue response: status=%s, location=%s",
                resp.status_code,
                resp.headers.get("Location", ""),
            )
        return resp

    @staticmethod
    def _extract_auth_code(
        session: requests.Session,
        resp: requests.Response,
        resume_url: str,
    ) -> str:
        """Extract auth code from redirect, handling consent if needed."""
        redirect_url = resp.headers.get("Location", "")
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)

        # Check for error in redirect (e.g. failed OTP returns
        # polestar-explore://...?error=access_denied)
        if "error" in params:
            error = params["error"][0]
            error_desc = params.get("error_description", [""])[0]
            _LOGGER.debug("Auth redirect contains error: %s (%s)", error, error_desc)
            raise UpdateFailed(f"Authentication failed: {error_desc or error}")

        # Handle consent/confirmation
        if "code" not in params and "uid" in params:
            uid = params["uid"][0]
            resp = session.post(
                resume_url,
                data={"pf.submit": "true", "subject": uid},
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code not in (302, 303):
                raise UpdateFailed("Consent confirmation failed")

            redirect_url = resp.headers.get("Location", "")
            parsed = urlparse(redirect_url)
            params = parse_qs(parsed.query)

        # Only follow redirect if it is an HTTP(S) URL
        if "code" not in params and parsed.scheme in ("http", "https"):
            resp = session.get(redirect_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
            if resp.status_code in (302, 303):
                redirect_url = resp.headers.get("Location", "")
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)

        if "code" not in params:
            _LOGGER.debug("No auth code in redirect URL: %s", redirect_url)
            raise UpdateFailed("No authorization code received")

        return params["code"][0]

    def _exchange_code_for_tokens(
        self,
        session: requests.Session,
        auth_code: str,
        code_verifier: str,
    ) -> dict:
        """Exchange authorization code for tokens and store them."""
        resp = session.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": code_verifier,
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

        tokens = resp.json()
        if "access_token" not in tokens:
            raise UpdateFailed("Token exchange failed")

        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token")
        return tokens

    @staticmethod
    def _detect_otp_challenge(resp: requests.Response, resume_url: str) -> tuple[str, str] | None:
        """Check if a credential-POST response is a 2FA challenge.

        Returns ``(otp_resume_url, csrf_token)`` if 2FA is required,
        or ``None``.
        """
        if resp.status_code in (302, 303):
            return None  # No 2FA — redirect means success
        if resp.status_code != 200:
            return None  # Not an OTP page
        if "ERR001" in resp.text or "authMessage" in resp.text:
            return None  # Auth error, not OTP
        action_match = re.search(
            r'action:\s*"(/as/[^"]+/resume/as/authorization\.ping)"',
            resp.text,
        )
        if not action_match:
            # Try HTML form action attribute as fallback
            action_match = re.search(
                r'action="(/as/[^"]+/resume/as/authorization\.ping)"',
                resp.text,
            )
        if not action_match:
            return None
        otp_url = OIDC_BASE_URL + action_match.group(1)
        # Extract CSRF token from the OTP challenge page
        csrf_match = re.search(r'cSRFToken:\s*"([^"]+)"', resp.text)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        _LOGGER.debug(
            "OTP challenge detected: url=%s, has_csrf=%s",
            otp_url,
            bool(csrf_token),
        )
        return otp_url, csrf_token

    # -- Public login methods ------------------------------------------------

    def login(
        self,
        email: str,
        password: str,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> dict:
        """Perform full OAuth2 Authorization Code + PKCE login."""
        session, resume_url, code_verifier = self._start_auth_session(scope, acr_values)
        resp = self._submit_credentials(session, resume_url, email, password)

        if resp.status_code not in (302, 303):
            if "ERR001" in resp.text or "authMessage" in resp.text:
                raise ConfigEntryAuthFailed("Invalid email or password")

            # 2SV: server returned OTP challenge page (200 with form)
            otp_result = self._detect_otp_challenge(resp, resume_url)
            if otp_result:
                otp_resume, csrf_token = otp_result
                otp_code = self._get_otp_code()
                if not otp_code:
                    raise UpdateFailed("2FA code required but not provided")

                _LOGGER.debug("Submitting OTP to %s", otp_resume)
                resp = self._submit_otp(session, otp_resume, otp_code, csrf_token)

                if resp.status_code not in (302, 303):
                    raise UpdateFailed(f"2FA verification failed ({resp.status_code})")
            else:
                raise UpdateFailed(f"Unexpected login response ({resp.status_code})")

        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def login_start_2fa(
        self,
        email: str,
        password: str,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> dict:
        """Start login with 2FA. Triggers OTP email.

        Returns a dict with ``"needs_otp": True`` and an opaque
        ``"_session_state"`` if the server challenges for OTP.
        If no 2FA is required, completes the flow and returns tokens
        (with ``"access_token"`` present).
        """
        session, resume_url, code_verifier = self._start_auth_session(scope, acr_values)
        resp = self._submit_credentials(session, resume_url, email, password)

        if resp.status_code not in (302, 303):
            if "ERR001" in resp.text or "authMessage" in resp.text:
                raise ConfigEntryAuthFailed("Invalid email or password")

            otp_result = self._detect_otp_challenge(resp, resume_url)
            if otp_result:
                otp_resume, csrf_token = otp_result
                # 2FA triggered — return session state for the caller to
                # collect the OTP code and call login_complete_2fa().
                return {
                    "needs_otp": True,
                    "_session_state": {
                        "session": session,
                        "otp_resume": otp_resume,
                        "csrf_token": csrf_token,
                        "resume_url": resume_url,
                        "code_verifier": code_verifier,
                    },
                }

            raise UpdateFailed(f"Unexpected login response ({resp.status_code})")

        # No 2FA — complete the flow directly
        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def login_complete_2fa(self, session_state: dict, otp_code: str) -> dict:
        """Complete 2FA login by submitting the OTP code.

        ``session_state`` is the ``"_session_state"`` dict returned by
        ``login_start_2fa``.
        """
        session: requests.Session = session_state["session"]
        otp_resume: str = session_state["otp_resume"]
        csrf_token: str = session_state.get("csrf_token", "")
        resume_url: str = session_state["resume_url"]
        code_verifier: str = session_state["code_verifier"]

        _LOGGER.debug("Submitting OTP to %s", otp_resume)
        resp = self._submit_otp(session, otp_resume, otp_code, csrf_token)

        if resp.status_code not in (302, 303):
            raise UpdateFailed(f"2FA verification failed ({resp.status_code})")

        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def refresh_tokens(self, refresh_token: str) -> dict:
        """Refresh the access token using a refresh token."""
        client_id = self._client_id
        resp = requests.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        tokens = resp.json()
        if "access_token" not in tokens:
            raise UpdateFailed("Token refresh failed")

        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", refresh_token)
        return tokens

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = requests.post(API_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()

        if "errors" in result:
            messages = [e.get("message", str(e)) for e in result["errors"]]
            raise UpdateFailed(f"GraphQL errors: {'; '.join(messages)}")

        return result.get("data", {})

    def get_vehicles(self) -> list:
        """Fetch list of vehicles."""
        data = self._graphql(QUERY_GET_CARS)
        return data.get("getConsumerCarsV2", [])

    def get_telematics(self, vins: list[str]) -> tuple[dict, dict[str, UpdateFailed]]:
        """Fetch telematics data for given VINs.

        Issues the battery and odometer queries independently so a
        schema-validation failure on one (e.g. Polestar removing the
        ``battery`` field per evcc #27726) does not take the other down.

        Returns ``(data, failures)`` where ``data`` matches the legacy shape
        ``{"battery": [...], "odometer": [...]}`` and ``failures`` maps an
        endpoint name like ``"carTelematicsV2.battery"`` to the ``UpdateFailed``
        it raised.  Callers must record that exception rather than the endpoint
        name: it carries the GraphQL ``errors[].message`` text, which is the
        only thing that identifies a schema change as the cause (issue #22).

        If both sub-queries fail, ``UpdateFailed`` is re-raised so the
        coordinator surfaces the outage. HTTP 401s are not caught here —
        ``requests.HTTPError`` propagates so the existing 401 retry path
        in ``_async_update_data`` still triggers.
        """
        result: dict = {"battery": [], "odometer": []}
        failing: dict[str, UpdateFailed] = {}

        try:
            data = self._graphql(QUERY_TELEMATICS_BATTERY, {"vins": vins})
            result["battery"] = data.get("carTelematicsV2", {}).get("battery", []) or []
        except UpdateFailed as err:
            failing["carTelematicsV2.battery"] = err

        try:
            data = self._graphql(QUERY_TELEMATICS_ODOMETER, {"vins": vins})
            result["odometer"] = data.get("carTelematicsV2", {}).get("odometer", []) or []
        except UpdateFailed as err:
            failing["carTelematicsV2.odometer"] = err

        # One key per sub-query issued, so both this check and the message below
        # stay correct if a third is ever added — provided its key is seeded in
        # the literal above rather than only assigned inside its ``try``, which
        # would leave it missing on failure and stop this guard firing.
        if len(failing) == len(result):
            detail = "; ".join(
                f"{endpoint.rsplit('.', 1)[-1]}={err}" for endpoint, err in failing.items()
            )
            raise UpdateFailed(f"GraphQL telematics: {detail}")

        return result, failing


class PolestarCoordinator(DataUpdateCoordinator):
    """Coordinate data updates from the Polestar API."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.config_entry = entry
        self.api = PolestarAPI()
        self.api.access_token = entry.data.get("access_token")
        self.api.refresh_token = entry.data.get("refresh_token")

        # PCCS API instance kept for potential future write operations
        # that may require the PCCS token with 2FA scope.
        self._pccs_api = PolestarAPI(
            client_id=PCCS_CLIENT_ID,
            redirect_uri=PCCS_REDIRECT_URI,
        )
        self._pccs_api.access_token = entry.data.get("pccs_access_token")
        self._pccs_api.refresh_token = entry.data.get("pccs_refresh_token")

        # PCCS chronos services accept the web token for reads;
        # writes require the PCCS 2FA token (customer:attributes:write scope).
        self.pccs = PccsClient(
            access_token=self.api.access_token or "",
            write_access_token=self._pccs_api.access_token,
        )
        self.cep = CepClient(
            access_token=self.api.access_token or "",
            write_access_token=self._pccs_api.access_token,
        )
        self._email: str = entry.data["email"]
        self._password: str = entry.data["password"]
        self._health = _LayerHealth()

    async def _async_update_data(self) -> dict:
        """Fetch data from the Polestar API.

        Two distinct retry paths exist:

        1. **HTTP 401 from GraphQL** → refresh tokens, retry once.
        2. **gRPC PERMISSION_DENIED / UNAUTHENTICATED** → refresh tokens,
           reset both gRPC channels, retry once.

        Both paths cap at one retry per cycle.  After a retry the second
        ``_fetch_data`` invocation runs with ``auth_retry_used=True``,
        which prevents the executor-side code from raising
        ``_GrpcAuthError`` again even if the second attempt also fails —
        further failures are recorded via ``_LayerHealth`` instead.
        """
        try:
            return await self._fetch_data()
        except _GrpcAuthError as err:
            _LOGGER.debug(
                "gRPC auth failure on layer=%s, refreshing tokens and retrying",
                err.layer,
            )
            try:
                await self._refresh_or_relogin()
            except ConfigEntryAuthFailed:
                raise
            except Exception as refresh_err:
                raise UpdateFailed(
                    f"Token refresh failed after gRPC auth error: {refresh_err}"
                ) from refresh_err
            # Reset both gRPC channels so the next call picks up the new
            # token in fresh metadata and any channel-level rejection
            # (cookie / SNI / session) is cleared.
            self.pccs.close()
            self.cep.close()
            try:
                return await self._fetch_data(auth_retry_used=True)
            except ConfigEntryAuthFailed:
                raise
            except Exception as retry_err:
                raise UpdateFailed(f"API error after gRPC auth retry: {retry_err}") from retry_err
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 401:
                _LOGGER.debug("Access token expired, attempting refresh")
            else:
                raise UpdateFailed(f"API error: {err}") from err

        # HTTP 401 path: refresh tokens, retry once.
        try:
            await self._refresh_or_relogin()
            return await self._fetch_data(auth_retry_used=True)
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"API error after re-auth: {err}") from err

    async def _refresh_or_relogin(self) -> None:
        """Try token refresh, fall back to full re-login for both API clients."""
        # Refresh/relogin the main (web) API — failure is fatal
        await self._refresh_or_relogin_api(self.api)
        self._update_stored_tokens()
        # Refresh the PCCS API — only try token refresh, not
        # full re-login (which requires 2FA and can't be done in background).
        if self._pccs_api.refresh_token:
            try:
                await self.hass.async_add_executor_job(
                    self._pccs_api.refresh_tokens, self._pccs_api.refresh_token
                )
            except Exception:
                _LOGGER.warning(
                    "PCCS token refresh failed; PCCS sensors will be unavailable "
                    "until the integration is reconfigured",
                    exc_info=True,
                )
        self._update_stored_tokens()

    async def _refresh_or_relogin_api(
        self,
        api: PolestarAPI,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> None:
        """Try token refresh, fall back to full re-login for a single API client."""
        if api.refresh_token:
            try:
                await self.hass.async_add_executor_job(api.refresh_tokens, api.refresh_token)
                return
            except Exception:
                _LOGGER.debug("Refresh token failed for %s, doing full re-login", api.client_id)

        # Full re-login
        try:
            await self.hass.async_add_executor_job(
                api.login,
                self._email,
                self._password,
                scope,
                acr_values,
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise ConfigEntryAuthFailed(
                "Re-login failed. Please reconfigure the integration."
            ) from err

    async def _fetch_data(self, *, auth_retry_used: bool = False) -> dict:
        """Fetch vehicles, telematics, and PCCS data (blocking, run in executor).

        ``auth_retry_used`` is propagated from ``_async_update_data``: when
        ``True``, gRPC auth failures are recorded via ``_LayerHealth``
        instead of raising ``_GrpcAuthError`` (which would re-trigger
        another refresh-and-retry).
        """
        return await self.hass.async_add_executor_job(self._do_fetch, auth_retry_used)

    def _do_fetch(self, auth_retry_used: bool) -> dict:
        """Synchronous fetch path — runs in the executor thread.

        Per-call gRPC failures are routed through the layer health tracker.
        On the first PERMISSION_DENIED / UNAUTHENTICATED of the cycle (and
        only if ``auth_retry_used`` is False), ``_GrpcAuthError`` is raised
        so the async caller can refresh tokens and retry exactly once.
        """
        # Mutable per-cycle state.  Once an auth retry is "used" (either
        # because the caller already retried, or because we just raised
        # _GrpcAuthError on this layer in this cycle), no further raise
        # happens — subsequent failures fall through to the health tracker.
        retry_used = [auth_retry_used]
        previous = self.data or {}
        health = self._health
        health.start_cycle()

        def call(layer: str, endpoint: str, fn: Callable[[], object]) -> object:
            """Run ``fn``, classify any exception, return result or ``_FAILED`` on failure.

            Returns the sentinel ``_FAILED`` on failure so callers can
            distinguish "call failed" from "call returned None" (which
            would otherwise be a stale-data trap if any getter ever
            legitimately returns ``None``).

            Order of catch arms matters: ``_GrpcAuthError`` must propagate
            to the async caller, so it appears above the general
            ``grpc.RpcError`` handler.
            """
            try:
                result = fn()
            except _GrpcAuthError:
                raise
            except grpc.RpcError as err:
                health.record_failure(layer, endpoint, err)
                if not retry_used[0] and err.code() in _AUTH_GRPC_CODES:
                    retry_used[0] = True
                    raise _GrpcAuthError(layer) from err
                return _FAILED
            except Exception:
                _LOGGER.debug("Failed %s/%s (non-gRPC)", layer, endpoint, exc_info=True)
                health.record_failure(layer, endpoint, _NonGrpcError(f"{layer}/{endpoint}"))
                return _FAILED
            health.record_success(layer, endpoint)
            return result

        def call_or_keep(
            layer: str, endpoint: str, vin: str, fn: Callable[[], object]
        ) -> object | None:
            """Like ``call``, but on failure preserves previous cycle's per-VIN data."""
            result = call(layer, endpoint, fn)
            if result is not _FAILED:
                return result
            return previous.get(endpoint, {}).get(vin)

        # ---- GraphQL: vehicle list ----
        try:
            vehicles = self.api.get_vehicles()
            health.record_success(LAYER_GRAPHQL, "getConsumerCarsV2")
        except UpdateFailed as err:
            health.record_failure(LAYER_GRAPHQL, "getConsumerCarsV2", err)
            health.end_cycle()
            raise
        except requests.HTTPError:
            # Let HTTP 401 propagate to the async layer's existing retry.
            raise

        if not vehicles:
            health.end_cycle()
            return {
                "vehicles": [],
                "battery": {},
                "odometer": {},
                "target_soc": {},
                "amp_limit": {},
                "charge_timer": {},
                "climate_timers": {},
                "climate_timer_settings": {},
                "climate": {},
                "cep_battery": {},
                "location": {},
                "exterior": {},
                "availability": {},
                "health": {},
                "api_health": health.to_dict(),
            }

        vins = [v["vin"] for v in vehicles]

        # ---- GraphQL: telematics (battery + odometer split) ----
        try:
            telematics, graphql_failing = self.api.get_telematics(vins)
        except UpdateFailed as err:
            # Both sub-queries failed — record both endpoints, then re-raise.
            # The combined message names both, so if only one was a validation
            # error the other is also flagged schema-broken here.  Left as-is:
            # it self-corrects on that endpoint's next success.
            for endpoint in ("carTelematicsV2.battery", "carTelematicsV2.odometer"):
                health.record_failure(LAYER_GRAPHQL, endpoint, err)
            health.end_cycle()
            raise
        # Mark whichever sub-queries succeeded vs failed.  Record the real
        # UpdateFailed, not a synthetic marker: its message carries the GraphQL
        # errors[].message text that identifies a schema change (issue #22).
        for endpoint, err in graphql_failing.items():
            health.record_failure(LAYER_GRAPHQL, endpoint, err)
        if "carTelematicsV2.battery" not in graphql_failing:
            health.record_success(LAYER_GRAPHQL, "carTelematicsV2.battery")
        if "carTelematicsV2.odometer" not in graphql_failing:
            health.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")

        battery_by_vin: dict = {}
        for b in telematics.get("battery", []) or []:
            if b:
                battery_by_vin[b["vin"]] = b

        odometer_by_vin: dict = {}
        for o in telematics.get("odometer", []) or []:
            if o:
                odometer_by_vin[o["vin"]] = o

        # A failed sub-query yields no rows at all, which blanks every entity it
        # backs on the very first bad poll.  Fall back to the previous cycle, as
        # call_or_keep already does for the PCCS/CEP reads.  Gated on failure: a
        # *successful* query returning [] is real data, not an outage.
        #
        # call_or_keep itself cannot be reused here — it looks up
        # previous[endpoint], which only works because the PCCS/CEP endpoint
        # names double as data keys.  These do not ("battery" vs
        # "carTelematicsV2.battery"), and it is per-VIN over a callable besides.
        #
        # A VIN that has left the account keeps its stale entry until the
        # sub-query recovers — harmless, and cheaper than tracking removals.
        if "carTelematicsV2.battery" in graphql_failing:
            battery_by_vin = {**previous.get("battery", {}), **battery_by_vin}
        if "carTelematicsV2.odometer" in graphql_failing:
            odometer_by_vin = {**previous.get("odometer", {}), **odometer_by_vin}

        # ---- PCCS: per-VIN reads ----
        target_soc_by_vin: dict = {}
        amp_limit_by_vin: dict = {}
        charge_timer_by_vin: dict = {}
        climate_timers_by_vin: dict = {}
        climate_timer_settings_by_vin: dict = {}
        for vin in vins:
            target_soc_by_vin[vin] = call_or_keep(
                LAYER_PCCS, "target_soc", vin, lambda v=vin: self.pccs.get_target_soc(v)
            )
            amp_limit_by_vin[vin] = call_or_keep(
                LAYER_PCCS, "amp_limit", vin, lambda v=vin: self.pccs.get_amp_limit(v)
            )
            charge_timer_by_vin[vin] = call_or_keep(
                LAYER_PCCS,
                "charge_timer",
                vin,
                lambda v=vin: self.pccs.get_global_charge_timer(v),
            )
            climate_timers_by_vin[vin] = call_or_keep(
                LAYER_PCCS,
                "climate_timers",
                vin,
                lambda v=vin: self.pccs.get_parking_climate_timers(v),
            )
            climate_timer_settings_by_vin[vin] = call_or_keep(
                LAYER_PCCS,
                "climate_timer_settings",
                vin,
                lambda v=vin: self.pccs.get_parking_climate_timer_settings(v),
            )

        # ---- CEP: per-VIN reads ----
        climate_by_vin: dict = {}
        cep_battery_by_vin: dict = {}
        location_by_vin: dict = {}
        exterior_by_vin: dict = {}
        availability_by_vin: dict = {}
        health_by_vin: dict = {}
        for vin in vins:
            climate_by_vin[vin] = call_or_keep(
                LAYER_CEP,
                "climate",
                vin,
                lambda v=vin: self.cep.get_parking_climatization(v),
            )
            cep_battery_by_vin[vin] = call_or_keep(
                LAYER_CEP, "cep_battery", vin, lambda v=vin: self.cep.get_battery(v)
            )
            location_by_vin[vin] = call_or_keep(
                LAYER_CEP, "location", vin, lambda v=vin: self.cep.get_location(v)
            )
            exterior_by_vin[vin] = call_or_keep(
                LAYER_CEP, "exterior", vin, lambda v=vin: self.cep.get_exterior(v)
            )
            availability_by_vin[vin] = call_or_keep(
                LAYER_CEP, "availability", vin, lambda v=vin: self.cep.get_availability(v)
            )
            health_by_vin[vin] = call_or_keep(
                LAYER_CEP, "health", vin, lambda v=vin: self.cep.get_health(v)
            )

        health.end_cycle()
        return {
            "vehicles": vehicles,
            "battery": battery_by_vin,
            "odometer": odometer_by_vin,
            "target_soc": _drop_none(target_soc_by_vin),
            "amp_limit": _drop_none(amp_limit_by_vin),
            "charge_timer": _drop_none(charge_timer_by_vin),
            "climate_timers": _drop_none(climate_timers_by_vin),
            "climate_timer_settings": _drop_none(climate_timer_settings_by_vin),
            "climate": _drop_none(climate_by_vin),
            "cep_battery": _drop_none(cep_battery_by_vin),
            "location": _drop_none(location_by_vin),
            "exterior": _drop_none(exterior_by_vin),
            "availability": _drop_none(availability_by_vin),
            "health": _drop_none(health_by_vin),
            "api_health": health.to_dict(),
        }

    def _update_stored_tokens(self) -> None:
        """Persist refreshed tokens in config entry."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                "access_token": self.api.access_token,
                "refresh_token": self.api.refresh_token,
                "pccs_access_token": self._pccs_api.access_token,
                "pccs_refresh_token": self._pccs_api.refresh_token,
            },
        )
        # Keep gRPC client tokens in sync
        self.pccs.access_token = self.api.access_token or ""
        self.pccs.write_access_token = self._pccs_api.access_token
        self.cep.access_token = self.api.access_token or ""
        self.cep.write_access_token = self._pccs_api.access_token

    def close(self) -> None:
        """Close gRPC channels."""
        self.pccs.close()
        self.cep.close()
