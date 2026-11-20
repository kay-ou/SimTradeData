"""Retry decorator with exponential backoff, jitter, and error classification."""

import functools
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Exception types that should trigger a retry.
RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
)

# Exception types that should never be retried.
NON_RETRYABLE_EXCEPTIONS = (
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
)

# Keywords in error messages that indicate a retryable condition.
RETRYABLE_KEYWORDS = (
    "timeout",
    "timed out",
    "connection",
    "reset",
    "refused",
    "temporary",
    "service unavailable",
)


def is_retryable(error: BaseException) -> bool:
    """Determine whether an error should trigger a retry.

    Checks exception type hierarchy first, then falls back to
    keyword matching in the error message for unclassified exceptions.

    Args:
        error: The exception to classify.

    Returns:
        True if the error is retryable, False otherwise.
    """
    # Check non-retryable types first (more specific wins).
    if isinstance(error, NON_RETRYABLE_EXCEPTIONS):
        return False

    # Check retryable types via isinstance (covers subclasses).
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True

    # For other exception types, check message keywords.
    message = str(error).lower()
    return any(keyword in message for keyword in RETRYABLE_KEYWORDS)


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on delay between retries in seconds.
        backoff_factor: Multiplier applied to delay after each retry.
        jitter: Fraction of delay to randomize (0.0 = no jitter, 1.0 = full).
    """

    max_retries: int = 3
    base_delay: float = 2.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter: float = 0.3


# Module-level default configuration instance.
DEFAULT_RETRY_CONFIG = RetryConfig()


def _calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate the delay before the next retry attempt.

    Uses exponential backoff with optional jitter. The delay is computed as:
        delay = base_delay * (backoff_factor ** attempt)
    then capped at max_delay, and jitter is applied as a random offset
    within [-jitter * delay, +jitter * delay].

    Args:
        attempt: Zero-based attempt index (0 = first retry).
        config: Retry configuration parameters.

    Returns:
        Delay in seconds (always >= 0).
    """
    delay = config.base_delay * (config.backoff_factor ** attempt)
    delay = min(delay, config.max_delay)

    if config.jitter > 0:
        jitter_range = delay * config.jitter
        delay += random.uniform(-jitter_range, jitter_range)

    return max(0.0, delay)


def retry(config: Optional[RetryConfig] = None) -> Callable:
    """Decorator factory that retries a function on retryable errors.

    Non-retryable errors are raised immediately without any retry attempt.
    Retryable errors trigger up to config.max_retries total attempts
    with exponential backoff between each.

    Args:
        config: Retry configuration. Uses DEFAULT_RETRY_CONFIG if None.

    Returns:
        A decorator that wraps the target function with retry logic.
    """
    if config is None:
        config = DEFAULT_RETRY_CONFIG

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(config.max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e

                    if not is_retryable(e):
                        raise

                    remaining = config.max_retries - attempt - 1
                    if remaining <= 0:
                        break

                    delay = _calculate_delay(attempt, config)
                    logger.warning(
                        "Retry %d/%d for %s after %s: %.2fs delay, "
                        "%d attempts remaining",
                        attempt + 1,
                        config.max_retries,
                        func.__name__,
                        type(e).__name__,
                        delay,
                        remaining,
                    )
                    time.sleep(delay)

            raise last_error

        return wrapper

    return decorator
