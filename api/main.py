"""SC LiveSim access layer: REST endpoints and the SSE event stream.

Run with `uvicorn --factory api.main:create_app` (reads DATABASE_URL).
OpenAPI docs at /docs, the spec at /openapi.json.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.models import (
    DisruptionAccepted,
    DisruptionCreate,
    DisruptionTypeOut,
    RunCreate,
    RunOut,
    ScenarioOut,
    ShipmentOut,
)
from api.settings import Settings
from api.stream import event_stream
from api.webhooks import router as webhooks_router
from engine import ENGINE_VERSION
from engine.clock import SystemClock, sim_now
from engine.control import (
    RunNotFound,
    RunOptions,
    RunStateError,
    create_run,
    get_runs,
    inject_disruption,
    set_desired_status,
)
from engine.scenarios import SCENARIOS
from engine.simulation.catalog import CATALOG
from schema import SCHEMA_VERSION
from schema.enums import EventType, OrderStatus, ShipmentStatus
from schema.migrate import migrate
from schema.models import Disruption, Event, Inventory, Item, Lane, Location, Order

INVENTORY_NOTE = "DataCo has no inventory data; inventory stays empty in Phase 1."
MAX_LIMIT = 1000


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with psycopg.connect(settings.database_url) as conn:
            migrate(conn)
        pool = ConnectionPool(settings.database_url, min_size=1, max_size=10, open=True)
        app.state.pool = pool
        app.state.settings = settings
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="SC LiveSim API",
        version=ENGINE_VERSION,
        description=(
            "Live supply chain data through one open schema and one event stream. "
            f"Core schema v{SCHEMA_VERSION}. All timestamps are simulated time (UTC)."
        ),
        license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-SC-LiveSim-Run", "X-SC-LiveSim-Note"],
    )
    _routes(app)
    app.include_router(webhooks_router)
    return app


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
RunParam = Annotated[
    str | None, Query(alias="run", description="Run ID (default: the most recent run)")
]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]


def _run_out(row: dict[str, Any]) -> RunOut:
    now = datetime.now(UTC)
    current = sim_now(row["sim_position"], row["wall_anchor"], row["time_factor"], now)
    if row["status"] in ("created",):
        current = row["sim_start"]
    return RunOut(**row, sim_time=current)


def _resolve_run(conn: psycopg.Connection, run: str | None, response: Response) -> str:
    if run:
        found = conn.execute("SELECT id FROM sim_run WHERE id = %s", (run,)).fetchone()
    else:
        found = conn.execute("SELECT id FROM sim_run ORDER BY id DESC LIMIT 1").fetchone()
    if not found:
        raise HTTPException(404, f"run {run} not found" if run else "no runs yet")
    run_id: str = found[0]
    response.headers["X-SC-LiveSim-Run"] = run_id
    return run_id


def _routes(app: FastAPI) -> None:
    @app.get("/health", tags=["meta"])
    def health(conn: Conn) -> dict[str, str]:
        conn.execute("SELECT 1")
        return {"status": "ok", "engine_version": ENGINE_VERSION, "schema_version": SCHEMA_VERSION}

    @app.get("/scenarios", tags=["runs"])
    def scenarios() -> list[ScenarioOut]:
        return [
            ScenarioOut(
                name=s.name, version=s.version, mode=s.mode.value, description=s.description
            )
            for s in SCENARIOS.values()
        ]

    @app.get("/disruption-types", tags=["disruptions"])
    def disruption_types() -> list[DisruptionTypeOut]:
        return [
            DisruptionTypeOut(name=t.name, effect=t.effect, targets=t.targets,
                              description=t.description)
            for t in CATALOG.values()
        ]  # fmt: skip

    @app.get("/runs", tags=["runs"])
    def list_runs(conn: Conn) -> list[RunOut]:
        return [_run_out(r) for r in get_runs(conn)]

    @app.post("/runs", status_code=201, tags=["runs"])
    def post_run(body: RunCreate, conn: Conn) -> RunOut:
        try:
            run_id = create_run(conn, RunOptions(**body.model_dump()), SystemClock())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _run_out(get_runs(conn, run_id)[0])

    @app.get("/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str, conn: Conn) -> RunOut:
        rows = get_runs(conn, run_id)
        if not rows:
            raise HTTPException(404, f"run {run_id} not found")
        return _run_out(rows[0])

    def _control(run_id: str, desired: str, conn: psycopg.Connection) -> RunOut:
        rows = get_runs(conn, run_id)
        if not rows:
            raise HTTPException(404, f"run {run_id} not found")
        if rows[0]["status"] in ("finished", "failed"):
            raise HTTPException(409, f"run {run_id} is {rows[0]['status']}")
        set_desired_status(conn, run_id, desired)
        return _run_out(get_runs(conn, run_id)[0])

    @app.post("/runs/{run_id}/pause", status_code=202, tags=["runs"])
    def pause_run(run_id: str, conn: Conn) -> RunOut:
        """Request a pause; the engine applies it within one tick."""
        return _control(run_id, "paused", conn)

    @app.post("/runs/{run_id}/resume", status_code=202, tags=["runs"])
    def resume_run(run_id: str, conn: Conn) -> RunOut:
        """Request a resume; the engine applies it within one tick."""
        return _control(run_id, "running", conn)

    @app.get("/shipments", tags=["data"])
    def shipments(
        conn: Conn,
        response: Response,
        run: RunParam = None,
        status: Annotated[list[ShipmentStatus] | None, Query()] = None,
        open: Annotated[bool, Query(description="Only not yet delivered/cancelled")] = False,
        delayed: Annotated[bool, Query(description="Only delayed shipments")] = False,
        expand: Annotated[str | None, Query(pattern="^order$")] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> list[ShipmentOut]:
        run_id = _resolve_run(conn, run, response)
        where: list[str] = ["s.sim_run_id = %s"]
        params: list[Any] = [run_id]
        if status:
            where.append("s.status = ANY(%s)")
            params.append([s.value for s in status])
        if open:
            where.append("s.status NOT IN ('delivered', 'cancelled')")
        if delayed:
            where.append("s.status = 'delayed'")
        order_cols = ", to_jsonb(o) AS order_json" if expand else ""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT s.*{order_cols} FROM shipment s"
                ' JOIN "order" o ON o.sim_run_id = s.sim_run_id AND o.id = s.order_id'
                f" WHERE {' AND '.join(where)} ORDER BY s.id LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
            rows = cur.fetchall()
        result = []
        for row in rows:
            order = row.pop("order_json", None)
            result.append(ShipmentOut(**row, order=Order.model_validate(order) if order else None))
        return result

    @app.get("/orders", tags=["data"])
    def orders(
        conn: Conn,
        response: Response,
        run: RunParam = None,
        status: Annotated[list[OrderStatus] | None, Query()] = None,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> list[Order]:
        run_id = _resolve_run(conn, run, response)
        where: list[str] = ["sim_run_id = %s"]
        params: list[Any] = [run_id]
        if status:
            where.append("status = ANY(%s)")
            params.append([s.value for s in status])
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f'SELECT * FROM "order" WHERE {" AND ".join(where)} ORDER BY id LIMIT %s OFFSET %s',
                [*params, limit, offset],
            )
            return [Order(**r) for r in cur.fetchall()]

    @app.get("/inventory", tags=["data"])
    def inventory(
        conn: Conn, response: Response, run: RunParam = None, limit: Limit = 100,
        offset: Offset = 0,
    ) -> list[Inventory]:  # fmt: skip
        run_id = _resolve_run(conn, run, response)
        response.headers["X-SC-LiveSim-Note"] = INVENTORY_NOTE
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM inventory WHERE sim_run_id = %s"
                " ORDER BY location_id, item_id LIMIT %s OFFSET %s",
                (run_id, limit, offset),
            )
            return [Inventory(**r) for r in cur.fetchall()]

    @app.get("/disruptions", tags=["disruptions"])
    def disruptions(conn: Conn, response: Response, run: RunParam = None) -> list[Disruption]:
        """Disruptions that have started in a run (ongoing ones have no `end`)."""
        run_id = _resolve_run(conn, run, response)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM disruption WHERE sim_run_id = %s ORDER BY start, id", (run_id,)
            )
            return [Disruption(**r) for r in cur.fetchall()]

    @app.post("/runs/{run_id}/disruptions", status_code=202, tags=["disruptions"])
    def post_disruption(run_id: str, body: DisruptionCreate, conn: Conn) -> DisruptionAccepted:
        """Inject a disruption into a running simulation.

        The engine applies it within one tick and re-plans the run from the
        current simulated time; nothing that already happened changes.
        """
        try:
            disruption_id = inject_disruption(conn, run_id, **body.model_dump())
        except RunNotFound as exc:
            raise HTTPException(404, f"run {run_id} not found") from exc
        except RunStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return DisruptionAccepted(id=disruption_id, run_id=run_id)

    @app.get("/locations", tags=["master data"])
    def locations(conn: Conn) -> list[Location]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM location ORDER BY id")
            return [Location(**r) for r in cur.fetchall()]

    @app.get("/items", tags=["master data"])
    def items(conn: Conn) -> list[Item]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM item ORDER BY id")
            return [Item(**r) for r in cur.fetchall()]

    @app.get("/lanes", tags=["master data"])
    def lanes(conn: Conn) -> list[Lane]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM lane ORDER BY id")
            return [Lane(**r) for r in cur.fetchall()]

    @app.get("/events", tags=["data"])
    def events(
        conn: Conn,
        response: Response,
        run: RunParam = None,
        types: Annotated[list[EventType] | None, Query(alias="type")] = None,
        after: Annotated[str | None, Query(description="Return events after this ID")] = None,
        limit: Limit = 100,
    ) -> list[Event]:
        """Event history of a run, oldest first (use `after` to page)."""
        run_id = _resolve_run(conn, run, response)
        where: list[str] = ["sim_run_id = %s"]
        params: list[Any] = [run_id]
        if types:
            where.append("type = ANY(%s)")
            params.append([t.value for t in types])
        if after:
            where.append("id > %s")
            params.append(after)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM event WHERE {' AND '.join(where)} ORDER BY id LIMIT %s",
                [*params, limit],
            )
            return [Event(**r) for r in cur.fetchall()]

    @app.get(
        "/stream",
        tags=["stream"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    def stream(
        request: Request,
        run: Annotated[str | None, Query(description="Only events of this run")] = None,
        types: Annotated[
            str | None, Query(description="Comma-separated event types, e.g. shipment.delayed")
        ] = None,
    ) -> StreamingResponse:
        """Server-Sent Events: one `event:` per core event, `data:` is the event as JSON.

        Reconnecting clients send `Last-Event-ID` and receive what they missed
        (when `run` is set). Keep-alive comments are sent every 15 seconds.
        """
        wanted: set[str] | None = None
        if types:
            wanted = {t.strip() for t in types.split(",") if t.strip()}
            unknown = wanted - {t.value for t in EventType}
            if unknown:
                raise HTTPException(422, f"unknown event types: {sorted(unknown)}")
        settings: Settings = request.app.state.settings
        return StreamingResponse(
            event_stream(
                request,
                settings,
                run=run,
                types=wanted,
                last_event_id=request.headers.get("last-event-id"),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
