"""AdminBridge — table-setup commands (campaign/character/session lifecycle).

These are the `!rv ...` commands players type to create and run a table. They are
handled here (application layer) so the Discord bot stays a thin adapter. Setup
commands are deliberately routed BEFORE the game bridge, so a leading `!rv` is never
mistaken for a committed `!` character action.

No game logic lives here either — every command calls the existing engine services.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from app.core.errors import ReverieError
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.models.enums import MemberRole, SceneMode
from app.services.campaigns import CampaignService, CharacterService
from app.services.sessions import (
    PostSessionService,
    SessionClosingService,
    SessionOpeningService,
    SessionService,
)
from app.tabletop.rules import SUPPORTED_CLASSES
from app.world import LocationService

ADMIN_PREFIX = "!rv"

# Simple, transparent class presets (the beginner AI concept flow is future work).
CLASS_PRESETS: dict[str, dict] = {
    "fighter": dict(abilities={"str": 16, "con": 15, "dex": 13, "wis": 12, "int": 10, "cha": 8},
                    proficiencies=["athletics", "intimidation"], max_hp=12, ac=16),
    "rogue": dict(abilities={"dex": 16, "int": 13, "wis": 12, "con": 12, "cha": 10, "str": 8},
                  proficiencies=["stealth", "perception", "sleight_of_hand", "acrobatics"],
                  max_hp=9, ac=14),
    "wizard": dict(abilities={"int": 16, "con": 13, "dex": 12, "wis": 12, "cha": 10, "str": 8},
                   proficiencies=["arcana", "investigation"], max_hp=7, ac=12),
    "cleric": dict(abilities={"wis": 16, "con": 14, "str": 13, "cha": 12, "dex": 10, "int": 8},
                   proficiencies=["medicine", "insight", "religion"], max_hp=10, ac=15),
    "ranger": dict(abilities={"dex": 16, "wis": 14, "con": 13, "str": 12, "int": 10, "cha": 8},
                   proficiencies=["survival", "perception", "stealth", "animal_handling"],
                   max_hp=11, ac=14),
    "bard": dict(abilities={"cha": 16, "dex": 14, "con": 13, "int": 12, "wis": 10, "str": 8},
                 proficiencies=["persuasion", "performance", "deception", "insight"],
                 max_hp=9, ac=13),
}

HELP = (
    "**คำสั่งตั้งโต๊ะ Reverie**\n"
    "`!rv campaign new <ชื่อ>` — สร้างแคมเปญผูกกับห้องนี้ (ผู้สร้าง = เจ้าของโต๊ะ)\n"
    "`!rv join` — เข้าร่วมเป็นผู้เล่น\n"
    "`!rv character <ชื่อ> [คลาส]` — สร้างตัวละคร (คลาส: "
    + ", ".join(sorted(SUPPORTED_CLASSES)) + ")\n"
    "`!rv session start` — (เจ้าของโต๊ะ) เริ่มเซสชันและเปิดฉาก\n"
    "`!rv session end` — (เจ้าของโต๊ะ) จบเซสชันและสรุป\n"
    "`!rv status` — ดูสถานะโต๊ะ\n"
    "จากนั้นพิมพ์การกระทำของตัวละครโดยขึ้นต้นด้วย `!` เช่น `! ผมค่อยๆ ย่องไปดูหน้าต่าง`"
)


@dataclass
class _Ctx:
    inbound: InboundMessage
    args: list[str]


def is_admin_command(content: str) -> bool:
    c = content.strip()
    return c == ADMIN_PREFIX or c.startswith(ADMIN_PREFIX + " ")


class AdminBridge:
    def __init__(self, db, provider) -> None:
        self.db = db
        self.provider = provider

    async def handle(self, inbound: InboundMessage) -> BridgeResult:
        try:
            args = shlex.split(inbound.content.strip()[len(ADMIN_PREFIX):].strip())
        except ValueError:
            args = inbound.content.strip()[len(ADMIN_PREFIX):].strip().split()
        if not args or args[0] in ("help", "?"):
            return self._reply(inbound, HELP)

        cmd, rest = args[0].lower(), args[1:]
        ctx = _Ctx(inbound=inbound, args=rest)
        try:
            handler = {
                "campaign": self._campaign,
                "join": self._join,
                "character": self._character,
                "session": self._session,
                "status": self._status,
            }.get(cmd)
            if handler is None:
                return self._reply(inbound, f"ไม่รู้จักคำสั่ง `{cmd}` — พิมพ์ `!rv help`")
            return await handler(ctx)
        except ReverieError as exc:
            return self._reply(inbound, f"⚠️ {exc}")

    # --- commands ------------------------------------------------------------
    async def _campaign(self, ctx: _Ctx) -> BridgeResult:
        if not ctx.args or ctx.args[0].lower() != "new":
            return self._reply(ctx.inbound, "ใช้: `!rv campaign new <ชื่อ>`")
        name = " ".join(ctx.args[1:]).strip() or "แคมเปญไร้ชื่อ"
        async with self.db.unit_of_work() as s:
            svc = CampaignService(s)
            if await svc.resolve_campaign_by_channel(ctx.inbound.channel_id):
                return self._reply(ctx.inbound, "ห้องนี้มีแคมเปญอยู่แล้ว")
            campaign = await svc.create_campaign(
                name=name, discord_guild_id=ctx.inbound.guild_id,
                game_channel_id=ctx.inbound.channel_id,
                owner_discord_user_id=ctx.inbound.author_discord_id,
                owner_display_name=ctx.inbound.author_display_name,
            )
            await svc.activate_campaign(campaign.id)
        return self._reply(
            ctx.inbound,
            f"✅ สร้างแคมเปญ **{name}** แล้ว เจ้าของโต๊ะคือ "
            f"{ctx.inbound.author_display_name}\nให้ผู้เล่นพิมพ์ `!rv join` แล้วสร้างตัวละคร",
        )

    async def _join(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            svc = CampaignService(s)
            campaign = await svc.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._reply(ctx.inbound, "ยังไม่มีแคมเปญในห้องนี้ — `!rv campaign new <ชื่อ>`")
            await svc.add_member(
                campaign_id=campaign.id, discord_user_id=ctx.inbound.author_discord_id,
                display_name=ctx.inbound.author_display_name, role=MemberRole.PLAYER,
            )
        return self._reply(ctx.inbound, f"✅ {ctx.inbound.author_display_name} เข้าร่วมแล้ว "
                                        f"— ต่อไปสร้างตัวละคร: `!rv character <ชื่อ> [คลาส]`")

    async def _character(self, ctx: _Ctx) -> BridgeResult:
        if not ctx.args:
            return self._reply(ctx.inbound, "ใช้: `!rv character <ชื่อ> [คลาส]`")
        char_class = "fighter"
        name_tokens = ctx.args
        if ctx.args[-1].lower() in CLASS_PRESETS:
            char_class = ctx.args[-1].lower()
            name_tokens = ctx.args[:-1]
        name = " ".join(name_tokens).strip() or "นักผจญภัย"
        preset = CLASS_PRESETS[char_class]
        async with self.db.unit_of_work() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._reply(ctx.inbound, "ยังไม่มีแคมเปญในห้องนี้")
            member = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            if member is None:
                return self._reply(ctx.inbound, "เข้าร่วมก่อนด้วย `!rv join`")
            char = await CharacterService(s).create_character(
                member_id=member.id, name=name, char_class=char_class,
                abilities=preset["abilities"], proficiencies=preset["proficiencies"],
                max_hp=preset["max_hp"], ac=preset["ac"], set_active=True,
            )
        return self._reply(
            ctx.inbound,
            f"✅ สร้างตัวละคร **{char.name}** ({char_class}) — HP {preset['max_hp']}, AC {preset['ac']}\n"
            f"ทักษะถนัด: {', '.join(preset['proficiencies'])}",
        )

    async def _session(self, ctx: _Ctx) -> BridgeResult:
        sub = (ctx.args[0].lower() if ctx.args else "")
        if sub == "start":
            return await self._session_start(ctx)
        if sub == "end":
            return await self._session_end(ctx)
        return self._reply(ctx.inbound, "ใช้: `!rv session start` หรือ `!rv session end`")

    async def _session_start(self, ctx: _Ctx) -> BridgeResult:
        # Resolve + authorize (owner only), gather attendance, ensure a location.
        async with self.db.unit_of_work() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._reply(ctx.inbound, "ยังไม่มีแคมเปญในห้องนี้")
            owner = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            if owner is None or owner.role != MemberRole.OWNER.value:
                return self._reply(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้นที่เริ่มเซสชันได้")
            if await SessionService(s).get_active_session(campaign.id):
                return self._reply(ctx.inbound, "มีเซสชันที่กำลังเล่นอยู่แล้ว")

            members = await camp.list_members(campaign.id)
            attending, participants = [], []
            for m in members:
                char = await CharacterService(s).get_active_character(m)
                if char is not None:
                    attending.append(m.id)
                    participants.append(f"character:{char.id}")
            if not attending:
                return self._reply(ctx.inbound, "ยังไม่มีใครมีตัวละคร — `!rv character <ชื่อ> [คลาส]`")

            location = await LocationService(s).create_location(
                campaign_id=campaign.id, name="โรงเตี๊ยมหมาป่าเทา",
                description_obvious="โรงเตี๊ยมไม้เก่า ไฟในเตาผิงกำลังลุก มีคนนั่งกระจายอยู่ไม่กี่โต๊ะ",
            )
            campaign_id, location_id = campaign.id, location.id

        opener = SessionOpeningService(self.db, self.provider)
        opening = await opener.open_new_session(
            campaign_id=campaign_id, attendance_member_ids=attending, location_id=location_id,
            scene_purpose="เริ่มการผจญภัย หาเบาะแสแรกในเมือง",
            dramatic_question="พวกเขาจะเริ่มต้นจากตรงไหน",
            participants=participants, mode=SceneMode.EXPLORATION,
        )
        parts = [f"🎲 **เซสชันที่ {opening.number} เริ่มแล้ว**", "", opening.opening_text]
        if opening.reminders:
            parts += ["", "*เตือนความจำ:* " + "; ".join(opening.reminders)]
        parts += ["", "พิมพ์การกระทำของตัวละครโดยขึ้นต้นด้วย `!`"]
        return self._reply(ctx.inbound, "\n".join(parts))

    async def _session_end(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.session() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._reply(ctx.inbound, "ยังไม่มีแคมเปญในห้องนี้")
            owner = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            if owner is None or owner.role != MemberRole.OWNER.value:
                return self._reply(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้นที่จบเซสชันได้")
            active = await SessionService(s).get_active_session(campaign.id)
            if active is None:
                return self._reply(ctx.inbound, "ไม่มีเซสชันที่กำลังเล่นอยู่")
            campaign_id, session_id = campaign.id, active.id

        closing = await SessionClosingService(self.db, self.provider).close_session(
            campaign_id=campaign_id, session_id=session_id
        )
        artifacts = await PostSessionService(self.db, self.provider).run(
            campaign_id=campaign_id, session_id=session_id
        )
        return self._reply(
            ctx.inbound,
            "🏁 **จบเซสชัน**\n\n" + artifacts.player_summary + "\n\n" + closing.recap_text,
        )

    async def _status(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.session() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._reply(ctx.inbound, "ยังไม่มีแคมเปญในห้องนี้ — `!rv campaign new <ชื่อ>`")
            members = await camp.list_members(campaign.id)
            active = await SessionService(s).get_active_session(campaign.id)
            lines = [f"**{campaign.name}** — สถานะ {campaign.status}",
                     f"เซสชันที่กำลังเล่น: {'มี' if active else 'ไม่มี'}",
                     f"สมาชิก ({len(members)}):"]
            for m in members:
                char = await CharacterService(s).get_active_character(m)
                who = "เจ้าของโต๊ะ" if m.role == MemberRole.OWNER.value else "ผู้เล่น"
                cinfo = f"{char.name} ({char.char_class}) HP {char.hp}/{char.max_hp}" if char else "ยังไม่มีตัวละคร"
                lines.append(f"• {who}: {cinfo}")
        return self._reply(ctx.inbound, "\n".join(lines))

    # --- util ----------------------------------------------------------------
    @staticmethod
    def _reply(inbound: InboundMessage, text: str) -> BridgeResult:
        return BridgeResult(handled=True, responses=[OutboundMessage(inbound.channel_id, text)])
