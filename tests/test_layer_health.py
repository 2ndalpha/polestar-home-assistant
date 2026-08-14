"""Tests for the per-layer error tracker (_LayerHealth) in coordinator.py."""

from __future__ import annotations

import logging

import grpc
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.polestar_soc.coordinator import (
    LAYER_CEP,
    LAYER_GRAPHQL,
    LAYER_PCCS,
    _classify_error,
    _drop_none,
    _LayerHealth,
    _NonGrpcError,
)

from .conftest import make_rpc_error as _rpc_error

# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_grpc_permission_denied(self):
        err = _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        assert _classify_error(err) == "PERMISSION_DENIED"

    def test_grpc_unauthenticated(self):
        err = _rpc_error(grpc.StatusCode.UNAUTHENTICATED)
        assert _classify_error(err) == "UNAUTHENTICATED"

    def test_grpc_unavailable(self):
        err = _rpc_error(grpc.StatusCode.UNAVAILABLE)
        assert _classify_error(err) == "UNAVAILABLE"

    def test_grpc_no_code(self):
        # An RpcError instance whose code() returns None.
        class NoCodeErr(grpc.RpcError):
            def code(self):
                return None

        assert _classify_error(NoCodeErr()) == "RPC_ERROR"

    def test_grpc_code_raises(self):
        # An RpcError instance whose code() itself raises.
        class CodeRaises(grpc.RpcError):
            def code(self):
                raise Exception("internal")

        assert _classify_error(CodeRaises()) == "RPC_ERROR"

    def test_non_grpc_error(self):
        assert _classify_error(ValueError("x")) == "ValueError"

    def test_non_grpc_marker(self):
        assert _classify_error(_NonGrpcError("x")) == "_NonGrpcError"

    def test_graphql_field_undefined_is_a_schema_error(self):
        """The exact error Polestar returned in issue #22."""
        err = UpdateFailed(
            "GraphQL errors: Validation error of type FieldUndefined: Field "
            "'chargingStatus' in type 'BatteryV2' is undefined @ "
            "'carTelematicsV2/battery/chargingStatus'"
        )
        assert _classify_error(err) == "SCHEMA_ERROR"

    def test_other_validation_errors_also_classify_as_schema(self):
        """graphql-java uses the same prefix for every validation failure type."""
        err = UpdateFailed("GraphQL errors: Validation error of type UnknownType: Unknown type")
        assert _classify_error(err) == "SCHEMA_ERROR"

    def test_transient_update_failed_is_not_a_schema_error(self):
        assert _classify_error(UpdateFailed("timeout talking to API")) == "UpdateFailed"

    def test_non_graphql_exception_mentioning_validation_is_unaffected(self):
        """Only UpdateFailed is sniffed — gRPC and other errors keep their type name."""
        assert _classify_error(ValueError("validation error")) == "ValueError"


# ---------------------------------------------------------------------------
# _drop_none
# ---------------------------------------------------------------------------


class TestDropNone:
    def test_strips_none_values(self):
        assert _drop_none({"a": 1, "b": None, "c": 0}) == {"a": 1, "c": 0}

    def test_keeps_falsy_non_none(self):
        # 0, "", [] are falsy but not None — they must survive.
        assert _drop_none({"a": 0, "b": "", "c": []}) == {"a": 0, "b": "", "c": []}

    def test_empty_in_empty_out(self):
        assert _drop_none({}) == {}


# ---------------------------------------------------------------------------
# _LayerHealth
# ---------------------------------------------------------------------------


class TestLayerHealthInitialState:
    def test_starts_ok(self):
        h = _LayerHealth()
        snap = h.to_dict()
        for layer in (LAYER_PCCS, LAYER_CEP, LAYER_GRAPHQL):
            assert snap[layer]["status"] == "ok"
            assert snap[layer]["consecutive_failures"] == 0
            assert snap[layer]["last_code"] is None
            assert snap[layer]["last_success_at"] is None
            assert snap[layer]["failing_endpoints"] == []


class TestLayerHealthCycleAccounting:
    def test_clean_cycle_keeps_ok(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_success(LAYER_PCCS, "target_soc")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_PCCS]["status"] == "ok"
        assert snap[LAYER_PCCS]["consecutive_failures"] == 0
        assert snap[LAYER_PCCS]["last_success_at"] is not None

    def test_first_failure_marks_degraded(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED))
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_PCCS]["status"] == "degraded"
        assert snap[LAYER_PCCS]["consecutive_failures"] == 1
        assert snap[LAYER_PCCS]["last_code"] == "PERMISSION_DENIED"
        assert snap[LAYER_PCCS]["failing_endpoints"] == ["target_soc"]

    def test_two_consecutive_failures_marks_down(self):
        h = _LayerHealth()
        for _ in range(2):
            h.start_cycle()
            h.record_failure(
                LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
            )
            h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_PCCS]["status"] == "down"
        assert snap[LAYER_PCCS]["consecutive_failures"] == 2

    def test_partial_success_resets_counter_but_lists_endpoint(self):
        """Partial success keeps the layer ok but the failing endpoint is exposed."""
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(
            LAYER_GRAPHQL,
            "carTelematicsV2.battery",
            _NonGrpcError("connection reset"),
        )
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["status"] == "ok"
        assert snap[LAYER_GRAPHQL]["consecutive_failures"] == 0
        assert snap[LAYER_GRAPHQL]["failing_endpoints"] == ["carTelematicsV2.battery"]

    def test_recovery_resets_counter(self):
        h = _LayerHealth()
        for _ in range(3):
            h.start_cycle()
            h.record_failure(LAYER_CEP, "battery", _rpc_error(grpc.StatusCode.PERMISSION_DENIED))
            h.end_cycle()
        assert h.to_dict()[LAYER_CEP]["consecutive_failures"] == 3

        h.start_cycle()
        h.record_success(LAYER_CEP, "battery")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_CEP]["status"] == "ok"
        assert snap[LAYER_CEP]["consecutive_failures"] == 0

    def test_idle_cycle_unchanged(self):
        h = _LayerHealth()
        h.start_cycle()
        # No record_success / record_failure for any layer.
        h.end_cycle()
        snap = h.to_dict()
        for layer in (LAYER_PCCS, LAYER_CEP, LAYER_GRAPHQL):
            assert snap[layer]["status"] == "ok"
            assert snap[layer]["consecutive_failures"] == 0


def _schema_error(field: str = "chargingStatus") -> UpdateFailed:
    return UpdateFailed(
        f"GraphQL errors: Validation error of type FieldUndefined: "
        f"Field '{field}' in type 'BatteryV2' is undefined"
    )


class TestLayerHealthSchemaErrors:
    """Issue #22: a permanently broken sub-query used to keep reporting ``ok``.

    A partial-success cycle resets ``consecutive_failures`` to 0, so the layer
    looked healthy forever while one endpoint was dead.
    """

    def test_schema_break_with_partial_success_reports_degraded(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["status"] == "degraded"
        assert snap[LAYER_GRAPHQL]["consecutive_failures"] == 0
        assert snap[LAYER_GRAPHQL]["last_code"] == "SCHEMA_ERROR"
        assert snap[LAYER_GRAPHQL]["schema_endpoints"] == ["carTelematicsV2.battery"]

    def test_stays_degraded_across_later_cycles(self):
        """The flag is sticky — a schema break does not self-heal."""
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()

        for _ in range(3):
            h.start_cycle()
            h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
            h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
            h.end_cycle()

        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["status"] == "degraded"
        assert snap[LAYER_GRAPHQL]["schema_endpoints"] == ["carTelematicsV2.battery"]

    def test_names_the_culprit_after_failing_endpoints_is_cleared(self):
        """start_cycle wipes failing_endpoints; schema_endpoints must survive it."""
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()

        # A cycle where the broken endpoint is not even attempted.
        h.start_cycle()
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()

        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["failing_endpoints"] == []
        assert snap[LAYER_GRAPHQL]["schema_endpoints"] == ["carTelematicsV2.battery"]
        assert snap[LAYER_GRAPHQL]["status"] == "degraded"

    def test_success_on_the_broken_endpoint_restores_ok(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()
        assert h.to_dict()[LAYER_GRAPHQL]["status"] == "degraded"

        h.start_cycle()
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.battery")
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()

        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["status"] == "ok"
        assert snap[LAYER_GRAPHQL]["schema_endpoints"] == []

    def test_transient_partial_failure_still_reports_ok(self):
        """Only schema errors are sticky — outages keep the existing behaviour."""
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", UpdateFailed("timeout"))
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_GRAPHQL]["status"] == "ok"
        assert snap[LAYER_GRAPHQL]["schema_endpoints"] == []

    def test_a_full_outage_still_outranks_a_schema_break(self):
        """Two dead cycles must read ``down``, not stay pinned at ``degraded``."""
        h = _LayerHealth()
        for _ in range(2):
            h.start_cycle()
            h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
            h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.odometer", _schema_error())
            h.end_cycle()
        assert h.to_dict()[LAYER_GRAPHQL]["status"] == "down"

    def test_other_layers_unaffected(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_GRAPHQL, "carTelematicsV2.battery", _schema_error())
        h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
        h.record_success(LAYER_CEP, "cep_battery")
        h.end_cycle()
        snap = h.to_dict()
        assert snap[LAYER_CEP]["status"] == "ok"
        assert snap[LAYER_CEP]["schema_endpoints"] == []


class TestLayerHealthWarnOnce:
    def test_first_failure_warns(self, caplog: pytest.LogCaptureFixture):
        h = _LayerHealth()
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            h.start_cycle()
            h.record_failure(
                LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
            )
            h.end_cycle()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "PERMISSION_DENIED" in warnings[0].getMessage()
        assert "target_soc" in warnings[0].getMessage()

    def test_repeat_failure_does_not_re_warn(self, caplog: pytest.LogCaptureFixture):
        h = _LayerHealth()
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            for _ in range(3):
                h.start_cycle()
                h.record_failure(
                    LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
                )
                h.end_cycle()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]

    def test_recovery_then_failure_warns_again(self, caplog: pytest.LogCaptureFixture):
        h = _LayerHealth()
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            h.start_cycle()
            h.record_failure(
                LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
            )
            h.end_cycle()

            # Full clean cycle — should clear warned-set.
            h.start_cycle()
            h.record_success(LAYER_PCCS, "target_soc")
            h.end_cycle()

            # New failure → fresh warning expected.
            h.start_cycle()
            h.record_failure(
                LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
            )
            h.end_cycle()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    def test_partial_success_does_not_clear_warned_set(self, caplog: pytest.LogCaptureFixture):
        """If one endpoint is persistently broken and others succeed, do not re-warn."""
        h = _LayerHealth()
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            for _ in range(5):
                h.start_cycle()
                h.record_failure(
                    LAYER_GRAPHQL,
                    "carTelematicsV2.battery",
                    _NonGrpcError("connection reset"),
                )
                h.record_success(LAYER_GRAPHQL, "carTelematicsV2.odometer")
                h.end_cycle()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]

    def test_different_codes_each_warn(self, caplog: pytest.LogCaptureFixture):
        h = _LayerHealth()
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            h.start_cycle()
            h.record_failure(
                LAYER_PCCS, "target_soc", _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
            )
            h.record_failure(LAYER_PCCS, "amp_limit", _rpc_error(grpc.StatusCode.UNAVAILABLE))
            h.end_cycle()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2


class TestLayerHealthFailingEndpointsLifecycle:
    def test_failing_endpoints_cleared_at_start_of_cycle(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_PCCS, "a", _rpc_error(grpc.StatusCode.PERMISSION_DENIED))
        h.end_cycle()
        assert h.to_dict()[LAYER_PCCS]["failing_endpoints"] == ["a"]

        # Next cycle: previously failing endpoint succeeds.
        h.start_cycle()
        h.record_success(LAYER_PCCS, "a")
        h.end_cycle()
        assert h.to_dict()[LAYER_PCCS]["failing_endpoints"] == []

    def test_failing_endpoints_sorted(self):
        h = _LayerHealth()
        h.start_cycle()
        h.record_failure(LAYER_PCCS, "z", _rpc_error(grpc.StatusCode.PERMISSION_DENIED))
        h.record_failure(LAYER_PCCS, "a", _rpc_error(grpc.StatusCode.PERMISSION_DENIED))
        h.end_cycle()
        assert h.to_dict()[LAYER_PCCS]["failing_endpoints"] == ["a", "z"]
