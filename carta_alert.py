"""
CARTA Flood Alert System - Phase 3: Real-Time Rider Alert Generator

Combines NOAA's astronomical tide forecast with any active NWS storm-surge
signal to estimate the actual water level Charleston Harbor will see in the
next 24 hours, then translates that into a plain-language alert naming the
specific CARTA routes and stops at risk -- the kind of message that could
be texted or emailed to riders the night before.

Pipeline:
  1. NOAA CO-OPS  -> 48-hour tide predictions, Station 8665530
  2. NWS Alerts   -> any active Coastal Flood Advisory/Warning for SCZ150,
                      with a surge estimate pulled from its text
  3. tide + surge -> combined water level estimate
  4. combined level -> severity band -> affected routes (from
                        route_flood_summary.csv) -> affected stops (from
                        stop_flood_risk_report.csv)
  5. plain-language alert text
"""

import csv
from datetime import datetime, timedelta

import requests

NOAA_STATION_ID = "8665530"
# Charleston County's current NWS public forecast zone, verified live
# against api.weather.gov/zones?area=SC&type=land (zone SCZ047 referenced
# in earlier versions of this file does not exist as a current NWS zone).
NWS_ZONE_ID = "SCZ150"

NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NWS_URL = f"https://api.weather.gov/alerts/active/zone/{NWS_ZONE_ID}"
GTFS_ROUTES_TXT = "gtfs_data/carta-sc-us/routes.txt"

STOP_CSV = "stop_flood_risk_report.csv"
ROUTE_SUMMARY_CSV = "route_flood_summary.csv"

# Severity bands as combined-water-level ranges, in the order they escalate.
# "floor" is the lowest combined level (ft, MLLW) that reaches this severity.
SEVERITY_BANDS = [
    ("MAJOR", 8.0),
    ("MODERATE", 7.5),
    ("MINOR", 7.0),
]
SEVERITY_RANK = {"MINOR": 0, "MODERATE": 1, "MAJOR": 2}


# ---------------------------------------------------------------------------
# 1. NOAA tide predictions
# ---------------------------------------------------------------------------

def get_tide_predictions():
    """Fetch the next 48 hours of high/low tide predictions from NOAA.

    interval="hilo" returns only the high/low turning points (not a
    reading every 6 minutes), and time_zone="lst_ldt" is NOAA's spelling
    for "local time, DST-aware."

    IMPORTANT: this deliberately uses explicit begin_date/end_date rather
    than range="48". Without a date param, NOAA's `range` returns the 48
    hours ENDING at the latest data it has -- which is effectively the
    recent past, not a forecast -- so a plain range="48" request silently
    returns yesterday and today instead of the next two days. Anchoring
    begin_date to now and end_date to now+48h is what actually makes this
    forward-looking.
    """
    now = datetime.now()
    params = {
        "station": NOAA_STATION_ID,
        "product": "predictions",
        "datum": "MLLW",
        "time_zone": "lst_ldt",
        "units": "english",
        "interval": "hilo",
        "format": "json",
        "begin_date": now.strftime("%Y%m%d %H:%M"),
        "end_date": (now + timedelta(hours=48)).strftime("%Y%m%d %H:%M"),
    }
    response = requests.get(NOAA_URL, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("predictions", [])


def get_highest_tide_in_window(predictions, start_hours, end_hours):
    """From the 48-hour prediction list, find the single highest HIGH tide
    whose time falls between (now + start_hours) and (now + end_hours), and
    return (datetime, height_ft). Returns (None, None) if none falls in
    that window (shouldn't happen in practice -- Charleston sees roughly
    two high tides a day -- but we don't want to crash if NOAA ever
    returns a short list).

    A shared, parameterized window lookup rather than one hardcoded to
    "next 24 hours" -- the dashboard (app.py) needs a second, later
    window ("24 to 48 hours from now") for its day-2 outlook panel, and
    duplicating this loop with different numbers would be an easy place
    for the two to quietly drift apart.
    """
    now = datetime.now()
    window_start = now + timedelta(hours=start_hours)
    window_end = now + timedelta(hours=end_hours)

    candidates = []
    for entry in predictions:
        if entry["type"] != "H":
            continue
        dt = datetime.strptime(entry["t"], "%Y-%m-%d %H:%M")
        if window_start <= dt <= window_end:
            candidates.append((dt, float(entry["v"])))

    if not candidates:
        return None, None
    return max(candidates, key=lambda c: c[1])


def get_highest_tide_next_24h(predictions):
    """The highest predicted high tide in the next 24 hours."""
    return get_highest_tide_in_window(predictions, 0, 24)


# ---------------------------------------------------------------------------
# 2. NWS alerts + surge extraction
# ---------------------------------------------------------------------------

def get_active_flood_alerts():
    """Fetch active NWS alerts for SCZ150, filtered to the three coastal
    flood products that map onto this project's severity bands: Advisory
    (MINOR), Watch (MODERATE), and Warning (MAJOR). Watch was previously
    excluded here, which silently dropped exactly the product type most
    likely to be active ahead of a landfalling storm.
    """
    headers = {
        "User-Agent": "CARTA Flood Alert Prototype (student project, contact: amychasewest@gmail.com)"
    }
    response = requests.get(NWS_URL, headers=headers, timeout=10)
    response.raise_for_status()
    features = response.json().get("features", [])
    return [
        f for f in features
        if f["properties"].get("event") in NWS_FLOOD_PRODUCT_FLOOR_FT
    ]


# NWS's own coastal flood products already correspond to this project's
# MINOR/MODERATE/MAJOR severity bands (see SEVERITY_BANDS) -- an Advisory
# means "minor flood stage reached," a Watch means "moderate flood stage
# possible," a Warning means "major flood stage expected." Rather than
# regex-parsing a water-level number out of free-form advisory text (which
# breaks whenever NWS phrases a bulletin differently -- e.g. giving a
# direct MLLW range instead of an explicit "N feet of storm surge"), read
# the product type directly off the alert and floor the combined water
# level at what that product guarantees.
NWS_FLOOD_PRODUCT_FLOOR_FT = {
    "Coastal Flood Advisory": 7.1,   # MINOR  (SEVERITY_BANDS floor: 7.0)
    "Coastal Flood Watch": 7.9,      # MODERATE (SEVERITY_BANDS floor: 7.5)
    "Coastal Flood Warning": 8.1,    # MAJOR  (SEVERITY_BANDS floor: 8.0)
}
NWS_FLOOD_PRODUCT_RANK = {"Coastal Flood Advisory": 0, "Coastal Flood Watch": 1, "Coastal Flood Warning": 2}


def most_severe_alert(alerts):
    """NWS can have more than one coastal flood product active at once --
    e.g. an Advisory covering tonight's high tide layered under a Watch
    for tomorrow morning's higher one, exactly as happened during Isaias.
    Pick whichever implies the higher water level, same "overestimate
    risk, not underestimate it" principle as the rest of this module.
    Returns None if `alerts` is empty."""
    if not alerts:
        return None
    return max(alerts, key=lambda a: NWS_FLOOD_PRODUCT_RANK.get(a["properties"].get("event"), -1))


def flood_product_floor_ft(alert):
    """The minimum combined water level (ft, MLLW) implied by this alert's
    NWS product type. Returns 0.0 if the alert isn't a recognized coastal
    flood product (shouldn't happen given get_active_flood_alerts already
    filters to NWS_FLOOD_PRODUCT_FLOOR_FT's keys, but a function that reads
    an unvalidated dict key shouldn't KeyError on anything unexpected)."""
    return NWS_FLOOD_PRODUCT_FLOOR_FT.get(alert["properties"].get("event"), 0.0)


# ---------------------------------------------------------------------------
# 3 & 4. Combine tide + surge, classify severity, find affected routes/stops
# ---------------------------------------------------------------------------

def classify_severity(combined_level_ft):
    """Return the severity label for a combined water level, or None if
    it's below minor flood stage."""
    for label, floor in SEVERITY_BANDS:
        if combined_level_ft >= floor:
            return label
    return None


def load_route_summary():
    with open(ROUTE_SUMMARY_CSV, newline="") as f:
        return {row["route"]: row for row in csv.DictReader(f)}


def load_route_long_names():
    names = {}
    with open(GTFS_ROUTES_TXT, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["route_short_name"]:
                names[row["route_short_name"]] = row["route_long_name"]
    return names


def load_stops_by_route():
    by_route = {}
    with open(STOP_CSV, newline="") as f:
        for row in csv.DictReader(f):
            for route in (r.strip() for r in row["routes"].split(",") if r.strip()):
                by_route.setdefault(route, []).append(row)
    return by_route


def affected_routes(severity, route_summary):
    """A route is affected once the combined water level reaches ITS
    first-triggering threshold (from route_flood_summary.csv), not a
    threshold re-typed here by hand.

    This reproduces exactly the route lists given in the brief (MINOR/
    MODERATE -> everyone except Route 20; MAJOR -> all seven) because
    six of the seven routes have a first_threshold of MINOR and Route 20's
    is MAJOR -- but deriving it from the real per-route data means this
    stays correct even if a future session's elevation data changes which
    routes trip at which stage, instead of silently drifting out of sync
    with route_flood_summary.csv.
    """
    if severity is None:
        return []
    return [
        route for route, row in route_summary.items()
        if row["first_threshold"] != "none"
        and SEVERITY_RANK[row["first_threshold"]] <= SEVERITY_RANK[severity]
    ]


def at_risk_stops(route, combined_level_ft, stops_by_route, limit=3):
    """Stops on this route whose flood threshold is already at or below
    the current combined water level -- i.e. actually underwater right
    now, not just "somewhere on an affected route." Sorted worst (lowest
    elevation) first, capped at `limit` for a readable alert."""
    at_risk = [
        row for row in stops_by_route.get(route, [])
        if row["triggers_at_harbor_ft"] and float(row["triggers_at_harbor_ft"]) <= combined_level_ft
    ]
    at_risk.sort(key=lambda r: float(r["elevation_ft"]))
    return at_risk[:limit], len(at_risk)


# ---------------------------------------------------------------------------
# 5. Plain-language alert text
# ---------------------------------------------------------------------------

def build_alert_text(tide_dt, tide_ft, surge_ft, combined_ft, severity, alert_used,
                      route_summary, route_long_names, stops_by_route):
    lines = []
    tide_time_str = tide_dt.strftime("%A, %B %d at %I:%M %p") if tide_dt else "unknown"

    lines.append("CARTA FLOOD ALERT")
    lines.append("=" * 40)
    lines.append(
        f"Tomorrow's highest predicted tide is {tide_ft:.1f} ft, forecast for {tide_time_str}."
    )

    if alert_used:
        lines.append(
            f"NWS has an active {alert_used['properties']['event']} for the Charleston "
            f"coastal zone, with an estimated {surge_ft:.1f} ft of additional storm surge."
        )
    else:
        lines.append("NWS has no active coastal flood advisory or warning right now.")

    lines.append(f"Projected combined water level: {combined_ft:.1f} ft.")
    lines.append("")

    if severity is None:
        lines.append("No flooding impact expected. All CARTA routes operating normally.")
        return "\n".join(lines)

    lines.append(f"{severity} FLOODING EXPECTED")
    lines.append("")
    lines.append("Affected routes:")

    affected = affected_routes(severity, route_summary)
    for route in sorted(affected, key=lambda r: (r != "20", r)):
        long_name = route_long_names.get(route, "")
        label = f"Route {route} ({long_name})" if long_name else f"Route {route}"
        worst_stops, total_at_risk = at_risk_stops(route, combined_ft, stops_by_route)

        lines.append(f"  * {label} -- expect delays or detours.")
        if worst_stops:
            names = "; ".join(s["stop_name"] for s in worst_stops)
            extra = f" (+{total_at_risk - len(worst_stops)} more)" if total_at_risk > len(worst_stops) else ""
            lines.append(f"    Highest-risk stops: {names}{extra}")

    all_routes = set(route_summary.keys())
    unaffected = sorted(all_routes - set(affected))
    if unaffected:
        lines.append("")
        lines.append(f"All other routes ({', '.join(unaffected)}): normal service expected.")

    lines.append("")
    lines.append("WHAT TO DO:")
    lines.append("  - Allow 15+ extra minutes if you ride an affected route.")
    lines.append("  - Consider an alternate stop if yours is listed above.")
    lines.append("  - Check ridecarta.com or CARTA's Transit App before you leave.")
    lines.append("")
    lines.append(f"Source: NOAA Station {NOAA_STATION_ID} + NWS Charleston coastal flood alerts")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    predictions = get_tide_predictions()
    tide_dt, tide_ft = get_highest_tide_next_24h(predictions)

    active_alerts = get_active_flood_alerts()
    alert_used = most_severe_alert(active_alerts)
    floor_ft = flood_product_floor_ft(alert_used) if alert_used else 0.0

    # surge_ft here means "how much the active NWS product's floor adds on
    # top of the plain predicted tide" -- kept so tide_ft + surge_ft still
    # equals combined_ft for the human-readable breakdown in build_alert_text.
    surge_ft = max(0.0, floor_ft - (tide_ft or 0.0))
    combined_ft = max(tide_ft or 0.0, floor_ft)
    severity = classify_severity(combined_ft)

    route_summary = load_route_summary()
    route_long_names = load_route_long_names()
    stops_by_route = load_stops_by_route()

    alert_text = build_alert_text(
        tide_dt, tide_ft, surge_ft, combined_ft, severity,
        alert_used, route_summary, route_long_names, stops_by_route,
    )
    print(alert_text)


if __name__ == "__main__":
    main()
