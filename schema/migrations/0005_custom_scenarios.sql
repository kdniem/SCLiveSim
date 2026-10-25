-- Custom scenarios created with the scenario builder or uploaded as YAML
-- (NOT part of the public core schema). Runs reference scenarios by name.

CREATE TABLE scenario (
    name        text PRIMARY KEY,
    version     text NOT NULL,
    origin      text NOT NULL CHECK (origin IN ('builder', 'upload', 'calibration')),
    spec        jsonb NOT NULL,
    request     jsonb,            -- builder request or calibration settings
    created_at  timestamptz NOT NULL DEFAULT now()
);
