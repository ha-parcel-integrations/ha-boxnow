"""Tests for the BoxNow API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.boxnow.api import (
    BoxNowApiClient,
    BoxNowApiError,
)

CODE = "EXAMPLE123456"
API_URL = "https://api-production.boxnow.gr/api/v1/parcels:track"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_sends_bare_post_with_no_auth():
    session = _session_returning(200, {"data": [{"id": CODE, "state": "delivered"}]})
    client = BoxNowApiClient(session, API_URL)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["id"] == CODE
    args, kwargs = session.post.call_args
    assert args[0] == API_URL
    assert kwargs["json"] == {"parcelId": CODE}
    headers = kwargs["headers"]
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "Accept" in headers
    assert "Content-Type" in headers
    assert "User-Agent" in headers


async def test_get_parcel_returns_none_on_missing_data_key():
    client = BoxNowApiClient(_session_returning(200, {}), API_URL)
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_on_non_array_data():
    client = BoxNowApiClient(_session_returning(200, {"data": "not-a-list"}), API_URL)
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_on_empty_data():
    client = BoxNowApiClient(_session_returning(200, {"data": []}), API_URL)
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_on_non_dict_item():
    client = BoxNowApiClient(_session_returning(200, {"data": ["not-a-dict"]}), API_URL)
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_handles_multiple_data_items_without_crashing():
    """Should not normally occur for a single parcelId, but must not crash."""
    client = BoxNowApiClient(_session_returning(
            200,
            {
                "data": [
                    {"id": CODE, "state": "delivered"},
                    {"id": "OTHER", "state": "missing"},
                ]
            },
        ), API_URL)
    parcel = await client.async_get_parcel(CODE)
    assert parcel["id"] == CODE


async def test_get_parcel_raises_on_error_status():
    client = BoxNowApiClient(_session_returning(500, {}), API_URL)
    with pytest.raises(BoxNowApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = BoxNowApiClient(_session_returning(200, "not json"), API_URL)
    with pytest.raises(BoxNowApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = BoxNowApiClient(_session_returning(200, ["not", "a", "dict"]), API_URL)
    with pytest.raises(BoxNowApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_429_with_retry_after_header():
    session = _session_returning(429, {})
    session.post.return_value.__aenter__.return_value.headers = {"Retry-After": "30"}
    client = BoxNowApiClient(session, API_URL)
    with pytest.raises(BoxNowApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 30.0


async def test_get_parcel_raises_on_429_with_unparseable_retry_after():
    session = _session_returning(429, {})
    session.post.return_value.__aenter__.return_value.headers = {
        "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"
    }
    client = BoxNowApiClient(session, API_URL)
    with pytest.raises(BoxNowApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = BoxNowApiClient(session, API_URL)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
