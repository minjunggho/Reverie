"""AI-assisted campaign creation — a short owner premise becomes a reviewable world.

The owner writes one natural-language idea (`!rv campaign create <premise>`); this
job turns it into a full `CampaignProposal` — the SAME schema the file importer
produces — so one pipeline reviews, validates, and commits both. The AI proposes;
the engine validates (deterministic checks in canon_import); the owner approves;
only then does anything become canon. Nothing here mutates state.
"""
from __future__ import annotations

from app.ai.llm.base import LLMMessage, LLMProvider
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.services.campaigns.canon_import import CampaignProposal, ImportReview, _validate

log = get_logger(__name__)

_SYSTEM = """คุณคือผู้ช่วยออกแบบแคมเปญ TTRPG ภาษาไทย หน้าที่ของคุณคือแปลงไอเดียสั้นๆ ของเจ้าของโต๊ะ
ให้เป็นข้อเสนอโลกแคมเปญที่ "เล่นได้จริง" — ไม่ใช่แค่รายชื่อ

กติกาเหล็ก:
- ทุกอย่างต้องเติบโตจาก PREMISE ของเจ้าของโต๊ะ ห้ามสร้างแนวเรื่องอื่นมาแทน
- ห้ามใช้ฉากเปิดสำเร็จรูปสากล (โรงเตี๊ยมทั่วไป กองไฟ จัตุรัสเมืองนิรนาม) เว้นแต่ PREMISE ขอเอง
- locations: 4-8 แห่ง ต้องเชื่อมถึงกันด้วย exits (ไปได้และมีชื่อเส้นทางภาษาไทย)
  มีทั้งที่เปิดเรื่อง ที่อยู่อาศัย/ทำงานของ NPC และปลายทางของเบาะแสอย่างน้อยหนึ่งแห่ง
- key ของทุกอย่างเป็น ASCII slug ตัวเล็ก (a-z0-9-) และห้ามซ้ำ
- starting_location ต้องเป็น key ที่มีจริงใน locations
- session_prep ต้องมี purpose, opening_location (key เดียวกับ starting_location),
  present_npcs (ชื่อ NPC ที่มีจริง), current_activity, allowed_clues (1-3 ข้อ)
- npcs: 2-5 คน ทุกคนมี personality, voice, goal และ location (key ที่มีจริง)
- factions/threats: อย่างละ 1-2 มี goal และ next_action ที่กำลังจะเกิด
- secrets: 1-2 ความจริงเบื้องหลัง แต่ละอันมี clues อย่างน้อย 2 ทาง
- brief: ย่อหน้าที่ผู้เล่นอ่านได้ (ห้ามสปอยล์ secret) / central_question: คำถามใหญ่หนึ่งประโยค
- world_facts: 3-8 ข้อเท็จจริงของโลกที่ผู้เล่นรู้ได้

ตอบเป็น JSON ตาม schema ที่กำหนดเท่านั้น"""


async def propose_campaign_world(
    provider: LLMProvider, *, premise: str, campaign_name: str = "",
    table_profile: dict | None = None,
) -> tuple[CampaignProposal, ImportReview]:
    """Generate a validated world proposal from one owner premise.

    Runs the SAME deterministic validation as the file importer; one semantic
    retry with the validation error fed back, then the error surfaces to the
    owner (never a silently-broken world)."""
    profile = table_profile or {}
    user = (
        f"PREMISE: {premise}\n"
        f"CAMPAIGN_NAME: {campaign_name or '-'}\n"
        f"TONE: {profile.get('tone', '-')}\n"
        f"STYLE: {profile.get('balance', '-')}\n"
        f"BOUNDARIES: {', '.join(profile.get('boundaries') or []) or '-'}"
    )
    messages: list[LLMMessage] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    last_error: ValidationError | None = None
    for attempt in range(2):
        proposal = await provider.structured_complete(
            response_model=CampaignProposal, messages=messages,
            task="propose_campaign_world", temperature=0.8,
        )
        try:
            review = _validate(proposal)
        except ValidationError as exc:
            last_error = exc
            log.warning("campaign proposal failed validation (attempt %d): %s",
                        attempt + 1, exc)
            messages = messages + [
                {"role": "assistant", "content": proposal.model_dump_json()},
                {"role": "user", "content": f"ข้อเสนอนี้ผิดโครงสร้าง: {exc}\nแก้แล้วส่งใหม่ทั้งฉบับ"},
            ]
            continue
        return proposal, review
    raise last_error  # type: ignore[misc]
