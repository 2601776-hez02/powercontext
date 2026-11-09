"""Regression tests for pricing behavior that must not change.

These tests describe behavior that already works in the delivered snapshot.
The SDD task must keep them green.
"""

from datetime import date

import pytest

from evently import CATALOG, UnknownCouponError, apply_coupon, find_coupon, subtotal_cents


def test_subtotal_multiplies_unit_price() -> None:
    assert subtotal_cents(1) == 12_500
    assert subtotal_cents(3) == 37_500


def test_vip20_takes_twenty_percent() -> None:
    assert apply_coupon("VIP20", 25_000) == 20_000


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(UnknownCouponError):
        find_coupon("NOPE")
    with pytest.raises(UnknownCouponError):
        apply_coupon("NOPE", 10_000)


def test_catalog_pins_the_campaign_dates() -> None:
    assert CATALOG["SPRING10"].valid_until < date.today()
    assert CATALOG["VIP20"].valid_until > date.today()
