# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""Batch IP geolocation via ip-api.com."""

import logging

import aiohttp

logger = logging.getLogger("slashAI.utils.geoip")

# US state name → two-letter abbreviation
_US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}


async def resolve_geo(ips: list[str]) -> dict[str, str]:
    """Batch-resolve IPs to location strings via ip-api.com.

    Returns a dict mapping each IP to a display string like
    "Dallas, TX" (US) or "Frankfurt, HE, DE" (international).
    Returns empty string for IPs that fail to resolve.
    On any error, returns an empty dict (graceful degradation).
    """
    unique_ips = list({ip for ip in ips if ip})
    if not unique_ips:
        return {}

    payload = [
        {"query": ip, "fields": "query,status,city,regionName,country,countryCode"}
        for ip in unique_ips[:100]  # batch endpoint max 100
    ]

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            # Free tier requires HTTP (not HTTPS)
            async with session.post("http://ip-api.com/batch", json=payload) as resp:
                if resp.status != 200:
                    logger.warning("ip-api.com returned status %d", resp.status)
                    return {}
                data = await resp.json()
    except Exception:
        logger.debug("ip-api.com request failed", exc_info=True)
        return {}

    result: dict[str, str] = {}
    for entry in data:
        if entry.get("status") != "success":
            continue
        ip = entry["query"]
        city = entry.get("city", "")
        region = entry.get("regionName", "")
        country_code = entry.get("countryCode", "")

        if not city:
            continue

        if country_code == "US":
            abbr = _US_STATES.get(region, region)
            result[ip] = f"{city}, {abbr}"
        else:
            # International: "City, Region, CC"
            parts = [city]
            if region and region != city:
                parts.append(region)
            parts.append(country_code)
            result[ip] = ", ".join(parts)

    return result
