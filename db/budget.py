"""
Budget-reservation invariant: any code path that checks agent.spent_so_far
against agent.budget_limit and then updates spent_so_far MUST do so inside
one DB transaction holding a row lock (SELECT ... FOR UPDATE) on that agent
row. Without the lock, two concurrent purchase attempts can both read the
same stale spent_so_far, both pass the check, and both commit an update —
letting an agent overspend its budget.

Nothing calls this yet (no purchase/authorization path exists as of Day
2-3) — every future purchase flow must route its budget check through this
function rather than re-implementing the check-then-act logic inline.

release_budget() is the mirror for the other direction: an order that had
budget reserved (it passed policy_check) but then terminates as "failed"
before completing — a rejected approval_request/confirm, or a Razorpay
decline — must give that reservation back. Same row-lock discipline. If an
order never reached policy_check, nothing was reserved, so nothing should
be released for it.
"""

from decimal import Decimal

import psycopg

from db.connection import get_database_url


def check_and_reserve_budget(agent_id: int, amount: Decimal) -> bool:
    """
    Atomically check whether `amount` fits within the agent's remaining
    budget and, if so, reserve it by incrementing spent_so_far in the same
    transaction.

    Returns True and reserves the amount if spent_so_far + amount <=
    budget_limit. Returns False and makes no changes otherwise. Raises
    ValueError if the agent doesn't exist.
    """
    amount = Decimal(amount)

    # `with psycopg.connect(...) as conn:` commits on clean exit and rolls
    # back on exception, either way releasing the row lock taken below.
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT budget_limit, spent_so_far FROM agent WHERE id = %s FOR UPDATE",
            (agent_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"agent {agent_id} not found")

        budget_limit, spent_so_far = row
        if spent_so_far + amount > budget_limit:
            return False

        conn.execute(
            "UPDATE agent SET spent_so_far = spent_so_far + %s WHERE id = %s",
            (amount, agent_id),
        )
        return True


def release_budget(agent_id: int, amount: Decimal) -> None:
    """
    Atomically give back a previously reserved amount by decrementing
    spent_so_far, under the same row lock as check_and_reserve_budget.

    Unconditional decrement — there's no "does it fit" question on release,
    only on reserve. If the caller releases more than was ever reserved for
    this agent, spent_so_far would go negative, which the agent table's own
    CHECK constraint (spent_so_far >= 0) rejects; that's treated as a bug
    signal (e.g. a double release), not something to silently clamp away.
    Raises ValueError if the agent doesn't exist.
    """
    amount = Decimal(amount)

    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT spent_so_far FROM agent WHERE id = %s FOR UPDATE",
            (agent_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"agent {agent_id} not found")

        conn.execute(
            "UPDATE agent SET spent_so_far = spent_so_far - %s WHERE id = %s",
            (amount, agent_id),
        )
