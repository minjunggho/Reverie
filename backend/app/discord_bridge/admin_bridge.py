"""AdminBridge — table-setup commands (campaign/character/session lifecycle).

`!rv ...` commands are how the table gets set up and inspected. They are routed
BEFORE the game bridge, so a leading `!rv` is never mistaken for a committed `!`
character action. No game logic lives here — every command calls engine services,
and every reply is a kinded message the adapter renders.

Design rule from the experience overhaul: replies read like a table being set, not
like CRUD receipts. Bare `!rv` is a context-aware welcome that tells you the next
step for YOUR table's current state.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from app.core.errors import ReverieError
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.models.enums import MemberRole, SceneMode
from app.presentation import MessageKind
from app.services.campaigns import CampaignService, CharacterService
from app.services.campaigns.inventory_service import InventoryService
from app.services.campaigns.presets import CLASS_PRESETS, CLASS_TH
from app.services.sessions import (
    PostSessionService,
    SessionClosingService,
    SessionOpeningService,
    SessionService,
)
from app.services.views import (
    build_character_sheet,
    build_inventory_view,
    build_journal_view,
    build_party_view,
)
from app.world import LocationService

ADMIN_PREFIX = "!rv"

HELP_LINES = (
    "`!rv campaign new <ชื่อ>` — เปิดโต๊ะใหม่ในห้องนี้",
    "`!rv setup` — (เจ้าของโต๊ะ) ตั้งโทนและสไตล์ของโต๊ะ (Session Zero)",
    "`!rv join` — นั่งร่วมโต๊ะ",
    "`!rv character` — สร้างตัวละครแบบคุยกัน (แนะนำ) · หรือ `!rv character <ชื่อ> <คลาส>`",
    "`!rv session start` / `!rv session end` — เริ่ม/จบเซสชัน (เจ้าของโต๊ะ)",
    "`!rv sheet` · `!rv inventory` · `!rv journal` · `!rv party` — ดูตัวละคร/ของ/บันทึก/ปาร์ตี้",
)


@dataclass
class _Ctx:
    inbound: InboundMessage
    args: list[str]


def is_admin_command(content: str) -> bool:
    c = content.strip()
    return c == ADMIN_PREFIX or c.startswith(ADMIN_PREFIX + " ")


class AdminBridge:
    def __init__(self, db, provider, *, creation_flow=None, session_zero=None) -> None:
        self.db = db
        self.provider = provider
        self.creation_flow = creation_flow
        self.session_zero = session_zero

    async def handle(self, inbound: InboundMessage) -> BridgeResult:
        try:
            args = shlex.split(inbound.content.strip()[len(ADMIN_PREFIX):].strip())
        except ValueError:
            args = inbound.content.strip()[len(ADMIN_PREFIX):].strip().split()
        if not args or args[0] in ("help", "?"):
            return await self._welcome(inbound)

        cmd, rest = args[0].lower(), args[1:]
        ctx = _Ctx(inbound=inbound, args=rest)
        try:
            handler = {
                "campaign": self._campaign,
                "setup": self._setup,
                "join": self._join,
                "character": self._character,
                "session": self._session,
                "sheet": self._sheet,
                "inventory": self._inventory,
                "journal": self._journal,
                "party": self._party,
                "status": self._party,  # old alias
            }.get(cmd)
            if handler is None:
                return self._notice(inbound, f"ไม่รู้จักคำสั่ง `{cmd}` — พิมพ์ `!rv` เพื่อดูว่าทำอะไรได้บ้าง")
            return await handler(ctx)
        except ReverieError as exc:
            return self._notice(inbound, f"⚠️ {exc}")

    # --- welcome (context-aware onboarding) -----------------------------------
    async def _welcome(self, inbound: InboundMessage) -> BridgeResult:
        async with self.db.session() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(inbound.channel_id)
            member = char = active = None
            if campaign is not None:
                member = await camp.resolve_member(campaign.id, inbound.author_discord_id)
                if member is not None:
                    char = await CharacterService(s).get_active_character(member)
                active = await SessionService(s).get_active_session(campaign.id)

        if campaign is None:
            next_step = "เริ่มจากเปิดโต๊ะ: `!rv campaign new <ชื่อแคมเปญ>`"
        elif member is None:
            next_step = f"โต๊ะ **{campaign.name}** เปิดอยู่ — นั่งร่วมโต๊ะด้วย `!rv join`"
        elif char is None:
            next_step = "เจ้ายังไม่มีตัวละคร — พิมพ์ `!rv character` แล้วมาสร้างด้วยกัน"
        elif active is None:
            next_step = f"ทุกอย่างพร้อม — เจ้าของโต๊ะพิมพ์ `!rv session start` ได้เลย ({char.name} รออยู่)"
        else:
            next_step = f"เซสชันกำลังเล่นอยู่ — พิมพ์การกระทำของ {char.name} ขึ้นต้นด้วย `!`"

        body = (
            "ข้าคือ Reverie — Dungeon Master ประจำโต๊ะนี้\n"
            "คุยกันตามปกติได้เลย ข้าฟังอยู่ · อยากให้ตัวละคร 'ลงมือทำ' เมื่อไหร่ "
            "ให้ขึ้นต้นข้อความด้วย `!`\n\n"
            f"**ตอนนี้:** {next_step}"
        )
        return BridgeResult(handled=True, responses=[OutboundMessage(
            inbound.channel_id, body, kind=MessageKind.REVERIE_WELCOME,
            title="Reverie", data={"fields": [
                {"name": "คำสั่งทั้งหมด", "value": "\n".join(HELP_LINES), "inline": False},
            ]},
        )])

    # --- campaign / setup / join ------------------------------------------------
    async def _campaign(self, ctx: _Ctx) -> BridgeResult:
        if not ctx.args or ctx.args[0].lower() != "new":
            return self._notice(ctx.inbound, "ใช้: `!rv campaign new <ชื่อ>`")
        name = " ".join(ctx.args[1:]).strip() or "แคมเปญไร้ชื่อ"
        async with self.db.unit_of_work() as s:
            svc = CampaignService(s)
            if await svc.resolve_campaign_by_channel(ctx.inbound.channel_id):
                return self._notice(ctx.inbound, "ห้องนี้มีโต๊ะอยู่แล้ว — `!rv` เพื่อดูสถานะ")
            campaign = await svc.create_campaign(
                name=name, discord_guild_id=ctx.inbound.guild_id,
                game_channel_id=ctx.inbound.channel_id,
                owner_discord_user_id=ctx.inbound.author_discord_id,
                owner_display_name=ctx.inbound.author_display_name,
            )
            await svc.activate_campaign(campaign.id)
        body = (
            f"โต๊ะ **{name}** พร้อมแล้ว และ {ctx.inbound.author_display_name} คือเจ้าของโต๊ะ\n\n"
            "ต่อไป:\n"
            "• `!rv setup` — เลือกโทนของโต๊ะกันก่อน (2 นาที แนะนำ)\n"
            "• เพื่อนๆ พิมพ์ `!rv join` แล้ว `!rv character` มาสร้างตัวละครกัน"
        )
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id, body, kind=MessageKind.REVERIE_WELCOME,
            title=f"🕯️ {name}",
        )])

    async def _setup(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้ — `!rv campaign new <ชื่อ>`")
        if member is None or member.role != MemberRole.OWNER.value:
            return self._notice(ctx.inbound, "Session Zero เป็นหน้าที่ของเจ้าของโต๊ะ")
        if self.session_zero is None:
            return self._notice(ctx.inbound, "ระบบ setup ยังไม่พร้อม")
        return await self.session_zero.start(
            campaign_id=campaign.id, channel_id=ctx.inbound.channel_id
        )

    async def _join(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            svc = CampaignService(s)
            campaign = await svc.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้ — `!rv campaign new <ชื่อ>`")
            await svc.add_member(
                campaign_id=campaign.id, discord_user_id=ctx.inbound.author_discord_id,
                display_name=ctx.inbound.author_display_name, role=MemberRole.PLAYER,
            )
        return self._notice(
            ctx.inbound,
            f"ยินดีต้อนรับสู่โต๊ะ, {ctx.inbound.author_display_name} 🪑\n"
            "พิมพ์ `!rv character` แล้วมาสร้างตัวละครกัน — แค่เล่าว่าอยากเป็นใคร",
        )

    # --- character ----------------------------------------------------------------
    async def _character(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
        if member is None:
            return self._notice(ctx.inbound, "นั่งร่วมโต๊ะก่อนด้วย `!rv join`")

        # Guided conversation (the recommended path).
        if not ctx.args:
            if self.creation_flow is None:
                return self._notice(ctx.inbound, "ใช้: `!rv character <ชื่อ> <คลาส>`")
            return await self.creation_flow.start(
                campaign_id=campaign.id, member_id=member.id,
                channel_id=ctx.inbound.channel_id,
            )

        # Quick path: name [+ class]. Still gets gear and a sheet to look at.
        char_class = "fighter"
        name_tokens = ctx.args
        if ctx.args[-1].lower() in CLASS_PRESETS:
            char_class = ctx.args[-1].lower()
            name_tokens = ctx.args[:-1]
        name = " ".join(name_tokens).strip() or "นักผจญภัย"
        preset = CLASS_PRESETS[char_class]
        async with self.db.unit_of_work() as s:
            char = await CharacterService(s).create_character(
                member_id=member.id, name=name, char_class=char_class,
                abilities=preset["abilities"], proficiencies=preset["proficiencies"],
                max_hp=preset["max_hp"], ac=preset["ac"], set_active=True,
            )
            gear = await InventoryService(s).grant_starting_gear(character=char)
        fields = [
            {"name": "สาย", "value": CLASS_TH.get(char_class, char_class), "inline": True},
            {"name": "HP / AC", "value": f"{preset['max_hp']} / {preset['ac']}", "inline": True},
            {"name": "🎒 สัมภาระ", "value": "\n".join(f"• {g}" for g in gear), "inline": False},
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id,
            "-# อยากได้ตัวละครที่มีเรื่องราวลึกกว่านี้ ลอง `!rv character` เฉยๆ แล้วคุยกัน",
            kind=MessageKind.CHARACTER_REVEAL, title=f"🎭 {char.name}",
            data={"fields": fields},
        )])

    # --- views ---------------------------------------------------------------------
    async def _sheet(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if member is None:
            return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")
        async with self.db.session() as s:
            char = await CharacterService(s).get_active_character(member)
            if char is None:
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")
            msg = await build_character_sheet(s, character=char,
                                              channel_id=ctx.inbound.channel_id)
        return BridgeResult(handled=True, responses=[msg])

    async def _inventory(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if member is None:
            return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")
        async with self.db.session() as s:
            char = await CharacterService(s).get_active_character(member)
            if char is None:
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")
            msg = await build_inventory_view(s, character=char,
                                             channel_id=ctx.inbound.channel_id)
        return BridgeResult(handled=True, responses=[msg])

    async def _journal(self, ctx: _Ctx) -> BridgeResult:
        campaign, _ = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
        async with self.db.session() as s:
            msg = await build_journal_view(s, campaign_id=campaign.id,
                                           channel_id=ctx.inbound.channel_id)
        return BridgeResult(handled=True, responses=[msg])

    async def _party(self, ctx: _Ctx) -> BridgeResult:
        campaign, _ = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้ — `!rv campaign new <ชื่อ>`")
        async with self.db.session() as s:
            camp = CampaignService(s)
            members = await camp.list_members(campaign.id)
            chars = CharacterService(s)
            msg = await build_party_view(
                s, members=members, channel_id=ctx.inbound.channel_id,
                get_character=chars.get_active_character,
            )
        return BridgeResult(handled=True, responses=[msg])

    # --- session lifecycle ------------------------------------------------------------
    async def _session(self, ctx: _Ctx) -> BridgeResult:
        sub = (ctx.args[0].lower() if ctx.args else "")
        if sub == "start":
            return await self._session_start(ctx)
        if sub == "end":
            return await self._session_end(ctx)
        return self._notice(ctx.inbound, "ใช้: `!rv session start` หรือ `!rv session end`")

    async def _session_start(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.unit_of_work() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
            owner = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            if owner is None or owner.role != MemberRole.OWNER.value:
                return self._notice(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้นที่เริ่มเซสชันได้")
            if await SessionService(s).get_active_session(campaign.id):
                return self._notice(ctx.inbound, "มีเซสชันที่กำลังเล่นอยู่แล้ว")

            members = await camp.list_members(campaign.id)
            attending, participants = [], []
            for m in members:
                char = await CharacterService(s).get_active_character(m)
                if char is not None:
                    attending.append(m.id)
                    participants.append(f"character:{char.id}")
            if not attending:
                return self._notice(ctx.inbound, "ยังไม่มีใครมีตัวละครเลย — `!rv character` ก่อนนะ")

            # Reuse the campaign's latest location; only invent one when none exists.
            location = await LocationService(s).latest_location(campaign.id)
            if location is None:
                location = await LocationService(s).create_location(
                    campaign_id=campaign.id, name="โรงเตี๊ยมหมาป่าเทา",
                    description_obvious="โรงเตี๊ยมไม้เก่า ไฟในเตาผิงกำลังลุก มีคนนั่งกระจายอยู่ไม่กี่โต๊ะ",
                )
            campaign_id, location_id = campaign.id, location.id

        opener = SessionOpeningService(self.db, self.provider)
        opening = await opener.open_new_session(
            campaign_id=campaign_id, attendance_member_ids=attending,
            location_id=location_id, channel_id=ctx.inbound.channel_id,
            participants=participants, mode=SceneMode.EXPLORATION,
        )
        return BridgeResult(handled=True, responses=opening.messages)

    async def _session_end(self, ctx: _Ctx) -> BridgeResult:
        async with self.db.session() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
            owner = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            if owner is None or owner.role != MemberRole.OWNER.value:
                return self._notice(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้นที่จบเซสชันได้")
            active = await SessionService(s).get_active_session(campaign.id)
            if active is None:
                return self._notice(ctx.inbound, "ไม่มีเซสชันที่กำลังเล่นอยู่")
            campaign_id, session_id = campaign.id, active.id

        closing = await SessionClosingService(self.db, self.provider).close_session(
            campaign_id=campaign_id, session_id=session_id,
            channel_id=ctx.inbound.channel_id,
        )
        # Continuity artifacts + lifecycle -> COMPLETE (private report stays server-side).
        await PostSessionService(self.db, self.provider).run(
            campaign_id=campaign_id, session_id=session_id
        )
        return BridgeResult(handled=True, responses=closing.messages)

    # --- utils -------------------------------------------------------------------------
    async def _resolve(self, ctx: _Ctx):
        async with self.db.session() as s:
            camp = CampaignService(s)
            campaign = await camp.resolve_campaign_by_channel(ctx.inbound.channel_id)
            member = None
            if campaign is not None:
                member = await camp.resolve_member(campaign.id, ctx.inbound.author_discord_id)
            return campaign, member

    @staticmethod
    def _notice(inbound: InboundMessage, text: str) -> BridgeResult:
        return BridgeResult(handled=True, responses=[OutboundMessage(
            inbound.channel_id, text, kind=MessageKind.TABLE_NOTICE,
        )])
