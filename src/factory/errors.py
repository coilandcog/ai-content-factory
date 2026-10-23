"""Errors the factory raises on purpose (CONVENTIONS.md §4)."""

from __future__ import annotations


class FactoryError(Exception):
    """Base class, so callers can catch every factory error in one place."""


class ConfigError(FactoryError):
    """Bad YAML or settings. Stop immediately: nothing can run on a broken config."""


class StatusTransitionError(FactoryError):
    """A status change that DATABASE.md §4 doesn't allow; it always points to a bug."""


class IntegrationError(FactoryError):
    """An external API refused the request (a 4xx that retrying won't fix)."""


class RetryableError(IntegrationError):
    """Network error, timeout, HTTP 429 or 5xx: worth retrying (3 tries, 2 s/4 s/8 s)."""


class BudgetExceededError(FactoryError):
    """A call would go over today's budget; the stage stops before making it."""


class PolicyBlockedError(FactoryError):
    """Blocklist hit, similarity too high or missing disclosure: the item is rejected."""


class QualityCheckError(FactoryError):
    """Automated QA failed; the item stays 'generated' with the reason."""
