# SC LiveSim

**Live supply chain data for apps that need something that moves.**

SC LiveSim is an open-source engine that streams supply chain data (orders,
shipments, inventory, disruptions) through one open schema and one event
stream. Control towers, risk tools, ETA trackers and dashboards plug into it
instead of loading static CSVs.

> **Status: v0.1 (Phase 1, Replay MVP).** Replays the public DataCo Smart Supply
> Chain dataset as a live stream. Simulation, scenarios and calibration follow.

![Mini Control Tower showing live shipments](docs/images/control-tower.png)

## Quickstart (about 5 minutes)

Requirements: [Docker](https://docs.docker.com/get-docker/) with Compose.

```bash
git clone https://github.com/kdniem/SCLiveSim.git
cd SCLiveSim
docker compose up -d
```

On first start the engine downloads the DataCo dataset (96 MB, CC BY 4.0) into
`data/raw/`, loads it (about 15 seconds) and starts a replay at **1440×**: one
simulated day per minute, beginning *now*.

| What | Where |
| --- | --- |
| Mini control tower | <http://localhost:8080> |
| API docs (OpenAPI) | <http://localhost:8000/docs> |
| Live event stream | `curl -N http://localhost:8000/stream` |

Watch the engine with `docker compose logs -f engine`. Stop everything with
`docker compose down` (add `-v` to delete the database).

The first delayed shipments appear after one to four minutes, when their
planned delivery date passes.

## Use the API

```bash
# Runs: list, start another one, pause, resume
curl http://localhost:8000/runs
curl -X POST http://localhost:8000/runs -H 'content-type: application/json' \
     -d '{"seed": 7, "time_factor": 3600, "sample_rate": 0.5}'
curl -X POST http://localhost:8000/runs/RUN-0001/pause
curl -X POST http://localhost:8000/runs/RUN-0001/resume

# Current state (default: the most recent run, or ?run=RUN-0001)
curl 'http://localhost:8000/shipments?open=true'
curl 'http://localhost:8000/shipments?delayed=true&expand=order'
curl 'http://localhost:8000/orders?status=cancelled'

# Only delay events, as Server-Sent Events
curl -N 'http://localhost:8000/stream?types=shipment.delayed'
```

| Endpoint | Purpose |
| --- | --- |
| `GET /runs`, `POST /runs`, `GET /runs/{id}` | List, start and inspect runs |
| `POST /runs/{id}/pause`, `/resume` | Pause and resume (applied within 0.5 s) |
| `GET /shipments` | Shipments; filters `open`, `delayed`, `status`, `expand=order` |
| `GET /orders` | Orders; filter `status` |
| `GET /inventory` | Empty in Phase 1 (DataCo has no inventory data) |
| `GET /events` | Event history; filters `type`, `after` |
| `GET /stream` | SSE stream; filters `run`, `types`; resumes with `Last-Event-ID` |
| `GET /scenarios`, `GET /health` | Available datasets, health check |

### Run options

| Field | Default | Meaning |
| --- | --- | --- |
| `time_factor` | 1440 | Simulated seconds per wall-clock second |
| `seed` | 0 | Seed for sampling; same seed → identical event stream |
| `sample_rate` | 1.0 | Share of orders to replay |
| `dataset_from`, `dataset_to` | whole dataset | Replay a window of the dataset |
| `sim_start` | now | Simulated time that the dataset start maps to |
| `paused` | false | Create the run paused |

## How it works

```
Inputs (public datasets, later: scenarios, your own data)
  → Engine (Python): Replay | Simulation (later) | Calibration (later)
  → PostgreSQL: core schema + append-only event log
  → Access layer (FastAPI): REST, SSE stream
  → Consumers: control tower, risk tool, notebooks
```

- **One schema** ([`schema/`](schema/README.md)): Pydantic models, published as
  JSON Schema, versioned separately (SemVer). Current: v0.1.0.
- **Append-only event log.** Events: `order.created`, `shipment.departed`,
  `shipment.delayed`, `shipment.delivered` (more types are defined in the schema).
  Current state tables are updated in the same transaction as each event.
- **Reproducible.** Same scenario version + seed + options + engine version →
  identical event stream (tested in `tests/test_replay.py`).
- **Local first.** Everything runs with `docker compose up`; no cloud account needed.

How DataCo is mapped, and which assumptions that needs, is documented in
[docs/datasets.md](docs/datasets.md). Design decisions are in [docs/adr/](docs/adr/).

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (or a local PostgreSQL 16).

```bash
uv sync
cp .env.example .env
docker compose up -d db                       # PostgreSQL only
uv run python -m adapters.dataco.fetch        # dataset → data/raw/
export DATABASE_URL=postgresql://livesim:livesim@localhost:5432/livesim
uv run python -m engine                       # terminal 1
uv run uvicorn --factory api.main:create_app  # terminal 2
```

Checks (CI runs the same):

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest          # uses DATABASE_URL, or a temporary local PostgreSQL
```

Tests never use the real dataset; they run on a small synthetic fixture.

## Repository layout

| Folder | Content |
| --- | --- |
| `schema/` | Core schema: Pydantic models, JSON Schema, SQL migrations |
| `engine/` | Replay engine and run control |
| `adapters/` | Importers that map public datasets to the core schema |
| `api/` | FastAPI app: REST and SSE stream |
| `examples/` | [Mini control tower](examples/control-tower/README.md) |
| `docs/` | Dataset notes, `docs/adr/` for architecture decisions |
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
