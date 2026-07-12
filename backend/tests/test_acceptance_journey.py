"""THE acceptance journey (experience overhaul): two players, the complete table
experience end-to-end on the FakeLLM — from cold channel to session 2 continuity.

install/open -> friendly onboarding -> join -> immersive creation -> reveal with
hooks -> Session 1 opening tied to an established hook -> discussion is a no-op ->
! Thai action -> adjudication -> server dice -> canonical commit -> structured Thai
narration -> sheet -> inventory -> private secret (no public leak) -> NPC dialogue
respecting NPC knowledge -> close -> immersive safe chronicle -> reload -> session 2
with correct continuity and recap.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.randomness import SequenceRandomness
from app.discord_bridge import AdminBridge, InboundMessage, is_admin_command
from app.engine import build_bridge
from app.models.character import Character
from app.models.enums import ConsequenceClass, EventType, MessageCategory, SessionStatus
from app.models.event import Event
from app.models.knowledge import Secret
from app.models.session import Session
from app.presentation import MessageKind
from app.schemas.llm_io import ClassificationResult, ConsequenceProposal, ProposedDelta

_n = {"v": 0}
OWNER, P2 = "u-nick", "u-mai"


def _msg(content, author, name):
    _n["v"] += 1
    return InboundMessage(
        discord_message_id=f"j{_n['v']}", guild_id="g1", channel_id="table-1",
        author_discord_id=author, author_display_name=name, content=content,
    )


class Table:
    """One 'application instance': admin + game bridges over the shared DB."""

    def __init__(self, db, provider, rng=None):
        self.game = build_bridge(db, provider=provider,
                                 rng=rng or SequenceRandomness(default=12))
        self.admin = AdminBridge(db, provider, creation_flow=self.game.creation_flow,
                                 session_zero=self.game.session_zero)

    async def send(self, content, author, name):
        inbound = _msg(content, author, name)
        if is_admin_command(content):
            return await self.admin.handle(inbound)
        return await self.game.handle_inbound(inbound)


async def test_two_player_full_journey(db, provider):
    table = Table(db, provider, rng=SequenceRandomness([17]))

    # ---- 1. open Reverie: friendly, context-aware onboarding --------------------
    r = await table.send("!rv", OWNER, "นิค")
    assert r.responses[0].kind == MessageKind.REVERIE_WELCOME
    assert "campaign new" in r.responses[0].content        # tells you the next step

    r = await table.send("!rv campaign new เงาเหนือนครฝน", OWNER, "นิค")
    assert r.responses[0].kind == MessageKind.REVERIE_WELCOME

    # Session Zero — quick and friendly, stored on the table profile.
    r = await table.send("!rv setup", OWNER, "นิค")
    assert r.responses[0].choices
    await table.send("มืด จริงจัง", OWNER, "นิค")
    await table.send("เน้นบทบาท คุยกับ NPC", OWNER, "นิค")
    await table.send("มี — ช่วยอธิบายกฎหน่อยนะ", OWNER, "นิค")
    r = await table.send("ไม่มี ข้ามได้", OWNER, "นิค")
    assert "พร้อมแล้ว" in r.responses[0].content

    # ---- 1b. the owner turns ONE idea into a reviewable world (AI proposes,
    #          the owner approves, only then does it become canon) ----------------
    r = await table.send(
        "!rv campaign create หมู่บ้านชายแดนที่ชื่อผู้คนค่อยๆ หายไปจากทะเบียน", OWNER, "นิค")
    assert "ยังไม่มีอะไรเป็น canon" in r.responses[0].content
    assert "ลานเวรยามเก่า" in r.responses[0].content         # reviewable proposal
    import re as _re

    approve_id = _re.search(r"approve (\w+)", r.responses[0].content).group(1)
    r = await table.send(f"!rv campaign import approve {approve_id}", OWNER, "นิค")
    assert "canon" in r.responses[0].content

    # ---- 2. both players join; P1 creates via the immersive two-stage flow ------
    await table.send("!rv join", OWNER, "นิค")   # owner also plays
    await table.send("!rv join", P2, "ไหม")

    from tests.test_experience_overhaul import walk_nara_creation

    r = await walk_nara_creation(table, author=OWNER, name="นิค")

    # ---- 3. final reveal: real hooks + player-chosen supported mechanics --------
    reveal = r.responses[0]
    assert reveal.kind == MessageKind.CHARACTER_REVEAL
    async with db.session() as s:
        nara = (await s.execute(select(Character).where(Character.name == "Nara"))).scalar_one()
        assert nara.char_class == "rogue"                   # chosen by the player
        assert nara.hooks.get("desire") and nara.hooks.get("flaw")
        campaign_id = nara.campaign_id

    # P2 takes the quick path — still valid.
    await table.send("!rv character Tam fighter", P2, "ไหม")

    # Pre-author a DM secret for later (the model can only POINT at it).
    async with db.unit_of_work() as s:
        secret = Secret(campaign_id=campaign_id, fact="SECRET_รอยสักของยามคือตราโบสถ์เงิน")
        s.add(secret)
        await s.flush()
        secret_id = secret.id

    # ---- 4. Session 1 opening tied to an established character hook, at the
    #          APPROVED world's starting location (never an invented tavern) ------
    r = await table.send("!rv session start", OWNER, "นิค")
    kinds = [m.kind for m in r.responses]
    assert kinds[0] == MessageKind.SESSION_TITLE
    assert "ลานเวรยามเก่า" in r.responses[0].data["footer"]  # canonical start
    assert "วันที่ 1" in r.responses[0].data["footer"]       # authoritative time
    frame = next(m for m in r.responses if m.kind == MessageKind.SCENE_FRAME)
    assert frame.data.get("decision_prompt")                # one open decision point
    async with db.session() as s:
        scene_started = (await s.execute(select(Event).where(
            Event.event_type == EventType.SCENE_STARTED.value))).scalars().first()
        used_hooks = scene_started.payload.get("used_hooks", [])
        assert used_hooks and "desire" in used_hooks[0]     # a REAL established hook

    # ---- 5. normal discussion causes no committed action ------------------------
    async with db.session() as s:
        events_before = (await s.execute(select(func.count(Event.id)))).scalar_one()
    r = await table.send("เราไปคุยกับยามก่อนดีไหม", P2, "ไหม")
    assert r.state_mutated is False
    async with db.session() as s:
        assert (await s.execute(select(func.count(Event.id)))).scalar_one() == events_before

    # ---- 6-9. ! Thai action -> adjudication -> DICE RITUAL -> commit -> narration
    provider.on("plan_consequence", lambda m, model: ConsequenceProposal(
        consequence_class=ConsequenceClass.SUCCESS,
        deltas=[ProposedDelta(kind="reveal_secret", target=f"character:{nara.id}",
                              payload={"secret_id": secret_id})],
    ))
    r = await table.send("! ฉันย่องเข้าไปใกล้ๆ แอบดูข้อมือของยาม ไม่ให้เขาเห็น", OWNER, "นิค")
    assert r.responses[0].kind == MessageKind.CHECK_PROMPT   # the table holds its breath
    assert r.state_mutated is False
    r = await table.send("🎲 ทอย d20", OWNER, "นิค")          # the player rolls
    public = [m for m in r.responses if m.private_to_discord_id is None]
    private = [m for m in r.responses if m.private_to_discord_id is not None]
    assert public[0].kind == MessageKind.CHECK_RESOLUTION
    assert "17 + 5 = 22" in public[0].data["roll_line"]     # server d20=17, DEX+3 prof+2
    assert public[1].kind == MessageKind.SCENE_FRAME        # narration arrives separately
    assert "22" not in public[1].content                    # prose stays prose
    async with db.session() as s:
        check = (await s.execute(select(Event).where(
            Event.event_type == EventType.ABILITY_CHECK_RESOLVED.value))).scalar_one()
        assert check.mechanical_changes["natural_roll"] == 17
        assert check.payload["skill"] == "stealth"

    # ---- 12. one private secret, zero public leakage ----------------------------
    assert len(private) == 1 and private[0].kind == MessageKind.PRIVATE_SECRET
    assert private[0].private_to_discord_id == OWNER
    assert "SECRET_" in private[0].content
    assert all("SECRET_" not in m.content for m in public)

    # ---- 10-11. inspect character sheet + inventory ------------------------------
    r = await table.send("!rv sheet", OWNER, "นิค")
    assert r.responses[0].kind == MessageKind.CHARACTER_SHEET
    field_names = [f["name"] for f in r.responses[0].data["fields"]]
    assert any("HP" in n for n in field_names)
    r = await table.send("!rv inventory", OWNER, "นิค")
    assert r.responses[0].kind == MessageKind.INVENTORY
    assert "มีดสั้นคู่" in r.responses[0].content            # rogue starting gear

    # ---- 13. NPC dialogue that respects NPC knowledge ----------------------------
    # The scene has no NPC visible in this generated opening; wire one in by
    # authoring the guard into the active scene (owner-side prep).
    from app.npcs import NPCService
    from app.services.scenes import SceneService
    from app.services.sessions import SessionService
    async with db.unit_of_work() as s:
        guard = await NPCService(s).create_npc(
            campaign_id=campaign_id, name="ยามประตูฝน", personality="ขี้เบื่อ",
            voice_register="ห้วน")
        active = await SessionService(s).get_active_session(campaign_id)
        scene = await SceneService(s).get_active_scene(active.id)
        await SceneService(s).update_context(
            scene, visible_entity_ids=[f"npc:{guard.id}"])
        session1_id = active.id
    provider.push("classify_table_message", ClassificationResult(
        category=MessageCategory.CHARACTER_DIALOGUE, confidence=0.9))
    r = await table.send("“ท่านยาม ประตูปิดเร็วจังคืนนี้”", P2, "ไหม")
    assert r.responses[0].kind == MessageKind.NPC_DIALOGUE
    assert r.responses[0].title == "ยามประตูฝน"
    assert "SECRET_" not in r.responses[0].content           # NPC can't leak either

    # ---- 14. close: deliberate beat + immersive safe chronicle -------------------
    r = await table.send("!rv session end", OWNER, "นิค")
    kinds = [m.kind for m in r.responses]
    assert MessageKind.SCENE_TRANSITION in kinds
    chronicle = next(m for m in r.responses if m.kind == MessageKind.SESSION_END)
    assert "SECRET_" not in chronicle.content
    assert chronicle.data["fields"]                          # structured chronicle
    # Light one-tap feedback, recorded when tapped.
    r = await table.send("🔥 สนุกมาก", P2, "ไหม")
    async with db.session() as s:
        s1 = await s.get(Session, session1_id)
        assert s1.status == SessionStatus.COMPLETE.value
        assert list(s1.feedback.values()) == ["🔥 สนุกมาก"]

    # ---- 15. reload the application; session 2 continuity + recap ----------------
    table2 = Table(db, provider)  # fresh bridges = process restart; same DB
    r = await table2.send("!rv session start", OWNER, "นิค")
    kinds = [m.kind for m in r.responses]
    assert kinds[0] == MessageKind.SESSION_TITLE
    assert MessageKind.PLAYER_SAFE_RECAP in kinds            # ความเดิมตอนที่แล้ว
    recap = next(m for m in r.responses if m.kind == MessageKind.PLAYER_SAFE_RECAP)
    assert "SECRET_" not in recap.text if hasattr(recap, "text") else True
    assert "SECRET_" not in recap.content
    assert "เซสชันที่ 2" in (r.responses[0].title or "")
