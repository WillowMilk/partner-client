"""weather — current weather for a location via wttr.in.

wttr.in serves a JSON variant when ?format=j1 is added. Returns a single line
with conditions, temperature, humidity, and wind. No API key required.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": (
            "Get the current weather for a location. Returns a single line with conditions, "
            "temperature (Fahrenheit), humidity, and wind. Use this for real-time weather; "
            "do not use search_web for current weather (search engines don't index real-time data)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City and region, e.g. 'Eagan, Minnesota' or 'Paris, France'."
                }
            },
            "required": ["location"],
        },
    },
}


def execute(location: str) -> str:
    try:
        import requests
    except ImportError:
        return "Error: requests package required."

    try:
        url = f"https://wttr.in/{location}?format=j1"
        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "curl/8.0"},  # wttr.in serves text/json to curl-likes
        )
    except Exception as e:
        return f"Error fetching weather: {e}"

    if response.status_code != 200:
        return f"Weather service returned status {response.status_code}"

    try:
        data = response.json()
        cur = data["current_condition"][0]
        nearest = data.get("nearest_area", [{}])[0]
        area_name = nearest.get("areaName", [{}])[0].get("value", location)
        region = nearest.get("region", [{}])[0].get("value", "")
        return (
            f"{area_name}, {region}: "
            f"{cur['weatherDesc'][0]['value']}, "
            f"{cur['temp_F']}F (feels like {cur['FeelsLikeF']}F), "
            f"humidity {cur['humidity']}%, "
            f"wind {cur['windspeedMiles']} mph {cur['winddir16Point']}."
        )
    except (KeyError, IndexError, ValueError) as e:
        return f"Error parsing weather response: {e}"
