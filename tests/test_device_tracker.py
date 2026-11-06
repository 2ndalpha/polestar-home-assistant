"""Tests for device_tracker platform — vehicle GPS location."""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.device_tracker import SourceType
from homeassistant.const import EVENT_STATE_CHANGED, STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.polestar_soc.const import DOMAIN
from custom_components.polestar_soc.device_tracker import PolestarDeviceTracker

from .conftest import set_up_entry

VIN = "YSMYKEAE1RB000001"


def _make_tracker(coordinator_data: dict, vehicle: dict, vin: str) -> PolestarDeviceTracker:
    """Create a PolestarDeviceTracker with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    return PolestarDeviceTracker(coordinator, vehicle, vin)


class TestPolestarDeviceTracker:
    def test_latitude(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.latitude == 59.329323

    def test_longitude(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.longitude == 18.068581

    def test_source_type(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.source_type == SourceType.GPS

    def test_unique_id(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.unique_id == f"{VIN}_location"

    def test_device_info(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.device_info["identifiers"] == {(DOMAIN, VIN)}
        assert tracker.device_info["manufacturer"] == "Polestar"

    def test_extra_state_attributes(self, sample_coordinator_data, sample_vehicle):
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        attrs = tracker.extra_state_attributes
        assert attrs is not None
        assert "location_timestamp" in attrs
        assert attrs["location_timestamp"] == "2026-03-08T17:14:18.845000+00:00"

    def test_none_when_no_location_data(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["location"] = {}
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.latitude is None
        assert tracker.longitude is None
        assert tracker.extra_state_attributes is None

    def test_none_when_no_coordinator_data(self, sample_vehicle):
        coordinator_data = None
        coordinator = MagicMock()
        coordinator.data = coordinator_data
        tracker = PolestarDeviceTracker(coordinator, sample_vehicle, VIN)
        assert tracker.latitude is None
        assert tracker.longitude is None
        assert tracker.extra_state_attributes is None

    def test_none_when_location_values_none(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["location"][VIN] = {
            "latitude": None,
            "longitude": None,
            "timestamp_ms": None,
        }
        tracker = _make_tracker(sample_coordinator_data, sample_vehicle, VIN)
        assert tracker.latitude is None
        assert tracker.longitude is None
        assert tracker.extra_state_attributes is None


@pytest.mark.usefixtures("enable_custom_integrations")
class TestTrackerStateWrites:
    @pytest.fixture
    async def entry(self, hass: HomeAssistant, fetch: AsyncMock) -> MockConfigEntry:
        return await set_up_entry(hass)

    @pytest.fixture
    def entity_id(self, hass: HomeAssistant, entry: MockConfigEntry) -> str:
        entity_id = er.async_get(hass).async_get_entity_id(
            "device_tracker", DOMAIN, f"{VIN}_location"
        )
        assert entity_id is not None
        return entity_id

    async def _refresh_with_location(
        self, hass: HomeAssistant, entry: MockConfigEntry, fetch: AsyncMock, location: dict
    ) -> None:
        data = copy.deepcopy(fetch.return_value)
        data["location"][VIN] = location
        fetch.return_value = data
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()

    async def test_refresh_with_unchanged_location_writes_no_state_change(
        self, hass: HomeAssistant, entry: MockConfigEntry, entity_id: str, fetch: AsyncMock, freezer
    ):
        before = hass.states.get(entity_id)
        events = async_capture_events(hass, EVENT_STATE_CHANGED)

        freezer.tick(300)
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()

        assert fetch.await_count == 2
        assert [e for e in events if e.data["entity_id"] == entity_id] == []
        after = hass.states.get(entity_id)
        assert after.last_changed == before.last_changed
        assert after.last_updated == before.last_updated

    async def test_moving_within_the_same_zone_updates_attributes_and_keeps_last_changed(
        self, hass: HomeAssistant, entry: MockConfigEntry, entity_id: str, fetch: AsyncMock, freezer
    ):
        before = hass.states.get(entity_id)
        assert before.state == STATE_NOT_HOME

        freezer.tick(300)
        await self._refresh_with_location(
            hass,
            entry,
            fetch,
            {"latitude": 59.4, "longitude": 18.1, "timestamp_ms": 1772990358845},
        )

        after = hass.states.get(entity_id)
        assert after.state == STATE_NOT_HOME
        assert after.attributes["latitude"] == 59.4
        assert after.attributes["location_timestamp"] == "2026-03-08T17:19:18.845000+00:00"
        assert after.last_updated > before.last_updated
        assert after.last_changed == before.last_changed

    async def test_entering_the_home_zone_moves_last_changed(
        self, hass: HomeAssistant, entry: MockConfigEntry, entity_id: str, fetch: AsyncMock, freezer
    ):
        before = hass.states.get(entity_id)

        freezer.tick(300)
        await self._refresh_with_location(
            hass,
            entry,
            fetch,
            {
                "latitude": hass.config.latitude,
                "longitude": hass.config.longitude,
                "timestamp_ms": 1772990358845,
            },
        )

        after = hass.states.get(entity_id)
        assert after.state == STATE_HOME
        assert after.last_changed > before.last_changed
