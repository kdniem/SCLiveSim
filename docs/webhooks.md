# Webhooks

Webhooks push events to your app instead of you polling or holding an SSE
connection. The `webhooks` service in Docker Compose delivers them.

## Subscribe

```bash
curl -X POST http://localhost:8000/webhooks -H 'content-type: application/json' \
     -d '{"url": "http://host.docker.internal:9000/hook",
          "types": ["shipment.delayed", "inventory.below_safety_stock"]}'
```

The response contains a generated `secret`. It is shown only once. Optional
fields: `types` (default: all events), `run` (default: all runs), `secret`
(bring your own, at least 16 characters).

`GET /webhooks` lists subscriptions with delivery counts; `DELETE /webhooks/{id}`
deactivates one.

From inside Docker Compose, `localhost` is the container itself; use
`host.docker.internal` (Docker Desktop) or your machine's IP to reach an app on
your host.

## Delivery

Each event is POSTed as JSON (the same object as in `GET /events` and the SSE
stream) with these headers:

| Header | Content |
| --- | --- |
| `X-SC-LiveSim-Event` | Event type, e.g. `shipment.delayed` |
| `X-SC-LiveSim-Delivery` | `<webhook>:<run>:<event>`, unique per delivery: use it to ignore duplicates |
| `X-SC-LiveSim-Timestamp` | Unix time of the attempt |
| `X-SC-LiveSim-Signature` | `sha256=` + HMAC-SHA256(secret, `"{timestamp}.{body}"`) |

Any 2xx response counts as delivered. Otherwise the worker retries after 2, 4,
8, … seconds, up to 8 attempts, then marks the delivery as failed.

- **At least once:** your endpoint may see an event twice; deduplicate by the
  delivery header.
- **Order:** deliveries go out in event order, but a retried event can arrive
  after later ones.
- **Nothing is lost while the worker is down:** deliveries are queued by a
  database trigger in the same transaction as the event.

## Verify the signature

```python
import hashlib, hmac, time

def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:  # reject replays older than 5 minutes
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

The same function is available as `api.webhooks.verify`.
