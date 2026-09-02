-- Phase 2 of multi-tenant architecture: real merchant login. Columns are
-- added nullable here (bcrypt hashing needs Python, not pure SQL) — a
-- separate script (db/seed_merchant_credentials.py) fills in real values
-- for both existing merchants right after this migration runs, then
-- migration 0007 tightens both columns to NOT NULL once that data exists.

ALTER TABLE merchant ADD COLUMN email TEXT UNIQUE;
ALTER TABLE merchant ADD COLUMN password_hash TEXT;
