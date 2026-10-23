# Scenario: wholesale

A consumer goods wholesaler with one distribution centre (DC) in Hamburg.

```
Shenzhen (CN) ──sea, 35 d──┐                    ┌── Berlin, Cologne (1 d)
                           ├──> Hamburg DC ──road┼── Copenhagen (1.5 d)
Poznan (PL) ──road, 2 d────┘                    └── Munich (2 d), Vienna (3 d)
```

| | |
| --- | --- |
| Items | Headset, kettle (from China), office chair, desk lamp (from Poland) |
| Demand | 9 order streams, about 10 customer orders per day |
| Stock | (s, S) policies at the DC; sea items reviewed weekly, road items daily |
| Horizon | 365 days, about 17,000 events |

## Scheduled disruptions

| Day | Disruption | Effect |
| --- | --- | --- |
| 40–46 | `port_closure` on the Shenzhen–Hamburg lane, severity 0.8 | Sea shipments in transit arrive about 5 days late |
| 75–80 | `demand_spike` at the DC, severity 0.4 | Demand × 2.2 |
| 120–123 | `carrier_strike` on lanes to Munich and Vienna, severity 0.6 | Deliveries about 2 days late |

## Good for testing

- ETA tracking and delay alerts (outbound and inbound)
- Inventory monitoring and below-safety-stock alerts
- Risk views that link a disruption to the shipments it delays (`cause` in the
  delay event payload)

Licensed under CC BY 4.0.
