"""Window policies — the configured edge-case behaviour, in one place.

Read from `campaign.config["planning"]`. Defaults encode the product decisions: shared
planning turns on automatically with 2+ eligible players, a solo player auto-readies so
single-player never slows down, and there is no countdown unless a host opts in. Every
special case the spec lists (disconnect, AFK, pass, trivial invalidation…) is a policy
value here rather than an `if` buried in the resolver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WindowPolicies:
    # "auto" opens a shared window only when 2+ players are eligible; "always" opens one
    # even for a solo player; "off" keeps the legacy one-by-one flow entirely.
    enabled: str = "auto"
    # Solo player: submitting is enough, the round resolves without a Ready click.
    single_player_auto_ready: bool = True
    # Even a solo player must press Ready (overrides auto-ready) when a host wants it.
    manual_ready_solo: bool = False
    # 0 = wait for everyone indefinitely; >0 = optional countdown before auto-freeze.
    countdown_seconds: int = 0
    # When an earlier action invalidates a later one and no fallback was declared:
    # "fallback_or_skip" applies a safe default (skip); "pause" asks the player.
    trivial_invalidation: str = "fallback_or_skip"
    # A repeatedly-invalid submission is returned to that player this many times before
    # it is auto-passed (so one player cannot stall the whole table forever).
    max_validation_retries: int = 3

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "WindowPolicies":
        planning = ((config or {}).get("planning") or {})
        base = cls()
        return cls(
            enabled=str(planning.get("enabled", base.enabled)),
            single_player_auto_ready=bool(
                planning.get("single_player_auto_ready", base.single_player_auto_ready)),
            manual_ready_solo=bool(planning.get("manual_ready_solo", base.manual_ready_solo)),
            countdown_seconds=int(planning.get("countdown_seconds", base.countdown_seconds)),
            trivial_invalidation=str(
                planning.get("trivial_invalidation", base.trivial_invalidation)),
            max_validation_retries=int(
                planning.get("max_validation_retries", base.max_validation_retries)),
        )

    def should_open_window(self, eligible_count: int) -> bool:
        """Whether a shared decision window governs this scene at all.

        A shared planning window is for coordinating 2+ players. A solo table keeps the
        clean cinematic opening + the direct one-by-one pipeline (no planning panel),
        UNLESS a host explicitly opts in with `enabled: "always"`."""
        if self.enabled == "off":
            return False
        if self.enabled == "always":
            return eligible_count >= 1
        return eligible_count >= 2   # "auto"

    def solo_auto_ready(self, required_count: int) -> bool:
        return (
            required_count <= 1
            and self.single_player_auto_ready
            and not self.manual_ready_solo
        )

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "single_player_auto_ready": self.single_player_auto_ready,
            "manual_ready_solo": self.manual_ready_solo,
            "countdown_seconds": self.countdown_seconds,
            "trivial_invalidation": self.trivial_invalidation,
            "max_validation_retries": self.max_validation_retries,
        }
