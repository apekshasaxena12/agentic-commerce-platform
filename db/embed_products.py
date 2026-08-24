"""
One-off script: embed each product's semantic_description with
sentence-transformers/all-MiniLM-L6-v2 (384-dim, matches migration 0002)
and store the vector in product.embedding. Safe to re-run — it overwrites
every row's embedding based on the current semantic_description.

Run: python -m db.embed_products
"""

import psycopg
from sentence_transformers import SentenceTransformer

from db.connection import get_database_url

MODEL_NAME = "all-MiniLM-L6-v2"


def to_pgvector_literal(vec) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def main() -> None:
    model = SentenceTransformer(MODEL_NAME)

    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            "SELECT id, semantic_description FROM product ORDER BY id"
        ).fetchall()
        ids = [r[0] for r in rows]
        descriptions = [r[1] for r in rows]

        embeddings = model.encode(descriptions, show_progress_bar=True)

        for product_id, vec in zip(ids, embeddings):
            conn.execute(
                "UPDATE product SET embedding = %s::vector WHERE id = %s",
                (to_pgvector_literal(vec), product_id),
            )

    print(f"embedded {len(ids)} products using {MODEL_NAME}")


if __name__ == "__main__":
    main()
