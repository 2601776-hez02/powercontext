# Reservation specification

| Field | Value |
| --- | --- |
| Owner | Ticketing domain |
| Status | active |
| Implementation | `src/evently/reservation.py` |
| Verified by | `tests/test_reservation.py`, `tests/test_coupon_policy.py` |

## RSV-1 Quantity

A reservation quantity MUST be a positive integer. Zero, negative numbers and
non-integers MUST raise `InvalidQuantityError` before any capacity is checked.

## RSV-2 Capacity

Each event has a fixed capacity. Reserving more seats than remain MUST raise
`SoldOutError`. Unknown event ids MUST raise `UnknownEventError`; they are not
silently treated as sold out.

A successful reservation consumes exactly the reserved quantity.

## RSV-3 Coupon application and capacity safety

Coupons are applied after the subtotal is computed, following the pricing
specification (PRC-2 through PRC-5). The reservation total MUST equal the
discounted amount.

A reservation that fails because the coupon is unknown, malformed or expired
MUST NOT consume capacity: the seats remain available for a later successful
reservation. In other words, coupon validation happens before the seat counter
is updated.

## RSV-4 Failed reservations are not recorded

A failed reservation MUST NOT appear in later reads of the reservation state.
Only successful reservations consume seats and are reported back to the caller.
