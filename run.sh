#!/bin/bash
# Starts the CARTA Flood Alert Dashboard.
#
# Run from anywhere -- this script cd's into its own directory first, so
# app.py can find its data files (the CSVs, gtfs_data/) via the relative
# paths it expects, regardless of where you were standing when you ran it.
cd "$(dirname "$0")"
# exec replaces this shell with the python process (rather than running it
# as a child), so Ctrl+C or `kill` on run.sh actually stops the server
# instead of leaving it running as an orphaned background process.
exec python3 app.py
