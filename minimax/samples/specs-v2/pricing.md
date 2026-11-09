# Pricing and coupon specification

| Field | Value |
| --- | --- |
| Owner | Ticketing domain |
| Status | active |
| Revision | 2 |
| Implementation | `src/evently/pricing.py`, `src/evently/catalog.py` |
| Verified by | `tests/test_pricing.py`, `tests/test_coupon_policy.py` |

## PRC-1 Unit price

A standard ticket costs 12,500 cents (¥125.00). The subtotal of an order is
`quantity * 12,500` cents and is computed before any coupon is applied.
Introductory or dynamic pricing is out of scope for this revision.

## PRC-2 Coupon catalog

The campaign team publishes the following coupons:

| Code | Discount | Valid until |
| --- | --- | --- |
| `SPRING10` | 10% | 2020-03-31 |
| `VIP20` | 20% | 2099-12-31 |

`src/evently/catalog.py` is the implementation snapshot of this table. A code
that is not listed here does not exist, regardless of what the customer typed.

## PRC-3 Coupon matching

Coupon codes are matched **case-insensitively**. Surrounding whitespace
(spaces, tabs, newlines) MUST be stripped before matching. Codes are also
normalized by removing dashes, so `VIP-20` matches `VIP20`. Internal whitespace
is significant and no other normalization is allowed.

A code that does not resolve to a catalog entry MUST raise `UnknownCouponError`
and MUST NOT change the order total. Matching MUST NOT modify the catalog or
create new entries.

Every coupon decision is reported to the campaign dashboard once per order,
together with the matched code and the UTC order date.

## PRC-4 Coupon validity

A coupon is usable only while its `valid until` date is today or later. The
date is inclusive and evaluated against the UTC calendar date of the order.

Attempting to use an expired coupon MUST raise `ExpiredCouponError`. The order
total MUST remain unchanged and the failure MUST NOT be reported as an unknown
code, because support tooling treats the two cases differently. The public
error code for this failure is `coupon_expired`.

## PRC-5 Rounding

Percentage discounts are computed with integer arithmetic and rounded toward
zero (floor for non-negative amounts). A 10% discount on 12,500 cents is
therefore 1,250 cents, not 1,249.99.

## PRC-6 Out of scope

Stacking coupons, per-customer limits and refunds are not part of this
revision. Do not add behavior for them.
