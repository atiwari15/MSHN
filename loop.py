"""Shared scaffolding for the two always-on loops.

Both loops have the same shape - do one pass, sleep, repeat, and shut down
cleanly on a signal - so that machinery lives here and each loop file only
supplies the body of a single pass. Keeping the pass a plain function also
means every loop can still be run exactly once (`--once`), which is how
they are tested and how a cron-style host can drive them instead.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time


def configure_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # The HTTP clients log every request at INFO, which drowns out the
    # loop's own output in a long-running process.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger(name)


class ShutdownSignal:
    """Set when SIGINT/SIGTERM arrives, so a pass finishes rather than
    being killed mid-write."""

    def __init__(self) -> None:
        self._event = threading.Event()
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum, _frame) -> None:
        self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep, but wake immediately on shutdown. Returns True if we should stop."""
        return self._event.wait(timeout=seconds)


def add_loop_args(parser: argparse.ArgumentParser, default_interval: int) -> None:
    parser.add_argument(
        "--interval",
        type=int,
        default=default_interval,
        help=f"seconds between passes (default {default_interval})",
    )
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")


def run_loop(log: logging.Logger, pass_fn, interval: int, once: bool = False) -> None:
    """Call pass_fn repeatedly until shutdown.

    A failing pass is logged and retried on the next tick rather than
    killing the loop - a transient EDGAR 503 or a dropped database
    connection should not end an always-on service.
    """
    if once:
        pass_fn()
        return

    shutdown = ShutdownSignal()
    log.info("starting loop (interval=%ss); Ctrl-C to stop", interval)

    while not shutdown.is_set:
        started = time.monotonic()
        try:
            pass_fn()
        except Exception:
            log.exception("pass failed; retrying next tick")

        if shutdown.is_set:
            break
        elapsed = time.monotonic() - started
        if shutdown.wait(max(0.0, interval - elapsed)):
            break

    log.info("shutdown complete")
