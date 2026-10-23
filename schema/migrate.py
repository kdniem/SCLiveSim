"""Apply plain SQL migrations from `schema/migrations/` in filename order.

Usage: `python -m schema.migrate` (reads DATABASE_URL).
"""

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply all pending migrations and return the names of those applied."""
    row = conn.execute("SHOW server_encoding").fetchone()
    if row is None or row[0] != "UTF8":
        raise RuntimeError(f"database encoding must be UTF8, not {row[0] if row else '?'}")
    with conn.transaction():
        # Serialize concurrent migrators (e.g. engine and API starting together).
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('sc_livesim_migrate'))")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = conn.execute("SELECT name FROM schema_migration").fetchall()
        done = {row[0] for row in rows}
        applied: list[str] = []
        for path in migration_files():
            if path.name in done:
                continue
            conn.execute(path.read_text().encode())
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applied = migrate(conn)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or '-'}")


if __name__ == "__main__":
    main()
