"""level_up — advance a character one level through the framework, not by hand.

Deterministic and edition-correct (SRD 5.2.1):
- level += 1, proficiency bonus recomputed;
- max HP += the class hit die's fixed average + CON modifier (the "take the
  average" option — no hidden dice at the table);
- hit-dice pool cap grows by one;
- newly-available class features are granted; any that carry a limited-use
  resource have that resource granted (re-scaled to the new level for existing
  ones), so a level-3 caster's slots/uses match its level.

Runs inside the caller's transaction (flush, no commit). It never touches spells
that require a player choice (new prepared/known picks) — those remain explicit;
this establishes the mechanical scaffolding the choice UI hangs off.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.progression import CharacterGrant
from app.rules_content import get_registry
from app.tabletop.rules.core import ability_modifier, proficiency_bonus_for_level

# Fixed "average" HP gained per level by hit-die size (die/2 + 1), 2024 option.
_HIT_DIE_AVERAGE = {6: 4, 8: 5, 10: 6, 12: 7}


async def level_up(session: AsyncSession, character: Character) -> dict:
    """Advance `character` by one level. Returns a Thai-note summary dict."""
    reg = get_registry()
    cls = reg.get_class(character.char_class)
    from app.tabletop.resources import ResourceEngine

    engine = ResourceEngine(session)

    before_level = character.level
    character.level += 1
    character.proficiency_bonus = proficiency_bonus_for_level(character.level)

    con_mod = ability_modifier(character.con_score)
    hp_gain = max(1, _HIT_DIE_AVERAGE.get(cls.hit_die, cls.hit_die // 2 + 1) + con_mod)
    character.max_hp += hp_gain
    character.hp = min(character.max_hp, character.hp + hp_gain)
    character.hit_dice_remaining = min(character.level, character.hit_dice_remaining + 1)

    notes: list[str] = [f"เลเวล {before_level} → {character.level}",
                        f"HP +{hp_gain} (สูงสุด {character.max_hp})"]

    # Grant features newly unlocked at the new level; wire their resources.
    from sqlalchemy import select

    granted_keys = {
        g.key for g in (await session.execute(
            select(CharacterGrant).where(
                CharacterGrant.character_id == character.id,
                CharacterGrant.grant_type == "feature"))).scalars()
    }
    for feat in cls.features_at(character.level):
        if feat.level <= before_level or feat.key in granted_keys:
            # Already had it; re-scale its resource pool to the new level.
            if feat.resource_id:
                await _regrant_resource(engine, character, feat.resource_id)
            continue
        session.add(CharacterGrant(
            character_id=character.id, grant_type="feature", key=feat.key,
            name_th=feat.name_th, source_type="CLASS", source_key=f"class:{cls.name}"))
        notes.append(f"ได้ฟีเจอร์ใหม่: {feat.name_th}")
        if feat.resource_id:
            await engine.grant(character, feat.resource_id)

    # Re-scale spell slots (and any other level-scaled resource already present).
    if cls.spellcasting is not None:
        for rid in cls.spellcasting.slot_resources.values():
            await _regrant_resource(engine, character, rid)

    await session.flush()
    from app.services.events import EventService

    from app.core.ids import entity_ref

    await EventService(session).record(
        campaign_id=character.campaign_id, event_type=EventType.FEATURE_USED,
        actor_entity=entity_ref("character", character.id), visibility=Visibility.PARTY,
        payload={"summary": f"{character.name} เลื่อนเป็นเลเวล {character.level}",
                 "kind": "level_up", "notes": notes},
        mechanical_changes={"level": character.level, "max_hp": character.max_hp},
        narrative_significance=20)
    return {"level": character.level, "notes": notes}


async def _regrant_resource(engine, character: Character, resource_id: str) -> None:
    """Re-scale an existing resource's max to the character's current level,
    topping up current by the increase (a level-up refreshes new capacity)."""
    reg = get_registry()
    state = await engine.get(character.id, resource_id)
    d = reg.get_resource(resource_id)
    mod = ability_modifier(character.ability_score(d.max_formula.ability)) if d.max_formula.ability else 0
    new_max = reg.resolve_max(d.max_formula, class_level=character.level, ability_mod=mod)
    if state is None:
        await engine.grant(character, resource_id)
        return
    gained = max(0, new_max - state.max_value)
    state.max_value = new_max
    state.current = min(new_max, state.current + gained)
