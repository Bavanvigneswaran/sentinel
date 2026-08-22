"""Static host metadata, reported once at connect."""

from __future__ import annotations

import logging
import platform
import socket

import psutil

from sentinel_agent import __version__ as AGENT_VERSION

logger = logging.getLogger(__name__)


def collect_host_info() -> dict:
    vmem = None
    try:
        vmem = psutil.virtual_memory()
    except Exception:
        logger.debug("could not read total memory", exc_info=True)

    return {
        "hostname": socket.gethostname()[:255],
        "os": platform.system() or "unknown",
        "os_version": (platform.release() or None),
        "kernel_version": (platform.version() or None),
        "arch": (platform.machine() or None),
        "cpu_cores": psutil.cpu_count(logical=True),
        "total_memory_bytes": getattr(vmem, "total", None),
        "agent_version": AGENT_VERSION,
        "platform": "desktop",
    }
