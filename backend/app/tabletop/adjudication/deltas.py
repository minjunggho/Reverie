"""DeltaApplier — validate AI-proposed consequence deltas, then commit them through
domain services with canonical events.

The ConsequencePlanner may only PROPOSE deltas from a small allowlist. Anything off
the list, or targeting the wrong entity kind, is rejected (RulesViolation /
ValidationError). Crucially, damage/HP/resource changes are NOT proposable here —
those only ever come from the deterministic dice/rules path — so narration and
consequence planning can never invent authoritative numbers.
"""
from __future__ import annotations

from app.core.errors import RulesViolation, ValidationError
from app.core.ids import parse_entity_ref
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.models.npc import NPC
from app.schemas.llm_io import ProposedDelta
from app.services.events import EventService

ALLOWED_DELTA_KINDS = frozenset({"advance_time", "raise_suspicion", "note", "reveal_secret"})


class DeltaApplier:
    def __init__(
        self,
        session,
        *,
        campaign_id: str,
        session_id: str | None,
        scene_id: str | None,
        actor_entity: str | None,
    ) -> None:
        self.session = session
        self.events = EventService(session)
        self.campaign_id = campaign_id
        self.session_id = session_id
        self.scene_id = scene_id
        self.actor_entity = actor_entity
        # Private reveals committed this action: [{"character_id", "fact"}].
        # The pipeline turns these into PRIVATE_SECRET direct messages.
        self.private_reveals: list[dict] = []

    def validate(self, delta: ProposedDelta) -> None:
        if delta.kind not in ALLOWED_DELTA_KINDS:
            raise RulesViolation(
                f"proposed delta kind {delta.kind!r} is not permitted "
                f"(allowed: {sorted(ALLOWED_DELTA_KINDS)})"
            )
        if delta.kind == "raise_suspicion":
            kind, _ = parse_entity_ref(delta.target or "")
            if kind != "npc":
                raise ValidationError("raise_suspicion must target an npc:<id>")
        if delta.kind == "advance_time":
            minutes = delta.payload.get("minutes")
            if not isinstance(minutes, int) or minutes <= 0:
                raise ValidationError("advance_time requires payload.minutes > 0")
        if delta.kind == "reveal_secret":
            kind, _ = parse_entity_ref(delta.target or "")
            if kind != "character":
                raise ValidationError("reveal_secret must target a character:<id>")
            if not delta.payload.get("secret_id"):
                raise ValidationError("reveal_secret requires payload.secret_id "
                                      "(only PRE-AUTHORED secrets can be revealed)")

    async def apply(self, delta: ProposedDelta) -> list[Event]:
        self.validate(delta)
        if delta.kind == "note":
            return []
        if delta.kind == "advance_time":
            await self._advance_time(int(delta.payload["minutes"]))
            return []  # the world clock records the canonical event(s)
        if delta.kind == "raise_suspicion":
            return [await self._raise_suspicion(delta)]
        if delta.kind == "reveal_secret":
            return [await self._reveal_secret(delta)]
        return []  # unreachable (validate() guards)

    async def apply_all(self, deltas: list[ProposedDelta]) -> list[Event]:
        out: list[Event] = []
        for d in deltas:
            out.extend(await self.apply(d))
        return out

    async def apply_valid(
        self, deltas: list[ProposedDelta]
    ) -> tuple[list[Event], list[tuple[ProposedDelta, str]]]:
        """Apply only the deltas that pass validation; return (applied, rejected).

        An illegal or out-of-authority delta is DROPPED (not committed) with its
        reason, rather than aborting the whole action — a hallucinated delta must
        never mutate state, but it also must not block the player's turn.
        """
        applied: list[Event] = []
        rejected: list[tuple[ProposedDelta, str]] = []
        for d in deltas:
            try:
                self.validate(d)
            except (RulesViolation, ValidationError) as exc:
                rejected.append((d, str(exc)))
                continue
            applied.extend(await self.apply(d))
        return applied, rejected

    # --- concrete effects ----------------------------------------------------
    async def _advance_time(self, minutes: int) -> None:
        # Route through the world clock so DUE threats/events tick on the same path.
        # The clock service records the canonical WORLD_TIME_ADVANCED (+ any THREAT_ADVANCED).
        from app.world.world_clock import WorldClockService

        await WorldClockService(self.session).advance_time(
            campaign_id=self.campaign_id, minutes=minutes,
            session_id=self.session_id, scene_id=self.scene_id, actor_entity=self.actor_entity,
        )

    async def _reveal_secret(self, delta: ProposedDelta) -> Event:
        """Reveal a PRE-AUTHORED Secret to one character, privately.

        The LLM can only point at an existing Secret row (by id); it can never
        invent secret content. Delivery is engine-enforced: a PLAYER_ONLY event +
        a private message queued for the pipeline."""
        from app.models.knowledge import Secret

        _, character_id = parse_entity_ref(delta.target)
        secret = await self.session.get(Secret, delta.payload["secret_id"])
        if secret is None or secret.campaign_id != self.campaign_id:
            raise ValidationError("unknown secret_id — only pre-authored secrets exist")
        if secret.revealed:
            raise ValidationError("secret already revealed")
        secret.revealed = True
        vis_map = dict(secret.visibility_map or {})
        vis_map.setdefault("characters", []).append(character_id)
        secret.visibility_map = vis_map

        self.private_reveals.append({"character_id": character_id, "fact": secret.fact})
        return await self.events.record(
            campaign_id=self.campaign_id, session_id=self.session_id, scene_id=self.scene_id,
            event_type=EventType.KNOWLEDGE_GAINED, actor_entity=self.actor_entity,
            target_entities=[delta.target], visibility=Visibility.PLAYER_ONLY,
            witnesses=[delta.target],
            payload={"secret_id": secret.id, "summary": "ได้รู้บางอย่างที่คนอื่นยังไม่รู้"},
            narrative_significance=40,
        )

    async def _raise_suspicion(self, delta: ProposedDelta) -> Event:
        _, npc_id = parse_entity_ref(delta.target)
        npc = await self.session.get(NPC, npc_id)
        if npc is None:
            raise ValidationError(f"unknown npc target {delta.target!r}")
        amount = int(delta.payload.get("amount", 1))
        attitudes = dict(npc.attitudes or {})
        before_level = int(attitudes.get("suspicion_level", 0))
        after_level = before_level + amount
        attitudes["suspicion_level"] = after_level
        npc.attitudes = attitudes
        before_state = npc.emotional_state
        npc.emotional_state = "ระแวง"
        # NPC suspicion is DM-scoped: players do not automatically learn of it.
        return await self.events.record(
            campaign_id=self.campaign_id, session_id=self.session_id, scene_id=self.scene_id,
            event_type=EventType.NPC_STATE_CHANGED, actor_entity=self.actor_entity,
            target_entities=[delta.target], visibility=Visibility.DM_ONLY,
            mechanical_changes={"suspicion": {"from": before_level, "to": after_level}},
            payload={"emotional_state": {"from": before_state, "to": "ระแวง"},
                     "reason": delta.reason},
            narrative_significance=30,
        )
