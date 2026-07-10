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


async def build_character_sheet(
    session: AsyncSession, *, character: Character, channel_id: str
) -> OutboundMessage:
    hooks = character.hooks or {}
    abilities = "  ".join(
        f"{_ABILITY_TH[a]} {character.ability_score(a)} ({_mod(character.ability_score(a))})"
        for a in ("str", "dex", "con", "int", "wis", "cha")
    )
    lines = []
    if hooks.get("concept"):
        lines.append(f"*{hooks['concept']}*")
    if character.appearance:
        lines.append(character.appearance)
    fields = [
        {"name": "❤️ HP", "value": f"{character.hp}/{character.max_hp}", "inline": True},
        {"name": "🛡️ AC", "value": str(character.ac), "inline": True},
        {"name": "⭐ เลเวล", "value": f"{character.level} ({character.char_class})", "inline": True},
        {"name": "ความสามารถ", "value": abilities, "inline": False},
        {"name": "ทักษะถนัด", "value": ", ".join(character.proficiencies) or "—", "inline": False},
    ]
    hook_bits = [hooks[k] for k in ("desire", "fear", "flaw") if hooks.get(k)]
    if hook_bits:
        fields.append({"name": "ตัวตน", "value": "\n".join(f"• {h}" for h in hook_bits),
                       "inline": False})
    if character.conditions:
        fields.append({"name": "สภาวะ", "value": ", ".join(character.conditions), "inline": False})
    return OutboundMessage(
        channel_id, "\n".join(lines), kind=MessageKind.CHARACTER_SHEET,
        title=character.name, data={"fields": fields},
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
