-- Tightens merchant.email/password_hash to NOT NULL now that
-- db/seed_merchant_credentials.py has populated both existing rows.

ALTER TABLE merchant ALTER COLUMN email SET NOT NULL;
ALTER TABLE merchant ALTER COLUMN password_hash SET NOT NULL;
