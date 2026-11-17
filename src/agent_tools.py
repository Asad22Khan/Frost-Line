"""The three tools the FrostLine agent can call.

  query_telemetry_db         text-to-SQL over the read-only fleet view (guarded by src/sql_guard.py)
  fetch_corridor_conditions  live weather at a GPS point (Open-Meteo, free, no key)
  search_compliance_sop      semantic search over the client's SOPs, with citations

Every tool returns plain text, and failures are returned as text too, never raised. The agent can then
read the error, correct itself, or tell the user honestly what is unavailable instead of guessing.
"""

from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import requests
from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.db import get_engine
from src.sop_store import search_sop
from src.sql_guard import MAX_ROWS, UnsafeQueryError, validate_and_limit

VIEW = "fde_views.vw_active_fleet"

# The SOPs were written against the legacy column codes; the agent queries the clean view.
SOP_COLUMN_MAP = {
    "IOT_TEMP_VAL_C": "cargo_temp_c",
    "PRT_CNG_LVL": "port_congestion_level",
    "RISK_CLS_TXT": "risk_classification",
    "DELAY_PROB_DEC": "delay_probability",
}


# ---------------------------------------------------------------- tool 1: telemetry database
@lru_cache(maxsize=1)
def fleet_view_schema() -> str:
    """Column list with descriptions, read from the COMMENTs stored on the view in the database."""
    query = text(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), col_description(a.attrelid, a.attnum)
        FROM pg_attribute a
        WHERE a.attrelid = CAST(:view AS regclass) AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """
    )
    with get_engine("agent").connect() as conn:
        rows = conn.execute(query, {"view": VIEW}).all()
        summary = conn.execute(text("SELECT obj_description(CAST(:view AS regclass))"), {"view": VIEW}).scalar()
    columns = "\n".join(f"- {name} ({dtype}): {comment or ''}" for name, dtype, comment in rows)
    return f"{summary}\nColumns:\n{columns}"


def _format_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, Decimal):
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def query_telemetry_db(sql_query: str) -> str:
    try:
        safe_sql = validate_and_limit(sql_query)
    except UnsafeQueryError as exc:
        return f"QUERY REJECTED: {exc}"

    try:
        with get_engine("agent").connect() as conn:
            result = conn.execute(text(safe_sql))
            columns = list(result.keys())
            rows = result.fetchmany(MAX_ROWS)
    except DBAPIError as exc:
        message = str(exc.orig).strip().splitlines()[0]
        return f"DATABASE ERROR: {message}\nFix the SQL and try again."

    if not rows:
        return f"No rows matched.\nSQL executed: {safe_sql}"
    lines = [" | ".join(columns)] + [" | ".join(_format_value(v) for v in row) for row in rows]
    footer = f"\n({len(rows)} row(s); results are capped at {MAX_ROWS})" if len(rows) == MAX_ROWS else ""
    return f"SQL executed: {safe_sql}\n\n" + "\n".join(lines) + footer


def _telemetry_description() -> str:
    try:
        schema = fleet_view_schema()
    except Exception:  # database asleep or unreachable: fall back to a short static description
        schema = "Columns include event_time, latitude, longitude, cargo_temp_c, risk_classification, " \
                 "delay_probability, port_congestion_level, route_risk_level, weather_severity and more."
    return (
        f"Run ONE read-only PostgreSQL SELECT query against {VIEW} (you can write FROM vw_active_fleet). "
        f"Use aggregates (COUNT, AVG, MIN, MAX, GROUP BY) instead of fetching raw rows; at most {MAX_ROWS} rows "
        "are returned. Use PostgreSQL syntax (LIMIT, not TOP). Filter locations with latitude/longitude ranges.\n\n"
        f"{schema}"
    )


# ---------------------------------------------------------------- tool 2: live corridor weather
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}
SEVERE_CODES = {66, 67, 75, 86, 95, 96, 99}
ELEVATED_CODES = {45, 48, 56, 57, 65, 73, 82, 85}


def _weather_risk(wind: float, gusts: float, precip: float, code: int, temp: float) -> str:
    if code in SEVERE_CODES or gusts >= 60:
        level = "HIGH"
    elif code in ELEVATED_CODES or wind >= 30 or precip >= 2:
        level = "ELEVATED"
    else:
        level = "LOW"
    heat = " High ambient heat increases refrigeration load on reefer units." if temp >= 30 else ""
    return f"{level} (weather-based estimate from wind, gusts, precipitation and conditions).{heat}"


def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return "INVALID COORDINATES: latitude must be -90..90 and longitude -180..180."
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,"
                           "wind_speed_10m,wind_gusts_10m",
                "timezone": "auto",
            },
            timeout=8,
        )
        response.raise_for_status()
        now = response.json()["current"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        return f"WEATHER SERVICE UNAVAILABLE ({exc.__class__.__name__}). Do not guess conditions; report them as unknown."

    code = int(now["weather_code"])
    return (
        f"LIVE weather right now at ({latitude}, {longitude}), observed {now['time']} local time:\n"
        f"- Conditions: {WEATHER_CODES.get(code, f'code {code}')}\n"
        f"- Air temperature: {now['temperature_2m']} °C, humidity {now['relative_humidity_2m']}%\n"
        f"- Wind: {now['wind_speed_10m']} km/h, gusts {now['wind_gusts_10m']} km/h\n"
        f"- Precipitation: {now['precipitation']} mm\n"
        f"- Transit weather risk: {_weather_risk(now['wind_speed_10m'], now['wind_gusts_10m'], now['precipitation'], code, now['temperature_2m'])}\n"
        "Note: this is current weather. Fleet telemetry in the database is historical (2021-2024)."
    )


# ---------------------------------------------------------------- tool 3: SOP knowledge base
def search_compliance_sop(question: str) -> str:
    try:
        results = search_sop(question, top_k=3)
    except Exception as exc:
        return f"SOP KNOWLEDGE BASE UNAVAILABLE ({exc.__class__.__name__}). Do not state any SOP rule from memory."

    if not results:
        return "NO MATCHING SOP CLAUSE. The SOPs do not cover this topic; say so instead of guessing."

    blocks = []
    for r in results:
        section = r["section"].split(" > ")[-1] if r["section"] else "-"
        blocks.append(f"[Source: {r['source_file']} | Section: {section} | relevance {r['score']}]\n{r['text']}")
    mapping = ", ".join(f"{legacy} = {col}" for legacy, col in SOP_COLUMN_MAP.items())
    return "\n\n".join(blocks) + f"\n\nColumn names used in the SOP map to the database view as: {mapping}."


# ---------------------------------------------------------------- registry
def build_tools() -> list[BaseTool]:
    """Create the LangChain tools. The SQL tool's description is generated from the live view schema."""
    return [
        StructuredTool.from_function(
            func=query_telemetry_db,
            name="query_telemetry_db",
            description=_telemetry_description(),
        ),
        StructuredTool.from_function(
            func=fetch_corridor_conditions,
            name="fetch_corridor_conditions",
            description=(
                "Get LIVE current weather (temperature, wind, gusts, precipitation, conditions) and a transit "
                "weather-risk estimate for a GPS point. Use it when the user asks about current conditions "
                "along a route or near a location."
            ),
        ),
        StructuredTool.from_function(
            func=search_compliance_sop,
            name="search_compliance_sop",
            description=(
                "Search the company's Standard Operating Procedures (cold-chain temperature rules, breach "
                "mitigation, port congestion diversion, risk escalation tiers). Returns the matching clauses "
                "with their source file and section, which you must cite."
            ),
        ),
    ]
