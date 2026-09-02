-- Persists the LangGraph thread_id each order's checkout pipeline runs on,
-- so a paused (merchant-approval-pending) order can be resumed by any
-- process, not just the one that started it — the checkpointer itself
-- (pipeline/graph.py's GRAPH) is already Postgres-backed, but until now the
-- order_id -> thread_id mapping needed to find that checkpoint lived only
-- in mcp_server/server.py's in-memory _order_to_thread dict, which a server
-- restart wiped, producing "no in-progress MCP checkout found for
-- order_id=... in this server process".

ALTER TABLE orders ADD COLUMN thread_id TEXT;
