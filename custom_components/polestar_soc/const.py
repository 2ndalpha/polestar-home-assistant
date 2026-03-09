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

# Mobile app client — used for PCCS gRPC (requires broader scope + 2SV)
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
