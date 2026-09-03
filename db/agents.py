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
    """
    All agent rows — merchant dashboard's agent-overview panel (Day 12).

    Day 15: extended with real, computed-not-invented trust stats, still
    platform-wide (not merchant-scoped) — consistent with this function's
    existing scope, since an agent isn't tied to one merchant. One LEFT
    JOIN to orders (not audit_log_entry directly, which would fan out rows
    and break the plain COUNT()s) plus two correlated EXISTS subqueries per
    agent, same policy_check-FAIL / verification-reached logic as
    db.audit.get_incident_summary:
      - total_orders / successful_orders: straight COUNT(...) FILTER.
      - policy_block_count / payment_failure_count: COUNT(DISTINCT o.id)
        FILTER, same "status='failed' AND (policy_check logged a FAIL /
        a verification entry exists)" logic as the incident summary, just
        grouped by agent_id instead of merchant_id.
    success_rate is a plain successful/total percentage (rounded to 1
    decimal), computed here in Python since it's a display convenience over
    the two real counts above — None when total_orders is 0 rather than a
    misleading 0%.
    """
    query = """
        SELECT
            ag.id, ag.type, ag.name, ag.budget_limit, ag.spent_so_far, ag.permissions,
            COUNT(o.id) AS total_orders,
            COUNT(o.id) FILTER (WHERE o.status = 'completed') AS successful_orders,
            COUNT(DISTINCT o.id) FILTER (
                WHERE o.status = 'failed' AND EXISTS (
                    SELECT 1 FROM audit_log_entry a
                    WHERE a.order_id = o.id AND a.step = 'policy_check' AND a.output_summary LIKE %(fail_pat)s
                )
            ) AS policy_block_count,
            COUNT(DISTINCT o.id) FILTER (
                WHERE o.status = 'failed' AND EXISTS (
                    SELECT 1 FROM audit_log_entry a WHERE a.order_id = o.id AND a.step = 'verification'
                )
            ) AS payment_failure_count
        FROM agent ag
        LEFT JOIN orders o ON o.agent_id = ag.id
        GROUP BY ag.id
        ORDER BY ag.id
    """
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(query, {"fail_pat": "%=FAIL%"}).fetchall()
    result = []
    for r in rows:
        total_orders = r[6]
        successful_orders = r[7]
        result.append(
            {
                "id": r[0],
                "type": r[1],
                "name": r[2],
                "budget_limit": float(r[3]),
                "spent_so_far": float(r[4]),
                "permissions": r[5],
                "total_orders": total_orders,
                "successful_orders": successful_orders,
                "policy_block_count": r[8],
                "payment_failure_count": r[9],
                "success_rate": round(successful_orders / total_orders * 100, 1) if total_orders else None,
            }
        )
    return result
