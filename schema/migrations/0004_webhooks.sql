-- Access layer: webhook subscriptions and their delivery queue
-- (NOT part of the public core schema).

CREATE TABLE webhook (
    id          text PRIMARY KEY,
    url         text NOT NULL,
    secret      text NOT NULL,
    types       text[],           -- NULL = all event types
    run_id      text,             -- NULL = all runs
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_delivery (
    webhook_id       text NOT NULL REFERENCES webhook (id),
    sim_run_id       text NOT NULL,
    event_id         text NOT NULL,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts         integer NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    last_status      integer,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    delivered_at     timestamptz,
    -- No foreign key to event: events are never deleted, and a reference would
    -- make TRUNCATE fail before the append-only trigger can reject it.
    PRIMARY KEY (webhook_id, sim_run_id, event_id)
);
CREATE INDEX webhook_delivery_due_idx ON webhook_delivery (next_attempt_at)
    WHERE status = 'pending';

-- Queue a delivery for every matching subscription in the same transaction
-- as the event, so no event is missed while the worker is down.
CREATE FUNCTION webhook_enqueue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO webhook_delivery (webhook_id, sim_run_id, event_id)
    SELECT w.id, NEW.sim_run_id, NEW.id
    FROM webhook w
    WHERE w.active
      AND (w.types IS NULL OR NEW.type = ANY (w.types))
      AND (w.run_id IS NULL OR w.run_id = NEW.sim_run_id);
    RETURN NULL;
END;
$$;

CREATE TRIGGER event_webhook_enqueue
    AFTER INSERT ON event
    FOR EACH ROW EXECUTE FUNCTION webhook_enqueue();
