"""Player Grimoire projections — PLAYER-SAFE by construction.

Every function returns plain JSON-safe dicts computed from canonical state via the
existing derivation engine and visibility-aware queries. Nothing here reads
Secret / DM_ONLY canon / other players' PLAYER_ONLY records, so those cannot leak
into a player payload regardless of what the frontend asks for.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import format_game_time
from app.core.ids import entity_ref
from app.models.campaign import Campaign, CampaignMember
from app.models.character import Character
from app.models.enums import Visibility
from app.models.event import Event
from app.models.location import Location
from app.models.progression import CharacterGrant, CharacterSpell, ResourceState
from app.models.user import User
from app.rules_content import get_registry
from app.services.campaigns.inventory_service import InventoryService
from app.tabletop.effects import ConcentrationService
from app.tabletop.rules.core import ABILITIES, SKILL_TO_ABILITY, ability_modifier, \
    proficiency_bonus_for_level
from app.tabletop.rules.derive import (
    initiative_bonus,
    passive_perception,
    save_bonus,
    skill_bonus,
    spellcasting_block,
)

_RECHARGE_TH = {
    "short_rest": "พักสั้น",
    "long_rest": "พักยาว",
    "long_rest_cycle_after_short_rest": "พักยาว (คืนบางส่วนหลังพักสั้น)",
}

_SOURCE_TH = {
    "CLASS": "คลาส", "CLASS_LEVEL": "เลเวลคลาส", "SUBCLASS": "ซับคลาส",
    "SPECIES": "เผ่า", "BACKGROUND": "ภูมิหลัง", "FEAT": "ความสามารถพิเศษ",
    "ITEM": "ไอเทม", "CAMPAIGN_HOMEBREW": "แคมเปญ", "TEMPORARY_EFFECT": "ผลชั่วคราว",
}


def _breakdown(parts: list[tuple[str, int]]) -> list[dict]:
    return [{"label": label, "value": value} for label, value in parts]


async def _resources(session: AsyncSession, character_id: str) -> list[dict]:
    reg = get_registry()
    rows = list((await session.execute(
        select(ResourceState).where(ResourceState.character_id == character_id)
    )).scalars())
    out = []
    for r in rows:
        rd = reg.resources.get(r.resource_id)
        out.append({
            "resource_id": r.resource_id,
            "name": rd.name if rd else r.resource_id,
            "name_th": rd.name_th if rd else r.resource_id,
            "current": r.current,
            "max": r.max_value,
            "recharge": rd.recharge if rd else "",
            "recharge_th": _RECHARGE_TH.get(rd.recharge, "") if rd else "",
        })
    return sorted(out, key=lambda x: x["resource_id"])


async def build_overview(session: AsyncSession, *, character: Character,
                         campaign: Campaign) -> dict:
    reg = get_registry()
    cls = reg.classes.get(character.char_class)
    sp = reg.species.get(character.species)
    bg = reg.backgrounds.get(character.background) if character.background else None
    location_name = None
    if character.location_id:
        loc = await session.get(Location, character.location_id)
        location_name = loc.name if loc else None

    conc = await ConcentrationService(session).current(character.id)
    sc = spellcasting_block(character)
    hooks = character.hooks or {}
    ds = character.death_saves or {}

    return {
        "character_id": character.id,
        "name": character.name,
        "level": character.level,
        "char_class": character.char_class,
        "class_name_th": cls.name_th if cls else character.char_class,
        "planned_subclass": character.planned_subclass,
        "species": character.species,
        "species_name_th": sp.name_th if sp else character.species,
        "background": character.background,
        "background_name_th": bg.name_th if bg else character.background,
        "concept": hooks.get("concept") or "",
        "location_name": location_name,
        "hp": character.hp,
        "max_hp": character.max_hp,
        "temp_hp": character.temp_hp,
        "ac": character.ac,
        "initiative": initiative_bonus(character),
        "speed": character.speed,
        "proficiency_bonus": proficiency_bonus_for_level(character.level),
        "hit_die": character.hit_die,
        "hit_dice_remaining": character.hit_dice_remaining,
        "conditions": list(character.conditions or []),
        "exhaustion": character.exhaustion,
        "dying": character.dying,
        "stable": character.stable,
        "dead": character.dead,
        "death_saves": {"successes": ds.get("successes", 0), "failures": ds.get("failures", 0)},
        "concentration": ({"name": conc.name, "spell_key": conc.spell_key}
                          if conc is not None else None),
        "resources": await _resources(session, character.id),
        "spellcasting": ({"save_dc": sc["save_dc"], "attack_bonus": sc["attack_bonus"],
                          "ability": sc["ability"]} if sc else None),
        "game_time": campaign.current_game_time,
        "game_time_th": format_game_time(campaign.current_game_time),
    }


def build_abilities_and_skills(session: AsyncSession, *, character: Character) -> dict:
    """All six abilities + saves + all 18 skills with real engine breakdowns."""
    reg = get_registry()
    grants_note = None  # provenance for skills comes from CharacterGrant (below callers)
    abilities = []
    for a in ABILITIES:
        score = character.ability_score(a)
        sv = save_bonus(character, a)
        abilities.append({
            "key": a,
            "score": score,
            "modifier": ability_modifier(score),
            "save_total": sv.total,
            "save_breakdown": _breakdown(sv.parts),
            "save_proficient": a in (character.save_proficiencies or []),
        })
    skills = []
    for key, d in sorted(reg.skills.items()):
        b = skill_bonus(character, key)
        prof = ("EXPERTISE" if key in (character.expertise or [])
                else "PROFICIENT" if key in (character.proficiencies or [])
                else "NONE")
        skills.append({
            "key": key,
            "name": d.name,
            "name_th": d.name_th,
            "ability": SKILL_TO_ABILITY[key],
            "total": b.total,
            "proficiency": prof,
            "breakdown": _breakdown(b.parts),
            "passive": 10 + b.total,
            "explain_th": d.explain_th,
        })
    return {
        "abilities": abilities,
        "skills": skills,
        "passive_perception": passive_perception(character),
        "proficiency_bonus": proficiency_bonus_for_level(character.level),
        "note": grants_note,
    }


async def build_spellbook(session: AsyncSession, *, character: Character) -> dict:
    reg = get_registry()
    sc = spellcasting_block(character)
    if sc is None:
        return {"is_caster": False, "spells": [], "slots": [], "concentration": None}
    rows = list((await session.execute(
        select(CharacterSpell).where(CharacterSpell.character_id == character.id)
    )).scalars())
    spells = []
    for r in rows:
        d = reg.spells.get(r.spell_key)
        spells.append({
            "key": r.spell_key,
            "kind": r.kind,                     # cantrip | book | known
            "prepared": bool(r.prepared) or r.kind == "cantrip",
            "name": d.name if d else r.spell_key,
            "name_th": d.name_th_hint if d else r.spell_key,
            "level": d.level if d else 0,
            "school": d.school if d else "",
            "casting_time": d.casting_time if d else "",
            "range": d.range if d else "",
            "duration": d.duration if d else "",
            "concentration": d.concentration if d else False,
            "ritual": d.ritual if d else False,
            "summary_th": d.mech_summary_th if d else "",
            "category": d.ux_category if d else "",
            "source": "CLASS",
        })
    slots = []
    from app.tabletop.resources import ResourceEngine

    slot_state = await ResourceEngine(session).get(character.id, "resource:spell_slots_1")
    if slot_state is not None:
        slots.append({"level": 1, "current": slot_state.current, "max": slot_state.max_value})
    conc = await ConcentrationService(session).current(character.id)
    return {
        "is_caster": True,
        "ability": sc["ability"],
        "save_dc": sc["save_dc"],
        "attack_bonus": sc["attack_bonus"],
        "prepared_count": sc["prepared_count"],
        "slots": slots,
        "spells": sorted(spells, key=lambda s: (s["level"], s["name"])),
        "concentration": ({"name": conc.name, "spell_key": conc.spell_key}
                          if conc is not None else None),
        # No safe domain service exists for re-preparation outside the rest flow —
        # the view is explicitly read-only (no fake controls).
        "preparation_editable": False,
    }


async def build_features(session: AsyncSession, *, character: Character) -> dict:
    reg = get_registry()
    grants = list((await session.execute(
        select(CharacterGrant).where(CharacterGrant.character_id == character.id)
    )).scalars())
    resource_state = {r["resource_id"]: r for r in await _resources(session, character.id)}

    groups: dict[str, list[dict]] = {}
    for g in grants:
        if g.grant_type == "skill":
            continue  # skills are surfaced in the skills view with provenance
        note = (g.data or {}).get("note", "")
        executable = not note  # grants recorded-but-not-executable carry a note
        entry = {
            "key": g.key,
            "grant_type": g.grant_type,
            "name_th": g.name_th or g.key,
            "source_type": g.source_type,
            "source_th": _SOURCE_TH.get(g.source_type, g.source_type),
            "source_key": g.source_key,
            "executable": executable,
            "note_th": note,
            "data": {k: v for k, v in (g.data or {}).items() if k != "note"},
            "resource": None,
        }
        # Attach live resource state when the class feature has a pool.
        cls = reg.classes.get(character.char_class)
        if cls is not None:
            for feat in cls.features:
                if feat.key == g.key and feat.resource_id:
                    entry["resource"] = resource_state.get(feat.resource_id)
        groups.setdefault(g.source_type, []).append(entry)

    order = ["CLASS", "SUBCLASS", "SPECIES", "BACKGROUND", "FEAT", "ITEM",
             "CAMPAIGN_HOMEBREW", "TEMPORARY_EFFECT"]
    return {
        "groups": [
            {"source_type": st, "source_th": _SOURCE_TH.get(st, st), "entries": groups[st]}
            for st in order if st in groups
        ],
    }


async def build_inventory(session: AsyncSession, *, character: Character) -> dict:
    rows = await InventoryService(session).list_inventory(character.id)
    items = [{
        "id": entry.id,
        "name": item.name,
        "kind": item.kind,
        "quantity": entry.quantity,
        "equipped": entry.equipped,
        "description": item.description,
    } for entry, item in rows]
    return {"items": items, "count": len(items)}


async def build_story(session: AsyncSession, *, character: Character,
                      campaign: Campaign) -> dict:
    """The player's own narrative material + their PLAYER_ONLY discoveries."""
    hooks = character.hooks or {}
    me = entity_ref("character", character.id)
    # PLAYER_ONLY events witnessed by THIS character only (e.g. private reveals).
    rows = list((await session.execute(
        select(Event).where(
            Event.campaign_id == campaign.id,
            Event.visibility == Visibility.PLAYER_ONLY.value,
        ).order_by(Event.seq.asc())
    )).scalars())
    discoveries = [
        {"seq": e.seq, "summary": (e.payload or {}).get("summary", ""),
         "game_time_th": format_game_time(e.campaign_time)}
        for e in rows if me in (e.witnesses or [])
    ]
    return {
        "name": character.name,
        "concept": hooks.get("concept") or "",
        "origin": hooks.get("origin") or "",
        "desire": hooks.get("desire") or "",
        "fear": hooks.get("fear") or "",
        "flaw": hooks.get("flaw") or "",
        "connection": hooks.get("connection") or "",
        "appearance": character.appearance or hooks.get("appearance") or "",
        "brief": campaign.brief or "",
        "central_question": campaign.central_question or "",
        "discoveries": discoveries,
    }


async def build_party(session: AsyncSession, *, campaign: Campaign,
                      viewer_member: CampaignMember) -> dict:
    members = list((await session.execute(
        select(CampaignMember).where(CampaignMember.campaign_id == campaign.id)
    )).scalars())
    out = []
    for m in members:
        if not m.active_character_id:
            continue
        char = await session.get(Character, m.active_character_id)
        if char is None:
            continue
        user = await session.get(User, m.user_id)
        location_name = None
        if char.location_id:
            loc = await session.get(Location, char.location_id)
            location_name = loc.name if loc else None
        observable = list(char.conditions or [])
        if char.dead:
            observable.append("เสียชีวิต")
        elif char.hp <= 0:
            observable.append("หมดสติ")
        elif char.hp <= char.max_hp // 3:
            observable.append("บาดเจ็บหนัก")
        out.append({
            "character_id": char.id,
            "name": char.name,
            "player_name": user.display_name if user else "",
            "char_class": char.char_class,
            "level": char.level,
            "species": char.species,
            "is_you": m.id == viewer_member.id,
            "observable": observable,
            "location_name": location_name,
            # Exact HP of OTHER characters is not exposed — only the viewer's own.
            **({"hp": char.hp, "max_hp": char.max_hp} if m.id == viewer_member.id else {}),
        })
    return {"members": out}


_EVENT_TH = {
    "SESSION_STARTED": "เริ่มเซสชัน", "SESSION_ENDED": "จบเซสชัน",
    "SCENE_STARTED": "ฉากใหม่", "PLAYER_ACTION_COMMITTED": "การกระทำ",
    "ABILITY_CHECK_RESOLVED": "ทอยเช็ค", "ATTACK_RESOLVED": "การโจมตี",
    "DAMAGE_APPLIED": "ความเสียหาย", "ITEM_GAINED": "ได้รับไอเทม",
    "ITEM_LOST": "เสียไอเทม", "CHARACTER_MOVED": "การเดินทาง",
    "KNOWLEDGE_GAINED": "ได้รู้บางอย่าง", "WORLD_TIME_ADVANCED": "เวลาผ่านไป",
    "COMBAT_STARTED": "เริ่มการต่อสู้", "COMBAT_ENDED": "จบการต่อสู้",
}


async def build_chronicle(session: AsyncSession, *, campaign: Campaign,
                          character_id: str, limit: int = 40,
                          before_seq: int | None = None) -> dict:
    """Player-safe campaign timeline: PUBLIC + PARTY + this player's PLAYER_ONLY.

    The visibility filter is in the SQL — DM_ONLY rows are never fetched, so
    nothing exists for the client to 'hide'.
    """
    me = entity_ref("character", character_id)
    stmt = select(Event).where(
        Event.campaign_id == campaign.id,
        Event.visibility.in_([Visibility.PUBLIC.value, Visibility.PARTY.value,
                              Visibility.PLAYER_ONLY.value]),
    )
    if before_seq is not None:
        stmt = stmt.where(Event.seq < before_seq)
    rows = list((await session.execute(
        stmt.order_by(Event.seq.desc()).limit(limit * 2)
    )).scalars())
    entries = []
    for e in rows:
        if e.visibility == Visibility.PLAYER_ONLY.value and me not in (e.witnesses or []):
            continue  # someone ELSE's private event — excluded server-side
        summary = (e.payload or {}).get("summary", "")
        if not summary:
            continue
        entries.append({
            "seq": e.seq,
            "event_type": e.event_type,
            "event_type_th": _EVENT_TH.get(e.event_type, e.event_type),
            "summary": summary,
            "session_id": e.session_id,
            "game_time": e.campaign_time,
            "game_time_th": format_game_time(e.campaign_time),
            "private": e.visibility == Visibility.PLAYER_ONLY.value,
        })
        if len(entries) >= limit:
            break
    entries.reverse()
    return {
        "entries": entries,
        "oldest_seq": entries[0]["seq"] if entries else None,
        "has_more": len(entries) >= limit,
    }
