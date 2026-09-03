"""BoxNow public tracking API client.

``async_get_parcel`` returns the raw per-parcel dict on success, returns
``None`` when the carrier reports no matching parcel (empty/missing/non-array
``data``), and raises :class:`BoxNowApiError` for anything else, with
``status_code`` set on a non-2xx response and ``retry_after`` set when the
carrier's own ``Retry-After`` header on a 429 could be parsed as seconds — the
coordinator's backoff reads both. ``aiohttp.ClientError`` propagates
untouched — ``DataUpdateCoordinator`` already wraps those into
``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Browser-like headers only — no cookie, API key or account credential is
# part of the confirmed live-200 request.
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


class BoxNowApiError(Exception):
    """Raised when a BoxNow API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"BoxNow API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class BoxNowApiClient:
    """Client for the public BoxNow ``parcels:track`` endpoint.

    No authentication: the endpoint is keyed on the ``parcelId`` in the POST
    body alone. It answers HTTP 200 with ``{"data": [...]}`` — one item per
    requested parcel, normally exactly one for a single ``parcelId``. A
    missing ``data`` key, a non-array ``data``, or an empty ``data`` array are
    all treated as "no such parcel" — the exact error body for an unknown
    ``parcelId`` was never captured, so branch defensively rather than
    matching an invented shape.
    """

    def __init__(self, session: aiohttp.ClientSession, api_url: str) -> None:
        """Initialise the client with an aiohttp session and country backend."""
        self._session = session
        self._api_url = api_url

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        endpoint reports no matching parcel. Any other failure envelope or
        non-2xx status raises :class:`BoxNowApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        async with self._session.post(
            self._api_url,
            json={"parcelId": tracking_code},
            headers=_HEADERS,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise BoxNowApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if not 200 <= response.status < 300:
                raise BoxNowApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise BoxNowApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise BoxNowApiError("unexpected body (not a JSON object)")

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            # Missing key, non-array, or empty array — never manufacture a
            # parcel from a bare HTTP 200.
            return None

        # A single ``parcelId`` request should return one item; do not assume
        # it always does — take the first and move on
        # rather than crashing the whole poll on an unexpected extra item.
        parcel = data[0]
        if not isinstance(parcel, dict):
            return None
        return parcel
