"""Character creation and the one-active-character-per-member rule."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.tabletop.rules import ability_modifier, proficiency_bonus_for_level, validate_class


class CharacterService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_character(
        self,
        *,
        member_id: str,
        name: str,
        species: str = "human",
        char_class: str = "fighter",
        abilities: dict[str, int] | None = None,
        proficiencies: list[str] | None = None,
        level: int = 1,
        max_hp: int = 10,
        ac: int = 12,
        set_active: bool = True,
    ) -> Character:
        member = await self.session.get(CampaignMember, member_id)
        if member is None:
            raise NotFoundError(f"member {member_id} not found")
        validate_class(char_class)
        # Registry-known chassis for the quick path (saves, hit die, speed).
        from app.rules_content import get_registry

        cls = get_registry().get_class(char_class)
        spec = get_registry().species.get(species.lower())

        abilities = abilities or {}
        char = Character(
            campaign_id=member.campaign_id,
            owner_member_id=member.id,
            name=name,
            species=species,
            char_class=char_class,
            str_score=abilities.get("str", 10),
            dex_score=abilities.get("dex", 10),
            con_score=abilities.get("con", 10),
            int_score=abilities.get("int", 10),
            wis_score=abilities.get("wis", 10),
            cha_score=abilities.get("cha", 10),
            proficiencies=proficiencies or [],
            save_proficiencies=list(cls.saving_throws),
            proficiency_bonus=proficiency_bonus_for_level(level),
            level=level,
            max_hp=max_hp,
            hp=max_hp,
            ac=ac,
            speed=spec.speed if spec else 30,
            hit_die=cls.hit_die,
            hit_dice_remaining=level,
        )
        self.session.add(char)
        await self.session.flush()

        if set_active:
            await self.set_active_character(member_id=member.id, character_id=char.id)
        return char

    async def set_active_character(self, *, member_id: str, character_id: str) -> CampaignMember:
        member = await self.session.get(CampaignMember, member_id)
        if member is None:
            raise NotFoundError(f"member {member_id} not found")
        char = await self.session.get(Character, character_id)
        if char is None:
            raise NotFoundError(f"character {character_id} not found")
        if char.owner_member_id != member_id:
            raise ValidationError("character is not owned by this member")
        # One active character per member: this single pointer IS the invariant.
        member.active_character_id = character_id
        return member

    async def get_active_character(self, member: CampaignMember) -> Character | None:
        if member.active_character_id is None:
            return None
        return await self.session.get(Character, member.active_character_id)

    async def list_characters(self, member_id: str) -> list[Character]:
        return list(
            (
                await self.session.execute(
                    select(Character).where(Character.owner_member_id == member_id)
                )
            ).scalars()
        )

    @staticmethod
    def ability_modifier(character: Character, ability: str) -> int:
        return ability_modifier(character.ability_score(ability))
