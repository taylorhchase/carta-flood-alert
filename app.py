"""
CARTA Flood Alert Dashboard

A Flask web app that turns carta_alert.py's data pipeline into a live
webpage a CARTA employee could open every morning. It shows two panels:

  Panel 1 -- Tonight's Alert: the highest tide in the next 24 hours,
             combined with any active NWS storm-surge advisory, with a
             copy-ready alert per affected route.
  Panel 2 -- 48-Hour Outlook: the highest tide in the 24-48 hour window
             (the day after), astronomical prediction only -- there's no
             way to know yet whether NWS will issue a surge advisory for
             that day, so this panel is explicitly marked preliminary.

A background thread (using the `schedule` library) refreshes the cached
data at 6 AM and 6 PM daily, so the page itself never has to hit NOAA/NWS
on every request -- it just reads whatever the last scheduled refresh
computed. The browser separately auto-refreshes the page every 60 seconds
so anyone with it open sees a new scheduled refresh without reloading by
hand.
"""

import threading
import time
from datetime import datetime, timedelta

import pytz
import schedule
from flask import Flask, render_template, redirect, url_for

import carta_alert as ca

app = Flask(__name__)

# CARTA operates in Charleston, SC, so every time shown on the dashboard --
# last-updated, the scheduled refreshes, the flood risk windows -- should
# read in Eastern time regardless of what time zone the server itself is
# set to (e.g. a UTC-clocked host).
EASTERN = pytz.timezone("America/New_York")


def to_eastern(naive_dt):
    """NOAA's tide predictions come back with time_zone="lst_ldt" (see
    carta_alert.get_tide_predictions), i.e. the station's own local time --
    already Eastern wall-clock, just without a tzinfo attached. Attach it
    (localize, not convert) so the dashboard can treat it like any other
    Eastern-aware datetime."""
    return EASTERN.localize(naive_dt) if naive_dt else None

# NONE/MINOR/MODERATE/MAJOR collapse down to the three colors the dashboard
# asked for: green (no impact), yellow (minor or moderate), red (major).
SEVERITY_COLOR = {"NONE": "green", "MINOR": "yellow", "MODERATE": "yellow", "MAJOR": "red"}

# How many stops to show when a route row is expanded. carta_alert.py's
# at_risk_stops() defaults to 3 (enough for a short texted alert); the
# dashboard's expandable panel should show everything, so we pass a limit
# high enough that no real route will ever hit it.
ALL_STOPS = 1000


# ---------------------------------------------------------------------------
# Data cache
# ---------------------------------------------------------------------------
# The Flask route handler never calls NOAA/NWS directly -- it just reads
# this dict, which the scheduler (or a manual refresh) fills in. A lock
# guards it because the scheduler thread writes to it while request
# threads may be reading it at the same time.

_cache_lock = threading.Lock()
_cache = {"panel1": None, "panel2": None, "generated_at": None, "error": None}


# ---------------------------------------------------------------------------
# Per-route alert text (Panel 1 only -- see build_panel1)
# ---------------------------------------------------------------------------

def build_route_alert_text(route, long_name, severity, window_start_str, window_end_str,
                            stops, combined_ft, tide_ft, surge_ft):
    """The copy-ready message for one affected route: what's happening,
    the specific window riders should plan around, and which stops to
    avoid. This is meant to be pasted straight into a text or email."""
    label = f"Route {route} ({long_name})" if long_name else f"Route {route}"
    lines = [
        f"CARTA {label} -- {severity} FLOOD RISK",
        f"Risk window: {window_start_str} to {window_end_str}",
    ]
    if surge_ft:
        lines.append(
            f"Projected water level: {combined_ft:.1f} ft "
            f"({tide_ft:.1f} ft tide + {surge_ft:.1f} ft storm surge)"
        )
    else:
        lines.append(f"Projected water level: {combined_ft:.1f} ft (astronomical tide)")

    if stops:
        lines.append("Stops likely affected:")
        for s in stops:
            lines.append(f"  - {s['stop_name']} ({float(s['elevation_ft']):.1f} ft elevation)")

    lines.append("Riders: allow 15+ extra minutes, or use an alternate stop if yours is listed above.")
    lines.append(f"Source: NOAA Station {ca.NOAA_STATION_ID} + NWS Charleston coastal flood alerts")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Building one panel's route rows
# ---------------------------------------------------------------------------

def build_route_rows(severity, combined_ft, route_summary, route_long_names, stops_by_route,
                      window_start_str=None, window_end_str=None, tide_ft=None, surge_ft=None,
                      include_alert_text=False):
    """One row per route, in "most exposed first" order (same convention
    as generate_briefing.py), with a status color and, if the route is
    currently affected, its at-risk stops and (for Panel 1 only) a
    ready-to-copy alert."""
    affected = set(ca.affected_routes(severity, route_summary)) if severity != "NONE" else set()

    ordered_routes = sorted(
        route_summary.items(),
        key=lambda kv: (-int(kv[1]["est_disruptions_per_year"]), -int(kv[1]["stops_at_risk"])),
    )

    rows = []
    for route, _summary_row in ordered_routes:
        is_affected = route in affected
        color = "red" if (is_affected and severity == "MAJOR") else ("yellow" if is_affected else "green")

        stops, total_at_risk = ([], 0)
        if is_affected:
            stops, total_at_risk = ca.at_risk_stops(route, combined_ft, stops_by_route, limit=ALL_STOPS)

        alert_text = None
        if include_alert_text and is_affected and window_start_str:
            alert_text = build_route_alert_text(
                route, route_long_names.get(route, ""), severity,
                window_start_str, window_end_str, stops, combined_ft, tide_ft, surge_ft,
            )

        rows.append({
            "route": route,
            "name": route_long_names.get(route, ""),
            "color": color,
            "status_label": severity if is_affected else "NORMAL",
            "stops": stops,
            "total_at_risk": total_at_risk,
            "alert_text": alert_text,
        })
    return rows


# ---------------------------------------------------------------------------
# Panel 1: Tonight's Alert (next 24 hours, tide + surge)
# ---------------------------------------------------------------------------

def build_panel1(predictions, active_alerts, route_summary, route_long_names, stops_by_route):
    tide_dt, tide_ft = ca.get_highest_tide_in_window(predictions, 0, 24)
    tide_dt = to_eastern(tide_dt)
    alert_used = active_alerts[0] if active_alerts else None
    surge_ft = ca.extract_surge_ft(alert_used) if alert_used else 0.0
    combined_ft = (tide_ft or 0.0) + surge_ft
    severity = ca.classify_severity(combined_ft) or "NONE"

    window_start_str = window_end_str = None
    if tide_dt:
        # The flood risk window: 2 hours before peak tide to 3 hours after --
        # tide-driven street flooding doesn't start and stop exactly at the
        # peak, it ramps up beforehand and drains down afterward.
        window_start_str = (tide_dt - timedelta(hours=2)).strftime("%a %b %d, %I:%M %p")
        window_end_str = (tide_dt + timedelta(hours=3)).strftime("%a %b %d, %I:%M %p")

    rows = build_route_rows(
        severity, combined_ft, route_summary, route_long_names, stops_by_route,
        window_start_str, window_end_str, tide_ft, surge_ft, include_alert_text=True,
    )

    return {
        "label": "Tonight's Alert -- Next 24 Hours",
        "tide_ft": tide_ft,
        "tide_time": tide_dt.strftime("%A, %B %d at %I:%M %p") if tide_dt else None,
        "surge_ft": surge_ft,
        "combined_ft": combined_ft,
        "severity": severity,
        "color": SEVERITY_COLOR[severity],
        "alert_event": alert_used["properties"]["event"] if alert_used else None,
        "window_start": window_start_str,
        "window_end": window_end_str,
        "routes": rows,
    }


# ---------------------------------------------------------------------------
# Panel 2: 48-Hour Outlook (24-48 hours out, astronomical only)
# ---------------------------------------------------------------------------

def build_panel2(predictions, route_summary, route_long_names, stops_by_route):
    tide_dt, tide_ft = ca.get_highest_tide_in_window(predictions, 24, 48)
    tide_dt = to_eastern(tide_dt)
    # No surge is added here -- NWS doesn't forecast storm surge this far
    # out, so this is deliberately the astronomical tide alone.
    combined_ft = tide_ft or 0.0
    severity = ca.classify_severity(combined_ft) or "NONE"

    rows = build_route_rows(
        severity, combined_ft, route_summary, route_long_names, stops_by_route,
        include_alert_text=False,
    )

    return {
        "label": "48-Hour Outlook -- Day After Tomorrow",
        "tide_ft": tide_ft,
        "tide_time": tide_dt.strftime("%A, %B %d at %I:%M %p") if tide_dt else None,
        "severity": severity,
        "color": SEVERITY_COLOR[severity],
        "note": (
            "Astronomical tide prediction only -- NWS has not yet issued a storm surge "
            "forecast this far out. If a coastal flood advisory is issued closer to this "
            "date, actual water levels and route impacts could be higher than shown here."
        ),
        "routes": rows,
    }


# ---------------------------------------------------------------------------
# Refresh: the one function the scheduler calls
# ---------------------------------------------------------------------------

def refresh_dashboard_data():
    """Pull fresh data from NOAA/NWS and rebuild both panels. Wrapped in
    try/except because an uncaught exception here would otherwise
    propagate out of schedule.run_pending() and kill the background
    scheduler thread permanently -- silently, since nothing is watching
    that thread. A failed refresh leaves the previous (stale but valid)
    cached data in place rather than blanking the page."""
    try:
        predictions = ca.get_tide_predictions()
        active_alerts = ca.get_active_flood_alerts()
        route_summary = ca.load_route_summary()
        route_long_names = ca.load_route_long_names()
        stops_by_route = ca.load_stops_by_route()

        panel1 = build_panel1(predictions, active_alerts, route_summary, route_long_names, stops_by_route)
        panel2 = build_panel2(predictions, route_summary, route_long_names, stops_by_route)

        with _cache_lock:
            _cache["panel1"] = panel1
            _cache["panel2"] = panel2
            _cache["generated_at"] = datetime.now(EASTERN)
            _cache["error"] = None
        print(f"[{datetime.now(EASTERN):%Y-%m-%d %H:%M:%S %Z}] Dashboard data refreshed.")
    except Exception as exc:
        with _cache_lock:
            _cache["error"] = str(exc)
        print(f"[{datetime.now(EASTERN):%Y-%m-%d %H:%M:%S %Z}] Dashboard refresh FAILED: {exc}")


# ---------------------------------------------------------------------------
# Scheduler: runs refresh_dashboard_data() at 6 AM and 6 PM Eastern every day
# ---------------------------------------------------------------------------
# `schedule`'s `.at()` otherwise uses whatever time zone the host machine's
# system clock is set to. Passing tz="America/New_York" pins these two runs
# to 6 AM / 6 PM Eastern (to match Charleston, SC) regardless of what time
# zone the server itself runs in.

def run_scheduler():
    schedule.every().day.at("06:00", "America/New_York").do(refresh_dashboard_data)
    schedule.every().day.at("18:00", "America/New_York").do(refresh_dashboard_data)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    with _cache_lock:
        context = dict(_cache)
    return render_template("dashboard.html", **context)


@app.route("/refresh", methods=["POST"])
def manual_refresh():
    """A manual "Refresh Now" button, mainly useful for testing or if an
    employee wants fresher data without waiting for the next 6am/6pm run."""
    refresh_dashboard_data()
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    refresh_dashboard_data()  # populate the cache immediately, before the first request
    threading.Thread(target=run_scheduler, daemon=True).start()
    # debug=False is deliberate: Flask's debug reloader re-executes this
    # whole script in a second process on every code change, which would
    # start a second scheduler thread ticking alongside the first one.
    #
    # port=5050, not Flask's usual default of 5000: on current macOS,
    # port 5000 is claimed by the AirPlay Receiver service, so 5000 fails
    # with "Address already in use" on most Macs unless that's disabled.
    app.run(host="0.0.0.0", port=5050, debug=False)
