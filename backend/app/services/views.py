"""Player-facing views: character sheet, inventory, journal, party status.

Read-only builders that return kinded OutboundMessage structures. The journal is a
DERIVED view over player-visible events (retrieval-enforced) — no separate table,
no chance of leaking DM-only entries.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.discord_bridge.dto import OutboundMessage
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.models.enums import Visibility
from app.presentation import MessageKind
from app.services.campaigns.inventory_service import InventoryService
from app.services.events import EventService
from app.tabletop.rules import ability_modifier

_ABILITY_TH = {"str": "พลัง", "dex": "คล่องแคล่ว", "con": "อึด",
               "int": "ปัญญา", "wis": "สังเกตการณ์", "cha": "เสน่ห์"}


def _mod(n: int) -> str:
    m = ability_modifier(n)
    return f"+{m}" if m >= 0 else str(m)


def build_belief_fields(
    character: Character, *, owner_view: bool
) -> list[dict]:
    """Privacy-safe identity fields; secret/private faith is absent for others."""
    from app.rules_content.faith_registry import get_faith_registry
    from app.services.beliefs import BeliefService

    profile = BeliefService.visible_profile(
        character.belief_profile, owner_view=owner_view
    )
    fields: list[dict] = []
    if profile is not None:
        registry = get_faith_registry()
        keys = [profile.primary_deity_key, *profile.secondary_deity_keys]
        names = [
            registry.get_deity(key).name_th
            for key in keys if key and registry.get_deity(key)
        ]
        bits = [profile.stance.value, profile.devotion.value]
        if names:
            bits.append(", ".join(names))
        if owner_view:
            bits.append(profile.visibility.value)
            if profile.personal_reason:
                bits.append(profile.personal_reason)
        fields.append({
            "name": "ความเชื่อส่วนตัว",
            "value": " · ".join(bits),
            "inline": False,
        })
        if owner_view and profile.owner_notes:
            fields.append({
                "name": "บันทึกศาสนา (เจ้าของเท่านั้น)",
                "value": profile.owner_notes,
                "inline": False,
            })
    if owner_view and character.cleric_deity_key:
        deity = get_faith_registry().get_deity(character.cleric_deity_key)
        fields.append({
            "name": "กลไก Cleric",
            "value": f"{deity.name_th if deity else character.cleric_deity_key} · "
                     f"Domain: {character.cleric_domain}",
            "inline": False,
        })
    return fields


async def build_character_sheet(
    session: AsyncSession, *, character: Character, channel_id: str
) -> OutboundMessage:
    from app.rules_content import get_registry
    from app.tabletop.resources import ResourceEngine
    from app.tabletop.rules.derive import (
        initiative_bonus,
        passive_perception,
        save_bonus,
        skill_bonus,
        spellcasting_block,
    )

    reg = get_registry()
    hooks = character.hooks or {}
    abilities = "  ".join(
        f"{_ABILITY_TH[a].split()[0] if False else a.upper()} {character.ability_score(a)} "
        f"({_mod(character.ability_score(a))})"
        for a in ("str", "dex", "con", "int", "wis", "cha")
    )
    saves = "  ".join(
        f"{a.upper()} {save_bonus(character, a).total:+d}"
        + ("●" if a in (character.save_proficiencies or []) else "")
        for a in ("str", "dex", "con", "int", "wis", "cha")
    )
    lines = []
    if hooks.get("concept"):
        lines.append(f"*{hooks['concept']}*")
    if character.appearance:
        lines.append(character.appearance)

    hp_line = f"{character.hp}/{character.max_hp}"
    if character.temp_hp:
        hp_line += f" (+{character.temp_hp} ชั่วคราว)"
    fields = [
        {"name": "❤️ HP", "value": hp_line, "inline": True},
        {"name": "🛡️ AC", "value": str(character.ac), "inline": True},
        {"name": "⚡ Initiative", "value": f"{initiative_bonus(character):+d}", "inline": True},
        {"name": "⭐ เลเวล", "value": f"{character.level} ({character.char_class})", "inline": True},
        {"name": "🥾 ความเร็ว", "value": f"{character.speed} ฟุต", "inline": True},
        {"name": "🎲 Hit Dice", "value": f"{character.hit_dice_remaining}/{character.level} (d{character.hit_die})",
         "inline": True},
        {"name": "ความสามารถ", "value": abilities, "inline": False},
        {"name": "เซฟวิ่งโธรว์ (● = ถนัด)", "value": saves, "inline": False},
        {"name": "👁️ Passive Perception", "value": str(passive_perception(character)),
         "inline": True},
    ]
    if character.dying or character.stable:
        ds = character.death_saves or {}
        fields.append({"name": "💀 เฮือกสุดท้าย",
                       "value": ("ทรงตัวแล้ว" if character.stable else
                                 f"สำเร็จ {ds.get('successes', 0)}/3 · พลาด {ds.get('failures', 0)}/3"),
                       "inline": True})
    # Skills: proficient/expertise first with derived bonuses.
    skill_bits = []
    for sk in (character.proficiencies or []):
        b = skill_bonus(character, sk)
        star = "★" if sk in (character.expertise or []) else "●"
        th = reg.skills[sk].name_th if sk in reg.skills else sk
        skill_bits.append(f"{star} {th} {b.total:+d}")
    fields.append({"name": "ทักษะถนัด (★ = เชี่ยวชาญ)",
                   "value": "  ".join(skill_bits) or "—", "inline": False})

    sc = spellcasting_block(character)
    if sc is not None:
        slot_txt = ""
        slots = await ResourceEngine(session).get(character.id, "resource:spell_slots_1")
        if slots is not None:
            slot_txt = f" · ช่องเวท Lv.1 {slots.current}/{slots.max_value}"
        fields.append({"name": "✨ การร่ายเวท",
                       "value": f"Save DC {sc['save_dc']} · โจมตีเวท +{sc['attack_bonus']}{slot_txt}",
                       "inline": False})
    hook_bits = [hooks[k] for k in ("desire", "fear", "flaw") if hooks.get(k)]
    if hook_bits:
        fields.append({"name": "ตัวตน", "value": "\n".join(f"• {h}" for h in hook_bits),
                       "inline": False})
    if character.conditions:
        fields.append({"name": "สภาวะ", "value": ", ".join(character.conditions), "inline": False})
    if character.exhaustion:
        fields.append({"name": "ความอ่อนล้า", "value": f"ระดับ {character.exhaustion}", "inline": True})
    from app.services.beliefs import BeliefService

    await BeliefService(session).get_character_belief(character)
    fields.extend(build_belief_fields(character, owner_view=True))
    return OutboundMessage(
        channel_id, "\n".join(lines), kind=MessageKind.CHARACTER_SHEET,
        title=f"{character.name} — {character.species} · {character.char_class}"
              + (f" · {character.background}" if character.background else ""),
        data={"fields": fields, "footer": "!rv skill <ชื่อ> — ดูที่มาของตัวเลข · !rv spells — คาถา"},
    )


async def build_spells_view(
    session: AsyncSession, *, character: Character, channel_id: str
) -> OutboundMessage:
    from sqlalchemy import select

    from app.models.progression import CharacterSpell
    from app.rules_content import get_registry
    from app.tabletop.effects import ConcentrationService
    from app.tabletop.resources import ResourceEngine
    from app.tabletop.rules.derive import spellcasting_block

    reg = get_registry()
    sc = spellcasting_block(character)
    if sc is None:
        return OutboundMessage(channel_id, f"{character.name} ไม่ใช่ผู้ใช้เวท",
                               kind=MessageKind.TABLE_NOTICE)
    rows = list((await session.execute(
        select(CharacterSpell).where(CharacterSpell.character_id == character.id)
    )).scalars())

    def _fmt(spell_key: str, prepared: bool | None = None) -> str:
        sp = reg.spells.get(spell_key)
        if sp is None:
            return spell_key
        mark = " ✦" if prepared else ""
        conc = " · เพ่งสมาธิ" if sp.concentration else ""
        return f"**{sp.name_th_hint}** ({sp.name}){mark} — {sp.mech_summary_th}{conc}"

    cantrips = [_fmt(r.spell_key) for r in rows if r.kind == "cantrip"]
    book = [(r.spell_key, r.prepared) for r in rows if r.kind in ("book", "known")]
    fields = []
    slots = await ResourceEngine(session).get(character.id, "resource:spell_slots_1")
    slot_line = f"ช่องเวท Lv.1: {'◆' * slots.current}{'◇' * (slots.max_value - slots.current)}" \
        if slots else ""
    header = f"Save DC {sc['save_dc']} · โจมตีเวท +{sc['attack_bonus']} · ใช้ {sc['ability'].upper()}"
    if slot_line:
        header += f"\n{slot_line}"
    conc_effect = await ConcentrationService(session).current(character.id)
    if conc_effect is not None:
        header += f"\n\n🧠 **กำลังเพ่งสมาธิ: {conc_effect.name}**"
    if cantrips:
        fields.append({"name": "คาถาประจำตัว (ร่ายได้ไม่จำกัด)",
                       "value": "\n".join(cantrips), "inline": False})
    if book:
        fields.append({"name": "ตำรา/คาถาที่รู้ (✦ = เตรียมไว้แล้ว)",
                       "value": "\n".join(_fmt(k, p) for k, p in book), "inline": False})
    return OutboundMessage(
        channel_id, header, kind=MessageKind.CHARACTER_SHEET,
        title=f"✨ คาถาของ {character.name}",
        data={"fields": fields, "footer": "เปลี่ยนคาถาที่เตรียมไว้ได้หลังพักยาว"},
    )


async def build_skill_explain(
    session: AsyncSession, *, character: Character, skill: str, channel_id: str
) -> OutboundMessage:
    """Answers 'ทำไม Arcana +5?' with the real composition from the derivation engine."""
    from app.rules_content import get_registry
    from app.tabletop.rules.derive import skill_bonus

    reg = get_registry()
    s = skill.strip().lower().replace(" ", "_")
    if s not in reg.skills:
        return OutboundMessage(
            channel_id, f"ไม่รู้จักทักษะ `{skill}` — ลอง: " + ", ".join(sorted(reg.skills)),
            kind=MessageKind.TABLE_NOTICE)
    d = reg.skills[s]
    b = skill_bonus(character, s)
    parts = "\n".join(f"• {label} {v:+d}" for label, v in b.parts)
    body = (f"{d.explain_th}\n\n**{character.name}: {b.total:+d}**\n{parts}")
    return OutboundMessage(
        channel_id, body, kind=MessageKind.CHARACTER_SHEET,
        title=f"{d.name_th} ({d.name}) — ทำไมถึง {b.total:+d}?",
    )


async def build_inventory_view(
    session: AsyncSession, *, character: Character, channel_id: str
) -> OutboundMessage:
    rows = await InventoryService(session).list_inventory(character.id)
    if not rows:
        body = "*ย่ามว่างเปล่า — โลกยังไม่ได้มอบอะไรให้*"
    else:
        lines = []
        for entry, item in rows:
            qty = f" x{entry.quantity}" if entry.quantity > 1 else ""
            eq = " (สวมใส่อยู่)" if entry.equipped else ""
            lines.append(f"**{item.name}**{qty}{eq}\n-# {item.description}" if item.description
                         else f"**{item.name}**{qty}{eq}")
        body = "\n".join(lines)
    return OutboundMessage(
        channel_id, body, kind=MessageKind.INVENTORY,
        title=f"ย่ามของ {character.name}",
        data={"count": len(rows)},
    )


async def build_journal_view(
    session: AsyncSession, *, campaign_id: str, channel_id: str, limit: int = 12
) -> OutboundMessage:
    """Derived from PLAYER-VISIBLE events only — structurally leak-proof."""
    events = await EventService(session).list_visible_events(
        campaign_id=campaign_id,
        allowed_visibilities=[Visibility.PUBLIC, Visibility.PARTY],
    )
    entries = [e.payload.get("summary") for e in events
               if isinstance(e.payload, dict) and e.payload.get("summary")]
    recent = entries[-limit:]
    body = "\n".join(f"• {s}" for s in recent) if recent else "*บันทึกยังว่าง เรื่องราวเพิ่งเริ่มต้น*"
    return OutboundMessage(
        channel_id, body, kind=MessageKind.JOURNAL, title="บันทึกการเดินทาง",
        data={"entry_count": len(recent)},
    )


async def build_party_view(
    session: AsyncSession, *, members: list[CampaignMember], channel_id: str,
    get_character,
) -> OutboundMessage:
    fields = []
    for m in members:
        char = await get_character(m)
        if char is None:
            continue
        hp_note = "" if char.hp == char.max_hp else "  ⚠️" if char.hp <= char.max_hp // 3 else ""
        fields.append({
            "name": char.name,
            "value": f"{char.char_class} lvl {char.level} — HP {char.hp}/{char.max_hp}{hp_note}"
                     + (f"\nสภาวะ: {', '.join(char.conditions)}" if char.conditions else ""),
            "inline": True,
        })
    return OutboundMessage(
        channel_id, "", kind=MessageKind.PARTY_STATUS, title="สถานะปาร์ตี้",
        data={"fields": fields or [{"name": "—", "value": "ยังไม่มีตัวละครในปาร์ตี้"}]},
    )
