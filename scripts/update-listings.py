#!/usr/bin/env python3
"""
Toronto Rideshare Board — daily data generator.

Pulls the Kijiji Toronto rideshare & carpool category page, extracts the
listings from the page's __NEXT_DATA__ JSON (Apollo cache), applies Pat's
hard filters, extracts a departure day/time and a phone number from each
ad's own text, fetches route weather from Open-Meteo, and writes:

  - data/listings.json   (cleaned data + weather snapshot + counts)
  - index.html           (rendered from templates/index.template.html with
                          the current listings baked into the static HTML,
                          so search engines and AI crawlers see real content
                          without running JavaScript)
  - sitemap.xml          (lastmod refreshed)

Uses only the Python 3 standard library so it runs unattended anywhere,
including GitHub Actions.

Usage:
  python3 scripts/update-listings.py [--source-file saved.html] [--skip-weather]
"""

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "listings.json"
TEMPLATE_FILE = ROOT / "templates" / "index.template.html"
INDEX_FILE = ROOT / "index.html"
SITEMAP_FILE = ROOT / "sitemap.xml"

KIJIJI_URL = "https://www.kijiji.ca/b-rideshare-carpool/city-of-toronto/c5l1700273"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TORONTO_TZ = ZoneInfo("America/Toronto")

# Cities along the two corridors, west to east, for the weather strip.
CITIES = [
    {"name": "Toronto", "lat": 43.6532, "lon": -79.3832},
    {"name": "Kingston", "lat": 44.2312, "lon": -76.4860},
    {"name": "Ottawa", "lat": 45.4215, "lon": -75.6972},
    {"name": "Montréal", "lat": 45.5019, "lon": -73.5674},
]

BLOCKED_CITIES = ("brampton", "windsor")

ROUTE_LABELS = {
    "montreal": "Toronto → Montréal",
    "ottawa": "Toronto → Ottawa",
    "other": "Other Toronto rides",
}
ROUTE_ORDER = ("montreal", "ottawa", "other")
ROUTE_COLORS = {"montreal": "#2563eb", "ottawa": "#16a34a", "other": "#ea580c"}

# ---------------------------------------------------------------------------
# Fetching / parsing
# ---------------------------------------------------------------------------


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-CA,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_listings(page_html: str) -> list[dict]:
    """Extract StandardListing entries from the __NEXT_DATA__ Apollo cache."""
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S
    )
    if not m:
        raise RuntimeError("__NEXT_DATA__ block not found — Kijiji page layout changed?")
    data = json.loads(m.group(1))
    apollo = data["props"]["pageProps"]["__APOLLO_STATE__"]
    listings = [
        v
        for v in apollo.values()
        if isinstance(v, dict) and v.get("__typename") == "StandardListing"
    ]
    return listings


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def format_price(price: dict | None) -> str:
    if not price:
        return "See ad"
    amount = price.get("amount")
    if amount is None or amount == 0:
        return "See ad"
    dollars = amount / 100.0  # Kijiji amounts are in cents
    if amount % 100 == 0:
        return f"${amount // 100:,}"
    return f"${dollars:,.2f}"


def format_posted(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TORONTO_TZ)
    except ValueError:
        return ""
    return dt.strftime("%b %-d, %-I:%M %p")


WEEKDAYS = [
    ("monday", "Mon"),
    ("tuesday", "Tue"),
    ("wednesday", "Wed"),
    ("thursday", "Thu"),
    ("friday", "Fri"),
    ("saturday", "Sat"),
    ("sunday", "Sun"),
    ("lundi", "Mon"),
    ("mardi", "Tue"),
    ("mercredi", "Wed"),
    ("jeudi", "Thu"),
    ("vendredi", "Fri"),
    ("samedi", "Sat"),
    ("dimanche", "Sun"),
    ("mon", "Mon"),
    ("tues", "Tue"),
    ("tue", "Tue"),
    ("thur", "Thu"),
    ("thurs", "Thu"),
    ("thu", "Thu"),
    ("wed", "Wed"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]
WEEKDAY_RE = re.compile(
    r"\b(" + "|".join(name for name, _ in WEEKDAYS) + r")\b", re.I
)
WEEKDAY_SHORT = dict(WEEKDAYS)
RANGE_RE = re.compile(
    r"\b(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?)"
    r"\s*(?:-|–|—|to|through|au)\s*"
    r"(mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.I,
)
TIME_AMPM_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([aApP])\.?[mM]\b\.?")
TIME_H_RE = re.compile(r"(?<![\d:])(\d{1,2})h(\d{2})?\b")


def extract_departure(text: str) -> tuple[str | None, dict]:
    """Best-effort departure day/time from the ad's own words.

    Returns (display, parts). Display is the English chip string like
    "Daily · 9 AM, 7 PM" or "Fri · 8:30 AM", or None when the ad states
    nothing usable — never invents a value. Parts carries the same
    information structurally ({"days": "Daily" | "Mon, Wed" | "Mon–Fri" |
    None, "times": ["8:30 AM", ...]}) so the page can re-render the chip
    in French client-side.
    """
    if not text:
        return None, {"days": None, "times": []}
    low = text.lower()

    day_part = None
    if re.search(r"\b(daily|every\s?day|each day|7\s?days|all week)\b", low):
        day_part = "Daily"
    elif re.search(r"\bweek\s?days?\b", low):
        day_part = "Weekdays"
    elif re.search(r"\bweek\s?ends?\b", low):
        day_part = "Weekends"
    else:
        rng = RANGE_RE.search(low)
        if rng:
            a = WEEKDAY_SHORT.get(rng.group(1), rng.group(1).title())
            b = WEEKDAY_SHORT.get(rng.group(2), rng.group(2).title())
            day_part = f"{a}–{b}"
        else:
            seen: list[str] = []
            for match in WEEKDAY_RE.finditer(low):
                short = WEEKDAY_SHORT[match.group(1)]
                if short not in seen:
                    seen.append(short)
            if seen:
                day_part = ", ".join(seen)

    times: list[str] = []
    for match in TIME_AMPM_RE.finditer(text):
        hour, minute, meridiem = match.group(1), match.group(2), match.group(3).upper()
        label = f"{int(hour)}:{minute} {meridiem}M" if minute else f"{int(hour)} {meridiem}M"
        if label not in times:
            times.append(label)
    if not times:
        for match in TIME_H_RE.finditer(text):
            label = (
                f"{int(match.group(1))}h{match.group(2)}"
                if match.group(2)
                else f"{int(match.group(1))}h"
            )
            if label not in times:
                times.append(label)
    times = times[:4]

    parts = [p for p in (day_part, ", ".join(times) if times else None) if p]
    display = " · ".join(parts) if parts else None
    return display, {"days": day_part, "times": times}


PHONE_SEPARATED_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-](\d{3})[\s.\-](\d{4})(?!\d)"
)
PHONE_COMPACT_RE = re.compile(r"(?<!\d)(\d{3})(\d{3})(\d{4})(?!\d)")


def extract_phone(text: str) -> dict | None:
    """First plausible North American phone number in the ad text."""
    if not text:
        return None
    for pattern in (PHONE_SEPARATED_RE, PHONE_COMPACT_RE):
        for match in pattern.finditer(text):
            area, mid, last = match.group(1), match.group(2), match.group(3)
            if area[0] in "01":  # not a valid NANP area code
                continue
            return {
                "display": f"({area}) {mid}-{last}",
                "tel": f"+1{area}{mid}{last}",
            }
    return None


def classify_route(title: str, description: str) -> str:
    hay_title = title.lower()
    hay_desc = description.lower()
    if re.search(r"montr[eé]al|\bmtl\b", hay_title):
        return "montreal"
    if "ottawa" in hay_title:
        return "ottawa"
    if re.search(r"montr[eé]al|\bmtl\b", hay_desc):
        return "montreal"
    if "ottawa" in hay_desc:
        return "ottawa"
    return "other"


def mentions_blocked_city(text: str) -> str | None:
    low = text.lower()
    for city in BLOCKED_CITIES:
        if re.search(rf"\b{city}\b", low):
            return city
    return None


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, no API key)
# ---------------------------------------------------------------------------

WMO_LABELS = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Drizzle", "🌦️"),
    55: ("Drizzle", "🌦️"),
    56: ("Freezing drizzle", "🌧️"),
    57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌧️"),
    63: ("Rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"),
    67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"),
    73: ("Snow", "🌨️"),
    75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"),
    80: ("Light showers", "🌦️"),
    81: ("Showers", "🌧️"),
    82: ("Heavy showers", "🌧️"),
    85: ("Snow showers", "🌨️"),
    86: ("Snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm, hail", "⛈️"),
    99: ("Thunderstorm, hail", "⛈️"),
}


def fetch_weather() -> list[dict]:
    out = []
    for city in CITIES:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={city['lat']}&longitude={city['lon']}"
            "&current=temperature_2m,weather_code,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min"
            "&timezone=America%2FToronto&forecast_days=1"
        )
        try:
            raw = json.loads(fetch(url))
            cur = raw.get("current", {})
            daily = raw.get("daily", {})
            code = cur.get("weather_code")
            label, emoji = WMO_LABELS.get(code, ("—", "🌡️"))
            out.append(
                {
                    "name": city["name"],
                    "lat": city["lat"],
                    "lon": city["lon"],
                    "temp": cur.get("temperature_2m"),
                    "code": code,
                    "label": label,
                    "emoji": emoji,
                    "wind": cur.get("wind_speed_10m"),
                    "hi": (daily.get("temperature_2m_max") or [None])[0],
                    "lo": (daily.get("temperature_2m_min") or [None])[0],
                }
            )
        except Exception as exc:  # weather must never kill the run
            print(f"weather fetch failed for {city['name']}: {exc}", file=sys.stderr)
            out.append(
                {
                    "name": city["name"],
                    "lat": city["lat"],
                    "lon": city["lon"],
                    "temp": None,
                    "code": None,
                    "label": "Unavailable",
                    "emoji": "🌡️",
                    "wind": None,
                    "hi": None,
                    "lo": None,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------


def build_records(raw_listings: list[dict]):
    kept: list[dict] = []
    seen_ids: set[str] = set()
    hidden_topad = 0
    hidden_city = 0

    for raw in raw_listings:
        lid = str(raw.get("id", ""))
        if not lid or lid in seen_ids:
            continue
        seen_ids.add(lid)

        title = clean_text(raw.get("title"))
        description = clean_text(raw.get("description"))
        full_text = f"{title} {description}"
        flags = raw.get("flags") or {}

        # Hard filter 1: Kijiji paid promoted / "Top Ad" listings.
        if flags.get("topAd") or flags.get("isPromotionTopAd") or flags.get("isPromotionProvTopAd"):
            hidden_topad += 1
            continue
        # Hard filter 2: rides serving Brampton or Windsor.
        if mentions_blocked_city(full_text):
            hidden_city += 1
            continue

        location = raw.get("location") or {}
        coords = location.get("coordinates") or {}
        posted_iso = raw.get("activationDate") or raw.get("sortingDate")
        phone = extract_phone(full_text)
        dep_display, dep_parts = extract_departure(full_text)

        kept.append(
            {
                "id": lid,
                "title": title,
                "description": description[:400],
                "price": format_price(raw.get("price")),
                "address": clean_text(location.get("address")),
                "lat": coords.get("latitude"),
                "lng": coords.get("longitude"),
                "url": raw.get("url", ""),
                "posted": format_posted(posted_iso),
                "postedIso": posted_iso,
                "route": classify_route(title, description),
                "departure": dep_display,
                "departureParts": dep_parts,
                "phone": phone["display"] if phone else None,
                "phoneTel": phone["tel"] if phone else None,
                "spot": None,  # assigned below, in display order
            }
        )

    # Freshest first within each route, then assign spot numbers in the
    # order the board renders (Montréal board, Ottawa board, other rides).
    kept.sort(key=lambda r: (r["postedIso"] or ""), reverse=True)
    ordered: list[dict] = []
    for route in ROUTE_ORDER:
        ordered.extend(r for r in kept if r["route"] == route)
    spot = 0
    for record in ordered:
        if record["lat"] is not None and record["lng"] is not None:
            spot += 1
            record["spot"] = spot

    counts = {
        "pulled": len(raw_listings),
        "kept": len(ordered),
        "hidden": hidden_topad + hidden_city,
        "hiddenTopAd": hidden_topad,
        "hiddenBlockedCity": hidden_city,
        "byRoute": {
            route: sum(1 for r in ordered if r["route"] == route)
            for route in ROUTE_ORDER
        },
        "withDeparture": sum(1 for r in ordered if r["departure"]),
        "withPhone": sum(1 for r in ordered if r["phone"]),
    }
    return ordered, counts


# ---------------------------------------------------------------------------
# HTML rendering (listings baked into static HTML for crawlers)
# ---------------------------------------------------------------------------


def esc(s) -> str:
    return html.escape(str(s) if s is not None else "", quote=True)


def render_card(r: dict) -> str:
    spot_badge = (
        f'<span class="spot-badge" style="background:{ROUTE_COLORS[r["route"]]}">{r["spot"]}</span>'
        if r["spot"]
        else ""
    )
    when = (
        f'<p class="when">🗓️ <strong>{esc(r["departure"])}</strong></p>'
        if r["departure"]
        else ""
    )
    call = (
        f'<p class="call">📞 Call: <a href="tel:{esc(r["phoneTel"])}">{esc(r["phone"])}</a></p>'
        if r["phone"]
        else ""
    )
    pickup = f'<p class="pickup">📍 {esc(r["address"])}</p>' if r["address"] else ""
    desc = f'<p class="desc">{esc(r["description"])}</p>' if r["description"] else ""
    return f"""<article class="card" id="ride-{esc(r['id'])}" data-route="{esc(r['route'])}">
  <div class="card-top">{spot_badge}<span class="price">{esc(r['price'])}</span><span class="posted">Posted {esc(r['posted'])}</span></div>
  <h3 class="card-title"><a href="{esc(r['url'])}" target="_blank" rel="noopener">{esc(r['title'])}</a></h3>
  {when}
  {call}
  {pickup}
  {desc}
  <a class="open-link" href="{esc(r['url'])}" target="_blank" rel="noopener">View ad on Kijiji →</a>
</article>"""


def render_listings_html(listings: list[dict]) -> str:
    sections = []
    for route in ROUTE_ORDER:
        rides = [r for r in listings if r["route"] == route]
        cards = "\n".join(render_card(r) for r in rides)
        empty = "" if rides else '<p class="empty">No rides listed right now — check back after the next daily update.</p>'
        sections.append(
            f"""<section class="route-section" data-section="{route}">
<h2>{esc(ROUTE_LABELS[route])} <span class="count">({len(rides)})</span></h2>
<div class="cards">
{cards}
{empty}
</div>
</section>"""
        )
    return "\n".join(sections)


def render_spots_html(listings: list[dict]) -> str:
    items = []
    for r in listings:
        if not r["spot"]:
            continue
        items.append(
            f'<li><button type="button" class="spot-item" data-target="ride-{esc(r["id"])}" data-route="{esc(r["route"])}">'
            f'<span class="spot-badge" style="background:{ROUTE_COLORS[r["route"]]}">{r["spot"]}</span>'
            f'<span class="spot-text">{esc(r["address"] or r["title"])} — {esc(ROUTE_LABELS[r["route"]])} · {esc(r["price"])}</span>'
            f"</button></li>"
        )
    if not items:
        return '<li class="empty">No pickup locations published right now.</li>'
    return "\n".join(items)


def render_weather_html(weather: list[dict]) -> str:
    chips = []
    for w in weather:
        if w["temp"] is None:
            body = f'{esc(w["emoji"])} {esc(w["label"])}'
        else:
            body = (
                f'{esc(w["emoji"])} {round(w["temp"])}°C · {esc(w["label"])}'
                f' · H {round(w["hi"])}° / L {round(w["lo"])}°'
                if w["hi"] is not None and w["lo"] is not None
                else f'{esc(w["emoji"])} {round(w["temp"])}°C · {esc(w["label"])}'
            )
        chips.append(
            f'<div class="wx-chip" data-city="{esc(w["name"])}"><span class="wx-city">{esc(w["name"])}</span><span class="wx-body">{body}</span></div>'
        )
    return "\n".join(chips)


def build_jsonld(listings: list[dict], canonical: str) -> str:
    items = [
        {
            "@type": "ListItem",
            "position": i + 1,
            "name": r["title"],
            "url": r["url"],
        }
        for i, r in enumerate(listings)
    ]
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "name": "Toronto Rideshare Board",
                "url": canonical,
                "inLanguage": ["en-CA", "fr-CA"],
                "description": (
                    "Daily-updated board of Toronto rideshare and carpool rides "
                    "to Montréal and Ottawa, with pickup spots, route weather, "
                    "departure days and driver phone numbers."
                ),
            },
            {
                "@type": "ItemList",
                "name": "Toronto rideshare listings — Toronto to Montréal, Toronto to Ottawa and other Toronto rides",
                "numberOfItems": len(listings),
                "itemListElement": items,
            },
        ],
    }
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def update_sitemap(generated: datetime) -> None:
    if not SITEMAP_FILE.exists():
        return
    text = SITEMAP_FILE.read_text(encoding="utf-8")
    text = re.sub(
        r"<lastmod>.*?</lastmod>",
        f"<lastmod>{generated.date().isoformat()}</lastmod>",
        text,
    )
    SITEMAP_FILE.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the Toronto Rideshare Board data and page.")
    parser.add_argument("--source-file", help="Parse a saved Kijiji HTML file instead of fetching live.")
    parser.add_argument("--skip-weather", action="store_true", help="Skip the Open-Meteo fetch.")
    args = parser.parse_args()

    if args.source_file:
        page_html = Path(args.source_file).read_text(encoding="utf-8")
    else:
        page_html = fetch(KIJIJI_URL)

    raw_listings = parse_listings(page_html)
    listings, counts = build_records(raw_listings)
    weather = [] if args.skip_weather else fetch_weather()

    generated = datetime.now(TORONTO_TZ)
    payload = {
        "generatedAt": generated.isoformat(),
        "source": KIJIJI_URL,
        "counts": counts,
        "weather": weather,
        "listings": listings,
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if TEMPLATE_FILE.exists():
        template = TEMPLATE_FILE.read_text(encoding="utf-8")
        canonical_match = re.search(r'<link rel="canonical" href="([^"]+)"', template)
        canonical = canonical_match.group(1) if canonical_match else ""
        board_data = {
            "generatedAt": payload["generatedAt"],
            "counts": counts,
            "listings": listings,
        }
        page = (
            template.replace("%%GENERATED_AT_DISPLAY%%", generated.strftime("%A, %B %-d, %Y, %-I:%M %p %Z"))
            .replace("%%GENERATED_AT_ISO%%", generated.isoformat())
            .replace(
                "%%HIDDEN_NOTE%%",
                f'{counts["hidden"]} ads hidden by filters '
                f'({counts["hiddenBlockedCity"]} Brampton/Windsor, {counts["hiddenTopAd"]} promoted)',
            )
            .replace("%%LISTINGS_HTML%%", render_listings_html(listings))
            .replace("%%SPOTS_HTML%%", render_spots_html(listings))
            .replace("%%WEATHER_HTML%%", render_weather_html(weather))
            .replace("%%WEATHER_JSON%%", safe_json(weather))
            .replace("%%LISTINGS_JSON%%", safe_json(board_data))
            .replace("%%JSONLD%%", build_jsonld(listings, canonical))
        )
        INDEX_FILE.write_text(page, encoding="utf-8")
        update_sitemap(generated)

    print(json.dumps(counts, indent=1))
    print(f"Wrote {DATA_FILE} and {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
