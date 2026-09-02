-- 0004: Add image_url to product table for catalogue product imagery
ALTER TABLE product ADD COLUMN IF NOT EXISTS image_url TEXT;
