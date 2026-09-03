"""
Day 10-11 demo: a scripted MCP client acting as an external AI buyer agent,
talking to mcp_server/server.py over a REAL MCP transport (streamable-http,
not direct Python function calls) — that round trip is the whole point,
since it's what proves Front Door 2 is a real, separate front door and not
just the pipeline functions called from a different file.

Two scenarios against the real seeded catalog/DB:

  (a) search_catalog + checkout on a product UNDER
      merchant_policy.approval_required_above (2000) -> "completed", no
      human/merchant step at any point.

  (b) search_catalog + checkout on a product OVER approval_required_above
      -> "pending_approval". The agent polls check_order_status (still
      pending_approval). Then an HTTP POST to the merchant dashboard's
      /merchant/resolve-approval endpoint (Day 12) — served by the SAME
      running server process, on the same port, but reached over plain
      HTTP rather than MCP — approves it as the merchant. The agent polls
      again -> "completed". This is the merchant-side half of the
      self-approval boundary: an MCP client (this script's own
      ClientSession) has no tool that can do this — see
      mcp_server/server.py's module docstring.

Reuses the exact two products pipeline/demo_run.py already proved trigger
each side of the gate (Compression Running Tights @ 1599, under threshold;
Windproof Running Jacket @ 3499, over threshold) rather than picking new
ones blind.

Requires DATABASE_URL / GROQ_API_KEY / RAZORPAY_* to be reachable from
wherever this runs (see .env) — by default this script spawns the real MCP
server as a local subprocess, which makes real DB/Groq/Razorpay calls
exactly like Front Door 1 does.

Day 14: can also run against an already-running server instead of
spawning one — e.g. the deployed Render service — via --server-url or the
MCP_SERVER_URL env var (see main_async/parse_args below). DATABASE_URL is
still required either way: reset_agent_spend() always talks to the DB
directly, and against the deployed server that DATABASE_URL must be the
same Supabase instance Render's agentic-commerce-mcp service uses (see
DEPLOY.md) — otherwise this script would zero a different agent row than
the one the remote server actually charges against.

Run:
    python mcp_server/demo_ai_buyer.py                       # local subprocess (unchanged default)
    python mcp_server/demo_ai_buyer.py --server-url https://agentic-commerce-mcp-okgk.onrender.com
    MCP_SERVER_URL=https://agentic-commerce-mcp-okgk.onrender.com python mcp_server/demo_ai_buyer.py
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from db.connection import get_database_url  # noqa: E402

PORT = 8765
LOCAL_URL = f"http://127.0.0.1:{PORT}/mcp"
LOCAL_MERCHANT_API = f"http://127.0.0.1:{PORT}/merchant"

AI_AGENT_ID = 6  # "Shopping Assistant Agent" — see mcp_server/server.py's AI_AGENT_ID note

# owner@shopfrontrunning.com owns the Windproof Running Jacket (merchant #1,
# see db/seed_merchant_credentials.py) — the merchant who must resolve
# scenario (b)'s pending approval.
MERCHANT_EMAIL = "owner@shopfrontrunning.com"
MERCHANT_PASSWORD = "RunningCo#2026"

UNDER_THRESHOLD_QUERY = "Compression Running Tights"
OVER_THRESHOLD_QUERY = "Windproof Running Jacket"


def reset_agent_spend() -> None:
    """Demo hygiene, same rationale/pattern as pipeline/demo_run.py's
    reset_agent_spend: zero out the AI agent's spent_so_far so repeated
    demo runs (or scenario (b) running right after scenario (a) spent some
    of the same budget) don't accumulate false budget pressure."""
    with psycopg.connect(get_database_url()) as conn:
        conn.execute("UPDATE agent SET spent_so_far = 0 WHERE id = %s", (AI_AGENT_ID,))


def _parse_tool_result(result):
    """
    FastMCP emits ONE content block per element for a list-returning tool
    (verified against this exact mcp==1.26.0 install — a naive
    result.content[0] silently truncates a multi-product search_catalog
    response to just its first item, and a *single*-product response is
    indistinguishable at the content-block level from a plain dict return,
    since both produce exactly one block). structuredContent doesn't have
    that ambiguity: it's None for a bare-dict-returning tool and
    {"result": [...]} for a list-returning tool (even a 1- or 0-item one),
    so prefer it whenever present.
    """
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if isinstance(sc, dict) and set(sc.keys()) == {"result"} else sc
    texts = [c.text for c in result.content if hasattr(c, "text")]
    return json.loads(texts[0]) if texts else None


async def call(session: ClientSession, name: str, arguments: dict, log: bool = True) -> dict:
    if log:
        print(f"\n>>> tool_call {name}({json.dumps(arguments)})")
    result = await session.call_tool(name, arguments)
    parsed = _parse_tool_result(result)
    if log:
        print(f"<<< {json.dumps(parsed, indent=2)}")
    return parsed


async def wait_for_server_ready(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc = None
    while time.monotonic() < deadline:
        try:
            async with streamablehttp_client(url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return
        except Exception as exc:  # server subprocess still starting up
            last_exc = exc
            await asyncio.sleep(0.4)
    raise RuntimeError(f"MCP server never became ready on {url}: {last_exc}")


async def scenario_a_under_threshold(session: ClientSession) -> None:
    print("\n" + "=" * 70)
    print(f"SCENARIO (a): AI buyer checks out '{UNDER_THRESHOLD_QUERY}' (under approval_required_above)")
    print("=" * 70)

    results = await call(session, "search_catalog", {"query": UNDER_THRESHOLD_QUERY, "category": "apparel_bottom"})
    assert results, "expected search_catalog to find the seeded product"
    product = results[0]
    print(f"top match: #{product['id']} {product['name']!r} price={product['price']}")

    outcome = await call(session, "checkout", {"product_id": product["id"], "quantity": 1})
    assert outcome["outcome"] == "completed", f"expected completed, got {outcome}"
    print("\nCONFIRMED: completed with zero human/merchant involvement at any point.")


async def scenario_b_over_threshold(session: ClientSession, merchant_api: str) -> None:
    print("\n" + "=" * 70)
    print(f"SCENARIO (b): AI buyer checks out '{OVER_THRESHOLD_QUERY}' (over approval_required_above)")
    print("=" * 70)

    reset_agent_spend()  # isolate this scenario's budget from (a)'s spend, same as pipeline/demo_run.py

    results = await call(session, "search_catalog", {"query": OVER_THRESHOLD_QUERY, "category": "outerwear"})
    assert results, "expected search_catalog to find the seeded product"
    product = results[0]
    print(f"top match: #{product['id']} {product['name']!r} price={product['price']}")

    checkout_outcome = await call(session, "checkout", {"product_id": product["id"], "quantity": 1})
    assert checkout_outcome["outcome"] == "pending_approval", f"expected pending_approval, got {checkout_outcome}"
    order_id = checkout_outcome["order_id"]
    approval_id = checkout_outcome["approval_request_id"]
    print(f"\nAI buyer's over-threshold purchase (order #{order_id}) is paused: approval_request #{approval_id}.")

    poll = await call(session, "check_order_status", {"order_id": order_id})
    assert poll["status"] == "pending_approval", f"expected pending_approval, got {poll}"
    print("\nconfirmed: check_order_status agrees -> still pending_approval (no self-approval by the buyer itself).")

    print(
        "\n(logging in as the merchant and resolving via HTTP — the merchant-side half of the "
        "self-approval boundary; this script's own MCP ClientSession has no tool that can do this)"
    )
    # Generous timeout: resolving an approval synchronously drives the rest
    # of the pipeline (a real Razorpay order-create call, then the
    # synthetic-webhook resume through verification), not just a DB write.
    async with httpx.AsyncClient(base_url=merchant_api, timeout=30.0) as http:
        login = await http.post("/login", json={"email": MERCHANT_EMAIL, "password": MERCHANT_PASSWORD})
        login.raise_for_status()
        resolve = await http.post(f"/resolve-approval/{approval_id}", json={"approved": True})
        resolve.raise_for_status()
        resolution = resolve.json()
    print(f"POST /merchant/resolve-approval/{approval_id} -> {resolution}")
    assert resolution["outcome"] == "completed", f"expected completed after merchant approval, got {resolution}"

    poll2 = await call(session, "check_order_status", {"order_id": order_id})
    assert poll2["status"] == "completed", f"expected completed, got {poll2}"
    print("\nCONFIRMED: merchant approval resolved the pause -> check_order_status agrees -> completed.")


async def main_async(server_url: str | None) -> None:
    reset_agent_spend()

    server_proc = None
    if server_url:
        base = server_url.rstrip("/")
        url = f"{base}/mcp"
        merchant_api = f"{base}/merchant"
        print(f"Connecting to remote MCP server (no subprocess spawned): {url}")
    else:
        url = LOCAL_URL
        merchant_api = LOCAL_MERCHANT_API
        print(f"Starting MCP server subprocess: python -m mcp_server.server --http --port {PORT}")
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_server.server", "--http", "--port", str(PORT)],
            cwd=str(CODE_DIR),
        )
    try:
        await wait_for_server_ready(url)
        print("MCP server is up; connecting a real MCP client over streamable-http...")

        async with streamablehttp_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(f"Server exposes tools: {[t.name for t in tools.tools]}")

                await scenario_a_under_threshold(session)
                await scenario_b_over_threshold(session, merchant_api)

        print("\n" + "=" * 70)
        print("ALL SCENARIOS PASSED — real MCP server/client round trip, real pipeline, real gate.")
        print("=" * 70)
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--server-url",
        default=os.environ.get("MCP_SERVER_URL"),
        help=(
            "Base URL of an already-running MCP server, e.g. "
            "https://agentic-commerce-mcp-okgk.onrender.com — connects directly instead of "
            "spawning a local subprocess. Falls back to the MCP_SERVER_URL env var, then to "
            "spawning 'python -m mcp_server.server --http --port 8765' locally if neither is set."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args.server_url))


if __name__ == "__main__":
    main()
