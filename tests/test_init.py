"""Tests for setting up the integration and the polling interval it runs at."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.polestar_soc.const import CONF_SCAN_INTERVAL_MINUTES

from .conftest import set_up_entry as _set_up


async def _advance(hass: HomeAssistant, delta: timedelta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + delta)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations")
class TestPollingInterval:
    async def test_polls_every_five_minutes_by_default(self, hass: HomeAssistant, fetch: AsyncMock):
        entry = await _set_up(hass, {})
        assert fetch.await_count == 1

        await _advance(hass, timedelta(minutes=4))
        assert fetch.await_count == 1

        await _advance(hass, timedelta(minutes=5, seconds=1))
        assert fetch.await_count == 2

        assert await hass.config_entries.async_unload(entry.entry_id)

    async def test_polls_every_minute_when_configured(self, hass: HomeAssistant, fetch: AsyncMock):
        entry = await _set_up(hass, {CONF_SCAN_INTERVAL_MINUTES: 1})

        await _advance(hass, timedelta(minutes=1, seconds=1))
        assert fetch.await_count == 2

        assert await hass.config_entries.async_unload(entry.entry_id)

    async def test_changing_the_interval_applies_without_a_restart(
        self, hass: HomeAssistant, fetch: AsyncMock
    ):
        entry = await _set_up(hass, {})
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL_MINUTES: 1}
        )
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        polls_after_reload = fetch.await_count

        await _advance(hass, timedelta(minutes=1, seconds=1))
        assert fetch.await_count == polls_after_reload + 1

        assert await hass.config_entries.async_unload(entry.entry_id)
