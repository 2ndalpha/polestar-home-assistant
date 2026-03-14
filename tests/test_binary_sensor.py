"""Tests for binary_sensor platform — vehicle exterior state."""

from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.polestar_soc.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    PolestarBinarySensor,
)
from custom_components.polestar_soc.const import DOMAIN

VIN = "YSMYKEAE1RB000001"


def _make_sensor(
    coordinator_data: dict | None,
    vehicle: dict,
    vin: str,
    key: str = "front_left_door",
) -> PolestarBinarySensor:
    """Create a PolestarBinarySensor with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    desc = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == key)
    return PolestarBinarySensor(coordinator, desc, vehicle, vin)


class TestDoorIsOn:
    def test_closed(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is False  # fixture has CLOSED(2)

    def test_open(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["front_left_door"] = 1  # OPEN
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is True

    def test_ajar(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["front_left_door"] = 3  # AJAR
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is True

    def test_unspecified(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["front_left_door"] = 0  # UNSPECIFIED
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is None


class TestAlarmIsOn:
    def test_idle(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "alarm")
        assert sensor.is_on is False  # fixture has IDLE(1)

    def test_triggered(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["alarm"] = 2  # TRIGGERED
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "alarm")
        assert sensor.is_on is True

    def test_unspecified(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["alarm"] = 0  # UNSPECIFIED
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "alarm")
        assert sensor.is_on is None


class TestBinarySensorEntity:
    def test_unique_id(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.unique_id == f"{VIN}_front_left_door"

    def test_device_info(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.device_info["identifiers"] == {(DOMAIN, VIN)}
        assert sensor.device_info["manufacturer"] == "Polestar"

    def test_device_class_door(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.device_class == BinarySensorDeviceClass.DOOR

    def test_device_class_window(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_window")
        assert sensor.device_class == BinarySensorDeviceClass.WINDOW

    def test_device_class_opening(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "hood")
        assert sensor.device_class == BinarySensorDeviceClass.OPENING

    def test_device_class_safety(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "alarm")
        assert sensor.device_class == BinarySensorDeviceClass.SAFETY


class TestExtraStateAttributes:
    def test_door_closed_label(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.extra_state_attributes["raw_state"] == "Closed"

    def test_door_ajar_label(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["front_left_door"] = 3
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.extra_state_attributes["raw_state"] == "Ajar"

    def test_alarm_idle_label(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "alarm")
        assert sensor.extra_state_attributes["raw_state"] == "Idle"

    def test_unspecified_returns_none(self, sample_coordinator_data, sample_vehicle):
        """UNSPECIFIED(0) exterior value → extra_state_attributes is None."""
        sample_coordinator_data["exterior"][VIN]["sunroof"] = 0
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "sunroof")
        # sunroof is 0 in fixture, raw_val is 0 which is falsy → returns None
        assert sensor.extra_state_attributes is None


class TestAvailabilityIsOn:
    def test_available(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.is_on is True  # fixture has AVAILABLE(1)

    def test_unavailable(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["availability"][VIN]["availability_status"] = 2  # UNAVAILABLE
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.is_on is False

    def test_unspecified(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["availability"][VIN]["availability_status"] = None
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.is_on is None

    def test_no_availability_data(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["availability"] = {}
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.is_on is None

    def test_extra_attrs_with_reason(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["availability"][VIN]["unavailable_reason"] = 2  # POWER_SAVING
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.extra_state_attributes == {"unavailable_reason": "Power saving mode"}

    def test_extra_attrs_no_reason(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.extra_state_attributes == {"unavailable_reason": None}

    def test_device_class_connectivity(self, sample_coordinator_data, sample_vehicle):
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "vehicle_available")
        assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY


class TestNoneHandling:
    def test_none_when_no_coordinator_data(self, sample_vehicle):
        sensor = _make_sensor(None, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is None
        assert sensor.extra_state_attributes is None

    def test_none_when_no_exterior_data(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"] = {}
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is None
        assert sensor.extra_state_attributes is None

    def test_none_when_field_is_none(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["front_left_door"] = None
        sensor = _make_sensor(sample_coordinator_data, sample_vehicle, VIN, "front_left_door")
        assert sensor.is_on is None
        assert sensor.extra_state_attributes is None


class TestDescriptionCounts:
    def test_total_descriptions(self):
        assert len(BINARY_SENSOR_DESCRIPTIONS) == 14

    def test_enabled_by_default_count(self):
        enabled = [d for d in BINARY_SENSOR_DESCRIPTIONS if d.entity_registry_enabled_default]
        assert len(enabled) == 5  # vehicle_available + 4 doors

    def test_disabled_by_default_count(self):
        disabled = [d for d in BINARY_SENSOR_DESCRIPTIONS if not d.entity_registry_enabled_default]
        assert len(disabled) == 9  # 4 windows + hood + tailgate + tank_lid + sunroof + alarm
