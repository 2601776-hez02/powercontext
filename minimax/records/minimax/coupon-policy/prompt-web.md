### SYSTEM
You are a senior software engineer working on a repository that follows Spec-Driven Development. You receive the specification items that apply to one task, the code symbols that need to change, the tests that will judge you, and the persisted task state.

Rules:
- The specification is authoritative. Where it disagrees with the current code, the code is wrong.
- Change only the code shown under "Code to change" unless a specification forces a new file.
- Never edit, weaken or delete tests, and never rename a public symbol that the tests import.
- Keep every behavior the specification lists as preserved.
- Answer with a single unified diff in a ```diff fenced block and nothing else.
- Use repository-relative `a/<path>` and `b/<path>` headers so `git apply -p1` accepts the diff.

### USER
## Task

**Apply coupon codes case-insensitively and reject expired codes**

The reservation flow rejects coupon codes that customers type in lowercase, and it still accepts the retired SPRING10 campaign code because the validity date is never checked. Make coupon matching follow PRC-3 (case-insensitive, surrounding whitespace stripped, unknown codes unchanged) and make expired coupons fail with ExpiredCouponError per PRC-4, without consuming capacity (RSV-3). Pricing, capacity and quantity behavior must not change.

# Development context: Apply coupon codes case-insensitively and reject expired codes

- Task id: `coupon-policy`
- Generated: 2026-11-09T14:25:14Z
- Repository: `/root/minimax/.runtime/repo`
- Index: import `20261109T141752697580` · spec revision `18471ec47752` · code revision `0655c6f81590` · 50 items
- Storage: embedded-seekdb:/root/minimax/.runtime/seekdb (seekdb 5.7.25-OceanBase seekdb-v1.4.0.0)

## Task

The reservation flow rejects coupon codes that customers type in lowercase, and it still accepts the retired SPRING10 campaign code because the validity date is never checked. Make coupon matching follow PRC-3 (case-insensitive, surrounding whitespace stripped, unknown codes unchanged) and make expired coupons fail with ExpiredCouponError per PRC-4, without consuming capacity (RSV-3). Pricing, capacity and quantity behavior must not change.

Behavior that must be preserved:
- subtotal = quantity * 12500 cents (PRC-1)
- VIP20 keeps taking 20% off (PRC-2)
- unknown codes raise UnknownCouponError (PRC-3)
- quantity and capacity errors keep their exception types (RSV-1, RSV-2)

## Applicable specification items

### `docs/specs/reservation.md#RSV-3` (Coupon application and capacity safety)
- Revision: `845db18e9ab5` · score 0.563 · fulltext match; exact symbol/path match; identifier coverage 0.33; keyword coverage 0.58; text coverage 0.30

```text
## RSV-3 Coupon application and capacity safety

Coupons are applied after the subtotal is computed, following the pricing
specification (PRC-2 through PRC-5). The reservation total MUST equal the
discounted amount.

A reservation that fails because the coupon is unknown, malformed or expired
MUST NOT consume capacity: the seats remain available for a later successful
reservation. In other words, coupon validation happens before the seat counter
is updated.
```

### `docs/specs/pricing.md#PRC-3` (Coupon matching)
- Revision: `1fc2126f0081` · score 0.501 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.42; text coverage 0.34

```text
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
```

### `docs/specs/pricing.md#PRC-4` (Coupon validity)
- Revision: `1fc2126f0081` · score 0.478 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.33; text coverage 0.28

```text
## PRC-4 Coupon validity

A coupon is usable only while its `valid until` date is today or later. The
date is inclusive and evaluated against the UTC calendar date of the order.

Attempting to use an expired coupon MUST raise `ExpiredCouponError`. The order
total MUST remain unchanged and the failure MUST NOT be reported as an unknown
code, because support tooling treats the two cases differently. The public
error code for this failure is `coupon_expired`.
```

### `docs/specs/reservation.md#RSV-2` (Capacity)
- Revision: `845db18e9ab5` · score 0.463 · fulltext match; exact symbol/path match; identifier coverage 0.25; keyword coverage 0.25; text coverage 0.13

```text
## RSV-2 Capacity

Each event has a fixed capacity. Reserving more seats than remain MUST raise
`SoldOutError`. Unknown event ids MUST raise `UnknownEventError`; they are not
silently treated as sold out.

A successful reservation consumes exactly the reserved quantity.
```

## Code to change

### `src/evently/pricing.py#apply_coupon` (lines 24-28)
- Revision: `42054ffe235d` · score 0.447 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.33; text coverage 0.11

```python
def apply_coupon(code: str, amount_cents: int) -> int:
    """Return ``amount_cents`` after applying the coupon for ``code``."""
    coupon = find_coupon(code)
    discount = amount_cents * coupon.percent_off // 100
    return amount_cents - discount
```

### `src/evently/reservation.py#ReservationService` (lines 21-50)
- Revision: `03b71ba982dd` · score 0.439 · fulltext match; exact symbol/path match; identifier coverage 0.08; keyword coverage 0.33; text coverage 0.19

```python
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
```

### `src/evently/reservation.py#ReservationService.reserve` (lines 34-50)
- Revision: `03b71ba982dd` · score 0.433 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.25; text coverage 0.13

```python
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
```

### `src/evently/pricing.py#find_coupon` (lines 16-21)
- Revision: `42054ffe235d` · score 0.417 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.17; text coverage 0.13

```python
def find_coupon(code: str) -> Coupon:
    """Return the catalog entry the customer typed in."""
    try:
        return CATALOG[code]
    except KeyError:
        raise UnknownCouponError(f"unknown coupon code: {code!r}") from None
```

### `src/evently/errors.py#ExpiredCouponError` (lines 38-41)
- Revision: `faa9b5e69a6f` · score 0.414 · fulltext match; exact symbol/path match; identifier coverage 0.17; keyword coverage 0.17; text coverage 0.11

```python
class ExpiredCouponError(DomainError):
    """The coupon existed but is no longer valid for the order date."""

    code = "coupon_expired"
```

## Tests for verification

### `tests/test_coupon_policy.py#test_codes_are_matched_case_insensitively`
- Revision: `65220d8d8b33` · score 0.857 · fulltext match; exact symbol/path match; imports evently.errors, evently.pricing; test imports a selected symbol; identifier coverage 0.25; keyword coverage 0.50; text coverage 0.21; declared acceptance test for this task

```python
def test_codes_are_matched_case_insensitively() -> None:
    """PRC-3: matching is case-insensitive."""
    assert find_coupon("vip20").code == "VIP20"
    assert apply_coupon("vip20", 25_000) == 20_000
```

### `tests/test_coupon_policy.py#test_lowercase_coupon_applies_to_reservation`
- Revision: `65220d8d8b33` · score 0.857 · fulltext match; exact symbol/path match; imports evently.errors, evently.pricing; test imports a selected symbol; identifier coverage 0.25; keyword coverage 0.50; text coverage 0.21; declared acceptance test for this task

```python
def test_lowercase_coupon_applies_to_reservation() -> None:
    """PRC-3 + RSV-3: normalized codes apply inside the reservation flow."""
    reservation = service().reserve("gig-1", 2, "vip20")
    assert reservation.total_cents == 20_000
```

### `tests/test_coupon_policy.py#test_expired_coupon_does_not_change_the_total`
- Revision: `65220d8d8b33` · score 0.824 · fulltext match; exact symbol/path match; imports evently.errors, evently.pricing; test imports a selected symbol; identifier coverage 0.17; keyword coverage 0.42; text coverage 0.19; declared acceptance test for this task

```python
def test_expired_coupon_does_not_change_the_total() -> None:
    """PRC-4: the failure must not be reported as a discounted total."""
    with pytest.raises(ExpiredCouponError):
        apply_coupon("spring10", 25_000)
```

### `tests/test_coupon_policy.py#test_surrounding_whitespace_is_ignored`
- Revision: `65220d8d8b33` · score 0.824 · fulltext match; exact symbol/path match; imports evently.errors, evently.pricing; test imports a selected symbol; identifier coverage 0.17; keyword coverage 0.42; text coverage 0.19

```python
def test_surrounding_whitespace_is_ignored() -> None:
    """PRC-3: surrounding whitespace is stripped before matching."""
    assert find_coupon("  vip20\n").code == "VIP20"
    assert apply_coupon(" VIP20\t", 10_000) == 8_000
```

## Task state (persisted in PowerContext)

- Status: `verified` · updated 2026-11-09T13:31:31Z
- Done:
  - Read PRC-3, PRC-4 and RSV-3
  - Reproduced: lowercase code raises UnknownCouponError, expired code is still accepted
- Todo:
  - Normalize coupon codes before the catalog lookup
  - Reject expired coupons with ExpiredCouponError before capacity is consumed
- Verification results:
  - /root/minimax/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coupon_policy.py → exit 1 (1 passed, 6 failed) at 
  - /root/minimax/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_coupon_policy.py tests/test_pricing.py tests/test_reservation.py → exit 0 (17 passed) at 
- Next step: Send the context pack to MiniMax and review the patch

## Source freshness

- `docs/specs/reservation.md#RSV-3` → **ok** lines 23-33
- `docs/specs/pricing.md#PRC-3` → **ok** lines 29-42
- `docs/specs/pricing.md#PRC-4` → **ok** lines 43-52
- `docs/specs/reservation.md#RSV-2` → **ok** lines 15-22
- `src/evently/pricing.py#apply_coupon` → **ok** lines 24-28
- `src/evently/reservation.py#ReservationService` → **ok** lines 21-50
- `src/evently/reservation.py#ReservationService.reserve` → **ok** lines 34-50
- `src/evently/pricing.py#find_coupon` → **ok** lines 16-21
- `src/evently/errors.py#ExpiredCouponError` → **ok** lines 38-41
- `tests/test_coupon_policy.py#test_codes_are_matched_case_insensitively` → **ok** lines 22-25
- `tests/test_coupon_policy.py#test_lowercase_coupon_applies_to_reservation` → **ok** lines 46-49
- `tests/test_coupon_policy.py#test_expired_coupon_does_not_change_the_total` → **ok** lines 40-43
- `tests/test_coupon_policy.py#test_surrounding_whitespace_is_ignored` → **ok** lines 28-31

## Constraints for the change

- Modify only the code shown under "Code to change" unless a specification item forces a new file.
- Keep every behavior that the specification marks as preserved; the regression tests enforce it.
- Do not edit tests, do not delete assertions and do not rename public symbols.
- Follow the specification items above over any habit that contradicts them.


## Output

Return one unified diff in a ```diff block that makes the acceptance tests pass.
If a previous attempt failed, the failure output is included below; fix it without widening the change.
