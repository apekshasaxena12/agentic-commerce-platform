"""
One-time reconciliation for agent.spent_so_far, which had drifted from
reality because several demo/test scripts were directly resetting it via
raw SQL (bypassing db/budget.py's reserve/release transactions entirely)
instead of treating it as real state. See the session notes for the full
root-cause audit; this script only fixes the data, it does not touch
pipeline/graph.py's reserve/release logic, which was confirmed correct.

Correct spent_so_far definition (per agent): the sum of amounts for every
order that reached policy_check with a real reservation (its policy_check
audit_log_entry contains "budget_available=PASS" — the exact string
graph.py's policy_check_node logs only when check_and_reserve_budget
actually incremented spent_so_far) and has NOT since been released, i.e.
every such order except ones now status='failed' (release_budget is only
ever called on the human-reject, ai_agent-reject, and payment.failed
paths, all three of which set status='failed' — see db/budget.py's three
call sites in pipeline/graph.py).

Verified against live data before writing this: every non-'failed' order
already has a policy_check "budget_available=PASS" entry (no zombie
orders that skipped reservation), so the WHERE clause below is exactly
equivalent to the fuller EXISTS(...) check — kept simple, not because the
EXISTS check wasn't considered.

Before running, also fixes one specific broken order found during the
audit: order #223 (agent #6) — its approval_request #24 was resolved
'rejected' by the merchant on 2026-09-04, but the resulting
release_budget() call crashed with the agent_spent_so_far_check violation
(spent_so_far had already been reset out from under the reservation by
one of the offending scripts), leaving the order permanently stuck in
'pending_approval' instead of 'failed'. This script sets it to 'failed'
to match what the merchant's rejection should have produced — the same
fix already applied by hand to the other order this exact bug hit
(order #242) in the prior session.

Run: python -m tools.reconcile_spent_so_far
Safe to re-run: recomputes from orders/audit_log_entry each time, so a
clean re-run after a correct first run is a no-op.
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_database_url  # noqa: E402

STUCK_ORDER_ID = 223  # rejected approval_request #24, never reached 'failed' — see module docstring


def main() -> None:
    with psycopg.connect(get_database_url()) as conn:
        stuck = conn.execute("SELECT status FROM orders WHERE id = %s", (STUCK_ORDER_ID,)).fetchone()
        if stuck is None:
            print(f"order #{STUCK_ORDER_ID} not found — already handled or DB has changed, skipping")
            fix_stuck_order = False
        elif stuck[0] == "pending_approval":
            print(f"order #{STUCK_ORDER_ID} is stuck in 'pending_approval' (rejected approval never applied) — will fix to 'failed'")
            fix_stuck_order = True
        else:
            print(f"order #{STUCK_ORDER_ID} already status={stuck[0]!r} — no fix needed")
            fix_stuck_order = False

        before = dict(conn.execute("SELECT id, spent_so_far FROM agent ORDER BY id").fetchall())

        # Preview the fix's effect on the reconciliation sum by excluding
        # STUCK_ORDER_ID's status='pending_approval' row the same way its
        # corrected 'failed' status would.
        correct = dict(
            conn.execute(
                """
                SELECT agent_id, COALESCE(SUM(amount), 0)
                FROM orders
                WHERE status != 'failed' AND id != %s
                GROUP BY agent_id
                """,
                (STUCK_ORDER_ID,),
            ).fetchall()
        )
        for agent_id in before:
            correct.setdefault(agent_id, 0)

        print("\n--- reconciliation preview ---")
        for agent_id in sorted(before):
            print(f"agent #{agent_id}: spent_so_far {before[agent_id]} -> {correct[agent_id]}")

        confirm = input("\nApply this correction in one transaction? [y/N] ").strip().lower()
        if confirm != "y":
            print("aborted, no changes made")
            return

        with conn.transaction():
            if fix_stuck_order:
                conn.execute("UPDATE orders SET status = 'failed', updated_at = now() WHERE id = %s", (STUCK_ORDER_ID,))
            for agent_id, correct_value in correct.items():
                conn.execute(
                    "UPDATE agent SET spent_so_far = %s WHERE id = %s",
                    (correct_value, agent_id),
                )

        after = dict(conn.execute("SELECT id, spent_so_far FROM agent ORDER BY id").fetchall())
        print("\n--- applied ---")
        for agent_id in sorted(after):
            print(f"agent #{agent_id}: spent_so_far = {after[agent_id]}")


if __name__ == "__main__":
    main()
