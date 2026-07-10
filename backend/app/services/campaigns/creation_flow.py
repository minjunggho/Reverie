"""Guided character creation — a short conversation, not a form.

Flow: `!rv character` (no args) opens a draft → the player's plain Thai messages
route here (bridge checks for an active draft) → the CreationGuidance AI extracts
hook fields and asks ONE question per turn → when ready, a confirm step → the
engine creates the Character from the class PRESET (AI never emits stats), grants
starting gear, stores hooks, and posts a CHARACTER_REVEAL.

Bounded: after MAX_STEPS the flow reveals with the best data it has rather than
interrogating forever. Cancel anytime with 'ยกเลิก'.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.ai.llm.base import LLMMessage, LLMProvider
from app.ai.prompts.system_prompts import CREATION_GUIDE_SYSTEM
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.character_draft import CharacterDraft
from app.presentation import MessageKind
from app.schemas.llm_io import CreationGuidance
from app.services.campaigns.campaign_service import CampaignService
from app.services.campaigns.character_service import CharacterService
from app.services.campaigns.inventory_service import InventoryService
from app.services.campaigns.presets import CLASS_PRESETS, CLASS_TH, infer_class_from_concept
from app.tabletop.rules import SUPPORTED_CLASSES

log = get_logger(__name__)

MAX_STEPS = 6
HOOK_KEYS = ("concept", "origin", "desire", "fear", "flaw", "connection", "appearance", "name")
CONFIRM_YES = "✅ ใช่ นี่แหละตัวข้า"
CONFIRM_EDIT = "✏️ ขอปรับอีกนิด"
_CANCEL_WORDS = ("ยกเลิก", "cancel", "เลิก")

OPENING_QUESTION = (
    "มาสร้างตัวละครกัน — ไม่ต้องคิดเป็นศัพท์เกม\n"
    "เล่าให้ฟังหน่อยว่าอยากเล่นเป็นคนแบบไหน?\n"
    "-# เช่น \"ผู้หญิงที่โตมากับโจร ไม่ค่อยพูด ใช้มีด แล้วชอบโกหกคน\""
)


class CreationFlowService:
    def __init__(self, db, provider: LLMProvider) -> None:
        self.db = db
        self.provider = provider

    # --- queries ---------------------------------------------------------------
    async def active_draft(self, session, member_id: str) -> CharacterDraft | None:
        return (
            await session.execute(
                select(CharacterDraft).where(
                    CharacterDraft.member_id == member_id,
                    CharacterDraft.status == "ACTIVE",
                )
            )
        ).scalars().first()

    # --- entry points ------------------------------------------------------------
    async def start(self, *, campaign_id: str, member_id: str, channel_id: str) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            existing = await self.active_draft(s, member_id)
            if existing is None:
                s.add(CharacterDraft(campaign_id=campaign_id, member_id=member_id))
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, OPENING_QUESTION, kind=MessageKind.CHARACTER_CREATION,
            title="สร้างตัวละคร", data={"footer": "พิมพ์ 'ยกเลิก' ได้ทุกเมื่อ"},
        )])

    async def handle_message(
        self, *, member_id: str, channel_id: str, text: str
    ) -> BridgeResult:
        text = (text or "").strip()
        async with self.db.session() as s:
            draft = await self.active_draft(s, member_id)
        if draft is None:  # raced away; treat as unhandled notice
            return BridgeResult(handled=True, note="no active draft")

        if any(w in text.lower() for w in _CANCEL_WORDS):
            return await self._cancel(draft, channel_id)

        data = dict(draft.data or {})

        # Confirm step: the reveal proposal is on the table.
        if data.get("_awaiting_confirm"):
            if text.startswith("✏") or CONFIRM_EDIT in text:
                data["_awaiting_confirm"] = False
                await self._save(draft, data, step_inc=0)
                return self._ask(channel_id, "อยากปรับตรงไหน บอกมาได้เลย —")
            if text.startswith("✅") or any(w in text for w in ("ใช่", "โอเค", "สร้างเลย", "เอาเลย", "ตามนั้น")):
                return await self._reveal(draft, data, channel_id)
            # Anything else = an adjustment in prose; fall through to the guide.
            data["_awaiting_confirm"] = False

        # Ask the guide to extract fields + pose the next question.
        guidance = await self._guide(draft, data, text)
        for key, value in (guidance.updated_fields or {}).items():
            if key in HOOK_KEYS and isinstance(value, str) and value.strip():
                data[key] = value.strip()
        if guidance.proposed_class:
            cls = guidance.proposed_class.strip().lower()
            data["class"] = cls if cls in SUPPORTED_CLASSES else infer_class_from_concept(
                data.get("concept", "") + " " + text
            )

        step = draft.step + 1
        forced = step >= MAX_STEPS
        ready = guidance.ready_to_reveal or forced
        if ready and self._complete_enough(data, force=forced):
            data["_awaiting_confirm"] = True
            data["_summary"] = guidance.reveal_summary or data.get("concept", "")
            await self._save(draft, data)
            return self._confirm_proposal(channel_id, data)

        await self._save(draft, data)
        question = guidance.next_question or "เล่าเพิ่มอีกนิดได้ไหม?"
        return self._ask(channel_id, question)

    # --- internals ---------------------------------------------------------------
    async def _guide(self, draft: CharacterDraft, data: dict, text: str) -> CreationGuidance:
        known = "; ".join(f"{k}={v}" for k, v in data.items()
                          if k in HOOK_KEYS or k == "class") or "-"
        messages: list[LLMMessage] = [
            {"role": "system", "content": CREATION_GUIDE_SYSTEM},
            {"role": "user", "content": f"DRAFT_STEP: {draft.step + 1}\nKNOWN: {known}\nMESSAGE: {text}"},
        ]
        try:
            return await self.provider.guide_character_creation(messages)
        except LLMError as exc:
            log.warning("creation guide fallback: %s", exc)
            # Safe fallback: capture the raw text into the emptiest slot, keep going.
            slot = next((k for k in ("concept", "origin", "flaw") if k not in data), "concept")
            return CreationGuidance(
                updated_fields={slot: text[:300]},
                next_question="เล่าเพิ่มอีกหน่อย — เขาเป็นคนยังไง แล้วชื่ออะไรดี?",
            )

    @staticmethod
    def _complete_enough(data: dict, *, force: bool) -> bool:
        identity = sum(1 for k in ("origin", "desire", "fear", "flaw") if data.get(k))
        ok = bool(data.get("concept")) and bool(data.get("name")) and identity >= 2
        if force:
            data.setdefault("name", "นิรนาม")
            data.setdefault("concept", "นักผจญภัยปริศนา")
            return True
        return ok and bool(data.get("class"))

    async def _save(self, draft: CharacterDraft, data: dict, step_inc: int = 1) -> None:
        async with self.db.unit_of_work() as s:
            row = await s.get(CharacterDraft, draft.id)
            row.data = data
            row.step = row.step + step_inc

    def _ask(self, channel_id: str, question: str) -> BridgeResult:
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, question, kind=MessageKind.CHARACTER_CREATION, title="สร้างตัวละคร",
        )])

    def _confirm_proposal(self, channel_id: str, data: dict) -> BridgeResult:
        cls = data.get("class", "fighter")
        preset = CLASS_PRESETS[cls]
        body_lines = [data.get("_summary") or data.get("concept", "")]
        if data.get("appearance"):
            body_lines.append(f"\n{data['appearance']}")
        fields = [
            {"name": "สาย", "value": f"{CLASS_TH.get(cls, cls)} ({cls})", "inline": True},
            {"name": "HP / AC", "value": f"{preset['max_hp']} / {preset['ac']}", "inline": True},
            {"name": "ทักษะถนัด", "value": ", ".join(preset["proficiencies"]), "inline": False},
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, "\n".join(body_lines),
            kind=MessageKind.CHARACTER_CREATION, title=f"{data.get('name', '?')} — ใช่คนนี้ไหม?",
            data={"fields": fields}, choices=[CONFIRM_YES, CONFIRM_EDIT],
        )])

    async def _reveal(self, draft: CharacterDraft, data: dict, channel_id: str) -> BridgeResult:
        cls = data.get("class") or infer_class_from_concept(data.get("concept", ""))
        preset = CLASS_PRESETS[cls]
        hooks = {k: v for k, v in data.items() if k in HOOK_KEYS and k not in ("name",)}

        async with self.db.unit_of_work() as s:
            char = await CharacterService(s).create_character(
                member_id=draft.member_id, name=data.get("name", "นิรนาม"), char_class=cls,
                abilities=preset["abilities"], proficiencies=preset["proficiencies"],
                max_hp=preset["max_hp"], ac=preset["ac"], set_active=True,
            )
            char.hooks = hooks
            char.appearance = data.get("appearance", "")
            gear = await InventoryService(s).grant_starting_gear(character=char)
            row = await s.get(CharacterDraft, draft.id)
            row.status = "DONE"

        summary = data.get("_summary") or data.get("concept", "")
        fields = [
            {"name": "สาย", "value": f"{CLASS_TH.get(cls, cls)}", "inline": True},
            {"name": "HP / AC", "value": f"{preset['max_hp']} / {preset['ac']}", "inline": True},
            {"name": "🎒 สัมภาระเริ่มต้น", "value": "\n".join(f"• {g}" for g in gear) or "—",
             "inline": False},
        ]
        hook_lines = [f"• {data[k]}" for k in ("desire", "fear", "flaw", "connection")
                      if data.get(k)]
        if hook_lines:
            fields.append({"name": "สิ่งที่ติดตัวมา", "value": "\n".join(hook_lines),
                           "inline": False})
        return BridgeResult(handled=True, state_mutated=True, responses=[OutboundMessage(
            channel_id, summary, kind=MessageKind.CHARACTER_REVEAL,
            title=f"🎭 {char.name}",
            data={"fields": fields,
                  "footer": "ดูรายละเอียดได้ทุกเมื่อ: !rv sheet / !rv inventory"},
        )])

    async def _cancel(self, draft: CharacterDraft, channel_id: str) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            row = await s.get(CharacterDraft, draft.id)
            row.status = "CANCELLED"
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, "ไม่เป็นไร ไว้พร้อมเมื่อไรพิมพ์ `!rv character` มาใหม่ได้เลย",
            kind=MessageKind.TABLE_NOTICE,
        )])


_NAME_RE = re.compile(r"ชื่อ(?:ว่า)?\s*[:：]?\s*(\S+)")


def extract_name(text: str) -> str | None:
    """Utility for tests/fakes: pull 'ชื่อ X' out of Thai prose."""
    m = _NAME_RE.search(text or "")
    return m.group(1) if m else None
