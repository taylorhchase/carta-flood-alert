"""
CARTA Route-Level Flood Summary

Builds on stop_flood_risk_report.csv (per-stop elevation + flood risk,
from Session 5) by:
  1. Adding a flood_events_per_year column, using NOAA's historical
     average frequency of each tide stage at Charleston Harbor
     Station 8665530.
  2. Rolling the per-stop data up into a per-route summary: how many
     stops on each route are at risk, what's the earliest (most
     frequent) threshold any of them trips, and roughly how many
     times a year that route can expect a flood-related disruption.

NOTE: "flood_events_per_year" per stop is really "how often does the
harbor stage that WOULD flood this stop occur" -- it's a property of the
threshold, not of the individual stop. Two stops that both trigger at
MINOR both get the same 25/year figure. The interesting per-stop signal
is which threshold they trip, not a unique frequency each.
"""

import csv

INPUT_CSV = "stop_flood_risk_report.csv"
STOPS_OUTPUT_CSV = "stop_flood_risk_report.csv"  # updated in place
ROUTES_OUTPUT_CSV = "route_flood_summary.csv"

# NOAA historical average frequency of each tide stage being reached,
# Charleston Harbor Station 8665530 (events per year).
EVENTS_PER_YEAR = {
    "MINOR": 25,
    "MODERATE": 8,
    "MAJOR": 3,
}

# Severity order, most frequent (easiest to trigger) first, so we can
# find each route's *earliest* triggering threshold.
SEVERITY_ORDER = ["MINOR", "MODERATE", "MAJOR"]


def load_stops():
    with open(INPUT_CSV, newline="") as f:
        return list(csv.DictReader(f))


def add_flood_events_column(rows):
    """Attach flood_events_per_year to each row, based on its flood_risk
    label. Stops with no risk or no elevation data get an empty value --
    there's no "events per year" number for a stop that never floods
    under these thresholds."""
    for row in rows:
        row["flood_events_per_year"] = EVENTS_PER_YEAR.get(row["flood_risk"], "")
    return rows


def write_stops_csv(rows, path):
    fieldnames = [
        "stop_id", "stop_name", "routes", "lat", "lon",
        "elevation_ft", "flood_risk", "triggers_at_harbor_ft",
        "flood_events_per_year",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_route_summary(rows):
    """
    Roll stop-level rows up into one summary row per route. A stop that
    serves multiple routes (e.g. "213, XP1") counts toward EACH of those
    routes -- it's a real flood risk for riders of both, not split
    between them.
    """
    routes = {}  # route_name -> {"total": int, "at_risk": [] of flood_risk labels}

    for row in rows:
        route_names = [r.strip() for r in row["routes"].split(",") if r.strip()]
        for route in route_names:
            bucket = routes.setdefault(route, {"total": 0, "risks": []})
            bucket["total"] += 1
            if row["flood_risk"] in EVENTS_PER_YEAR:
                bucket["risks"].append(row["flood_risk"])

    summary_rows = []
    for route in sorted(routes.keys()):
        bucket = routes[route]
        at_risk_count = len(bucket["risks"])

        if at_risk_count == 0:
            first_threshold = "none"
            annual_disruptions = 0
        else:
            # The route's first (most frequent) triggering threshold is
            # the least-severe threshold reached by ANY of its stops --
            # that's the flood stage the route will feel first and most
            # often, regardless of how rare its worse-case stops are.
            first_threshold = next(
                level for level in SEVERITY_ORDER if level in bucket["risks"]
            )
            annual_disruptions = EVENTS_PER_YEAR[first_threshold]

        summary_rows.append(
            {
                "route": route,
                "total_stops": bucket["total"],
                "stops_at_risk": at_risk_count,
                "first_threshold": first_threshold,
                "est_disruptions_per_year": annual_disruptions,
            }
        )

    return summary_rows


def write_route_summary_csv(summary_rows, path):
    fieldnames = ["route", "total_stops", "stops_at_risk", "first_threshold", "est_disruptions_per_year"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)


def print_route_summary(summary_rows):
    print()
    print("=" * 80)
    print("CARTA ROUTE-LEVEL FLOOD SUMMARY")
    print("=" * 80)
    header = f"{'Route':<8} {'Total Stops':>12} {'At Risk':>8} {'First Threshold':>16} {'Est. Disruptions/Year':>22}"
    print(header)
    print("-" * len(header))
    # Sort so the most exposed routes (highest annual disruption estimate,
    # then most at-risk stops) show up first.
    for r in sorted(
        summary_rows,
        key=lambda r: (-r["est_disruptions_per_year"], -r["stops_at_risk"]),
    ):
        print(
            f"{r['route']:<8} {r['total_stops']:>12} {r['stops_at_risk']:>8} "
            f"{r['first_threshold']:>16} {r['est_disruptions_per_year']:>22}"
        )


if __name__ == "__main__":
    stops = load_stops()
    stops = add_flood_events_column(stops)
    write_stops_csv(stops, STOPS_OUTPUT_CSV)
    print(f"Updated {STOPS_OUTPUT_CSV} with flood_events_per_year column ({len(stops)} rows).")

    route_summary = build_route_summary(stops)
    write_route_summary_csv(route_summary, ROUTES_OUTPUT_CSV)
    print(f"Wrote route-level summary to {ROUTES_OUTPUT_CSV} ({len(route_summary)} routes).")

    print_route_summary(route_summary)
