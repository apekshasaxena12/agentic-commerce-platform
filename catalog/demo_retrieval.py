"""
Demo/proof script for catalog/retrieval.py — runs a few realistic queries
against the real seeded catalog and prints actual ranked results, so the
retrieval behavior can be eyeballed before any pipeline is built on top of
it.

Run: python -m catalog.demo_retrieval
"""

from catalog.retrieval import search_products


def show(title: str, query: str, filters: dict | None, limit: int = 5) -> None:
    print(f"\n=== {title} ===")
    print(f"query={query!r} filters={filters}")
    results = search_products(query, filters=filters, limit=limit)
    if not results:
        print("  (no results)")
        return
    for p in results:
        print(
            f"  [{p.score:.3f}] (kw={p.keyword_score:.2f} sem={p.semantic_score:.2f}) "
            f"#{p.id} {p.name} — {p.category} — ₹{p.price} — stock={p.stock}"
        )


def main() -> None:
    # 1. Keyword + semantic + a real price filter. The "5000" in the query
    #    is NOT parsed by search_products — the caller passes it as a
    #    filter, same as the future Intent node would.
    show(
        "running shoes under 5000",
        query="running shoes",
        filters={"max_price": 5000},
    )

    # 2. Pure semantic — no filters, and the phrase "cold weather" does not
    #    literally appear in any product's semantic_description (products
    #    use "thermal" / "cold-weather" with a hyphen instead), so this
    #    tests whether embeddings alone can surface the right apparel.
    show(
        "something for cold weather running",
        query="something for cold weather running",
        filters=None,
    )

    # 3. Category filter + in-stock-only + a query that's mostly semantic
    #    (no product description contains the word "recovery").
    show(
        "recovery gear, socks category only, in stock",
        query="comfortable recovery wear after a long run",
        filters={"category": "socks", "in_stock_only": True},
    )


if __name__ == "__main__":
    main()
