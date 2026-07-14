"""Campaign-scoped read API and explicit pantheon activation.

No method in this module mutates a character, NPC, faction, story, or mechanic.
Static lore comes from the validated faith registry; campaign rows store only the
content packs their owner has explicitly activated.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.campaign import Campaign
from app.rules_content.faith_registry import (
    DeityDefinition,
    DeityRelationship,
    DeityResolution,
    FaithContentError,
    FaithRegistry,
    PantheonActivationStatus,
    PantheonDefinition,
    get_faith_registry,
)


class FaithService:
    def __init__(
        self,
        session: AsyncSession,
        registry: FaithRegistry | None = None,
    ) -> None:
        self.session = session
        self.registry = registry or get_faith_registry()

    async def activate_pantheon(
        self,
        campaign_id: str,
        pantheon_key: str,
    ) -> PantheonDefinition:
        """Explicitly activate available content for one campaign only."""
        campaign = await self._campaign(campaign_id)
        pantheon = self.registry.get_pantheon(pantheon_key)
        if pantheon is None:
            raise FaithContentError(
                f"campaign {campaign_id} cannot activate missing pantheon {pantheon_key!r}"
            )
        if pantheon.activation_status is not PantheonActivationStatus.AVAILABLE:
            raise FaithContentError(
                f"campaign {campaign_id} cannot activate disabled pantheon {pantheon_key!r}"
            )
        active = list(campaign.active_pantheon_keys or [])
        if pantheon_key not in active:
            campaign.active_pantheon_keys = [*active, pantheon_key]
        return pantheon

    async def deactivate_pantheon(self, campaign_id: str, pantheon_key: str) -> None:
        campaign = await self._campaign(campaign_id)
        campaign.active_pantheon_keys = [
            key for key in (campaign.active_pantheon_keys or [])
            if key != pantheon_key
        ]

    async def list_active_pantheons(
        self,
        campaign_id: str,
    ) -> list[PantheonDefinition]:
        campaign = await self._campaign(campaign_id)
        active: list[PantheonDefinition] = []
        active_keys = list(campaign.active_pantheon_keys or [])
        if len(active_keys) != len(set(active_keys)):
            raise FaithContentError(
                f"campaign {campaign_id} has duplicate pantheon activation keys"
            )
        for key in active_keys:
            pantheon = self.registry.get_pantheon(key)
            if pantheon is None:
                raise FaithContentError(
                    f"campaign {campaign_id} activates missing pantheon {key!r}"
                )
            if pantheon.activation_status is not PantheonActivationStatus.AVAILABLE:
                raise FaithContentError(
                    f"campaign {campaign_id} activates disabled pantheon {key!r}"
                )
            active.append(pantheon)
        return active

    async def list_selectable_deities(
        self,
        campaign_id: str,
    ) -> list[DeityDefinition]:
        return [
            deity
            for deity in await self._active_deities(campaign_id)
            if deity.selectable_as_belief
        ]

    async def get_deity(
        self,
        campaign_id: str,
        deity_key: str,
    ) -> DeityDefinition | None:
        active_keys = {
            deity.key for deity in await self._active_deities(campaign_id)
        }
        if deity_key not in active_keys:
            return None
        return self.registry.get_deity(deity_key)

    async def resolve_deity_reference(
        self,
        campaign_id: str,
        reference: str,
    ) -> DeityResolution:
        active = await self._active_deities(campaign_id)
        return self.registry.resolver(deity.key for deity in active).resolve(reference)

    async def list_cleric_compatible_deities(
        self,
        campaign_id: str,
    ) -> list[DeityDefinition]:
        return [
            deity
            for deity in await self._active_deities(campaign_id)
            if deity.cleric_capable
        ]

    async def grants_cleric_powers(
        self,
        campaign_id: str,
        deity_key: str,
    ) -> bool:
        deity = await self.get_deity(campaign_id, deity_key)
        return bool(deity and deity.cleric_capable)

    async def list_deity_domains(
        self,
        campaign_id: str,
        deity_key: str,
    ) -> tuple[str, ...]:
        deity = await self.get_deity(campaign_id, deity_key)
        return deity.domains if deity else ()

    async def list_rivals(
        self,
        campaign_id: str,
        deity_key: str,
    ) -> list[DeityDefinition]:
        deity = await self.get_deity(campaign_id, deity_key)
        if deity is None:
            return []
        return await self._active_relationship_targets(campaign_id, deity.rivals)

    async def list_allies(
        self,
        campaign_id: str,
        deity_key: str,
    ) -> list[DeityDefinition]:
        deity = await self.get_deity(campaign_id, deity_key)
        if deity is None:
            return []
        return await self._active_relationship_targets(campaign_id, deity.allies)

    async def defined_relationship(
        self,
        campaign_id: str,
        left_key: str,
        right_key: str,
    ) -> DeityRelationship | None:
        left = await self.get_deity(campaign_id, left_key)
        right = await self.get_deity(campaign_id, right_key)
        if left is None or right is None:
            return None
        if right.key in left.allies or left.key in right.allies:
            return DeityRelationship.ALLY
        if right.key in left.rivals or left.key in right.rivals:
            return DeityRelationship.RIVAL
        if right.key in left.enemy_faiths or left.key in right.enemy_faiths:
            return DeityRelationship.ENEMY_FAITH
        return None

    async def validate_campaign_activations(self, campaign_id: str) -> None:
        await self.list_active_pantheons(campaign_id)

    async def validate_all_campaign_activations(self) -> None:
        campaign_ids = (
            await self.session.execute(select(Campaign.id))
        ).scalars().all()
        for campaign_id in campaign_ids:
            await self.validate_campaign_activations(campaign_id)

    async def _active_deities(self, campaign_id: str) -> list[DeityDefinition]:
        pantheons = await self.list_active_pantheons(campaign_id)
        result: list[DeityDefinition] = []
        for pantheon in pantheons:
            for key in pantheon.deity_keys:
                deity = self.registry.get_deity(key)
                if deity is None:
                    raise FaithContentError(
                        f"active pantheon {pantheon.key!r} references missing deity {key!r}"
                    )
                result.append(deity)
        return result

    async def _active_relationship_targets(
        self,
        campaign_id: str,
        keys: tuple[str, ...],
    ) -> list[DeityDefinition]:
        active = {deity.key: deity for deity in await self._active_deities(campaign_id)}
        return [active[key] for key in keys if key in active]

    async def _campaign(self, campaign_id: str) -> Campaign:
        campaign = await self.session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return campaign


__all__ = ["FaithService"]
