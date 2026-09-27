from flood_alert import classify_flood_risk

thresholds = {"minor": 7.01, "moderate": 7.51, "major": 8.01}

assert classify_flood_risk(5.39, thresholds) is None, "5.39 ft should be no risk"
assert classify_flood_risk(7.00, thresholds) is None, "7.00 ft should be no risk (below minor threshold)"
assert classify_flood_risk(7.01, thresholds) == "MINOR FLOOD RISK", "7.01 ft should be minor (exactly at threshold)"
assert classify_flood_risk(7.30, thresholds) == "MINOR FLOOD RISK", "7.30 ft should be minor (between minor and moderate)"
assert classify_flood_risk(7.51, thresholds) == "MODERATE FLOOD RISK", "7.51 ft should be moderate (exactly at threshold)"
assert classify_flood_risk(7.90, thresholds) == "MODERATE FLOOD RISK", "7.90 ft should be moderate (between moderate and major)"
assert classify_flood_risk(8.01, thresholds) == "MAJOR FLOOD RISK", "8.01 ft should be major (exactly at threshold)"
assert classify_flood_risk(9.50, thresholds) == "MAJOR FLOOD RISK", "9.50 ft should be major (way above everything)"

print("All 8 threshold tests passed.")
