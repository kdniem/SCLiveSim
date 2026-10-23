"""Engine process: `python -m engine`.

Applies migrations, optionally starts a default run, then ticks forever.

Environment:
- DATABASE_URL (required)
- LIVESIM_TICK_SECONDS (default 0.5)
- LIVESIM_AUTOSTART: scenario to start when no run exists yet (default: none)
- LIVESIM_AUTOSTART_TIME_FACTOR (default 1440), LIVESIM_AUTOSTART_SEED (default 42)
"""

import logging
import os
import signal
import time

import psycopg

from engine.clock import SystemClock
from engine.control import RunOptions, create_run
from engine.replay.runner import ReplayEngine
from schema.migrate import migrate

log = logging.getLogger("sc_livesim.engine")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tick_seconds = float(os.environ.get("LIVESIM_TICK_SECONDS", "0.5"))
    clock = SystemClock()
    engine = ReplayEngine(clock)
    stop = False

    def handle_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applied = migrate(conn)
        log.info("migrations applied: %s", applied or "none")
        autostart = os.environ.get("LIVESIM_AUTOSTART", "").strip()
        if autostart:
            row = conn.execute("SELECT count(*) FROM sim_run").fetchone()
            conn.commit()
            if row and row[0] == 0:
                run_id = create_run(
                    conn,
                    RunOptions(
                        scenario=autostart,
                        seed=int(os.environ.get("LIVESIM_AUTOSTART_SEED", "42")),
                        time_factor=float(os.environ.get("LIVESIM_AUTOSTART_TIME_FACTOR", "1440")),
                    ),
                    clock,
                )
                log.info("autostarted run %s (%s)", run_id, autostart)
        log.info("engine running, tick every %.2fs", tick_seconds)
        while not stop:
            started = time.monotonic()
            engine.tick(conn)
            time.sleep(max(0.0, tick_seconds - (time.monotonic() - started)))
    log.info("engine stopped")


if __name__ == "__main__":
    main()
