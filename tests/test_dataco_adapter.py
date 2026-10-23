"""Tests for the DataCo adapter against a small synthetic fixture (no real data)."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adapters.dataco.mapping import DataCoLoader, parse, read_rows
from engine.replay.plan import SourceOptions
from schema.enums import EventType

FIXTURE = Path(__file__).parent / "fixtures" / "dataco_sample.csv"


@pytest.fixture(scope="module")
def loader() -> DataCoLoader:
    return DataCoLoader(FIXTURE)


def by_object(events: list, object_id: str) -> list[EventType]:  # type: ignore[type-arg]
    return [e.type for e in events if e.object_id == object_id]


def test_parse_groups_lines_into_orders() -> None:
    parsed = parse(read_rows(FIXTURE))
    assert len(parsed.orders) == 6
    assert len(parsed.orders["101"].lines) == 2
    assert set(parsed.items) == {"ITM-0001", "ITM-0002", "ITM-0003"}
    assert set(parsed.partners) == {"CUS-09001", "CUS-09002", "CUS-09003", "CUS-09004"}


def test_event_sequences(loader: DataCoLoader) -> None:
    events = list(loader(SourceOptions()).events)
    created, departed = EventType.ORDER_CREATED, EventType.SHIPMENT_DEPARTED
    delayed, delivered = EventType.SHIPMENT_DELAYED, EventType.SHIPMENT_DELIVERED
    # advance shipping: no delay
    assert by_object(events, "ORD-000101") == [created]
    assert by_object(events, "SHP-000101") == [departed, delivered]
    # late: delayed at the planned ETA, then delivered
    assert by_object(events, "SHP-000102") == [departed, delayed, delivered]
    # cancelled: order only, no shipment
    assert by_object(events, "ORD-000104") == [created]
    assert by_object(events, "SHP-000104") == []
    assert Counter(e.type for e in events) == {created: 6, departed: 5, delayed: 2, delivered: 5}


def test_timing_of_late_order(loader: DataCoLoader) -> None:
    events = [e for e in loader(SourceOptions()).events if e.object_id == "SHP-000102"]
    t0 = datetime(2015, 1, 1, 1, 0, tzinfo=UTC)
    assert [e.at for e in events] == [t0, t0 + timedelta(days=1), t0 + timedelta(days=2)]
    assert events[1].payload["delay_days"] == 1
    assert events[1].payload["eta_current"] == t0 + timedelta(days=2)


def test_events_are_sorted_and_orders_precede_shipments(loader: DataCoLoader) -> None:
    events = list(loader(SourceOptions()).events)
    assert events == sorted(events, key=lambda e: e.sort_key())
    seen_orders: set[str] = set()
    for event in events:
        if event.type is EventType.ORDER_CREATED:
            seen_orders.add(event.object_id)
        else:
            assert event.payload["order_id"] in seen_orders


def test_no_personal_data_is_imported(loader: DataCoLoader) -> None:
    source = loader(SourceOptions())
    text = repr(source)
    for secret in ("Fixturefirst", "Fixturelast", "Fixture Street", "XXXXXXXXX", "18.25"):
        assert secret not in text


def test_sampling_is_seeded(loader: DataCoLoader) -> None:
    def orders(seed: int) -> list[str]:
        opts = SourceOptions(seed=seed, sample_rate=0.5)
        return [e.object_id for e in loader(opts).events if e.type is EventType.ORDER_CREATED]

    assert orders(7) == orders(7)
    assert {tuple(orders(s)) for s in range(10)} != {tuple(orders(7))}


def test_window_and_origin(loader: DataCoLoader) -> None:
    start = datetime(2015, 1, 2, tzinfo=UTC)
    source = loader(SourceOptions(dataset_from=start))
    assert source.origin == start
    created = [e.object_id for e in source.events if e.type is EventType.ORDER_CREATED]
    assert created == ["ORD-000105", "ORD-000106"]
    assert loader(SourceOptions()).origin == datetime(2015, 1, 1, tzinfo=UTC)
