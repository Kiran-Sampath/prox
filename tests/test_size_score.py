"""
Tests for size_score: unit conversion, dimensions, and tolerance.
Uses parse_product(name, None) and size_score(a, b) with real-style inputs.
"""
import pytest

from src.normalize import parse_product
from src.match import size_score


def test_16_oz_vs_1_lb():
    """16 oz = 1 lb (same mass) → 1.0."""
    a = parse_product("Chicken 16 oz", None)
    b = parse_product("Chicken 1 lb", None)
    assert size_score(a, b) == 1.0


def test_500_ml_vs_05_L():
    """500 ml = 0.5 L (same volume) → 1.0."""
    a = parse_product("Milk 500 ml", None)
    b = parse_product("Milk 0.5 L", None)
    assert size_score(a, b) == 1.0


def test_6_oz_vs_6_fl_oz():
    """Weight oz vs volume fl oz (different dimensions) → 0.0."""
    a = parse_product("Yogurt 6 oz", None)
    b = parse_product("Yogurt 6 fl oz", None)
    assert size_score(a, b) == 0.0


def test_12_ct_vs_13_ct():
    """Count within ±1 → 1.0."""
    a = parse_product("Coke 12pk", None)
    b = parse_product("Coke 13pk", None)
    assert size_score(a, b) == 1.0


def test_12_ct_vs_24_ct():
    """Count outside ±1 → 0.0."""
    a = parse_product("Coke 12pk", None)
    b = parse_product("Coke 24pk", None)
    assert size_score(a, b) == 0.0


def test_missing_size_one_side():
    """One side missing size → 0.4 (unknown)."""
    a = parse_product("Chicken 16 oz", None)
    b = parse_product("Chicken Breast", None)
    assert size_score(a, b) == 0.4


def test_missing_size_both():
    """Both missing size → 0.4."""
    a = parse_product("Generic Pasta", None)
    b = parse_product("Store Brand Pasta", None)
    assert size_score(a, b) == 0.4


def test_numeric_within_2_pct():
    """Same dimension, within ±2% → 1.0."""
    a = parse_product("Juice 16 fl oz", None)
    b = parse_product("Juice 16.2 fl oz", None)
    assert size_score(a, b) == 1.0


def test_numeric_outside_2_pct():
    """Same dimension, outside ±2% → 0.0."""
    a = parse_product("Juice 16 fl oz", None)
    b = parse_product("Juice 20 fl oz", None)
    assert size_score(a, b) == 0.0


def test_2_lb_vs_32_oz():
    """2 lb = 32 oz (same mass) → 1.0."""
    a = parse_product("Ground Beef 2 lb", None)
    b = parse_product("Ground Beef 32 oz", None)
    assert size_score(a, b) == 1.0


def test_1_L_vs_1000_ml():
    """1 L = 1000 ml (same volume) → 1.0."""
    a = parse_product("Water 1 L", None)
    b = parse_product("Water 1000 ml", None)
    assert size_score(a, b) == 1.0


def test_count_exact_match():
    """Same count → 1.0."""
    a = parse_product("Eggs 12 count", None)
    b = parse_product("Eggs 12 ct", None)
    assert size_score(a, b) == 1.0


def test_mass_vs_volume_floz():
    """Mass (oz) vs volume (fl oz) → 0.0."""
    a = parse_product("Honey 12 oz", None)
    b = parse_product("Soda 12 fl oz", None)
    assert size_score(a, b) == 0.0
