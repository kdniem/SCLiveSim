-- Core schema v0.1 (see schema/models.py, the source of truth)
-- All timestamps are simulated time in UTC. IDs are stable strings.

-- Master data --------------------------------------------------------------

CREATE TABLE location (
    id          text PRIMARY KEY,
    type        text NOT NULL CHECK (type IN ('plant', 'warehouse', 'port', 'customer_site', 'supplier_site')),
    name        text NOT NULL,
    country     text,
    lat         double precision CHECK (lat BETWEEN -90 AND 90),
    lon         double precision CHECK (lon BETWEEN -180 AND 180),
    attributes  jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE partner (
    id           text PRIMARY KEY,
    role         text NOT NULL CHECK (role IN ('supplier', 'customer', 'carrier')),
    location_id  text REFERENCES location (id),
    attributes   jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE item (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    category    text,
    unit        text NOT NULL DEFAULT 'pcs',
    weight_kg   numeric CHECK (weight_kg >= 0),
    value       numeric CHECK (value >= 0),
    attributes  jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE lane (
    id                       text PRIMARY KEY,
    origin_location_id       text NOT NULL REFERENCES location (id),
    destination_location_id  text NOT NULL REFERENCES location (id),
    mode                     text NOT NULL CHECK (mode IN ('road', 'sea', 'air', 'rail')),
    planned_transit_days     numeric NOT NULL CHECK (planned_transit_days >= 0),
    cost                     numeric CHECK (cost >= 0)
);

-- Run ----------------------------------------------------------------------

CREATE TABLE sim_run (
    id                text PRIMARY KEY,
    mode              text NOT NULL CHECK (mode IN ('replay', 'simulation', 'calibration')),
    scenario          text NOT NULL,
    scenario_version  text NOT NULL,
    seed              bigint NOT NULL,
    time_factor       double precision NOT NULL CHECK (time_factor > 0),
    sim_start         timestamptz NOT NULL,
    wall_start        timestamptz,
    status            text NOT NULL CHECK (status IN ('created', 'running', 'paused', 'finished', 'failed')),
    engine_version    text NOT NULL,
    schema_version    text NOT NULL
);

-- Movement data (current state, per run) -----------------------------------

CREATE TABLE "order" (
    sim_run_id      text NOT NULL REFERENCES sim_run (id),
    id              text NOT NULL,
    direction       text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    partner_id      text NOT NULL REFERENCES partner (id),
    order_date      timestamptz NOT NULL,
    requested_date  timestamptz,
    status          text NOT NULL CHECK (status IN ('created', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (sim_run_id, id)
);

CREATE TABLE order_line (
    sim_run_id  text NOT NULL,
    id          text NOT NULL,
    order_id    text NOT NULL,
    item_id     text NOT NULL REFERENCES item (id),
    quantity    numeric NOT NULL CHECK (quantity > 0),
    value       numeric CHECK (value >= 0),
    PRIMARY KEY (sim_run_id, id),
    FOREIGN KEY (sim_run_id, order_id) REFERENCES "order" (sim_run_id, id)
);

CREATE TABLE shipment (
    sim_run_id   text NOT NULL,
    id           text NOT NULL,
    order_id     text NOT NULL,
    lane_id      text REFERENCES lane (id),
    carrier_id   text REFERENCES partner (id),
    etd          timestamptz,
    eta_planned  timestamptz,
    eta_current  timestamptz,
    atd          timestamptz,
    ata          timestamptz,
    status       text NOT NULL CHECK (status IN ('planned', 'picked_up', 'in_transit', 'delayed', 'delivered', 'cancelled')),
    PRIMARY KEY (sim_run_id, id),
    FOREIGN KEY (sim_run_id, order_id) REFERENCES "order" (sim_run_id, id)
);
CREATE INDEX shipment_status_idx ON shipment (sim_run_id, status);

CREATE TABLE inventory (
    sim_run_id   text NOT NULL REFERENCES sim_run (id),
    location_id  text NOT NULL REFERENCES location (id),
    item_id      text NOT NULL REFERENCES item (id),
    on_hand      numeric NOT NULL,
    reserved     numeric NOT NULL DEFAULT 0,
    in_transit   numeric NOT NULL DEFAULT 0,
    as_of        timestamptz NOT NULL,
    PRIMARY KEY (sim_run_id, location_id, item_id)
);

-- Events -------------------------------------------------------------------

CREATE TABLE event (
    sim_run_id   text NOT NULL REFERENCES sim_run (id),
    id           text NOT NULL,
    ts           timestamptz NOT NULL,
    type         text NOT NULL CHECK (type IN (
                     'order.created', 'order.confirmed',
                     'shipment.picked_up', 'shipment.departed', 'shipment.eta_changed',
                     'shipment.delayed', 'shipment.delivered',
                     'inventory.below_safety_stock',
                     'disruption.started', 'disruption.ended')),
    object_type  text NOT NULL CHECK (object_type IN ('order', 'shipment', 'inventory', 'disruption')),
    object_id    text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (sim_run_id, id)
);
CREATE INDEX event_run_ts_idx ON event (sim_run_id, ts);
CREATE INDEX event_type_idx ON event (sim_run_id, type);

CREATE TABLE disruption (
    sim_run_id  text NOT NULL REFERENCES sim_run (id),
    id          text NOT NULL,
    type        text NOT NULL,
    affected    jsonb NOT NULL DEFAULT '{"locations": [], "lanes": []}'::jsonb,
    start       timestamptz NOT NULL,
    "end"       timestamptz,
    severity    double precision NOT NULL CHECK (severity BETWEEN 0 AND 1),
    PRIMARY KEY (sim_run_id, id)
);

-- The event log is append-only: block UPDATE, DELETE and TRUNCATE.
CREATE FUNCTION event_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'event is append-only (% not allowed)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER event_no_update_delete
    BEFORE UPDATE OR DELETE ON event
    FOR EACH ROW EXECUTE FUNCTION event_append_only();

CREATE TRIGGER event_no_truncate
    BEFORE TRUNCATE ON event
    FOR EACH STATEMENT EXECUTE FUNCTION event_append_only();

-- Announce new events to listeners (payload stays well below the 8000 byte limit).
CREATE FUNCTION event_notify() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify(
        'sc_events',
        json_build_object('sim_run_id', NEW.sim_run_id, 'id', NEW.id, 'type', NEW.type)::text
    );
    RETURN NULL;
END;
$$;

CREATE TRIGGER event_notify_insert
    AFTER INSERT ON event
    FOR EACH ROW EXECUTE FUNCTION event_notify();
