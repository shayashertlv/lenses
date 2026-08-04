"""Comprehensive tests for the matching algorithm across all entry points.

Tests tag_matcher (core engine), face_analysis/inventory_matcher (Smart Fit),
and optimal_configuration/search_engine (Free Search / natural language).
All three must produce consistent results from the same underlying engine.
"""

import copy
import json
import os
import sys
import unittest

# ── Path setup so all modules are importable ──────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "face_analysis"))
sys.path.insert(0, os.path.join(ROOT, "optimal_configuration"))

from tag_matcher import (
    _MISSING_TAG_CREDIT,
    _FILTER_FIELD_SET,
    FIELD_WEIGHTS,
    _norm_color,
    _norm_material,
    _norm_shape,
    _visual_signature,
    _is_dedicated_sport,
    _FILTER_FIELD_SET,
    compute_component_scores,
    compute_tag_score,
    preferences_to_query_tags,
    rank_products,
)
from face_analysis.inventory_matcher import InventoryMatcher
from optimal_configuration.search_engine import GlassesSearchEngine

# ── Load real catalog ─────────────────────────────────────────────────────
CATALOG_JSON = os.path.join(ROOT, "lenses", "catalog", "catalog.json")
with open(CATALOG_JSON, "r", encoding="utf-8") as f:
    CATALOG_DATA = json.load(f)
PRODUCTS = CATALOG_DATA["products"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. COLOR NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class TestColorNormalization(unittest.TestCase):
    """Verify color aliases resolve to canonical names."""

    def test_dark_variants_to_black(self):
        for alias in ("dark-grey", "dark grey", "dark-gray", "dark gray",
                       "jet-black", "jet black"):
            self.assertEqual(_norm_color(alias), "black", f"{alias!r} should → black")

    def test_brown_family(self):
        for alias in ("amber", "cognac", "caramel", "chocolate", "espresso",
                       "tan", "mocha", "honey", "bronze", "copper"):
            self.assertEqual(_norm_color(alias), "brown", f"{alias!r} should → brown")

    def test_havana_to_tortoiseshell(self):
        self.assertEqual(_norm_color("havana"), "tortoiseshell")
        self.assertEqual(_norm_color("havana-brown"), "tortoiseshell")

    def test_silver_gray_family(self):
        self.assertEqual(_norm_color("gunmetal"), "silver")
        self.assertEqual(_norm_color("charcoal"), "gray")
        self.assertEqual(_norm_color("slate"), "gray")
        self.assertEqual(_norm_color("pewter"), "silver")
        self.assertEqual(_norm_color("chrome"), "silver")
        self.assertEqual(_norm_color("grey"), "gray")

    def test_blue_family(self):
        for alias in ("navy", "navy-blue", "cobalt", "teal", "dark-blue"):
            self.assertEqual(_norm_color(alias), "blue", f"{alias!r} should → blue")

    def test_green_family(self):
        for alias in ("olive", "forest-green", "emerald", "sage"):
            self.assertEqual(_norm_color(alias), "green", f"{alias!r} should → green")

    def test_gold_family(self):
        for alias in ("champagne", "brass", "antique-gold"):
            self.assertEqual(_norm_color(alias), "gold", f"{alias!r} should → gold")

    def test_transparent_family(self):
        for alias in ("crystal", "clear", "nude"):
            self.assertEqual(_norm_color(alias), "transparent", f"{alias!r} should → transparent")

    def test_adjective_stripping(self):
        self.assertEqual(_norm_color("matte-black"), "black")
        self.assertEqual(_norm_color("rubber-black"), "black")
        self.assertEqual(_norm_color("glossy-silver"), "silver")
        self.assertEqual(_norm_color("gradient-brown"), "brown")
        self.assertEqual(_norm_color("polished-gold"), "gold")
        self.assertEqual(_norm_color("mirrored-silver"), "silver")

    def test_adjective_plus_alias(self):
        """Adjective stripping + alias resolution should chain."""
        self.assertEqual(_norm_color("matte-gunmetal"), "silver")
        self.assertEqual(_norm_color("polished-champagne"), "gold")

    def test_identity_passthrough(self):
        """Canonical names should pass through unchanged."""
        for canonical in ("black", "silver", "gold", "blue", "brown", "gray"):
            self.assertEqual(_norm_color(canonical), canonical)

    def test_case_insensitive(self):
        self.assertEqual(_norm_color("NAVY"), "blue")
        self.assertEqual(_norm_color("Gunmetal"), "silver")


# ═══════════════════════════════════════════════════════════════════════════
# 2. SHAPE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class TestShapeNormalization(unittest.TestCase):

    def test_butterfly_to_cat_eye(self):
        self.assertEqual(_norm_shape("butterfly"), "cat-eye")

    def test_pilot_to_aviator(self):
        self.assertEqual(_norm_shape("pilot"), "aviator")
        self.assertEqual(_norm_shape("teardrop"), "aviator")

    def test_oversized_variants(self):
        self.assertEqual(_norm_shape("oversized-round"), "round")
        self.assertEqual(_norm_shape("oversized-square"), "square")

    def test_wrap_variants(self):
        self.assertEqual(_norm_shape("shield"), "wrap")
        self.assertEqual(_norm_shape("curved-wrap"), "wrap")

    def test_identity_passthrough(self):
        for shape in ("round", "square", "rectangular", "oval", "cat-eye", "aviator"):
            self.assertEqual(_norm_shape(shape), shape)


# ═══════════════════════════════════════════════════════════════════════════
# 2b. MATERIAL NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class TestMaterialNormalization(unittest.TestCase):

    def test_metal_family(self):
        for alias in ("stainless-steel", "mixed-metal", "mixed-metal-acetate",
                       "mixed-metal-plastic", "mixed-metal-nylon",
                       "mixed-metal-carbon", "mixed-metal-injected"):
            self.assertEqual(_norm_material(alias), "metal", f"{alias!r} should -> metal")

    def test_plastic_family(self):
        for alias in ("propionate", "bio-injected", "recycled-injected", "o-matter"):
            self.assertEqual(_norm_material(alias), "plastic", f"{alias!r} should -> plastic")

    def test_acetate_family(self):
        self.assertEqual(_norm_material("recycled-acetate"), "acetate")

    def test_nylon_family(self):
        self.assertEqual(_norm_material("bio-nylon"), "nylon")

    def test_identity_passthrough(self):
        for mat in ("metal", "acetate", "plastic", "titanium", "carbon-fiber", "nylon"):
            self.assertEqual(_norm_material(mat), mat)

    def test_case_insensitive(self):
        self.assertEqual(_norm_material("O-Matter"), "plastic")
        self.assertEqual(_norm_material("Stainless-Steel"), "metal")

    def test_material_normalization_in_scoring(self):
        """stainless-steel product should match metal query via normalization."""
        q = {"frame": {"material": "metal"}, "lenses": {}, "style": {}}
        p = {"frame": {"material": "stainless-steel"}, "lenses": {}, "style": {}}
        score = compute_tag_score(q, p)
        self.assertAlmostEqual(score, 100.0)


# ═══════════════════════════════════════════════════════════════════════════
# 3. SCORING ENGINE (compute_tag_score)
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeTagScore(unittest.TestCase):

    def _make_tags(self, **kw):
        """Helper — build a minimal tags dict from keyword args."""
        tags = {"frame": {}, "lenses": {}, "style": {}}
        for key, val in kw.items():
            cat, field = key.split(".", 1)
            tags[cat][field] = val
        return tags

    def test_perfect_match_soft_fields(self):
        """Perfect match on soft fields only → 100."""
        tags = self._make_tags(**{
            "frame.material": "metal",
            "frame.rim_type": "full-rim",
            "style.aesthetic": ["classic"],
        })
        score = compute_tag_score(tags, tags)
        self.assertAlmostEqual(score, 100.0)

    def test_perfect_match_with_filter_fields(self):
        """Filter fields need active_filter_fields to earn credit."""
        tags = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["black"],
            "frame.rim_type": "full-rim",
            "style.aesthetic": ["classic"],
        })
        active = [("frame", "color")]
        score = compute_tag_score(tags, tags, active_filter_fields=active)
        self.assertAlmostEqual(score, 100.0)

    def test_zero_overlap(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.rim_type": "full-rim",
        })
        product = self._make_tags(**{
            "frame.material": "acetate",
            "frame.rim_type": "rimless",
        })
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 0.0)

    def test_partial_overlap(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.rim_type": "full-rim",
        })
        product = self._make_tags(**{
            "frame.material": "metal",
            "frame.rim_type": "rimless",
        })
        # material matches (weight 6), rim_type doesn't (weight 5)
        # score = 6 / 11 * 100 ≈ 54.5
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 6.0 / 11.0 * 100.0)

    def test_missing_product_tag_gets_partial_credit(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.rim_type": "full-rim",
        })
        product = self._make_tags(**{
            "frame.material": "metal",
            # rim_type is missing entirely
        })
        # material matches (6 * 1.0), rim_type missing (5 * 0.25)
        # score = (6.0 + 1.25) / 11.0 * 100
        score = compute_tag_score(query, product)
        expected = (6.0 + 5.0 * _MISSING_TAG_CREDIT) / 11.0 * 100.0
        self.assertAlmostEqual(score, expected)

    def test_missing_tag_scores_higher_than_mismatch(self):
        query = self._make_tags(**{"frame.rim_type": "full-rim"})
        missing = self._make_tags()           # no rim_type at all
        mismatch = self._make_tags(**{"frame.rim_type": "rimless"})
        self.assertGreater(
            compute_tag_score(query, missing),
            compute_tag_score(query, mismatch),
        )

    def test_no_query_fields_returns_100(self):
        """Empty query → all products score 100."""
        query = self._make_tags()
        product = self._make_tags(**{"frame.material": "metal"})
        self.assertEqual(compute_tag_score(query, product), 100.0)

    def test_filter_field_without_active_list_gets_zero(self):
        """Filter fields not in active_filter_fields earn 0 credit."""
        query = self._make_tags(**{"frame.color": ["black"]})
        product = self._make_tags(**{"frame.color": ["black"]})
        # frame.color is a filter field; without active_filter_fields, it's 0
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 0.0)

    def test_filter_field_with_active_list_gets_full(self):
        """Filter fields in active_filter_fields (not relaxed) earn full credit."""
        query = self._make_tags(**{"frame.color": ["black"]})
        product = self._make_tags(**{"frame.color": ["black"]})
        score = compute_tag_score(query, product,
                                  active_filter_fields=[("frame", "color")])
        self.assertAlmostEqual(score, 100.0)

    def test_relaxed_filter_field_gets_zero(self):
        """Relaxed filter fields earn 0 credit."""
        query = self._make_tags(**{"frame.color": ["black"]})
        product = self._make_tags(**{"frame.color": ["gold"]})
        score = compute_tag_score(query, product,
                                  active_filter_fields=[("frame", "color")],
                                  relaxed_fields=[("frame", "color")])
        self.assertAlmostEqual(score, 0.0)

    def test_multi_value_partial_overlap(self):
        query = self._make_tags(**{"style.aesthetic": ["classic", "modern"]})
        product = self._make_tags(**{"style.aesthetic": ["classic", "vintage"]})
        # overlap 1 out of 2 query values → weight 5 * 0.5 = 2.5 / 5 * 100 = 50
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 50.0)


# ═══════════════════════════════════════════════════════════════════════════
# 4. COMPONENT SUB-SCORES
# ═══════════════════════════════════════════════════════════════════════════

class TestComponentScores(unittest.TestCase):

    def _make_tags(self, **kw):
        tags = {"frame": {}, "lenses": {}, "style": {}}
        for key, val in kw.items():
            cat, field = key.split(".", 1)
            tags[cat][field] = val
        return tags

    def test_returns_all_three_components(self):
        q = self._make_tags(**{"frame.material": "metal"})
        p = self._make_tags(**{"frame.material": "metal"})
        sub = compute_component_scores(q, p)
        self.assertIn("fit", sub)
        self.assertIn("style", sub)
        self.assertIn("color", sub)

    def test_scores_are_deterministic(self):
        q = self._make_tags(**{
            "frame.material": "metal",
            "style.aesthetic": ["modern"],
            "lenses.size": "medium",
        })
        p = self._make_tags(**{
            "frame.material": "metal",
            "style.aesthetic": ["modern"],
            "lenses.size": "large",
        })
        sub1 = compute_component_scores(q, p)
        sub2 = compute_component_scores(q, p)
        self.assertEqual(sub1, sub2)

    def test_perfect_match_all_100(self):
        tags = self._make_tags(**{
            "frame.material": "metal",
            "frame.thickness": "thin",
            "frame.rim_type": "full-rim",
            "frame.finish": "matte",
            "style.aesthetic": ["classic"],
            "style.occasion": ["everyday"],
            "style.face_shape_fit": ["oval"],
            "lenses.size": "medium",
        })
        sub = compute_component_scores(tags, tags)
        for component, val in sub.items():
            self.assertAlmostEqual(val, 100.0, msg=f"{component} should be 100")

    def test_color_component_independent(self):
        """Color component should only reflect color-group fields."""
        q = self._make_tags(**{
            "frame.material": "metal",
            "frame.thickness": "thin",
            "style.aesthetic": ["modern"],  # style component, not color
        })
        p_color_match = self._make_tags(**{
            "frame.material": "metal",
            "frame.thickness": "thin",
            "style.aesthetic": ["vintage"],  # mismatch on style
        })
        sub = compute_component_scores(q, p_color_match)
        self.assertAlmostEqual(sub["color"], 100.0)
        self.assertLess(sub["style"], 100.0)

    def test_fallback_to_overall_when_no_fields(self):
        """If query has no fields in a component group, fall back to overall."""
        q = self._make_tags(**{"frame.material": "metal"})  # only in color component
        p = self._make_tags(**{"frame.material": "metal"})
        sub = compute_component_scores(q, p)
        overall = compute_tag_score(q, p)
        # fit and style have no queried fields → should equal overall
        self.assertAlmostEqual(sub["fit"], overall)
        self.assertAlmostEqual(sub["style"], overall)


# ═══════════════════════════════════════════════════════════════════════════
# 4b. FILTER FIELDS THROUGH THE BREAKDOWN
#
# The gap that let the free-search cards ship reading FIT 0 STYLE 0 COLOUR 0
# under a score of 100.  Every test above this line queries soft fields only,
# so the filter-field branch of compute_component_scores — the one that credits
# nothing unless it is told which filters were active — had no coverage at all,
# and the in-range test it did have accepts 0.0 happily.
#
# Five of the eleven fields the free-search form can set are filter fields, and
# they are the prominent ones: shape, frame colour, lens colour, lens type,
# gender.  Pick a shape, press go, and every number on the page was a zero.
# ═══════════════════════════════════════════════════════════════════════════

class TestFilterFieldsAreScored(unittest.TestCase):

    FILTER_FIELDS = [
        ("frame_shape", "hexagonal", ("lenses", "shape")),
        ("frame_color", "black", ("frame", "color")),
        ("lens_color", "clear", ("lenses", "color")),
        ("gender", "men", ("style", "gender_target")),
    ]

    def test_the_fields_under_test_really_are_filter_fields(self):
        """Guards the premise. If these stop being filter fields the tests
        below keep passing while covering nothing."""
        for _, _, tag in self.FILTER_FIELDS:
            self.assertIn(tag, _FILTER_FIELD_SET, f"{tag} is no longer a filter field")

    def test_a_matched_filter_field_earns_its_weight(self):
        """The unit-level contract: told which filters were active, a matching
        filter field scores. Not told, it silently earns nothing."""
        q = {"frame": {}, "lenses": {"shape": "hexagonal"}, "style": {}}
        p = next(p for p in PRODUCTS
                 if _norm_shape(p["tags"]["lenses"].get("shape")) == "hexagonal")

        uninformed = compute_component_scores(q, p["tags"])
        informed = compute_component_scores(q, p["tags"],
                                            active_filter_fields=[("lenses", "shape")])
        self.assertEqual({round(v, 1) for v in uninformed.values()}, {0.0})
        for component, value in informed.items():
            self.assertAlmostEqual(value, 100.0, msg=f"{component} should be 100")

    def test_rank_products_never_returns_a_zero_breakdown_under_a_high_score(self):
        """The regression test. rank_products holds the filter context, so the
        breakdown it returns must agree with the score it returns."""
        for field, value, tag in self.FILTER_FIELDS:
            with self.subTest(field=field):
                q = preferences_to_query_tags({field: value})
                filters = {"in_stock_only": True}
                if field == "gender":
                    filters["gender"] = value
                results = rank_products(q, PRODUCTS, top_k=3, filters=filters)
                self.assertTrue(results, f"{field}={value} matched nothing")
                for product, score, components in results:
                    if score < 50.0:
                        continue
                    self.assertGreater(
                        max(components.values()), 0.0,
                        f"{field}={value}: {product['name']} scored {score} overall "
                        f"but {components} across the board",
                    )

    def test_breakdown_agrees_with_the_score_on_every_form_field(self):
        """One field at a time, filter and soft alike: a product that scores
        100 overall cannot be 0 on all three parts of that same score."""
        every_field = self.FILTER_FIELDS + [
            ("frame_material", "acetate", ("frame", "material")),
            ("frame_thickness", "thin", ("frame", "thickness")),
            ("rim_type", "full-rim", ("frame", "rim_type")),
            ("lens_size", "medium", ("lenses", "size")),
            ("aesthetic", "classic", ("style", "aesthetic")),
            ("occasion", "everyday", ("style", "occasion")),
        ]
        for field, value, _ in every_field:
            with self.subTest(field=field):
                q = preferences_to_query_tags({field: value})
                filters = {"in_stock_only": True}
                if field == "gender":
                    filters["gender"] = value
                results = rank_products(q, PRODUCTS, top_k=1, filters=filters)
                if not results:
                    continue
                product, score, components = results[0]
                if score < 99.0:
                    continue
                for name, part in components.items():
                    self.assertGreater(
                        part, 0.0,
                        f"{field}={value}: {product['name']} scored {score} but "
                        f"{name}={part}",
                    )

    def test_match_carries_the_breakdown_as_a_field(self):
        """Three fields, named. A caller that ignores the breakdown has to say
        so — which is the whole point of not hiding it on the tuple."""
        q = preferences_to_query_tags({"frame_shape": "round"})
        results = rank_products(q, PRODUCTS, top_k=1, filters={"in_stock_only": True})
        self.assertTrue(results)
        match = results[0]
        self.assertEqual(len(match), 3)
        self.assertIsInstance(match.product, dict)
        self.assertIsInstance(match.score, float)
        self.assertEqual(set(match.components), {"fit", "style", "color"})
        self.assertEqual(tuple(match), (match.product, match.score, match.components))

    def test_breakdown_is_scored_against_the_folded_query(self):
        """rank_products folds frame.shape into lenses.shape before scoring.
        A breakdown computed outside, against the caller's unfolded tags, would
        drop the shape signal — so the fold has to be inside the same scope."""
        q = {"frame": {"shape": "round"}, "lenses": {}, "style": {}}
        results = rank_products(q, PRODUCTS, top_k=1, filters={"in_stock_only": True})
        self.assertTrue(results)
        product, score, components = results[0]
        self.assertGreater(score, 0.0)
        self.assertGreater(max(components.values()), 0.0,
                           f"{product['name']}: {score} overall, {components} in parts")


# ═══════════════════════════════════════════════════════════════════════════
# 5. RANK_PRODUCTS (full pipeline)
# ═══════════════════════════════════════════════════════════════════════════

class TestRankProducts(unittest.TestCase):

    def test_returns_up_to_top_k(self):
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=5)
        self.assertLessEqual(len(results), 5)
        self.assertGreater(len(results), 0)

    def test_results_sorted_descending(self):
        query = {"frame": {"material": "metal"}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=10, min_score=0)
        scores = [s for _, s, _ in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_gender_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "women"}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0)
        for product, _, _ in results:
            gender = product["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("women", "unisex"),
                          f"{product['name']} has gender_target={gender}")

    def test_gender_via_filters(self):
        """Gender passed in filters dict should work same as in query_tags."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0,
                                filters={"gender": "men"})
        for product, _, _ in results:
            gender = product["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("men", "unisex"),
                          f"{product['name']} has gender_target={gender}")

    def test_sport_isolation_excludes_sport(self):
        """Non-sport query should never return sport products.

        Sport membership is keyed on style.occasion == "sport" (not lenses.type)."""
        query = {"frame": {}, "lenses": {"type": ["sunglasses"]}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0)
        for product, _, _ in results:
            occasion = product["tags"]["style"].get("occasion", [])
            if isinstance(occasion, str):
                occasion = [occasion]
            self.assertNotIn("sport", occasion,
                             f"{product['name']} is a sport product but was returned")

    def test_sport_isolation_returns_sport(self):
        """Sport query should only return sport products (by style.occasion)."""
        query = {"frame": {}, "lenses": {"type": ["sport"]}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0)
        if results:  # catalog may not have sport products
            for product, _, _ in results:
                occasion = product["tags"]["style"].get("occasion", [])
                if isinstance(occasion, str):
                    occasion = [occasion]
                self.assertIn("sport", occasion,
                              f"{product['name']} is not a sport product")

    def test_price_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0,
                                filters={"max_price": 500})
        for product, _, _ in results:
            price = product["tags"]["product"]["price"]
            self.assertLessEqual(price, 500, f"{product['name']} costs {price}")

    def test_in_stock_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0,
                                filters={"in_stock_only": True})
        for product, _, _ in results:
            self.assertTrue(product["tags"]["product"].get("in_stock", False),
                            f"{product['name']} is not in stock")

    def test_min_score_threshold(self):
        """Results are sorted by score descending."""
        query = {"frame": {"material": "carbon-fiber", "rim_type": "rimless"},
                 "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=50)
        scores = [s for _, s, _ in results]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         "Results must be sorted by score descending")

    def test_query_tags_not_mutated(self):
        """rank_products must never modify the caller's query_tags."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        original = copy.deepcopy(query)
        rank_products(query, PRODUCTS, top_k=3,
                      filters={"gender": "men", "in_stock_only": True})
        self.assertEqual(query, original)

    def test_shape_normalization_in_filter(self):
        """Query for 'butterfly' should match catalog products tagged 'cat-eye'."""
        q_butterfly = {"frame": {}, "lenses": {"shape": "butterfly"}, "style": {}}
        q_cat_eye = {"frame": {}, "lenses": {"shape": "cat-eye"}, "style": {}}
        r_butterfly = rank_products(q_butterfly, PRODUCTS, top_k=10, min_score=0)
        r_cat_eye = rank_products(q_cat_eye, PRODUCTS, top_k=10, min_score=0)
        butterfly_ids = {p["id"] for p, _, _ in r_butterfly}
        cat_eye_ids = {p["id"] for p, _, _ in r_cat_eye}
        self.assertTrue(butterfly_ids <= cat_eye_ids,
                        f"butterfly IDs not subset of cat-eye: {butterfly_ids - cat_eye_ids}")

    def test_color_normalization_in_filter(self):
        """Query for 'gunmetal' frame color should match 'silver' equivalently."""
        q = {"frame": {"color": ["gunmetal"]}, "lenses": {}, "style": {}}
        results = rank_products(q, PRODUCTS, top_k=10, min_score=0)
        self.assertGreater(len(results), 0)

    def test_graceful_fallback_on_impossible_filter(self):
        """Filters that would empty the pool should be skipped (except sport)."""
        query = {
            "frame": {},
            "lenses": {"shape": "NONEXISTENT_SHAPE_XYZ"},
            "style": {"gender_target": "men"},
        }
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0)
        self.assertGreater(len(results), 0)

    def test_relaxed_filter_scores_lower(self):
        """When a filter is relaxed for the pool, a product that STILL matches
        the relaxed field earns full credit while one that does NOT match earns
        zero — so the matching product scores higher.  (Per-product credit is
        preserved regardless of pool-wide relaxation.)"""
        q = {"frame": {}, "lenses": {"shape": "round"}, "style": {}}
        match = {"frame": {}, "lenses": {"shape": "round"}, "style": {}}
        no_match = {"frame": {}, "lenses": {"shape": "square"}, "style": {}}
        # Filter was relaxed pool-wide; score each product individually.
        score_match = compute_tag_score(
            q, match,
            active_filter_fields=[("lenses", "shape")],
            relaxed_fields=[("lenses", "shape")],
        )
        score_no_match = compute_tag_score(
            q, no_match,
            active_filter_fields=[("lenses", "shape")],
            relaxed_fields=[("lenses", "shape")],
        )
        self.assertGreater(score_match, score_no_match,
                           "Relaxed filter: matching product should score higher")

    def test_relaxed_filter_in_full_pipeline(self):
        """End-to-end: when all products fail a filter, it relaxes and
        products still get returned (graceful fallback works)."""
        products = [
            {"id": "A", "name": "A", "image": "a.jpg", "tags": {
                "frame": {}, "lenses": {"type": ["clear"], "shape": "round"},
                "style": {"gender_target": "unisex"},
                "product": {"in_stock": True, "price": 100},
            }},
            {"id": "B", "name": "B", "image": "b.jpg", "tags": {
                "frame": {}, "lenses": {"type": ["clear"], "shape": "square"},
                "style": {"gender_target": "unisex"},
                "product": {"in_stock": True, "price": 100},
            }},
        ]
        query = {"frame": {}, "lenses": {"shape": "hexagonal"}, "style": {}}
        results = rank_products(query, products, top_k=2, min_score=0)
        self.assertEqual(len(results), 2)

    def test_empty_query_returns_results(self):
        """Empty query should return top-k products (all score 100)."""
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0)
        self.assertEqual(len(results), 3)
        for _, score, _ in results:
            self.assertAlmostEqual(score, 100.0)


# ═══════════════════════════════════════════════════════════════════════════
# 6. PREFERENCES_TO_QUERY_TAGS (Free Search input conversion)
# ═══════════════════════════════════════════════════════════════════════════

class TestPreferencesToQueryTags(unittest.TestCase):

    def test_full_preferences(self):
        prefs = {
            "frame_shape": "round",
            "frame_color": ["black"],
            "frame_material": "metal",
            "rim_type": "full-rim",
            "lens_type": ["sunglasses"],
            "gender": "women",
            "aesthetic": ["vintage"],
        }
        tags = preferences_to_query_tags(prefs)
        self.assertEqual(tags["lenses"]["shape"], "round")
        self.assertEqual(tags["frame"]["color"], ["black"])
        self.assertEqual(tags["frame"]["material"], "metal")
        self.assertEqual(tags["frame"]["rim_type"], "full-rim")
        self.assertEqual(tags["lenses"]["type"], ["sunglasses"])
        self.assertEqual(tags["style"]["gender_target"], "women")
        self.assertEqual(tags["style"]["aesthetic"], ["vintage"])

    def test_empty_preferences(self):
        tags = preferences_to_query_tags({})
        self.assertEqual(tags, {"frame": {}, "lenses": {}, "style": {}})

    def test_partial_preferences(self):
        tags = preferences_to_query_tags({"gender": "men"})
        self.assertEqual(tags["style"]["gender_target"], "men")
        self.assertEqual(tags["frame"], {})
        self.assertEqual(tags["lenses"], {})


# ═══════════════════════════════════════════════════════════════════════════
# 7. INVENTORY MATCHER (face_analysis entry point)
# ═══════════════════════════════════════════════════════════════════════════

class TestInventoryMatcher(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.matcher = InventoryMatcher(
            catalog_dir=os.path.join(ROOT, "lenses", "catalog"),
            catalog_data=CATALOG_DATA,
        )

    def test_basic_match(self):
        recommended_tags = {
            "frame": {"material": "metal", "color": ["black"], "rim_type": "full-rim",
                       "thickness": "thin", "finish": "matte"},
            "lenses": {"type": ["clear"], "color": ["clear"], "shape": "rectangular",
                       "size": "medium"},
            "style": {"aesthetic": ["classic", "professional"], "gender_target": "men",
                       "face_shape_fit": ["oval"], "occasion": ["everyday", "office"]},
        }
        result = self.matcher.match(recommended_tags, top_k=3, gender="men")
        self.assertTrue(result.success)
        self.assertGreater(len(result.matches), 0)
        self.assertLessEqual(len(result.matches), 3)

    def test_gender_filtering(self):
        recommended_tags = {
            "frame": {"color": ["black"]},
            "lenses": {},
            "style": {},
        }
        result = self.matcher.match(recommended_tags, top_k=50, gender="women")
        self.assertTrue(result.success)
        for product, _, _ in result.matches:
            gender = product["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("women", "unisex"))

    def test_does_not_mutate_recommended_tags(self):
        recommended_tags = {
            "frame": {"color": ["black"]},
            "lenses": {},
            "style": {},
        }
        original = copy.deepcopy(recommended_tags)
        self.matcher.match(recommended_tags, top_k=3, gender="men")
        self.assertEqual(recommended_tags, original)

    def test_scores_in_valid_range(self):
        recommended_tags = {
            "frame": {"material": "acetate"},
            "lenses": {"type": ["sunglasses"]},
            "style": {"aesthetic": ["modern"]},
        }
        result = self.matcher.match(recommended_tags, top_k=5)
        for _, score, _ in result.matches:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)


# ═══════════════════════════════════════════════════════════════════════════
# 8. GLASSES SEARCH ENGINE (optimal_configuration entry point)
# ═══════════════════════════════════════════════════════════════════════════

class TestGlassesSearchEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = GlassesSearchEngine(catalog_path=CATALOG_JSON)

    def test_basic_search(self):
        query_tags = {
            "frame": {"shape": "round", "color": ["gold"], "material": "metal"},
            "lenses": {"type": ["sunglasses"]},
            "style": {"aesthetic": ["vintage"], "gender_target": "women"},
        }
        results = self.engine.search(query_tags, top_k=5)
        self.assertGreater(len(results), 0)
        self.assertLessEqual(len(results), 5)

    def test_with_filters(self):
        query_tags = {
            "frame": {"color": ["black"]},
            "lenses": {},
            "style": {},
        }
        results = self.engine.search(
            query_tags, top_k=10,
            filters={"max_price": 600, "in_stock_only": True, "gender": "men"},
        )
        for product, _, _ in results:
            self.assertLessEqual(product["tags"]["product"]["price"], 600)
            self.assertTrue(product["tags"]["product"].get("in_stock", False))
            self.assertIn(product["tags"]["style"]["gender_target"],
                          ("men", "unisex"))

    def test_results_sorted_descending(self):
        query_tags = {
            "frame": {"material": "acetate", "color": ["tortoiseshell"]},
            "lenses": {},
            "style": {},
        }
        results = self.engine.search(query_tags, top_k=10)
        scores = [s for _, s, _ in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


# ═══════════════════════════════════════════════════════════════════════════
# 9. CROSS-ENTRY-POINT CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossEntryPointConsistency(unittest.TestCase):
    """Verify all three entry points use the same engine and produce
    identical results for the same query."""

    @classmethod
    def setUpClass(cls):
        cls.matcher = InventoryMatcher(
            catalog_dir=os.path.join(ROOT, "lenses", "catalog"),
            catalog_data=CATALOG_DATA,
        )
        cls.engine = GlassesSearchEngine(catalog_path=CATALOG_JSON)

    def test_direct_vs_search_engine(self):
        """rank_products directly vs GlassesSearchEngine.search should match."""
        query_tags = {
            "frame": {"color": ["black"], "material": "metal"},
            "lenses": {"type": ["sunglasses"], "shape": "rectangular"},
            "style": {"aesthetic": ["classic"], "gender_target": "men"},
        }
        filters = {"in_stock_only": True, "gender": "men"}

        direct = rank_products(query_tags, PRODUCTS, top_k=5, filters=filters)
        engine = self.engine.search(query_tags, top_k=5, filters=filters)

        direct_ids = [(p["id"], round(s, 6)) for p, s, _ in direct]
        engine_ids = [(p["id"], round(s, 6)) for p, s, _ in engine]
        self.assertEqual(direct_ids, engine_ids)

    def test_direct_vs_inventory_matcher(self):
        """rank_products directly vs InventoryMatcher.match should match
        (on the same product pool — InventoryMatcher filters by image existence)."""
        query_tags = {
            "frame": {"color": ["black"], "material": "stainless-steel"},
            "lenses": {"type": ["clear"], "shape": "rectangular"},
            "style": {"aesthetic": ["professional"], "gender_target": "men"},
        }

        # Direct call with same filters as InventoryMatcher uses
        direct = rank_products(
            query_tags, PRODUCTS, top_k=3,
            filters={"in_stock_only": True, "gender": "men"},
        )

        matcher_result = self.matcher.match(query_tags, top_k=3, gender="men")

        # InventoryMatcher pre-filters by image existence, so its pool may be
        # smaller. But every matcher result should appear in direct results.
        if matcher_result.success:
            matcher_ids = {p["id"] for p, _, _ in matcher_result.matches}
            direct_ids = {p["id"] for p, _, _ in direct}
            self.assertTrue(matcher_ids <= direct_ids,
                            f"Matcher returned IDs not in direct: {matcher_ids - direct_ids}")

    def test_free_search_preferences_round_trip(self):
        """preferences_to_query_tags → rank_products should work end-to-end."""
        prefs = {
            "frame_shape": "round",
            "frame_color": ["gold"],
            "frame_material": "metal",
            "lens_type": ["sunglasses"],
            "gender": "women",
            "aesthetic": ["vintage", "classic"],
            "occasion": ["fashion"],
        }
        query_tags = preferences_to_query_tags(prefs)
        results = rank_products(query_tags, PRODUCTS, top_k=3, min_score=0,
                                filters={"in_stock_only": True, "gender": "women"})
        self.assertGreater(len(results), 0)
        # Top result should have some affinity for the query
        _, top_score, _ = results[0]
        self.assertGreater(top_score, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# 10. EDGE CASES & REGRESSION
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_all_filters_impossible_still_returns(self):
        """Even with contradictory non-sport filters, graceful fallback returns results."""
        query = {
            "frame": {},
            "lenses": {"shape": "NONEXISTENT", "type": ["clear"],
                       "color": ["NONEXISTENT"]},
            "style": {"gender_target": "men"},
        }
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0)
        self.assertGreater(len(results), 0)

    def test_single_product_catalog(self):
        product = PRODUCTS[0]
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, [product], top_k=3, min_score=0)
        self.assertLessEqual(len(results), 1)

    def test_empty_catalog(self):
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, [], top_k=3)
        self.assertEqual(results, [])

    def test_score_symmetry_with_normalization(self):
        """Products with 'rubber-black' should score same as 'black' for a 'black' query
        when both go through the full pipeline (rank_products normalizes in filter)."""
        product_plain = {
            "id": "test_plain", "name": "Test Plain", "image": "x.jpg",
            "tags": {
                "frame": {"color": ["black"]},
                "lenses": {"type": ["clear"]},
                "style": {"gender_target": "unisex"},
                "product": {"in_stock": True, "price": 100},
            }
        }
        product_rubber = {
            "id": "test_rubber", "name": "Test Rubber", "image": "x.jpg",
            "tags": {
                "frame": {"color": ["rubber-black"]},
                "lenses": {"type": ["clear"]},
                "style": {"gender_target": "unisex"},
                "product": {"in_stock": True, "price": 100},
            }
        }
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        r_plain = rank_products(query, [product_plain], top_k=1, min_score=0)
        r_rubber = rank_products(query, [product_rubber], top_k=1, min_score=0)
        self.assertEqual(r_plain[0][1], r_rubber[0][1],
                         "rubber-black should normalize to black and score equally")

    def test_real_catalog_product_self_scores_high(self):
        """Pick a real product and verify its own tags produce a high self-score
        through the full pipeline."""
        product = PRODUCTS[0]
        ptags = product["tags"]
        # Build query from the product's own tags (all fields)
        query = {
            "frame": {
                "material": ptags["frame"].get("material"),
                "color": ptags["frame"].get("color"),
                "thickness": ptags["frame"].get("thickness"),
                "rim_type": ptags["frame"].get("rim_type"),
                "finish": ptags["frame"].get("finish"),
            },
            "lenses": {
                "type": ptags["lenses"].get("type"),
                "shape": ptags["lenses"].get("shape"),
                "color": ptags["lenses"].get("color"),
                "size": ptags["lenses"].get("size"),
            },
            "style": {
                "gender_target": ptags["style"].get("gender_target"),
                "aesthetic": ptags["style"].get("aesthetic"),
                "face_shape_fit": ptags["style"].get("face_shape_fit"),
                "occasion": ptags["style"].get("occasion"),
            },
        }
        results = rank_products(query, [product], top_k=1, min_score=0)
        self.assertGreater(len(results), 0)
        _, score, _ = results[0]
        self.assertGreaterEqual(score, 90.0,
                                msg=f"{product['name']} should self-score ≥90, got {score}")

    def test_component_scores_in_range(self):
        """Component scores should be individually plausible (0–100 range)."""
        query = {
            "frame": {"material": "metal", "thickness": "thin",
                       "rim_type": "full-rim", "finish": "matte"},
            "lenses": {"size": "medium"},
            "style": {"aesthetic": ["classic"], "face_shape_fit": ["oval"],
                       "occasion": ["everyday"]},
        }
        for product in PRODUCTS[:20]:
            sub = compute_component_scores(query, product["tags"])
            for comp, val in sub.items():
                self.assertGreaterEqual(val, 0.0, f"{product['name']} {comp}={val}")
                self.assertLessEqual(val, 100.0, f"{product['name']} {comp}={val}")


# ═══════════════════════════════════════════════════════════════════════════
# 11. EXTENSIVE SCORE COMBO TESTING
# ═══════════════════════════════════════════════════════════════════════════

class TestScoreCombinations(unittest.TestCase):
    """Systematic tests across many query combos to verify scores are logical."""

    # ── Synthetic product fixtures ────────────────────────────────────────

    PRODUCT_A = {
        "id": "combo_a", "name": "Combo A", "image": "a.jpg",
        "tags": {
            "frame": {"color": ["black"], "material": "metal",
                      "rim_type": "full-rim", "thickness": "thin", "finish": "matte"},
            "lenses": {"type": ["sunglasses"], "shape": "rectangular",
                       "color": ["gray"], "size": "medium"},
            "style": {"gender_target": "men", "aesthetic": ["classic"],
                      "face_shape_fit": ["oval"], "occasion": ["everyday"]},
            "product": {"in_stock": True, "price": 200},
        },
    }
    PRODUCT_B = {
        "id": "combo_b", "name": "Combo B", "image": "b.jpg",
        "tags": {
            "frame": {"color": ["gold"], "material": "acetate",
                      "rim_type": "rimless", "thickness": "thick", "finish": "glossy"},
            "lenses": {"type": ["clear"], "shape": "round",
                       "color": ["clear"], "size": "large"},
            "style": {"gender_target": "women", "aesthetic": ["vintage"],
                      "face_shape_fit": ["round"], "occasion": ["fashion"]},
            "product": {"in_stock": True, "price": 300},
        },
    }
    BOTH = [PRODUCT_A, PRODUCT_B]

    # ── Score range tests ─────────────────────────────────────────────────

    def test_scores_always_in_0_100(self):
        """No query combo should produce scores outside [0, 100]."""
        combos = [
            {"frame": {"color": ["black"]}, "lenses": {}, "style": {}},
            {"frame": {}, "lenses": {"shape": "round"}, "style": {"gender_target": "men"}},
            {"frame": {"material": "metal", "rim_type": "full-rim"},
             "lenses": {"type": ["sunglasses"]}, "style": {"aesthetic": ["classic"]}},
            {"frame": {}, "lenses": {}, "style": {}},
        ]
        for query in combos:
            results = rank_products(query, PRODUCTS, top_k=50, min_score=0)
            for product, score, _ in results:
                self.assertGreaterEqual(score, 0.0,
                    f"Score {score} < 0 for {product['name']}")
                self.assertLessEqual(score, 100.0,
                    f"Score {score} > 100 for {product['name']}")

    def test_all_catalog_scores_in_range(self):
        """Sweep all products with a rich query — all scores in [0, 100]."""
        query = {
            "frame": {"color": ["black"], "material": "metal",
                      "rim_type": "full-rim", "thickness": "thin", "finish": "matte"},
            "lenses": {"type": ["sunglasses"], "shape": "rectangular",
                       "color": ["gray"], "size": "medium"},
            "style": {"gender_target": "men", "aesthetic": ["classic"],
                      "face_shape_fit": ["oval"], "occasion": ["everyday"]},
        }
        results = rank_products(query, PRODUCTS, top_k=999, min_score=0)
        for product, score, _ in results:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    # ── Only filter fields → all passing products tie at 100 ──────────────

    def test_only_gender_all_pass_at_100(self):
        """When only gender is specified, all matching products score 100."""
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "men"}}
        results = rank_products(query, self.BOTH, top_k=10, min_score=0)
        for _, score, _ in results:
            self.assertAlmostEqual(score, 100.0)

    def test_only_shape_all_pass_at_100(self):
        query = {"frame": {}, "lenses": {"shape": "rectangular"}, "style": {}}
        results = rank_products(query, self.BOTH, top_k=10, min_score=0)
        for _, score, _ in results:
            self.assertAlmostEqual(score, 100.0)

    def test_only_multiple_filters_all_pass_at_100(self):
        """Gender + shape + frame_color — product A passes all."""
        query = {"frame": {"color": ["black"]}, "lenses": {"shape": "rectangular"},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, [self.PRODUCT_A], top_k=1, min_score=0)
        self.assertAlmostEqual(results[0][1], 100.0)

    # ── Filter + soft fields → differentiation ────────────────────────────

    def test_filter_plus_matching_soft_scores_100(self):
        """Product A matches gender + material → 100."""
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, [self.PRODUCT_A], top_k=1, min_score=0)
        self.assertAlmostEqual(results[0][1], 100.0)

    def test_filter_plus_mismatching_soft_below_100(self):
        """Product B mismatches material but passes gender filter → < 100."""
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "women"}}
        results = rank_products(query, [self.PRODUCT_B], top_k=1, min_score=0)
        score = results[0][1]
        self.assertLess(score, 100.0)
        self.assertGreater(score, 0.0)

    def test_more_matching_soft_fields_higher_score(self):
        """Adding more matching soft fields should increase the score."""
        query_1soft = {"frame": {"material": "metal"}, "lenses": {},
                       "style": {"gender_target": "men"}}
        query_2soft = {"frame": {"material": "metal", "rim_type": "full-rim"},
                       "lenses": {}, "style": {"gender_target": "men"}}
        query_3soft = {"frame": {"material": "metal", "rim_type": "full-rim"},
                       "lenses": {"size": "medium"}, "style": {"gender_target": "men"}}

        r1 = rank_products(query_1soft, [self.PRODUCT_A], top_k=1, min_score=0)
        r2 = rank_products(query_2soft, [self.PRODUCT_A], top_k=1, min_score=0)
        r3 = rank_products(query_3soft, [self.PRODUCT_A], top_k=1, min_score=0)

        # All should be 100 since Product A matches everything
        self.assertAlmostEqual(r1[0][1], 100.0)
        self.assertAlmostEqual(r2[0][1], 100.0)
        self.assertAlmostEqual(r3[0][1], 100.0)

    def test_mismatching_soft_field_lowers_score(self):
        """Product B doesn't match material=metal → lower score than Product A."""
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "women"}}
        # Product B: gender=women (match), material=acetate (mismatch)
        results = rank_products(query, [self.PRODUCT_B], top_k=1, min_score=0)
        score = results[0][1]
        # gender(10) matches, material(6) doesn't → 10/16 * 100 = 62.5
        self.assertAlmostEqual(score, 10.0 / 16.0 * 100.0)

    # ── Relaxed filter → lower score ──────────────────────────────────────

    def test_relaxed_filter_lowers_score_in_pipeline(self):
        """When a filter is relaxed, affected products score lower."""
        # Query for shape=hexagonal — neither product has it → shape relaxes.
        # Product A has gender=men → gender passes.
        query = {"frame": {}, "lenses": {"shape": "hexagonal"},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, self.BOTH, top_k=2, min_score=0)
        for _, score, _ in results:
            # gender passes (10) but shape relaxed (0) → 10/20 * 100 = 50
            self.assertAlmostEqual(score, 50.0)

    def test_exact_filter_beats_relaxed(self):
        """Product passing filter exactly should score higher than one needing relaxation."""
        products = [
            {"id": "exact", "name": "Exact", "image": "x.jpg", "tags": {
                "frame": {"color": ["black"]}, "lenses": {"shape": "round", "type": ["clear"]},
                "style": {"gender_target": "men"},
                "product": {"in_stock": True, "price": 100},
            }},
            {"id": "other", "name": "Other", "image": "x.jpg", "tags": {
                "frame": {"color": ["black"]}, "lenses": {"shape": "square", "type": ["clear"]},
                "style": {"gender_target": "men"},
                "product": {"in_stock": True, "price": 100},
            }},
        ]
        # Both have shape round or square; query for round + a nonexistent color
        # so color relaxes and shape stays exact for the round product.
        query = {"frame": {"color": ["black"]}, "lenses": {"shape": "round"},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, products, top_k=2, min_score=0)
        # Only the round product should survive shape filter
        self.assertEqual(results[0][0]["id"], "exact")

    # ── Monotonicity: more matches → higher score ─────────────────────────

    def test_monotonicity_adding_matching_fields(self):
        """Score should not decrease when adding a field that matches."""
        base_q = {"frame": {}, "lenses": {},
                  "style": {"gender_target": "men"}}
        extended_q = {"frame": {"material": "metal"}, "lenses": {},
                      "style": {"gender_target": "men"}}

        r_base = rank_products(base_q, [self.PRODUCT_A], top_k=1, min_score=0)
        r_ext = rank_products(extended_q, [self.PRODUCT_A], top_k=1, min_score=0)

        self.assertGreaterEqual(r_ext[0][1], r_base[0][1] - 0.01,
            "Adding a matching field should not decrease score significantly")

    def test_monotonicity_adding_mismatching_field(self):
        """Score should decrease when adding a field that doesn't match."""
        base_q = {"frame": {}, "lenses": {},
                  "style": {"gender_target": "women"}}
        extended_q = {"frame": {"material": "metal"}, "lenses": {},
                      "style": {"gender_target": "women"}}
        # Product B: gender=women (match), material=acetate (mismatch)
        r_base = rank_products(base_q, [self.PRODUCT_B], top_k=1, min_score=0)
        r_ext = rank_products(extended_q, [self.PRODUCT_B], top_k=1, min_score=0)

        self.assertGreater(r_base[0][1], r_ext[0][1],
            "Adding a mismatching field should decrease score")

    # ── Real catalog sweeps ───────────────────────────────────────────────

    def test_real_catalog_ranking_order(self):
        """Top result should always score >= all other results."""
        queries = [
            {"frame": {"color": ["black"], "material": "metal"},
             "lenses": {"type": ["sunglasses"], "shape": "rectangular"},
             "style": {"gender_target": "men", "aesthetic": ["classic"]}},
            {"frame": {"color": ["gold"]},
             "lenses": {"shape": "round"},
             "style": {"gender_target": "women", "aesthetic": ["vintage"]}},
            {"frame": {"material": "acetate", "rim_type": "full-rim"},
             "lenses": {"type": ["clear"]},
             "style": {}},
        ]
        for query in queries:
            results = rank_products(query, PRODUCTS, top_k=10, min_score=0)
            if len(results) >= 2:
                for i in range(len(results) - 1):
                    self.assertGreaterEqual(results[i][1], results[i + 1][1],
                        f"Results not sorted: {results[i][1]} < {results[i + 1][1]}")

    def test_real_catalog_varied_combos_no_crash(self):
        """Sweep many field combos on real catalog — no crashes, scores valid."""
        genders = ["men", "women", None]
        shapes = ["round", "rectangular", "aviator", None]
        materials = ["metal", "acetate", None]
        colors = [["black"], ["gold"], None]

        for gender in genders:
            for shape in shapes:
                for material in materials:
                    for color in colors:
                        query = {"frame": {}, "lenses": {}, "style": {}}
                        if gender:
                            query["style"]["gender_target"] = gender
                        if shape:
                            query["lenses"]["shape"] = shape
                        if material:
                            query["frame"]["material"] = material
                        if color:
                            query["frame"]["color"] = color

                        results = rank_products(query, PRODUCTS, top_k=5, min_score=0)
                        for product, score, _ in results:
                            self.assertGreaterEqual(score, 0.0)
                            self.assertLessEqual(score, 100.0)

    def test_real_catalog_all_fields_specified(self):
        """Full query with every possible field — verify score spread."""
        query = {
            "frame": {"color": ["black"], "material": "metal",
                      "rim_type": "full-rim", "thickness": "thin", "finish": "matte"},
            "lenses": {"type": ["sunglasses"], "shape": "rectangular",
                       "color": ["gray"], "size": "medium"},
            "style": {"gender_target": "men", "aesthetic": ["classic"],
                      "face_shape_fit": ["oval"], "occasion": ["everyday"]},
        }
        results = rank_products(query, PRODUCTS, top_k=20, min_score=0)
        scores = [s for _, s, _ in results]
        if len(scores) >= 2:
            # With all fields specified, there should be meaningful spread
            score_range = max(scores) - min(scores)
            self.assertGreater(score_range, 5.0,
                f"Score range too narrow ({score_range:.1f}) with all fields specified")

    def test_self_match_through_pipeline(self):
        """A product's own tags as query should score very high through rank_products."""
        for product in PRODUCTS[:5]:
            ptags = product["tags"]
            query = {
                "frame": {
                    "color": ptags["frame"].get("color"),
                    "material": ptags["frame"].get("material"),
                    "rim_type": ptags["frame"].get("rim_type"),
                    "thickness": ptags["frame"].get("thickness"),
                    "finish": ptags["frame"].get("finish"),
                },
                "lenses": {
                    "type": ptags["lenses"].get("type"),
                    "shape": ptags["lenses"].get("shape"),
                    "color": ptags["lenses"].get("color"),
                    "size": ptags["lenses"].get("size"),
                },
                "style": {
                    "gender_target": ptags["style"].get("gender_target"),
                    "aesthetic": ptags["style"].get("aesthetic"),
                    "face_shape_fit": ptags["style"].get("face_shape_fit"),
                    "occasion": ptags["style"].get("occasion"),
                },
            }
            # Clean out None values
            for cat in query:
                query[cat] = {k: v for k, v in query[cat].items() if v is not None}

            results = rank_products(query, PRODUCTS, top_k=1, min_score=0)
            self.assertGreater(len(results), 0)
            _, top_score, _ = results[0]
            self.assertGreaterEqual(top_score, 85.0,
                f"{product['name']} self-match should score ≥85, got {top_score:.1f}")

    # ── Specific weight calculation verification ──────────────────────────

    def test_known_weight_calculation(self):
        """Verify exact score for a known scenario."""
        # Query: gender(10) + material(6). Product A: both match.
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, [self.PRODUCT_A], top_k=1, min_score=0)
        # gender=10 (filter, exact match), material=6 (soft, match)
        # total=16, earned=16 → 100
        self.assertAlmostEqual(results[0][1], 100.0)

    def test_known_partial_weight_calculation(self):
        """Verify exact score for partial match scenario."""
        # Query: gender(10) + material(6). Product B: gender=women (match), material=acetate (mismatch)
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "women"}}
        results = rank_products(query, [self.PRODUCT_B], top_k=1, min_score=0)
        # gender=10 (match), material=6 (mismatch=0) → 10/16 * 100 = 62.5
        self.assertAlmostEqual(results[0][1], 10.0 / 16.0 * 100.0)

    def test_known_missing_tag_calculation(self):
        """Product with missing soft tag gets partial credit."""
        product_missing = {
            "id": "missing", "name": "Missing", "image": "x.jpg",
            "tags": {
                "frame": {"color": ["black"]},  # no material tag
                "lenses": {"type": ["clear"]},
                "style": {"gender_target": "men"},
                "product": {"in_stock": True, "price": 100},
            },
        }
        query = {"frame": {"material": "metal"}, "lenses": {},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, [product_missing], top_k=1, min_score=0)
        # gender=10 (filter match), material=6 (missing → 6*0.25=1.5)
        # total=16, earned=11.5 → 71.875
        self.assertAlmostEqual(results[0][1], (10.0 + 6.0 * 0.25) / 16.0 * 100.0)


# ═══════════════════════════════════════════════════════════════════════════
# 12. GENDER / UNISEX MATCHING
# ═══════════════════════════════════════════════════════════════════════════

class TestGenderUnisexMatching(unittest.TestCase):
    """Verify that unisex products always appear for men/women queries."""

    def test_men_query_includes_unisex(self):
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "men"}}
        results = rank_products(query, PRODUCTS, top_k=200, min_score=0)
        genders = {p["tags"]["style"]["gender_target"] for p, _, _ in results}
        self.assertIn("unisex", genders, "Unisex products missing from men query")
        self.assertNotIn("women", genders, "Women-only products in men query")

    def test_women_query_includes_unisex(self):
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "women"}}
        results = rank_products(query, PRODUCTS, top_k=200, min_score=0)
        genders = {p["tags"]["style"]["gender_target"] for p, _, _ in results}
        self.assertIn("unisex", genders, "Unisex products missing from women query")
        self.assertNotIn("men", genders, "Men-only products in women query")

    def test_unisex_query_matches_all_genders(self):
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "unisex"}}
        results = rank_products(query, PRODUCTS, top_k=200, min_score=0)
        genders = {p["tags"]["style"]["gender_target"] for p, _, _ in results}
        self.assertTrue(len(genders) >= 2, f"Unisex query should match broadly, got {genders}")

    def test_men_pool_larger_than_men_only(self):
        """Men query pool should include unisex → bigger than strict men-only count."""
        men_only = sum(1 for p in PRODUCTS
                       if p["tags"]["style"].get("gender_target") == "men"
                       and p["tags"]["product"].get("in_stock"))
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "men"}}
        results = rank_products(query, PRODUCTS, top_k=200, min_score=0,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), men_only)

    def test_women_pool_larger_than_women_only(self):
        women_only = sum(1 for p in PRODUCTS
                         if p["tags"]["style"].get("gender_target") == "women"
                         and p["tags"]["product"].get("in_stock"))
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "women"}}
        results = rank_products(query, PRODUCTS, top_k=200, min_score=0,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), women_only)

    def test_gender_plus_shape_finds_unisex(self):
        """men+square should find unisex square products, not only men-only."""
        query = {"frame": {}, "lenses": {"shape": "square"},
                 "style": {"gender_target": "men"}}
        results = rank_products(query, PRODUCTS, top_k=20, min_score=0)
        unisex_results = [p for p, _, _ in results
                          if p["tags"]["style"]["gender_target"] == "unisex"]
        self.assertGreater(len(unisex_results), 0,
                           "No unisex products in men+square query")

    def test_gender_plus_all_filters_finds_unisex(self):
        """men + square + black + clear should return unisex products."""
        prefs = {"gender": "men", "frame_shape": "square",
                 "frame_color": "black", "lens_type": "clear"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=5,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), 0)
        # At least one should be unisex (RX7256 is unisex)
        has_unisex = any(p["tags"]["style"]["gender_target"] == "unisex"
                         for p, _, _ in results)
        self.assertTrue(has_unisex, "No unisex products for men+square+black+clear")


# ═══════════════════════════════════════════════════════════════════════════
# 13. PER-FILTER-FIELD VALIDATION — every hard filter value returns matches
# ═══════════════════════════════════════════════════════════════════════════

class TestEveryFilterValueReturnsResults(unittest.TestCase):
    """For each hard filter field, every UI option value should produce
    at least one result when it's the only filter specified."""

    FILTER_OPTIONS = {
        "gender": ["men", "women", "unisex"],
        "frame_shape": ["round", "square", "rectangular", "oval", "cat-eye",
                        "teardrop", "geometric", "hexagonal", "irregular", "shield"],
        "lens_type": ["clear", "prescription-ready", "sunglasses", "polarized",
                      "gradient", "mirrored", "chromance", "prizm", "sport"],
        "frame_color": ["black", "silver", "gold", "rose-gold", "tortoiseshell",
                        "transparent", "gray", "blue", "green", "red", "pink", "white"],
        "lens_color": ["transparent", "gray", "brown", "green", "blue", "gold",
                       "yellow", "pink", "red", "black", "silver"],
    }

    PREF_KEY_MAP = {
        "gender": "gender",
        "frame_shape": "frame_shape",
        "lens_type": "lens_type",
        "frame_color": "frame_color",
        "lens_color": "lens_color",
    }

    def test_each_filter_value_standalone(self):
        """Every single option value returns ≥1 result when used alone."""
        for field, values in self.FILTER_OPTIONS.items():
            for val in values:
                pref_key = self.PREF_KEY_MAP[field]
                prefs = {pref_key: val}
                qt = preferences_to_query_tags(prefs)
                results = rank_products(qt, PRODUCTS, top_k=5, min_score=0,
                                        filters={"in_stock_only": True})
                self.assertGreater(
                    len(results), 0,
                    f"No results for {field}={val}")

    def test_each_filter_value_with_gender_men(self):
        """Each filter value + gender=men returns results."""
        for field, values in self.FILTER_OPTIONS.items():
            if field == "gender":
                continue
            for val in values:
                pref_key = self.PREF_KEY_MAP[field]
                prefs = {"gender": "men", pref_key: val}
                qt = preferences_to_query_tags(prefs)
                results = rank_products(qt, PRODUCTS, top_k=5, min_score=0,
                                        filters={"in_stock_only": True})
                # Graceful fallback ensures we always get results
                self.assertGreater(
                    len(results), 0,
                    f"No results for men + {field}={val}")

    def test_each_filter_value_with_gender_women(self):
        """Each filter value + gender=women returns results."""
        for field, values in self.FILTER_OPTIONS.items():
            if field == "gender":
                continue
            for val in values:
                pref_key = self.PREF_KEY_MAP[field]
                prefs = {"gender": "women", pref_key: val}
                qt = preferences_to_query_tags(prefs)
                results = rank_products(qt, PRODUCTS, top_k=5, min_score=0,
                                        filters={"in_stock_only": True})
                self.assertGreater(
                    len(results), 0,
                    f"No results for women + {field}={val}")


# ═══════════════════════════════════════════════════════════════════════════
# 14. LENS COLOR HARD FILTER — brown, transparent, etc. never return wrong colors
# ═══════════════════════════════════════════════════════════════════════════

class TestLensColorFiltering(unittest.TestCase):
    """Verify the lens color hard filter never returns mismatched lens colors
    (the reported bug: brown lens query returning clear-lens products)."""

    def _norm_lc_set(self, lc_val):
        """Normalize lens color values to canonical set."""
        if isinstance(lc_val, list):
            return {_norm_color(v) for v in lc_val}
        return {_norm_color(str(lc_val))}

    def test_brown_lens_no_clear(self):
        prefs = {"lens_color": "brown"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=20, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            lc = p["tags"]["lenses"].get("color", [])
            norm_colors = self._norm_lc_set(lc)
            self.assertNotIn("transparent", norm_colors,
                             f"{p['name']} has clear lenses: {lc}")
            self.assertIn("brown", norm_colors,
                          f"{p['name']} doesn't have brown lenses: {lc}")

    def test_gray_lens_no_clear(self):
        prefs = {"lens_color": "gray"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=20, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            lc = p["tags"]["lenses"].get("color", [])
            norm_colors = self._norm_lc_set(lc)
            self.assertIn("gray", norm_colors,
                          f"{p['name']} doesn't have gray lenses: {lc}")

    def test_transparent_lens_only_clear(self):
        prefs = {"lens_color": "transparent"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=20, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            lc = p["tags"]["lenses"].get("color", [])
            norm_colors = self._norm_lc_set(lc)
            self.assertIn("transparent", norm_colors,
                          f"{p['name']} doesn't have clear lenses: {lc}")

    def test_blue_lens_only_blue(self):
        prefs = {"lens_color": "blue"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=20, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            lc = p["tags"]["lenses"].get("color", [])
            norm_colors = self._norm_lc_set(lc)
            self.assertIn("blue", norm_colors,
                          f"{p['name']} doesn't have blue lenses: {lc}")

    def test_green_lens_only_green(self):
        prefs = {"lens_color": "green"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=20, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            lc = p["tags"]["lenses"].get("color", [])
            norm_colors = self._norm_lc_set(lc)
            self.assertIn("green", norm_colors,
                          f"{p['name']} doesn't have green lenses: {lc}")

    def test_every_lens_color_option_returns_matching(self):
        """Every lens color option: products scoring 100 must have that color.
        Lower-scoring products may not match (graceful fallback)."""
        lens_colors = ["transparent", "gray", "brown", "green", "blue",
                       "gold", "yellow", "pink", "red", "black", "silver"]
        for color in lens_colors:
            prefs = {"lens_color": color}
            qt = preferences_to_query_tags(prefs)
            results = rank_products(qt, PRODUCTS, top_k=50, min_score=0,
                                    filters={"in_stock_only": True})
            for p, score, _ in results:
                if score < 100.0:
                    continue  # filter was relaxed — fallback product
                lc = p["tags"]["lenses"].get("color", [])
                norm_colors = self._norm_lc_set(lc)
                self.assertIn(color, norm_colors,
                    f"lens_color={color}: {p['name']} scores 100 but lc={lc} (norm={norm_colors})")


# ═══════════════════════════════════════════════════════════════════════════
# 15. FRAME COLOR HARD FILTER — same validation
# ═══════════════════════════════════════════════════════════════════════════

class TestFrameColorFiltering(unittest.TestCase):
    """Verify frame color filter returns only matching products."""

    def _norm_fc_set(self, fc_val):
        if isinstance(fc_val, list):
            return {_norm_color(v) for v in fc_val}
        return {_norm_color(str(fc_val))}

    def test_every_frame_color_option_returns_matching(self):
        """Every frame color option: products scoring 100 must have that color."""
        frame_colors = ["black", "silver", "gold", "rose-gold", "tortoiseshell",
                        "transparent", "gray", "blue", "green", "red", "pink", "white"]
        for color in frame_colors:
            prefs = {"frame_color": color}
            qt = preferences_to_query_tags(prefs)
            results = rank_products(qt, PRODUCTS, top_k=50, min_score=0,
                                    filters={"in_stock_only": True})
            for p, score, _ in results:
                if score < 100.0:
                    continue  # filter was relaxed — fallback product
                fc = p["tags"]["frame"].get("color", [])
                norm_colors = self._norm_fc_set(fc)
                self.assertIn(color, norm_colors,
                    f"frame_color={color}: {p['name']} scores 100 but fc={fc} (norm={norm_colors})")

    def test_black_frame_no_gold_only(self):
        """Querying black frames should not return gold-only products."""
        prefs = {"frame_color": "black"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=50, min_score=0,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            fc = p["tags"]["frame"].get("color", [])
            norm_colors = self._norm_fc_set(fc)
            self.assertIn("black", norm_colors,
                          f"{p['name']} has only {fc}, no black")


# ═══════════════════════════════════════════════════════════════════════════
# 16. COMBINED HARD FILTER VALIDATION — multi-field combos
# ═══════════════════════════════════════════════════════════════════════════

class TestCombinedFilterValidation(unittest.TestCase):
    """Test that combined filter selections produce coherent results."""

    def test_men_round_black_clear(self):
        """Previously broken combo: should return unisex square black clear products."""
        prefs = {"gender": "men", "frame_shape": "square",
                 "frame_color": "black", "lens_type": "clear"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=5,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), 0)
        # Top result should be a perfect match (score 100)
        top_score = results[0][1]
        self.assertEqual(top_score, 100.0,
                         f"Expected 100 for perfect match, got {top_score}")

    def test_women_round_brown_lens(self):
        prefs = {"gender": "women", "frame_shape": "round",
                 "lens_color": "brown"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=5,
                                filters={"in_stock_only": True})
        for p, _, _ in results:
            gender = p["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("women", "unisex"))

    def test_all_five_hard_filters(self):
        """Specify all 5 hard filters with values that exist together."""
        prefs = {"gender": "men", "frame_shape": "rectangular",
                 "frame_color": "black", "lens_type": "clear",
                 "lens_color": "transparent"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=5,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), 0)
        for p, s, _ in results:
            # If score is 100, all filters passed exactly
            if s == 100.0:
                t = p["tags"]
                self.assertIn(t["style"]["gender_target"], ("men", "unisex"))

    def test_impossible_combo_still_returns_via_fallback(self):
        """Completely impossible combo still returns results (graceful fallback)."""
        prefs = {"gender": "women", "frame_shape": "hexagonal",
                 "frame_color": "red", "lens_type": "chromance",
                 "lens_color": "pink"}
        qt = preferences_to_query_tags(prefs)
        results = rank_products(qt, PRODUCTS, top_k=3, min_score=0,
                                filters={"in_stock_only": True})
        self.assertGreater(len(results), 0, "Even impossible combos should return fallback results")
        # Score should be low since everything relaxed
        self.assertLess(results[0][1], 50.0)

    def test_broad_sweep_all_filter_pairs(self):
        """Test every pair of hard filter fields with sample values."""
        fields = {
            "gender": "women",
            "frame_shape": "round",
            "lens_type": "sunglasses",
            "frame_color": "black",
            "lens_color": "brown",
        }
        field_names = list(fields.keys())
        for i in range(len(field_names)):
            for j in range(i + 1, len(field_names)):
                f1, f2 = field_names[i], field_names[j]
                prefs = {f1: fields[f1], f2: fields[f2]}
                qt = preferences_to_query_tags(prefs)
                results = rank_products(qt, PRODUCTS, top_k=5, min_score=0,
                                        filters={"in_stock_only": True})
                self.assertGreater(
                    len(results), 0,
                    f"No results for {f1}={fields[f1]} + {f2}={fields[f2]}")

    def test_triple_filter_combos(self):
        """Test all triples of hard filter fields."""
        fields = {
            "gender": "men",
            "frame_shape": "rectangular",
            "lens_type": "clear",
            "frame_color": "black",
            "lens_color": "transparent",
        }
        field_names = list(fields.keys())
        for i in range(len(field_names)):
            for j in range(i + 1, len(field_names)):
                for k in range(j + 1, len(field_names)):
                    f1, f2, f3 = field_names[i], field_names[j], field_names[k]
                    prefs = {f1: fields[f1], f2: fields[f2], f3: fields[f3]}
                    qt = preferences_to_query_tags(prefs)
                    results = rank_products(qt, PRODUCTS, top_k=5, min_score=0,
                                            filters={"in_stock_only": True})
                    self.assertGreater(
                        len(results), 0,
                        f"No results for {f1}+{f2}+{f3}")


class TestV2PortFeatures(unittest.TestCase):
    """Behaviors ported (and catalog-adapted) from the lenses_v2 matcher:
    frame.shape fold, per-model + visual-signature dedup, never-empty fallback,
    and the top_k guard."""

    def test_top_k_zero_returns_empty(self):
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        self.assertEqual(rank_products(query, PRODUCTS, top_k=0), [])
        self.assertEqual(rank_products(query, PRODUCTS, top_k=-5), [])

    def test_frame_shape_fold_drives_matching(self):
        """A shape supplied under frame.shape (as query_interpreter and the
        face-analysis recommended_tags emit it) must match the same products as
        the equivalent lenses.shape query — i.e. it is no longer dropped."""
        q_frame = {"frame": {"shape": "cat-eye"}, "lenses": {}, "style": {}}
        q_lens = {"frame": {}, "lenses": {"shape": "cat-eye"}, "style": {}}
        r_frame = rank_products(q_frame, PRODUCTS, top_k=10, min_score=0)
        r_lens = rank_products(q_lens, PRODUCTS, top_k=10, min_score=0)
        self.assertGreater(len(r_frame), 0, "frame.shape query returned nothing")
        self.assertEqual({p["id"] for p, _, _ in r_frame},
                         {p["id"] for p, _, _ in r_lens},
                         "frame.shape should fold into lenses.shape and match identically")

    def test_frame_shape_fold_does_not_clobber_explicit_lens_shape_path(self):
        """preferences_to_query_tags writes lenses.shape (not frame.shape); the
        fold must leave that path untouched."""
        qt = preferences_to_query_tags({"frame_shape": "round"})
        self.assertEqual(qt["lenses"].get("shape"), "round")
        self.assertNotIn("shape", qt["frame"])
        self.assertGreater(len(rank_products(qt, PRODUCTS, top_k=5, min_score=0)), 0)

    def test_per_model_dedup(self):
        """No model_name appears more than max_per_model times among the
        confident (non-fallback) results."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=10, max_per_model=2,
                                min_score=0)
        counts: dict[str, int] = {}
        for product, _, _ in results:
            if product.get("_low_confidence"):
                continue
            model = product["tags"].get("product", {}).get("model_name", "")
            counts[model] = counts.get(model, 0) + 1
        for model, n in counts.items():
            self.assertLessEqual(n, 2, f"model {model!r} appears {n} times (>2)")

    def test_visual_signature_dedup(self):
        """Confident results never share a visual signature (no near-identical
        twins on the same result page)."""
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "men"}}
        results = rank_products(query, PRODUCTS, top_k=10, min_score=0)
        sigs = [
            _visual_signature(p) for p, _, _ in results
            if not p.get("_low_confidence")
        ]
        self.assertEqual(len(sigs), len(set(sigs)),
                         "two confident results share a visual signature")

    def test_never_empty_when_over_budget(self):
        """A max_price below the catalog minimum still returns top_k results,
        flagged low-confidence + over-budget rather than an empty page."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=3,
                                filters={"max_price": 100, "in_stock_only": True})
        self.assertEqual(len(results), 3, "over-tight budget should still fill top_k")
        for product, _, _ in results:
            self.assertTrue(product.get("_low_confidence"),
                            "fallback result should be flagged low_confidence")
            self.assertTrue(product.get("_over_budget"),
                            "fallback result should be flagged over_budget")

    def test_free_search_sport_query_returns_sport(self):
        """A realistic Free Search 'sports' query lands sport in style.occasion
        (the only place query_interpreter/analysis_prompt emit it), NOT in
        lenses.type.  user_wants_sport must trigger on occasion so the user gets
        sport frames, not the inverted non-sport pool."""
        query = {"frame": {}, "lenses": {}, "style": {"occasion": ["sport"]}}
        results = rank_products(query, PRODUCTS, top_k=5, min_score=0)
        self.assertGreater(len(results), 0)
        for product, _, _ in results:
            occasion = product["tags"]["style"].get("occasion", [])
            if isinstance(occasion, str):
                occasion = [occasion]
            self.assertIn("sport", occasion,
                          f"{product['name']} is not sport but a sport query returned it")

    def test_sport_query_prioritizes_dedicated(self):
        """Within a sports search, dedicated performance frames rank ahead of
        versatile everyday-and-sport frames (so a sports search surfaces real
        sports glasses first, not office frames that merely also list sport)."""
        query = {"frame": {}, "lenses": {}, "style": {"occasion": ["sport"]}}
        results = rank_products(query, PRODUCTS, top_k=10, min_score=0)
        flags = [_is_dedicated_sport(p) for p, _, _ in results]
        self.assertIn(True, flags, "expected at least one dedicated sport frame")
        if False in flags:  # both kinds present → all dedicated must come first
            last_dedicated = max(i for i, f in enumerate(flags) if f)
            first_versatile = min(i for i, f in enumerate(flags) if not f)
            self.assertLess(last_dedicated, first_versatile,
                            "dedicated sport frames should rank before versatile ones")
        # The very top result must be a dedicated sports frame.
        self.assertTrue(flags[0], "top sports result should be a dedicated sport frame")

    def test_under_budget_pool_not_padded_with_violations(self):
        """When SOME products fit the budget, results stay within budget and are
        not padded with over-budget items (Pass 5 only fires on an empty pool)."""
        results = rank_products({"frame": {}, "lenses": {}, "style": {}}, PRODUCTS,
                                top_k=50, min_score=0,
                                filters={"max_price": 500, "in_stock_only": True})
        self.assertGreater(len(results), 0)
        for product, _, _ in results:
            self.assertFalse(product.get("_over_budget"),
                             f"{product['name']} is over budget but was padded in")
            self.assertLessEqual(product["tags"]["product"]["price"], 500)


if __name__ == "__main__":
    unittest.main()
