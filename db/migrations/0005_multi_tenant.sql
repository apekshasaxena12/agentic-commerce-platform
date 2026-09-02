-- Phase 1 of multi-tenant architecture: a merchant table, and merchant_id
-- on product/merchant_policy/orders. All EXISTING data (the original
-- ~40-product catalog, its co_purchase_stat pairs — unaffected, both
-- products in every existing pair already belong to the same catalog —
-- the singleton merchant_policy row, and every order placed so far)
-- belongs to one merchant, backfilled here as "Shopfront Running Co." —
-- nothing existing changes id or gets hand-edited; this file is the one
-- and only place that transformation happens. A second merchant's
-- products/policy are added separately by db/seed_merchant_b.py (plain
-- INSERTs, not a migration, since they're new data rather than a schema
-- change).
--
-- agent stays global/unscoped on purpose: a shopper or AI buyer can
-- purchase from any merchant, so agent has no merchant_id. Policy is
-- resolved per-order instead, from whichever merchant owns the product(s)
-- being bought (see pipeline/graph.py's policy_check/authorization nodes
-- and db/policy.py's get_merchant_policy_for_order).

CREATE TABLE merchant (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO merchant (name, slug) VALUES ('Shopfront Running Co.', 'shopfront-running-co');

ALTER TABLE product ADD COLUMN merchant_id BIGINT REFERENCES merchant(id);
UPDATE product SET merchant_id = (SELECT id FROM merchant WHERE slug = 'shopfront-running-co');
ALTER TABLE product ALTER COLUMN merchant_id SET NOT NULL;
CREATE INDEX idx_product_merchant_id ON product(merchant_id);

-- merchant_policy was a singleton (one row, no owner) — now one row per
-- merchant, enforced with a UNIQUE constraint so a merchant can't
-- accidentally get two conflicting policy rows.
ALTER TABLE merchant_policy ADD COLUMN merchant_id BIGINT REFERENCES merchant(id);
UPDATE merchant_policy SET merchant_id = (SELECT id FROM merchant WHERE slug = 'shopfront-running-co');
ALTER TABLE merchant_policy ALTER COLUMN merchant_id SET NOT NULL;
ALTER TABLE merchant_policy ADD CONSTRAINT merchant_policy_merchant_id_key UNIQUE (merchant_id);

-- orders.merchant_id is set once, at order-creation time in
-- pipeline/graph.py's retrieve node, from the merchant_id of the
-- product(s) being bought — durable historical attribution even if a
-- product's own merchant_id were ever reassigned later. Backfilling every
-- existing order to Shopfront Running Co. is correct: it's the only
-- merchant/catalog that has ever existed until this migration.
ALTER TABLE orders ADD COLUMN merchant_id BIGINT REFERENCES merchant(id);
UPDATE orders SET merchant_id = (SELECT id FROM merchant WHERE slug = 'shopfront-running-co');
ALTER TABLE orders ALTER COLUMN merchant_id SET NOT NULL;
CREATE INDEX idx_orders_merchant_id ON orders(merchant_id);

-- co_purchase_stat gets no schema change: a pair's two products must
-- belong to the same merchant, enforced by db/seed_merchant_b.py only
-- ever pairing its own new products together (see that file) rather than
-- a DB constraint, which would need a cross-row subquery CHECK that
-- Postgres doesn't support directly.
