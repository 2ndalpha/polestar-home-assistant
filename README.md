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

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
