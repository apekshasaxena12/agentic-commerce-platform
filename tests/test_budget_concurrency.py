"""
Verifies the invariant in db/budget.py: two concurrent purchase attempts
that would together exceed an agent's budget must not both succeed. This
hits the live Supabase DB configured via DATABASE_URL, using a throwaway
agent row that's cleaned up after the test.
"""

import threading
from decimal import Decimal

import psycopg
import pytest

from db.budget import check_and_reserve_budget
from db.connection import get_database_url


@pytest.fixture
def temp_agent():
    with psycopg.connect(get_database_url()) as conn:
        agent_id = conn.execute(
            """
            INSERT INTO agent (type, name, budget_limit, spent_so_far, permissions)
            VALUES ('ai_agent', 'concurrency-test-agent', 1000, 0, '{}')
            RETURNING id
            """
        ).fetchone()[0]

    yield agent_id

    with psycopg.connect(get_database_url()) as conn:
        conn.execute("DELETE FROM agent WHERE id = %s", (agent_id,))


def test_only_one_of_two_concurrent_reservations_near_limit_succeeds(temp_agent):
    agent_id = temp_agent
    # budget_limit=1000; two concurrent requests for 800 each. Combined
    # (1600) blows the budget, but either one alone (800) fits, so exactly
    # one must win the race.
    results = [None, None]
    barrier = threading.Barrier(2)

    def worker(i: int) -> None:
        barrier.wait()  # line both threads up so they actually race for the lock
        results[i] = check_and_reserve_budget(agent_id, Decimal("800"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]

    with psycopg.connect(get_database_url()) as conn:
        spent = conn.execute(
            "SELECT spent_so_far FROM agent WHERE id = %s", (agent_id,)
        ).fetchone()[0]
    assert spent == Decimal("800.00")
