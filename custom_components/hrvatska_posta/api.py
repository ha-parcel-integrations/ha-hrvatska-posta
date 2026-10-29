"""Hrvatska pošta public tracking client.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown (a
  normal, expected state — never an error),
* raises :class:`HrvatskaPostaApiError` for anything else, with
  ``status_code`` set on a non-2xx response and ``retry_after`` set when the
  carrier's own ``Retry-After`` header on a 429 could be parsed as seconds,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import MAX_BODY_BYTES, TRACKING_API_URL
from .parcels import NEW_ISSUE_URL, parse_clock
from .parser import BOT_WALL_MARKERS, HrvatskaPostaParseError, parse_page

_LOGGER = logging.getLogger(__name__)

_WEIGHT_RE = re.compile(r"^(\d+) g$")

# Fixed warning texts already logged this session, so each is shown once.
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """Log a fixed, value-free warning once per HA session."""
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning("%s Open an issue and paste this line: %s", message, NEW_ISSUE_URL)


class HrvatskaPostaApiError(Exception):
    """Raised when a Hrvatska pošta API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Hrvatska pošta API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class HrvatskaPostaApiClient:
    """Client for the public Hrvatska pošta tracking page.

    No authentication: the page is keyed on the tracking code alone and
    answers HTTP 200 for known and unknown codes alike.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parsed record for a known parcel, or ``None`` when the
        page reports the number as unknown (which includes a number whose
        history has aged out). Any reply that cannot be positively read
        raises :class:`HrvatskaPostaApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        code = tracking_code.upper()
        url = TRACKING_API_URL.format(tracking_code=quote(code, safe=""))
        async with self._session.get(
            url, headers={"Accept": "text/html"}, allow_redirects=False
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds
                raise HrvatskaPostaApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise HrvatskaPostaApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            # read(n) returns whatever has arrived, not the whole body, so
            # collect chunks until the end or the cap.
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    break
            body = b"".join(chunks)

        if len(body) > MAX_BODY_BYTES:
            raise HrvatskaPostaApiError("reply too large")
        html = body.decode("utf-8", errors="replace")
        if not html.strip():
            raise HrvatskaPostaApiError("empty reply")
        if any(marker in html for marker in BOT_WALL_MARKERS):
            _warn_once(
                "bot-wall",
                "Hrvatska pošta answered with a bot-protection page instead of "
                "tracking data.",
            )
            raise HrvatskaPostaApiError("bot-protection page")

        try:
            parsed = parse_page(html, code)
        except HrvatskaPostaParseError as err:
            _warn_once(
                f"parse:{err}",
                f"Hrvatska pošta's tracking page was not recognised ({err}).",
            )
            raise HrvatskaPostaApiError(f"unexpected reply ({err})") from err
        if parsed is None:
            return None

        weight = parsed.pop("weight")
        parsed["weightGrams"] = self._grams(weight)
        rows = []
        for row in parsed["rows"]:
            if row["wording"] and parse_clock(
                row["date"], row["time"], row["location"]
            ):
                rows.append(row)
            else:
                _warn_once(
                    "bad-row",
                    "Hrvatska pošta returned a scan row that could not be read.",
                )
        if not rows:
            raise HrvatskaPostaApiError("no readable scan rows")
        parsed["rows"] = rows
        return parsed

    @staticmethod
    def _grams(weight: str | None) -> int | None:
        """Return the weight in grams, or ``None`` when absent or odd."""
        if weight is None:
            return None
        match = _WEIGHT_RE.match(weight)
        if match is None:
            _warn_once(
                "bad-weight",
                "Hrvatska pošta returned a weight in an unrecognised format.",
            )
            return None
        return int(match.group(1))
