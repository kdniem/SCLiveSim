# SC LiveSim

**Live supply chain data for apps that need something that moves.**

SC LiveSim is an open-source engine that streams supply chain data (orders,
shipments, inventory, disruptions) through one open schema and one event
stream. Control towers, risk tools, ETA trackers and dashboards plug into it
instead of loading static CSVs.

Three ways to get data, one stream for all of them:

| | |
| --- | --- |
| **Click together your own supply chain** | Pick industry, region, size, flows (inbound, production, outbound) and a disruption profile in the scenario builder; SC LiveSim generates the network and simulates it. |
| **Replay real data** | The public DataCo Smart Supply Chain dataset, played back as if it were happening now. |
| **Continue real data** | Replay the end of a dataset, then a simulation calibrated on it carries on seamlessly. |

> **Status: v0.2.0 (Phase 2, Simulation).** See the [CHANGELOG](CHANGELOG.md).

![Mini Control Tower showing live shipments, inventory and disruptions](docs/images/control-tower.png)

## Quickstart (about 5 minutes)

Requirements: [Docker](https://docs.docker.com/get-docker/) with Compose.

```bash
git clone https://github.com/kdniem/SCLiveSim.git
cd SCLiveSim
docker compose up -d
```

The engine starts the `wholesale` simulation at **1440×** (one simulated day per
minute, beginning *now*).

| What | Where |
| --- | --- |
| Mini control tower | <http://localhost:8080> |
| Scenario builder | <http://localhost:8080/builder.html> |
| API docs (OpenAPI) | <http://localhost:8000/docs> |
| Live event stream | `curl -N http://localhost:8000/stream` |

Watch the engine with `docker compose logs -f engine`. Stop everything with
`docker compose down` (add `-v` to delete the database).

![Scenario builder](docs/images/scenario-builder.png)

## Build your own scenario

In the [scenario builder](http://localhost:8080/builder.html), or via the API:

```bash
curl -X POST http://localhost:8000/scenarios -H 'content-type: application/json' \
     -d '{"name": "my-pharma", "industry": "pharma", "region": "north_america",
          "size": "medium", "disruptions": "high"}'
curl -X POST http://localhost:8000/runs -H 'content-type: application/json' \
     -d '{"scenario": "my-pharma", "time_factor": 1440}'
```

| Choice | Options |
| --- | --- |
| Industry | consumer electronics, fashion retail, food & beverage, pharma, automotive parts (production), industrial machinery (production) |
| Region | Europe, North America, Asia-Pacific, Latin America (real cities; overseas sourcing hubs) |
| Size | small (1 site, 4 customers), medium (2, 8), large (3, 14) |
| Flows | outbound always; inbound replenishment and production can be switched off |
| Disruptions | none, low (~2/year), medium (~5), high (~10) from the [catalog](scenarios/README.md#disruption-catalog) |
| Fine-tuning | demand level, horizon, layout seed, name |

Every scenario can be downloaded as YAML (`GET /scenarios/{name}?format=yaml`),
edited and uploaded again (`POST /scenarios/upload`). The format is documented in
[scenarios/README.md](scenarios/README.md); two hand-written templates,
[`wholesale`](scenarios/wholesale/README.md) and
[`manufacturing`](scenarios/manufacturing/README.md), are included.

## Replay or continue a dataset

```bash
# DataCo replayed from its start (2015) as if it happened now
curl -X POST http://localhost:8000/runs -d '{"scenario": "dataco"}' -H 'content-type: application/json'
# The last 30 days of DataCo, then a calibrated simulation continues it
curl -X POST http://localhost:8000/runs -d '{"scenario": "dataco-continued"}' -H 'content-type: application/json'
# Save the calibrated scenario to run or edit it on its own
curl -X POST http://localhost:8000/scenarios/calibrate -d '{"window_days": 90}' -H 'content-type: application/json'
```

The dataset (96 MB, CC BY 4.0) is downloaded on first use. How it is mapped and
calibrated, and which assumptions that needs: [docs/datasets.md](docs/datasets.md).

## Use the data

| Endpoint | Purpose |
| --- | --- |
| `GET /runs`, `POST /runs`, `GET /runs/{id}` | List, start and inspect runs |
| `POST /runs/{id}/pause`, `/resume` | Pause and resume (applied within 0.5 s) |
| `POST /runs/{id}/disruptions` | Inject a disruption into a running simulation |
| `GET /shipments` | Filters `open`, `delayed`, `status`, `expand=order` |
| `GET /orders`, `GET /inventory`, `GET /disruptions` | Current state of a run |
| `GET /events` | Event history; filters `type`, `after` |
| `GET /stream` | Server-Sent Events; filters `run`, `types`; resumes with `Last-Event-ID` |
| `GET /scenarios`, `POST /scenarios`, `/scenarios/upload`, `/scenarios/calibrate` | Scenarios |
| `GET /builder/options`, `POST /builder/preview` | Scenario builder |
| `GET /locations`, `/items`, `/lanes`, `/disruption-types` | Master data and catalog |
| `POST /webhooks`, `GET /webhooks`, `DELETE /webhooks/{id}` | [Webhooks](docs/webhooks.md) |

Run options for `POST /runs`: `scenario`, `seed` (same seed → identical event
stream), `time_factor` (default 1440), `sample_rate` and `dataset_from`/`dataset_to`
(replays), `sim_start` (default: now), `paused`.

**Agents:** an [MCP server](docs/mcp.md) gives Claude and other MCP clients tools
to build scenarios, start runs, read shipments and inject disruptions.

## How it works

```
Inputs: datasets, scenario YAML, scenario builder
  → Engine: Replay | Simulation (SimPy) | Calibration (replay → simulation)
  → PostgreSQL: core schema + append-only event log
  → Access layer: REST, SSE stream, webhooks, MCP server
  → Consumers: control tower, risk tool, notebooks, agents
```

- **One schema** ([`schema/`](schema/README.md)), versioned separately (v0.2.0) and
  published as JSON Schema. Consumers cannot tell replayed, simulated and
  calibrated runs apart.
- **Append-only event log**: `order.created`, `order.confirmed`, `shipment.departed`,
  `shipment.delayed`, `shipment.eta_changed`, `shipment.delivered`,
  `inventory.changed`, `inventory.below_safety_stock`, `disruption.started`,
  `disruption.ended`. Current state tables change in the same transaction.
- **Reproducible**: same scenario version + seed + options + engine version →
  identical event stream; an injected disruption changes only what comes after it.
- **Local first**: everything runs with `docker compose up`.

Design decisions: [docs/adr/](docs/adr/). Acceptance results:
[Phase 1](docs/phase1-acceptance.md), [Phase 2](docs/phase2-acceptance.md).

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (or a local PostgreSQL 16).

```bash
uv sync
cp .env.example .env
docker compose up -d db
export DATABASE_URL=postgresql://livesim:livesim@localhost:5432/livesim
uv run python -m engine                       # terminal 1
uv run uvicorn --factory api.main:create_app  # terminal 2
uv run python -m api.webhooks                 # terminal 3 (optional)
```

Checks (CI runs the same):

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest          # uses DATABASE_URL, or a temporary local PostgreSQL
```

Tests never use the real dataset, except `tests/test_soak_dataco.py`, which runs
only when the dataset is in `data/raw/`.

## Repository layout

| Folder | Content |
| --- | --- |
| `schema/` | Core schema: Pydantic models, JSON Schema, SQL migrations |
| `engine/` | Replay engine, simulation (`engine/simulation/`), builder, continuation, run control |
| `scenarios/` | Scenario templates and the scenario format |
| `adapters/` | Dataset importers and calibration |
| `api/` | FastAPI app: REST, SSE stream, webhooks, scenarios |
| `mcp_server/` | MCP server (named so it does not shadow the `mcp` SDK) |
| `examples/` | [Mini control tower and scenario builder](examples/control-tower/README.md) |
| `docs/` | Datasets, webhooks, MCP, acceptance results, `docs/adr/` |
| `data/` | Gitignored; raw downloads go to `data/raw/` |

## Licensing

| Part | License |
| --- | --- |
| Code | [Apache-2.0](LICENSE) |
| Core schema, scenario files | CC BY 4.0 |
| Data generated by simulation runs | CC0 1.0 |
| Third-party datasets | Their original license; not redistributed here |

The DataCo dataset is by Constante, Silva and Pereira (2019), Mendeley Data,
<https://doi.org/10.17632/8gx2fvg2k6.5>, CC BY 4.0. See [NOTICE](NOTICE) and
[ADR 0002](docs/adr/0002-licensing.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues: see [SECURITY.md](SECURITY.md).
