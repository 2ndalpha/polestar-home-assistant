# Polestar Home Assistant Integration

Custom Home Assistant integration for Polestar vehicles. Provides battery, charging, climate, and range sensors via the Polestar and Volvo cloud APIs.

## Features

### Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Battery SOC | % | Battery state of charge |
| Charging Status | — | Charging / Idle / Fully charged / Scheduled / Fault |
| Charging Time Remaining | min | Estimated time to full charge |
| Estimated Range | km | Estimated remaining range |
| Odometer | km | Total distance driven |
| Climate Status | — | Off / Pre-conditioning / Starting / Residual heat |
| Driver Seat Heating | — | Off / Low / Medium / High |
| Passenger Seat Heating | — | Off / Low / Medium / High |
| Rear Left Seat Heating | — | Off / Low / Medium / High |
| Rear Right Seat Heating | — | Off / Low / Medium / High |
| Steering Wheel Heating | — | Off / Low / Medium / High |

### Device Tracker

| Entity | Description |
|--------|-------------|
| Location | Vehicle GPS position with timestamp |

### Binary Sensors

| Sensor | Device Class | Description |
|--------|-------------|-------------|
| Central Lock | Lock | Locked / Unlocked |
| Front Left Door | Door | Open / Closed / Ajar |
| Front Right Door | Door | Open / Closed / Ajar |
| Rear Left Door | Door | Open / Closed / Ajar |
| Rear Right Door | Door | Open / Closed / Ajar |
| Front Left Window* | Window | Open / Closed |
| Front Right Window* | Window | Open / Closed |
| Rear Left Window* | Window | Open / Closed |
| Rear Right Window* | Window | Open / Closed |
| Hood* | Opening | Open / Closed |
| Tailgate* | Opening | Open / Closed |
| Tank Lid* | Opening | Open / Closed |
| Sunroof* | Opening | Open / Closed |
| Alarm* | Safety | Idle / Triggered |

*Disabled by default — enable in the entity registry.

### Controls

| Entity | Type | Description |
|--------|------|-------------|
| Charge Limit | Number (50–100%, step 5) | Target state of charge slider |
| Charging Start Time | Time | Scheduled charging start time |
| Charging End Time | Time | Scheduled charging end time |

## Installation

1. Copy `custom_components/polestar_soc/` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & Services > Add Integration** and search for **Polestar State of Charge**.
4. Enter your Polestar ID email and password.
5. If prompted, enter the OTP code sent to your email (required for charge limit control).

## Configuration

The integration authenticates using Polestar ID OAuth2. A second authentication step (email OTP) is offered during setup to enable charge limit and charge timer controls. This step is optional — sensors work without it.

Data is polled every 5 minutes.

## Troubleshooting

### API health diagnostic sensor

The integration exposes a diagnostic entity called **API health** (under
the integration's diagnostic entities, not the default dashboard) that
reports the rolling state of each upstream API layer:

| State | Meaning | What to do |
|-------|---------|------------|
| `ok` | All API layers responded successfully on the last poll. | Nothing. |
| `degraded` | One layer failed on the last poll cycle. | Wait one cycle (5 min). Could be a transient blip. |
| `down` | A layer has failed at least two consecutive cycles. | Check the entity's attributes for `last_*_code` and `*_failing_endpoints`. |

The sensor's attributes break down per layer (`pccs`, `cep`, `graphql`):

- `<layer>_status` — same three-state value, scoped to one layer.
- `last_<layer>_code` — the most recent gRPC status code (e.g.
  `PERMISSION_DENIED`, `UNAVAILABLE`) or exception type.
- `last_<layer>_success_at` — UTC timestamp of the last successful call,
  useful for telling fresh from stale data.
- `<layer>_failing_endpoints` — the specific endpoints that failed.
- `<layer>_consecutive_failures` — cycle count since the last success.

When data fetch fails for a layer, the integration **preserves the last
known value per VIN** so sensors don't flip to `unavailable` on every
blip. Check the API health attributes if you need to know whether a
sensor's value is fresh or stale.

### Diagnostic probe script

For deeper triage — for example, when filing an issue about a
backend-side change like
[#20](https://github.com/2ndalpha/polestar-home-assistant/issues/20) —
run the diagnostic probe:

```bash
.venv/bin/python scripts/diagnose_apis.py
.venv/bin/python scripts/diagnose_apis.py --vin <YOUR_VIN>
.venv/bin/python scripts/diagnose_apis.py --out report.txt
```

The script logs into Polestar (using `POLESTAR_EMAIL` /
`POLESTAR_PASSWORD` env vars or interactive prompts), then probes every
API layer for one VIN and prints a table of per-endpoint status, plus
decoded JWT claims.

Output is **redacted by default** (email, JWT `sub` claim, and the last
six characters of the VIN are masked). Pass `--unredacted` only when
you've reviewed the output and confirmed it's safe to share.

The script distinguishes "auth failed at login" from "auth ok, endpoints
rejected" in its summary line — the latter is the signature of a
backend-side change and is what you want to confirm before opening a
bug.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
