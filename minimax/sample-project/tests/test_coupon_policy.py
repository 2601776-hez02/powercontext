"""Acceptance tests for the coupon policy task.

Every test in this file maps to a specification item:

* PRC-3 - coupon matching is case-insensitive and ignores surrounding whitespace
* PRC-4 - expired coupons raise ExpiredCouponError and keep the total untouched
* RSV-3 - a rejected coupon must not consume capacity

The delivered snapshot fails the tests below; the SDD task must make them pass
without breaking ``test_pricing.py`` or ``test_reservation.py``.
"""

import pytest

from evently import ExpiredCouponError, ReservationService, apply_coupon, find_coupon


def service() -> ReservationService:
    return ReservationService({"gig-1": 10})


def test_codes_are_matched_case_insensitively() -> None:
    """PRC-3: matching is case-insensitive."""
    assert find_coupon("vip20").code == "VIP20"
    assert apply_coupon("vip20", 25_000) == 20_000


def test_surrounding_whitespace_is_ignored() -> None:
    """PRC-3: surrounding whitespace is stripped before matching."""
    assert find_coupon("  vip20\n").code == "VIP20"
    assert apply_coupon(" VIP20\t", 10_000) == 8_000


def test_expired_coupon_is_rejected() -> None:
    """PRC-4: an expired coupon raises ExpiredCouponError."""
    with pytest.raises(ExpiredCouponError):
        apply_coupon("SPRING10", 10_000)


def test_expired_coupon_does_not_change_the_total() -> None:
    """PRC-4: the failure must not be reported as a discounted total."""
    with pytest.raises(ExpiredCouponError):
        apply_coupon("spring10", 25_000)


def test_lowercase_coupon_applies_to_reservation() -> None:
    """PRC-3 + RSV-3: normalized codes apply inside the reservation flow."""
    reservation = service().reserve("gig-1", 2, "vip20")
    assert reservation.total_cents == 20_000


def test_expired_coupon_does_not_consume_capacity() -> None:
    """RSV-3: coupon validation happens before the seat counter is updated."""
    svc = service()
    with pytest.raises(ExpiredCouponError):
        svc.reserve("gig-1", 1, "spring10")
    assert svc.remaining("gig-1") == 10


def test_unknown_code_is_still_unknown_after_normalization() -> None:
    """PRC-3: normalization must not make unknown codes valid."""
    from evently import UnknownCouponError

    with pytest.raises(UnknownCouponError):
        find_coupon("  nope ")
