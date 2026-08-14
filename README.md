# Polestar Home Assistant Integration

Custom Home Assistant integration for Polestar vehicles. Provides battery, charging, climate, and range sensors via the Polestar and Volvo cloud APIs.

## Features

### Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| Battery SOC | % | Battery state of charge |
| Charging Status | — | Charging / Idle / Scheduled |
| Charging Time Remaining | min | Estimated time to full charge |
| Estimated Range | km | Estimated remaining range |
| Odometer | km | Total distance driven |
| Climate Status | — | Off / Pre-conditioning / Starting / Residual heat |
| Driver Seat Heating | — | Off / Low / Medium / High |
| Passenger Seat Heating | — | Off / Low / Medium / High |
| Rear Left Seat Heating | — | Off / Low / Medium / High |
| Rear Right Seat Heating | — | Off / Low / Medium / High |
| Steering Wheel Heating | — | Off / Low / Medium / High |

> **Charging Status changed.** Polestar removed the `chargingStatus` field from
> their GraphQL API, so this sensor is now read from the Volvo CEP API instead.
> Two consequences for existing dashboards and automations:
>
> - **`Fully charged` and `Fault` are no longer reported.** The CEP enum has no
>   known equivalent for them. Any value it reports that we don't yet recognise
>   shows up as `Unknown (n)` rather than being dropped — please open an issue if
>   you see one, since that is how the missing states would be identified.
> - **The sensor now reports `unknown` when no value is available**, where it
>   previously reported the literal string `Unknown`. Automations comparing
>   against `"Unknown"` should compare against `"unknown"` instead.

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

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
