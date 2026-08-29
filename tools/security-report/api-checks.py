#!/usr/bin/env python3
"""Phase 4 of the security assessment, as something CI can run.

    python tools/security-report/api-checks.py --base-url http://127.0.0.1:8000 \
        --email e2e@example.com --password e2e-Sentinel-Test-2026

Non-destructive and detection-only, the same rules of engagement the manual
pass ran under. It creates a throwaway tenant (authorization cannot be tested
with one account) and a couple of devices in whatever database it is pointed
at, and it removes every web-push subscription it registers. Point it at the
`make e2e-db` stack, never at a real deployment.

Each check carries a severity, and that is what makes the workflow's "fail
only on Critical" mean something: an anonymous caller reaching a protected
route, or one tenant reading another's data, is Critical. A missing header or
a server banner is not, and a run that went red for those would stop being
read.

Writes `api-checks.json` and a GitHub-flavoured summary on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import uuid

import httpx

CRITICAL, HIGH, MEDIUM, LOW, INFO = "Critical", "High", "Medium", "Low", "Info"

REQUIRED_HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
)

INJECTION_PAYLOADS = (
    "' OR '1'='1",
    "1; DROP TABLE users--",
    "' UNION SELECT NULL--",
    "<script>alert(1)</script>",
    "{{7*7}}",
    "${jndi:ldap://x/a}",
    "\x00",
    "../../../../etc/passwd",
)

TRAVERSAL_PAYLOADS = (
    "../../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "/etc/passwd",
    "..\\..\\windows\\win.ini",
)

#: Registered and immediately removed. Never delivered to — that would need an
#: alert to fire, which is exploitation rather than detection.
SSRF_PAYLOADS = (
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "file:///etc/passwd",
)

results: list[dict] = []

#: Everything the probes create, so the run puts the tenant back as it found it.
#: This is not tidiness. The fleet being *empty* is a real state this product
#: renders on purpose, and both the Selenium and Appium suites assert it
#: (`devices-empty`, and the fleet totals reading zero). One probe device left
#: behind fails nine of their cases with an error that points at the console.
_created_devices: list[str] = []
_created_rules: list[str] = []


def record(name: str, severity: str, passed: bool | None, observed: str) -> None:
    results.append(
        {"check": name, "severity": severity, "observed": observed,
         "verdict": "PASS" if passed else ("FAIL" if passed is False else "INFO")}
    )
    mark = {True: "PASS", False: "FAIL", None: "INFO"}[passed]
    print(f"[{mark:4}] {severity:8} {name:52} {observed}", file=sys.stderr)


def b64url_decode(segment: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def b64url_encode(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def check_headers(client: httpx.Client, auth: dict) -> None:
    for path in ("/health", "/auth/me", "/", "/nonexistent-page"):
        response = client.get(path, headers=auth)
        missing = [h for h in REQUIRED_HEADERS if h not in response.headers]
        record(
            f"security headers on {path}", MEDIUM, not missing,
            "all present" if not missing else f"missing {missing}",
        )
    response = client.get("/health")
    # Gated on the request scheme, so it must be absent over plaintext. Present
    # here would mean a browser is being told to refuse plaintext to a host that
    # only speaks it — unrecoverable for a year.
    record(
        "HSTS is scheme-gated (absent over http)", MEDIUM,
        "strict-transport-security" not in response.headers,
        "absent" if "strict-transport-security" not in response.headers else "PRESENT over http",
    )
    record("server banner", LOW, None, response.headers.get("server", "(absent)"))


def check_anonymous_access(client: httpx.Client, spec: dict) -> None:
    """Every operation the schema marks as secured must refuse an anonymous call."""
    placeholder = str(uuid.uuid4())
    reachable, probed = [], 0
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            if not operation.get("security"):
                continue
            probed += 1
            url = path
            for name in ("device_id", "rule_id", "token_id", "silence_id",
                         "incident_id", "schedule_id"):
                url = url.replace("{" + name + "}", placeholder)
            url = url.replace("{filename}", "sentinel-agent")
            response = client.request(
                method.upper(), url,
                json={} if method in ("post", "patch", "put") else None,
            )
            if response.status_code not in (401, 403):
                reachable.append(f"{method.upper()} {path} -> {response.status_code}")
    record(
        f"all {probed} protected operations refuse an anonymous caller", CRITICAL,
        not reachable, "all 401/403" if not reachable else str(reachable),
    )


def check_jwt(client: httpx.Client, token: str) -> None:
    header, payload, signature = token.split(".")
    claims = b64url_decode(payload)

    def swap(**overrides: object) -> str:
        return f"{header}.{b64url_encode({**claims, **overrides})}.{signature}"

    attacks = {
        "alg=none": f"{b64url_encode({'alg': 'none', 'typ': 'JWT'})}.{payload}.",
        "signature stripped": f"{header}.{payload}.",
        "sub swapped": swap(sub=str(uuid.uuid4())),
        "signature flipped": f"{header}.{payload}.{signature[:-3]}AAA",
        "expired": swap(exp=int(time.time()) - 10),
        "wrong audience": swap(aud="attacker"),
        "typ confusion": swap(typ="refresh"),
        "garbage": "not-a-token",
    }
    for name, forged in attacks.items():
        code = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code
        record(f"jwt: {name} refused", CRITICAL, code == 401, str(code))

    code = client.get("/auth/me", headers={"Authorization": token}).status_code
    record("jwt: bearer scheme required", MEDIUM, code == 401, str(code))


def check_tenancy(client: httpx.Client, auth: dict) -> None:
    """Cross-tenant isolation. Needs a second account; there is no other way."""
    suffix = uuid.uuid4().hex[:8]
    device = client.post("/devices", headers=auth,
                         json={"name": f"seccheck-{suffix}", "platform": "desktop"})
    if device.status_code != 201:
        record("tenancy: cross-tenant isolation", CRITICAL, None,
               f"could not create a probe device: {device.status_code}")
        return
    device_id = device.json()["id"]
    _created_devices.append(device_id)

    rule = client.post("/alerts/rules", headers=auth, json={
        "name": f"seccheck-rule-{suffix}", "rule_type": "threshold",
        "metric": "cpu_percent", "comparison": ">", "threshold": 99.0,
        "for_duration_seconds": 60, "severity": "warning"})
    rule_id = rule.json()["id"] if rule.status_code in (200, 201) else None
    if rule_id:
        _created_rules.append(rule_id)

    signup = client.post("/auth/signup", json={
        "email": f"seccheck-{uuid.uuid4().hex[:10]}@sentinel.dev",
        "password": f"Seccheck-Probe-{suffix}-2026"})
    if signup.status_code != 201:
        record("tenancy: cross-tenant isolation", CRITICAL, None,
               f"could not create a probe tenant: {signup.status_code}")
        return
    attacker = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    leaks = []
    for method, url in (
        ("GET", f"/devices/{device_id}"),
        ("GET", f"/devices/{device_id}/summary"),
        ("GET", f"/devices/{device_id}/series"),
        ("GET", f"/devices/{device_id}/samples/recent"),
        ("GET", f"/devices/{device_id}/novelty"),
        ("DELETE", f"/devices/{device_id}"),
    ):
        response = client.request(method, url, headers=attacker)
        if response.status_code < 400:
            leaks.append(f"{method} {url} -> {response.status_code}")

    if rule_id:
        for method, url, body in (
            ("PATCH", f"/alerts/rules/{rule_id}", {"enabled": False}),
            ("DELETE", f"/alerts/rules/{rule_id}", None),
        ):
            response = client.request(method, url, headers=attacker, json=body)
            if response.status_code < 400:
                leaks.append(f"{method} {url} -> {response.status_code}")

    if client.post("/enrollment-codes", headers=attacker,
                   json={"device_id": device_id}).status_code == 201:
        leaks.append("POST /enrollment-codes minted a code for another tenant's device")

    for url in ("/devices", "/fleet/overview", "/incidents", "/alerts/events",
                "/forecasts", "/reports/analytics"):
        if device_id in client.get(url, headers=attacker).text:
            leaks.append(f"GET {url} leaked the other tenant's device id")

    record("tenancy: a second account is denied everywhere", CRITICAL,
           not leaks, "no cross-tenant access" if not leaks else str(leaks))
    record("tenancy: the probe device survived the DELETE attempt", CRITICAL,
           device_id in client.get("/devices", headers=auth).text, "intact")


def check_injection(client: httpx.Client, auth: dict) -> None:
    errors = []
    for payload in INJECTION_PAYLOADS:
        probes = (
            client.get("/devices", headers=auth, params={"include_removed": payload}),
            client.post("/auth/login", json={"email": payload, "password": payload}),
            client.get("/alerts/events", headers=auth,
                       params={"status": payload, "limit": payload}),
            client.post("/enroll", json={"code": payload, "device_name": payload[:100] or "x",
                                         "platform": "desktop"}),
        )
        errors += [f"{payload!r} -> {p.status_code}" for p in probes if p.status_code >= 500]
    record(f"injection: {len(INJECTION_PAYLOADS)} payload families x 4 endpoints", HIGH,
           not errors, "no 5xx" if not errors else str(errors))

    response = client.get("/devices", headers=auth,
                          params={"include_removed": "<script>alert(1)</script>"})
    reflected = "<script>" in response.text and "html" in response.headers.get("content-type", "")
    record("injection: no reflected XSS in an error body", HIGH, not reflected,
           "JSON, escaped" if not reflected else "REFLECTED AS HTML")


def check_traversal(client: httpx.Client, auth: dict) -> None:
    served = []
    for payload in TRAVERSAL_PAYLOADS:
        response = client.get(f"/downloads/agent/{payload}", headers=auth)
        if response.status_code == 200:
            served.append(f"{payload} -> 200")
    record(f"path traversal: {len(TRAVERSAL_PAYLOADS)} payloads on /downloads/agent",
           CRITICAL, not served, "all refused" if not served else str(served))

    escaped = []
    for payload in ("../backend/.env", "..%2f..%2fbackend%2f.env",
                    "/assets/../../backend/.env", "index.html%00.png"):
        response = client.get(f"/{payload}")
        if response.status_code == 200 and (
            "JWT_SECRET" in response.text or "DATABASE_URL" in response.text
        ):
            escaped.append(payload)
    record("path traversal: console static handler stays inside dist/", CRITICAL,
           not escaped, "no escape" if not escaped else str(escaped))


def check_session(client: httpx.Client, email: str, password: str) -> None:
    response = client.post("/auth/login", json={"email": email, "password": password})
    cookie = response.headers.get("set-cookie", "")
    record("session: refresh cookie is HttpOnly", HIGH, "HttpOnly" in cookie,
           "present" if "HttpOnly" in cookie else "MISSING")
    record("session: refresh cookie is SameSite=strict", HIGH, "SameSite=strict" in cookie,
           "strict" if "SameSite=strict" in cookie else cookie)
    record("session: access token is not in a cookie", HIGH, "access_token" not in cookie,
           "body only" if "access_token" not in cookie else "IN COOKIE")
    record("session: Secure flag", INFO, None,
           "present" if "Secure" in cookie else "absent (COOKIE_SECURE=false on this stack)")

    # A fresh client so the cookie jar holds exactly one refresh token.
    with httpx.Client(base_url=str(client.base_url), timeout=30) as fresh:
        login = fresh.post("/auth/login", json={"email": email, "password": password})
        presented = login.cookies.get("sentinel_refresh")
        first = fresh.post("/auth/refresh", cookies={"sentinel_refresh": presented})
        replay = fresh.post("/auth/refresh", cookies={"sentinel_refresh": presented})
        record("session: a replayed refresh token is refused", CRITICAL,
               first.status_code == 200 and replay.status_code == 401,
               f"first={first.status_code} replay={replay.status_code}")
        if first.status_code == 200:
            rotated = first.cookies.get("sentinel_refresh")
            after = fresh.post("/auth/refresh", cookies={"sentinel_refresh": rotated})
            record("session: reuse revokes the whole family", CRITICAL,
                   after.status_code == 401, str(after.status_code))

        login = fresh.post("/auth/login", json={"email": email, "password": password})
        presented = login.cookies.get("sentinel_refresh")
        fresh.post("/auth/logout", cookies={"sentinel_refresh": presented})
        after_logout = fresh.post("/auth/refresh", cookies={"sentinel_refresh": presented})
        record("session: logout revokes the family", HIGH,
               after_logout.status_code == 401, str(after_logout.status_code))


def check_enumeration(client: httpx.Client, email: str) -> None:
    unknown = client.post("/auth/login", json={
        "email": f"nobody-{uuid.uuid4().hex}@sentinel.dev", "password": "whatever123456"})
    wrong = client.post("/auth/login", json={"email": email, "password": "wrong-password-here"})
    record("authn: unknown email and wrong password are indistinguishable", MEDIUM,
           unknown.text == wrong.text and unknown.status_code == wrong.status_code,
           f"{unknown.status_code}/{wrong.status_code}, bodies "
           f"{'identical' if unknown.text == wrong.text else 'DIFFER'}")

    def mean(payload: dict) -> float:
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            client.post("/auth/login", json=payload)
            samples.append(time.perf_counter() - start)
        return sum(samples) / len(samples)

    unknown_ms = mean({"email": f"nobody-{uuid.uuid4().hex}@sentinel.dev",
                       "password": "whatever123456"})
    wrong_ms = mean({"email": email, "password": "wrong-password-here"})
    ratio = max(unknown_ms, wrong_ms) / max(min(unknown_ms, wrong_ms), 1e-9)
    record("authn: no timing oracle on the unknown-email path", MEDIUM, ratio < 1.5,
           f"{unknown_ms * 1000:.0f}ms vs {wrong_ms * 1000:.0f}ms, ratio {ratio:.2f}")

    for weak in ("short", "1234567", "aaaaaaaaaaa"):
        response = client.post("/auth/signup", json={
            "email": f"pw-{uuid.uuid4().hex[:8]}@sentinel.dev", "password": weak})
        if response.status_code == 201:
            record("authn: short passwords are rejected", MEDIUM, False,
                   f"{weak!r} ({len(weak)} chars) ACCEPTED")
            return
    record("authn: short passwords are rejected", MEDIUM, True, "all 3 refused")


def check_cors(client: httpx.Client) -> None:
    response = client.options("/auth/login", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    allowed = response.headers.get("access-control-allow-origin")
    record("cors: an arbitrary origin is not reflected", HIGH,
           allowed != "http://evil.example", f"allow-origin={allowed}")


def check_ssrf(client: httpx.Client, auth: dict) -> None:
    accepted = []
    for endpoint in SSRF_PAYLOADS:
        response = client.post("/notifications/web-push/subscribe", headers=auth,
                               json={"endpoint": endpoint, "p256dh": "x" * 40, "auth": "y" * 20})
        if response.status_code == 204:
            accepted.append(endpoint)
            # Registered only to observe acceptance; removed in the same breath.
            client.request("DELETE", "/notifications/web-push/subscribe",
                           headers=auth, json={"endpoint": endpoint})
    record("ssrf: web-push endpoint rejects internal and non-http URLs", MEDIUM,
           not accepted, "all refused" if not accepted
           else f"accepted {len(accepted)}/{len(SSRF_PAYLOADS)}: {accepted}")


def check_errors(client: httpx.Client, auth: dict) -> None:
    disclosed = []
    for method, url in (("GET", "/devices/not-a-uuid"), ("GET", "/alerts/events?limit=99999"),
                        ("GET", "/reports/export.pdf?period_days=99999")):
        response = client.request(method, url, headers=auth)
        disclosed += [f"{url}: {m}" for m in
                      ("Traceback", "sqlalchemy", "asyncpg", "psycopg", "/home/", "/Users/")
                      if m in response.text]
    record("errors: no stack traces or paths in a response body", MEDIUM, not disclosed,
           "none" if not disclosed else str(disclosed))

    response = client.post("/devices", headers=auth, json={
        "name": f"seccheck-{uuid.uuid4().hex[:8]}", "platform": "desktop",
        "status": "online", "user_id": str(uuid.uuid4())})
    if response.status_code == 201:
        _created_devices.append(response.json()["id"])
    ignored = response.status_code == 201 and response.json().get("status") != "online"
    record("mass assignment: server-owned fields are ignored", HIGH, ignored,
           "ignored" if ignored else f"{response.status_code} {response.text[:80]}")

    code = client.request("TRACE", "/health").status_code
    record("http: TRACE is refused", LOW, code >= 400, str(code))


async def check_websockets(base_url: str, client: httpx.Client, auth: dict) -> None:
    try:
        import websockets
    except ImportError:
        record("websocket probes", INFO, None, "websockets not installed; skipped")
        return

    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")

    async def connect(url: str, headers: dict | None = None) -> str:
        try:
            async with websockets.connect(url, additional_headers=headers or {},
                                          open_timeout=10) as socket:
                try:
                    await asyncio.wait_for(socket.recv(), timeout=3)
                except asyncio.TimeoutError:
                    pass
                return "ACCEPTED"
        except Exception as exc:  # noqa: BLE001 — any failure is a refusal
            return f"refused ({type(exc).__name__})"

    outcome = await connect(f"{ws_base}/ws/viewer")
    record("ws: viewer refuses a missing ticket", CRITICAL, outcome != "ACCEPTED", outcome)

    outcome = await connect(f"{ws_base}/ws/viewer?ticket={'a' * 43}")
    record("ws: viewer refuses a fabricated ticket", CRITICAL, outcome != "ACCEPTED", outcome)

    minted = client.post("/ws/tickets", headers=auth)
    if minted.status_code in (200, 201):
        ticket = minted.json()["ticket"]
        outcome = await connect(f"{ws_base}/ws/viewer?ticket={ticket}")
        record("ws: viewer accepts a valid ticket", HIGH, outcome == "ACCEPTED", outcome)
        replay = await connect(f"{ws_base}/ws/viewer?ticket={ticket}")
        record("ws: a ticket is single-use", CRITICAL, replay != "ACCEPTED", replay)

    outcome = await connect(f"{ws_base}/ws/agent")
    record("ws: ingest refuses a missing agent token", CRITICAL, outcome != "ACCEPTED", outcome)

    outcome = await connect(f"{ws_base}/ws/agent",
                            {"Authorization": "Bearer sag_" + "a" * 43})
    record("ws: ingest refuses a fabricated agent token", CRITICAL,
           outcome != "ACCEPTED", outcome)

    outcome = await connect(f"{ws_base}/ws/agent",
                            {"Authorization": auth["Authorization"]})
    record("ws: a user access JWT is not an agent token", CRITICAL,
           outcome != "ACCEPTED", outcome)

    # Origin checking. Neither socket was exploitable without it — a ticket
    # needs the Bearer token, which lives in page memory — but a cross-site
    # handshake carrying a valid ticket used to be accepted, and the property
    # holding that closed lived in another file entirely.
    host = base_url.split("//", 1)[-1]
    minted = client.post("/ws/tickets", headers=auth)
    if minted.status_code in (200, 201):
        outcome = await connect(f"{ws_base}/ws/viewer?ticket={minted.json()['ticket']}",
                                {"Origin": "http://evil.example"})
        record("ws: viewer refuses a foreign Origin", HIGH, outcome != "ACCEPTED", outcome)

    minted = client.post("/ws/tickets", headers=auth)
    if minted.status_code in (200, 201):
        outcome = await connect(f"{ws_base}/ws/viewer?ticket={minted.json()['ticket']}",
                                {"Origin": f"http://{host}"})
        record("ws: viewer still accepts its own Origin", HIGH,
               outcome == "ACCEPTED", outcome)

    # An agent sends no Origin at all, so this only ever rejects a browser —
    # which could not set the Authorization header anyway.
    outcome = await connect(f"{ws_base}/ws/agent",
                            {"Origin": "http://evil.example",
                             "Authorization": "Bearer sag_" + "a" * 43})
    record("ws: ingest refuses a browser Origin", MEDIUM, outcome != "ACCEPTED", outcome)


def cleanup(client: httpx.Client, auth: dict) -> None:
    """Remove the devices and rules the probes created.

    Deliberately *only* what this run made, matched by id rather than by a name
    prefix: a sweep over anything called `seccheck-*` would delete a real
    device somebody happened to name that way, and a security probe that
    removes production data is worse than one that leaves litter.

    The throwaway tenants created for the authorization checks stay — there is
    no delete-account endpoint, they own nothing, and they are invisible to the
    account under test. `make e2e-db` drops them with the database.
    """
    removed = 0
    for rule_id in _created_rules:
        removed += client.delete(f"/alerts/rules/{rule_id}", headers=auth).status_code == 204
    for device_id in _created_devices:
        removed += client.delete(f"/devices/{device_id}", headers=auth).status_code == 204
    record("cleanup: probe devices and rules removed", INFO, None,
           f"{removed}/{len(_created_devices) + len(_created_rules)} removed; "
           "the fleet is empty again")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--out", default="api-checks.json")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=60.0, follow_redirects=False)

    login = client.post("/auth/login", json={"email": args.email, "password": args.password})
    if login.status_code != 200:
        print(f"could not sign in as {args.email}: {login.status_code} {login.text[:200]}",
              file=sys.stderr)
        return 2
    token = login.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    spec = client.get("/openapi.json")
    if spec.status_code == 200:
        check_anonymous_access(client, spec.json())
    else:
        # 404 is correct in prod, and it means this check cannot enumerate.
        record("all protected operations refuse an anonymous caller", CRITICAL, None,
               f"/openapi.json is {spec.status_code}; cannot enumerate")

    check_headers(client, auth)
    check_jwt(client, token)
    check_tenancy(client, auth)
    check_injection(client, auth)
    check_traversal(client, auth)
    check_session(client, args.email, args.password)
    check_enumeration(client, args.email)
    check_cors(client)
    check_ssrf(client, auth)
    check_errors(client, auth)
    asyncio.run(check_websockets(args.base_url, client, auth))
    cleanup(client, auth)

    with open(args.out, "w") as handle:
        json.dump({"base_url": args.base_url, "checks": results}, handle, indent=1)

    failed = [r for r in results if r["verdict"] == "FAIL"]
    critical = [r for r in failed if r["severity"] == CRITICAL]

    print(f"\n## API security checks — {args.base_url}\n")
    print(f"{len(results)} checks · "
          f"{sum(1 for r in results if r['verdict'] == 'PASS')} passed · "
          f"{len(failed)} failed · "
          f"{sum(1 for r in results if r['verdict'] == 'INFO')} informational\n")
    if failed:
        print("| Severity | Check | Observed |")
        print("| --- | --- | --- |")
        for r in sorted(failed, key=lambda r: [CRITICAL, HIGH, MEDIUM, LOW, INFO]
                        .index(r["severity"])):
            print(f"| {r['severity']} | {r['check']} | {r['observed']} |")
    else:
        print("No failures.")

    # Non-zero only for Critical, matching the workflow's gate. A Medium here is
    # a finding to read, not a reason to stop the build.
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
