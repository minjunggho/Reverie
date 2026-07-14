"""Bounded NPC belief proposals over active campaign faith content.

This module never grants class mechanics and never overwrites imported canon.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.errors import ConflictError
from app.rules_content.choice_names import normalize_choice_name
from app.schemas.belief import (
    BeliefProfile,
    BeliefSource,
    BeliefStance,
    BeliefVisibility,
    DevotionLevel,
    ReligiousKnowledgeLevel,
    ReligiousRole,
)
from app.services.beliefs import BeliefService
from app.services.faith import FaithService


@dataclass(frozen=True)
class NPCBeliefContext:
    name: str
    culture: str = ""
    region: str = ""
    settlement: str = ""
    profession: str = ""
    character_class: str = ""
    family: str = ""
    faction: str = ""
    temple_connection: str = ""
    personality: str = ""
    current_hardship: str = ""
    campaign_tone: str = ""
    imported_canon: BeliefProfile | None = None
    religious_role: ReligiousRole | None = None


@dataclass(frozen=True)
class NPCBeliefProposal:
    profile: BeliefProfile | None
    rationale: str
    imported_canon_won: bool = False


_DEEP_ROLES = {
    ReligiousRole.PRIEST,
    ReligiousRole.THEOLOGIAN,
    ReligiousRole.INQUISITOR,
    ReligiousRole.RELIGIOUS_OFFICIAL,
}
_INFORMED_ROLES = {
    ReligiousRole.ACOLYTE,
    ReligiousRole.MONK,
    ReligiousRole.SHRINE_KEEPER,
    ReligiousRole.FUNERAL_KEEPER,
    ReligiousRole.HERETIC,
    ReligiousRole.CULTIST,
}


def knowledge_for_role(role: ReligiousRole | None) -> ReligiousKnowledgeLevel:
    if role in _DEEP_ROLES:
        return ReligiousKnowledgeLevel.DEEP
    if role in _INFORMED_ROLES:
        return ReligiousKnowledgeLevel.INFORMED
    if role is not None:
        return ReligiousKnowledgeLevel.CULTURAL
    return ReligiousKnowledgeLevel.NONE


class NPCBeliefGenerator:
    def __init__(self, faith: FaithService) -> None:
        self.faith = faith

    async def propose(
        self, campaign_id: str, context: NPCBeliefContext
    ) -> NPCBeliefProposal:
        if context.imported_canon is not None:
            imported = context.imported_canon.model_copy(update={
                "source": BeliefSource.IMPORTED_CANON,
                "provenance": context.imported_canon.provenance or "IMPORTED_NPC_CANON",
            })
            await BeliefService(self.faith.session, self.faith).validate_profile(
                campaign_id, imported
            )
            return NPCBeliefProposal(
                imported,
                "explicit imported NPC canon has priority",
                imported_canon_won=True,
            )

        deities = await self.faith.list_selectable_deities(campaign_id)
        text_fields = (
            context.culture, context.region, context.settlement, context.profession,
            context.character_class, context.family, context.faction,
            context.temple_connection, context.personality, context.current_hardship,
        )
        matches: set[str] = set()
        for field in text_fields:
            normalized_field = f" {normalize_choice_name(field)} "
            if normalized_field.strip():
                for deity in deities:
                    refs = (deity.key, deity.canonical_name_en, deity.name_th, *deity.aliases)
                    if any(
                        (needle := normalize_choice_name(ref))
                        and f" {needle} " in normalized_field
                        for ref in refs
                    ):
                        matches.add(deity.key)
        if len(matches) > 1:
            raise ConflictError(
                f"NPC belief context is ambiguous between {sorted(matches)!r}; "
                "generated content must remain a proposal"
            )

        deity_key = next(iter(matches), None)
        role = context.religious_role
        knowledge = knowledge_for_role(role)
        seed_text = "|".join((campaign_id, *map(str, context.__dict__.values())))
        bucket = hashlib.sha256(seed_text.encode("utf-8")).digest()[0] % 8

        if deity_key is None:
            # No active deity was justified by context. Most ordinary people do not
            # receive an invented patron; some have a non-deity stance, some no
            # meaningful religious identity at all.
            if role is not None:
                raise ConflictError(
                    f"religious role {role.value} needs an explicit active deity context"
                )
            if bucket in {0, 1, 2}:
                return NPCBeliefProposal(None, "no meaningful religious identity proposed")
            stance = {
                3: BeliefStance.AGNOSTIC,
                4: BeliefStance.DOUBTFUL,
                5: BeliefStance.CULTURAL,
                6: BeliefStance.ATHEIST,
                7: BeliefStance.QUESTIONING,
            }[bucket]
            return NPCBeliefProposal(BeliefProfile(
                stance=stance,
                devotion=DevotionLevel.NONE if stance is not BeliefStance.CULTURAL else DevotionLevel.CASUAL,
                visibility=BeliefVisibility.PUBLIC,
                knowledge_level=ReligiousKnowledgeLevel.CULTURAL if stance is BeliefStance.CULTURAL else ReligiousKnowledgeLevel.NONE,
                source=BeliefSource.AI_GENERATED,
                provenance="NPC_BELIEF_PROPOSAL_V1",
            ), "non-deity stance proposed from ordinary NPC distribution")

        if role is not None:
            stance = BeliefStance.DEVOUT
            devotion = DevotionLevel.DEVOUT
        else:
            stance = (
                BeliefStance.CULTURAL if bucket in {0, 1, 2}
                else BeliefStance.QUESTIONING if bucket == 3
                else BeliefStance.BELIEVER
            )
            devotion = (
                DevotionLevel.CASUAL if stance is BeliefStance.CULTURAL
                else DevotionLevel.ORDINARY
            )
        reason = context.temple_connection or context.family or context.profession or None
        return NPCBeliefProposal(BeliefProfile(
            primary_deity_key=deity_key,
            stance=stance,
            devotion=devotion,
            visibility=BeliefVisibility.PUBLIC,
            religious_role=role,
            knowledge_level=knowledge,
            personal_reason=reason,
            doubt=context.current_hardship or None if stance is BeliefStance.QUESTIONING else None,
            source=BeliefSource.AI_GENERATED,
            provenance="NPC_BELIEF_PROPOSAL_V1",
        ), "active deity explicitly supported by NPC context")


__all__ = [
    "NPCBeliefContext",
    "NPCBeliefGenerator",
    "NPCBeliefProposal",
    "knowledge_for_role",
]
