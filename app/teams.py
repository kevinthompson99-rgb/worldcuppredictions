"""Maps national team names (as they arrive from football-data.org) to flag emoji.

football-data.org sometimes returns the long form, sometimes a shorter alias (see
`Fixture.home_team`/`away_team` in app/sync.py, populated from `name` or `shortName`)
- so each flag is keyed by every form we've seen or might reasonably see, normalised
to lowercase for lookup.
"""


def _flag(iso2):
    """Build a flag emoji from an ISO 3166-1 alpha-2 code via regional indicator symbols."""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in iso2.upper())


# England/Scotland/Wales/Northern Ireland have no ISO country code - their flags are
# emoji tag sequences for the relevant subdivision.
_ENGLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
_SCOTLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
_WALES = "\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F"

# Name -> ISO 3166-1 alpha-2 code (or a literal flag for the home-nations exceptions),
# covering the hosts, the confirmed/likely 2026 confederation qualifiers, and the
# common aliases football-data.org uses for them.
_TEAM_CODES = {
    # 2026 hosts
    "canada": "CA",
    "mexico": "MX",
    "usa": "US",
    "united states": "US",
    "united states of america": "US",
    # CONMEBOL
    "argentina": "AR",
    "brazil": "BR",
    "uruguay": "UY",
    "ecuador": "EC",
    "colombia": "CO",
    "paraguay": "PY",
    "bolivia": "BO",
    "chile": "CL",
    "peru": "PE",
    "venezuela": "VE",
    # CONCACAF
    "costa rica": "CR",
    "panama": "PA",
    "honduras": "HN",
    "jamaica": "JM",
    "haiti": "HT",
    "curacao": "CW",
    "curaçao": "CW",
    "guatemala": "GT",
    "el salvador": "SV",
    "trinidad and tobago": "TT",
    # UEFA
    "england": _ENGLAND,
    "scotland": _SCOTLAND,
    "wales": _WALES,
    "northern ireland": "GB",
    "republic of ireland": "IE",
    "ireland": "IE",
    "france": "FR",
    "germany": "DE",
    "spain": "ES",
    "portugal": "PT",
    "italy": "IT",
    "netherlands": "NL",
    "belgium": "BE",
    "croatia": "HR",
    "switzerland": "CH",
    "denmark": "DK",
    "sweden": "SE",
    "norway": "NO",
    "poland": "PL",
    "austria": "AT",
    "serbia": "RS",
    "ukraine": "UA",
    "turkey": "TR",
    "türkiye": "TR",
    "czech republic": "CZ",
    "czechia": "CZ",
    "greece": "GR",
    "hungary": "HU",
    "romania": "RO",
    "slovakia": "SK",
    "slovenia": "SI",
    "iceland": "IS",
    "albania": "AL",
    "georgia": "GE",
    "bosnia and herzegovina": "BA",
    "north macedonia": "MK",
    "kosovo": "XK",
    "finland": "FI",
    # CAF
    "morocco": "MA",
    "tunisia": "TN",
    "egypt": "EG",
    "algeria": "DZ",
    "senegal": "SN",
    "ghana": "GH",
    "nigeria": "NG",
    "cameroon": "CM",
    "ivory coast": "CI",
    "côte d'ivoire": "CI",
    "cote d'ivoire": "CI",
    "south africa": "ZA",
    "cape verde": "CV",
    "cabo verde": "CV",
    "dr congo": "CD",
    "congo dr": "CD",
    "mali": "ML",
    "gabon": "GA",
    "uganda": "UG",
    "jordan": "JO",
    # AFC
    "japan": "JP",
    "korea republic": "KR",
    "south korea": "KR",
    "iran": "IR",
    "ir iran": "IR",
    "saudi arabia": "SA",
    "australia": "AU",
    "qatar": "QA",
    "uzbekistan": "UZ",
    "iraq": "IQ",
    "indonesia": "ID",
    "bahrain": "BH",
    "united arab emirates": "AE",
    "korea dpr": "KP",
    "north korea": "KP",
    "china": "CN",
    "china pr": "CN",
    "oman": "OM",
    "kuwait": "KW",
    "vietnam": "VN",
    "thailand": "TH",
    "india": "IN",
    # OFC
    "new zealand": "NZ",
    "new caledonia": "NC",
    "fiji": "FJ",
    "papua new guinea": "PG",
    "tahiti": "PF",
    "solomon islands": "SB",
}

_FLAG_CACHE = {
    name: (code if len(code) > 2 else _flag(code)) for name, code in _TEAM_CODES.items()
}


def flag_for(team_name):
    """Return the flag emoji for a team name, or '' if the team isn't recognised."""
    if not team_name:
        return ""
    return _FLAG_CACHE.get(team_name.strip().lower(), "")
