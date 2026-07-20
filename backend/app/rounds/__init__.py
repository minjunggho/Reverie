"""Shared decision-window round system — the new unit of resolution.

`DecisionWindowService` is the server-authoritative state machine for collecting a set
of intentions; `RoundResolver` freezes that set and turns it into one coherent world
update plus a structured `RoundPackage` for a single combined narration. Policies live
in `WindowPolicies` (from `campaign.config["planning"]`), so edge-case behaviour is
configured, not scattered.
"""
from app.rounds.policies import WindowPolicies
from app.rounds.service import DecisionWindowService
from app.rounds.resolver import RoundPackage, RoundResolver

__all__ = ["WindowPolicies", "DecisionWindowService", "RoundResolver", "RoundPackage"]
