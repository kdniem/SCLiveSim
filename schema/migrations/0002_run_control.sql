-- Engine-internal run control (NOT part of the public core schema).
-- The API writes the desired status; the engine applies it and keeps the clock.

CREATE TABLE run_control (
    sim_run_id      text PRIMARY KEY REFERENCES sim_run (id),
    options         jsonb NOT NULL DEFAULT '{}'::jsonb,
    desired_status  text NOT NULL CHECK (desired_status IN ('running', 'paused')),
    -- Simulated time at wall_anchor; while paused, the frozen simulated time.
    sim_position    timestamptz NOT NULL,
    -- Wall-clock time when the run was last started or resumed; NULL while paused.
    wall_anchor     timestamptz,
    -- Number of planned events already written.
    cursor          integer NOT NULL DEFAULT 0,
    events_total    integer,
    error           text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
