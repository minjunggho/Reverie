"""A default deterministic script for the FakeLLMProvider.

The AI jobs embed machine-readable markers in their prompts (see the `prompts`
modules): `MESSAGE:`, `ACTION:`, `OUTCOME:`, `EVENTS:`. These handlers read those
markers and return schema-valid objects that reproduce the canonical Thai scenarios
from §33/§34. Any test may override a task with `provider.on(...)`/`provider.push(...)`.

This is a TEST DOUBLE. The scenario "judgement" encoded here stands in for a real
model; the engine still owns every number and every commit.
"""
from __future__ import annotations

from app.ai.llm.fake import FakeLLMProvider
from app.models.enums import (
    ConsequenceClass,
    DifficultyBand,
    MessageCategory,
    ResolutionType,
)
from app.schemas.llm_io import (
    ActionInterpretation,
    AdjudicationDecision,
    ClassificationResult,
    ConsequenceProposal,
    CreationGuidance,
    Narration,
    NPCResponse,
    OpeningScene,
    PostSessionReport,
    ProposedDelta,
    Recap,
)


def _joined(messages) -> str:
    return "\n".join(m.get("content", "") for m in messages)


def _marker(messages, name: str) -> str:
    """Return the text following a `NAME:` marker (rest of that line), else ''."""
    for m in messages:
        for line in m.get("content", "").splitlines():
            if line.strip().startswith(f"{name}:"):
                return line.split(":", 1)[1].strip()
    return ""


# --- handlers ----------------------------------------------------------------

def _classify(messages, _model) -> ClassificationResult:
    text = _marker(messages, "MESSAGE") or _joined(messages)
    if "?" in text or "ไหม" in text or "มั้ย" in text or "รึ" in text:
        # A rules/DM question vs planning discussion. Planning ("เราไป...ดีไหม") is OOC.
        if any(k in text for k in ("กติกา", "กฎ", "ทอย", "โรล", "roll", "dc", "ค่า")):
            return ClassificationResult(category=MessageCategory.RULES_QUESTION, confidence=0.8)
        if any(k in text for k in ("เรา", "พวกเรา", "ไป", "ดีไหม", "ดีมั้ย")):
            return ClassificationResult(category=MessageCategory.OOC_DISCUSSION, confidence=0.75)
        return ClassificationResult(category=MessageCategory.DM_QUESTION, confidence=0.7)
    return ClassificationResult(category=MessageCategory.SOCIAL_OR_JOKE, confidence=0.6)


def _interpret(messages, _model) -> ActionInterpretation:
    text = _marker(messages, "ACTION") or _joined(messages)

    if "จัดการ" in text and "ยาม" in text:
        return ActionInterpretation(
            goal="จัดการกับยาม", method="ไม่ระบุ",
            target_references=["ยาม"], declared_constraints=[], risk_awareness=[],
            intent_confidence=0.45,
            missing_information=["วิธีจัดการ: ฆ่า / ทำให้สลบ / ติดสินบน / เลี่ยงผ่าน"],
        )
    if "หน้าต่าง" in text:
        constraints = ["ไม่ให้ยามเห็น"] if ("ไม่ให้" in text and "เห็น" in text) else []
        method = "ค่อยๆ เคลื่อนที่อย่างเงียบ" if constraints or "ย่อง" in text or "ค่อยๆ" in text else "เดินไปดู"
        return ActionInterpretation(
            goal="เข้าไปดูตรงหน้าต่าง", method=method,
            target_references=["หน้าต่าง"], declared_constraints=constraints,
            risk_awareness=["ยามอาจเห็น"] if constraints else [],
            intent_confidence=0.85, missing_information=[],
        )
    if "ประตู" in text and ("เปิด" in text):
        return ActionInterpretation(
            goal="เปิดประตู", method="เดินไปเปิด", target_references=["ประตู"],
            declared_constraints=[], risk_awareness=[], intent_confidence=0.9,
            missing_information=[],
        )
    if "inspect" in text.lower() or "ศพ" in text:
        return ActionInterpretation(
            goal="ตรวจสอบศพ", method="พินิจดูอย่างละเอียด", target_references=["ศพ"],
            declared_constraints=[], risk_awareness=[], intent_confidence=0.82,
            missing_information=[],
        )
    return ActionInterpretation(
        goal=text[:60] or "ทำบางอย่าง", method="ตามที่บรรยาย", target_references=[],
        declared_constraints=[], risk_awareness=[], intent_confidence=0.75,
        missing_information=[],
    )


def _adjudicate(messages, _model) -> AdjudicationDecision:
    text = _marker(messages, "ACTION") or _joined(messages)

    if "จัดการ" in text and "ยาม" in text:
        return AdjudicationDecision(
            needs_clarification=True,
            clarification_question="จะจัดการยามยังไง?",
            rationale="วิธีการต่างกันให้ผลกลไกต่างกันมาก",
        )
    if ("ย่อง" in text or "แอบ" in text or ("ไม่ให้" in text and "เห็น" in text)
            or ("ค่อยๆ" in text and "หน้าต่าง" in text)):
        return AdjudicationDecision(
            resolution_type=ResolutionType.ABILITY_CHECK, ability="dex", skill="stealth",
            dc_band=DifficultyBand.MEDIUM,
            rationale="ต้องเคลื่อนที่โดยไม่ให้เป้าหมายสังเกตเห็น",
        )
    if "ประตู" in text and "เปิด" in text:
        return AdjudicationDecision(
            resolution_type=ResolutionType.AUTOMATIC_SUCCESS,
            rationale="ประตูธรรมดา ไม่ได้ล็อก ไม่มีความไม่แน่นอน",
        )
    if "ศพ" in text or "inspect" in text.lower():
        return AdjudicationDecision(
            resolution_type=ResolutionType.ABILITY_CHECK, ability="int", skill="investigation",
            dc_band=DifficultyBand.MEDIUM,
            rationale="พินิจหาเบาะแสต้องใช้การวิเคราะห์",
        )
    return AdjudicationDecision(
        resolution_type=ResolutionType.ABILITY_CHECK, ability="wis", skill="perception",
        dc_band=DifficultyBand.MEDIUM, rationale="ค่าเริ่มต้นที่ปลอดภัย",
    )


def _consequence(messages, _model) -> ConsequenceProposal:
    outcome = (_marker(messages, "OUTCOME") or "").lower()
    target = _marker(messages, "TARGET") or None
    if outcome.startswith("success"):
        return ConsequenceProposal(
            consequence_class=ConsequenceClass.SUCCESS, deltas=[],
            narration_hint="สำเร็จอย่างเงียบเชียบ",
        )
    deltas = []
    if target:
        deltas.append(ProposedDelta(kind="raise_suspicion", target=target,
                                    payload={"amount": 1}, reason="ทำเสียงดัง"))
    return ConsequenceProposal(
        consequence_class=ConsequenceClass.FAILURE_WITH_CONSEQUENCE,
        deltas=deltas, narration_hint="พลาดจนถูกสังเกต",
    )


def _narrate(messages, _model) -> Narration:
    outcome = (_marker(messages, "OUTCOME") or "").lower()
    if outcome.startswith("success"):
        return Narration(
            text="เจ้าค่อยๆ เคลื่อนตัวไปจนถึงหน้าต่าง\nยามยังยืนหันหลังอยู่ ไม่ทันสังเกต",
            style="concise",
            decision_prompt="ข้างนอกหน้าต่างคือลานหลังบ้าน — จะทำอะไรต่อ?",
        )
    return Narration(
        text="รองเท้าของเจ้าครูดกับพื้นหิน\nยามหันขวับมาทางเสียงนั้นทันที",
        style="concise",
        decision_prompt=None,
    )


def _recap(messages, _model) -> Recap:
    events = []
    capture = False
    for m in messages:
        for line in m.get("content", "").splitlines():
            if line.strip().startswith("EVENTS:"):
                capture = True
                continue
            if capture and line.strip().startswith("-"):
                events.append(line.strip()[1:].strip())
    body = "\n".join(events) if events else "ยังไม่มีเหตุการณ์สำคัญ"
    return Recap(text="เรื่องราวที่ผ่านมา:\n" + body)


def _post_session(messages, _model) -> PostSessionReport:
    return PostSessionReport(
        player_summary="สรุปเซสชัน (เวอร์ชันเทมเพลต)", continuity_report={},
    )


def _creation_guide(messages, _model) -> CreationGuidance:
    """Deterministic 3-turn creation conversation keyed off KNOWN fields."""
    from app.services.campaigns.creation_flow import extract_name

    known = _marker(messages, "KNOWN")
    msg = _marker(messages, "MESSAGE")

    if "concept=" not in known:
        return CreationGuidance(
            updated_fields={"concept": msg},
            next_question="ฟังดูมีเรื่องราว — เขาโตมายังไง แล้วตอนนี้ต้องการอะไรที่สุด?",
        )
    if "desire=" not in known:
        return CreationGuidance(
            updated_fields={"origin": msg, "desire": "อยากพิสูจน์ว่าตัวเองไม่ใช่แค่อดีตของตัวเอง",
                            "fear": "กลัวถูกทิ้งอีกครั้ง"},
            next_question="จุดอ่อนหรือความขัดแย้งในใจของเขาคืออะไร แล้วเขาชื่ออะไรดี?",
        )
    name = extract_name(msg) or "นิรนาม"
    concept = ""
    for part in known.split(";"):
        if part.strip().startswith("concept="):
            concept = part.split("=", 1)[1]
    proposed = "rogue" if any(w in concept for w in ("มีด", "โจร", "โกหก")) else \
               "wizard" if "เวท" in concept else "fighter"
    return CreationGuidance(
        updated_fields={"flaw": msg, "name": name, "connection": "รู้จักใครบางคนในปาร์ตี้มาก่อน",
                        "appearance": "ตัวเล็ก ตาไว มีดสั้นซ่อนในแขนเสื้อ"},
        proposed_class=proposed, ready_to_reveal=True,
        reveal_summary=f"{name} — {concept}",
    )


def _session_opening(messages, _model) -> OpeningScene:
    """Fake opening that provably uses a character hook from the context."""
    blob = _joined(messages)
    used = []
    hook_line = ""
    for line in blob.splitlines():
        if line.strip().startswith("- ") and "desire=" in line:
            frag = line.split("desire=", 1)[1].split(";", 1)[0].strip()
            if frag:
                used.append(f"desire:{frag}")
                hook_line = frag
            break
    return OpeningScene(
        title="ฝนแรกที่ประตูเมืองเก่า",
        situation_lines=[
            "ฝนเพิ่งหยุด กลิ่นดินเปียกลอยทั่วลาน",
            "พวกเจ้ายืนอยู่หน้าประตูเมืองที่ปิดเร็วกว่าปกติ",
            f"มีคนจำหน้าเจ้าได้ — เรื่องที่ว่า{hook_line or 'พวกเจ้ามีธุระในเมือง'} ไปถึงหูใครบางคนแล้ว",
        ],
        pressure="ยามบนกำแพงเริ่มชี้มือมาทางนี้ และประตูจะปิดสนิทในไม่ช้า",
        decision_prompt="ประตูกำลังจะปิด — พวกเจ้าจะทำยังไง?",
        used_hooks=used,
    )


def _npc_response(messages, _model) -> NPCResponse:
    # Default: cautious in-character reply, no proposed deltas. The utterance may echo
    # only what the NPC was told is KNOWN_TO_NPC. Tests override to propose deltas.
    return NPCResponse(utterance="ยามมองเจ้าอย่างระแวง แล้วพูดห้วนๆ ว่า 'มีธุระอะไร'")


def install_default_script(fake: FakeLLMProvider) -> FakeLLMProvider:
    fake.on("classify_table_message", _classify)
    fake.on("interpret_committed_action", _interpret)
    fake.on("adjudicate_uncertain_action", _adjudicate)
    fake.on("plan_consequence", _consequence)
    fake.on("generate_dm_narration", _narrate)
    fake.on("generate_safe_recap", _recap)
    fake.on("process_post_session_continuity", _post_session)
    fake.on("generate_npc_response", _npc_response)
    fake.on("guide_character_creation", _creation_guide)
    fake.on("generate_session_opening", _session_opening)
    return fake
