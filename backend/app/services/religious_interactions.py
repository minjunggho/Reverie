"""Faith-aware interaction services over existing canon, memory and economy state."""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import KnowledgeStatus
from app.models.location import Location
from app.models.npc import NPC
from app.models.npc_epistemic import NPCFact, NPCMemory
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord
from app.npcs.knowledge_service import NPCKnowledgeService
from app.npcs.memory_service import NPCMemoryService
from app.rules_content.faith_interactions import get_faith_interaction_registry
from app.schemas.belief import BeliefVisibility, ReligiousKnowledgeLevel, ReligiousRole
from app.schemas.religious_interaction import (
    DoctrineContext,
    ReligiousInteractionContext,
    ReligiousKnowledgeSource,
    ReligiousOutcomeKind,
    TempleAccessDecision,
    TempleArea,
    TemplePolicy,
    TempleServiceKind,
    ValidatedReligiousOutcome,
)
from app.services.beliefs import BeliefService
from app.services.economy.wallet_service import WalletService
from app.services.faith import FaithService

_BELIEF_SUBJECT = "religious_identity:{listener_ref}"
_TEMPLE_CATEGORY = "religious_temple"
_ACCESS_CATEGORY = "religious_access_state"
_REPUTATION_CATEGORY = "religious_faction_reputation"
_RELIGIOUS_MEMORY_TYPES = {
    "RELIGIOUS_REVELATION", "RELIGIOUS_LIE", "PRAYED_TOGETHER",
    "SHRINE_DESECRATED", "TEMPLE_PROTECTED", "RELIGIOUS_PROMISE",
    "RELIGIOUS_PROMISE_BROKEN", "SACRED_OBJECT_RETURNED", "PRIEST_ATTACKED",
    "RECRUITMENT_REJECTED", "FUNERAL_RITE", "OPPOSED_UNDEAD",
}
_PRIEST_ROLES = {
    ReligiousRole.PRIEST, ReligiousRole.THEOLOGIAN, ReligiousRole.INQUISITOR,
    ReligiousRole.RELIGIOUS_OFFICIAL, ReligiousRole.SHRINE_KEEPER,
    ReligiousRole.FUNERAL_KEEPER,
}


@dataclass(frozen=True)
class ReligiousBehavior:
    memory_type: str
    summary: str
    event_tags: tuple[str, ...] = ()
    importance: int = 50
    valence: int = 0
    relationship_deltas: dict[str, int] | None = None


class ReligiousInteractionService:
    """Campaign-scoped faith interactions; never grants class or divine mechanics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.faith = FaithService(session)
        self.beliefs = BeliefService(session, self.faith)
        self.interactions = get_faith_interaction_registry()

    async def build_context(
        self, *, campaign_id: str, npc_id: str, character_id: str
    ) -> ReligiousInteractionContext:
        npc = await self._npc(campaign_id, npc_id)
        character = await self._character(campaign_id, character_id)
        listener_ref = entity_ref("character", character.id)
        player = await self.beliefs.get_character_belief(character)
        npc_profile = await self.beliefs.get_npc_belief(npc)

        public = None
        if player and player.visibility is BeliefVisibility.PUBLIC:
            public = self._safe_belief(player)

        learned = await self._learned_belief(npc.id, listener_ref)
        known_private = learned[0] if learned else None
        sources = learned[1] if learned else ()
        visible_symbols: list[str] = []
        if player and player.sacred_symbol and player.visibility is BeliefVisibility.PUBLIC:
            visible_symbols.append(player.sacred_symbol)
        if ReligiousKnowledgeSource.VISIBLE_SYMBOL in sources and player and player.sacred_symbol:
            visible_symbols.append(player.sacred_symbol)

        player_key = (known_private or public or {}).get("primary_deity_key")
        npc_key = npc_profile.primary_deity_key if npc_profile else None
        allied: list[str] = []
        rivals: list[str] = []
        enemies: list[str] = []
        if player_key and npc_key and player_key != npc_key:
            relationship = await self.faith.defined_relationship(campaign_id, npc_key, player_key)
            if relationship:
                bucket = {
                    "ALLY": allied, "RIVAL": rivals, "ENEMY_FAITH": enemies,
                }[relationship.value]
                bucket.append(player_key)

        recalled = await NPCMemoryService(self.session).recall(
            npc_id=npc.id, listener_ref=listener_ref
        )
        religious_memories = tuple(
            item.summary for item in recalled.memories
            if item.memory_type in _RELIGIOUS_MEMORY_TYPES
        )
        behavior = tuple(
            item.memory_type for item in recalled.memories
            if item.memory_type in _RELIGIOUS_MEMORY_TYPES
        )
        relationship_state = None
        if recalled.relationship:
            relationship_state = {
                "stance": recalled.relationship.current_stance,
                "trust": recalled.relationship.trust,
                "respect": recalled.relationship.respect,
                "suspicion": recalled.relationship.suspicion,
            }

        doctrine: list[DoctrineContext] = []
        for key in dict.fromkeys(key for key in (npc_key, player_key) if key):
            item = self.interactions.doctrine(key)
            if item:
                doctrine.append(DoctrineContext(
                    deity_key=key, values=item.values,
                    supported_event_tags=item.supported_event_tags,
                    opposed_event_tags=item.opposed_event_tags,
                ))

        temple = await self._temple_at(campaign_id, npc.current_location_id)
        access = await self._access_state(campaign_id, character.id, temple.id if temple else None)
        current = await self._current_religious_context(campaign_id, npc.current_location_id)
        return ReligiousInteractionContext(
            campaign_id=campaign_id, npc_id=npc.id, listener_ref=listener_ref,
            player_public_belief=public, player_known_private_belief=known_private,
            visible_symbols=tuple(dict.fromkeys(visible_symbols)), knowledge_sources=sources,
            npc_belief=self._safe_belief(npc_profile) if npc_profile else None,
            npc_religious_role=npc_profile.religious_role.value if npc_profile and npc_profile.religious_role else None,
            npc_religious_knowledge=npc_profile.knowledge_level.value if npc_profile else None,
            religious_faction_id=npc_profile.temple_or_faction_id if npc_profile else None,
            shared_deity=bool(player_key and player_key == npc_key),
            allied_deities=tuple(allied), rival_deities=tuple(rivals),
            enemy_faiths=tuple(enemies), doctrine=tuple(doctrine),
            known_religious_behavior=behavior, relationship_state=relationship_state,
            important_religious_memories=religious_memories,
            temple_access_state=access, current_religious_context=current,
        )

    async def reveal_belief(
        self, *, campaign_id: str, npc_id: str, character_id: str,
        source_event_id: str, source: ReligiousKnowledgeSource = ReligiousKnowledgeSource.PLAYER_DISCLOSURE,
    ) -> NPCFact:
        npc = await self._npc(campaign_id, npc_id)
        character = await self._character(campaign_id, character_id)
        profile = await self.beliefs.get_character_belief(character)
        if profile is None:
            raise ValidationError("character has no religious belief to reveal")
        listener_ref = entity_ref("character", character.id)
        subject = _BELIEF_SUBJECT.format(listener_ref=listener_ref)
        fact = json.dumps(self._safe_belief(profile), ensure_ascii=False, sort_keys=True)
        row = await NPCKnowledgeService(self.session).upsert_belief(
            npc_id=npc.id, subject=subject, status=KnowledgeStatus.KNOWS,
            fact=fact, confidence=1.0, source=source.value,
        )
        await NPCMemoryService(self.session).record_typed_memory(
            npc_id=npc.id, subject_ref=listener_ref, event_id=source_event_id,
            memory_type="RELIGIOUS_REVELATION",
            summary=f"{character.name} revealed a religious belief",
            importance=65, valence=0, source_ref=listener_ref,
            location_id=npc.current_location_id,
            relationship_deltas={"familiarity": 3},
        )
        return row

    async def observe_religious_identity(
        self, *, campaign_id: str, npc_id: str, character_id: str,
        source: ReligiousKnowledgeSource, source_event_id: str,
    ) -> NPCFact:
        if source not in {
            ReligiousKnowledgeSource.VISIBLE_SYMBOL,
            ReligiousKnowledgeSource.RELIGIOUS_CLOTHING,
            ReligiousKnowledgeSource.PUBLIC_REPUTATION,
            ReligiousKnowledgeSource.TEMPLE_RECORD,
            ReligiousKnowledgeSource.WITNESSED_RITUAL,
            ReligiousKnowledgeSource.SHARED_FACTION,
        }:
            raise ValidationError("observation requires an observable or authorized source")
        return await self.reveal_belief(
            campaign_id=campaign_id, npc_id=npc_id, character_id=character_id,
            source_event_id=source_event_id, source=source,
        )

    async def evaluate_special_interactions(
        self, context: ReligiousInteractionContext, *, event_tags: tuple[str, ...] = ()
    ) -> tuple[ValidatedReligiousOutcome, ...]:
        outcomes: list[ValidatedReligiousOutcome] = []
        if context.shared_deity and context.npc_religious_role:
            outcomes.append(ValidatedReligiousOutcome(
                kind=ReligiousOutcomeKind.RECOGNITION,
                reason="shared deity recognized by a religious representative",
                payload={"trust_delta": 0, "mechanical_effect": None},
            ))
            player_interpretation = (context.player_known_private_belief
                                     or context.player_public_belief or {}).get(
                                         "personal_interpretation"
                                     )
            npc_interpretation = (context.npc_belief or {}).get("personal_interpretation")
            if (player_interpretation and npc_interpretation
                    and player_interpretation.casefold() != npc_interpretation.casefold()):
                outcomes.append(ValidatedReligiousOutcome(
                    kind=ReligiousOutcomeKind.DIALOGUE_STANCE,
                    reason="same deity but different personal interpretation",
                    payload={"direction": "disagreement", "trust_delta": 0},
                ))
        if context.rival_deities or context.enemy_faiths:
            outcomes.append(ValidatedReligiousOutcome(
                kind=ReligiousOutcomeKind.WARNING,
                reason="known active-pantheon religious relationship",
                payload={"combat": False, "trust_delta": 0},
            ))
        tags = set(event_tags)
        for doctrine in context.doctrine:
            if tags.intersection(doctrine.supported_event_tags):
                outcomes.append(ValidatedReligiousOutcome(
                    kind=ReligiousOutcomeKind.DIALOGUE_STANCE,
                    reason=f"witnessed behavior is relevant to {doctrine.deity_key} doctrine",
                    payload={"direction": "approving", "deity_key": doctrine.deity_key},
                ))
            if tags.intersection(doctrine.opposed_event_tags):
                outcomes.append(ValidatedReligiousOutcome(
                    kind=ReligiousOutcomeKind.DIALOGUE_STANCE,
                    reason=f"witnessed behavior conflicts with {doctrine.deity_key} doctrine",
                    payload={"direction": "disapproving", "deity_key": doctrine.deity_key},
                ))
        return tuple(outcomes)

    async def record_religious_behavior(
        self, *, campaign_id: str, npc_id: str, character_id: str,
        source_event_id: str, behavior: ReligiousBehavior,
    ) -> NPCMemory:
        await self._npc(campaign_id, npc_id)
        character = await self._character(campaign_id, character_id)
        if behavior.memory_type not in _RELIGIOUS_MEMORY_TYPES:
            raise ValidationError(f"unsupported religious memory type {behavior.memory_type!r}")
        return await NPCMemoryService(self.session).record_typed_memory(
            npc_id=npc_id, subject_ref=entity_ref("character", character.id),
            event_id=source_event_id, memory_type=behavior.memory_type,
            summary=behavior.summary, importance=behavior.importance,
            valence=behavior.valence, source_ref=entity_ref("character", character.id),
            relationship_deltas=behavior.relationship_deltas or {},
        )

    async def register_temple(self, campaign_id: str, policy: TemplePolicy) -> CampaignCanonRecord:
        if await self.faith.get_deity(campaign_id, policy.deity_key) is None:
            raise ValidationError("temple deity must belong to an active campaign pantheon")
        location = await self.session.get(Location, policy.location_id)
        if location is None or location.campaign_id != campaign_id:
            raise ValidationError("temple location must belong to the campaign")
        if policy.faction_id:
            faction = await self.session.get(Threat, policy.faction_id)
            if faction is None or faction.campaign_id != campaign_id:
                raise ValidationError("temple faction must belong to the campaign")
        existing = (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category == _TEMPLE_CATEGORY,
            CampaignCanonRecord.fact == policy.key,
            CampaignCanonRecord.active.is_(True),
        ))).scalars().first()
        if existing:
            raise ConflictError(f"temple policy {policy.key!r} already exists")
        row = CampaignCanonRecord(
            campaign_id=campaign_id, category=_TEMPLE_CATEGORY, fact=policy.key,
            visibility="PUBLIC", provenance=policy.provenance, scope_type="location",
            scope_id=policy.location_id, data={"policy": policy.model_dump(mode="json")},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def decide_temple_access(
        self, *, campaign_id: str, temple_key: str, character_id: str, area: TempleArea
    ) -> TempleAccessDecision:
        character = await self._character(campaign_id, character_id)
        row, policy = await self._temple_by_key(campaign_id, temple_key)
        state = await self._access_state(campaign_id, character.id, row.id)
        if area is TempleArea.PUBLIC:
            return TempleAccessDecision(allowed=policy.public_access, area=area, reason="public temple policy")
        if state and area.value in state.get("allowed_areas", []):
            return TempleAccessDecision(allowed=True, area=area, reason="explicit persisted permission")
        profile = await self.beliefs.get_character_belief(character)
        is_member = bool(profile and profile.temple_or_faction_id in {row.id, policy.faction_id})
        is_clergy = bool(profile and profile.religious_role in _PRIEST_ROLES)
        if area is TempleArea.MEMBER and policy.member_access and is_member:
            return TempleAccessDecision(allowed=True, area=area, reason="verified religious membership")
        if area is TempleArea.CLERGY_ONLY and policy.clergy_access and is_clergy:
            return TempleAccessDecision(allowed=True, area=area, reason="verified clergy role")
        if area is TempleArea.EMERGENCY_SANCTUARY and policy.emergency_sanctuary:
            return TempleAccessDecision(
                allowed=True, area=area, reason="temple emergency sanctuary policy"
            )
        return TempleAccessDecision(
            allowed=False, area=area, reason="permission or policy requirement not met"
        )

    async def grant_temple_access(
        self, *, campaign_id: str, temple_key: str, character_id: str,
        areas: tuple[TempleArea, ...], source_event_id: str,
    ) -> CampaignCanonRecord:
        character = await self._character(campaign_id, character_id)
        temple, _ = await self._temple_by_key(campaign_id, temple_key)
        row = await self._state_record(campaign_id, _ACCESS_CATEGORY, temple.id, character.id)
        data = dict(row.data or {}) if row else {}
        applied = set(data.get("source_event_ids", []))
        if source_event_id in applied:
            return row
        allowed = set(data.get("allowed_areas", []))
        allowed.update(area.value for area in areas)
        data.update({"character_id": character.id, "allowed_areas": sorted(allowed),
                     "source_event_ids": sorted(applied | {source_event_id})})
        if row:
            row.data = data
            return row
        row = CampaignCanonRecord(
            campaign_id=campaign_id, category=_ACCESS_CATEGORY,
            fact=f"temple access for {character.id}", visibility="DM_ONLY",
            provenance="COMMITTED_EVENT", scope_type="religious_temple", scope_id=temple.id,
            data=data,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def change_faction_reputation(
        self, *, campaign_id: str, faction_id: str, character_id: str,
        delta: int, source_event_id: str,
    ) -> int:
        character = await self._character(campaign_id, character_id)
        faction = await self.session.get(Threat, faction_id)
        if faction is None or faction.campaign_id != campaign_id:
            raise NotFoundError("religious faction not found in campaign")
        row = await self._state_record(campaign_id, _REPUTATION_CATEGORY, faction.id, character.id)
        data = dict(row.data or {}) if row else {}
        applied = set(data.get("source_event_ids", []))
        if source_event_id in applied:
            return int(data.get("score", 0))
        score = max(-100, min(100, int(data.get("score", 0)) + int(delta)))
        data.update({"character_id": character.id, "score": score,
                     "source_event_ids": sorted(applied | {source_event_id})})
        if row:
            row.data = data
        else:
            row = CampaignCanonRecord(
                campaign_id=campaign_id, category=_REPUTATION_CATEGORY,
                fact=f"religious faction reputation for {character.id}",
                visibility="DM_ONLY", provenance="COMMITTED_EVENT",
                scope_type="faction", scope_id=faction.id, data=data,
            )
            self.session.add(row)
        await self.session.flush()
        return score

    async def purchase_temple_service(
        self, *, campaign_id: str, temple_key: str, character_id: str,
        service_kind: TempleServiceKind, idempotency_key: str,
    ) -> dict[str, int]:
        character = await self._character(campaign_id, character_id)
        temple, policy = await self._temple_by_key(campaign_id, temple_key)
        service = next((item for item in policy.services if item.kind is service_kind), None)
        if service is None or not service.available:
            raise ValidationError("temple service is not available")
        decision = await self.decide_temple_access(
            campaign_id=campaign_id, temple_key=temple_key,
            character_id=character_id, area=service.required_area,
        )
        if not decision.allowed:
            raise ValidationError(decision.reason)
        if not service.price:
            raise ValidationError("service has no owner-approved price; free service was not assumed")
        return await WalletService(self.session).apply(
            character_id=character.id,
            amounts={key: -value for key, value in service.price.items()},
            transaction_type="TEMPLE_SERVICE", counterparty_ref=f"religious_temple:{temple.id}",
            reason=f"{policy.name}: {service_kind.value}", idempotency_key=idempotency_key,
        )

    @staticmethod
    def _safe_belief(profile) -> dict:
        return {
            "primary_deity_key": profile.primary_deity_key,
            "secondary_deity_keys": list(profile.secondary_deity_keys),
            "stance": profile.stance.value,
            "devotion": profile.devotion.value,
            "visibility": profile.visibility.value,
            "personal_interpretation": profile.personal_interpretation,
        }

    async def _learned_belief(self, npc_id: str, listener_ref: str):
        subject = _BELIEF_SUBJECT.format(listener_ref=listener_ref)
        row = (await self.session.execute(select(NPCFact).where(
            NPCFact.npc_id == npc_id, NPCFact.subject == subject,
            NPCFact.status.in_([KnowledgeStatus.KNOWS.value, KnowledgeStatus.BELIEVES.value]),
        ))).scalars().first()
        if not row:
            return None
        try:
            value = json.loads(row.fact)
        except json.JSONDecodeError:
            return None
        try:
            source = ReligiousKnowledgeSource(row.source)
        except ValueError:
            source = ReligiousKnowledgeSource.PRIOR_CONVERSATION
        return value, (source,)

    async def _npc(self, campaign_id: str, npc_id: str) -> NPC:
        npc = await self.session.get(NPC, npc_id)
        if npc is None or npc.campaign_id != campaign_id:
            raise NotFoundError("NPC not found in campaign")
        return npc

    async def _character(self, campaign_id: str, character_id: str) -> Character:
        character = await self.session.get(Character, character_id)
        if character is None or character.campaign_id != campaign_id:
            raise NotFoundError("character not found in campaign")
        return character

    async def _temple_at(self, campaign_id: str, location_id: str | None):
        if not location_id:
            return None
        return (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category == _TEMPLE_CATEGORY,
            CampaignCanonRecord.scope_id == location_id,
            CampaignCanonRecord.active.is_(True),
        ))).scalars().first()

    async def _temple_by_key(self, campaign_id: str, temple_key: str):
        row = (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category == _TEMPLE_CATEGORY,
            CampaignCanonRecord.fact == temple_key,
            CampaignCanonRecord.active.is_(True),
        ))).scalars().first()
        if row is None:
            raise NotFoundError("temple not found in campaign")
        return row, TemplePolicy.model_validate((row.data or {}).get("policy"))

    async def _state_record(self, campaign_id: str, category: str, scope_id: str, character_id: str):
        rows = (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category == category,
            CampaignCanonRecord.scope_id == scope_id,
            CampaignCanonRecord.active.is_(True),
        ))).scalars().all()
        return next((row for row in rows if (row.data or {}).get("character_id") == character_id), None)

    async def _access_state(self, campaign_id: str, character_id: str, temple_id: str | None):
        if not temple_id:
            return None
        row = await self._state_record(campaign_id, _ACCESS_CATEGORY, temple_id, character_id)
        return dict(row.data or {}) if row else None

    async def _current_religious_context(self, campaign_id: str, location_id: str | None):
        if not location_id:
            return ()
        rows = (await self.session.execute(select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign_id,
            CampaignCanonRecord.category.in_(["religious_event", _TEMPLE_CATEGORY]),
            CampaignCanonRecord.scope_id == location_id,
            CampaignCanonRecord.active.is_(True),
        ))).scalars().all()
        return tuple(row.fact for row in rows)


__all__ = ["ReligiousBehavior", "ReligiousInteractionService"]
