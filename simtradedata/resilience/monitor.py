"""Request monitor with stats tracking and health probing for data sources."""

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class SourceStats:
    """Aggregated request statistics for a single data source.

    Attributes:
        total: Total number of recorded requests.
        successful: Number of successful requests.
        failed: Number of failed requests.
        total_response_time: Cumulative response time across all requests.
        min_response_time: Shortest observed response time.
        max_response_time: Longest observed response time.
    """

    total: int = 0
    successful: int = 0
    failed: int = 0
    total_response_time: float = 0.0
    min_response_time: float = float("inf")
    max_response_time: float = 0.0


class RequestMonitor:
    """Thread-safe request monitor that tracks per-source statistics and
    provides health probing capabilities.

    Maintains independent SourceStats for each data source and supports
    registering probe functions that can be executed on demand or
    automatically on a background timer.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stats: dict[str, SourceStats] = {}
        self._probes: dict[str, Callable[[], bool]] = {}
        self._auto_probe_event: Optional[threading.Event] = None
        self._auto_probe_thread: Optional[threading.Thread] = None

    def _get_stats(self, source: str) -> SourceStats:
        """Return the stats object for a source, creating one if needed.

        Must be called while holding self._lock.
        """
        if source not in self._stats:
            self._stats[source] = SourceStats()
        return self._stats[source]

    def record_request(
        self,
        source: str,
        success: bool,
        response_time: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        """Record a request result for the given source.

        Args:
            source: Identifier of the data source.
            success: Whether the request succeeded.
            response_time: Time taken by the request in seconds.
            error: Optional error description when success is False.
        """
        with self._lock:
            stats = self._get_stats(source)
            stats.total += 1
            stats.total_response_time += response_time

            stats.min_response_time = min(stats.min_response_time, response_time)
            stats.max_response_time = max(stats.max_response_time, response_time)

            if success:
                stats.successful += 1
            else:
                stats.failed += 1
                if error:
                    logger.warning(
                        "Request to '%s' failed: %s", source, error,
                    )

    def get_stats(self, source: str) -> dict:
        """Return a snapshot of statistics for a single source.

        Args:
            source: Identifier of the data source.

        Returns:
            Dictionary with keys: total, successful, failed, success_rate,
            avg_response_time, min_response_time, max_response_time.
        """
        with self._lock:
            stats = self._get_stats(source)
            total = stats.total
            return {
                "total": total,
                "successful": stats.successful,
                "failed": stats.failed,
                "success_rate": stats.successful / total if total > 0 else 0.0,
                "avg_response_time": (
                    stats.total_response_time / total if total > 0 else 0.0
                ),
                # No recorded requests: hide the initial infinite minimum.
                "min_response_time": stats.min_response_time if total > 0 else 0.0,
                "max_response_time": stats.max_response_time,
            }

    def get_all_stats(self) -> dict[str, dict]:
        """Return statistics snapshots for all tracked sources.

        Returns:
            Dictionary mapping source name to its stats dictionary.
        """
        with self._lock:
            return {
                source: self.get_stats(source) for source in self._stats
            }

    def register_probe(
        self, source: str, probe_func: Callable[[], bool],
    ) -> None:
        """Register a health-check probe for a data source.

        Args:
            source: Identifier of the data source.
            probe_func: Callable that returns True if the source is healthy.
        """
        with self._lock:
            self._probes[source] = probe_func

    def probe(self, source: str) -> bool:
        """Execute the registered probe for a source.

        Args:
            source: Identifier of the data source.

        Returns:
            True if the probe succeeds, False if it fails or raises
            an exception.
        """
        with self._lock:
            probe_func = self._probes.get(source)

        if probe_func is None:
            logger.warning("No probe registered for source '%s'", source)
            return False

        try:
            return probe_func()
        except Exception:
            logger.exception("Probe for source '%s' raised an exception", source)
            return False

    def probe_all(self) -> dict[str, bool]:
        """Execute all registered probes.

        Returns:
            Dictionary mapping source name to probe result (True/False).
        """
        with self._lock:
            sources = list(self._probes.keys())

        return {source: self.probe(source) for source in sources}

    def start_auto_probe(self, interval: float = 300.0) -> None:
        """Start a daemon background thread that probes all sources
        at regular intervals.

        Args:
            interval: Seconds between probe cycles (default 300).
        """
        with self._lock:
            if self._auto_probe_thread is not None:
                logger.warning("Auto-probe is already running")
                return

            self._auto_probe_event = threading.Event()

            def _run() -> None:
                while not self._auto_probe_event.is_set():
                    results = self.probe_all()
                    for source, healthy in results.items():
                        if not healthy:
                            logger.warning(
                                "Auto-probe: source '%s' is unhealthy",
                                source,
                            )
                    self._auto_probe_event.wait(timeout=interval)

            self._auto_probe_thread = threading.Thread(
                target=_run, daemon=True,
            )
            self._auto_probe_thread.start()
            logger.info(
                "Auto-probe started with %.1fs interval", interval,
            )

    def stop_auto_probe(self) -> None:
        """Stop the background auto-probe thread."""
        with self._lock:
            if self._auto_probe_event is None:
                return
            self._auto_probe_event.set()
            thread = self._auto_probe_thread
            self._auto_probe_thread = None
            self._auto_probe_event = None

        if thread is not None:
            thread.join(timeout=5.0)
            logger.info("Auto-probe stopped")

    def print_summary(self) -> None:
        """Print a formatted summary table of all source statistics to
        stdout."""
        all_stats = self.get_all_stats()
        print("============= Data Source Statistics =============")
        print(
            f"{'Source':<16}"
            f"{'Requests':>8}  "
            f"{'Success':>7}  "
            f"{'Failed':>6}  "
            f"{'Rate':>5}    "
            f"{'Avg Time':>8}"
        )
        print("-" * 49)
        for source, stats in sorted(all_stats.items()):
            rate_str = f"{stats['success_rate'] * 100:.1f}%"
            avg_str = f"{stats['avg_response_time']:.2f}s"
            print(
                f"{source:<16}"
                f"{stats['total']:>8}  "
                f"{stats['successful']:>7}  "
                f"{stats['failed']:>6}  "
                f"{rate_str:>5}    "
                f"{avg_str:>8}"
            )
        print("=" * 49)


# Global singleton for convenient access across the application.
_monitor = RequestMonitor()


def get_monitor() -> RequestMonitor:
    """Return the global RequestMonitor singleton."""
    return _monitor
