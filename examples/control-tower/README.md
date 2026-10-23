# Mini Control Tower

A minimal consumer of SC LiveSim: one static HTML page with plain JavaScript,
no build step, no framework. It shows open and delayed shipments live.

![Mini Control Tower](../../docs/images/control-tower.png)

## Run it

With Docker Compose it runs at <http://localhost:8080>. Without Docker, serve
the folder with any static web server while the API runs on port 8000:

```bash
python -m http.server 8080 --directory examples/control-tower
```

Point it at another API with `?api=http://host:port`.

## How it works

1. `GET /runs` picks the most recent active run.
2. It subscribes to `GET /stream?run=…&types=shipment.departed,shipment.delayed,…`
   (Server-Sent Events) **before** loading data, so no event is missed.
3. `GET /shipments?open=true&expand=order` loads the current open shipments.
4. Each event updates an in-memory map:
   - `shipment.departed` adds a shipment,
   - `shipment.delayed` marks it delayed and sets the new ETA,
   - `shipment.delivered` removes it.
5. The pause/resume button calls `POST /runs/{id}/pause|resume`.

Everything goes through the access layer; the page never talks to the database.
That is the pattern any consumer (risk tool, notebook, dashboard) can follow.
