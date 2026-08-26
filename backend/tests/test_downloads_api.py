"""The agent download catalogue.

Most of these are about the *absence* of a build, which is the state this
server is actually in: no CI has published anything to it. Following the
project's existing posture for unset SMTP/VAPID/FCM, that has
to degrade visibly — a reason the page can render — never an empty list the UI
must guess about, and never a link that 404s.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services import download_service as svc

SIGNUP = "/auth/signup"
CREDS = {"email": "downloads-owner@example.com", "password": "a-perfectly-fine-password"}

CATALOG = "/downloads/agent"


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, environment="test", **overrides)


def _point_at(monkeypatch, directory, **overrides):
    monkeypatch.setattr(
        svc,
        "get_settings",
        lambda: _settings(agent_dist_dir=str(directory), **overrides),
    )


def _build(**overrides) -> dict:
    entry = {
        "os": "macos",
        "arch": "arm64",
        "version": "0.1.0",
        "filename": "sentinel-agent-0.1.0-macos-arm64",
        "size_bytes": 19,
        "sha256": "a" * 64,
        "signed": False,
        "signing": "unsigned: SENTINEL_MACOS_SIGN_IDENTITY is not set (Gatekeeper will warn)",
        "built_at": "2026-08-23T10:00:00+00:00",
        "built_on": "Darwin 25.5.0",
    }
    entry.update(overrides)
    return entry


def _write_manifest(directory, builds, *, schema_version=svc.SCHEMA_VERSION):
    (directory / svc.MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "generated_at": "2026-08-23T10:00:00+00:00",
                "builds": builds,
            }
        )
    )
    for build in builds:
        name = build.get("filename")
        if isinstance(name, str) and name and "/" not in name and not name.startswith(".."):
            (directory / name).write_bytes(b"not really a binary")


# --- authentication ---------------------------------------------------------


async def test_the_catalogue_requires_authentication(client):
    """Not because a binary is secret, but because making it public would add a
    second unauthenticated route beside /enroll for no gain."""
    assert (await client.get(CATALOG)).status_code == 401


async def test_downloading_a_build_requires_authentication(client):
    assert (await client.get(f"{CATALOG}/sentinel-agent-0.1.0-macos-arm64")).status_code == 401


# --- nothing published ------------------------------------------------------


async def test_an_unconfigured_server_says_so_rather_than_returning_nothing(
    client, monkeypatch
):
    monkeypatch.setattr(svc, "get_settings", lambda: _settings(agent_dist_dir=None))

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()

    assert body["configured"] is False
    assert body["builds"] == []
    assert "AGENT_DIST_DIR" in body["unavailable_reason"]


async def test_a_configured_directory_with_no_manifest_still_explains_itself(
    client, monkeypatch, tmp_path
):
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()

    assert body["configured"] is True
    assert body["builds"] == []
    assert body["unavailable_reason"]


async def test_a_corrupt_manifest_degrades_instead_of_500ing(client, monkeypatch, tmp_path):
    (tmp_path / svc.MANIFEST_FILENAME).write_text("{ not json")
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(CATALOG, headers=await _auth_headers(client))

    assert resp.status_code == 200
    assert resp.json()["unavailable_reason"] is not None


async def test_a_manifest_from_incompatible_tooling_is_refused(client, monkeypatch, tmp_path):
    """CI writes this file on a machine the server never sees; the two get out
    of step."""
    _write_manifest(tmp_path, [_build()], schema_version=99)
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()

    assert body["builds"] == []
    assert "incompatible" in body["unavailable_reason"]


async def test_an_empty_build_list_still_carries_a_reason(client, monkeypatch, tmp_path):
    """A catalogue with no builds and no reason is indistinguishable from one
    that failed to load, and the page would have nothing honest to say."""
    _write_manifest(tmp_path, [])
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()
    assert body["unavailable_reason"] is not None


# --- a real catalogue -------------------------------------------------------


async def test_a_published_build_is_offered_with_its_checksum(client, monkeypatch, tmp_path):
    _write_manifest(tmp_path, [_build()])
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()

    assert body["unavailable_reason"] is None
    (build,) = body["builds"]
    assert build["os"] == "macos"
    assert build["arch"] == "arm64"
    assert build["sha256"] == "a" * 64
    assert build["download_url"] == f"{CATALOG}/{build['filename']}"


async def test_an_unsigned_build_is_reported_as_unsigned(client, monkeypatch, tmp_path):
    """The page has to say Gatekeeper will interrupt *before* the click."""
    _write_manifest(tmp_path, [_build()])
    _point_at(monkeypatch, tmp_path)

    (build,) = (await client.get(CATALOG, headers=await _auth_headers(client))).json()["builds"]

    assert build["signed"] is False
    assert "Gatekeeper" in build["signing"]


async def test_an_external_host_takes_over_the_download_url(client, monkeypatch, tmp_path):
    """Streaming multi-megabyte files out of the app process is fine for a
    handful of installs and wrong at any scale."""
    _write_manifest(tmp_path, [_build()])
    _point_at(monkeypatch, tmp_path, agent_download_base_url="https://cdn.example.com/agent/")

    (build,) = (await client.get(CATALOG, headers=await _auth_headers(client))).json()["builds"]

    assert build["download_url"] == (
        "https://cdn.example.com/agent/sentinel-agent-0.1.0-macos-arm64"
    )


async def test_several_platforms_are_listed_together(client, monkeypatch, tmp_path):
    _write_manifest(
        tmp_path,
        [
            _build(),
            _build(os="windows", arch="x64", filename="sentinel-agent-0.1.0-windows-x64.exe"),
            _build(os="linux", arch="x64", filename="sentinel-agent-0.1.0-linux-x64"),
        ],
    )
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()
    assert {b["os"] for b in body["builds"]} == {"macos", "windows", "linux"}


async def test_android_is_a_valid_platform_in_the_catalogue(client, monkeypatch, tmp_path):
    """Phase 10b's collector is a real agent even though Gradle builds it and
    build.py never will."""
    _write_manifest(
        tmp_path,
        [_build(os="android", arch="arm64", filename="sentinel-0.1.0-android-arm64.apk")],
    )
    _point_at(monkeypatch, tmp_path)

    (build,) = (await client.get(CATALOG, headers=await _auth_headers(client))).json()["builds"]
    assert build["os"] == "android"


# --- entries that must not be rendered --------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["../../../etc/passwd", "sub/dir/agent", ".hidden", "", "/etc/passwd"],
)
async def test_a_manifest_filename_that_is_not_a_plain_name_is_dropped(
    client, monkeypatch, tmp_path, filename
):
    """The manifest is written elsewhere and validated here, not trusted. A
    name is checked as it enters the system, so the allow-list the file route
    consults can never contain a path."""
    _write_manifest(tmp_path, [_build(filename=filename)])
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()
    assert body["builds"] == []


async def test_an_unknown_platform_is_dropped_not_rendered_unlabelled(
    client, monkeypatch, tmp_path
):
    _write_manifest(tmp_path, [_build(os="haiku"), _build(arch="riscv64")])
    _point_at(monkeypatch, tmp_path)

    assert (await client.get(CATALOG, headers=await _auth_headers(client))).json()["builds"] == []


async def test_a_malformed_entry_does_not_take_the_good_ones_with_it(
    client, monkeypatch, tmp_path
):
    _write_manifest(tmp_path, [{"os": "macos"}, _build()])
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()
    assert len(body["builds"]) == 1


# --- serving the file -------------------------------------------------------


async def test_a_listed_build_downloads(client, monkeypatch, tmp_path):
    _write_manifest(tmp_path, [_build()])
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(
        f"{CATALOG}/sentinel-agent-0.1.0-macos-arm64", headers=await _auth_headers(client)
    )

    assert resp.status_code == 200
    assert resp.content == b"not really a binary"
    assert resp.headers["content-type"] == "application/octet-stream"


async def test_a_file_that_is_not_in_the_manifest_is_not_served(client, monkeypatch, tmp_path):
    """Even though it is sitting right there in the same directory."""
    _write_manifest(tmp_path, [_build()])
    (tmp_path / "private-notes.txt").write_text("secret")
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(f"{CATALOG}/private-notes.txt", headers=await _auth_headers(client))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "attempt",
    [
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "....//....//etc/passwd",
        "sentinel-agent-0.1.0-macos-arm64%00.txt",
    ],
)
async def test_path_traversal_gets_nothing(client, monkeypatch, tmp_path, attempt):
    _write_manifest(tmp_path, [_build()])
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(f"{CATALOG}/{attempt}", headers=await _auth_headers(client))
    assert resp.status_code == 404


async def test_a_manifest_entry_whose_file_vanished_404s_rather_than_500s(
    client, monkeypatch, tmp_path
):
    _write_manifest(tmp_path, [_build()])
    (tmp_path / "sentinel-agent-0.1.0-macos-arm64").unlink()
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(
        f"{CATALOG}/sentinel-agent-0.1.0-macos-arm64", headers=await _auth_headers(client)
    )
    assert resp.status_code == 404


async def test_a_symlink_pointing_out_of_the_dist_directory_is_refused(
    client, monkeypatch, tmp_path
):
    """Second line of defence: the allow-list already matched the name, so this
    is the check that catches a symlink planted inside the dist directory."""
    outside = tmp_path / "outside.txt"
    outside.write_text("not for you")
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_manifest(dist, [_build(filename="escape")])
    (dist / "escape").unlink()
    (dist / "escape").symlink_to(outside)
    _point_at(monkeypatch, dist)

    resp = await client.get(f"{CATALOG}/escape", headers=await _auth_headers(client))
    assert resp.status_code == 404


async def test_the_reason_does_not_leak_the_server_filesystem_layout(
    client, monkeypatch, tmp_path
):
    """Every reason string is rendered verbatim on a page. An absolute server
    path hands any signed-in user a piece of the deployment's layout for no
    benefit — they cannot act on it, and the operator reads the log."""
    _point_at(monkeypatch, tmp_path)

    body = (await client.get(CATALOG, headers=await _auth_headers(client))).json()

    assert str(tmp_path) not in body["unavailable_reason"]
    assert "/" not in body["unavailable_reason"]


@pytest.mark.parametrize(
    ("builds", "label"),
    [
        ([{**_build(), "os": []}], "an unhashable os"),
        ([{**_build(), "arch": {"a": 1}}], "a dict arch"),
        ([{**_build(), "size_bytes": {}}], "a dict size"),
        ([{**_build(), "filename": 42}], "a numeric filename"),
        (5, "a builds field that is not a list"),
        ({"a": 1}, "a builds field that is an object"),
        ([None, 42, "nope"], "junk entries"),
    ],
)
async def test_no_manifest_shape_can_500_the_catalogue(
    client, monkeypatch, tmp_path, builds, label
):
    """This file is written by CI on a machine the server never sees, so
    "validated, not trusted" has to mean no input shape reaches an exception.

    `{"os": []}` in particular makes `raw["os"] not in KNOWN_OS` raise
    TypeError on an unhashable key — a 500 rather than the visible degradation
    this module exists for.
    """
    (tmp_path / svc.MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": svc.SCHEMA_VERSION, "builds": builds})
    )
    _point_at(monkeypatch, tmp_path)

    resp = await client.get(CATALOG, headers=await _auth_headers(client))

    assert resp.status_code == 200, label
    assert resp.json()["builds"] == [], label
    assert resp.json()["unavailable_reason"] is not None, label


async def test_a_backslash_filename_is_not_offered(client, monkeypatch, tmp_path):
    """A backslash is not a path separator on POSIX, so Path().name leaves
    `..\\..\\x` intact. It cannot escape the dist directory, but it has no
    business being rendered as a build either."""
    _write_manifest(tmp_path, [_build(filename=r"..\..\etc\passwd")])
    _point_at(monkeypatch, tmp_path)

    assert (await client.get(CATALOG, headers=await _auth_headers(client))).json()["builds"] == []
