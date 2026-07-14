"""The presentation vocabulary (§overhaul). Every player-facing message has a kind.

`KIND_STYLE` is a *hint* table (emoji + accent color) the adapter may use; it keeps
the visual language consistent without leaking Discord types into the engine.
"""
from __future__ import annotations

from app.models.enums import StrEnum


class MessageKind(StrEnum):
    REVERIE_WELCOME = "REVERIE_WELCOME"
    CHARACTER_CREATION = "CHARACTER_CREATION"
    CHARACTER_REVEAL = "CHARACTER_REVEAL"
    SESSION_TITLE = "SESSION_TITLE"
    CAMPAIGN_PROLOGUE = "CAMPAIGN_PROLOGUE"  # the cinematic Session-1 world-scale opening
    PLAYER_SAFE_RECAP = "PLAYER_SAFE_RECAP"
    SCENE_FRAME = "SCENE_FRAME"
    NPC_DIALOGUE = "NPC_DIALOGUE"
    CHECK_PROMPT = "CHECK_PROMPT"          # the dice ritual: [🎲 ทอย d20]
    CHECK_RESOLUTION = "CHECK_RESOLUTION"
    ATTACK_RESOLUTION = "ATTACK_RESOLUTION"
    DAMAGE = "DAMAGE"
    PRIVATE_SECRET = "PRIVATE_SECRET"
    ITEM_GAINED = "ITEM_GAINED"
    CONDITION_CHANGED = "CONDITION_CHANGED"
    COMBAT_TURN = "COMBAT_TURN"
    SCENE_TRANSITION = "SCENE_TRANSITION"
    SESSION_END = "SESSION_END"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    CHARACTER_SHEET = "CHARACTER_SHEET"
    INVENTORY = "INVENTORY"
    JOURNAL = "JOURNAL"
    PARTY_STATUS = "PARTY_STATUS"
    TABLE_NOTICE = "TABLE_NOTICE"  # neutral admin/system notices (join, status, help)


# (emoji, accent color as 0xRRGGBB). Adapter hint only — engine logic never reads this.
KIND_STYLE: dict[MessageKind, tuple[str, int]] = {
    MessageKind.REVERIE_WELCOME: ("🕯️", 0x8B6FB8),
    MessageKind.CHARACTER_CREATION: ("✒️", 0x6FA8DC),
    MessageKind.CHARACTER_REVEAL: ("🎭", 0xB8860B),
    MessageKind.SESSION_TITLE: ("🎲", 0x8B6FB8),
    MessageKind.CAMPAIGN_PROLOGUE: ("🎬", 0x8B6FB8),
    MessageKind.PLAYER_SAFE_RECAP: ("📖", 0x7A9E7E),
    MessageKind.SCENE_FRAME: ("🌄", 0x5B7C99),
    MessageKind.NPC_DIALOGUE: ("💬", 0xC0A16B),
    MessageKind.CHECK_PROMPT: ("🎲", 0xB8860B),
    MessageKind.CHECK_RESOLUTION: ("🎲", 0x5B7C99),
    MessageKind.ATTACK_RESOLUTION: ("⚔️", 0xA85751),
    MessageKind.DAMAGE: ("💥", 0xA85751),
    MessageKind.PRIVATE_SECRET: ("🤫", 0x4B3869),
    MessageKind.ITEM_GAINED: ("🎒", 0x7A9E7E),
    MessageKind.CONDITION_CHANGED: ("🩸", 0xA85751),
    MessageKind.COMBAT_TURN: ("⏳", 0xA85751),
    MessageKind.SCENE_TRANSITION: ("🌒", 0x5B7C99),
    MessageKind.SESSION_END: ("🏮", 0x8B6FB8),
    MessageKind.TECHNICAL_ERROR: ("🛠️", 0x777777),
    MessageKind.CHARACTER_SHEET: ("📜", 0x6FA8DC),
    MessageKind.INVENTORY: ("🎒", 0x7A9E7E),
    MessageKind.JOURNAL: ("📔", 0x7A9E7E),
    MessageKind.PARTY_STATUS: ("🛡️", 0x6FA8DC),
    MessageKind.TABLE_NOTICE: ("🪑", 0x999999),
}
