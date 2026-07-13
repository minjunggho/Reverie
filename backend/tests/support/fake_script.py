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
    ActionStep,
    AdjudicationDecision,
    ClassificationResult,
    ConsequenceProposal,
    CreationGuidance,
    LocationDraft,
    Narration,
    NPCResponse,
    OpeningScene,
    PostSessionReport,
    ProposedDelta,
    Recap,
)


def _joined(messages) -> str:
    return "\n".join(m.get("content", "") for m in messages)


# Deterministic compound-action splitter for the FAKE interpreter (the real LLM
# does this itself). Splits on connectives, classifies each clause into a typed
# ActionStep, and marks dialogue/future-intention as non-executable.
_STEP_CONNECTIVES = ("แล้ว", "จากนั้น", " then ", ", ")
_FUTURE_MARKERS = ("เดี๋ยว", "ไว้ค่อย", "จะไป", "ต้องไป", "later", "จะ")


def _classify_clause(clause: str) -> ActionStep:
    c = clause.strip()
    low = c.lower()
    # Dialogue / future intention: preserved, never executed as a physical action.
    is_thanks = any(w in c for w in ("ขอบคุณ", "ขอบใจ", "thank"))
    if is_thanks and any(w in c for w in ("ต้องไป", "จะไป", "ธุระ", "เดี๋ยว")):
        return ActionStep(kind="SPEAK", text=c, temporal="FUTURE")
    if is_thanks or c.startswith("“") or c.startswith('"') or "พูด" in c:
        target = "เขา" if "เขา" in c else ""
        return ActionStep(kind="SPEAK", text=c, targets=[target] if target else [],
                          temporal="IMMEDIATE")
    if any(w in c for w in ("ต่อย", "โจมตี", "ฟัน", "แทง", "attack", "punch", "hit")):
        tgt = _grab_target(c, ("ต่อย", "โจมตี", "attack", "punch", "hit"))
        return ActionStep(kind="ATTACK", text=c, targets=[tgt] if tgt else [], method=c)
    if any(w in c for w in ("หยิบ", "คว้า", "เก็บ", "grab", "take", "pick")):
        return ActionStep(kind="SEARCH", text=c, method=c)
    if any(w in c for w in ("วิ่งหนี", "หนี", "flee", "run", "เดินออก", "ออกจาก", "ออกไป", "leave", "ไปหา")):
        dest = c
        return ActionStep(kind="MOVE", text=c, destination=dest, method=c)
    if c.startswith("ร่าย") or low.startswith("cast"):
        return ActionStep(kind="CAST", text=c, spell_reference=c.replace("ร่าย", "").strip())
    return ActionStep(kind="OTHER", text=c, method=c)


def _grab_target(clause: str, verbs) -> str:
    import re as _re

    rest = clause
    for v in verbs:
        rest = _re.sub(rf".*?{v}", "", rest, count=1, flags=_re.I)
    return rest.strip().split()[0].strip("“”\"") if rest.strip() else ""


# Feature phrases → canonical feature key for the FAKE interpreter.
_FEATURE_PHRASES = {
    "second_wind": ("second wind", "ลมหายใจที่สอง"),
    "action_surge": ("action surge", "ระเบิดพลัง"),
    "rage": ("เกรี้ยวกราด", "rage", "โหมดเดือด", "คลั่ง"),
    "reckless_attack": ("บ้าระห่ำ", "reckless"),
    "flurry_of_blows": ("หมัดรัว", "flurry"),
    "patient_defense": ("ตั้งรับ", "patient defense"),
    "step_of_the_wind": ("ย่างลม", "step of the wind"),
    "lay_on_hands": ("วางมือ", "lay on hands"),
    "divine_smite": ("ตวัดศักดิ์สิทธิ์", "divine smite", "สมิท"),
    "channel_divinity": ("channel divinity", "ศักดิ์สิทธิ์"),
    "wild_shape": ("แปลงร่าง", "wild shape"),
}


def _feature_reference(text: str):
    low = text.lower()
    if not (text.startswith("ใช้") or "โหมด" in text or "เข้าสู่" in text
            or any(p in low for fam in _FEATURE_PHRASES.values() for p in fam)):
        return None
    for key, phrases in _FEATURE_PHRASES.items():
        if any(p in low for p in phrases):
            return key
    return None


def _compound_steps(text: str):
    import re as _re

    pattern = "|".join(_re.escape(c) for c in _STEP_CONNECTIVES)
    clauses = [p.strip() for p in _re.split(pattern, text) if p.strip()]
    if len(clauses) < 2:
        return None
    return [_classify_clause(c) for c in clauses]


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
    low = text.lower()

    # Natural following (reuse consent system): "ตาม Kael ไป" / "I follow Kael".
    if (("ตาม" in text and "หยุด" not in text and "เลิก" not in text)
            or low.startswith("i follow") or " follow " in low):
        import re as _re

        after = _re.sub(r".*?ตาม|.*?follow", "", text, count=1, flags=_re.I)
        leader = _re.split(r"ไป|\bto\b", after, maxsplit=1)[0].strip()
        return ActionInterpretation(
            goal=f"ตาม {leader}", method="เดินตาม", intent_confidence=0.9,
            follow_intent=True, follow_reference=leader)
    if any(p in text for p in ("หยุดตาม", "เลิกตาม", "อยู่ที่นี่", "ไม่ตาม")) \
            or "stay here" in low or "stop following" in low:
        return ActionInterpretation(goal="หยุดตาม", method="อยู่กับที่",
                                    intent_confidence=0.9, stop_following=True)

    # Class-feature activation: "ใช้ <feature>" / "เข้าโหมดเกรี้ยวกราด" / feature name.
    feat = _feature_reference(text)
    if feat is not None:
        return ActionInterpretation(
            goal=f"ใช้ {feat}", method="ใช้ความสามารถ", intent_confidence=0.9,
            activate_intent=True, feature_reference=feat)

    # Ordered compound action: split on Thai/English connectives into steps.
    compound = _compound_steps(text)
    if compound is not None and len([s for s in compound if s.temporal == "IMMEDIATE"]) >= 2:
        return ActionInterpretation(
            goal=text[:60], method="หลายขั้นตอน", intent_confidence=0.85,
            target_references=[t for s in compound for t in s.targets], steps=compound)

    # Spellcasting: "ร่าย <spell> ใส่ <target>" / "cast <spell> at <target>".
    if text.startswith("ร่าย") or "ร่ายคาถา" in text or low.startswith("cast "):
        import re as _re

        after = text.replace("ร่ายคาถา", "").replace("ร่าย", "").strip()
        after = _re.sub(r"^cast\s+", "", after, flags=_re.I)
        # spell name = text before ใส่/at; target = after it.
        m = _re.split(r"ใส่|\bat\b|to\b", after, maxsplit=1)
        spell_ref = m[0].strip()
        targets = [m[1].strip()] if len(m) > 1 and m[1].strip() else []
        return ActionInterpretation(
            goal=f"ร่าย {spell_ref}", method="ร่ายคาถา", target_references=targets,
            declared_constraints=[], risk_awareness=[], intent_confidence=0.9,
            missing_information=[], cast_intent=True, spell_reference=spell_ref,
        )

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


# Explicit class/species keyword tables for the deterministic "rich concept" fake.
_FAKE_CLASS_WORDS = {
    "paladin": "paladin", "sorcerer": "sorcerer", "warlock": "warlock",
    "barbarian": "barbarian", "druid": "druid", "monk": "monk",
    "fighter": "fighter", "rogue": "rogue", "wizard": "wizard",
    "cleric": "cleric", "ranger": "ranger", "bard": "bard",
}
_FAKE_SPECIES_WORDS = {
    "catfolk": "Catfolk", "dragonborn": "Dragonborn", "elf": "elf",
    "dwarf": "dwarf", "human": "human", "tiefling": "tiefling", "aasimar": "aasimar",
}


def _rich_concept_guidance(msg: str, known: str):
    """If one message supplies an explicit class + ancestry (and some identity),
    extract it all at once and go to reveal — proving the flow does not re-ask for
    what was already given. Returns None to fall through to the 3-turn path."""
    from app.services.campaigns.creation_flow import extract_name

    low = (msg or "").lower()
    stated_class = next((canon for kw, canon in _FAKE_CLASS_WORDS.items() if kw in low), None)
    stated_species = next((canon for kw, canon in _FAKE_SPECIES_WORDS.items() if kw in low), None)
    if stated_class is None or stated_species is None or "concept=" in known:
        return None

    fields = {"concept": msg.strip()}
    # Extract a few structured aspects when their cue words appear.
    for cue, key in (("temple", "religion"), ("วิหาร", "religion"), ("โบสถ์", "religion"),
                     ("mentor", "mentors"), ("อาจารย์", "mentors"),
                     ("rival", "rivals"), ("คู่ปรับ", "rivals"),
                     ("family", "family"), ("ครอบครัว", "family"),
                     ("wing", "distinctive_marks"), ("ปีก", "distinctive_marks")):
        if cue in low:
            fields[key] = msg.strip()
    name = extract_name(msg) or _FIRST_CAP(msg) or "นักผจญภัย"
    fields["name"] = name
    fields.setdefault("origin", msg.strip())
    fields.setdefault("desire", "ตามหาความหมายของคำสาบานที่ให้ไว้")
    return CreationGuidance(
        updated_fields=fields,
        proposed_class=stated_class,
        proposed_species=stated_species,
        reaction="ภาพชัดมากตั้งแต่ประโยคแรก",
        ready_to_reveal=True,
        reveal_summary=f"{name} — {msg.strip()[:80]}",
    )


def _FIRST_CAP(text: str) -> str | None:
    """First capitalized word (a rough English name grab for the fake)."""
    import re as _re

    m = _re.search(r"\b([A-Z][a-z]{2,})\b", text or "")
    return m.group(1) if m else None


def _creation_guide(messages, _model) -> CreationGuidance:
    """Deterministic creation conversation.

    Two modes: a RICH one-message concept (explicit class/species + several identity
    aspects) is recognized immediately and goes straight to reveal; otherwise the
    original 3-turn minimal-concept path runs. Keyed off KNOWN/MESSAGE markers."""
    from app.services.campaigns.creation_flow import extract_name

    known = _marker(messages, "KNOWN")
    msg = _marker(messages, "MESSAGE")

    # --- rich one-message concept (adaptive: recognize what's supplied) ----------
    rich = _rich_concept_guidance(msg, known)
    if rich is not None:
        return rich

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


def _frame_scene(messages, _model) -> Narration:
    """Frame the destination from the canonical OBVIOUS marker — never inventing."""
    obvious = _marker(messages, "OBVIOUS")
    location = _marker(messages, "LOCATION")
    text = obvious or (f"เจ้ามาถึง{location}" if location else "ฉากใหม่เปิดขึ้น")
    return Narration(text=text, decision_prompt="จะทำอะไรต่อ?")


def _location_expansion(messages, _model) -> LocationDraft:
    request = _marker(messages, "REQUEST") or "ร้านเล็กๆ"
    name = request if "ร้าน" in request else f"ร้าน{request}"
    return LocationDraft(
        name=name, location_type="SHOP",
        obvious=f"{name} — ร้านแคบๆ เบียดอยู่ระหว่างอาคารสองหลัง",
        canon_justification="ร้านธรรมดาที่พบได้ทั่วไปในย่านนี้",
        connection_label="ประตูร้าน", travel_minutes=0, npc_name="เจ้าของร้าน",
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


def _campaign_proposal(messages, _model) -> dict:
    """Deterministic world proposal grown from the PREMISE marker — campaign-specific
    names (never a universal tavern), a navigable 3-location graph, session prep."""
    premise = _marker(messages, "PREMISE") or "โลกที่ยังไม่มีใครตั้งชื่อ"
    return {
        "identity_name": f"แคมเปญ: {premise[:60]}",
        "brief": f"โลกนี้เกิดจากไอเดียของเจ้าของโต๊ะ — {premise}",
        "central_question": "ความจริงที่ถูกฝังไว้จะคุ้มราคาที่ต้องจ่ายหรือไม่?",
        "world_facts": [
            {"fact": "ผู้คนที่นี่ไม่พูดถึงเรื่องนั้นตอนกลางคืน"},
            {"fact": "ผู้มาเยือนต้องรายงานตัวกับผู้ดูแลเขตก่อนพระอาทิตย์ตก"},
        ],
        "locations": [
            {"key": "watch-yard", "name": "ลานเวรยามเก่า", "location_type": "LOCATION",
             "obvious": "ลานหินกว้าง มีหอสังเกตการณ์ไม้เอียงๆ และกระดานประกาศที่ถูกฉีกครึ่ง",
             "current_activity": "ชาวบ้านกลุ่มเล็กกำลังถกเถียงกันเรื่องประกาศที่หายไป",
             "exits": [{"to": "keeper-hall", "label": "ทางเดินสู่หอผู้ดูแล", "travel_minutes": 5},
                        {"to": "old-boundary", "label": "ถนนสู่แนวเขตเก่า", "travel_minutes": 15}]},
            {"key": "keeper-hall", "name": "หอผู้ดูแลเขต", "location_type": "BUILDING",
             "obvious": "อาคารเตี้ยหลังคาหนัก ในโถงมีสมุดทะเบียนเล่มใหญ่วางเปิดอยู่",
             "exits": [{"to": "watch-yard", "label": "ประตูหน้าออกสู่ลาน", "travel_minutes": 5}]},
            {"key": "old-boundary", "name": "แนวเขตเก่า", "location_type": "LOCATION",
             "obvious": "เสาหินปักเรียงเป็นแนว บางต้นล้ม มีรอยเชือกใหม่ผูกอยู่กับต้นที่ยังตั้ง",
             "exits": [{"to": "watch-yard", "label": "ถนนกลับสู่ลานเวรยาม", "travel_minutes": 15}]},
        ],
        "npcs": [
            {"key": "keeper-orin", "name": "ผู้ดูแลโอริน", "personality": "ละเอียด ระแวดระวัง",
             "voice": "เนิบ ชัดถ้อยชัดคำ", "goal": "ปกปิดว่าทะเบียนหน้าหนึ่งถูกฉีกไป",
             "location": "keeper-hall"},
            {"key": "runner-mai", "name": "คนส่งข่าวไหม", "personality": "ปากไว ใจร้อน",
             "voice": "รัว เร็ว", "goal": "หาคนช่วยตามหาพี่ชายที่หายไปแถวแนวเขต",
             "location": "watch-yard"},
        ],
        "factions": [
            {"key": "boundary-wardens", "name": "ผู้พิทักษ์แนวเขต",
             "goal": "ไม่ให้ใครข้ามแนวเขตเก่าโดยไม่มีตรา", "next_action": "เพิ่มเวรยามกลางคืน"},
        ],
        "threats": [
            {"key": "the-unmarked", "name": "สิ่งที่ไม่มีตรา",
             "goal": "ลบชื่อผู้คนออกจากทะเบียน", "next_action": "ชื่อถัดไปจะหายภายในคืนนี้",
             "progress": 20, "scheduled_minutes": 480},
        ],
        "secrets": [
            {"key": "torn-page", "fact": "หน้าทะเบียนที่หายไปมีชื่อของผู้ดูแลโอรินเอง",
             "clues": ["รอยฉีกในสมุดทะเบียนตรงกับกระดาษที่พบใต้หอสังเกตการณ์",
                        "คนส่งข่าวไหมจำได้ว่าพี่ชายเคยถือกระดาษหน้านั้น"]},
        ],
        "session_prep": {
            "purpose": "แนะนำแนวเขตและการหายไปครั้งแรก",
            "opening_location": "watch-yard",
            "present_npcs": ["คนส่งข่าวไหม"],
            "current_activity": "ชาวบ้านกำลังถกเถียงเรื่องประกาศที่ถูกฉีก",
            "allowed_clues": ["ประกาศที่ถูกฉีกครึ่งพูดถึง 'ชื่อที่หายไป'"],
        },
        "starting_location": "watch-yard",
    }


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
    fake.on("frame_scene", _frame_scene)
    fake.on("generate_location_expansion", _location_expansion)
    fake.on("propose_campaign_world", _campaign_proposal)
    return fake
