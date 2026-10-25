"""Webhooks: subscriptions (REST) and the delivery worker (`python -m api.webhooks`).

A database trigger queues one delivery per matching subscription in the same
transaction as each event. The worker POSTs the event as JSON and retries
failures with exponential backoff (at-least-once delivery; after a retry,
events of one subscription may arrive out of order).

Every request carries:
- `X-SC-LiveSim-Event`: event type
- `X-SC-LiveSim-Delivery`: `<webhook>:<run>:<event>` (use it to deduplicate)
- `X-SC-LiveSim-Timestamp`: Unix time of the attempt
- `X-SC-LiveSim-Signature`: `sha256=<hex>` = HMAC-SHA256(secret, f"{timestamp}.{body}")
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import signal
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, HttpUrl

from schema.enums import EventType

log = logging.getLogger("sc_livesim.webhooks")

MAX_ATTEMPTS = 8
BACKOFF_SECONDS = 2.0  # 2, 4, 8, ... seconds
TIMEOUT_SECONDS = 5.0


# --- signing ---------------------------------------------------------------


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, body: bytes, signature: str, tolerance: int = 300) -> bool:
    """Check a signature (reference implementation for receivers)."""
    if abs(time.time() - int(timestamp)) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, int(timestamp), body), signature)


# --- REST ------------------------------------------------------------------


class WebhookCreate(BaseModel):
    url: HttpUrl
    types: list[EventType] | None = Field(None, description="Event types (default: all)")
    run: str | None = Field(None, description="Only events of this run (default: all runs)")
    secret: str | None = Field(
        None, min_length=16, description="Signing secret (default: generated, returned once)"
    )


class WebhookOut(BaseModel):
    id: str
    url: str
    types: list[str] | None
    run: str | None
    active: bool
    created_at: datetime
    pending: int = 0
    delivered: int = 0
    failed: int = 0


class WebhookCreated(WebhookOut):
    secret: str = Field(description="Store it now; it is not shown again")


def _conn(request: Request) -> Any:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(_conn)]
router = APIRouter(tags=["webhooks"])

_SELECT = (
    "SELECT w.id, w.url, w.types, w.run_id AS run, w.active, w.created_at,"
    " count(*) FILTER (WHERE d.status = 'pending') AS pending,"
    " count(*) FILTER (WHERE d.status = 'delivered') AS delivered,"
    " count(*) FILTER (WHERE d.status = 'failed') AS failed"
    " FROM webhook w LEFT JOIN webhook_delivery d ON d.webhook_id = w.id"
)


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookCreate, conn: Conn) -> WebhookCreated:
    """Subscribe a URL to events. Deliveries start with the next event."""
    secret = body.secret or secrets.token_urlsafe(32)
    with conn.transaction():
        conn.execute("LOCK TABLE webhook IN SHARE ROW EXCLUSIVE MODE")
        row = conn.execute("SELECT count(*) FROM webhook").fetchone()
        webhook_id = f"WHK-{(row[0] if row else 0) + 1:04d}"
        conn.execute(
            "INSERT INTO webhook (id, url, secret, types, run_id) VALUES (%s, %s, %s, %s, %s)",
            (
                webhook_id,
                str(body.url),
                secret,
                [t.value for t in body.types] if body.types else None,
                body.run,
            ),
        )
    return WebhookCreated(**_get(conn, webhook_id), secret=secret)


def _get(conn: psycopg.Connection, webhook_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"{_SELECT} WHERE w.id = %s GROUP BY w.id", (webhook_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"webhook {webhook_id} not found")
    return row


@router.get("/webhooks")
def list_webhooks(conn: Conn) -> list[WebhookOut]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"{_SELECT} GROUP BY w.id ORDER BY w.id")
        return [WebhookOut(**r) for r in cur.fetchall()]


@router.get("/webhooks/{webhook_id}")
def get_webhook(webhook_id: str, conn: Conn) -> WebhookOut:
    return WebhookOut(**_get(conn, webhook_id))


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: str, conn: Conn) -> None:
    """Deactivate a subscription; pending deliveries are dropped."""
    with conn.transaction():
        cur = conn.execute("UPDATE webhook SET active = false WHERE id = %s", (webhook_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, f"webhook {webhook_id} not found")
        conn.execute(
            "UPDATE webhook_delivery SET status = 'failed', last_error = 'webhook deleted'"
            " WHERE webhook_id = %s AND status = 'pending'",
            (webhook_id,),
        )


# --- delivery worker -------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(type(value).__name__)


def deliver_due(conn: psycopg.Connection, client: httpx.Client, limit: int = 20) -> int:
    """Send due deliveries once. Returns the number of attempts made."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT d.webhook_id, d.sim_run_id, d.event_id, d.attempts, w.url, w.secret,"
            " e.ts, e.type, e.object_type, e.object_id, e.payload"
            " FROM webhook_delivery d"
            " JOIN webhook w ON w.id = d.webhook_id"
            " JOIN event e ON e.sim_run_id = d.sim_run_id AND e.id = d.event_id"
            " WHERE d.status = 'pending' AND d.next_attempt_at <= now()"
            " ORDER BY d.created_at, d.sim_run_id, d.event_id"
            " LIMIT %s FOR UPDATE OF d SKIP LOCKED",
            (limit,),
        )
        due = cur.fetchall()
        for d in due:
            event = {
                "sim_run_id": d["sim_run_id"],
                "id": d["event_id"],
                "ts": d["ts"],
                "type": d["type"],
                "object_type": d["object_type"],
                "object_id": d["object_id"],
                "payload": d["payload"],
            }
            body = json.dumps(event, default=_json_default, separators=(",", ":")).encode()
            timestamp = int(time.time())
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "sc-livesim-webhooks",
                "X-SC-LiveSim-Event": d["type"],
                "X-SC-LiveSim-Delivery": f"{d['webhook_id']}:{d['sim_run_id']}:{d['event_id']}",
                "X-SC-LiveSim-Timestamp": str(timestamp),
                "X-SC-LiveSim-Signature": sign(d["secret"], timestamp, body),
            }
            status: int | None = None
            error: str | None = None
            try:
                response = client.post(d["url"], content=body, headers=headers)
                status = response.status_code
                if not 200 <= status < 300:
                    error = f"HTTP {status}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
            attempts = d["attempts"] + 1
            key = (d["webhook_id"], d["sim_run_id"], d["event_id"])
            if error is None:
                conn.execute(
                    "UPDATE webhook_delivery SET status = 'delivered', attempts = %s,"
                    " last_status = %s, last_error = NULL, delivered_at = now()"
                    " WHERE webhook_id = %s AND sim_run_id = %s AND event_id = %s",
                    (attempts, status, *key),
                )
            else:
                final = attempts >= MAX_ATTEMPTS
                retry_at = datetime.now(UTC) + timedelta(
                    seconds=BACKOFF_SECONDS * 2 ** (attempts - 1)
                )
                conn.execute(
                    "UPDATE webhook_delivery SET status = %s, attempts = %s, last_status = %s,"
                    " last_error = %s, next_attempt_at = %s"
                    " WHERE webhook_id = %s AND sim_run_id = %s AND event_id = %s",
                    ("failed" if final else "pending", attempts, status, error, retry_at, *key),
                )
                log.warning("delivery %s attempt %d failed: %s", key, attempts, error)
    return len(due)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    poll = float(os.environ.get("LIVESIM_WEBHOOK_POLL_SECONDS", "0.5"))
    stop = False

    def handle_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    from schema.migrate import migrate

    with (
        psycopg.connect(os.environ["DATABASE_URL"]) as conn,
        httpx.Client(timeout=TIMEOUT_SECONDS) as client,
    ):
        migrate(conn)
        log.info("webhook worker running")
        while not stop:
            if deliver_due(conn, client) == 0:
                time.sleep(poll)
    log.info("webhook worker stopped")


if __name__ == "__main__":
    main()
