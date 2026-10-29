"""Sample Hrvatska pošta pages and parsed records shared by the test modules.

The pages follow the real markup (class names, the weekday span, the mobile
duplicate of every scan, the varying wording-span class) with invented
numbers and scrubbed locations (``NNNNN OFFICE``).
"""

from __future__ import annotations

from html import escape

ACTIVE_CODE = "RR000000001HR"
DELIVERED_CODE = "RR000000002HR"

ANNOUNCED = "Pošiljatelj je unio podatke, pošiljka još nije predana Hrvatskoj pošti"
ACCEPTED = "Pošiljka je zaprimljena"
ARRIVED = "Pošiljka je prispjela"
PREPARING = "Pošiljka je u pripremi za otpremu"
SENT = "Pošiljka je otpremljena"
REDIRECTED = "Pošiljka preusmjerena u paketomat"
OUT_FOR_DELIVERY = "Dostava na adresu je u tijeku"
ATTEMPT = "Pokušaj dostave pošiljke"
NOT_DELIVERED = "Pošiljka nije uručena"
PICKUP_OFFICE = "Pošiljku možete preuzeti u poštanskom uredu"
IN_LOCKER = "Pošiljka predana u paketomat"
DELIVERED = "Pošiljka je uručena"
RETURNING = "Pošiljka se vraća pošiljatelju"
DELIVERED_TO_SENDER = "Pošiljka je uručena pošiljatelju"

NO_DATA_TITLE = "Hrvatska Pošta - Nema podataka"
NO_DATA_NOTICE = "Nema podataka za uneseni prijamni broj pošiljke"
BAD_FORMAT_NOTICE = "Neispravan format prijamnog broja pošiljke"

_WEEKDAYS = (
    "Ponedjeljak",
    "Utorak",
    "Srijeda",
    "Četvrtak",
    "Petak",
    "Subota",
    "Nedjelja",
)


def row(date: str, clock: str, wording: str, location: str | None = None) -> dict:
    """One scan row as the parser returns it."""
    return {"date": date, "time": clock, "wording": wording, "location": location}


def _desktop_row(index: int, scan: dict) -> str:
    wording_class = "mr-5 mb-2 text-style-19-md-12" if index == 0 else "mr-5 mb-2"
    location = (
        f'<span class="text-style-52-md-4">{escape(scan["location"])}</span>'
        if scan["location"]
        else ""
    )
    return f"""
    <div class="d-none d-md-flex row align-items-center">
      <div class="col-6 col-lg-4 pl-0">
        <span class="mr-5 mb-2 Text-Style-20-md-6">
          {scan["date"]} <span class="ml-3">{scan["time"]}</span>
        </span>
        <span class="Text-Style-52-md-4">{_WEEKDAYS[0]}</span>
      </div>
      <div class="col-6 col-lg-8 pl-0">
        <span class="{wording_class}">{escape(scan["wording"])}</span>
        {location}
      </div>
    </div>
    <div class="d-flex d-md-none flex-column">
      <span class="Text-Style-20">{scan["date"]} {_WEEKDAYS[0]} {scan["time"]}</span>
      <span class="Text-Style-19">{escape(scan["wording"])}, MOBILE-DUPLICATE</span>
    </div>"""


def page(number: str, rows: list[dict], *, weight: str | None = "76 g") -> str:
    """A found page whose scan rows appear in the given (page) order."""
    weight_block = (
        f'<span class="mb-2 mb-md-3 Text-Style-4">Masa</span>'
        f'<span class="Text-Style-6">{weight}</span>'
        if weight is not None
        else ""
    )
    scans = "".join(_desktop_row(i, scan) for i, scan in enumerate(rows))
    return f"""<!DOCTYPE html>
<html><head><title>Hrvatska Pošta - Tracking</title></head>
<body>
<form><input type="text" data-invalid-barcode-message="{BAD_FORMAT_NOTICE}" /></form>
<div class="track__n__trace container">
  <span class="mb-2 mb-md-3 Text-Style-4">Broj pošiljke</span>
  <h5 class="Text-Style-6-md-134">{number}</h5>
  {weight_block}
  <img src="/assets/icons/hp-full-logo.svg" />
  <div class="event__status__item">
    <span class="mr-md-auto Text-Style-13">Uručena&#xD;&#xA;</span>
    <div class="event__status__item-content">{scans}
    </div>
  </div>
</div>
<footer>Broj pošiljke</footer>
</body></html>"""


def no_data_page() -> str:
    """The verbatim shape of the unknown / aged-out reply."""
    return f"""<!DOCTYPE html>
<html><head><title>{NO_DATA_TITLE}</title></head>
<body>
<form><input type="text" data-invalid-barcode-message="{BAD_FORMAT_NOTICE}" /></form>
<div class="notice"><p>{NO_DATA_NOTICE}</p></div>
</body></html>"""


def bad_format_page() -> str:
    """The reply for a number shape the page does not accept."""
    return f"""<!DOCTYPE html>
<html><head><title>{NO_DATA_TITLE}</title></head>
<body>
<form><input type="text" data-invalid-barcode-message="{BAD_FORMAT_NOTICE}" /></form>
<div class="notice"><p>{BAD_FORMAT_NOTICE}</p></div>
</body></html>"""


# -- scenarios (rows in chronological order) ---------------------------------

HOME_ROWS = [
    row("20.04.2026", "10:00:00", ANNOUNCED),
    row("21.04.2026", "09:15:10", ACCEPTED, "10000 OFFICE"),
    row("21.04.2026", "18:40:00", SENT, "10000 OFFICE"),
    row("22.04.2026", "06:30:00", ARRIVED, "21000 OFFICE"),
    row("22.04.2026", "08:05:00", OUT_FOR_DELIVERY, "21000 OFFICE"),
    row("22.04.2026", "13:12:42", DELIVERED, "21000 OFFICE"),
]

LOCKER_ROWS = [
    row("20.04.2026", "10:00:00", ANNOUNCED),
    row("21.04.2026", "09:15:10", ACCEPTED, "10000 OFFICE"),
    row("22.04.2026", "08:05:00", REDIRECTED, "21000 OFFICE"),
    row("22.04.2026", "14:20:00", IN_LOCKER, "21001 LOCKER"),
    row("23.04.2026", "09:35:34", DELIVERED, "21001 LOCKER"),
]

PICKUP_ROWS = [
    row("20.04.2026", "10:00:00", ANNOUNCED),
    row("21.04.2026", "09:15:10", ACCEPTED, "10000 OFFICE"),
    row("22.04.2026", "12:00:00", PICKUP_OFFICE, "21000 OFFICE"),
]

RETURN_ROWS = [
    row("20.04.2026", "10:00:00", ANNOUNCED),
    row("21.04.2026", "09:15:10", ACCEPTED, "10000 OFFICE"),
    row("22.04.2026", "12:00:00", PICKUP_OFFICE, "21000 OFFICE"),
    row("29.04.2026", "08:00:00", NOT_DELIVERED, "21000 OFFICE"),
    row("29.04.2026", "08:00:05", RETURNING, "21000 OFFICE"),
    row("02.05.2026", "07:00:00", OUT_FOR_DELIVERY, "10000 OFFICE"),
    row("02.05.2026", "11:00:00", DELIVERED_TO_SENDER, "10000 OFFICE"),
]

INBOUND_ROWS = [
    row("10.04.2026", "23:30:00", ACCEPTED, "DENUEB Nuernberg"),
    row("11.04.2026", "09:00:00", ARRIVED, "10000 OFFICE"),
]


def parsed(code: str, rows: list[dict], *, grams: int | None = 76) -> dict:
    """The record the client hands the coordinator."""
    return {
        "trackingNumber": code,
        "weightGrams": grams,
        "logos": ["hp-full-logo.svg"],
        "rows": rows,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered home parcel."""
    return parsed(code, HOME_ROWS)


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel out for delivery."""
    return parsed(code, HOME_ROWS[:-1])


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a post office."""
    return parsed(code, PICKUP_ROWS)
