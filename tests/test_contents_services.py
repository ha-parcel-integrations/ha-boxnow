"""Tests for the contents field, the update_contents action and the overlay."""

from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.boxnow.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import ACTIVE_CODE, DELIVERED_CODE, active_sample, delivered_sample

FETCH = "custom_components.boxnow.api.BoxNowApiClient.async_get_parcel"


def _fetch(code):
    return active_sample() if code == ACTIVE_CODE else delivered_sample()


async def _setup(hass, codes=(ACTIVE_CODE,)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={
            CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes],
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )
    entry.add_to_hass(hass)
    with patch(FETCH, new=AsyncMock(side_effect=_fetch)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, entry.runtime_data.coordinator


async def _call(hass, service, **data):
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


def _contents(coordinator):
    return {
        p["barcode"]: p["contents"] for p in coordinator.data + coordinator.delivered
    }


async def test_set_replace_clear_without_fetch_or_refresh(hass):
    _, coordinator = await _setup(hass)
    with (
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
        patch(FETCH, new=AsyncMock()) as fetch,
        patch.object(coordinator, "async_update_listeners") as listeners,
    ):
        await _call(
            hass, "update_contents", tracking_code=ACTIVE_CODE, contents=" Shoes "
        )
        assert _contents(coordinator)[ACTIVE_CODE] == "Shoes"
        assert listeners.call_count == 1

        await _call(
            hass, "update_contents", tracking_code=ACTIVE_CODE, contents="Shoes"
        )
        assert listeners.call_count == 1

        await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="Hose")
        assert _contents(coordinator)[ACTIVE_CODE] == "Hose"
        assert listeners.call_count == 2

        await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="")
        assert _contents(coordinator)[ACTIVE_CODE] is None
        assert listeners.call_count == 3

        await _call(hass, "update_contents", tracking_code=ACTIVE_CODE)
        assert listeners.call_count == 3
        refresh.assert_not_called()
        fetch.assert_not_called()


async def test_reapply_covers_delivered_parcels(hass):
    _, coordinator = await _setup(hass, (ACTIVE_CODE, DELIVERED_CODE))
    await _call(hass, "update_contents", tracking_code=DELIVERED_CODE, contents="Lamp")
    assert _contents(coordinator)[DELIVERED_CODE] == "Lamp"


async def test_poll_overlays_the_stored_value(hass):
    _, coordinator = await _setup(hass)
    await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="Shoes")
    with patch(FETCH, new=AsyncMock(side_effect=_fetch)):
        await coordinator.async_refresh()
    assert _contents(coordinator)[ACTIVE_CODE] == "Shoes"


async def test_untracked_code_can_be_set_and_is_used_when_tracked(hass):
    _, coordinator = await _setup(hass)
    await _call(hass, "update_contents", tracking_code=DELIVERED_CODE, contents="Lamp")
    assert coordinator.contents_store.get(DELIVERED_CODE) == "Lamp"
    with patch(FETCH, new=AsyncMock(side_effect=_fetch)):
        await _call(hass, "track_parcel", tracking_code=DELIVERED_CODE)
        await hass.async_block_till_done()
    assert _contents(coordinator)[DELIVERED_CODE] == "Lamp"


async def test_track_parcel_with_contents_refreshes_through_the_listener(hass):
    _, coordinator = await _setup(hass)
    with patch(FETCH, new=AsyncMock(side_effect=_fetch)) as fetch:
        await _call(hass, "track_parcel", tracking_code=DELIVERED_CODE, contents="Lamp")
        await hass.async_block_till_done()
    assert fetch.call_count >= 1
    assert _contents(coordinator)[DELIVERED_CODE] == "Lamp"


async def test_track_already_tracked_sets_contents_without_refresh(hass):
    _, coordinator = await _setup(hass)
    with (
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
        patch(FETCH, new=AsyncMock()) as fetch,
        patch.object(coordinator, "async_update_listeners") as listeners,
    ):
        await _call(hass, "track_parcel", tracking_code=ACTIVE_CODE, contents="Shoes")
        assert listeners.call_count == 1
        await _call(hass, "track_parcel", tracking_code=ACTIVE_CODE, contents="Shoes")
        await _call(hass, "track_parcel", tracking_code=ACTIVE_CODE)
        assert listeners.call_count == 1
        refresh.assert_not_called()
        fetch.assert_not_called()
    assert _contents(coordinator)[ACTIVE_CODE] == "Shoes"


async def test_untrack_drops_the_value(hass):
    _, coordinator = await _setup(hass)
    await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="Shoes")
    with patch(FETCH, new=AsyncMock(side_effect=_fetch)):
        await _call(hass, "untrack_parcel", tracking_code=ACTIVE_CODE)
        await hass.async_block_till_done()
    assert coordinator.contents_store.get(ACTIVE_CODE) is None


async def test_cap_drops_the_oldest_at_201(hass):
    _, coordinator = await _setup(hass)
    for i in range(201):
        await _call(hass, "update_contents", tracking_code=f"CODE{i:03d}", contents="x")
    assert coordinator.contents_store.get("CODE000") is None
    assert coordinator.contents_store.get("CODE001") == "x"
    assert coordinator.contents_store.get("CODE200") == "x"


async def test_update_contents_rejects_a_blank_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", tracking_code="  ", contents="x")


async def test_entry_without_a_store_file_works(hass):
    _, coordinator = await _setup(hass)
    assert _contents(coordinator)[ACTIVE_CODE] is None


async def test_remove_entry_deletes_the_store(hass):
    entry, _ = await _setup(hass)
    await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="Shoes")
    await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        "custom_components.boxnow.contents.ContentsStore.async_remove",
        new=AsyncMock(),
    ) as remove:
        await hass.config_entries.async_remove(entry.entry_id)
    remove.assert_awaited_once()


async def test_update_contents_raises_when_the_entry_is_not_loaded(hass):
    entry, _ = await _setup(hass)
    with patch(
        "custom_components.boxnow.services._resolve_entry",
        return_value=MockConfigEntry(domain=DOMAIN),
    ):
        with pytest.raises(ServiceValidationError, match="not loaded"):
            await _call(
                hass, "update_contents", tracking_code=ACTIVE_CODE, contents="x"
            )
        with pytest.raises(ServiceValidationError, match="not loaded"):
            await _call(hass, "track_parcel", tracking_code=ACTIVE_CODE, contents="x")
        # untrack without a loaded entry has no store to clear and must not raise
        await _call(hass, "untrack_parcel", tracking_code=ACTIVE_CODE)
    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None


async def test_services_raise_without_an_entry(hass):
    await _setup(hass)
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[]),
        pytest.raises(ServiceValidationError, match="not set up"),
    ):
        await _call(hass, "update_contents", tracking_code=ACTIVE_CODE, contents="x")
