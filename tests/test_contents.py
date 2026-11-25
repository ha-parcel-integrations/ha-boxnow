"""Tests for the contents helpers and the per-entry store."""

from unittest.mock import patch

from custom_components.boxnow.contents import (
    CONTENTS_MAX_LEN,
    CONTENTS_STORE_MAX_CODES,
    ContentsStore,
    clean_contents,
    join_contents,
    normalize_code,
)


def test_clean_trims_and_collapses_whitespace():
    assert clean_contents("  Garden \n  hose  ") == "Garden hose"


def test_clean_caps_the_length():
    assert clean_contents("x" * 500) == "x" * CONTENTS_MAX_LEN


def test_clean_drops_empty_non_string_and_filler():
    assert clean_contents("   ") is None
    assert clean_contents(None) is None
    assert clean_contents(5) is None
    assert clean_contents("N/A", frozenset({"n/a"})) is None


def test_join_dedupes_and_drops_empties():
    assert join_contents(["Shoes", "", "Shoes", "Hose"]) == "Shoes; Hose"
    assert join_contents(["", None]) is None


def test_normalize_code_uppercases():
    assert normalize_code(" ab1 ") == "AB1"


async def test_store_round_trip_and_unchanged(hass):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.async_set("ab1", " Shoes ") is True
    assert store.get("AB1") == "Shoes"
    assert store.async_set("AB1", "Shoes") is False
    assert store.async_set("AB1", "Hose") is True
    assert store.async_set("AB1", "") is True
    assert store.get("AB1") is None
    assert store.async_set("AB1", None) is False


async def test_store_cap_drops_the_oldest(hass):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    for i in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"C{i}", "x")
    assert store.get("C0") is None
    assert store.get("C1") == "x"
    assert store.get(f"C{CONTENTS_STORE_MAX_CODES}") == "x"


async def test_store_loads_saved_data_and_ignores_junk(hass):
    store = ContentsStore(hass, "e1")
    with patch.object(
        store._store,
        "async_load",
        return_value={"by_code": {"ab1": " Shoes ", "x": 5, "y": ""}},
    ):
        await store.async_load()
    assert store.get("AB1") == "Shoes"
    assert store.get("X") is None
    with patch.object(store._store, "async_load", return_value=None):
        await store.async_load()
    assert store.get("AB1") is None
    with patch.object(store._store, "async_load", return_value={"by_code": "bad"}):
        await store.async_load()
    assert store.get("AB1") is None


async def test_store_remove_clears(hass):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    store.async_set("AB1", "Shoes")
    await store.async_remove()
    assert store.get("AB1") is None
