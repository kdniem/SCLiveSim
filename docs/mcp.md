# MCP server

The MCP server lets AI agents (Claude Desktop, Claude Code and other MCP
clients) watch and steer SC LiveSim: list and start runs, read shipments,
inventory and events, summarise a run, and inject disruptions into simulations.
It only talks to the REST API, never to the database.

## Tools

| Tool | What it does |
| --- | --- |
| `list_scenarios`, `list_runs` | What can run, what is running |
| `start_run`, `pause_run`, `resume_run` | Run control |
| `run_summary` | Open and delayed shipments, stock, alerts, active disruptions |
| `get_shipments`, `get_inventory`, `get_events` | Current state and history (`get_events` pages with `after`) |
| `get_network`, `list_disruption_types` | Locations, lanes, items and the disruption catalog |
| `inject_disruption` | Disrupt a running simulation from now on |

## Connect

The API must be running (`docker compose up -d`). Then:

**Claude Code**

```bash
claude mcp add sc-livesim -- uv run --directory /path/to/SCLiveSim python -m mcp_server
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "sc-livesim": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/SCLiveSim", "python", "-m", "mcp_server"],
      "env": {"LIVESIM_API_URL": "http://localhost:8000"}
    }
  }
}
```

**Without a local Python setup**, run it from the Docker image (stdio):

```bash
docker compose run --rm -i -e LIVESIM_API_URL=http://api:8000 api python -m mcp_server
```

**Streamable HTTP** instead of stdio: `python -m mcp_server --http` serves
`http://localhost:8001/mcp` (port via `LIVESIM_MCP_PORT`).

## Try it

Ask your agent, for example:

- "Start a wholesale simulation and tell me which shipments are late."
- "Close the port on the Shenzhen–Hamburg lane for 10 days and watch what happens
  to headset stock in Hamburg."
- "Summarise the last disruption and the shipments it delayed."
