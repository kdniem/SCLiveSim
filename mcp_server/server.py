"""MCP server: lets AI agents watch and steer SC LiveSim runs.

It talks only to the REST API (the access layer), never to the database.

Run it:
- stdio (Claude Desktop, Claude Code, other MCP clients): `python -m mcp_server`
- Streamable HTTP: `python -m mcp_server --http` (port 8001, path /mcp)

Environment: LIVESIM_API_URL (default http://localhost:8000).
"""

import os
from collections import Counter
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from engine import ENGINE_VERSION

INSTRUCTIONS = """\
SC LiveSim streams live supply chain data (orders, shipments, inventory,
disruptions) from replayed datasets or simulations. All timestamps are simulated
time (UTC). Start with `list_runs` or `run_summary`. Tools that take `run`
default to the most recent run. Build your own scenario with `builder_options`
and `build_scenario`, then `start_run`; `dataco-continued` replays a real dataset
and continues it with a calibrated simulation. Simulations accept injected
disruptions (`list_disruption_types`, then `inject_disruption`)."""

server: MCPServer = MCPServer(
    name="sc-livesim",
    title="SC LiveSim",
    instructions=INSTRUCTIONS,
    website_url="https://github.com/kdniem/SCLiveSim",
    version=ENGINE_VERSION,
)

MAX_ROWS = 200


def api() -> httpx.Client:
    """HTTP client for the access layer (replaced in tests)."""
    return httpx.Client(
        base_url=os.environ.get("LIVESIM_API_URL", "http://localhost:8000"), timeout=15
    )


def _get(path: str, **params: Any) -> Any:
    clean = {k: v for k, v in params.items() if v is not None}
    with api() as client:
        response = client.get(path, params=clean)
        _raise(response)
        return response.json()


def _post(path: str, body: dict[str, Any] | None = None) -> Any:
    with api() as client:
        response = client.post(path, json=body or {})
        _raise(response)
        return response.json()


def _raise(response: httpx.Response) -> None:
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ValueError(f"API error {response.status_code}: {detail}")


@server.tool()
def list_scenarios() -> list[dict[str, Any]]:
    """List scenarios that can be run (replayed datasets and simulations)."""
    result: list[dict[str, Any]] = _get("/scenarios")
    return result


@server.tool()
def list_runs() -> list[dict[str, Any]]:
    """List runs with status, scenario, time factor and current simulated time."""
    keys = ("id", "scenario", "mode", "status", "seed", "time_factor", "sim_time",
            "events_emitted", "events_total")  # fmt: skip
    return [{k: r[k] for k in keys} for r in _get("/runs")]


@server.tool()
def start_run(
    scenario: str = "wholesale",
    seed: int = 0,
    time_factor: float = 1440,
    paused: bool = False,
) -> dict[str, Any]:
    """Start a new run. time_factor = simulated seconds per real second (1440: a day a minute)."""
    result: dict[str, Any] = _post(
        "/runs",
        {"scenario": scenario, "seed": seed, "time_factor": time_factor, "paused": paused},
    )
    return result


@server.tool()
def pause_run(run: str) -> dict[str, Any]:
    """Pause a run; simulated time stops until it is resumed."""
    result: dict[str, Any] = _post(f"/runs/{run}/pause")
    return result


@server.tool()
def resume_run(run: str) -> dict[str, Any]:
    """Resume a paused run."""
    result: dict[str, Any] = _post(f"/runs/{run}/resume")
    return result


@server.tool()
def run_summary(run: str | None = None) -> dict[str, Any]:
    """Key figures of a run: open and delayed shipments, low stock, active disruptions."""
    runs = _get("/runs")
    if not runs:
        raise ValueError("no runs yet; start one with start_run")
    info = next((r for r in runs if r["id"] == run), None) if run else runs[-1]
    if info is None:
        raise ValueError(f"run {run} not found")
    run_id = info["id"]
    open_shipments = _get("/shipments", run=run_id, open="true", limit=1000)
    delayed = [s for s in open_shipments if s["status"] == "delayed"]
    inventory = _get("/inventory", run=run_id, limit=1000)
    disruptions = _get("/disruptions", run=run_id)
    low = _get("/events", run=run_id, type="inventory.below_safety_stock", limit=1000)
    return {
        "run": {k: info[k] for k in ("id", "scenario", "mode", "status", "sim_time")},
        "open_shipments": len(open_shipments),
        "delayed_shipments": len(delayed),
        "delayed_share": round(len(delayed) / len(open_shipments), 3) if open_shipments else 0,
        "inventory": [
            {k: i[k] for k in ("location_id", "item_id", "on_hand", "reserved", "in_transit")}
            for i in inventory
        ],
        "below_safety_stock_alerts": len(low),
        "active_disruptions": [
            {k: d[k] for k in ("id", "type", "severity", "affected", "start")}
            for d in disruptions
            if d["end"] is None
        ],
        "past_disruptions": len([d for d in disruptions if d["end"] is not None]),
    }


@server.tool()
def get_shipments(
    run: str | None = None,
    delayed_only: bool = False,
    open_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Shipments with route and ETAs. By default the open ones (not yet delivered)."""
    rows = _get(
        "/shipments",
        run=run,
        delayed="true" if delayed_only else None,
        open="true" if open_only else None,
        expand="order",
        limit=min(limit, MAX_ROWS),
    )
    result = []
    for s in rows:
        attrs = (s.get("order") or {}).get("attributes", {})
        result.append(
            {
                **{k: s[k] for k in ("id", "order_id", "status", "lane_id", "atd",
                                     "eta_planned", "eta_current", "ata")},
                "origin": attrs.get("origin"),
                "destination": attrs.get("destination"),
                "mode": attrs.get("shipping_mode"),
            }
        )  # fmt: skip
    return result


@server.tool()
def get_inventory(run: str | None = None) -> list[dict[str, Any]]:
    """Current stock per location and item (empty for replayed DataCo runs)."""
    result: list[dict[str, Any]] = _get("/inventory", run=run, limit=1000)
    return result


@server.tool()
def get_events(
    run: str | None = None,
    types: list[str] | None = None,
    after: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Event history, oldest first. Page with `after` (an event ID). Types e.g. shipment.delayed."""
    events = _get("/events", run=run, type=types, after=after, limit=min(limit, MAX_ROWS))
    return {
        "events": events,
        "counts": dict(Counter(e["type"] for e in events)),
        "next_after": events[-1]["id"] if events else after,
    }


@server.tool()
def list_disruption_types() -> list[dict[str, Any]]:
    """Disruption catalog: what each type does and whether it targets lanes or locations."""
    result: list[dict[str, Any]] = _get("/disruption-types")
    return result


@server.tool()
def get_network(run: str | None = None) -> dict[str, Any]:
    """Locations, lanes and items (master data) to choose disruption targets from."""
    del run  # master data is shared by all runs
    return {"locations": _get("/locations"), "lanes": _get("/lanes"), "items": _get("/items")}


@server.tool()
def inject_disruption(
    run: str,
    type: str,
    severity: float,
    duration_days: float,
    lanes: list[str] | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    """Inject a disruption into a running simulation. It starts now (simulated time).

    severity: 0..1. Lane types (port_closure, carrier_strike, extreme_weather) need
    `lanes`; site and demand types need `locations`.
    """
    result: dict[str, Any] = _post(
        f"/runs/{run}/disruptions",
        {"type": type, "severity": severity, "duration_days": duration_days,
         "lanes": lanes or [], "locations": locations or []},
    )  # fmt: skip
    return result


@server.tool()
def builder_options() -> dict[str, Any]:
    """Industries, regions, sizes and disruption profiles for build_scenario."""
    options: dict[str, Any] = _get("/builder/options")
    return {k: options[k] for k in ("industries", "regions", "sizes", "disruption_profiles")}


@server.tool()
def build_scenario(
    industry: str,
    region: str,
    size: str = "medium",
    inbound: bool = True,
    production: bool = True,
    disruptions: str = "medium",
    demand_level: float = 1.0,
    horizon_days: int = 365,
    seed: int = 0,
    name: str | None = None,
) -> dict[str, Any]:
    """Generate and save a scenario from a few choices; then start it with start_run.

    industry/region: see builder_options. size: small, medium or large. disruptions:
    none, low, medium or high. Returns the saved scenario (use its name in start_run).
    """
    body = {"industry": industry, "region": region, "size": size, "inbound": inbound,
            "production": production, "disruptions": disruptions,
            "demand_level": demand_level, "horizon_days": horizon_days, "seed": seed}  # fmt: skip
    if name:
        body["name"] = name
    result: dict[str, Any] = _post("/scenarios", body)
    return result


def main() -> None:
    import sys

    if "--http" in sys.argv:
        port = int(os.environ.get("LIVESIM_MCP_PORT", "8001"))
        server.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        server.run()
