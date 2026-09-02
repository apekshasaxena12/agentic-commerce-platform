-- Initial schema for the agentic-commerce hackathon build.
-- Amounts are numeric(10,2) in rupees throughout (not paise) except where
-- the Razorpay SDK is called directly, which needs paise per its own API.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE agent_type AS ENUM ('human_session', 'ai_agent');
CREATE TYPE order_status AS ENUM ('pending_approval', 'approved', 'completed', 'failed');
CREATE TYPE audit_step AS ENUM (
    'intent', 'retrieve', 'recommend', 'policy_check',
    'authorization', 'razorpay', 'verification'
);
CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected');

CREATE TABLE product (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    structured_attributes JSONB NOT NULL DEFAULT '{}',
    semantic_description TEXT NOT NULL,
    -- 1536 dims to match OpenAI text-embedding-3-small; not populated yet.
    embedding VECTOR(1536),
    return_policy TEXT,
    image_url TEXT,
    substitute_ids INTEGER[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE co_purchase_stat (
    product_a_id BIGINT NOT NULL REFERENCES product(id),
    product_b_id BIGINT NOT NULL REFERENCES product(id),
    co_purchase_rate DOUBLE PRECISION NOT NULL
        CHECK (co_purchase_rate >= 0 AND co_purchase_rate <= 1),
    PRIMARY KEY (product_a_id, product_b_id),
    CHECK (product_a_id <> product_b_id)
);

CREATE TABLE merchant_policy (
    id BIGSERIAL PRIMARY KEY,
    max_discount_pct NUMERIC(5, 2) NOT NULL CHECK (max_discount_pct >= 0),
    max_autonomous_purchase_amount NUMERIC(10, 2) NOT NULL CHECK (max_autonomous_purchase_amount >= 0),
    allowed_payment_methods TEXT[] NOT NULL DEFAULT '{}',
    approval_required_above NUMERIC(10, 2) NOT NULL CHECK (approval_required_above >= 0)
);

CREATE TABLE agent (
    id BIGSERIAL PRIMARY KEY,
    type agent_type NOT NULL,
    name TEXT NOT NULL,
    budget_limit NUMERIC(10, 2) NOT NULL CHECK (budget_limit >= 0),
    spent_so_far NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (spent_so_far >= 0),
    permissions JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    agent_id BIGINT NOT NULL REFERENCES agent(id),
    items JSONB NOT NULL,
    amount NUMERIC(10, 2) NOT NULL CHECK (amount >= 0),
    discount_applied NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (discount_applied >= 0),
    status order_status NOT NULL DEFAULT 'pending_approval',
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log_entry (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    step audit_step NOT NULL,
    "timestamp" TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_summary TEXT,
    output_summary TEXT,
    reasoning_text TEXT
);

CREATE TABLE approval_request (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    status approval_status NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

CREATE INDEX idx_co_purchase_stat_product_a ON co_purchase_stat(product_a_id);
CREATE INDEX idx_orders_agent_id ON orders(agent_id);
CREATE INDEX idx_audit_log_entry_order_id ON audit_log_entry(order_id);
CREATE INDEX idx_approval_request_order_id ON approval_request(order_id);
