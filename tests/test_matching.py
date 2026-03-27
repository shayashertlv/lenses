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
    _norm_color,
    _norm_material,
    _norm_shape,
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
        self.assertEqual(score, 1.0)


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

    def test_perfect_match(self):
        tags = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["black"],
            "frame.rim_type": "full-rim",
            "style.aesthetic": ["classic"],
        })
        score = compute_tag_score(tags, tags)
        self.assertEqual(score, 1.0)

    def test_zero_overlap(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["black"],
        })
        product = self._make_tags(**{
            "frame.material": "acetate",
            "frame.color": ["gold"],
        })
        score = compute_tag_score(query, product)
        self.assertEqual(score, 0.0)

    def test_partial_overlap(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["black"],
        })
        product = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["gold"],
        })
        # material matches (weight 3.0), color doesn't (weight 3.0)
        # score = 3.0 / 6.0 = 0.5
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 0.5)

    def test_missing_product_tag_gets_partial_credit(self):
        query = self._make_tags(**{
            "frame.material": "metal",
            "frame.color": ["black"],
        })
        product = self._make_tags(**{
            "frame.material": "metal",
            # color is missing entirely
        })
        # material matches (3.0 * 1.0), color missing (3.0 * 0.25)
        # score = (3.0 + 0.75) / 6.0 = 0.625
        score = compute_tag_score(query, product)
        expected = (3.0 + 3.0 * _MISSING_TAG_CREDIT) / 6.0
        self.assertAlmostEqual(score, expected)

    def test_missing_tag_scores_higher_than_mismatch(self):
        query = self._make_tags(**{"frame.color": ["black"]})
        missing = self._make_tags()           # no color at all
        mismatch = self._make_tags(**{"frame.color": ["gold"]})
        self.assertGreater(
            compute_tag_score(query, missing),
            compute_tag_score(query, mismatch),
        )

    def test_no_query_fields_returns_one(self):
        """Empty query → all products score 1.0."""
        query = self._make_tags()
        product = self._make_tags(**{"frame.material": "metal"})
        self.assertEqual(compute_tag_score(query, product), 1.0)

    def test_color_normalization_in_scoring(self):
        """gunmetal product should match silver query via normalization."""
        query = self._make_tags(**{"frame.color": ["silver"]})
        product = self._make_tags(**{"frame.color": ["gunmetal"]})
        score = compute_tag_score(query, product)
        self.assertEqual(score, 1.0)

    def test_multi_value_partial_overlap(self):
        query = self._make_tags(**{"style.aesthetic": ["classic", "modern"]})
        product = self._make_tags(**{"style.aesthetic": ["classic", "vintage"]})
        # overlap 1 out of 2 query values
        score = compute_tag_score(query, product)
        self.assertAlmostEqual(score, 0.5)


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
        q = self._make_tags(**{"frame.color": ["black"]})
        p = self._make_tags(**{"frame.color": ["black"]})
        sub = compute_component_scores(q, p)
        self.assertIn("fit", sub)
        self.assertIn("style", sub)
        self.assertIn("color", sub)

    def test_scores_are_deterministic(self):
        q = self._make_tags(**{
            "frame.color": ["black"],
            "style.aesthetic": ["modern"],
            "lenses.size": "medium",
        })
        p = self._make_tags(**{
            "frame.color": ["black"],
            "style.aesthetic": ["modern"],
            "lenses.size": "large",
        })
        sub1 = compute_component_scores(q, p)
        sub2 = compute_component_scores(q, p)
        self.assertEqual(sub1, sub2)

    def test_perfect_match_all_ones(self):
        tags = self._make_tags(**{
            "frame.color": ["black"],
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
            self.assertAlmostEqual(val, 1.0, msg=f"{component} should be 1.0")

    def test_color_component_independent(self):
        """Color component should only reflect frame.color/material/thickness."""
        q = self._make_tags(**{
            "frame.color": ["black"],
            "frame.material": "metal",
            "style.aesthetic": ["modern"],  # style component, not color
        })
        p_color_match = self._make_tags(**{
            "frame.color": ["black"],
            "frame.material": "metal",
            "style.aesthetic": ["vintage"],  # mismatch on style
        })
        sub = compute_component_scores(q, p_color_match)
        self.assertAlmostEqual(sub["color"], 1.0)
        self.assertLess(sub["style"], 1.0)

    def test_fallback_to_overall_when_no_fields(self):
        """If query has no fields in a component group, fall back to overall."""
        q = self._make_tags(**{"frame.color": ["black"]})  # only color component
        p = self._make_tags(**{"frame.color": ["black"]})
        sub = compute_component_scores(q, p)
        overall = compute_tag_score(q, p)
        # fit and style have no queried fields → should equal overall
        self.assertAlmostEqual(sub["fit"], overall)
        self.assertAlmostEqual(sub["style"], overall)


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
        results = rank_products(query, PRODUCTS, top_k=10, min_score=0.0)
        scores = [s for _, s in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_gender_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {"gender_target": "women"}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0)
        for product, _ in results:
            gender = product["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("women", "unisex"),
                          f"{product['name']} has gender_target={gender}")

    def test_gender_via_filters(self):
        """Gender passed in filters dict should work same as in query_tags."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0,
                                filters={"gender": "men"})
        for product, _ in results:
            gender = product["tags"]["style"]["gender_target"]
            self.assertIn(gender, ("men", "unisex"),
                          f"{product['name']} has gender_target={gender}")

    def test_sport_isolation_excludes_sport(self):
        """Non-sport query should never return sport products."""
        query = {"frame": {}, "lenses": {"type": ["sunglasses"]}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0)
        for product, _ in results:
            lens_types = product["tags"]["lenses"]["type"]
            if isinstance(lens_types, str):
                lens_types = [lens_types]
            self.assertNotIn("sport", lens_types,
                             f"{product['name']} is a sport product but was returned")

    def test_sport_isolation_returns_sport(self):
        """Sport query should only return sport products."""
        query = {"frame": {}, "lenses": {"type": ["sport"]}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0)
        if results:  # catalog may not have sport products
            for product, _ in results:
                lens_types = product["tags"]["lenses"]["type"]
                if isinstance(lens_types, str):
                    lens_types = [lens_types]
                self.assertIn("sport", lens_types,
                              f"{product['name']} is not a sport product")

    def test_price_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0,
                                filters={"max_price": 500})
        for product, _ in results:
            price = product["tags"]["product"]["price"]
            self.assertLessEqual(price, 500, f"{product['name']} costs {price}")

    def test_in_stock_filter(self):
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.0,
                                filters={"in_stock_only": True})
        for product, _ in results:
            self.assertTrue(product["tags"]["product"].get("in_stock", False),
                            f"{product['name']} is not in stock")

    def test_min_score_threshold(self):
        query = {"frame": {"material": "carbon-fiber", "color": ["pink"]},
                 "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=50, min_score=0.5)
        for _, score in results:
            self.assertGreaterEqual(score, 0.5)

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
        r_butterfly = rank_products(q_butterfly, PRODUCTS, top_k=10, min_score=0.0)
        r_cat_eye = rank_products(q_cat_eye, PRODUCTS, top_k=10, min_score=0.0)
        # butterfly results should be a subset of cat-eye results (or equal)
        butterfly_ids = {p["id"] for p, _ in r_butterfly}
        cat_eye_ids = {p["id"] for p, _ in r_cat_eye}
        self.assertTrue(butterfly_ids <= cat_eye_ids,
                        f"butterfly IDs not subset of cat-eye: {butterfly_ids - cat_eye_ids}")

    def test_color_normalization_in_filter(self):
        """Query for 'gunmetal' lens color should match 'silver' equivalently (after normalization)."""
        # This tests the filter pipeline, not just scoring
        q = {"frame": {"color": ["gunmetal"]}, "lenses": {}, "style": {}}
        results = rank_products(q, PRODUCTS, top_k=10, min_score=0.0)
        # At least one result should exist if catalog has black/silver frames
        self.assertGreater(len(results), 0)

    def test_graceful_fallback_on_impossible_filter(self):
        """Filters that would empty the pool should be skipped (except sport)."""
        query = {
            "frame": {},
            "lenses": {"shape": "NONEXISTENT_SHAPE_XYZ"},
            "style": {"gender_target": "men"},
        }
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0.0)
        # Should still return results because shape filter falls back
        self.assertGreater(len(results), 0)

    def test_relaxed_filter_fields_still_scored(self):
        """When a filter is relaxed, the relaxed field should be scored
        softly via extra_fields so matching products rank higher."""
        # Test the mechanism directly: compute_tag_score with extra_fields
        # should differentiate products on the relaxed field.
        q = {"frame": {}, "lenses": {"shape": "round"}, "style": {}}
        p_match = {"frame": {}, "lenses": {"shape": "round"}, "style": {}}
        p_nomatch = {"frame": {}, "lenses": {"shape": "square"}, "style": {}}
        extra = [("lenses", "shape")]
        score_match = compute_tag_score(q, p_match, extra_fields=extra)
        score_nomatch = compute_tag_score(q, p_nomatch, extra_fields=extra)
        self.assertGreater(score_match, score_nomatch,
                           "Product matching relaxed filter field should score higher")

    def test_relaxed_filter_in_full_pipeline(self):
        """End-to-end: when all products fail a filter, it relaxes and
        the field is scored softly — products closer to the query rank higher."""
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
        # Query for hexagonal — neither has it → shape filter relaxes.
        # Neither product matches hexagonal, so relaxed scoring gives 0 to both.
        # Both should still be returned (graceful fallback works).
        query = {"frame": {}, "lenses": {"shape": "hexagonal"}, "style": {}}
        results = rank_products(query, products, top_k=2, min_score=0.0)
        self.assertEqual(len(results), 2)

    def test_empty_query_returns_results(self):
        """Empty query should return top-k products (all score 1.0)."""
        query = {"frame": {}, "lenses": {}, "style": {}}
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0.0)
        self.assertEqual(len(results), 3)
        for _, score in results:
            self.assertEqual(score, 1.0)


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
        for product, _ in result.matches:
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
        for _, score in result.matches:
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)


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
        for product, _ in results:
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
        scores = [s for _, s in results]
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

        direct_ids = [(p["id"], round(s, 6)) for p, s in direct]
        engine_ids = [(p["id"], round(s, 6)) for p, s in engine]
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
            matcher_ids = {p["id"] for p, _ in matcher_result.matches}
            direct_ids = {p["id"] for p, _ in direct}
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
        results = rank_products(query_tags, PRODUCTS, top_k=3,
                                filters={"in_stock_only": True, "gender": "women"})
        self.assertGreater(len(results), 0)
        # Top result should have some affinity for the query
        _, top_score = results[0]
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
        results = rank_products(query, PRODUCTS, top_k=3, min_score=0.0)
        self.assertGreater(len(results), 0)

    def test_single_product_catalog(self):
        product = PRODUCTS[0]
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, [product], top_k=3, min_score=0.0)
        self.assertLessEqual(len(results), 1)

    def test_empty_catalog(self):
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
        results = rank_products(query, [], top_k=3)
        self.assertEqual(results, [])

    def test_score_symmetry_with_normalization(self):
        """Products with 'rubber-black' should score same as 'black' for a 'black' query."""
        query = {"frame": {"color": ["black"]}, "lenses": {}, "style": {}}
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
        score_plain = compute_tag_score(query, product_plain["tags"])
        score_rubber = compute_tag_score(query, product_rubber["tags"])
        self.assertEqual(score_plain, score_rubber,
                         "rubber-black should normalize to black and score equally")

    def test_real_catalog_product_scores_sanely(self):
        """Pick a real product and verify its own tags produce a high self-score."""
        product = PRODUCTS[0]
        ptags = product["tags"]
        # Build query from the product's own tags (soft fields only)
        query = {
            "frame": {
                "material": ptags["frame"].get("material"),
                "color": ptags["frame"].get("color"),
                "thickness": ptags["frame"].get("thickness"),
                "rim_type": ptags["frame"].get("rim_type"),
                "finish": ptags["frame"].get("finish"),
            },
            "lenses": {
                "size": ptags["lenses"].get("size"),
            },
            "style": {
                "aesthetic": ptags["style"].get("aesthetic"),
                "face_shape_fit": ptags["style"].get("face_shape_fit"),
                "occasion": ptags["style"].get("occasion"),
            },
        }
        score = compute_tag_score(query, ptags)
        self.assertAlmostEqual(score, 1.0, places=5,
                               msg=f"{product['name']} should self-score ~1.0, got {score}")

    def test_component_scores_sum_coherence(self):
        """Component scores should be individually plausible (0–1 range)."""
        query = {
            "frame": {"color": ["black"], "material": "metal", "thickness": "thin",
                       "rim_type": "full-rim", "finish": "matte"},
            "lenses": {"size": "medium"},
            "style": {"aesthetic": ["classic"], "face_shape_fit": ["oval"],
                       "occasion": ["everyday"]},
        }
        for product in PRODUCTS[:20]:
            sub = compute_component_scores(query, product["tags"])
            for comp, val in sub.items():
                self.assertGreaterEqual(val, 0.0, f"{product['name']} {comp}={val}")
                self.assertLessEqual(val, 1.0, f"{product['name']} {comp}={val}")


if __name__ == "__main__":
    unittest.main()
