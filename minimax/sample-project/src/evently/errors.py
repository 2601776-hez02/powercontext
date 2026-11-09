"""Domain errors raised by the ticketing service.

Every error carries a stable ``code`` because the HTTP layer maps domain
errors onto machine readable problem codes.
"""


class DomainError(Exception):
    """Base class for every expected ticketing failure."""

    code = "domain_error"


class InvalidQuantityError(DomainError):
    """The requested number of tickets is not a positive integer."""

    code = "invalid_quantity"


class UnknownEventError(DomainError):
    """The event id is not present in the catalog."""

    code = "unknown_event"


class SoldOutError(DomainError):
    """The event cannot seat the requested quantity."""

    code = "sold_out"


class UnknownCouponError(DomainError):
    """The coupon code does not exist in the campaign catalog."""

    code = "unknown_coupon"


class ExpiredCouponError(DomainError):
    """The coupon existed but is no longer valid for the order date."""

    code = "coupon_expired"
