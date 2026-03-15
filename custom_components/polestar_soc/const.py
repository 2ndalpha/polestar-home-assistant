"""Constants for the Polestar State of Charge integration."""

from datetime import timedelta

DOMAIN = "polestar_soc"
SCAN_INTERVAL = timedelta(minutes=5)

# OAuth2 / OIDC constants
OIDC_BASE_URL = "https://polestarid.eu.polestar.com"
OIDC_AUTH_URL = f"{OIDC_BASE_URL}/as/authorization.oauth2"
OIDC_TOKEN_URL = f"{OIDC_BASE_URL}/as/token.oauth2"

# Web client — used for GraphQL (mystar-v2)
CLIENT_ID = "l3oopkc_10"
REDIRECT_URI = "https://www.polestar.com/sign-in-callback"
SCOPE = "openid profile email customer:attributes"

# PCCS client — used for PCCS gRPC (requires broader scope + 2SV)
PCCS_CLIENT_ID = "lp8dyrd_10"
PCCS_REDIRECT_URI = "polestar-explore://explore.polestar.com"
PCCS_SCOPE = "openid profile email customer:attributes customer:attributes:write"
PCCS_ACR_VALUES = "urn:volvoid:aal:bronze:2sv"

# GraphQL API
API_URL = "https://pc-api.polestar.com/eu-north-1/mystar-v2/"

# PCCS gRPC API
PCCS_API_HOST = "api.pccs-prod.plstr.io"

# Volvo CEP gRPC API (vehicle state reads)
CEP_API_HOST = "cepmobtoken.eu.prod.c3.volvocars.com"

QUERY_GET_CARS = """
query getCars {
  getConsumerCarsV2 {
    vin
    internalVehicleIdentifier
    modelYear
    content { model { code name } }
    hasPerformancePackage
    registrationNo
    deliveryDate
    currentPlannedDeliveryDate
  }
}
"""

QUERY_TELEMATICS = """
query CarTelematicsV2($vins: [String!]!) {
  carTelematicsV2(vins: $vins) {
    battery {
      vin
      batteryChargeLevelPercentage
      chargingStatus
      estimatedChargingTimeToFullMinutes
    }
    odometer {
      vin
      odometerMeters
    }
  }
}
"""

CHARGING_STATUS_MAP = {
    "CHARGING_STATUS_CHARGING": "Charging",
    "CHARGING_STATUS_IDLE": "Idle",
    "CHARGING_STATUS_DONE": "Fully charged",
    "CHARGING_STATUS_FAULT": "Fault",
    "CHARGING_STATUS_UNSPECIFIED": "Unknown",
    "CHARGING_STATUS_SCHEDULED": "Scheduled",
}

# Climate running status enum (field 2 of ParkingClimatizationState)
CLIMATE_RUNNING_STATUS_MAP: dict[int, str] = {
    0: "Unknown",
    1: "Starting",
    2: "Off",
    3: "Pre-conditioning",
    4: "Pre-conditioning (external power)",
    5: "Pre-cleaning",
    6: "Pre-conditioning and cleaning",
    7: "Residual heat",
}

# Heating intensity enum (seat heaters + steering wheel)
HEATING_INTENSITY_MAP: dict[int, str] = {
    0: "Off",
    1: "Low",
    2: "Medium",
    3: "High",
}

# InvocationResponse status enum (InvocationService)
INVOCATION_STATUS_MAP: dict[int, str] = {
    0: "UNKNOWN_ERROR",
    1: "SENT",
    2: "CAR_OFFLINE",
    4: "DELIVERED",
    5: "DELIVERY_TIMEOUT",
    6: "SUCCESS",
    7: "RESPONSE_TIMEOUT",
    8: "UNKNOWN_CAR_ERROR",
    9: "NOT_ALLOWED_PRIVACY_ENABLED",
    10: "NOT_ALLOWED_WRONG_USAGE_MODE",
    11: "INVOCATION_SPECIFIC_ERROR",
    12: "NOT_ALLOWED_CONFLICTING_INVOCATION",
}

# Intermediate statuses (command still in progress)
_INVOCATION_INTERMEDIATE_STATUSES = {1, 4}  # SENT, DELIVERED

# Exterior state enums (ExteriorService)
OPEN_STATUS_MAP: dict[int, str | None] = {
    0: None,
    1: "Open",
    2: "Closed",
    3: "Ajar",
}

ALARM_STATUS_MAP: dict[int, str | None] = {
    0: None,
    1: "Idle",
    2: "Triggered",
}

# Availability enums (AvailabilityService)
UNAVAILABLE_REASON_MAP: dict[int, str] = {
    1: "No internet",
    2: "Power saving mode",
    3: "Car in use",
    4: "OTA installation in progress",
    5: "Stolen vehicle tracking in progress",
    6: "Service mode active",
}

USAGE_MODE_MAP: dict[int, str] = {
    1: "Abandoned",
    2: "Inactive",
    3: "Convenience",
    4: "Active",
    5: "Driving",
    6: "Engine on",
    7: "Engine off",
}

# Weekday enum (ParkingClimateTimer)
WEEKDAY_MAP: dict[int, str] = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}
