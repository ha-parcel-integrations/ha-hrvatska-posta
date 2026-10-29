"""Reader for the Hrvatska pošta tracking reply.

The reply has no structured alternative, so this module fails closed: anything
it cannot positively identify raises :class:`HrvatskaPostaParseError` instead
of yielding a partial parcel. Only the ``track__n__trace`` block is read, and
scan rows are picked by position inside their columns because the class of the
wording span varies from row to row.
"""

from __future__ import annotations

from html.parser import HTMLParser
import re

TITLE_FOUND = "Hrvatska Pošta - Tracking"
TITLE_NO_DATA = "Hrvatska Pošta - Nema podataka"
NO_DATA_NOTICE = "Nema podataka za uneseni prijamni broj pošiljke"
BAD_FORMAT_NOTICE = "Neispravan format prijamnog broja pošiljke"

BOT_WALL_MARKERS = (
    "Request Rejected",
    "Access Denied",
    "Just a moment",
    "cf-turnstile",
)

_TRACE_CLASS = "track__n__trace"
_VOID_TAGS = frozenset(
    {"br", "hr", "img", "input", "link", "meta", "area", "base", "col", "wbr"}
)
_IGNORED_TEXT_TAGS = frozenset({"script", "style"})
_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_TIME_RE = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")
_LABEL_NUMBER = "Broj pošiljke"
_LABEL_WEIGHT = "Masa"


class HrvatskaPostaParseError(Exception):
    """Raised when the reply does not have the expected shape."""


def clean_text(value: str) -> str:
    """Collapse whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


class _Node:
    """One open element on the parser's stack."""

    __slots__ = ("classes", "parts", "tag")

    def __init__(self, tag: str, classes: frozenset[str]) -> None:
        self.tag = tag
        self.classes = classes
        self.parts: list[str] = []


class _PageParser(HTMLParser):
    """Collect the title, the visible text and the trace block's contents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.visible: list[str] = []
        self.number: str | None = None
        self.weight: str | None = None
        self.logos: list[str] = []
        self.rows: list[dict[str, str | None]] = []
        self.has_trace = False
        self._stack: list[_Node] = []
        self._ignored = 0
        self._in_title = False
        self._trace_depth: int | None = None
        self._row_depth: int | None = None
        self._row: dict[str, str | None] = {}
        self._column: str | None = None
        self._column_depth: int | None = None
        self._column_spans: list[str] = []
        self._label: str | None = None

    # -- structure helpers -------------------------------------------------

    def _in_trace(self) -> bool:
        return self._trace_depth is not None

    def _in_row(self) -> bool:
        return self._row_depth is not None

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag in _IGNORED_TEXT_TAGS:
            self._ignored += 1
        if tag == "title":
            self._in_title = True
        if tag == "img" and self._in_trace():
            src = attr_map.get("src") or ""
            name = src.rsplit("/", 1)[-1].split("?", 1)[0]
            if "logo" in name:
                self.logos.append(name)
        if tag in _VOID_TAGS:
            return
        classes = frozenset((attr_map.get("class") or "").split())
        self._stack.append(_Node(tag, classes))
        depth = len(self._stack)
        if tag == "div" and _TRACE_CLASS in classes and not self._in_trace():
            self._trace_depth = depth
            self.has_trace = True
        elif self._in_trace() and not self._in_row():
            if tag == "div" and {"d-none", "d-md-flex", "row"} <= classes:
                self._row_depth = depth
                self._row = {
                    "date": None,
                    "time": None,
                    "wording": None,
                    "location": None,
                }
        elif self._in_row() and tag == "div" and self._column is None:
            if "col-lg-4" in classes:
                self._column = "date"
            elif "col-lg-8" in classes:
                self._column = "what"
            if self._column is not None:
                self._column_depth = depth
                self._column_spans = []

    def handle_endtag(self, tag):
        if tag in _IGNORED_TEXT_TAGS and self._ignored:
            self._ignored -= 1
        if tag == "title":
            self._in_title = False
        if tag in _VOID_TAGS:
            return
        index = next(
            (
                i
                for i in range(len(self._stack) - 1, -1, -1)
                if self._stack[i].tag == tag
            ),
            None,
        )
        if index is None:
            return
        depth = index + 1
        closed = self._stack[index:]
        del self._stack[index:]
        node = closed[0]
        if self._column_depth == depth:
            self._finish_column()
        elif (
            self._column is not None
            and self._column_depth is not None
            and node.tag == "span"
            and depth == self._column_depth + 1
        ):
            self._column_spans.append(clean_text("".join(node.parts)))
        if self._row_depth == depth:
            self.rows.append(self._row)
            self._row_depth = None
        if self._trace_depth == depth:
            self._trace_depth = None
        # A nested element's text belongs to its parent's text as well.
        if self._stack and node.parts:
            self._stack[-1].parts.extend(node.parts)

    def _finish_column(self) -> None:
        spans = self._column_spans
        if self._column == "date" and spans:
            date = _DATE_RE.search(spans[0])
            clock = _TIME_RE.search(spans[0])
            self._row["date"] = date.group(1) if date else None
            self._row["time"] = clock.group(1) if clock else None
        elif self._column == "what":
            self._row["wording"] = spans[0] if spans else None
            self._row["location"] = (spans[1] or None) if len(spans) > 1 else None
        self._column = None
        self._column_depth = None
        self._column_spans = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._ignored:
            return
        if self._stack:
            self._stack[-1].parts.append(data)
        self.visible.append(data)
        if self._in_trace() and not self._in_row():
            self._header_text(clean_text(data))

    def _header_text(self, text: str) -> None:
        if not text:
            return
        if self._label == _LABEL_NUMBER:
            self.number = text
            self._label = None
        elif self._label == _LABEL_WEIGHT:
            self.weight = text
            self._label = None
        elif text in (_LABEL_NUMBER, _LABEL_WEIGHT):
            self._label = text


def parse_page(html: str, requested_number: str) -> dict | None:
    """Parse one reply.

    Returns ``None`` for the no-data notice, otherwise the parsed record::

        {"trackingNumber", "weightGrams", "logos", "rows": [...]}

    ``rows`` keep the page's order, each ``{date, time, wording, location}``.
    Anything unrecognisable raises :class:`HrvatskaPostaParseError`.
    """
    page = _PageParser()
    page.feed(html)
    page.close()
    title = clean_text(page.title)
    text = clean_text(" ".join(page.visible))

    if title == TITLE_NO_DATA:
        if NO_DATA_NOTICE in text:
            return None
        if BAD_FORMAT_NOTICE in text:
            raise HrvatskaPostaParseError("the page rejected the number's format")
        raise HrvatskaPostaParseError("no-data page without a known notice")
    if title != TITLE_FOUND:
        raise HrvatskaPostaParseError("unexpected page title")
    if not page.has_trace:
        raise HrvatskaPostaParseError("no trace block")
    if page.number is None:
        raise HrvatskaPostaParseError("trace block without a shipment number")
    if page.number.upper() != requested_number:
        raise HrvatskaPostaParseError("shipment number does not match the request")
    if not page.rows:
        raise HrvatskaPostaParseError("trace block without scan rows")

    return {
        "trackingNumber": requested_number,
        "weight": page.weight,
        "logos": page.logos,
        "rows": page.rows,
    }
