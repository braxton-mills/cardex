"""The Campi HTTP API (campi-ios docs/api-contract.md, v1) and the `campi ui` desktop window onto it.

Runs from its own venv (venv-ui): under the supervisor as `campi_timelapse api` when [api] enabled = true, or inside
`campi ui`. It only reads what the service writes; the service and every other command never import this package.
Its own data (stars, hidden items, label corrections, paired devices, posters, its logs) lives in
CampiTimelapse\\ui\\.
"""
