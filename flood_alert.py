"""
CARTA Flood Alert System - Prototype (Session 3)

Pulls two public data feeds for Charleston, SC and prints them:
  1. NOAA CO-OPS  - 48-hour high/low tide predictions, Station 8665530
  2. NWS Alerts   - active coastal flood advisories, Zone SCZ150
"""

import requests
from datetime import datetime, timedelta

# --- Configuration -----------------------------------------------------
# Keeping these as named constants (instead of typing the raw values
# inline below) makes it obvious what they mean, and means we only have
# to change them in one place if the station or zone ever changes.

NOAA_STATION_ID = "8665530"   # Charleston Harbor, SC
NWS_ZONE_ID = "SCZ150"        # Charleston County coastal zone

NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_MDAPI = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{NOAA_STATION_ID}"
NWS_URL = f"https://api.weather.gov/alerts/active/zone/{NWS_ZONE_ID}"


def get_tide_predictions():
    """Fetch the next 48 hours of high/low tide predictions from NOAA."""
    # 'params' becomes the ?key=value&key=value... query string.
    # interval="hilo" is what tells NOAA "just give me the high/low
    # turning points," not a reading every 6 minutes.
    #
    # IMPORTANT: we use explicit begin_date/end_date instead of range="48".
    # Without a date param, NOAA's `range` returns the 48 hours ENDING at
    # the latest data it has -- effectively the recent past, not a
    # forecast -- so a plain range="48" request silently returns
    # yesterday and today instead of the next two days. Anchoring
    # begin_date to now and end_date to now+48h is what actually makes
    # this forward-looking, which matters for a tool whose whole point is
    # warning riders about tomorrow.
    now = datetime.now()
    params = {
        "station": NOAA_STATION_ID,
        "product": "predictions",
        "datum": "MLLW",       # Mean Lower Low Water - the standard tidal reference
        "time_zone": "lst_ldt", # local standard time, adjusted for daylight saving
        "units": "english",    # feet, not meters
        "interval": "hilo",    # only high/low turning points
        "format": "json",
        "begin_date": now.strftime("%Y%m%d %H:%M"),
        "end_date": (now + timedelta(hours=48)).strftime("%Y%m%d %H:%M"),
    }
    response = requests.get(NOAA_URL, params=params, timeout=10)
    response.raise_for_status()  # throws an error if NOAA returns a bad status code
    data = response.json()
    return data.get("predictions", [])


def get_flood_alerts():
    """Fetch active NWS alerts for the Charleston coastal zone."""
    # The National Weather Service API requires every request to identify
    # who/what is calling it, so it can contact you if your script is
    # misbehaving. A missing User-Agent gets you blocked.
    headers = {
        "User-Agent": "CARTA Flood Alert Prototype (student project, contact: amychasewest@gmail.com)"
    }
    response = requests.get(NWS_URL, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get("features", [])


def get_flood_thresholds():
    """
    Fetch Charleston Harbor's official NWS coastal flood stage thresholds
    (minor/moderate/major) and convert them into the same MLLW datum our
    tide predictions use, so the two numbers are directly comparable.

    NOAA expresses every datum (MLLW, MHHW, etc.) as a height above a
    station's "Station Datum" -- an arbitrary fixed reference point that
    has no meaning on its own. The flood thresholds are given in Station
    Datum feet, but our tide predictions are in MLLW feet. To compare them
    we look up how far MLLW itself sits above Station Datum, and subtract
    that offset from each threshold.
    """
    levels = requests.get(f"{NOAA_MDAPI}/floodlevels.json", timeout=10).json()
    datums = requests.get(f"{NOAA_MDAPI}/datums.json", timeout=10).json()

    mllw_offset = next(d["value"] for d in datums["datums"] if d["name"] == "MLLW")

    return {
        "minor": levels["nws_minor"] - mllw_offset,
        "moderate": levels["nws_moderate"] - mllw_offset,
        "major": levels["nws_major"] - mllw_offset,
    }


def classify_flood_risk(height_ft, thresholds):
    """Return a risk label for a HIGH tide height, or None if it's routine."""
    if height_ft >= thresholds["major"]:
        return "MAJOR FLOOD RISK"
    if height_ft >= thresholds["moderate"]:
        return "MODERATE FLOOD RISK"
    if height_ft >= thresholds["minor"]:
        return "MINOR FLOOD RISK"
    return None


def build_risk_summary(predictions, thresholds, alerts):
    """
    Boil the raw tide list and raw alert list down into the one thing a
    rider actually needs: is there flood risk in the next 48 hours, and
    if so, roughly when. This is the "night before" warning -- everything
    else in this script is supporting detail.
    """
    risky_tides = []
    for entry in predictions:
        if entry["type"] != "H":
            continue
        height_ft = float(entry["v"])
        risk = classify_flood_risk(height_ft, thresholds)
        if risk:
            dt = datetime.strptime(entry["t"], "%Y-%m-%d %H:%M")
            risky_tides.append((dt, height_ft, risk))

    return {
        "tide_risk": bool(risky_tides),
        "risky_tides": risky_tides,
        "advisory_active": bool(alerts),
    }


def print_risk_summary(summary):
    print("=" * 55)
    print("RIDER SUMMARY")
    print("=" * 55)

    if not summary["tide_risk"] and not summary["advisory_active"]:
        print("No flood risk expected in the next 48 hours. Normal service.")
        print()
        return

    if summary["advisory_active"]:
        print("NWS has an ACTIVE coastal flood advisory right now.")

    if summary["tide_risk"]:
        print("Predicted high tides may cause flooding in the next 48 hours:")
        for dt, height_ft, risk in summary["risky_tides"]:
            pretty_time = dt.strftime("%a %b %d, %I:%M %p")
            print(f"  - {pretty_time}: {height_ft:.2f} ft ({risk})")
    print()


def print_tide_report(predictions, thresholds):
    print("=" * 55)
    print("NEXT 48 HOURS: HIGH & LOW TIDES (Charleston Harbor)")
    print("=" * 55)
    print(
        f"(Flood stage, MLLW: minor {thresholds['minor']:.2f} ft | "
        f"moderate {thresholds['moderate']:.2f} ft | "
        f"major {thresholds['major']:.2f} ft)"
    )
    if not predictions:
        print("No tide data returned.")
        return
    for entry in predictions:
        raw_time = entry["t"]          # e.g. "2026-08-22 14:36"
        height_ft = float(entry["v"])  # NOAA sends height as a string
        tide_type = "HIGH" if entry["type"] == "H" else "LOW"

        dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        pretty_time = dt.strftime("%a %b %d, %I:%M %p")

        line = f"{tide_type:5} | {pretty_time} | {height_ft:.2f} ft"

        # Flooding is a high-tide phenomenon -- a low tide can't overtop a
        # flood stage, so we only classify risk on HIGH entries.
        if tide_type == "HIGH":
            risk = classify_flood_risk(height_ft, thresholds)
            if risk:
                line += f"  <-- {risk}"

        print(line)


FLOOD_EVENT_KEYWORD = "Coastal Flood"


def filter_coastal_flood_alerts(alerts):
    """
    Keep only alerts that are actually about coastal flooding.

    The zone-alerts endpoint returns EVERY active NWS alert for SCZ150 --
    that can include unrelated hazards like a Rip Current Statement or
    Small Craft Advisory. This tool only cares about flooding, so we
    filter down to alerts whose event type mentions "Coastal Flood"
    (this matches the Watch / Advisory / Warning / Statement variants,
    since they all share that phrase in their event name).
    """
    return [
        alert for alert in alerts
        if FLOOD_EVENT_KEYWORD in alert["properties"].get("event", "")
    ]


def print_alert_report(all_alerts, flood_alerts):
    print()
    print("=" * 55)
    print("ACTIVE COASTAL FLOOD ADVISORIES (Charleston County)")
    print("=" * 55)
    if not flood_alerts:
        print("No active coastal flood advisories at this time.")
    for alert in flood_alerts:
        props = alert["properties"]
        print(f"- {props['event']}")
        print(f"  Headline:  {props.get('headline', 'N/A')}")
        print(f"  Effective: {props.get('effective', 'N/A')}")
        print(f"  Expires:   {props.get('expires', 'N/A')}")
        print()

    # Don't silently swallow the other alerts -- note that they exist,
    # even though this tool doesn't act on them.
    other_count = len(all_alerts) - len(flood_alerts)
    if other_count:
        plural = "s" if other_count != 1 else ""
        print(
            f"({other_count} other active NWS alert{plural} for this zone, "
            "not related to coastal flooding, not shown above.)"
        )


def main():
    tide_predictions = get_tide_predictions()
    thresholds = get_flood_thresholds()
    alerts = get_flood_alerts()
    flood_alerts = filter_coastal_flood_alerts(alerts)

    # The summary leads because it's the headline riders care about;
    # the tide table and advisory text below it are supporting detail.
    summary = build_risk_summary(tide_predictions, thresholds, flood_alerts)
    print_risk_summary(summary)

    print_tide_report(tide_predictions, thresholds)
    print_alert_report(alerts, flood_alerts)


if __name__ == "__main__":
    main()
