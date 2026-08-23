# Installing a Sentinel agent

> **Start the server first.** `make serve` builds the web console and runs it
> and the API together on one port, bound to every interface:
>
> ```
> make serve
> #   Console + API:  http://192.168.x.x:8000
> ```
>
> That one URL is the whole product — console, REST API, and the live
> WebSocket. Open it in a browser from any machine on the same network
> (Windows, Linux, macOS — it is a web app, there is nothing to install), and
> it is also the address an agent or the Android app is pointed at.
>
> `make dev-backend`/`make dev-frontend` are for developing Sentinel itself:
> both bind to localhost, so nothing else on the network can reach them.

An agent collects a machine's real metrics and pushes them **outbound** over an
encrypted WebSocket. It opens no inbound ports, works behind NAT and corporate
firewalls, and needs no elevated privileges.

You need an account on a Sentinel server and an **enrollment code** — mint one
on the *Devices* page. A code is single-use and expires; the agent exchanges it
for an opaque, device-scoped token you can revoke from the same page. **The
agent never sees your password.**

---

## 1. Get the binary

Sign in and open **Download**. The page detects your OS and offers the right
build, with its SHA-256.

If no build is listed for your platform, the page says so and gives you the
from-source command. That is not a broken page — see
[PACKAGING.md](PACKAGING.md): PyInstaller cannot cross-compile, so each
platform's binary has to be produced on that platform, and a server may only
have some of them.

### Running from source instead

Works everywhere, needs Python 3.11+, and is the same code. On Windows,
replace `.venv/bin/` with `.venv\Scripts\`:

```
cd agent
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\sentinel-agent enroll --code X4T9-K2QM-7PDR
.venv\Scripts\sentinel-agent run
```

**No packaged `.exe` exists yet.** No Windows binary has ever been built or
run — PyInstaller does not cross-compile, and this project has been developed
entirely on a Mac. Running from source is currently the only way to try this
on Windows, and even that has had exactly one bug already found and fixed by
static reading rather than by testing on a real Windows machine (see
[PACKAGING.md](PACKAGING.md)) — treat the first real run as the actual test,
report anything else that goes wrong, and expect it might be the first time
this exact code path has executed anywhere.

---

## 2. Expect your OS to block it

**Every published binary is currently unsigned.** No code-signing certificates
have been bought for this project. This is not hidden: the download page marks
each build `unsigned` and quotes the dialog you are about to see.

Check the SHA-256 first. With no signature, the checksum is the only thing
telling you the file is the one the server published.

### macOS — Gatekeeper

> "sentinel-agent" cannot be opened because it is from an unidentified developer.

```
shasum -a 256 sentinel-agent-0.1.0-macos-arm64     # compare with the page
chmod +x sentinel-agent-0.1.0-macos-arm64
xattr -d com.apple.quarantine sentinel-agent-0.1.0-macos-arm64
```

Or run it once, then **System Settings → Privacy & Security → Open Anyway**.
Both routes tell macOS to trust a file it cannot vouch for — hence the checksum
first.

Not sure which Mac you have? `uname -m` — `arm64` is Apple Silicon, `x86_64` is
Intel. Your browser will not tell the page: Safari and Chrome both report
Apple Silicon Macs as "Intel", permanently, for compatibility.

### Windows — SmartScreen

> Windows protected your PC — Microsoft Defender SmartScreen prevented an
> unrecognised app from starting.

```powershell
Get-FileHash -Algorithm SHA256 .\sentinel-agent-0.1.0-windows-x64.exe
```

Then **More info → Run anyway**. SmartScreen is reporting that this binary has
no reputation, which is true; the checksum is what replaces that reputation.

### Linux

No equivalent gate.

```
sha256sum sentinel-agent-0.1.0-linux-x64
chmod +x sentinel-agent-0.1.0-linux-x64
```

A binary built on the CI runner links against that runner's glibc. If it will
not start on an older distro, run from source.

---

## 3. Enrol

```
./sentinel-agent-0.1.0-macos-arm64 enroll --code X4T9-K2QM-7PDR
./sentinel-agent-0.1.0-macos-arm64 run
```

**Enrollment refuses a plaintext connection to a remote server.** It is the one
exchange carrying both the single-use code and the long-lived token it becomes,
so over `http://` anyone on the path ends up with a credential valid until
somebody notices and revokes it. `http://localhost` is fine; anything else
needs `https://`, or an explicit `--insecure` if you really are on a network
you trust. An already-enrolled agent that `run`s against `http://` warns rather
than refusing — stopping a working monitor over a transport choice its operator
already made would be the wrong trade.

Real numbers should appear on the dashboard within about ten seconds. To see
what this machine exposes without connecting to anything:

```
./sentinel-agent-… sample
```

Fields that read `null` are **not measurable on this platform** and are never
made up. On an Apple Silicon Mac, `cpu_freq_mhz` and `cpu_iowait_percent` are
genuinely unavailable; on Android most of the CPU family is (see
[ANDROID_METRICS.md](ANDROID_METRICS.md)).

---

## 4. Keep it running

```
./sentinel-agent-… install-service
./sentinel-agent-… status
./sentinel-agent-… uninstall-service
```

One command, three mechanisms — and the important differences:

### macOS — a launchd LaunchAgent

Per-user, no admin password. Installed at
`~/Library/LaunchAgents/com.sentinel.agent.plist`, logs at
`~/Library/Logs/sentinel/agent.log`.

It starts at **login**, not at boot, and stops at logout. For an always-on Mac
that must report before anyone logs in you need a LaunchDaemon, which is not
implemented — write `/Library/LaunchDaemons/com.sentinel.agent.plist` by hand
with the same keys plus `UserName`, put the config somewhere root owns
(`/Library/Application Support/Sentinel/agent.toml`, mode 0600), and
`sudo launchctl bootstrap system …`. `--scope system` refuses rather than
running untested code.

### Linux — a systemd unit

```
sentinel-agent install-service                  # ~/.config/systemd/user/, no root
sudo sentinel-agent --scope system install-service   # /etc/systemd/system/
journalctl --user -u sentinel-agent -f
```

A **user** unit stops when your last session ends unless lingering is enabled.
The installer runs `loginctl enable-linger` and tells you if it could not — on
a headless box, prefer `--scope system`.

A system-scope service reads `/etc/sentinel/agent.toml`, **not** your user
config, so enrol into it first:

```
sudo sentinel-agent --scope system enroll --code X4T9-K2QM-7PDR
sudo sentinel-agent --scope system install-service
```

The unit is hardened: no capabilities, `NoNewPrivileges`, `ProtectSystem=strict`.
The agent needs none of them.

### Windows — a Task Scheduler task, not a service

`services.msc` will **not** list the agent. Task Scheduler will, as
*Sentinel Agent*.

This is deliberate. A Windows service must answer the Service Control Manager
within ~30 seconds; a plain console binary cannot, so `sc.exe create` produces
a service Windows kills with error 1053. [PACKAGING.md](PACKAGING.md) has the
full reasoning and what a real service would cost.

```powershell
.\sentinel-agent-… install-service                       # at logon, no admin
.\sentinel-agent-… --scope system install-service        # at boot, as LocalSystem
```

The task is configured **not** to stop on battery and **not** to stop when the
idle period ends, both of which Task Scheduler does by default and either of
which would make a laptop agent look like an offline machine.

Task Scheduler also discards a task's output, so the installer passes
`--log-file` and the agent writes a rotating log itself:

```
%LOCALAPPDATA%\Sentinel\logs\agent.log      (user scope)
%ProgramData%\Sentinel\logs\agent.log       (system scope)
```

---

## Where the token lives

`agent.toml` holds the agent token. It is created with owner-only permissions
before the token is written into it, never after.

| | user scope | system scope |
|---|---|---|
| macOS | `~/.config/sentinel/` | `/Library/Application Support/Sentinel/` |
| Linux | `~/.config/sentinel/` | `/etc/sentinel/` |
| Windows | `%LOCALAPPDATA%\Sentinel\` | `%ProgramData%\Sentinel\` |

`sentinel-agent status` prints the resolved path and warns if the file is
readable by other users. `SENTINEL_AGENT_HOME` overrides the directory;
`--config` overrides the file.

---

## Monitoring an Android phone

The Android app is both a viewer and a monitored device. It enrols itself with
one tap — no code to copy — and keeps collecting with the app closed and after
a reboot. What a phone can honestly report is far less than a desktop and is
enumerated in [ANDROID_METRICS.md](ANDROID_METRICS.md); the visible consequence
is a health score with no CPU component, stated on screen rather than left as a
gap.

No APK is published: a release build needs a signing key that is not in this
repo, and shipping one signed with React Native's public debug key would let
anyone forge an update. Build it yourself with `make mobile-apk` after creating
a keystore — see [PACKAGING.md](PACKAGING.md).

---

## Removing an agent

```
sentinel-agent uninstall-service
```

Then delete the device on the **Devices** page. That revokes its token in the
same transaction — a deletion that leaves a working credential behind is not a
deletion. Finally remove `agent.toml`.
