"""The build manifest — the contract between CI and the download page.

Two things go quietly wrong in a release: a mislabelled architecture hands an
x64 binary to an Apple Silicon Mac, and a careless merge drops the platforms
built on the other CI runners. Both are asserted here.
"""

import json

import pytest

import agent_manifest as m


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "x64"),   # Linux, Intel macOS
        ("AMD64", "x64"),    # Windows spells it differently
        ("aarch64", "arm64"),  # Linux on ARM
        ("arm64", "arm64"),  # Apple Silicon
    ],
)
def test_every_spelling_of_an_architecture_collapses_to_one_key(machine, expected):
    """A manifest that says "x86_64" while the browser detects "x64" offers
    the user nothing at all."""
    assert m.arch_key(machine) == expected


def test_an_unknown_architecture_is_refused_not_guessed():
    with pytest.raises(m.ManifestError, match="riscv64"):
        m.arch_key("riscv64")


def test_an_unbuildable_platform_is_refused():
    with pytest.raises(m.ManifestError, match="FreeBSD"):
        m.os_key("FreeBSD")


def test_the_filename_carries_the_platform():
    """The file outlives the page that served it — someone will find it in a
    Downloads folder six months later and need to know what it is."""
    assert m.artifact_name("0.1.0", "macos", "arm64") == "sentinel-agent-0.1.0-macos-arm64"
    assert m.artifact_name("0.1.0", "linux", "x64") == "sentinel-agent-0.1.0-linux-x64"


def test_only_windows_gets_an_exe_suffix():
    assert m.artifact_name("0.1.0", "windows", "x64").endswith(".exe")
    assert not m.artifact_name("0.1.0", "linux", "x64").endswith(".exe")


def _entry(tmp_path, *, os_name="macos", arch="arm64", version="0.1.0", signed=False):
    path = tmp_path / m.artifact_name(version, os_name, arch)
    path.write_bytes(b"not really a binary")
    return m.build_entry(
        path,
        version=version,
        os_name=os_name,
        arch=arch,
        signed=signed,
        signing="unsigned: test",
    )


def test_an_entry_records_the_real_size_and_digest(tmp_path):
    entry = _entry(tmp_path)
    assert entry.size_bytes == len(b"not really a binary")
    assert entry.sha256 == m.sha256_file(tmp_path / entry.filename)
    assert len(entry.sha256) == 64


def test_signed_false_is_recorded_not_omitted(tmp_path):
    """An unsigned binary triggers Gatekeeper and SmartScreen, and the download
    page has to say so *before* the user meets the warning."""
    payload = _entry(tmp_path, signed=False).to_dict()
    assert payload["signed"] is False
    assert "signing" in payload


def test_merging_a_second_platform_keeps_the_first(tmp_path):
    """Three CI runners merge into the same file. This is the assertion that
    makes that safe."""
    manifest = m.merge(None, _entry(tmp_path, os_name="macos", arch="arm64"))
    manifest = m.merge(manifest, _entry(tmp_path, os_name="windows", arch="x64"))
    manifest = m.merge(manifest, _entry(tmp_path, os_name="linux", arch="x64"))

    assert {(b["os"], b["arch"]) for b in manifest["builds"]} == {
        ("macos", "arm64"),
        ("windows", "x64"),
        ("linux", "x64"),
    }


def test_rebuilding_replaces_rather_than_appends(tmp_path):
    """Otherwise the download page could still offer the superseded build."""
    first = _entry(tmp_path, version="0.1.0")
    manifest = m.merge(None, first)

    (tmp_path / m.artifact_name("0.2.0", "macos", "arm64")).write_bytes(b"newer")
    second = m.build_entry(
        tmp_path / m.artifact_name("0.2.0", "macos", "arm64"),
        version="0.2.0",
        os_name="macos",
        arch="arm64",
        signed=False,
        signing="unsigned: test",
    )
    manifest = m.merge(manifest, second)

    assert len(manifest["builds"]) == 1
    assert manifest["builds"][0]["version"] == "0.2.0"


def test_two_architectures_of_one_os_coexist(tmp_path):
    """Intel and Apple Silicon Macs are different downloads, not one that
    overwrites the other."""
    manifest = m.merge(None, _entry(tmp_path, os_name="macos", arch="arm64"))
    manifest = m.merge(manifest, _entry(tmp_path, os_name="macos", arch="x64"))
    assert len(manifest["builds"]) == 2


def test_a_manifest_from_a_future_schema_is_refused(tmp_path):
    """CI produces this file and a server reads it; those two get out of step."""
    with pytest.raises(m.ManifestError, match="schema_version"):
        m.merge({"schema_version": 99, "builds": []}, _entry(tmp_path))


def test_a_manifest_round_trips_through_disk(tmp_path):
    path = tmp_path / "manifest.json"
    m.write(path, m.merge(None, _entry(tmp_path)))

    loaded = m.load(path)
    assert loaded["schema_version"] == m.SCHEMA_VERSION
    assert loaded["builds"][0]["os"] == "macos"
    assert json.loads(path.read_text()) == loaded


def test_a_missing_manifest_loads_as_nothing(tmp_path):
    assert m.load(tmp_path / "absent.json") is None


def test_a_corrupt_manifest_is_an_error_not_a_silent_reset(tmp_path):
    """Swallowing this would drop every other platform's build on the next
    merge."""
    path = tmp_path / "manifest.json"
    path.write_text("{ not json")
    with pytest.raises(m.ManifestError):
        m.load(path)


# --- combining what a CI matrix produced ------------------------------------


def _single(tmp_path, os_name, arch, built_at=None):
    entry = _entry(tmp_path, os_name=os_name, arch=arch).to_dict()
    if built_at:
        entry["built_at"] = built_at
    return {"schema_version": m.SCHEMA_VERSION, "generated_at": None, "builds": [entry]}


def test_four_runners_combine_into_one_manifest(tmp_path):
    """PyInstaller does not cross-compile, so a release is assembled from
    machines that never see each other's output."""
    merged = m.merge_manifests(
        [
            _single(tmp_path, "macos", "arm64"),
            _single(tmp_path, "macos", "x64"),
            _single(tmp_path, "linux", "x64"),
            _single(tmp_path, "windows", "x64"),
        ]
    )
    assert len(merged["builds"]) == 4
    assert [b["os"] for b in merged["builds"]] == ["linux", "macos", "macos", "windows"]


def test_a_rerun_leg_supersedes_the_older_one(tmp_path):
    merged = m.merge_manifests(
        [
            _single(tmp_path, "linux", "x64", built_at="2026-01-01T00:00:00+00:00"),
            _single(tmp_path, "linux", "x64", built_at="2026-06-01T00:00:00+00:00"),
        ]
    )
    assert len(merged["builds"]) == 1
    assert merged["builds"][0]["built_at"] == "2026-06-01T00:00:00+00:00"


def test_merging_nothing_is_an_empty_manifest_not_a_crash(tmp_path):
    """A workflow where every build leg failed must still produce a readable
    manifest — the download page's "no build yet" state is a real state."""
    assert m.merge_manifests([])["builds"] == []


def test_a_stale_schema_stops_the_merge(tmp_path):
    with pytest.raises(m.ManifestError, match="schema_version"):
        m.merge_manifests([{"schema_version": 0, "builds": []}])
