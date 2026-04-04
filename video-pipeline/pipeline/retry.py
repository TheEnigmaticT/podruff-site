import functools
import logging
import time

logger = logging.getLogger(__name__)


def retry(max_attempts=3, base_delay=2.0, exceptions=(Exception,)):
    """Retry decorator with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error("%s failed after %d attempts: %s", func.__name__, max_attempts, e)
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning("%s attempt %d/%d failed: %s. Retrying in %.1fs", func.__name__, attempt, max_attempts, e, delay)
                    time.sleep(delay)
        return wrapper
    return decorator
