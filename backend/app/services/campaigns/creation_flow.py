"""Guided character creation — STAGE A: discover the person (§19).

`!rv character` opens a draft → the player's plain Thai messages route here →
the CreationGuidance AI extracts hook fields and asks ONE adaptive question per
turn → a REFLECTION card mirrors back what was heard ([ถูกต้อง]/[แก้ไข] — the AI
may summarize, never invent accepted facts) → then STAGE B (build_flow.py) walks
the actual SRD 5.2.1 choices, where the AI recommends and the PLAYER chooses
class, species, background, abilities, skills, and spells.

Bounded: after MAX_STEPS Stage A moves on with what it has. Cancel: 'ยกเลิก'.
"""
from __future__ import annotations

import re
import secrets
from asyncio import Lock

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
from app.services.campaigns.draft_store import DraftConflict, close_draft, save_draft
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
        from app.services.campaigns.build_flow import BuildFlow

        self.build = BuildFlow(db)
        # The live composition root constructs separate admin/game flow objects.
        # Store locks on their shared Database so concurrent clicks for one draft
        # serialize across both objects while different players remain independent.
        locks = getattr(db, "_character_creation_locks", None)
        if locks is None:
            locks = {}
            setattr(db, "_character_creation_locks", locks)
        self._locks: dict[tuple[str, str], Lock] = locks

    def _member_lock(self, campaign_id: str, member_id: str) -> Lock:
        return self._locks.setdefault((campaign_id, member_id), Lock())

    # --- queries ---------------------------------------------------------------
    async def active_draft(
        self, session, *, campaign_id: str, member_id: str
    ) -> CharacterDraft | None:
        return (
            await session.execute(
                select(CharacterDraft).where(
                    CharacterDraft.campaign_id == campaign_id,
                    CharacterDraft.member_id == member_id,
                    CharacterDraft.status == "ACTIVE",
                )
            )
        ).scalars().first()

    # --- entry points ------------------------------------------------------------
    async def start(self, *, campaign_id: str, member_id: str, channel_id: str) -> BridgeResult:
        async with self._member_lock(campaign_id, member_id):
            return await self._start_locked(
                campaign_id=campaign_id, member_id=member_id, channel_id=channel_id
            )

    async def _start_locked(
        self, *, campaign_id: str, member_id: str, channel_id: str
    ) -> BridgeResult:
        from sqlalchemy.exc import IntegrityError

        had_existing = False
        try:
            async with self.db.unit_of_work() as s:
                existing = await self.active_draft(
                    s, campaign_id=campaign_id, member_id=member_id
                )
                if existing is None:
                    s.add(CharacterDraft(
                        campaign_id=campaign_id,
                        member_id=member_id,
                        data={"_last_prompt": OPENING_QUESTION},
                    ))
                else:
                    had_existing = True
        except IntegrityError:
            # A concurrent starter (another process) won the one-active-draft
            # unique index; treat theirs as THE draft and resume it.
            had_existing = True
        if had_existing:
            return await self._resume_locked(
                campaign_id=campaign_id, member_id=member_id, channel_id=channel_id
            )
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, OPENING_QUESTION, kind=MessageKind.CHARACTER_CREATION,
            title="สร้างตัวละคร", data={"footer": "พิมพ์ 'ยกเลิก' ได้ทุกเมื่อ"},
        )])

    async def resume(
        self, *, campaign_id: str, member_id: str, channel_id: str
    ) -> BridgeResult:
        """Re-render one member's persisted draft without advancing or changing answers."""
        async with self._member_lock(campaign_id, member_id):
            return await self._resume_locked(
                campaign_id=campaign_id, member_id=member_id, channel_id=channel_id
            )

    async def _resume_locked(
        self, *, campaign_id: str, member_id: str, channel_id: str
    ) -> BridgeResult:
        async with self.db.session() as s:
            draft = await self.active_draft(
                s, campaign_id=campaign_id, member_id=member_id
            )
        if draft is None:
            return BridgeResult(handled=True, responses=[OutboundMessage(
                channel_id,
                "เจ้ายังไม่มีตัวละครที่สร้างค้างไว้ — เริ่มใหม่ด้วย `!rv character`",
                kind=MessageKind.TABLE_NOTICE,
            )])

        data = dict(draft.data or {})
        if data.get("_build"):
            if not data["_build"].get("component_token"):
                # Older drafts predate component tokens — mint one (safe default).
                data["_build"]["component_token"] = secrets.token_urlsafe(12)
                try:
                    await self._save(draft, data, step_inc=0)
                except DraftConflict:
                    # A concurrent writer already advanced the draft; render THEIR
                    # persisted state rather than our stale copy.
                    async with self.db.session() as s:
                        fresh = await self.active_draft(
                            s, campaign_id=campaign_id, member_id=member_id
                        )
                    if fresh is None:
                        return BridgeResult(handled=True, responses=[OutboundMessage(
                            channel_id,
                            "เจ้ายังไม่มีตัวละครที่สร้างค้างไว้ — เริ่มใหม่ด้วย `!rv character`",
                            kind=MessageKind.TABLE_NOTICE,
                        )])
                    data = dict(fresh.data or {})
            return self.build.render(data, channel_id)
        if data.get("_awaiting_confirm"):
            return self._reflection_card(channel_id, data)
        return self._ask(channel_id, data.get("_last_prompt") or OPENING_QUESTION)

    async def handle_message(
        self, *, campaign_id: str, member_id: str, channel_id: str, text: str
    ) -> BridgeResult:
        async with self._member_lock(campaign_id, member_id):
            try:
                return await self._handle_message_locked(
                    campaign_id=campaign_id,
                    member_id=member_id,
                    channel_id=channel_id,
                    text=text,
                )
            except DraftConflict:
                # Another writer (a second process / duplicated interaction) won
                # the compare-and-update. Nothing was overwritten — re-render the
                # winning persisted state instead of applying this stale input.
                return await self._resume_locked(
                    campaign_id=campaign_id, member_id=member_id, channel_id=channel_id
                )

    async def _handle_message_locked(
        self, *, campaign_id: str, member_id: str, channel_id: str, text: str
    ) -> BridgeResult:
        text = (text or "").strip()
        async with self.db.session() as s:
            draft = await self.active_draft(
                s, campaign_id=campaign_id, member_id=member_id
            )
        if draft is None:  # raced away; treat as unhandled notice
            return BridgeResult(
                handled=True,
                note="no active draft",
                responses=[OutboundMessage(
                    channel_id,
                    "แบบร่างนี้ไม่ได้เปิดอยู่แล้ว — ใช้ `!rv resume` เพื่อตรวจสอบ "
                    "หรือ `!rv character` เพื่อเริ่มใหม่",
                    kind=MessageKind.TABLE_NOTICE,
                )],
            )

        # Structured spell components carry a draft/step token and must reach
        # BuildFlow for validation before any action (especially cancel) occurs.
        is_spell_component = text.startswith("rvspell:")
        cancel_command = " ".join(text.casefold().split())
        if not is_spell_component and cancel_command in _CANCEL_WORDS:
            return await self._cancel(draft, channel_id)

        data = dict(draft.data or {})

        # A structured spell payload is meaningful only while this same draft is
        # on a spell step. Never let an old/foreign control become Stage-A prose or
        # a class/species choice merely because its text happens to contain a key.
        build_step = (data.get("_build") or {}).get("step")
        if is_spell_component and build_step not in {"cantrips", "book", "prepared"}:
            return BridgeResult(handled=True, responses=[OutboundMessage(
                channel_id,
                "ปุ่มนี้มาจากแบบร่างหรือขั้นตอนเก่า จึงไม่เปลี่ยนข้อมูลปัจจุบัน — "
                "ใช้ `!rv resume` เพื่อเปิดปุ่มของขั้นตอนล่าสุด",
                kind=MessageKind.CHARACTER_CREATION,
                title="ปุ่มนี้ใช้ไม่ได้แล้ว",
            )])

        # Stage B in progress? Delegate everything to the deterministic build walk.
        if data.get("_build"):
            if not data["_build"].get("component_token"):
                data["_build"]["component_token"] = secrets.token_urlsafe(12)
                await self._save(draft, data, step_inc=0)
            return await self.build.handle(draft, data, text, channel_id)

        # Reflection step: the mirror of what the DM heard is on the table.
        if data.get("_awaiting_confirm"):
            if text.startswith("✏") or CONFIRM_EDIT in text:
                data["_awaiting_confirm"] = False
                data["_last_prompt"] = "อยากปรับตรงไหน บอกมาได้เลย —"
                await self._save(draft, data, step_inc=0)
                return self._ask(channel_id, data["_last_prompt"])
            if text.startswith("✅") or any(w in text for w in ("ใช่", "โอเค", "ถูกต้อง", "เอาเลย", "ตามนั้น")):
                # Concept accepted → Stage B: the rules build (player chooses all).
                data["_awaiting_confirm"] = False
                return await self.build.start(draft, data, channel_id)
            # Anything else = an adjustment in prose; fall through to the guide.
            data["_awaiting_confirm"] = False

        # Ask the guide to extract fields + pose the next question.
        guidance = await self._guide(draft, data, text)
        for key, value in (guidance.updated_fields or {}).items():
            if key in HOOK_KEYS and isinstance(value, str) and value.strip():
                data[key] = value.strip()
        if guidance.proposed_class:
            # Stage A never fixes the class — it becomes the Stage-B recommendation.
            cls = guidance.proposed_class.strip().lower()
            data["_class_hint"] = cls if cls in SUPPORTED_CLASSES else infer_class_from_concept(
                data.get("concept", "") + " " + text
            )

        step = draft.step + 1
        forced = step >= MAX_STEPS
        ready = guidance.ready_to_reveal or forced
        if ready and self._complete_enough(data, force=forced):
            data["_awaiting_confirm"] = True
            data["_summary"] = guidance.reveal_summary or data.get("concept", "")
            await self._save(draft, data)
            return self._reflection_card(channel_id, data)

        question = guidance.next_question or "เล่าเพิ่มอีกนิดได้ไหม?"
        data["_last_prompt"] = question
        # Persist the exact adaptive prompt so a restart can reproduce this step.
        await self._save(draft, data)
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
        if force:
            data.setdefault("name", "นิรนาม")
            data.setdefault("concept", "นักผจญภัยปริศนา")
            return True
        return bool(data.get("concept")) and bool(data.get("name")) and identity >= 2

    async def _save(self, draft: CharacterDraft, data: dict, step_inc: int = 1) -> None:
        # Compare-and-update on draft.version — never a blind overwrite.
        await save_draft(self.db, draft, data, step_inc=step_inc)

    def _ask(self, channel_id: str, question: str) -> BridgeResult:
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, question, kind=MessageKind.CHARACTER_CREATION, title="สร้างตัวละคร",
        )])

    _REFLECT_LABEL = {
        "concept": None, "origin": "ที่มา", "desire": "สิ่งที่ต้องการ",
        "fear": "สิ่งที่กลัว", "flaw": "จุดอ่อนในใจ", "connection": "คนสำคัญ",
        "appearance": "รูปลักษณ์",
    }

    def _reflection_card(self, channel_id: str, data: dict) -> BridgeResult:
        """Mirror back what the DM heard — facts only, no mechanics, no class."""
        lines = []
        if data.get("concept"):
            lines.append(f"*{data['concept']}*")
        for key, label in self._REFLECT_LABEL.items():
            if label and data.get(key):
                lines.append(f"• {label}: {data[key]}")
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, "\n".join(lines),
            kind=MessageKind.CHARACTER_CREATION,
            title=f"สิ่งที่ข้าได้ยินจากเรื่องของ {data.get('name', 'เจ้า')}",
            data={"footer": "ถ้าถูกต้อง เดี๋ยวไปต่อส่วนกฎเกม — เจ้าเป็นคนเลือกทุกอย่างเอง"},
            choices=[CONFIRM_YES, CONFIRM_EDIT],
        )])

    async def _cancel(self, draft: CharacterDraft, channel_id: str) -> BridgeResult:
        await close_draft(self.db, draft.id, status="CANCELLED")
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, "ไม่เป็นไร ไว้พร้อมเมื่อไรพิมพ์ `!rv character` มาใหม่ได้เลย",
            kind=MessageKind.TABLE_NOTICE,
        )])


_NAME_RE = re.compile(r"ชื่อ(?:ว่า)?\s*[:：]?\s*(\S+)")


def extract_name(text: str) -> str | None:
    """Utility for tests/fakes: pull 'ชื่อ X' out of Thai prose."""
    m = _NAME_RE.search(text or "")
    return m.group(1) if m else None
