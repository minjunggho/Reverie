"""Session Zero — a short, friendly table-profile conversation (not a survey).

Owner runs `!rv setup`. Four quick questions (tone, balance, beginner help,
boundaries), answerable by button tap or typed text. Answers land in
`campaign.config['profile']` and feed the opening generator and assistance level.
State machine lives in `config['setup_state']` while active (working state).
"""
from __future__ import annotations

from app.discord_bridge.dto import BridgeResult, OutboundMessage
from app.models.campaign import Campaign
from app.models.enums import AssistanceLevel
from app.presentation import MessageKind

QUESTIONS = [
    {
        "key": "tone",
        "text": "โทนของโต๊ะเราเอาแบบไหนดี?",
        "choices": ["มืด จริงจัง", "ผจญภัยคลาสสิก", "สนุก ปนฮา"],
    },
    {
        "key": "balance",
        "text": "พวกเราชอบแบบไหนมากกว่ากัน?",
        "choices": ["เน้นบทบาท คุยกับ NPC", "สมดุลๆ ทุกอย่าง", "เน้นลุย เน้นสู้", "เน้นสำรวจ ไขปริศนา"],
    },
    {
        "key": "assistance",
        "text": "มีเพื่อนที่เพิ่งเคยเล่นแนวนี้ไหม?",
        "choices": ["มี — ช่วยอธิบายกฎหน่อยนะ", "ทุกคนพอเป็น เล่นเลย"],
    },
    {
        "key": "boundaries",
        "text": "มีเรื่องไหนที่ 'ไม่อยากให้โผล่' ในเกมไหม? พิมพ์บอกได้ตรงนี้เลย",
        "choices": ["ไม่มี ข้ามได้"],
    },
]

_SKIP_WORDS = ("ไม่มี", "ข้าม", "skip")


class SessionZeroService:
    def __init__(self, db) -> None:
        self.db = db

    @staticmethod
    def is_active(campaign: Campaign) -> bool:
        return bool((campaign.config or {}).get("setup_state"))

    async def start(self, *, campaign_id: str, channel_id: str) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            campaign = await s.get(Campaign, campaign_id)
            config = dict(campaign.config or {})
            config["setup_state"] = {"step": 0}
            campaign.config = config
        return self._question(channel_id, 0, intro=True)

    async def handle_message(
        self, *, campaign_id: str, channel_id: str, text: str
    ) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            campaign = await s.get(Campaign, campaign_id)
            config = dict(campaign.config or {})
            state = dict(config.get("setup_state") or {})
            step = int(state.get("step", 0))
            profile = dict(config.get("profile") or {})

            q = QUESTIONS[step]
            answer = (text or "").strip()
            if q["key"] == "boundaries":
                skipped = any(w in answer for w in _SKIP_WORDS)
                profile["boundaries"] = [] if skipped else [answer]
            elif q["key"] == "assistance":
                profile["assistance"] = (
                    AssistanceLevel.BEGINNER.value if "มี" in answer.split("—")[0] or "อธิบาย" in answer
                    else AssistanceLevel.MINIMAL.value
                )
                profile["assistance_answer"] = answer
            else:
                profile[q["key"]] = answer

            step += 1
            done = step >= len(QUESTIONS)
            if done:
                config.pop("setup_state", None)
                config["profile"] = profile
                # Mirror assistance into the engine-level default.
                config["assistance_default"] = profile.get(
                    "assistance", AssistanceLevel.BEGINNER.value
                )
            else:
                config["setup_state"] = {"step": step}
                config["profile"] = profile
            campaign.config = config

        if done:
            summary = [
                f"โทน: {profile.get('tone', '—')}",
                f"สไตล์: {profile.get('balance', '—')}",
                f"ช่วยมือใหม่: {profile.get('assistance_answer', '—')}",
            ]
            if profile.get("boundaries"):
                summary.append("ขอบเขต: รับทราบแล้ว (DM จะเลี่ยงให้)")
            return BridgeResult(handled=True, responses=[OutboundMessage(
                channel_id,
                "\n".join(summary) + "\n\nพร้อมแล้ว! สร้างตัวละครกันได้เลย: `!rv character`",
                kind=MessageKind.TABLE_NOTICE, title="โต๊ะของเราตั้งเสร็จแล้ว 🕯️",
            )])
        return self._question(channel_id, step)

    def _question(self, channel_id: str, step: int, intro: bool = False) -> BridgeResult:
        q = QUESTIONS[step]
        prefix = (
            "ก่อนเริ่มผจญภัย ขอถามสั้นๆ 4 ข้อ ให้ DM เล่าได้ถูกจริตพวกเรา\n\n"
            if intro else ""
        )
        return BridgeResult(handled=True, responses=[OutboundMessage(
            channel_id, prefix + f"**{step + 1}/4** — {q['text']}",
            kind=MessageKind.CHARACTER_CREATION, title="Session Zero",
            choices=list(q["choices"]),
        )])
