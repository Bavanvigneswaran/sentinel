"""The live pipeline: Redis fanout from ingest to viewer sockets.

Durability and liveness are separate concerns. `write_samples` (app/ingest/writer.py)
is the durable path and always runs first; everything in this package is a
best-effort convenience layered on top of it. A Redis outage must never turn
into a rejected agent batch or a failed ack.
"""
