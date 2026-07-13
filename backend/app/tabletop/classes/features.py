"""ClassFeatureService — activate a class feature through the shared systems.

"! ใช้ Second Wind" / "! เข้าโหมดเกรี้ยวกราด" resolve here: the feature is looked up
against the character's GRANTED features (never invented), its resource is spent
atomically via ResourceEngine, and its committed effect is applied using the same
primitives everything else uses — DiceEngine for numbers, ActiveEffect for stances
(Rage/Reckless), Character HP for healing. The LLM never decides the numbers or
whether the feature is available; the engine does.

Reactive features (Uncanny Dodge, Evasion, Deflect, Stunning Strike, Indomitable)
and combat-integrated ones (Extra Attack, Sneak Attack, Rage damage/resistance) are
applied inside combat resolution, not proactively activated here — see combat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import RulesViolation
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import ActiveEffect, CharacterGrant
from app.rules_content import get_registry
from app.tabletop.dice import DiceEngine
from app.tabletop.resources import ResourceEngine

# Physical damage types Rage grants resistance to (SRD 5.2.1).
RAGE_RESISTANCES = ("bludgeoning", "piercing", "slashing")


def rage_damage_bonus(level: int) -> int:
    """+2 (1-8), +3 (9-15), +4 (16+)."""
    return 4 if level >= 16 else 3 if level >= 9 else 2


def martial_arts_die(level: int) -> int:
    """Monk unarmed/Martial-Arts die: d6, d8 at 5, d10 at 11, d12 at 17."""
    return 12 if level >= 17 else 10 if level >= 11 else 8 if level >= 5 else 6


@dataclass
class FeatureOutcome:
    feature: str
    name_th: str
    spent: str | None = None          # resource id spent, if any
    healing: int = 0
    effect_started: str | None = None  # ActiveEffect name begun, if any
    line_th: str = ""
    notes: list[str] = field(default_factory=list)


class ClassFeatureService:
    def __init__(self, session: AsyncSession, dice: DiceEngine | None = None) -> None:
        self.session = session
        self.dice = dice
        self.reg = get_registry()

    async def granted_feature_keys(self, character_id: str) -> set[str]:
        rows = (await self.session.execute(select(CharacterGrant).where(
            CharacterGrant.character_id == character_id,
            CharacterGrant.grant_type.in_(("feature", "subclass_feature"))))).scalars()
        return {g.key for g in rows}

    async def activate(self, character: Character, feature_key: str) -> FeatureOutcome:
        """Activate one feature the character actually has. Raises RulesViolation if
        they don't have it, or if its resource is exhausted (nothing is spent then)."""
        cls = self.reg.get_class(character.char_class)
        feat = next((f for f in cls.features_at(character.level) if f.key == feature_key), None)
        if feat is None:
            raise RulesViolation(f"{character.name} ยังไม่มีความสามารถ {feature_key!r}")
        handler = getattr(self, f"_do_{feature_key}", None)
        if handler is None:
            # A supported feature with no proactive handler is reactive/passive.
            raise RulesViolation(
                f"{feat.name_th} ทำงานอัตโนมัติ/ตอบโต้ ไม่ต้องสั่งใช้เอง")
        out = FeatureOutcome(feature=feature_key, name_th=feat.name_th,
                             spent=feat.resource_id)
        if feat.resource_id:
            await ResourceEngine(self.session).spend(character.id, feat.resource_id, 1)
        await handler(character, out)
        await self._record(character, out)
        return out

    # --- fighter ---------------------------------------------------------------
    async def _do_second_wind(self, character: Character, out: FeatureOutcome) -> None:
        if self.dice is None:
            raise RulesViolation("second wind ต้องใช้ dice engine")
        healed = self.dice.roll_die(10) + character.level
        before = character.hp
        character.hp = min(character.max_hp, character.hp + healed)
        out.healing = character.hp - before
        out.line_th = f"Second Wind — ฟื้น {out.healing} HP (HP {before} → {character.hp})"

    async def _do_action_surge(self, character: Character, out: FeatureOutcome) -> None:
        # In combat, refresh this combatant's action; otherwise it's a confirmation.
        from app.models.combat import Combatant, CombatEncounter

        ref = entity_ref("character", character.id)
        combatant = (await self.session.execute(select(Combatant).join(
            CombatEncounter, Combatant.encounter_id == CombatEncounter.id).where(
            CombatEncounter.status == "active", Combatant.entity_ref == ref))).scalars().first()
        if combatant is not None:
            combatant.has_action = True
            out.notes.append("ได้แอ็กชันเพิ่มอีกหนึ่งในเทิร์นนี้")
        out.line_th = "Action Surge — พลังทะลักขึ้นมา ลงมือได้อีกครั้งทันที"

    # --- barbarian -------------------------------------------------------------
    async def _do_rage(self, character: Character, out: FeatureOutcome) -> None:
        # A stance (NOT concentration): resistance to physical + a damage bonus,
        # tracked as an ActiveEffect the combat engine reads.
        await self._end_effect(character, "Rage")   # never stack two rages
        bonus = rage_damage_bonus(character.level)
        effect = ActiveEffect(
            campaign_id=character.campaign_id, character_id=character.id, name="Rage",
            requires_concentration=False, active=True,
            data={"resistances": list(RAGE_RESISTANCES), "damage_bonus": bonus,
                  "kind": "rage"})
        self.session.add(effect)
        await self.session.flush()
        out.effect_started = "Rage"
        out.line_th = (f"เกรี้ยวกราด! ต้านทานดาเมจกายภาพ และโจมตีแรงขึ้น +{bonus}")

    async def _do_reckless_attack(self, character: Character, out: FeatureOutcome) -> None:
        await self._end_effect(character, "Reckless Attack")
        effect = ActiveEffect(
            campaign_id=character.campaign_id, character_id=character.id,
            name="Reckless Attack", requires_concentration=False, active=True,
            data={"kind": "reckless", "self_advantage": True, "attackers_advantage": True})
        self.session.add(effect)
        await self.session.flush()
        out.effect_started = "Reckless Attack"
        out.line_th = "บ้าระห่ำ — ทุ่มสุดตัว (ได้เปรียบการโจมตี แต่ก็เปิดช่องให้ศัตรู)"

    # --- monk ------------------------------------------------------------------
    async def _do_flurry_of_blows(self, character: Character, out: FeatureOutcome) -> None:
        out.line_th = "หมัดรัว — จ่าย 1 Focus ปล่อยหมัดมือเปล่าเพิ่มสองครั้ง"

    async def _do_patient_defense(self, character: Character, out: FeatureOutcome) -> None:
        out.line_th = "ตั้งรับ — จ่าย 1 Focus ถอยห่างและตั้งการ์ด (Disengage + Dodge)"

    async def _do_step_of_the_wind(self, character: Character, out: FeatureOutcome) -> None:
        out.line_th = "ย่างลม — จ่าย 1 Focus พุ่งตัวออกไปอย่างว่องไว (Dash + Disengage)"

    # --- helpers ---------------------------------------------------------------
    async def _end_effect(self, character: Character, name: str) -> None:
        existing = (await self.session.execute(select(ActiveEffect).where(
            ActiveEffect.character_id == character.id, ActiveEffect.name == name,
            ActiveEffect.active.is_(True)))).scalars().first()
        if existing is not None:
            existing.active = False

    async def _record(self, character: Character, out: FeatureOutcome) -> None:
        from app.services.events import EventService

        await EventService(self.session).record(
            campaign_id=character.campaign_id, event_type=EventType.FEATURE_USED,
            actor_entity=entity_ref("character", character.id), visibility=Visibility.PARTY,
            mechanical_changes={"healing": out.healing} if out.healing else {},
            payload={"feature": out.feature, "summary": out.line_th,
                     "spent": out.spent, "effect": out.effect_started},
            narrative_significance=14)


async def active_rage(session: AsyncSession, character_id: str) -> ActiveEffect | None:
    return (await session.execute(select(ActiveEffect).where(
        ActiveEffect.character_id == character_id, ActiveEffect.name == "Rage",
        ActiveEffect.active.is_(True)))).scalars().first()
