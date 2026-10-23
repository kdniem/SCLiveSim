# Security Policy

## Supported versions

SC LiveSim is pre-release (0.x). Only the `main` branch receives fixes.

## Scope

SC LiveSim is a local test-data engine. It has **no authentication** and is
not meant to be exposed to the internet. Running it on a public host is at your
own risk.

Webhooks POST event data to any URL that is registered through the API, and
their signing secrets are stored in the database in plain text. Only register
endpoints you control.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/kdniem/SCLiveSim/security/advisories/new)
for this repository. You can expect a first reply within 7 days.
