"""Coupon catalog maintained by the marketing team.

The values here mirror the campaign sheet that product keeps in
``docs/specs/pricing.md``. The specification is the source of truth; this
table is the implementation snapshot of it.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Coupon:
    """One campaign coupon."""

    code: str
    percent_off: int
    valid_until: date


CATALOG: dict[str, Coupon] = {
    "SPRING10": Coupon(code="SPRING10", percent_off=10, valid_until=date(2020, 3, 31)),
    "VIP20": Coupon(code="VIP20", percent_off=20, valid_until=date(2099, 12, 31)),
}
