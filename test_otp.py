"""Local test script for Polestar PCCS 2FA OTP flow."""
import base64
import getpass
import hashlib
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

OIDC_BASE_URL = "https://polestarid.eu.polestar.com"
OIDC_AUTH_URL = f"{OIDC_BASE_URL}/as/authorization.oauth2"
OIDC_TOKEN_URL = f"{OIDC_BASE_URL}/as/token.oauth2"

PCCS_CLIENT_ID = "lp8dyrd_10"
PCCS_REDIRECT_URI = "polestar-explore://explore.polestar.com"
PCCS_SCOPE = "openid profile email customer:attributes customer:attributes:write"
PCCS_ACR_VALUES = "urn:volvoid:aal:bronze:2sv"

HTTP_TIMEOUT = 30


def b64urlencode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main():
    email = sys.argv[1] if len(sys.argv) > 1 else input("Email: ")
    password = getpass.getpass("Password: ")

    code_verifier = b64urlencode(os.urandom(32))
    code_challenge = b64urlencode(hashlib.sha256(code_verifier.encode()).digest())
    state = b64urlencode(os.urandom(12))

    session = requests.Session()

    # Step 1: Start auth
    print("\n[1] Starting PCCS auth session...")
    resp = session.get(
        OIDC_AUTH_URL,
        params={
            "client_id": PCCS_CLIENT_ID,
            "redirect_uri": PCCS_REDIRECT_URI,
            "response_type": "code",
            "state": state,
            "scope": PCCS_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "acr_values": PCCS_ACR_VALUES,
        },
        allow_redirects=True,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    print(f"    Status: {resp.status_code}")
    print(f"    URL: {resp.url}")

    # Find resume URL
    match = re.search(r"(/as/[^/]+/resume/as/authorization\.ping)", resp.text)
    if not match:
        print("    ERROR: Could not find resume URL")
        sys.exit(1)
    resume_url = OIDC_BASE_URL + match.group(1)
    print(f"    Resume URL: {resume_url}")

    # Step 2: Submit credentials
    print("\n[2] Submitting credentials...")
    resp = session.post(
        resume_url,
        data={
            "pf.username": email,
            "pf.pass": password,
            "client_id": PCCS_CLIENT_ID,
        },
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    print(f"    Status: {resp.status_code}")
    print(f"    Location: {resp.headers.get('Location', 'none')}")

    if resp.status_code in (302, 303):
        print("    No 2FA required (got redirect)")
        sys.exit(0)

    if "ERR001" in resp.text or "authMessage" in resp.text:
        print("    ERROR: Invalid credentials")
        sys.exit(1)

    # Step 3: Detect OTP challenge
    print("\n[3] Analyzing OTP page...")
    print(f"    Page length: {len(resp.text)} chars")

    # Check JS action pattern (what the integration uses)
    js_action = re.search(
        r'action:\s*"(/as/[^"]+/resume/as/authorization\.ping)"',
        resp.text,
    )
    print(f"    JS action pattern found: {bool(js_action)}")

    # Check HTML form action pattern
    html_action = re.search(
        r'action="(/as/[^"]+/resume/as/authorization\.ping)"',
        resp.text,
    )
    print(f"    HTML form action found: {bool(html_action)}")

    # Find ALL input fields
    inputs = re.findall(r'<input[^>]*>', resp.text)
    print(f"    HTML input elements: {len(inputs)}")
    for inp in inputs:
        print(f"      {inp}")

    # Find JS field references
    js_names = re.findall(
        r'(?:name|fieldName|field|param|key)["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        resp.text,
    )
    print(f"    JS name/field references: {js_names}")

    # Find fetch/POST patterns
    fetch_patterns = re.findall(r'fetch\([^)]+\)', resp.text)
    print(f"    fetch() calls: {len(fetch_patterns)}")
    for fp in fetch_patterns[:5]:
        print(f"      {fp[:200]}")

    # Find JSON.stringify patterns
    stringify = re.findall(r'JSON\.stringify\(([^)]+)\)', resp.text)
    print(f"    JSON.stringify patterns: {stringify[:5]}")

    # Find any "otp" references
    otp_refs = re.findall(r'.{0,40}otp.{0,40}', resp.text, re.IGNORECASE)
    print(f"    'otp' references ({len(otp_refs)}):")
    for ref in otp_refs[:10]:
        print(f"      {ref.strip()}")

    # Dump key parts of the page
    print("\n    --- Page content (first 3000 chars) ---")
    print(resp.text[:3000])
    print("    --- End ---")

    otp_resume = OIDC_BASE_URL + (js_action or html_action).group(1)
    print(f"\n    OTP resume URL: {otp_resume}")

    # Step 4: Get OTP from user and submit
    otp_code = input("\n[4] Enter OTP code from email: ").strip()

    print(f"\n[5] Submitting OTP '{otp_code}' to {otp_resume}")

    # Try the current approach first (form POST with "otp" field)
    print("\n    --- Attempt A: POST form data {'otp': code} ---")
    resp_a = session.post(
        otp_resume,
        data={"otp": otp_code},
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )
    print(f"    Status: {resp_a.status_code}")
    print(f"    Location: {resp_a.headers.get('Location', 'none')}")
    loc_a = resp_a.headers.get("Location", "")
    if "code=" in loc_a:
        print("    SUCCESS with form data {'otp': code}!")
    elif "error" in loc_a:
        parsed = urlparse(loc_a)
        params = parse_qs(parsed.query)
        print(f"    FAILED: {params.get('error', ['?'])} - {params.get('error_description', ['?'])}")
    if "otp-success-form" in resp_a.text:
        print("    Has otp-success-form!")

    print(f"\n    Response body length: {len(resp_a.text)}")
    if resp_a.text:
        print(f"    Response body (first 1000): {resp_a.text[:1000]}")


if __name__ == "__main__":
    main()
