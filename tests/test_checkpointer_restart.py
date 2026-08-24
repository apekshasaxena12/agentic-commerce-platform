"""
Day 13 Part 1: proves the durability fix for the Day 10-12 finding — with
InMemorySaver, a paused order (checkpoint state living only in that
process's RAM) was lost the instant the process that started it exited, so
a restart/redeploy silently orphaned every pending approval/webhook. Now
that pipeline/graph.py's GRAPH uses PostgresSaver (see that module's
comment on GRAPH), the checkpoint is durable in the same DATABASE_URL every
other table already uses.

This can't be proven within one pytest process: importing pipeline.graph
twice in the same interpreter would just reuse the same module-level GRAPH/
ConnectionPool, which wouldn't catch a bug where state secretly still lived
in process memory somewhere. So this test genuinely starts and kills a
subprocess mid-pause (os._exit — no graceful shutdown, no atexit, the
closest a test can get to `kill -9`), then resumes the same thread_id from
a SECOND, freshly-started subprocess that has never seen the first
process's memory. If that second process can correctly recover the full
pipeline state (order_id, agent_id, target_product, amount — everything
razorpay_node needs) purely from Postgres and continue the graph forward,
the checkpoint survived the restart with no data loss.

Run: pytest tests/test_checkpointer_restart.py -v
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg

from db.connection import get_database_url
from db.orders import get_order

CODE_DIR = Path(__file__).resolve().parent.parent
HUMAN_AGENT_ID = 5  # "Demo Shopper (human)" — see server/app.py's HUMAN_AGENT_ID

# Runs run_pipeline() up to its first pause (authorization's
# human_confirm_required, guaranteed for a human_session agent regardless
# of amount — see pipeline/graph.py's _authorization_impl), prints the
# resulting order_id as JSON, then os._exit()s immediately — no clean
# shutdown, simulating the process dying right where InMemorySaver would
# have lost everything.
_START_AND_KILL = """
import json
import os
import sys

from pipeline.graph import run_pipeline

thread_id = sys.argv[1]
result = run_pipeline(
    {
        "user_message": "Buy the DryTech Running Tee",
        "agent_id": %d,
        "payment_method": "card",
        "discount_pct": 0,
    },
    thread_id,
)
interrupts = result.get("__interrupt__")
assert interrupts, f"expected a pause at authorization, got {result!r}"
value = interrupts[0].value
assert value.get("type") == "human_confirm_required", value

print(json.dumps({"order_id": result["order_id"], "amount": result["amount"]}))
sys.stdout.flush()
os._exit(0)  # no cleanup, no atexit — simulates a killed process, not a clean exit
""" % HUMAN_AGENT_ID

# A completely separate process/interpreter: fresh GRAPH, fresh
# ConnectionPool, no memory of _START_AND_KILL's run whatsoever. Resumes
# the SAME thread_id with the human's "confirm" decision.
_RESUME_AFTER_RESTART = """
import json
import sys

from pipeline.graph import resume_pipeline

thread_id = sys.argv[1]
result = resume_pipeline(thread_id, "confirm")

interrupts = result.get("__interrupt__")
next_interrupt_type = None
if interrupts:
    value = interrupts[0].value
    next_interrupt_type = value.get("type") if isinstance(value, dict) else None

print(json.dumps({
    "order_id": result.get("order_id"),
    "authorized": result.get("authorized"),
    "razorpay_order_id": result.get("razorpay_order_id"),
    "next_interrupt_type": next_interrupt_type,
}))
"""


def _run(code: str, thread_id: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code, thread_id],
        cwd=str(CODE_DIR),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_paused_order_survives_a_real_process_restart():
    with psycopg.connect(get_database_url()) as conn:
        conn.execute("UPDATE agent SET spent_so_far = 0 WHERE id = %s", (HUMAN_AGENT_ID,))

    thread_id = f"restart-test-{uuid.uuid4()}"

    # --- process 1: pause, then die without ever calling resume ---
    started = _run(_START_AND_KILL, thread_id)
    order_id = started["order_id"]
    assert order_id is not None

    order_before = get_order(order_id)
    assert order_before["status"] == "pending_approval"  # authorization hasn't run yet
    assert order_before["amount"] == started["amount"]

    # --- process 2: brand-new interpreter, resumes the same thread_id ---
    resumed = _run(_RESUME_AFTER_RESTART, thread_id)

    # Proves real state recovery, not a no-op: authorization ran (using the
    # agent_id/amount recovered from the checkpoint) and correctly
    # authorized the human's "confirm"; razorpay_node then ran too (using
    # the order_id/amount recovered from the checkpoint) and created a REAL
    # Razorpay order — impossible without target_product/amount surviving
    # the restart. It then paused again at verification (webhook_required),
    # which is the correct next step, not a crash or a restart-from-scratch.
    assert resumed["order_id"] == order_id
    assert resumed["authorized"] is True
    assert resumed["razorpay_order_id"] is not None
    assert resumed["next_interrupt_type"] == "webhook_required"

    order_after = get_order(order_id)
    assert order_after["status"] == "approved"
    assert order_after["razorpay_order_id"] == resumed["razorpay_order_id"]
    assert order_after["amount"] == order_before["amount"]  # unchanged — no data loss

    print(
        f"\nCONFIRMED: order #{order_id} paused in one process, killed with os._exit(0), "
        f"and resumed correctly by a second, unrelated process — checkpoint state "
        f"(agent_id, target_product, amount) survived entirely in Postgres."
    )
