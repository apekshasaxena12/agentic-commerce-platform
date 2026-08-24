-- 0001 created product.embedding as VECTOR(1536), assuming OpenAI embeddings.
-- We're using sentence-transformers/all-MiniLM-L6-v2 instead, which is
-- 384-dimensional. No embeddings have been generated yet (column is all
-- NULL), so this is a plain type change, no data migration needed.

ALTER TABLE product ALTER COLUMN embedding TYPE VECTOR(384) USING embedding::vector(384);
