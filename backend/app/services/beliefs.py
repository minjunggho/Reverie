"""Campaign-scoped persistence and validation for character/NPC beliefs."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.character import Character
from app.models.location import Location
from app.models.npc import NPC
from app.models.world import Threat
from app.models.world_graph import CampaignCanonRecord
from app.rules_content.faith_registry import FaithContentError
from app.schemas.belief import (
    BeliefProfile,
    BeliefSource,
    BeliefVisibility,
)
from app.services.faith import FaithService

class _Unset:
    pass


_UNSET = _Unset()


@dataclass(frozen=True)
class BeliefWriteResult:
    profile: BeliefProfile | None
    replaced_source: BeliefSource | None = None


class BeliefService:
    """The only supported read/write boundary for persisted belief profiles."""

    _SOURCE_PRIORITY = {
        BeliefSource.AI_GENERATED: 10,
        BeliefSource.PLAYER_AUTHORED: 20,
        BeliefSource.OWNER_EDITED: 30,
        BeliefSource.IMPORTED_CANON: 40,
    }

    def __init__(self, session: AsyncSession, faith: FaithService | None = None) -> None:
        self.session = session
        self.faith = faith or FaithService(session)

    @staticmethod
    def decode(value: dict | BeliefProfile | None) -> BeliefProfile | None:
        if value is None or isinstance(value, BeliefProfile):
            return value
        try:
            return BeliefProfile.model_validate(value)
        except PydanticValidationError as exc:
            raise FaithContentError(f"invalid persisted belief profile: {exc}") from exc

    @staticmethod
    def encode(profile: BeliefProfile | None) -> dict | None:
        return None if profile is None else profile.model_dump(mode="json")

    async def validate_profile(
        self, campaign_id: str, profile: BeliefProfile | dict | None
    ) -> BeliefProfile | None:
        parsed = self.decode(profile)
        if parsed is None:
            return None
        references = [
            parsed.primary_deity_key,
            *parsed.secondary_deity_keys,
            parsed.former_deity_key,
        ]
        for key in (item for item in references if item):
            deity = await self.faith.get_deity(campaign_id, key)
            if deity is None:
                raise FaithContentError(
                    f"campaign={campaign_id}; belief deity={key!r}; expected deity from an active pantheon"
                )
            if key != parsed.former_deity_key and not deity.selectable_as_belief:
                raise FaithContentError(
                    f"campaign={campaign_id}; belief deity={key!r}; expected selectable_as_belief=true"
                )
        if parsed.temple_or_faction_id:
            linked = None
            for model in (Location, Threat, CampaignCanonRecord):
                linked = await self.session.get(model, parsed.temple_or_faction_id)
                if linked is not None:
                    break
            if linked is None or linked.campaign_id != campaign_id:
                raise FaithContentError(
                    f"campaign={campaign_id}; temple_or_faction_id="
                    f"{parsed.temple_or_faction_id!r}; expected an entity in the same campaign"
                )
        return parsed

    async def validate_cleric_mechanics(
        self,
        campaign_id: str,
        *,
        char_class: str,
        deity_key: str | None,
        domain: str | None,
        required: bool = True,
    ) -> tuple[str | None, str | None]:
        if char_class.casefold() != "cleric":
            if deity_key is not None or domain is not None:
                raise FaithContentError(
                    f"class={char_class}; cleric deity/domain supplied; expected mechanical faith only for Cleric"
                )
            return None, None
        if not deity_key and not domain and not required:
            return None, None
        if not deity_key or not domain:
            raise FaithContentError(
                "class=cleric; missing mechanical deity or domain; expected both values"
            )
        deity = await self.faith.get_deity(campaign_id, deity_key)
        if deity is None:
            raise FaithContentError(
                f"class=cleric; deity={deity_key!r}; expected deity from an active pantheon"
            )
        if not deity.cleric_capable:
            raise FaithContentError(
                f"class=cleric; deity={deity_key!r}; expected cleric_capable=true"
            )
        canonical_domain = next(
            (candidate for candidate in deity.domains if candidate.casefold() == domain.casefold()),
            None,
        )
        if canonical_domain is None:
            raise FaithContentError(
                f"class=cleric; deity={deity_key!r}; domain={domain!r}; "
                f"expected one of {list(deity.domains)!r}"
            )
        return deity.key, canonical_domain

    async def get_character_belief(self, character: Character) -> BeliefProfile | None:
        return await self.validate_profile(character.campaign_id, character.belief_profile)

    async def set_character_belief(
        self,
        character: Character,
        profile: BeliefProfile | dict | None,
        *,
        cleric_deity_key: str | None | _Unset = _UNSET,
        cleric_domain: str | None | _Unset = _UNSET,
    ) -> BeliefWriteResult:
        parsed = await self.validate_profile(character.campaign_id, profile)
        mechanics_supplied = (
            not isinstance(cleric_deity_key, _Unset)
            or not isinstance(cleric_domain, _Unset)
        )
        deity_input = (
            character.cleric_deity_key
            if isinstance(cleric_deity_key, _Unset) else cleric_deity_key
        )
        domain_input = (
            character.cleric_domain
            if isinstance(cleric_domain, _Unset) else cleric_domain
        )
        deity_key, domain = await self.validate_cleric_mechanics(
            character.campaign_id,
            char_class=character.char_class,
            deity_key=deity_input,
            domain=domain_input,
            required=mechanics_supplied or bool(deity_input or domain_input),
        )
        character.belief_profile = self.encode(parsed)
        character.cleric_deity_key = deity_key
        character.cleric_domain = domain
        return BeliefWriteResult(parsed)

    async def get_npc_belief(self, npc: NPC) -> BeliefProfile | None:
        return await self.validate_profile(npc.campaign_id, npc.belief_profile)

    async def set_npc_belief(
        self,
        npc: NPC,
        profile: BeliefProfile | dict | None,
        *,
        report_conflict: bool = True,
    ) -> BeliefWriteResult:
        parsed = await self.validate_profile(npc.campaign_id, profile)
        current = self.decode(npc.belief_profile)
        if current is not None and parsed is not None:
            old_priority = self._SOURCE_PRIORITY[current.source]
            new_priority = self._SOURCE_PRIORITY[parsed.source]
            if new_priority < old_priority:
                if report_conflict:
                    raise ConflictError(
                        f"NPC '{npc.name}' has {current.source.value} belief canon; "
                        f"{parsed.source.value} may only be proposed, not applied"
                    )
                return BeliefWriteResult(current)
        npc.belief_profile = self.encode(parsed)
        return BeliefWriteResult(parsed, current.source if current else None)

    async def get_character(self, campaign_id: str, character_id: str) -> Character:
        character = await self.session.get(Character, character_id)
        if character is None or character.campaign_id != campaign_id:
            raise NotFoundError(f"character {character_id} not found in campaign")
        return character

    async def get_npc(self, campaign_id: str, npc_id: str) -> NPC:
        npc = await self.session.get(NPC, npc_id)
        if npc is None or npc.campaign_id != campaign_id:
            raise NotFoundError(f"npc {npc_id} not found in campaign")
        return npc

    async def validate_all_persisted_profiles(self) -> None:
        for character in (await self.session.execute(select(Character))).scalars():
            await self.get_character_belief(character)
            await self.validate_cleric_mechanics(
                character.campaign_id,
                char_class=character.char_class,
                deity_key=character.cleric_deity_key,
                domain=character.cleric_domain,
                required=False,
            )
        for npc in (await self.session.execute(select(NPC))).scalars():
            await self.get_npc_belief(npc)

    @staticmethod
    def visible_profile(
        profile: BeliefProfile | dict | None, *, owner_view: bool
    ) -> BeliefProfile | None:
        parsed = BeliefService.decode(profile)
        if parsed is None:
            return None
        if not owner_view and parsed.visibility is not BeliefVisibility.PUBLIC:
            return None
        if owner_view or parsed.owner_notes is None:
            return parsed
        return parsed.model_copy(update={"owner_notes": None})


__all__ = ["BeliefService", "BeliefWriteResult"]
