"""
Hybrid retrieval over the product catalog. This is a standalone module the
future Intent/Retrieve pipeline nodes will call — no LangGraph, no Groq, no
pipeline wiring here (day 6-8).

Query scope: `query` is free text used for keyword/semantic matching only.
It is NOT parsed for structured constraints — a query like "running shoes
under 5000" will not have "5000" extracted into a price filter here. That
kind of natural-language-to-structured-filter extraction is the job of the
future Intent node. Callers of search_products() are expected to pass
`filters` explicitly alongside a `query` that describes what's wanted in
words.

Ranking approach: filters are hard constraints applied first in SQL to
narrow the candidate set (a product outside the price range or wrong
category should never appear, no matter how well it scores otherwise).
Within that filtered set, keyword and semantic similarity are computed and
blended into one score:

  - Semantic score: pgvector cosine distance (`<=>`) between the query's
    embedding and each product's stored embedding, converted to a
    similarity (1 - distance). Computed in SQL so pgvector does the vector
    math, not Python.
  - Keyword score: fraction of query tokens found (case-insensitive
    substring) across name + category + semantic_description. Plain ILIKE
    per the task's guidance — no full-text search infra needed at this
    table size. Computed in Python after the filtered rows come back,
    since token-level matching across multiple tokens is simpler to express
    that way than as dynamic SQL.
  - Blend: 0.35 * keyword_score + 0.65 * semantic_score. Semantic is
    weighted higher because users describe intent in words that often
    don't literally appear in a product's description (e.g. "cold weather"
    vs. a description that says "thermal" / "cold-weather" with a hyphen,
    which a naive substring match on the phrase "cold weather" would miss).
    An exact keyword hit still deserves credit, just less. These weights
    are a starting judgment call, not fit to any labeled data — revisit
    once there's real usage to tune against.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import psycopg
from sentence_transformers import SentenceTransformer

from db.connection import get_database_url

MODEL_NAME = "all-MiniLM-L6-v2"

KEYWORD_WEIGHT = 0.35
SEMANTIC_WEIGHT = 0.65

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


@dataclass
class Product:
    id: int
    name: str
    category: str
    price: Decimal
    stock: int
    structured_attributes: dict
    semantic_description: str
    return_policy: Optional[str]
    substitute_ids: list[int]
    score: float
    keyword_score: float
    semantic_score: float


def _keyword_score(query: str, name: str, category: str, description: str) -> float:
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return 0.0
    haystack = f"{name} {category} {description}".lower()
    hits = sum(1 for t in tokens if t in haystack)
    return hits / len(tokens)


def _to_pgvector_literal(vec) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def search_products(
    query: str,
    filters: Optional[dict[str, Any]] = None,
    limit: int = 10,
) -> list[Product]:
    filters = filters or {}
    category = filters.get("category")
    min_price = filters.get("min_price")
    max_price = filters.get("max_price")
    in_stock_only = bool(filters.get("in_stock_only", False))

    model = _get_model()
    query_vec_literal = _to_pgvector_literal(model.encode(query).tolist())

    sql = """
        SELECT id, name, category, price, stock, structured_attributes,
               semantic_description, return_policy, substitute_ids,
               embedding <=> %(qvec)s::vector AS cosine_distance
        FROM product
        WHERE (%(category)s::text IS NULL OR category = %(category)s)
          AND (%(min_price)s::numeric IS NULL OR price >= %(min_price)s)
          AND (%(max_price)s::numeric IS NULL OR price <= %(max_price)s)
          AND (%(in_stock_only)s = FALSE OR stock > 0)
    """

    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            sql,
            {
                "qvec": query_vec_literal,
                "category": category,
                "min_price": min_price,
                "max_price": max_price,
                "in_stock_only": in_stock_only,
            },
        ).fetchall()

    results = []
    for (pid, name, cat, price, stock, attrs, desc, policy, subs, distance) in rows:
        semantic_score = 1.0 - float(distance)
        keyword_score = _keyword_score(query, name, cat, desc)
        blended = KEYWORD_WEIGHT * keyword_score + SEMANTIC_WEIGHT * semantic_score
        results.append(
            Product(
                id=pid,
                name=name,
                category=cat,
                price=price,
                stock=stock,
                structured_attributes=attrs,
                semantic_description=desc,
                return_policy=policy,
                substitute_ids=subs,
                score=blended,
                keyword_score=keyword_score,
                semantic_score=semantic_score,
            )
        )

    results.sort(key=lambda p: p.score, reverse=True)
    return results[:limit]


def get_product_detail(product_id: int) -> Optional[dict]:
    """
    Full detail for a single product, including cross-sell (co_purchase_stat)
    and substitute info — for Front Door 2's get_product MCP tool. Returns
    None if no such product exists. Standalone read, same spirit as
    search_products above: no pipeline involvement for a pure catalog fetch.
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT id, name, category, price, stock, structured_attributes,
                   semantic_description, return_policy, substitute_ids
            FROM product WHERE id = %s
            """,
            (product_id,),
        ).fetchone()
        if row is None:
            return None
        (pid, name, category, price, stock, attrs, desc, policy, substitute_ids) = row

        cross_sell_rows = conn.execute(
            """
            SELECT cp.product_b_id, p.name, cp.co_purchase_rate
            FROM co_purchase_stat cp
            JOIN product p ON p.id = cp.product_b_id
            WHERE cp.product_a_id = %s
            ORDER BY cp.co_purchase_rate DESC
            """,
            (product_id,),
        ).fetchall()

        substitute_rows = (
            conn.execute(
                "SELECT id, name, price FROM product WHERE id = ANY(%s)",
                (substitute_ids,),
            ).fetchall()
            if substitute_ids
            else []
        )

    return {
        "id": pid,
        "name": name,
        "category": category,
        "price": float(price),
        "stock": stock,
        "structured_attributes": attrs,
        "semantic_description": desc,
        "return_policy": policy,
        "cross_sell": [
            {"product_id": b_id, "name": b_name, "co_purchase_rate": rate}
            for (b_id, b_name, rate) in cross_sell_rows
        ],
        "substitutes": [
            {"product_id": s_id, "name": s_name, "price": float(s_price)}
            for (s_id, s_name, s_price) in substitute_rows
        ],
    }
