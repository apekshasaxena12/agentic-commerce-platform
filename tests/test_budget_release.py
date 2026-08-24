"""
Verifies release_budget (db/budget.py): it must restore spent_so_far
exactly, and must not corrupt state under concurrent access. Hits the live
Supabase DB via a throwaway agent, cleaned up after each test.
"""

import threading
from decimal import Decimal

import psycopg
import pytest

from db.budget import check_and_reserve_budget, release_budget
from db.connection import get_database_url


@pytest.fixture
def temp_agent():
    with psycopg.connect(get_database_url()) as conn:
        agent_id = conn.execute(
            """
            INSERT INTO agent (type, name, budget_limit, spent_so_far, permissions)
            VALUES ('ai_agent', 'release-test-agent', 1000, 0, '{}')
            RETURNING id
            """
        ).fetchone()[0]

    yield agent_id

    with psycopg.connect(get_database_url()) as conn:
        conn.execute("DELETE FROM agent WHERE id = %s", (agent_id,))


def _get_spent(agent_id: int) -> Decimal:
    with psycopg.connect(get_database_url()) as conn:
        return conn.execute(
            "SELECT spent_so_far FROM agent WHERE id = %s", (agent_id,)
        ).fetchone()[0]


def test_release_restores_original_and_unblocks_reservation_that_only_fits_after(temp_agent):
    agent_id = temp_agent  # budget_limit=1000, spent_so_far=0

    # reserve 800 — fits (0 + 800 <= 1000)
    assert check_and_reserve_budget(agent_id, Decimal("800")) is True
    assert _get_spent(agent_id) == Decimal("800.00")

    # a second 800 reservation does NOT fit while the first is still held
    # (800 + 800 = 1600 > 1000)
    assert check_and_reserve_budget(agent_id, Decimal("800")) is False
    assert _get_spent(agent_id) == Decimal("800.00")  # unchanged by the failed attempt

    # release the first reservation — spent_so_far must return to its
    # original value (0), not just "less than before"
    release_budget(agent_id, Decimal("800"))
    assert _get_spent(agent_id) == Decimal("0.00")

    # the exact same reservation that failed above now succeeds, because
    # (and only because) the release happened first
    assert check_and_reserve_budget(agent_id, Decimal("800")) is True
    assert _get_spent(agent_id) == Decimal("800.00")


def test_concurrent_double_release_does_not_go_negative(temp_agent):
    agent_id = temp_agent  # budget_limit=1000, spent_so_far=0

    assert check_and_reserve_budget(agent_id, Decimal("800")) is True

    # Only 800 was ever reserved. Fire two concurrent release_budget(800)
    # calls for it — mirrors the original concurrency test's shape (two
    # racing calls near a boundary, assert the invariant holds regardless
    # of interleaving). The row lock must serialize them: one release
    # succeeds (spent_so_far -> 0), the other would push spent_so_far to
    # -800, which the agent table's CHECK (spent_so_far >= 0) constraint
    # must reject rather than silently corrupting state.
    outcomes = [None, None]
    barrier = threading.Barrier(2)

    def worker(i: int) -> None:
        barrier.wait()
        try:
            release_budget(agent_id, Decimal("800"))
            outcomes[i] = "released"
        except psycopg.errors.CheckViolation:
            outcomes[i] = "rejected"

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["rejected", "released"]
    assert _get_spent(agent_id) == Decimal("0.00")
