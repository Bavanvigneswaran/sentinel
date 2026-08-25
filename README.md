# Sentinel

Cross-platform infrastructure monitoring. Agents collect **real** system metrics
on the machines you watch and push them outbound over WebSocket; a web console
and an Android app let you view any of your own devices remotely, with anomaly
detection, forecasting, alerting and AI-generated incident explanations.

See `docs/ARCHITECTURE.md` for how it fits together, `docs/INSTALL.md` for
installing an agent, and `CLAUDE.md` for the development history and invariants.

## Running it

First time only:

```bash
make install
```

Then, in two terminals:

```bash
make up
```

```bash
make serve
```

`make up` starts TimescaleDB and Redis in Docker. `make serve` builds the web
console and serves it **and** the API from one process, bound to every network
interface. It prints the address to use:

```
  Console + API:  http://192.168.0.2:8000
  On this Mac:    http://localhost:8000
```

**That first URL is the single link.** It is the console, the REST API and the
live-monitoring WebSocket on one origin, so it works unchanged in a browser on
this machine *and* on any phone or laptop on the same network — and it is the
same string you point an agent or the Android app at. There is no CORS to
configure because there is only one origin.

Running from VS Code changes nothing: open a terminal (`` Ctrl+` ``) and run the
two commands above. Stop the server with `Ctrl+C`.

> The address comes from your current network, so it changes if you move
> between WiFi networks. Re-run `make serve` to see the new one.

### Why `localhost:5173` does not work from another device

`make dev-frontend` (the Vite dev server, port 5173) and `make dev-backend`
(port 8000, reload enabled) are for developing Sentinel itself. Both bind to
**loopback only**, so nothing else on the network can reach them — and
`localhost` typed on another device means *that* device, never this one. They
give you hot reload; they cannot give you a shareable link. `make serve` is the
only command that produces something reachable from another machine.

Both can run at once if you want hot reload while something else uses the LAN
link, but they need different ports — `make serve` and `make dev-backend` both
want 8000.

### Getting past the network

Everything above is reachable from this machine's LAN and nowhere else. Reaching
it from cellular or another network means deploying the backend somewhere public
with a domain and TLS; at that point the `https://` URL needs no cleartext
exception on Android and the rest works unchanged.

## Everything else

`make test` runs every suite (backend, agent, web, mobile JS, Kotlin);
`make lint` and `make typecheck` do what they say. `make migrate` applies
database migrations. The full command list, including agent enrollment,
packaging and the Android build, is in the `Makefile` — every target has a
comment saying what it is for.
