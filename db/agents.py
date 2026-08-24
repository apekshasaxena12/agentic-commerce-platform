import psycopg

from db.connection import get_database_url


def get_agent(agent_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT id, type, name, budget_limit, spent_so_far, permissions FROM agent WHERE id = %s",
            (agent_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"agent {agent_id} not found")
    return {
        "id": row[0],
        "type": row[1],
        "name": row[2],
        "budget_limit": row[3],
        "spent_so_far": row[4],
        "permissions": row[5],
    }


def list_agents() -> list[dict]:
    """All agent rows — merchant dashboard's agent-overview panel (Day 12)."""
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            "SELECT id, type, name, budget_limit, spent_so_far, permissions FROM agent ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r[0],
            "type": r[1],
            "name": r[2],
            "budget_limit": float(r[3]),
            "spent_so_far": float(r[4]),
            "permissions": r[5],
        }
        for r in rows
    ]
