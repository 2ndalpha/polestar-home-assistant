"""Constants for the Polestar State of Charge integration."""

from datetime import timedelta

DOMAIN = "polestar_soc"
SCAN_INTERVAL = timedelta(minutes=5)

# OAuth2 / OIDC constants
OIDC_BASE_URL = "https://polestarid.eu.polestar.com"
OIDC_AUTH_URL = f"{OIDC_BASE_URL}/as/authorization.oauth2"
OIDC_TOKEN_URL = f"{OIDC_BASE_URL}/as/token.oauth2"
CLIENT_ID = "l3oopkc_10"
REDIRECT_URI = "https://www.polestar.com/sign-in-callback"
SCOPE = "openid profile email customer:attributes"

# GraphQL API
API_URL = "https://pc-api.polestar.com/eu-north-1/mystar-v2/"

# PCCS gRPC API
PCCS_API_HOST = "api.pccs-prod.plstr.io"

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
