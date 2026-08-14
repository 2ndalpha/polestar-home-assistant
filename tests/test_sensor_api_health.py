"""Tests for the diagnostic API health sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.polestar_soc.coordinator import (
    LAYER_CEP,
    LAYER_GRAPHQL,
    LAYER_PCCS,
)
from custom_components.polestar_soc.sensor import PolestarApiHealthSensor


def _layer(
    *,
    status: str = "ok",
    last_code: str | None = None,
    last_success_at: str | None = None,
    failing_endpoints: list[str] | None = None,
    schema_endpoints: list[str] | None = None,
    consecutive_failures: int = 0,
) -> dict:
    return {
        "status": status,
        "last_code": last_code,
        "last_success_at": last_success_at,
        "failing_endpoints": failing_endpoints or [],
        "schema_endpoints": schema_endpoints or [],
        "consecutive_failures": consecutive_failures,
    }


def _coordinator_with_health(api_health: dict | None) -> MagicMock:
    coord = MagicMock()
    coord.data = {"api_health": api_health} if api_health is not None else {}
    coord.last_update_success = True
    return coord


def _make_sensor(api_health: dict | None) -> PolestarApiHealthSensor:
    coord = _coordinator_with_health(api_health)
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    return PolestarApiHealthSensor(coord, entry)


# ---------------------------------------------------------------------------
# Static configuration
# ---------------------------------------------------------------------------


class TestSensorConfiguration:
    def test_diagnostic_category(self):
        s = _make_sensor(None)
        assert s._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_enum_device_class(self):
        s = _make_sensor(None)
        assert s._attr_device_class == SensorDeviceClass.ENUM
        assert s._attr_options == ["ok", "degraded", "down"]

    def test_unique_id_includes_entry_id(self):
        s = _make_sensor(None)
        assert s.unique_id == "polestar_soc_test_entry_id_api_health"

    def test_no_device_association(self):
        """Diagnostic sensor has no device — it appears at integration level."""
        s = _make_sensor(None)
        assert s._attr_device_info is None or not getattr(s, "_attr_device_info", None)


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


class TestSensorState:
    def test_all_layers_ok(self):
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(),
            }
        )
        assert s.native_value == "ok"

    def test_one_degraded_layer_marks_degraded(self):
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(consecutive_failures=1, status="degraded"),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(),
            }
        )
        assert s.native_value == "degraded"

    def test_one_down_layer_marks_down(self):
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(consecutive_failures=2, status="down"),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(),
            }
        )
        assert s.native_value == "down"

    def test_down_dominates_degraded(self):
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(consecutive_failures=1, status="degraded"),
                LAYER_CEP: _layer(consecutive_failures=5, status="down"),
                LAYER_GRAPHQL: _layer(),
            }
        )
        assert s.native_value == "down"

    def test_status_drives_state_not_the_failure_counter(self):
        """A sticky schema break reports degraded with a counter of 0.

        The counter is per-cycle and a partial-success cycle resets it, so
        deriving state from it here would have re-hidden issue #22.
        """
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(
                    consecutive_failures=0,
                    status="degraded",
                    schema_endpoints=["carTelematicsV2.battery"],
                ),
            }
        )
        assert s.native_value == "degraded"

    def test_unrecognised_status_is_ignored(self):
        """A future status string must not raise out of the ranking."""
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(status="catastrophic"),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(),
            }
        )
        assert s.native_value == "ok"

    def test_no_data_returns_ok(self):
        s = _make_sensor(None)
        assert s.native_value == "ok"

    def test_empty_api_health_returns_ok(self):
        s = _make_sensor({})
        assert s.native_value == "ok"


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------


class TestSensorAttributes:
    def test_attributes_for_each_layer(self):
        s = _make_sensor(
            {
                LAYER_PCCS: _layer(
                    status="down",
                    last_code="PERMISSION_DENIED",
                    last_success_at="2026-05-01T12:00:00+00:00",
                    failing_endpoints=["target_soc", "amp_limit"],
                    consecutive_failures=3,
                ),
                LAYER_CEP: _layer(),
                LAYER_GRAPHQL: _layer(
                    status="degraded",
                    last_code="SCHEMA_ERROR",
                    schema_endpoints=["carTelematicsV2.battery"],
                ),
            }
        )
        attrs = s.extra_state_attributes
        assert attrs["pccs_status"] == "down"
        assert attrs["last_pccs_code"] == "PERMISSION_DENIED"
        assert attrs["last_pccs_success_at"] == "2026-05-01T12:00:00+00:00"
        assert attrs["pccs_failing_endpoints"] == ["target_soc", "amp_limit"]
        assert attrs["pccs_consecutive_failures"] == 3
        # The schema-broken endpoint is named for drill-down.
        assert attrs["graphql_schema_endpoints"] == ["carTelematicsV2.battery"]
        assert attrs["last_graphql_code"] == "SCHEMA_ERROR"
        assert attrs["pccs_schema_endpoints"] == []
        # All three layers' fields are present.
        for layer in ("pccs", "cep", "graphql"):
            for suffix in (
                "_status",
                "_failing_endpoints",
                "_schema_endpoints",
                "_consecutive_failures",
            ):
                assert f"{layer}{suffix}" in attrs
            assert f"last_{layer}_code" in attrs
            assert f"last_{layer}_success_at" in attrs

    def test_no_data_returns_empty_attrs(self):
        s = _make_sensor(None)
        assert s.extra_state_attributes == {}
