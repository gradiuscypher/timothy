"""The closed sets the schema and the enforcement engine share.

These are stored as their *values*, not their Python names, so the database reads the
way PLAN.md's schema section writes it.
"""

from __future__ import annotations

from enum import StrEnum


class SubscriptionLevel(StrEnum):
    """How hard a guild has asked Timothy to enforce a pool."""

    BAN = "ban"
    """Listings are enforced as Discord bans."""

    WARN = "warn"
    """Listings are reported to the notification channel and never banned."""


class OutcomeStatus(StrEnum):
    """The result of enforcing one listing in one guild.

    A ``BANNED`` row is what makes a ban attributable to Timothy, and so what makes
    reverting it safe (ADR 0005). A ``WARNED`` row is simultaneously the audit trail and
    the dedupe key that keeps warnings to one per user, per pool, per guild.
    """

    BANNED = "banned"
    WARNED = "warned"
    FAILED = "failed"
    SKIPPED_EXCEPTION = "skipped_exception"


class JobStatus(StrEnum):
    """Where an enforcement job is in its life."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
