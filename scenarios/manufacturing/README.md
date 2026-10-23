# Scenario: manufacturing

A pump manufacturer with one plant in Stuttgart, building to stock.

```
Gyor (HU) motors ──road, 2 d────┐                ┌── Lyon (2 d)
Plzen (CZ) housings ──road, 1.5 d┼─> Stuttgart ───┼── Milan (2 d)
Hsinchu (TW) boards ──air, 4 d──┘   plant         └── Rotterdam (2.5 d)
```

| | |
| --- | --- |
| Bill of materials | 1 pump = 1 motor + 1 housing + 2 control boards |
| Production | Up to 40 pumps per day, fills finished goods up to 160 |
| Demand | 3 industrial customers, about 26 pumps per day |
| Horizon | 365 days, about 5,000 events |

## Scheduled disruptions

| Day | Disruption | Effect |
| --- | --- | --- |
| 25–33 | `supplier_outage` at Hsinchu, severity 1.0 | No control boards shipped; board stock runs down |
| 60–64 | `carrier_strike` on lanes to Lyon and Milan, severity 0.5 | Deliveries about 2 days late |
| 150–152 | `plant_shutdown` in Stuttgart, severity 1.0 | No production and no departures |

## Good for testing

- Inbound tracking of components and its effect on production
- Below-safety-stock alerts on components
- Supplier risk: link a supplier outage to later delays

Licensed under CC BY 4.0.
