"""DamageService — the typed, ordered damage & healing pipeline (SRD 5.2.1; §15).

Order of operations, per damage event:
1. each component independently: Immunity → 0; Resistance → half (round down);
   Vulnerability → double. NEVER flattened before type interactions.
2. total → Temporary HP absorbs first (never healable, keep-higher rule elsewhere)
3. HP reduction. At 0: excess ≥ max HP → instant death; else dying (unconscious,
   death-save state engaged). Damage while dying → death-save failure(s) instead
   (2 on a critical hit).
4. If the target is concentrating → emits a required CON save DC max(10, dmg//2),
   resolved via ConcentrationService (engine dice; failure ends the effect).

Narration receives the committed component breakdown; it cannot re-price damage.
Healing: cap at max HP; any amount ends dying and resets death saves; Temp HP is
never restored by healing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.services.events import EventService
from app.tabletop.effects.concentration import ConcentrationService
from app.tabletop.rules.derive import resistances as derive_resistances

DAMAGE_TYPES = frozenset({
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
})


@dataclass
class DamageComponent:
    amount: int
    damage_type: str

    def __post_init__(self) -> None:
        if self.damage_type not in DAMAGE_TYPES:
            raise ValidationError(f"unknown damage type: {self.damage_type!r}")
        if self.amount < 0:
            raise ValidationError("damage amount must be >= 0")


@dataclass
class ResolvedComponent:
    damage_type: str
    raw: int
    final: int
    note: str = ""          # "ต้านทาน" | "อ่อนแอ" | "ไม่ระคายเลย" | ""


@dataclass
class DamageResult:
    components: list[ResolvedComponent]
    total: int
    absorbed_by_temp_hp: int
    hp_before: int
    hp_after: int
    dying: bool
    dead: bool
    death_save_failures_added: int
    concentration_save: dict | None = None   # {"dc": int, "passed": bool, "effect": name}

    def breakdown_lines(self) -> list[str]:
        out = []
        for c in self.components:
            note = f" ({c.note})" if c.note else ""
            out.append(f"{c.damage_type}: {c.raw} → {c.final}{note}")
        return out


@dataclass
class HealResult:
    amount: int
    hp_before: int
    hp_after: int
    revived: bool


class DamageService:
    def __init__(self, session: AsyncSession, dice_engine=None) -> None:
        self.session = session
        self.events = EventService(session)
        self.dice = dice_engine  # required only when a concentration save must roll

    async def apply_damage(
        self, *, target: Character, components: list[DamageComponent],
        source: str = "", session_id: str | None = None, scene_id: str | None = None,
        critical: bool = False, character_grants: list | None = None,
    ) -> DamageResult:
        if target.dead:
            raise ValidationError("target is already dead")
        resist = derive_resistances(character_grants or [])
        resolved: list[ResolvedComponent] = []
        for comp in components:
            final, note = comp.amount, ""
            if comp.damage_type in resist:
                final, note = comp.amount // 2, "ต้านทาน"
            resolved.append(ResolvedComponent(comp.damage_type, comp.amount, final, note))
        total = sum(c.final for c in resolved)

        hp_before = target.hp
        was_dying = target.dying
        absorbed = min(target.temp_hp, total)
        target.temp_hp -= absorbed
        remaining = total - absorbed

        failures_added = 0
        if was_dying and remaining > 0:
            # Damage while dying: death-save failures, not HP math.
            failures_added = 2 if critical else 1
            saves = dict(target.death_saves or {"successes": 0, "failures": 0})
            saves["failures"] = saves.get("failures", 0) + failures_added
            target.death_saves = saves
            target.stable = False
            if saves["failures"] >= 3:
                target.dead = True
        else:
            new_hp = target.hp - remaining
            if new_hp <= 0:
                excess = -new_hp
                target.hp = 0
                if excess >= target.max_hp:
                    target.dead = True            # instant death
                else:
                    target.stable = False
                    target.death_saves = {"successes": 0, "failures": 0}
            else:
                target.hp = new_hp

        result = DamageResult(
            components=resolved, total=total, absorbed_by_temp_hp=absorbed,
            hp_before=hp_before, hp_after=target.hp,
            dying=target.dying, dead=target.dead,
            death_save_failures_added=failures_added,
        )

        await self.events.record(
            campaign_id=target.campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.DAMAGE_APPLIED,
            actor_entity=source or "system",
            target_entities=[entity_ref("character", target.id)],
            visibility=Visibility.PARTY,
            mechanical_changes={
                "components": [{"type": c.damage_type, "raw": c.raw, "final": c.final,
                                "note": c.note} for c in resolved],
                "total": total, "temp_hp_absorbed": absorbed,
                "hp": {"from": hp_before, "to": target.hp},
                "dying": target.dying, "dead": target.dead,
            },
            payload={"summary": f"{target.name} รับดาเมจ {total}"},
            narrative_significance=25 if (target.dying or target.dead) else 15,
        )

        # Concentration trigger (only when damage actually landed).
        if total > 0 and not target.dead:
            conc = ConcentrationService(self.session, self.dice)
            save = await conc.on_damage_taken(
                character=target, damage_total=total,
                session_id=session_id, scene_id=scene_id,
            )
            if save is not None:
                result.concentration_save = save
        elif target.dead:
            await ConcentrationService(self.session, None).end_all_for(
                target, reason="death", session_id=session_id, scene_id=scene_id,
            )
        return result

    async def heal(
        self, *, target: Character, amount: int, source: str = "",
        session_id: str | None = None, scene_id: str | None = None,
    ) -> HealResult:
        if amount < 0:
            raise ValidationError("healing must be >= 0")
        if target.dead:
            raise ValidationError("the dead are beyond healing")
        hp_before = target.hp
        revived = target.dying and amount > 0
        target.hp = min(target.max_hp, target.hp + amount)
        if revived or (hp_before == 0 and amount > 0):
            target.stable = False
            target.death_saves = {"successes": 0, "failures": 0}
        await self.events.record(
            campaign_id=target.campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.DAMAGE_APPLIED, actor_entity=source or "system",
            target_entities=[entity_ref("character", target.id)],
            visibility=Visibility.PARTY,
            mechanical_changes={"healing": amount,
                                "hp": {"from": hp_before, "to": target.hp},
                                "revived": revived},
            payload={"summary": f"{target.name} ฟื้น {target.hp - hp_before} HP"},
            narrative_significance=20 if revived else 10,
        )
        return HealResult(amount=amount, hp_before=hp_before, hp_after=target.hp,
                          revived=revived)

    @staticmethod
    def grant_temp_hp(target: Character, amount: int) -> int:
        """Temp HP doesn't stack — keep the higher pool (SRD rule)."""
        target.temp_hp = max(target.temp_hp, max(0, amount))
        return target.temp_hp
