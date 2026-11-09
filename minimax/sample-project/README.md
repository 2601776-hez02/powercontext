# Evently sample service

A deliberately small ticketing service used as the *business project* of the
SDD validation sample. It is a normal Python package with a pytest suite, no
network dependencies and no hidden state: `python -m pytest` is enough to see
whether the behavior matches the specification.

```
sample-project/
├── docs/specs/pricing.md       # PRC-1 .. PRC-6  (authoritative)
├── docs/specs/reservation.md   # RSV-1 .. RSV-4  (authoritative)
├── src/evently/
│   ├── catalog.py              # coupon catalog snapshot
│   ├── errors.py               # domain errors with stable codes
│   ├── pricing.py              # subtotal + coupon application
│   └── reservation.py          # capacity aware reservation flow
└── tests/
    ├── test_pricing.py         # regression: existing pricing behavior
    ├── test_reservation.py     # regression: existing reservation behavior
    └── test_coupon_policy.py   # acceptance tests for the current task
```

## Run

```bash
python -m pytest                 # whole suite
python -m pytest tests/test_coupon_policy.py   # the task's acceptance tests
```

## Delivered snapshot vs. task

`tests/test_coupon_policy.py` encodes PRC-3, PRC-4 and RSV-3. In the delivered
snapshot the coupon lookup is an exact dictionary lookup and the validity date
is never checked, so those tests fail. The SDD task is to change the pricing
path so the acceptance tests pass while every regression test keeps passing.

The sample is not modified by the demo: `scripts/replay.sh` copies it to
`.runtime/workdir`, applies the recorded patch there and runs the tests, so the
delivered baseline always stays reproducible.
