"""Enumerations used by the core schema."""

from enum import StrEnum


class LocationType(StrEnum):
    PLANT = "plant"
    WAREHOUSE = "warehouse"
    PORT = "port"
    CUSTOMER_SITE = "customer_site"
    SUPPLIER_SITE = "supplier_site"


class PartnerRole(StrEnum):
    SUPPLIER = "supplier"
    CUSTOMER = "customer"
    CARRIER = "carrier"


class TransportMode(StrEnum):
    ROAD = "road"
    SEA = "sea"
    AIR = "air"
    RAIL = "rail"


class OrderDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class OrderStatus(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class ShipmentStatus(StrEnum):
    PLANNED = "planned"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    DELAYED = "delayed"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class ObjectType(StrEnum):
    ORDER = "order"
    SHIPMENT = "shipment"
    INVENTORY = "inventory"
    DISRUPTION = "disruption"


class EventType(StrEnum):
    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    SHIPMENT_PICKED_UP = "shipment.picked_up"
    SHIPMENT_DEPARTED = "shipment.departed"
    SHIPMENT_ETA_CHANGED = "shipment.eta_changed"
    SHIPMENT_DELAYED = "shipment.delayed"
    SHIPMENT_DELIVERED = "shipment.delivered"
    INVENTORY_BELOW_SAFETY_STOCK = "inventory.below_safety_stock"
    INVENTORY_CHANGED = "inventory.changed"
    DISRUPTION_STARTED = "disruption.started"
    DISRUPTION_ENDED = "disruption.ended"


class RunMode(StrEnum):
    REPLAY = "replay"
    SIMULATION = "simulation"
    CALIBRATION = "calibration"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"
