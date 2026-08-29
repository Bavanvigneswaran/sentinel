"""Proving a model file is the one the training script wrote.

`joblib.load` is pickle, and unpickling executes arbitrary code in the API
process — which holds the database credential and the JWT signing key. The
permission check in `novelty_service.py` narrows who can write the file;
this narrows it further, to *whoever holds the deployment's secret*, which is
the property that actually matters.

Every model is written with a detached HMAC-SHA256 sidecar (`<name>.sig`) keyed
on a value only this deployment has. `verify()` is checked before the bytes
reach the unpickler, so a file an attacker managed to write — by a permissions
drift the other check missed, a restored backup, a shared volume — is refused
rather than executed.

The key is derived from `JWT_SECRET` rather than being a setting of its own.
That is deliberate: one more secret to configure is one more that gets left at
its default, and anyone who already has the JWT secret can mint an access token
for any account, so a separate key would protect nothing they could not already
reach. It is derived rather than used directly so the two never collide —
signing a model and signing a token are different purposes and must not share a
key by accident.

This is not a substitute for a serialisation format that is not code. If models
ever need to move between machines or come from anywhere but the local training
script, replace joblib with skops or ONNX; an HMAC proves provenance, not
safety.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

SIGNATURE_SUFFIX = ".sig"

#: Keeps the model-signing key distinct from the token-signing one, so the same
#: JWT_SECRET cannot be made to serve two purposes by accident.
_DERIVATION_INFO = b"sentinel/novelty-model-hmac/v1"

#: Read in chunks: a fitted forest is ~26 MB on disk and there is no reason to
#: hold it in memory twice just to hash it.
_CHUNK = 1024 * 1024


def _key() -> bytes:
    secret = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(secret, _DERIVATION_INFO, hashlib.sha256).digest()


def signature_path(model_path: Path) -> Path:
    return model_path.with_suffix(model_path.suffix + SIGNATURE_SUFFIX)


def compute(model_path: Path) -> str:
    """The hex HMAC of a model file's bytes."""
    mac = hmac.new(_key(), digestmod=hashlib.sha256)
    with model_path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            mac.update(chunk)
    return mac.hexdigest()


def sign(model_path: Path) -> Path:
    """Write the sidecar beside a model. Called by the training script."""
    destination = signature_path(model_path)
    destination.write_text(compute(model_path))
    return destination


def verify(model_path: Path) -> bool:
    """True when the sidecar matches the file, both being readable.

    A missing sidecar is a failure, not a pass. Treating it as "unsigned, so
    allow" would mean an attacker deletes one file to bypass the check — which
    is the usual way a signature scheme is defeated in practice.
    """
    sidecar = signature_path(model_path)
    try:
        expected = sidecar.read_text().strip()
    except OSError:
        logger.error(
            "novelty model at %s has no readable signature at %s; refusing to load it",
            model_path,
            sidecar,
        )
        return False

    try:
        actual = compute(model_path)
    except OSError:
        return False

    # compare_digest, not ==: the comparison is over a value an attacker chooses
    # and can time, unlike the token lookups elsewhere in this codebase which
    # are unique-index hits.
    if not hmac.compare_digest(expected, actual):
        logger.error(
            "novelty model at %s does not match its signature; refusing to unpickle it",
            model_path,
        )
        return False
    return True


__all__ = ["compute", "sign", "signature_path", "verify"]
