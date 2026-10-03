"""`campi ui`: on-demand desktop app (FastAPI + pywebview) for browsing sightings and timelapse output.

Runs from its own venv (venv-ui) in the desktop session and only reads what the service writes. The service and
every other command never import this package. Its own data (stars, hidden items, label corrections, its log) lives
in CampiTimelapse\\ui\\.
"""
