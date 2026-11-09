"""Ticket pricing and coupon application."""

from __future__ import annotations

from .catalog import CATALOG, Coupon
from .errors import UnknownCouponError

TICKET_PRICE_CENTS = 12_500


def subtotal_cents(quantity: int) -> int:
    """Return the undiscounted price for ``quantity`` standard tickets."""
    return TICKET_PRICE_CENTS * quantity


def find_coupon(code: str) -> Coupon:
    """Return the catalog entry the customer typed in."""
    try:
        return CATALOG[code]
    except KeyError:
        raise UnknownCouponError(f"unknown coupon code: {code!r}") from None


def apply_coupon(code: str, amount_cents: int) -> int:
    """Return ``amount_cents`` after applying the coupon for ``code``."""
    coupon = find_coupon(code)
    discount = amount_cents * coupon.percent_off // 100
    return amount_cents - discount
