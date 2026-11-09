"""Evently ticketing domain package."""

from .catalog import CATALOG, Coupon
from .errors import (
    DomainError,
    ExpiredCouponError,
    InvalidQuantityError,
    SoldOutError,
    UnknownCouponError,
    UnknownEventError,
)
from .pricing import TICKET_PRICE_CENTS, apply_coupon, find_coupon, subtotal_cents
from .reservation import Reservation, ReservationService

__all__ = [
    "CATALOG",
    "TICKET_PRICE_CENTS",
    "Coupon",
    "DomainError",
    "ExpiredCouponError",
    "InvalidQuantityError",
    "Reservation",
    "ReservationService",
    "SoldOutError",
    "UnknownCouponError",
    "UnknownEventError",
    "apply_coupon",
    "find_coupon",
    "subtotal_cents",
]
