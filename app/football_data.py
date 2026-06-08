"""Thin client for the football-data.org v4 API (free tier).

Docs: https://www.football-data.org/documentation/quickstart
Auth: header `X-Auth-Token: <FOOTBALL_DATA_API_KEY>`
"""

import requests
from flask import current_app

TIMEOUT_SECONDS = 15


def _headers():
    return {"X-Auth-Token": current_app.config["FOOTBALL_DATA_API_KEY"]}


def get_world_cup_matches(date_from=None, date_to=None):
    """Fetch matches for the World Cup competition, optionally filtered by date (YYYY-MM-DD)."""
    base = current_app.config["FOOTBALL_DATA_BASE_URL"]
    code = current_app.config["WORLD_CUP_COMPETITION_CODE"]
    url = f"{base}/competitions/{code}/matches"

    params = {}
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to

    response = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json().get("matches", [])
