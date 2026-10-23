"""Errors the factory raises on purpose (CONVENTIONS.md §4)."""

from __future__ import annotations


class FactoryError(Exception):
    """Base class, so callers can catch every factory error in one place."""


class ConfigError(FactoryError):
    """Bad YAML or settings. Stop immediately: nothing can run on a broken config."""
