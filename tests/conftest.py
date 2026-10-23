"""Shared fixtures.

Database tests need PostgreSQL 16. They use, in this order:
1. DATABASE_URL (e.g. `docker compose up -d` + `.env`), with a throwaway database per test, or
2. a temporary cluster started with the local `initdb`/`pg_ctl` binaries.
If neither is available, database tests are skipped.
"""

import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import conninfo

from schema.migrate import migrate


def _find_pg_bin() -> Path | None:
    if shutil.which("pg_ctl"):
        return Path(shutil.which("pg_ctl") or "").parent
    for candidate in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (candidate / "pg_ctl").exists():
            return candidate
    return None


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    url = os.environ.get("DATABASE_URL")
    if url:
        yield url
        return
    pg_bin = _find_pg_bin()
    if pg_bin is None:
        pytest.skip("no DATABASE_URL and no local PostgreSQL binaries")
    tmp = Path(tempfile.mkdtemp(prefix="sc-livesim-pg-"))
    data, port = tmp / "data", 54329
    # initdb refuses to run as root; fall back to an unprivileged account.
    prefix = ["runuser", "-u", "nobody", "--"] if os.geteuid() == 0 else []
    if prefix:
        shutil.chown(tmp, "nobody")

    def pg(tool: str, *args: str, check: bool = True) -> None:
        subprocess.run([*prefix, str(pg_bin / tool), *args], check=check, capture_output=True)

    pg("initdb", "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8")
    pg("pg_ctl", "-D", str(data), "-w", "-l", str(tmp / "log"),
       "-o", f"-p {port} -k {tmp} -c listen_addresses=''", "start")  # fmt: skip
    try:
        yield f"postgresql://postgres@/postgres?host={tmp}&port={port}"
    finally:
        pg("pg_ctl", "-D", str(data), "-m", "fast", "stop", check=False)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def db_url(admin_url: str) -> Iterator[str]:
    """A fresh, migrated database for one test."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = conninfo.make_conninfo(admin_url, dbname=name)
    with psycopg.connect(url) as conn:
        migrate(conn)
    try:
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(db_url) as connection:
        yield connection
