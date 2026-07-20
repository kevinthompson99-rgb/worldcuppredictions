"""Thin client for the football-data.org v4 API (free tier).

Docs: https://www.football-data.org/documentation/quickstart
Auth: header `X-Auth-Token: <FOOTBALL_DATA_API_KEY>`
"""

import requests
from flask import current_app

TIMEOUT_SECONDS = 15


def _headers():
    return {
        "X-Auth-Token": current_app.config["FOOTBALL_DATA_API_KEY"],
        "X-Api-Version": "v4.1",
    }


def get_premier_league_matches(season=None, date_from=None, date_to=None):
    """Fetch Premier League matches, optionally filtered by date (YYYY-MM-DD).

    `season` pins the fixture list to a specific season's year (e.g. 2026 for 2026/27) -
    always passed explicitly (defaulting to config's PL_SEASON_YEAR) rather than relying on
    the API's own "current season" default, since this app runs across an entire season.
    """
    base = current_app.config["FOOTBALL_DATA_BASE_URL"]
    code = current_app.config["PREMIER_LEAGUE_COMPETITION_CODE"]
    url = f"{base}/competitions/{code}/matches"

    params = {"season": season or current_app.config["PL_SEASON_YEAR"]}
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to

    response = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json().get("matches", [])
