"""
CARTA Flood Briefing Generator

Turns the numbers in stop_flood_risk_report.csv and route_flood_summary.csv
into a plain-language document (carta_flood_briefing.txt) suitable for
presenting to a CARTA operations manager -- no code, no jargon, just what
was found and what it means route by route.
"""

import csv

STOPS_CSV = "stop_flood_risk_report.csv"
ROUTE_SUMMARY_CSV = "route_flood_summary.csv"
GTFS_ROUTES_TXT = "gtfs_data/carta-sc-us/routes.txt"
OUTPUT_PATH = "carta_flood_briefing.txt"

SEVERITY_ORDER = ["MINOR", "MODERATE", "MAJOR"]
HARBOR_FT_BY_SEVERITY = {"MINOR": "7.0 ft", "MODERATE": "7.5 ft", "MAJOR": "8.0 ft"}
EVENTS_PER_YEAR = {"MINOR": 25, "MODERATE": 8, "MAJOR": 3}


def load_route_long_names():
    """Map route short name -> long name, from the raw GTFS feed."""
    names = {}
    with open(GTFS_ROUTES_TXT, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            names[row["route_short_name"]] = row["route_long_name"]
    return names


def load_route_summary():
    with open(ROUTE_SUMMARY_CSV, newline="") as f:
        return list(csv.DictReader(f))


def load_stops_by_route():
    """Return {route: [stop_row, ...]} -- a stop serving multiple routes
    appears under each of them, matching how route_flood_summary.csv
    counted stops."""
    by_route = {}
    with open(STOPS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            for route in (r.strip() for r in row["routes"].split(",") if r.strip()):
                by_route.setdefault(route, []).append(row)
    return by_route


def severity_breakdown(stop_rows):
    """Count how many of a route's stops trip at each severity level."""
    counts = {level: 0 for level in SEVERITY_ORDER}
    for row in stop_rows:
        if row["flood_risk"] in counts:
            counts[row["flood_risk"]] += 1
    return counts


def lowest_stop(stop_rows):
    """The single lowest-elevation stop on the route, for a concrete example."""
    with_elev = [r for r in stop_rows if r["elevation_ft"]]
    if not with_elev:
        return None
    return min(with_elev, key=lambda r: float(r["elevation_ft"]))


def write_route_paragraph(route, summary_row, stop_rows, long_names, out):
    total = int(summary_row["total_stops"])
    at_risk = int(summary_row["stops_at_risk"])
    first_threshold = summary_row["first_threshold"]
    disruptions = summary_row["est_disruptions_per_year"]
    long_name = long_names.get(route, "")
    label = f"Route {route} ({long_name})" if long_name else f"Route {route}"

    out.write(f"{label}\n")
    out.write("-" * len(label) + "\n")

    if at_risk == 0:
        out.write(
            f"None of this route's {total} stops sit low enough to flood under any of the "
            "three NOAA tide stages evaluated (minor, moderate, or major coastal flood "
            "stage at Charleston Harbor). Based on this screening, {route} is not expected "
            "to see tide-driven service disruptions.\n\n".format(route=route)
        )
        return

    pct = round(100 * at_risk / total)
    breakdown = severity_breakdown(stop_rows)
    breakdown_bits = [
        f"{count} at {level.lower()} stage" for level, count in breakdown.items() if count
    ]
    breakdown_str = ", ".join(breakdown_bits)

    low = lowest_stop(stop_rows)
    example = ""
    if low:
        example = (
            f" Its lowest point, {low['stop_name']}, sits at only "
            f"{float(low['elevation_ft']):.1f} ft of elevation."
        )

    out.write(
        f"{at_risk} of {total} stops on this route ({pct}%) sit low enough to flood during "
        f"elevated tides ({breakdown_str}).{example} The first stops go under at the "
        f"{first_threshold} coastal flood stage ({HARBOR_FT_BY_SEVERITY[first_threshold]} harbor "
        f"level), which NOAA's historical data shows Charleston Harbor reaches about "
        f"{disruptions} times per year. In practical terms, riders on this route should expect "
        f"tide-related disruptions on the order of {disruptions} days a year, concentrated "
        "around its lowest-lying stops.\n\n"
    )


def write_overview(route_summary, out):
    ranked = sorted(
        route_summary,
        key=lambda r: (-int(r["est_disruptions_per_year"]), -int(r["stops_at_risk"])),
    )
    most_exposed = [r for r in ranked if int(r["stops_at_risk"]) > 0]
    least_exposed = [r for r in ranked if int(r["stops_at_risk"]) == 0]

    total_stops_at_risk = sum(int(r["stops_at_risk"]) for r in route_summary)
    total_stops = sum(int(r["total_stops"]) for r in route_summary)

    out.write("OVERVIEW\n")
    out.write("=" * 8 + "\n")
    out.write(
        "This briefing covers 155 unique CARTA stops across seven routes: DASH 213, DASH 211, "
        "DASH 210, Route 20, Route 203, Route 30, and XP1. Some stops are shared by more than "
        "one route, so the counts below are given per route -- a shared stop is counted once "
        "for each route it serves, since it is a real risk to riders on all of them.\n\n"
        f"Of {total_stops} route-stop combinations across the seven routes, {total_stops_at_risk} "
        "sit at an elevation low enough to flood under at least one of NOAA's three coastal "
        "flood stages for Charleston Harbor (Station 8665530). Exposure varies significantly "
        "by route:\n\n"
    )
    if most_exposed:
        names = ", ".join(f"Route {r['route']}" for r in most_exposed)
        out.write(f"  - Routes with flood-exposed stops: {names}\n")
    if least_exposed:
        names = ", ".join(f"Route {r['route']}" for r in least_exposed)
        out.write(f"  - Routes with no flood-exposed stops under these thresholds: {names}\n")
    out.write("\n")
    out.write(
        "Routes are detailed below, ordered from most to least frequently affected.\n\n"
    )


def write_methodology(out):
    out.write("METHODOLOGY & DATA SOURCES\n")
    out.write("=" * 27 + "\n")
    out.write(
        "- Stop locations and route assignments: CARTA's public GTFS feed "
        "(data.trilliumtransit.com/gtfs/carta-sc-us/carta-sc-us.zip).\n"
        "- Ground elevation at each stop: USGS 3DEP Elevation Point Query Service "
        "(epqs.nationalmap.gov).\n"
        "- Flood thresholds: a stop is classified as flooding once ground elevation drops "
        "below 5.5 ft (minor, 7.0 ft harbor level), 6.5 ft (moderate, 7.5 ft harbor level), "
        "or 7.5 ft (major, 8.0 ft harbor level).\n"
        "- Annual event frequency: NOAA's historical average number of times per year "
        "Charleston Harbor Station 8665530 reaches each tide stage (minor: 25/yr, "
        "moderate: 8/yr, major: 3/yr).\n\n"
        "IMPORTANT CAVEAT: the elevation-to-flood mapping above is a simplifying screening "
        "assumption, not a validated hydrological model. It does not account for storm "
        "drainage capacity, distance from the harbor, or storm surge behavior. This "
        "analysis should be used to prioritize which stops warrant a closer engineering "
        "look, not as a precise prediction of which stops will flood on a given day.\n\n"
    )


if __name__ == "__main__":
    long_names = load_route_long_names()
    route_summary = load_route_summary()
    stops_by_route = load_stops_by_route()

    ranked = sorted(
        route_summary,
        key=lambda r: (-int(r["est_disruptions_per_year"]), -int(r["stops_at_risk"])),
    )

    with open(OUTPUT_PATH, "w") as out:
        out.write("CARTA FLOOD ALERT SYSTEM -- ROUTE FLOOD RISK BRIEFING\n")
        out.write("=" * 55 + "\n\n")

        write_overview(route_summary, out)
        write_methodology(out)

        out.write("ROUTE-BY-ROUTE SUMMARY\n")
        out.write("=" * 23 + "\n\n")
        for row in ranked:
            route = row["route"]
            write_route_paragraph(route, row, stops_by_route.get(route, []), long_names, out)

    print(f"Wrote briefing to {OUTPUT_PATH}")
