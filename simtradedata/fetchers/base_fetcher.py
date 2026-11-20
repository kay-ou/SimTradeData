"""
Base fetcher class for all data fetchers

This module provides the base class with common login/logout/context manager
functionality to eliminate code duplication across fetchers. Integrates
resilience infrastructure (cooldown, circuit breaker, monitor) so that
subclasses get automatic request tracking and protection.
"""

import logging
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Generator

from simtradedata.resilience.circuit_breaker import CircuitBreaker
from simtradedata.resilience.cooldown import cooldown_manager
from simtradedata.resilience.monitor import get_monitor

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """
    Base class for all data fetchers

    Provides common functionality:
    - Login/logout state tracking
    - Context manager support (with statement)
    - Destructor cleanup
    - Error handling
    - Resilience: cooldown, circuit breaker, request monitoring

    Subclasses only need to implement _do_login() and _do_logout()
    """

    source_name: str = "unknown"

    def __init__(self):
        self._logged_in = False
        self._cooldown = cooldown_manager
        self._monitor = get_monitor()
        self._circuit_breaker = CircuitBreaker(self.source_name)

    @abstractmethod
    def _do_login(self):
        """
        Subclass implements specific login logic

        Should raise ConnectionError if login fails
        """
        pass

    @abstractmethod
    def _do_logout(self):
        """
        Subclass implements specific logout logic

        Should handle cleanup of connections/resources
        """
        pass

    def login(self):
        """
        Login with state tracking

        Calls _do_login() if not already logged in
        """
        if not self._logged_in:
            self._do_login()
            self._logged_in = True
            logger.info(f"{self.__class__.__name__} login successful")

    def logout(self):
        """
        Logout with error handling

        Safely calls _do_logout() and handles any errors
        """
        if self._logged_in:
            try:
                self._do_logout()
            except Exception as e:
                logger.warning(f"{self.__class__.__name__} logout failed: {e}")
            finally:
                self._logged_in = False
                logger.info(f"{self.__class__.__name__} logout complete")

    def _make_request(self, func, *args, **kwargs):
        """Execute a data source request with resilience protection.

        Checks cooldown and circuit breaker state before calling func.
        Records success/failure metrics in monitor, circuit breaker,
        and cooldown manager.

        Args:
            func: Callable to execute.
            *args: Positional arguments forwarded to func.
            **kwargs: Keyword arguments forwarded to func.

        Returns:
            The return value of func, or None if the request was skipped
            due to cooldown or open circuit breaker.

        Raises:
            Exception: Re-raises any exception from func after recording
                the failure.
        """
        source = self.source_name

        if self._cooldown.is_in_cooldown(source):
            logger.debug(
                "Skipping request for '%s': source is in cooldown", source,
            )
            return None

        if not self._circuit_breaker.is_available():
            logger.debug(
                "Skipping request for '%s': circuit breaker is open", source,
            )
            return None

        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
            self._record_source_result(
                success=True, elapsed=time.monotonic() - start,
            )
            return result
        except Exception as e:
            self._record_source_result(
                success=False, elapsed=time.monotonic() - start, error=e,
            )
            raise

    def _ensure_source_available(self) -> bool:
        """Check cooldown and circuit breaker before a multi-retry operation.

        Unlike _make_request which checks on every attempt, this is meant
        as a single pre-check for methods that have their own internal
        retry loop. Returns False if the source should not be contacted.

        Returns:
            True if the source is available for requests.
        """
        if self._cooldown.is_in_cooldown(self.source_name):
            logger.warning(
                "Source '%s' is in cooldown, skipping request",
                self.source_name,
            )
            return False
        if not self._circuit_breaker.is_available():
            logger.warning(
                "Circuit breaker open for '%s', skipping request",
                self.source_name,
            )
            return False
        return True

    def _record_source_result(
        self,
        success: bool,
        elapsed: float,
        error: Exception | None = None,
    ) -> None:
        """Record a multi-retry operation result in all resilience systems.

        Call this once after an operation with its own internal retry
        logic completes (success or exhausted). Unlike _make_request which
        records per-attempt, this records the final outcome once.

        Args:
            success: Whether the overall operation succeeded.
            elapsed: Total wall-clock time in seconds.
            error: The final error if success is False.
        """
        if success:
            self._monitor.record_request(
                self.source_name, success=True, response_time=elapsed,
            )
            self._circuit_breaker.record_success()
            self._cooldown.record_success(self.source_name)
        else:
            error_type = self._classify_error(error) if error is not None else "default"
            self._monitor.record_request(
                self.source_name, success=False, response_time=elapsed,
                error=str(error) if error is not None else "",
            )
            self._circuit_breaker.record_failure()
            self._cooldown.record_failure(self.source_name, error_type)

    @contextmanager
    def _resilient_operation(self) -> Generator[bool, None, None]:
        """Context manager wrapping multi-retry operations with resilience.

        Handles pre-check (cooldown + circuit breaker) and post-recording
        (monitor, circuit breaker, cooldown) automatically. Use in methods
        that have their own internal retry loops.

        Yields:
            True if the operation should proceed, False if the source is
            blocked (caller should return early with an empty result).
        """
        if not self._ensure_source_available():
            yield False
            return
        start = time.monotonic()
        try:
            yield True
            self._record_source_result(
                success=True, elapsed=time.monotonic() - start,
            )
        except Exception as e:
            self._record_source_result(
                success=False, elapsed=time.monotonic() - start, error=e,
            )
            raise

    @staticmethod
    def _classify_error(error) -> str:
        """Classify an exception into an error category for cooldown.

        Args:
            error: The exception to classify.

        Returns:
            One of "rate_limit", "forbidden", "timeout",
            "connection_error", or "default".
        """
        msg = str(error).lower()

        if "429" in msg or "rate limit" in msg:
            return "rate_limit"
        if "403" in msg or "forbidden" in msg:
            return "forbidden"
        if isinstance(error, TimeoutError) or "timeout" in msg:
            return "timeout"
        if isinstance(error, ConnectionError) or "connection" in msg:
            return "connection_error"
        return "default"

    def __enter__(self):
        """Context manager entry - login"""
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - logout"""
        self.logout()
        return False  # Don't suppress exceptions

    def __del__(self):
        """Destructor - ensure cleanup on object deletion"""
        try:
            self.logout()
        except Exception:
            # Ignore all errors in destructor
            pass
