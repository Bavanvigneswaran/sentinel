"""The ingest frame parser, against hostile input.

`/ws/agent` is the one place in this product where bytes from a machine we do
not control meet a schema. Everything else a stranger can reach is either
behind a JWT or is `/enroll`, which takes three short fields. So this is the
parser worth being paranoid about, and "it validates with Pydantic" is a claim
about the happy path rather than evidence.

The property asserted throughout is narrow on purpose: for *any* input, parsing
either succeeds or raises `ValidationError`. Never another exception type —
which would escape the handler in `app/ingest/ws.py`, close the socket with a
1011 instead of a clean protocol rejection, and log a stack trace containing
whatever the attacker sent. Never a hang, and never an allocation the size cap
was supposed to prevent.

Seeded so a failure is reproducible: an unrepeatable fuzz failure is a bug
report nobody can act on.
"""

from __future__ import annotations

import json
import math
import random
import string

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.protocol import (
    MAX_FRAME_BYTES,
    MAX_SAMPLES_PER_BATCH,
    PROTOCOL_VERSION,
    AgentFrame,
)

_adapter = TypeAdapter(AgentFrame)

#: The smallest HostInfo that validates — HelloFrame requires it.
HOST = {"hostname": "probe", "os": "Linux", "agent_version": "0.1.0"}

SEED = 20260829
ITERATIONS = 400


def parse(raw: str) -> object:
    """Parse, or raise. Any exception that is not ValidationError is a failure —
    that is the whole point of the test, so it is asserted here rather than in
    every caller."""
    try:
        return _adapter.validate_json(raw)
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 — the failure this file exists for
        pytest.fail(f"{type(exc).__name__} escaped the parser for {raw[:200]!r}: {exc}")


# --- structurally broken input ----------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "\x00",
        "null",
        "true",
        "0",
        "[]",
        "{}",
        "[[[[[[[[[[]]]]]]]]]]",
        '{"type": "hello"',            # truncated
        '{"type": "hello"}}',          # trailing garbage
        '{"type": null}',
        '{"type": []}',                # unhashable discriminator
        '{"type": {"a": 1}}',
        '{"type": "nonexistent"}',
        '{"type": "hello", "protocol_version": "not-an-int"}',
        '{"type": "hello", "protocol_version": 1}',            # no host
        '{"type": "metrics", "batch_id": "b"}',                # no samples
        '{"type": "metrics", "batch_id": "b", "samples": []}', # min_length=1
        '{"TYPE": "hello"}',           # wrong case
        '{"type": "metrics", "samples": null}',
        '{"type": "metrics", "samples": "not-a-list"}',
        '{"type": "metrics", "samples": [null]}',
        '{"type": "metrics", "samples": [[]]}',
        '﻿{"type": "hello"}',     # byte-order mark
        '{"type": "hello", "protocol_version": 1e400}',
        "\\",
        '"\\ud800"',                   # a lone surrogate
    ],
)
def test_structurally_broken_frames_raise_validation_error(raw):
    with pytest.raises(ValidationError):
        parse(raw)


# --- numbers that are valid JSON and not valid readings ----------------------


@pytest.mark.parametrize(
    "value",
    ["1e309", "-1e309", "NaN", "Infinity", "-Infinity",
     str(2**63), str(-(2**63)), str(10**400), "1" + "0" * 400],
)
def test_extreme_numbers_are_refused_not_crashed(value):
    """`cpu_percent` is bounded 0-100. A float that overflows, or is NaN, must
    come back as a validation failure rather than as a stored reading — CLAUDE.md's
    first hard rule is that every number on screen was really measured."""
    raw = (
        '{"type": "metrics", "batch_id": "b", "samples": [{"ts": "2026-08-29T00:00:00Z", '
        f'"resolution_seconds": 10, "system": {{"cpu_percent": {value}}}}}]}}'
    )
    with pytest.raises(ValidationError):
        parse(raw)


def test_a_denormal_underflows_to_a_real_zero_rather_than_being_refused():
    """`1e-400` is below float64's range and becomes 0.0. That is *accepted*,
    and correctly so: 0% CPU is a real reading, and refusing it would discard a
    measurement over the notation the agent happened to serialise it in.

    Asserted rather than assumed, because the obvious version of the test above
    expects a rejection and would have written the opposite behaviour into the
    parser if anyone had "fixed" it to match."""
    raw = (
        '{"type": "metrics", "batch_id": "b", "samples": [{"ts": "2026-08-29T00:00:00Z", '
        '"resolution_seconds": 10, "system": {"cpu_percent": 1e-400}}]}'
    )
    frame = parse(raw)
    assert frame.samples[0].system.cpu_percent == 0.0


def test_a_percentage_outside_its_bounds_is_refused():
    for value in (-0.1, 100.1, 1e6):
        raw = json.dumps({
            "type": "metrics",
            "batch_id": "b",
            "samples": [{
                "ts": "2026-08-29T00:00:00Z",
                "resolution_seconds": 10,
                "system": {"cpu_percent": value},
            }],
        })
        with pytest.raises(ValidationError):
            parse(raw)


# --- the caps are the defence, so assert they bite --------------------------


def test_a_batch_larger_than_the_cap_is_refused():
    """Not merely slow — refused. The cap exists so an agent cannot make the
    server build an unbounded object graph."""
    sample = {"ts": "2026-08-29T00:00:00Z", "resolution_seconds": 10}
    raw = json.dumps({
        "type": "metrics", "batch_id": "b",
        "samples": [sample] * (MAX_SAMPLES_PER_BATCH + 1),
    })
    with pytest.raises(ValidationError):
        parse(raw)


def test_deep_nesting_does_not_blow_the_stack():
    """A recursive-descent parser meeting 50k open brackets is the classic way
    to turn a parse into a segfault. Pydantic's is not, and this is the
    regression guard for the day someone swaps it."""
    raw = "[" * 50_000 + "]" * 50_000
    with pytest.raises(ValidationError):
        parse(raw)


def test_an_enormous_key_count_is_refused_cleanly():
    raw = json.dumps({"type": "hello", **{f"k{i}": i for i in range(20_000)}})
    with pytest.raises(ValidationError):
        parse(raw)


def test_the_size_cap_is_smaller_than_anything_the_parser_would_have_to_hold():
    """The route checks `len(raw) > MAX_FRAME_BYTES` *before* parsing. This
    asserts the constant is actually a bound worth having rather than one large
    enough to be decorative."""
    assert 0 < MAX_FRAME_BYTES <= 1024 * 1024


# --- randomised ---------------------------------------------------------------


def _mutate(raw: str, rng: random.Random) -> str:
    """One random edit: delete, duplicate, or replace a character."""
    if not raw:
        return rng.choice(["{", "}", "[", '"'])
    index = rng.randrange(len(raw))
    action = rng.randrange(3)
    if action == 0:
        return raw[:index] + raw[index + 1 :]
    if action == 1:
        return raw[:index] + raw[index] + raw[index:]
    noise = rng.choice(string.printable + "\x00퟿￿")
    return raw[:index] + noise + raw[index + 1 :]


def test_mutated_valid_frames_never_escape_the_parser():
    """Start from frames the agent really sends, break them one character at a
    time, and assert the failure mode never changes shape."""
    rng = random.Random(SEED)
    seeds = [
        json.dumps({"type": "hello", "protocol_version": PROTOCOL_VERSION, "host": HOST}),
        json.dumps({"type": "pong"}),
        json.dumps({"type": "metrics", "batch_id": "b", "samples": [
            {"ts": "2026-08-29T00:00:00Z", "resolution_seconds": 10,
             "system": {"cpu_percent": 12.5, "mem_percent": 40.0}},
        ]}),
    ]
    for _ in range(ITERATIONS):
        raw = rng.choice(seeds)
        for _ in range(rng.randrange(1, 6)):
            raw = _mutate(raw, rng)
        try:
            parse(raw)
        except ValidationError:
            pass


def test_random_bytes_never_escape_the_parser():
    rng = random.Random(SEED + 1)
    alphabet = string.printable + "{}[]\":,\\\x00\x1b"
    for _ in range(ITERATIONS):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 200)))
        try:
            parse(raw)
        except ValidationError:
            pass


def test_random_json_shapes_never_escape_the_parser():
    """Well-formed JSON of arbitrary shape — the case a byte-level fuzzer mostly
    misses, because almost every mutation it makes stops being JSON at all."""
    rng = random.Random(SEED + 2)

    def value(depth: int):
        if depth <= 0:
            return rng.choice([None, True, 0, -1, 1.5, "", "x" * 50])
        kind = rng.randrange(4)
        if kind == 0:
            return [value(depth - 1) for _ in range(rng.randrange(0, 4))]
        if kind == 1:
            return {f"k{i}": value(depth - 1) for i in range(rng.randrange(0, 4))}
        if kind == 2:
            return rng.choice(["hello", "metrics", "pong", "welcome", "ack"])
        return rng.choice([None, True, 0, -1, 1.5, math.pi])

    for _ in range(ITERATIONS):
        payload = value(rng.randrange(1, 5))
        if isinstance(payload, dict) and rng.random() < 0.5:
            payload["type"] = rng.choice(["hello", "metrics", "pong", "nope"])
        try:
            parse(json.dumps(payload))
        except ValidationError:
            pass


def test_a_valid_frame_still_parses_after_all_that():
    """The guard against a fuzz suite that passes because the parser rejects
    everything."""
    frame = parse(json.dumps({
        "type": "hello", "protocol_version": PROTOCOL_VERSION, "host": HOST,
    }))
    assert frame.protocol_version == PROTOCOL_VERSION
    assert frame.host.hostname == "probe"
