"""
Seed data for the demo catalog: running shoes + sportswear. Chosen because
it's visually clear in a live demo (shoes are instantly recognizable, sizes
and colors give obvious "structured attributes" to filter on) and it makes
co-purchase stats intuitive ("68% of shoe buyers also bought these socks").

Idempotent-ish: wipes and re-inserts every table this script owns, so it's
safe to re-run while the catalog is still being iterated on during the
hackathon. Does NOT touch agent rows created by tests (those clean up after
themselves) or any orders/audit data (there isn't a purchase path yet).

Run: python -m db.seed
"""

from decimal import Decimal

import psycopg

from db.connection import get_database_url

# ---------------------------------------------------------------------------
# Products: id, name, category, price (INR), stock, structured_attributes,
# semantic_description, return_policy, substitute_ids
# ---------------------------------------------------------------------------

PRODUCTS = [
    # --- Running shoes (1-8) ---
    (1, "Velocity Air Runner", "running_shoes", 4999, 40,
     {"brand": "Nimbus Sports", "gender": "unisex", "use_case": "road running",
      "sizes_uk": [6, 7, 8, 9, 10, 11], "colors": ["black/white", "grey/orange"],
      "cushioning": "high", "weight_grams": 260},
     "Lightweight daily-trainer road running shoe with high-rebound foam "
     "cushioning, breathable mesh upper, and a wide size range. Good for "
     "beginners logging their first 5Ks up to daily training miles.",
     "30-day return if unworn, original box and tags required.",
     [3, 4, 7]),

    (2, "Trailblaze GTX", "running_shoes", 5999, 25,
     {"brand": "Nimbus Sports", "gender": "unisex", "use_case": "trail running",
      "sizes_uk": [6, 7, 8, 9, 10, 11, 12], "colors": ["olive/black", "rust/grey"],
      "cushioning": "medium", "weight_grams": 310, "waterproof": True},
     "Waterproof trail running shoe with an aggressive lug outsole for mud "
     "and loose gravel, reinforced toe cap, and a GORE-TEX-style membrane "
     "for wet-weather trail runs.",
     "30-day return if unworn, original box and tags required.",
     [6]),

    (3, "CloudStep Pro", "running_shoes", 6499, 30,
     {"brand": "Zephyr Athletics", "gender": "men", "use_case": "road running",
      "sizes_uk": [7, 8, 9, 10, 11], "colors": ["white/blue", "black/red"],
      "cushioning": "very high", "weight_grams": 245},
     "Premium max-cushion road running shoe with a rocker geometry designed "
     "to reduce joint impact on long runs. Popular with runners training "
     "for half and full marathons.",
     "30-day return if unworn, original box and tags required.",
     [1, 4, 5]),

    (4, "CloudStep Lite", "running_shoes", 3999, 45,
     {"brand": "Zephyr Athletics", "gender": "women", "use_case": "road running",
      "sizes_uk": [4, 5, 6, 7, 8], "colors": ["lilac/white", "black/mint"],
      "cushioning": "high", "weight_grams": 220},
     "A lighter, lower-cost version of the CloudStep line aimed at everyday "
     "5-10K road runs, with a snug knit upper and moderate cushioning.",
     "30-day return if unworn, original box and tags required.",
     [1, 3, 7]),

    (5, "Marathon Elite Carbon", "running_shoes", 8999, 15,
     {"brand": "Apex Run", "gender": "unisex", "use_case": "race day",
      "sizes_uk": [6, 7, 8, 9, 10, 11], "colors": ["neon yellow/black"],
      "cushioning": "high", "weight_grams": 195, "plate": "carbon fiber"},
     "Carbon-plated racing shoe built for marathon PBs, with an aggressive "
     "propulsive rocker and minimal weight. Not intended as a daily trainer "
     "due to reduced durability of the racing foam.",
     "15-day return if unworn (racing shoes only), original box required.",
     [3]),

    (6, "EasyStride Cushion", "running_shoes", 2999, 60,
     {"brand": "Apex Run", "gender": "unisex", "use_case": "walking/jogging",
      "sizes_uk": [5, 6, 7, 8, 9, 10, 11], "colors": ["grey/navy", "black/black"],
      "cushioning": "medium", "weight_grams": 280},
     "Budget-friendly cushioned shoe for walking and light jogging, wide "
     "toe box, machine washable. Entry-level option for casual runners.",
     "30-day return if unworn, original box and tags required.",
     [2, 8]),

    (7, "UrbanPace Knit", "running_shoes", 3499, 35,
     {"brand": "Stridewell", "gender": "unisex", "use_case": "road running",
      "sizes_uk": [6, 7, 8, 9, 10], "colors": ["charcoal", "white/pink"],
      "cushioning": "medium", "weight_grams": 250},
     "Sock-fit knit upper running shoe for short-to-mid distance road runs, "
     "with a versatile look that doubles as a casual sneaker.",
     "30-day return if unworn, original box and tags required.",
     [1, 4]),

    (8, "SprintForce X", "running_shoes", 4499, 28,
     {"brand": "Stridewell", "gender": "unisex", "use_case": "interval training",
      "sizes_uk": [6, 7, 8, 9, 10, 11], "colors": ["red/black", "blue/silver"],
      "cushioning": "low", "weight_grams": 215},
     "Responsive low-profile trainer built for speed work, tempo runs, and "
     "track intervals, with a firmer foam for better ground feedback.",
     "30-day return if unworn, original box and tags required.",
     [6]),

    # --- Socks (9-12) ---
    (9, "Compression Run Socks 3-Pack", "socks", 499, 100,
     {"brand": "FlexFit", "material": "nylon-spandex blend", "pack_size": 3,
      "sizes": ["S/M", "L/XL"], "compression": "graduated"},
     "Graduated compression running socks sold in packs of 3, designed to "
     "reduce calf fatigue and improve circulation on longer runs.",
     "7-day exchange only for unopened packs (hygiene item).",
     [10, 11, 12]),

    (10, "Merino Wool Trail Socks", "socks", 599, 70,
     {"brand": "FlexFit", "material": "merino wool blend", "pack_size": 1,
      "sizes": ["S/M", "L/XL"], "use_case": "trail running"},
     "Cushioned merino wool trail running socks with reinforced heel and "
     "toe, naturally odor-resistant and warm for cooler trail runs.",
     "7-day exchange only for unopened packs (hygiene item).",
     [9]),

    (11, "No-Show Cushion Socks 5-Pack", "socks", 399, 120,
     {"brand": "Nimbus Sports", "material": "cotton-poly blend", "pack_size": 5,
      "sizes": ["S/M", "L/XL"], "style": "no-show"},
     "Everyday no-show running socks in a 5-pack, cushioned sole, "
     "silicone heel grip to prevent slipping inside the shoe.",
     "7-day exchange only for unopened packs (hygiene item).",
     [9, 12]),

    (12, "Anti-Blister Coolmax Socks", "socks", 449, 80,
     {"brand": "Zephyr Athletics", "material": "Coolmax polyester", "pack_size": 2,
      "sizes": ["S/M", "L/XL"], "feature": "double-layer anti-blister"},
     "Double-layer anti-blister running socks that reduce friction on long "
     "runs, moisture-wicking Coolmax fabric keeps feet dry.",
     "7-day exchange only for unopened packs (hygiene item).",
     [9, 11]),

    # --- Insoles (13-15) ---
    (13, "Orthotic Comfort Insoles", "insoles", 799, 50,
     {"brand": "SoleCare", "arch_support": "high", "sizes_uk": ["S", "M", "L", "XL"]},
     "Firm orthotic insoles with deep heel cup and high arch support, "
     "recommended for runners with flat feet or plantar fasciitis history.",
     "30-day return if unused, original packaging required.",
     [14, 15]),

    (14, "Gel Cushion Insoles", "insoles", 599, 65,
     {"brand": "SoleCare", "arch_support": "medium", "sizes_uk": ["S", "M", "L", "XL"]},
     "Gel-cushioned everyday insoles for extra shock absorption, "
     "trimmable to fit, works well in both running shoes and sneakers.",
     "30-day return if unused, original packaging required.",
     [13],),

    (15, "Arch Support Trail Insoles", "insoles", 899, 40,
     {"brand": "SoleCare", "arch_support": "high", "sizes_uk": ["S", "M", "L", "XL"],
      "use_case": "trail running"},
     "Rugged arch-support insoles built for trail shoes, with a stiffer "
     "shank for stability on uneven terrain.",
     "30-day return if unused, original packaging required.",
     [13, 14]),

    # --- Apparel tops (16-18) ---
    (16, "DryTech Running Tee", "apparel_top", 899, 90,
     {"brand": "Stridewell", "gender": "unisex", "material": "polyester mesh",
      "sizes": ["S", "M", "L", "XL", "XXL"]},
     "Moisture-wicking short-sleeve running tee with mesh side panels for "
     "ventilation, flatlock seams to prevent chafing.",
     "30-day return if unworn with tags attached.",
     [18]),

    (17, "Long Sleeve Thermal Run Top", "apparel_top", 1499, 55,
     {"brand": "Apex Run", "gender": "unisex", "material": "brushed thermal poly",
      "sizes": ["S", "M", "L", "XL"], "use_case": "cold weather"},
     "Brushed-interior thermal long sleeve top for cold-weather runs, "
     "thumbholes and a half-zip collar for temperature control.",
     "30-day return if unworn with tags attached.",
     [22]),

    (18, "Compression Base Layer Top", "apparel_top", 1299, 45,
     {"brand": "FlexFit", "gender": "unisex", "material": "nylon-spandex compression",
      "sizes": ["S", "M", "L", "XL"]},
     "Snug compression base layer top for muscle support during runs or as "
     "a layering piece underneath a jacket in cold weather.",
     "30-day return if unworn with tags attached.",
     [16]),

    # --- Apparel bottoms (19-21) ---
    (19, "5-inch Running Shorts", "apparel_bottom", 999, 75,
     {"brand": "Nimbus Sports", "gender": "unisex", "material": "lightweight ripstop",
      "sizes": ["S", "M", "L", "XL"], "inseam_inches": 5, "liner": True},
     "Lightweight 5-inch running shorts with a built-in brief liner and a "
     "zippered pocket for keys or a gel pack.",
     "30-day return if unworn with tags attached.",
     [21]),

    (20, "Compression Running Tights", "apparel_bottom", 1599, 50,
     {"brand": "Zephyr Athletics", "gender": "unisex", "material": "nylon-spandex compression",
      "sizes": ["S", "M", "L", "XL"], "length": "full"},
     "Full-length compression tights for muscle support on long runs and "
     "recovery days, with reflective ankle zips for low-light visibility.",
     "30-day return if unworn with tags attached.",
     [19],),

    (21, "Convertible Running Pants", "apparel_bottom", 1899, 30,
     {"brand": "Apex Run", "gender": "unisex", "material": "stretch woven",
      "sizes": ["S", "M", "L", "XL"], "feature": "zip-off legs"},
     "Convertible running pants with zip-off lower legs, adapting from full "
     "pants to shorts mid-run as temperature changes.",
     "30-day return if unworn with tags attached.",
     [19, 20]),

    # --- Outerwear (22-23) ---
    (22, "Windproof Running Jacket", "outerwear", 3499, 35,
     {"brand": "Apex Run", "gender": "unisex", "material": "windproof ripstop",
      "sizes": ["S", "M", "L", "XL"], "packable": True},
     "Packable windproof running jacket that stuffs into its own pocket, "
     "reflective trim for early morning or evening runs.",
     "30-day return if unworn with tags attached.",
     [23]),

    (23, "Reflective Rain Shell", "outerwear", 3999, 20,
     {"brand": "Stridewell", "gender": "unisex", "material": "waterproof ripstop",
      "sizes": ["S", "M", "L", "XL"], "waterproof": True},
     "Fully waterproof running shell with taped seams and 360-degree "
     "reflective detailing, built for rainy-season training.",
     "30-day return if unworn with tags attached.",
     [22]),

    # --- Accessories (24-26) ---
    (24, "Reflective Running Cap", "accessories", 349, 110,
     {"brand": "Nimbus Sports", "material": "quick-dry polyester", "adjustable": True},
     "Lightweight quick-dry running cap with a reflective strip and sweat "
     "wicking headband for sun and rain protection.",
     "7-day exchange only for unopened items (hygiene item).",
     [25]),

    (25, "Runner's Waist Belt with Pouch", "accessories", 599, 60,
     {"brand": "FlexFit", "material": "elastic neoprene", "pouch_capacity_ml": 250},
     "Bounce-free elastic waist belt with a zippered pouch for a phone, "
     "keys, and gels, adjustable to fit most waist sizes.",
     "30-day return if unused, original packaging required.",
     [27]),

    (26, "Elastic No-Tie Laces", "accessories", 299, 150,
     {"brand": "SoleCare", "material": "elastic silicone", "one_size": True},
     "Elastic no-tie lace system that converts any lace-up shoe to a "
     "slip-on, popular for triathlon transitions and quick shoe changes.",
     "30-day return if unused, original packaging required.",
     [],),

    # --- Hydration (27) ---
    (27, "500ml Soft Flask Handheld", "hydration", 799, 55,
     {"brand": "HydroRun", "capacity_ml": 500, "material": "BPA-free soft flask",
      "hand_strap": True},
     "Collapsible 500ml soft flask with an ergonomic hand strap and bite "
     "valve, shrinks as you drink so it doesn't slosh.",
     "30-day return if unused, original packaging required.",
     [25]),

    # --- Wearable tech (28) ---
    (28, "GPS Running Watch Lite", "wearable_tech", 4999, 25,
     {"brand": "PulseTrack", "battery_life_days": 7, "gps": True,
      "heart_rate_monitor": "wrist-based", "water_resistance": "5 ATM"},
     "Entry-level GPS running watch with wrist-based heart rate, 7-day "
     "battery life, and pace/distance tracking for road and trail runs.",
     "15-day return if unopened, electronics warranty via manufacturer.",
     [],),
]

# ---------------------------------------------------------------------------
# Co-purchase stats: (product_a_id, product_b_id, rate). Rates model
# realistic merchandising patterns: shoes pair strongly with socks and
# insoles, weakly with unrelated accessories; apparel pairs across
# tops/bottoms/outerwear; a few plausible cross-category pairs.
# ---------------------------------------------------------------------------

CO_PURCHASE_STATS = [
    # Shoes -> socks (strong signal, varies a bit by shoe)
    (1, 9, 0.68), (1, 11, 0.41),
    (2, 10, 0.61), (2, 12, 0.35),
    (3, 9, 0.71), (3, 12, 0.44),
    (4, 9, 0.66), (4, 11, 0.39),
    (5, 9, 0.58),
    (6, 11, 0.52),
    (7, 9, 0.49),
    (8, 12, 0.55),
    # Shoes -> insoles (moderate)
    (1, 14, 0.33), (3, 13, 0.29), (5, 13, 0.24), (6, 14, 0.31),
    # Shoes -> laces (weak, niche)
    (5, 26, 0.14), (8, 26, 0.11),
    # Trail shoe -> trail insoles (strong, category match)
    (2, 15, 0.45),
    # Apparel: shorts + tee, tights + top
    (19, 16, 0.53), (19, 18, 0.31), (20, 18, 0.47), (20, 17, 0.28),
    # Jacket + tights (cold weather combo)
    (22, 20, 0.26), (23, 17, 0.22),
    # Watch + hydration flask
    (28, 27, 0.19),
    # Cap + waist belt (accessory bundle)
    (24, 25, 0.23),
]

# ---------------------------------------------------------------------------
# Merchant policy. Assumptions stated in the task summary.
# ---------------------------------------------------------------------------

MERCHANT_POLICY = {
    "max_discount_pct": Decimal("15.00"),
    "max_autonomous_purchase_amount": Decimal("2000.00"),
    "allowed_payment_methods": ["card", "upi"],
    "approval_required_above": Decimal("2000.00"),
}

# ---------------------------------------------------------------------------
# Agents.
# ---------------------------------------------------------------------------

AGENTS = [
    {
        "type": "human_session",
        "name": "Demo Shopper (human)",
        "budget_limit": Decimal("50000.00"),
        "spent_so_far": Decimal("0.00"),
        "permissions": {"can_apply_discount": True, "requires_approval": False},
    },
    {
        # budget_limit is well above any single item's price so the agent
        # never runs out of *budget*, but items priced above
        # approval_required_above (2000) still route through the
        # approval-request path per merchant_policy regardless of budget
        # headroom — that's the demo trigger, not the budget ceiling.
        "type": "ai_agent",
        "name": "Shopping Assistant Agent",
        "budget_limit": Decimal("5000.00"),
        "spent_so_far": Decimal("0.00"),
        "permissions": {"can_apply_discount": False, "requires_approval": True},
    },
]


def main() -> None:
    with psycopg.connect(get_database_url()) as conn:
        conn.execute("TRUNCATE co_purchase_stat, product, merchant_policy RESTART IDENTITY CASCADE")
        conn.execute("DELETE FROM agent WHERE name IN (%s, %s)",
                      (AGENTS[0]["name"], AGENTS[1]["name"]))

        for (pid, name, category, price, stock, attrs, desc, policy, subs) in PRODUCTS:
            conn.execute(
                """
                INSERT INTO product
                    (id, name, category, price, stock, structured_attributes,
                     semantic_description, return_policy, substitute_ids)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (pid, name, category, Decimal(price), stock, psycopg.types.json.Jsonb(attrs),
                 desc, policy, subs),
            )
        conn.execute("SELECT setval('product_id_seq', (SELECT max(id) FROM product))")
        print(f"inserted {len(PRODUCTS)} products")

        for (a, b, rate) in CO_PURCHASE_STATS:
            conn.execute(
                """
                INSERT INTO co_purchase_stat (product_a_id, product_b_id, co_purchase_rate)
                VALUES (%s, %s, %s)
                """,
                (a, b, rate),
            )
        print(f"inserted {len(CO_PURCHASE_STATS)} co_purchase_stat rows")

        conn.execute(
            """
            INSERT INTO merchant_policy
                (max_discount_pct, max_autonomous_purchase_amount,
                 allowed_payment_methods, approval_required_above)
            VALUES (%s, %s, %s, %s)
            """,
            (MERCHANT_POLICY["max_discount_pct"],
             MERCHANT_POLICY["max_autonomous_purchase_amount"],
             MERCHANT_POLICY["allowed_payment_methods"],
             MERCHANT_POLICY["approval_required_above"]),
        )
        print("inserted 1 merchant_policy row")

        for a in AGENTS:
            conn.execute(
                """
                INSERT INTO agent (type, name, budget_limit, spent_so_far, permissions)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (a["type"], a["name"], a["budget_limit"], a["spent_so_far"],
                 psycopg.types.json.Jsonb(a["permissions"])),
            )
        print(f"inserted {len(AGENTS)} agent rows")


if __name__ == "__main__":
    main()
