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
from app.models.consequences import QUEST_STATES, REPUTATION_SCOPES
from app.models.enums import EventType, Visibility
from app.models.event import Event
from app.models.npc import NPC
from app.schemas.llm_io import ProposedDelta
from app.services.events import EventService

# Persistent-world consequences the planner may PROPOSE. These delegate to the shared
# ConsequenceService (no parallel engine) and, like advance_time, are all information/
# state consequences — never authoritative mechanics. Injury, currency, items, combat,
# crime attribution, access-state, and scheduling stay engine-owned off this path.
_CONSEQUENCE_KINDS = frozenset({"spread_rumor", "update_quest", "change_reputation"})

ALLOWED_DELTA_KINDS = frozenset(
    {"advance_time", "raise_suspicion", "note", "reveal_secret", "reveal_fragment"}
    | _CONSEQUENCE_KINDS
)


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
        # Authored clue fragments the current scene permits (reveal_fragment gate).
        self.allowed_clues: list[str] = []
        # Party-visible fragments committed this action (pipeline shows them).
        self.revealed_fragments: list[str] = []
        # Authored objective keys this campaign has (update_quest gate). The model may
        # advance an objective the campaign declared; it may not invent one.
        self.allowed_quest_keys: list[str] = []
        # Chapter movement caused by this action's objective updates, for the pipeline.
        self.chapter_advances: list = []

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
        if delta.kind == "reveal_fragment":
            fragment = (delta.payload.get("text") or "").strip()
            if not fragment:
                raise ValidationError("reveal_fragment requires payload.text")
            allowed = self.allowed_clues or []
            if not any(fragment in clue or clue in fragment for clue in allowed):
                raise ValidationError(
                    "reveal_fragment must match an AUTHORED clue for this scene — "
                    "the model may time a reveal, never invent one")
        if delta.kind == "spread_rumor":
            if not (delta.payload.get("content") or "").strip():
                raise ValidationError("spread_rumor requires payload.content")
        if delta.kind == "update_quest":
            key = (delta.payload.get("key") or "").strip()
            if not key:
                raise ValidationError("update_quest requires payload.key")
            state = delta.payload.get("state")
            if state is not None and state not in QUEST_STATES:
                raise ValidationError(
                    f"update_quest state must be one of {list(QUEST_STATES)}")
            # Same discipline as reveal_fragment: the model may TIME an objective's
            # advance, never author one. Before the objective layer existed this was an
            # open upsert, so a hallucinated key silently created a quest nothing had
            # authored — state regenerated by the narrator, which is the thing the
            # engine is supposed to own.
            if key not in self.allowed_quest_keys:
                raise ValidationError(
                    f"update_quest key {key!r} is not an AUTHORED objective for this "
                    f"campaign (known: {sorted(self.allowed_quest_keys)}) — the model "
                    f"may advance an objective, never invent one")
        if delta.kind == "change_reputation":
            kind, _ = parse_entity_ref(delta.target or "")
            if kind not in ("character", "npc"):
                raise ValidationError(
                    "change_reputation must target a character:<id> or npc:<id>")
            if delta.payload.get("scope") not in REPUTATION_SCOPES:
                raise ValidationError(
                    f"change_reputation scope must be one of {list(REPUTATION_SCOPES)}")
            if not isinstance(delta.payload.get("amount"), int):
                raise ValidationError("change_reputation requires integer payload.amount")

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
        if delta.kind == "reveal_fragment":
            return [await self._reveal_fragment(delta)]
        if delta.kind in _CONSEQUENCE_KINDS:
            # ConsequenceService records the canonical event itself (like the world
            # clock), so no Event is returned to the caller here.
            await self._apply_consequence(delta)
            return []
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

    async def _apply_consequence(self, delta: ProposedDelta) -> None:
        """Delegate a proposed persistent consequence to the shared ConsequenceService
        — the single validated command layer — rather than mutating state here."""
        from app.world.consequence_service import ConsequenceService

        cs = ConsequenceService(
            self.session, campaign_id=self.campaign_id, session_id=self.session_id,
            scene_id=self.scene_id, actor_entity=self.actor_entity,
        )
        if delta.kind == "spread_rumor":
            await cs.spread_rumor(
                content=delta.payload["content"].strip(),
                truth=bool(delta.payload.get("truth", True)),
                origin_location_id=delta.payload.get("origin_location_id"),
            )
        elif delta.kind == "update_quest":
            await cs.update_quest(
                key=delta.payload["key"].strip(), name=delta.payload.get("name"),
                state=delta.payload.get("state"), progress=delta.payload.get("progress"),
                data=delta.payload.get("data"),
            )
            # An objective resolving may complete its chapter and open the next one.
            # This is where the campaign actually MOVES — checked on every objective
            # update because that is the only thing that can change the answer.
            from app.services.campaigns.progression_service import ProgressionService

            advance = await ProgressionService(self.session).advance_chapter_if_resolved(
                self.campaign_id)
            if advance.moved:
                self.chapter_advances.append(advance)
        elif delta.kind == "change_reputation":
            await cs.change_reputation(
                subject_ref=delta.target, scope=delta.payload["scope"],
                amount=int(delta.payload["amount"]),
                scope_ref=delta.payload.get("scope_ref"), reason=delta.reason,
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

    async def _reveal_fragment(self, delta: ProposedDelta) -> Event:
        """A PARTY-visible partial clue (e.g. overheard '...ไม่ใช่ของมนุษย์') —
        validated against the scene's authored clues; usually the teeth of a
        failed-but-interesting check."""
        fragment = delta.payload["text"].strip()
        self.revealed_fragments.append(fragment)
        return await self.events.record(
            campaign_id=self.campaign_id, session_id=self.session_id, scene_id=self.scene_id,
            event_type=EventType.KNOWLEDGE_GAINED, actor_entity=self.actor_entity,
            visibility=Visibility.PARTY,
            payload={"fragment": fragment, "summary": f"ได้ยินมาแว่วๆ: “{fragment}”"},
            narrative_significance=30,
        )

    async def _raise_suspicion(self, delta: ProposedDelta) -> Event:
        """Raise this NPC's suspicion OF THE ACTOR.

        This used to write a campaign-wide `attitudes["suspicion_level"]` counter that
        nothing ever read: NPCDecisionService derives stance, willingness and intent
        purely from NPCRelationship + NPCMemory. So suspicion could be raised and the
        NPC would still greet the culprit like a stranger. Worse, a bare counter has
        no idea WHO it is suspicious of.

        It now accumulates into the per-character relationship that the decision
        service, the recall path and the situational-DC reader all already read — one
        store, and suspicion that is about someone.
        """
        _, npc_id = parse_entity_ref(delta.target)
        npc = await self.session.get(NPC, npc_id)
        if npc is None:
            raise ValidationError(f"unknown npc target {delta.target!r}")
        amount = int(delta.payload.get("amount", 1))
        before_state = npc.emotional_state
        npc.emotional_state = "ระแวง"

        subject = self.actor_entity if (self.actor_entity or "").startswith(
            "character:") else None
        before_level = after_level = 0
        stance = None
        if subject:
            from app.npcs.memory_service import NPCMemoryService, _derive_stance

            rel = await NPCMemoryService(self.session)._relationship(npc_id, subject)
            before_level = int(rel.suspicion or 0)
            after_level = max(-100, min(100, before_level + amount))
            rel.suspicion = after_level
            rel.current_stance = stance = _derive_stance(rel)
            rel.attitude = stance
        else:
            # No identifiable actor: keep the legacy scene-level counter so the DM
            # signal is not lost, but it drives no per-character behaviour.
            attitudes = dict(npc.attitudes or {})
            before_level = int(attitudes.get("suspicion_level", 0))
            after_level = before_level + amount
            attitudes["suspicion_level"] = after_level
            npc.attitudes = attitudes

        # NPC suspicion is DM-scoped: players do not automatically learn of it.
        return await self.events.record(
            campaign_id=self.campaign_id, session_id=self.session_id, scene_id=self.scene_id,
            event_type=EventType.NPC_STATE_CHANGED, actor_entity=self.actor_entity,
            target_entities=[delta.target], visibility=Visibility.DM_ONLY,
            mechanical_changes={"suspicion": {"from": before_level, "to": after_level}},
            payload={"emotional_state": {"from": before_state, "to": "ระแวง"},
                     "reason": delta.reason, "subject": subject, "stance": stance},
            narrative_significance=30,
        )
