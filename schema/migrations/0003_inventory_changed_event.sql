-- Core schema 0.2.0: new event type inventory.changed (initial stock, production).
ALTER TABLE event DROP CONSTRAINT event_type_check;
ALTER TABLE event ADD CONSTRAINT event_type_check CHECK (type IN (
    'order.created', 'order.confirmed',
    'shipment.picked_up', 'shipment.departed', 'shipment.eta_changed',
    'shipment.delayed', 'shipment.delivered',
    'inventory.below_safety_stock', 'inventory.changed',
    'disruption.started', 'disruption.ended'));
