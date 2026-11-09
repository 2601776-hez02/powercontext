"""Regression tests for the reservation flow."""

import pytest

from evently import (
    InvalidQuantityError,
    ReservationService,
    SoldOutError,
    UnknownEventError,
)


def service() -> ReservationService:
    return ReservationService({"gig-1": 10})


def test_reserve_without_coupon() -> None:
    reservation = service().reserve("gig-1", 2)
    assert reservation.total_cents == 25_000
    assert reservation.coupon_code is None


def test_quantity_must_be_positive() -> None:
    for quantity in (0, -1):
        with pytest.raises(InvalidQuantityError):
            service().reserve("gig-1", quantity)


def test_capacity_is_enforced() -> None:
    with pytest.raises(SoldOutError):
        service().reserve("gig-1", 11)


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(UnknownEventError):
        service().reserve("does-not-exist", 1)


def test_successful_reservation_consumes_capacity() -> None:
    svc = service()
    svc.reserve("gig-1", 4)
    assert svc.remaining("gig-1") == 6


def test_uppercase_coupon_applies_to_reservation() -> None:
    reservation = service().reserve("gig-1", 2, "VIP20")
    assert reservation.total_cents == 20_000
    assert reservation.coupon_code == "VIP20"
