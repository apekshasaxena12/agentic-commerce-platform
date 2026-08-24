-- audit_log_entry.order_id was NOT NULL, but the "non-negotiable" rule that
-- every pipeline node logs an audit entry on every run collides with that:
-- the intent and retrieve nodes run before any order exists (e.g. a pure
-- browsing query never creates an order at all). Relaxing to nullable so
-- pre-order steps can still log, with order_id filled in once an order is
-- created (inside retrieve, for checkout intents).

ALTER TABLE audit_log_entry ALTER COLUMN order_id DROP NOT NULL;
