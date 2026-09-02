"""
Seeds a second merchant, "Roast & Ritual" (specialty coffee beans + brew
equipment + drinkware) — deliberately a different category from Merchant
A's running gear, so the two catalogs feel genuinely distinct in a demo,
and gets its own merchant_policy row with numbers deliberately different
from Merchant A's (see MERCHANT_B_POLICY below) to prove policy is
resolved per-merchant, not hardcoded.

Unlike db/seed.py (which TRUNCATEs and re-inserts Merchant A's original
catalog — safe only against an empty/fresh database), this script is
INSERT-only and never touches Merchant A's rows, agent rows, or order
history — safe to run once against the live, already-populated database.
Product ids are whatever BIGSERIAL assigns (not hardcoded), so
co_purchase_stat pairs are built from a name->id map captured right after
insert, rather than guessing the next id.

Run: python -m db.seed_merchant_b
Then: python -m db.embed_products   (re-embeds every product, including
      these new ones — safe/idempotent, see that script's docstring)
"""

from decimal import Decimal

import psycopg

from db.connection import get_database_url

MERCHANT = {"name": "Roast & Ritual", "slug": "roast-and-ritual"}

# ---------------------------------------------------------------------------
# Products: name, category, price (INR), stock, structured_attributes,
# semantic_description, return_policy, image_url. No substitute_ids/ids
# here (unlike db/seed.py) — this catalog is small enough not to need
# substitute suggestions, and ids aren't known until after insert.
# ---------------------------------------------------------------------------

PRODUCTS = [
    ("Ethiopia Yirgacheffe Light Roast 250g", "coffee_beans", 549, 60,
     {"brand": "Roast & Ritual", "origin": "Ethiopia", "roast_level": "light",
      "weight_grams": 250, "process": "washed",
      "tasting_notes": ["floral", "citrus", "jasmine"]},
     "Single-origin Ethiopian Yirgacheffe, washed process, roasted light to "
     "preserve its bright floral and citrus character. Best brewed pour-over "
     "or in an AeroPress within 4 weeks of the roast date on the bag.",
     "14-day return on unopened bags, roast-date freshness not guaranteed after that.",
     "/products/coffee-1.jpg"),

    ("Colombia Supremo Medium Roast 500g", "coffee_beans", 899, 55,
     {"brand": "Roast & Ritual", "origin": "Colombia", "roast_level": "medium",
      "weight_grams": 500, "process": "washed",
      "tasting_notes": ["caramel", "chocolate", "nutty"]},
     "A dependable daily-drinker from Colombia's Huila region, medium roast "
     "with a balanced caramel-and-chocolate cup. Works equally well in a "
     "drip machine, French press, or moka pot.",
     "14-day return on unopened bags, roast-date freshness not guaranteed after that.",
     "/products/coffee-2.jpg"),

    ("House Espresso Blend 1kg", "coffee_beans", 1699, 40,
     {"brand": "Roast & Ritual", "origin": "blend (Brazil/India)", "roast_level": "dark",
      "weight_grams": 1000, "process": "natural",
      "tasting_notes": ["dark chocolate", "roasted almond", "low acidity"]},
     "House espresso blend built for consistency at high volume — a Brazil/"
     "India natural-process blend, dark roasted for a syrupy body and low "
     "acidity that holds up under milk. Sold in a 1kg bag for cafes and "
     "heavy home use.",
     "14-day return on unopened bags, roast-date freshness not guaranteed after that.",
     "/products/coffee-3.jpg"),

    ("Decaf Sumatra Mandheling 250g", "coffee_beans", 649, 35,
     {"brand": "Roast & Ritual", "origin": "Indonesia", "roast_level": "medium-dark",
      "weight_grams": 250, "decaf_method": "Swiss Water Process",
      "tasting_notes": ["earthy", "spice", "dark chocolate"]},
     "Full-bodied Sumatran decaf, chemical-free Swiss Water Process, keeping "
     "the earthy, spiced character Mandheling beans are known for without "
     "the caffeine. A genuine evening-cup alternative, not an afterthought.",
     "14-day return on unopened bags, roast-date freshness not guaranteed after that.",
     "/products/coffee-4.jpg"),

    ("Ceramic Pour-Over Dripper", "brew_equipment", 899, 45,
     {"brand": "Roast & Ritual", "material": "ceramic", "cup_capacity": 2,
      "filter_type": "cone (size 02)"},
     "A cone-style ceramic pour-over dripper that holds heat better than "
     "plastic for a more even extraction. Fits standard size-02 paper "
     "filters and most 2-cup carafes and mugs.",
     "30-day return if unused, original packaging required.",
     "/products/coffee-5.jpg"),

    ("Stainless French Press 600ml", "brew_equipment", 1299, 38,
     {"brand": "Roast & Ritual", "material": "stainless steel + borosilicate glass",
      "capacity_ml": 600, "cup_capacity": 4},
     "600ml French press with a stainless steel frame and a borosilicate "
     "glass beaker, brews about 4 cups. A double-mesh filter cuts down on "
     "sediment compared to a single-mesh plunger.",
     "30-day return if unused, original packaging required.",
     "/products/coffee-6.jpg"),

    ("Manual Conical Burr Grinder", "brew_equipment", 2499, 22,
     {"brand": "Roast & Ritual", "burr_material": "stainless steel",
      "adjustable_settings": 24, "hopper_capacity_g": 30},
     "Hand-crank conical burr grinder with 24 click-stop settings, from "
     "espresso-fine to French-press-coarse. Consistent grind size matters "
     "more than any other single factor in cup quality — this is the "
     "upgrade most home setups need first.",
     "15-day return if unused, manufacturer warranty on the burr mechanism.",
     "/products/coffee-7.jpg"),

    ("Precision Coffee Scale with Timer", "brew_equipment", 1499, 30,
     {"brand": "Roast & Ritual", "max_weight_g": 3000, "resolution_g": 0.1,
      "built_in_timer": True},
     "0.1g-resolution digital scale with a built-in brew timer, the standard "
     "tool for dialing in a consistent coffee-to-water ratio on pour-over or "
     "espresso. USB-C rechargeable, auto-off after 5 minutes idle.",
     "15-day return if unopened, electronics warranty via manufacturer.",
     "/products/coffee-8.jpg"),

    ("Double-Wall Insulated Travel Mug 350ml", "drinkware", 799, 70,
     {"brand": "Roast & Ritual", "capacity_ml": 350, "material": "stainless steel",
      "leak_proof": True, "retains_heat_hours": 6},
     "Double-wall vacuum-insulated travel mug, keeps coffee hot for about 6 "
     "hours. Leak-proof slide-lock lid safe to toss in a bag, fits most car "
     "cup holders.",
     "30-day return if unused, original packaging required.",
     "/products/coffee-9.jpg"),

    ("Ceramic Pour-Over Mug Set of 2", "drinkware", 1199, 40,
     {"brand": "Roast & Ritual", "material": "ceramic", "capacity_ml": 300, "set_size": 2},
     "A matched pair of 300ml ceramic mugs sized for a pour-over serving, "
     "wide enough to fit a dripper resting directly on top. Microwave and "
     "dishwasher safe glaze.",
     "30-day return if unused, original packaging required.",
     "/products/coffee-10.jpg"),
]

# ---------------------------------------------------------------------------
# Co-purchase stats, referenced by product NAME (resolved to real ids after
# insert) — both products in every pair are Roast & Ritual's own, same
# same-merchant rule Merchant A's pairs already follow.
# ---------------------------------------------------------------------------

CO_PURCHASE_STATS = [
    ("Ethiopia Yirgacheffe Light Roast 250g", "Ceramic Pour-Over Dripper", 0.42),
    ("Ethiopia Yirgacheffe Light Roast 250g", "Precision Coffee Scale with Timer", 0.25),
    ("Colombia Supremo Medium Roast 500g", "Manual Conical Burr Grinder", 0.38),
    ("House Espresso Blend 1kg", "Stainless French Press 600ml", 0.31),
    ("Ceramic Pour-Over Dripper", "Ceramic Pour-Over Mug Set of 2", 0.36),
    ("Manual Conical Burr Grinder", "Precision Coffee Scale with Timer", 0.44),
    ("Stainless French Press 600ml", "Precision Coffee Scale with Timer", 0.29),
    ("Double-Wall Insulated Travel Mug 350ml", "Ethiopia Yirgacheffe Light Roast 250g", 0.22),
]

# ---------------------------------------------------------------------------
# Merchant policy — deliberately different numbers from Merchant A's
# (max_discount_pct=15.00, approval_required_above=2000.00, see db/seed.py)
# so the same ai_agent buying the same-priced item is gated at one merchant
# and auto-approved at the other.
# ---------------------------------------------------------------------------

MERCHANT_B_POLICY = {
    "max_discount_pct": Decimal("25.00"),
    "max_autonomous_purchase_amount": Decimal("5000.00"),
    "allowed_payment_methods": ["card", "upi"],
    "approval_required_above": Decimal("5000.00"),
}


def main() -> None:
    with psycopg.connect(get_database_url()) as conn:
        existing = conn.execute(
            "SELECT id FROM merchant WHERE slug = %s", (MERCHANT["slug"],)
        ).fetchone()
        if existing is not None:
            print(f"merchant {MERCHANT['slug']!r} already exists (id={existing[0]}); aborting, nothing inserted")
            return

        merchant_id = conn.execute(
            "INSERT INTO merchant (name, slug) VALUES (%s, %s) RETURNING id",
            (MERCHANT["name"], MERCHANT["slug"]),
        ).fetchone()[0]
        print(f"inserted merchant {MERCHANT['name']!r} (id={merchant_id})")

        name_to_id: dict[str, int] = {}
        for (name, category, price, stock, attrs, desc, policy, img_url) in PRODUCTS:
            pid = conn.execute(
                """
                INSERT INTO product
                    (name, category, price, stock, structured_attributes,
                     semantic_description, return_policy, image_url, merchant_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (name, category, Decimal(price), stock, psycopg.types.json.Jsonb(attrs),
                 desc, policy, img_url, merchant_id),
            ).fetchone()[0]
            name_to_id[name] = pid
        print(f"inserted {len(PRODUCTS)} products for {MERCHANT['name']!r}")

        for (name_a, name_b, rate) in CO_PURCHASE_STATS:
            conn.execute(
                """
                INSERT INTO co_purchase_stat (product_a_id, product_b_id, co_purchase_rate)
                VALUES (%s, %s, %s)
                """,
                (name_to_id[name_a], name_to_id[name_b], rate),
            )
        print(f"inserted {len(CO_PURCHASE_STATS)} co_purchase_stat rows")

        conn.execute(
            """
            INSERT INTO merchant_policy
                (merchant_id, max_discount_pct, max_autonomous_purchase_amount,
                 allowed_payment_methods, approval_required_above)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (merchant_id,
             MERCHANT_B_POLICY["max_discount_pct"],
             MERCHANT_B_POLICY["max_autonomous_purchase_amount"],
             MERCHANT_B_POLICY["allowed_payment_methods"],
             MERCHANT_B_POLICY["approval_required_above"]),
        )
        print(f"inserted merchant_policy row for {MERCHANT['name']!r}")


if __name__ == "__main__":
    main()
