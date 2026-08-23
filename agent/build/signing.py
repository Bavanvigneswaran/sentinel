"""Code signing — designed for, deliberately not configured.

No certificates have been bought for this project (ARCHITECTURE.md's Known
Constraints budgets for an Apple Developer ID at ~$99/yr and a Windows
code-signing certificate). The point of this module is that turning signing on
later is *configuration*, not a rewrite: the build already calls sign(), the
manifest already carries a `signed` field, and the download page already
renders the unsigned case honestly.

That is the same posture the rest of the project takes to unconfigured
integrations — SMTP, VAPID, FCM, ANTHROPIC_API_KEY — which no-op visibly rather
than pretending. Here the visible consequence is the download page telling the
user, before they click, exactly which OS warning they are about to see.

Configure by setting, at build time:

    macOS   SENTINEL_MACOS_SIGN_IDENTITY   "Developer ID Application: … (TEAMID)"
            SENTINEL_MACOS_NOTARY_PROFILE  a `notarytool store-credentials` profile
    Windows SENTINEL_WINDOWS_SIGN_THUMBPRINT  SHA-1 thumbprint of a cert in the store
            SENTINEL_WINDOWS_TIMESTAMP_URL    RFC-3161 timestamp server

Deliberately no `SENTINEL_WINDOWS_SIGN_PASSWORD`: signtool can take a .pfx and
its password on the command line, which puts a code-signing key's password into
the process table and every CI log that echoes commands. A certificate in the
machine store addressed by thumbprint — or a cloud signing service — is the
only form supported here.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 — codesign/signtool are the only interfaces
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


@dataclass(frozen=True, slots=True)
class SigningResult:
    signed: bool
    detail: str


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def sign(path: Path, os_name: str) -> SigningResult:
    """Sign `path` in place if this platform is configured for it."""
    if os_name == "macos":
        return _sign_macos(path)
    if os_name == "windows":
        return _sign_windows(path)
    return SigningResult(
        signed=False,
        detail=(
            "unsigned: Linux has no OS-level binary signature that a package "
            "manager-less download would check. The published SHA-256 is the "
            "integrity story here."
        ),
    )


def _sign_macos(path: Path) -> SigningResult:
    identity = os.environ.get("SENTINEL_MACOS_SIGN_IDENTITY")
    if not identity:
        return SigningResult(
            signed=False,
            detail="unsigned: SENTINEL_MACOS_SIGN_IDENTITY is not set (Gatekeeper will warn)",
        )
    if not shutil.which("codesign"):
        return SigningResult(signed=False, detail="unsigned: codesign not found on PATH")

    result = _run(
        [
            "codesign",
            "--force",
            # The hardened runtime is a notarization prerequisite, so it is not
            # optional even though signing alone would work without it.
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            identity,
            str(path),
        ]
    )
    if result.returncode != 0:
        return SigningResult(
            signed=False, detail=f"codesign failed: {(result.stderr or '').strip()}"
        )

    notary = os.environ.get("SENTINEL_MACOS_NOTARY_PROFILE")
    if not notary:
        return SigningResult(
            signed=True,
            detail=(
                f"signed with {identity}, not notarized "
                f"(Gatekeeper still warns on first open)"
            ),
        )
    return _notarize_macos(path, notary, identity)


def _notarize_macos(path: Path, profile: str, identity: str) -> SigningResult:
    """Submit the binary for notarization.

    notarytool takes an archive, not a bare Mach-O, so the binary is zipped for
    submission. The ticket cannot then be *stapled*: `stapler` only writes into
    a bundle, disk image or installer package, never a standalone executable.
    Gatekeeper therefore checks this one online, which is worth knowing before
    someone reports "it still warns on an air-gapped machine".
    """
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{path.name}.zip"
        zipped = _run(["ditto", "-c", "-k", "--keepParent", str(path), str(archive)])
        if zipped.returncode != 0:
            return SigningResult(
                signed=True, detail=f"signed; notarization skipped (ditto failed): {identity}"
            )
        result = _run(
            [
                "xcrun",
                "notarytool",
                "submit",
                str(archive),
                "--keychain-profile",
                profile,
                "--wait",
            ]
        )
    if result.returncode != 0:
        return SigningResult(
            signed=True,
            detail=f"signed with {identity}; notarization failed: {(result.stdout or '').strip()}",
        )
    return SigningResult(
        signed=True,
        detail=f"signed with {identity} and notarized (checked online; not stapleable)",
    )


def _sign_windows(path: Path) -> SigningResult:
    thumbprint = os.environ.get("SENTINEL_WINDOWS_SIGN_THUMBPRINT")
    if not thumbprint:
        return SigningResult(
            signed=False,
            detail=(
                "unsigned: SENTINEL_WINDOWS_SIGN_THUMBPRINT is not set "
                "(SmartScreen will warn)"
            ),
        )
    if not shutil.which("signtool"):
        return SigningResult(signed=False, detail="unsigned: signtool not found on PATH")

    timestamp = os.environ.get("SENTINEL_WINDOWS_TIMESTAMP_URL", DEFAULT_TIMESTAMP_URL)
    result = _run(
        [
            "signtool",
            "sign",
            "/fd",
            "sha256",
            # An RFC-3161 timestamp is what keeps the signature valid after the
            # certificate expires. Without it every binary ever shipped stops
            # verifying on the cert's expiry date.
            "/tr",
            timestamp,
            "/td",
            "sha256",
            "/sha1",
            thumbprint,
            str(path),
        ]
    )
    if result.returncode != 0:
        return SigningResult(
            signed=False,
            detail=f"signtool failed: {(result.stderr or result.stdout or '').strip()}",
        )
    return SigningResult(
        signed=True,
        detail=(
            f"signed with certificate {thumbprint[:8]}…, timestamped via {timestamp}. "
            f"SmartScreen reputation still accrues per-certificate over time."
        ),
    )
