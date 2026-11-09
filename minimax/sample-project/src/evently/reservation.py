"""Reservation flow for a single event."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidQuantityError, SoldOutError, UnknownEventError
from .pricing import apply_coupon, subtotal_cents


@dataclass(frozen=True)
class Reservation:
    """A confirmed reservation."""

    event_id: str
    quantity: int
    total_cents: int
    coupon_code: str | None


class ReservationService:
    """In-memory reservation service with a per-event capacity."""

    def __init__(self, capacity: dict[str, int]) -> None:
        self._capacity = dict(capacity)
        self._sold = {event_id: 0 for event_id in self._capacity}

    def remaining(self, event_id: str) -> int:
        """Return the number of seats that can still be reserved."""
        if event_id not in self._capacity:
            raise UnknownEventError(f"unknown event: {event_id!r}")
        return self._capacity[event_id] - self._sold[event_id]

    def reserve(self, event_id: str, quantity: int, coupon_code: str | None = None) -> Reservation:
        """Reserve ``quantity`` seats, optionally applying ``coupon_code``."""
        if quantity <= 0:
            raise InvalidQuantityError(f"quantity must be positive, got {quantity}")
        remaining = self.remaining(event_id)
        if quantity > remaining:
            raise SoldOutError(f"{event_id} only has {remaining} seats left")

        total = subtotal_cents(quantity)
        if coupon_code is None:
            reservation = Reservation(event_id, quantity, total, None)
        else:
            total = apply_coupon(coupon_code, total)
            reservation = Reservation(event_id, quantity, total, coupon_code)

        self._sold[event_id] += quantity
        return reservation
