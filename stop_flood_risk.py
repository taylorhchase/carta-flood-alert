"""
CARTA Stop-Level Flood Risk Report

For CARTA's downtown/DASH routes (213, 211, 210, 20, 203, 30, XP1), looks
up every stop's ground elevation via USGS 3DEP and flags which stops sit
low enough to flood at each given NOAA Charleston Harbor tide stage.

Data sources:
  - CARTA GTFS feed (routes.txt, trips.txt, stop_times.txt, stops.txt),
    downloaded from http://data.trilliumtransit.com/gtfs/carta-sc-us/carta-sc-us.zip
    (the transitfeeds.com link originally given is dead -- see note below)
  - USGS 3DEP Elevation Point Query Service (https://epqs.nationalmap.gov/v1/json)

NOTE ON THE FLOOD THRESHOLDS:
The harbor-level -> ground-elevation mapping below (a stop floods once
ground elevation drops under 5.5 / 6.5 / 7.5 ft at harbor levels of
7.0 / 7.5 / 8.0 ft) is a simplifying assumption, not a hydrological model
validated against real flood observations. It ignores drainage capacity,
distance from the harbor, and storm surge behavior -- treat this as a
rough screening tool for which stops deserve a closer look, not as a
precise prediction of which stops will flood.
"""

import csv
import time

import requests

GTFS_DIR = "gtfs_data/carta-sc-us"
TARGET_ROUTE_SHORT_NAMES = {"213", "211", "210", "20", "203", "30", "XP1"}

USGS_EPQS_URL = "https://epqs.nationalmap.gov/v1/json"

# (label, harbor level in ft MLLW that triggers it, ground elevation in ft
# below which a stop floods at that harbor level) -- ordered from the
# lowest harbor level (easiest to trigger) to the highest.
STOP_FLOOD_THRESHOLDS = [
    ("MINOR", 7.0, 5.5),
    ("MODERATE", 7.5, 6.5),
    ("MAJOR", 8.0, 7.5),
]


def load_target_routes():
    """Return {route_id: route_short_name} for the 7 routes we care about."""
    routes = {}
    with open(f"{GTFS_DIR}/routes.txt", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["route_short_name"] in TARGET_ROUTE_SHORT_NAMES:
                routes[row["route_id"]] = row["route_short_name"]
    return routes


def load_target_trips(route_ids):
    """Return {trip_id: route_id} for trips that belong to our target routes."""
    trip_to_route = {}
    with open(f"{GTFS_DIR}/trips.txt", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["route_id"] in route_ids:
                trip_to_route[row["trip_id"]] = row["route_id"]
    return trip_to_route


def load_stop_ids_by_route(trip_to_route):
    """Return {stop_id: {route_id, ...}}, streaming the 12 MB stop_times.txt
    row by row instead of loading the whole file into memory."""
    stop_to_routes = {}
    with open(f"{GTFS_DIR}/stop_times.txt", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            route_id = trip_to_route.get(row["trip_id"])
            if route_id is None:
                continue
            stop_to_routes.setdefault(row["stop_id"], set()).add(route_id)
    return stop_to_routes


def load_stop_details(stop_ids):
    """Return {stop_id: {name, lat, lon}} for the given stop IDs."""
    details = {}
    with open(f"{GTFS_DIR}/stops.txt", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["stop_id"] in stop_ids:
                details[row["stop_id"]] = {
                    "name": row["stop_name"],
                    "lat": float(row["stop_lat"]),
                    "lon": float(row["stop_lon"]),
                }
    return details


def get_elevation_ft(lat, lon):
    """Query USGS 3DEP for ground elevation at a point, in feet."""
    params = {"x": lon, "y": lat, "units": "Feet", "wkid": 4326, "includeDate": "false"}
    response = requests.get(USGS_EPQS_URL, params=params, timeout=10)
    response.raise_for_status()
    value = response.json().get("value")
    if value is None or value < -1000:
        # USGS returns a large negative sentinel for points with no
        # coverage (e.g. over open water) instead of an HTTP error.
        return None
    return value


def classify_stop_flood_risk(elevation_ft):
    """Return (label, harbor_ft) for the lowest harbor stage this
    elevation would flood at, or (None, None) if it clears all three."""
    for label, harbor_ft, elev_cutoff in STOP_FLOOD_THRESHOLDS:
        if elevation_ft < elev_cutoff:
            return label, harbor_ft
    return None, None


def build_report(route_short_names_by_id):
    trip_to_route = load_target_trips(set(route_short_names_by_id.keys()))
    stop_to_routes = load_stop_ids_by_route(trip_to_route)
    stop_details = load_stop_details(set(stop_to_routes.keys()))

    rows = []
    total = len(stop_details)
    for i, (stop_id, info) in enumerate(
        sorted(stop_details.items(), key=lambda kv: kv[1]["name"]), start=1
    ):
        route_labels = sorted(route_short_names_by_id[r] for r in stop_to_routes[stop_id])

        try:
            elevation_ft = get_elevation_ft(info["lat"], info["lon"])
        except requests.RequestException as exc:
            print(f"  [{i}/{total}] elevation lookup failed for {info['name']}: {exc}")
            elevation_ft = None

        if elevation_ft is None:
            risk_label, harbor_ft = "NO ELEVATION DATA", None
        else:
            label, harbor_ft = classify_stop_flood_risk(elevation_ft)
            risk_label = label if label else "no risk under these thresholds"

        rows.append(
            {
                "stop_id": stop_id,
                "name": info["name"],
                "routes": ", ".join(route_labels),
                "lat": info["lat"],
                "lon": info["lon"],
                "elevation_ft": elevation_ft,
                "risk_label": risk_label,
                "harbor_ft": harbor_ft,
            }
        )

        if i % 20 == 0 or i == total:
            print(f"  ...{i}/{total} stop elevations fetched")

        time.sleep(0.05)  # be polite to the USGS service

    return rows


def print_table(rows):
    print()
    print("=" * 100)
    print("CARTA STOP-LEVEL FLOOD RISK REPORT")
    print("Routes: DASH 213, DASH 211, DASH 210, Route 20, Route 203, Route 30, XP1")
    print("=" * 100)
    header = f"{'Stop Name':<42} {'Routes':<18} {'Elev (ft)':>10}  Flood Risk"
    print(header)
    print("-" * len(header))

    def sort_key(r):
        # Stops with no elevation data sort last; otherwise lowest (most
        # at-risk) elevation first.
        return (r["elevation_ft"] is None, r["elevation_ft"] or 0)

    for row in sorted(rows, key=sort_key):
        elev_str = "n/a" if row["elevation_ft"] is None else f"{row['elevation_ft']:.2f}"
        print(f"{row['name'][:42]:<42} {row['routes']:<18} {elev_str:>10}  {row['risk_label']}")

    at_risk = [r for r in rows if r["harbor_ft"] is not None]
    no_data = [r for r in rows if r["elevation_ft"] is None]
    print()
    print(f"{len(at_risk)} of {len(rows)} stops sit below at least one flood threshold.")
    if no_data:
        print(f"{len(no_data)} stop(s) had no elevation data returned.")


def write_csv(rows, path="stop_flood_risk_report.csv"):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["stop_id", "stop_name", "routes", "lat", "lon", "elevation_ft", "flood_risk", "triggers_at_harbor_ft"]
        )
        for r in sorted(rows, key=lambda r: (r["elevation_ft"] is None, r["elevation_ft"] or 0)):
            writer.writerow(
                [
                    r["stop_id"],
                    r["name"],
                    r["routes"],
                    r["lat"],
                    r["lon"],
                    "" if r["elevation_ft"] is None else round(r["elevation_ft"], 2),
                    r["risk_label"],
                    r["harbor_ft"] or "",
                ]
            )
    print(f"\nFull data also saved to {path}")


if __name__ == "__main__":
    routes = load_target_routes()
    print(f"Matched {len(routes)} routes: {sorted(routes.values())}")
    print("Fetching stop elevations from USGS 3DEP (one request per stop)...")
    report_rows = build_report(routes)
    print_table(report_rows)
    write_csv(report_rows)
