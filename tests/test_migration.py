import psycopg
import pytest

from schema.migrate import migrate
from schema.models import ALL_MODELS


def test_tables_match_models(conn: psycopg.Connection) -> None:
    for table, model in ALL_MODELS.items():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        assert {r[0] for r in rows} == set(model.model_fields), table


def test_migrate_is_idempotent(conn: psycopg.Connection) -> None:
    assert migrate(conn) == []


def _insert_run_and_event(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO sim_run (id, mode, scenario, scenario_version, seed, time_factor,"
        " sim_start, status, engine_version, schema_version)"
        " VALUES ('RUN-1', 'replay', 'dataco', '5', 42, 60, now(), 'created', '0', '0.1.0')"
    )
    conn.execute(
        "INSERT INTO event (sim_run_id, id, ts, type, object_type, object_id)"
        " VALUES ('RUN-1', 'EVT-1', now(), 'order.created', 'order', 'ORD-1')"
    )
    conn.commit()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE event SET object_id = 'X'",
        "DELETE FROM event",
        "TRUNCATE event",
    ],
)
def test_event_log_is_append_only(conn: psycopg.Connection, statement: str) -> None:
    _insert_run_and_event(conn)
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute(statement)
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM event").fetchone() == (1,)


def test_insert_notifies_listeners(db_url: str) -> None:
    with psycopg.connect(db_url, autocommit=True) as listener:
        listener.execute("LISTEN sc_events")
        with psycopg.connect(db_url) as writer:
            _insert_run_and_event(writer)
        notifies = list(listener.notifies(timeout=5, stop_after=1))
    assert len(notifies) == 1
    assert '"type" : "order.created"' in notifies[0].payload


def test_migrate_leaves_no_transaction_open(db_url: str) -> None:
    """A long-lived worker must not keep the migration lock (regression test)."""
    with psycopg.connect(db_url) as first, psycopg.connect(db_url) as second:
        migrate(first)
        assert first.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        second.execute("SET lock_timeout = '2s'")
        assert migrate(second) == []
