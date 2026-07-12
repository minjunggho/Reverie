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

from sqlalchemy import select

from app.core.errors import ReverieError
from app.discord_bridge.dto import BridgeResult, InboundMessage, OutboundMessage
from app.entities.directory import normalize_name
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.models.enums import MemberRole, SceneMode
from app.presentation import MessageKind
from app.services.campaigns import CampaignService, CharacterService
from app.services.campaigns.inventory_service import InventoryService
from app.services.campaigns.presets import CLASS_PRESETS, CLASS_TH
from app.services.sessions.closing_service import SessionClosingService
from app.services.sessions.opening_service import SessionOpeningService
from app.services.sessions.post_session_service import PostSessionService
from app.services.sessions.session_service import SessionService
from app.services.views import (
    build_character_sheet,
    build_inventory_view,
    build_journal_view,
    build_party_view,
    build_skill_explain,
    build_spells_view,
)
from app.world import LocationService, PositionService

ADMIN_PREFIX = "!rv"

HELP_LINES = (
    "`!rv campaign new <ชื่อ>` — เปิดโต๊ะใหม่ในห้องนี้",
    "`!rv campaign create <ไอเดีย>` — (เจ้าของโต๊ะ) ให้ AI เสนอโลกทั้งใบจากไอเดียสั้นๆ",
    "`!rv campaign import` — (เจ้าของโต๊ะ) นำเข้าโลกจากไฟล์ .md/.json ของเจ้าเอง",
    "`!rv setup` — (เจ้าของโต๊ะ) ตั้งโทนและสไตล์ของโต๊ะ (Session Zero)",
    "`!rv join` — นั่งร่วมโต๊ะ",
    "`!rv character` — สร้างตัวละครแบบคุยกัน (แนะนำ) · หรือ `!rv character <ชื่อ> <คลาส>`",
    "`!rv resume` — เปิดแบบร่างตัวละครที่สร้างค้างไว้จากขั้นตอนเดิม",
    "`!rv session start` / `!rv session end` — เริ่ม/จบเซสชัน (เจ้าของโต๊ะ)",
    "`!rv sheet` · `!rv spells` · `!rv inventory` · `!rv journal` · `!rv party` — ดูตัวละคร/คาถา/ของ/บันทึก/ปาร์ตี้",
    "`!rv wallet` — ถุงเงินของตัวละคร · `!rv time` — เวลาในโลก",
    "`!rv follow <ชื่อตัวละคร>` / `!rv unfollow` — ตามเดินทางหรือหยุดตามตัวละครนั้น",
    "`!rv skill <ชื่อ>` — ทำไมทักษะนี้ถึงได้เท่านี้",
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
                "resume": self._resume,
                "session": self._session,
                "sheet": self._sheet,
                "spells": self._spells,
                "skill": self._skill,
                "inventory": self._inventory,
                "journal": self._journal,
                "party": self._party,
                "status": self._party,  # old alias
                "wallet": self._wallet,
                "time": self._time,
                "follow": self._follow,
                "unfollow": self._unfollow,
                "diagnostics": self._diagnostics,
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
        sub = ctx.args[0].lower() if ctx.args else ""
        if sub == "import":
            return await self._campaign_import(ctx)
        if sub == "create":
            return await self._campaign_create(ctx)
        if sub != "new":
            return self._notice(ctx.inbound, (
                "ใช้ `!rv campaign new <ชื่อ>` เปิดโต๊ะ · "
                "`!rv campaign create <ไอเดีย>` ให้ AI เสนอโลก · "
                "หรือแนบไฟล์กับ `!rv campaign import`"))
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

    async def _campaign_import(self, ctx: _Ctx) -> BridgeResult:
        from app.services.campaigns.canon_import import CanonImportService

        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "Create the campaign before importing its world.")
        if member is None or member.role != MemberRole.OWNER.value:
            return self._notice(ctx.inbound, "Only the campaign owner can import or approve DM canon.")
        from app.models.location import Location
        from sqlalchemy import select

        operation = ctx.args[1].lower() if len(ctx.args) > 1 else "upload"
        if operation in {"approve", "reject", "repair"}:
            if len(ctx.args) < 3:
                return self._notice(ctx.inbound, f"Use `!rv campaign import {operation} <import-id>`")
            async with self.db.unit_of_work() as s:
                svc = CanonImportService(s)
                if operation == "approve":
                    review = await svc.approve(import_id=ctx.args[2], campaign_id=campaign.id)
                    names = list((await s.execute(
                        select(Location.name).where(Location.campaign_id == campaign.id))).scalars())
                    body = ("โลกของแคมเปญถูกยืนยันเป็น canon แล้ว\n\n"
                            + "สถานที่: " + ", ".join(names) + "\n"
                            + " · ".join(f"{k}={v}" for k, v in review.counts.items() if v))
                    return BridgeResult(handled=True, responses=[OutboundMessage(
                        ctx.inbound.channel_id, body, kind=MessageKind.TABLE_NOTICE,
                        title="Campaign imported ✓")])
                if operation == "repair":
                    result = await svc.repair_protocols(import_id=ctx.args[2], campaign_id=campaign.id)
                    return self._notice(
                        ctx.inbound,
                        f"Protocol backfill complete — {result['protocols_added']} added. "
                        "Locations/NPCs/secrets/threats were not touched.")
                await svc.reject(import_id=ctx.args[2], campaign_id=campaign.id)
                return self._notice(ctx.inbound, "Import rejected; no world canon was created.")
        if len(ctx.inbound.attachments) != 1:
            return self._notice(ctx.inbound, "Attach exactly one UTF-8 `.json`, `.md`, or `.txt` file.")
        attachment = ctx.inbound.attachments[0]
        async with self.db.unit_of_work() as s:
            row = await CanonImportService(s).create_draft(
                campaign_id=campaign.id, uploader_member_id=member.id,
                filename=attachment.filename, data=attachment.data,
            )
            review = row.proposal.get("_review", {})
            locations = row.proposal.get("locations", [])
        counts = review.get("counts", {})
        warnings = review.get("warnings", [])
        count_line = " · ".join(f"{k}: {v}" for k, v in counts.items() if v)
        loc_preview = "\n".join(f"• {x['name']}" for x in locations[:12])
        warn_block = ("\n\n⚠️ WARNINGS\n" + "\n".join(f"- {w}" for w in warnings[:12])) if warnings else ""
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id,
            f"Parsed `{attachment.filename}`. Nothing is canon yet.\n\n{count_line}\n\n"
            f"{loc_preview}{warn_block}\n\n"
            f"Confirm: `!rv campaign import approve {row.id}`\n"
            f"Cancel: `!rv campaign import reject {row.id}`",
            kind=MessageKind.TABLE_NOTICE, title="Campaign import review",
        )])

    async def _campaign_create(self, ctx: _Ctx) -> BridgeResult:
        """AI-assisted campaign creation (§2): one short premise → a reviewable world
        proposal. Same review/approve lifecycle as `!rv campaign import` — nothing
        becomes canon until the owner approves."""
        from app.ai.jobs.campaign_creator import propose_campaign_world
        from app.services.campaigns.canon_import import CanonImportService

        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "เปิดโต๊ะก่อน: `!rv campaign new <ชื่อ>`")
        if member is None or member.role != MemberRole.OWNER.value:
            return self._notice(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้นที่สร้างโลกแคมเปญได้")
        premise = " ".join(ctx.args[1:]).strip()
        if len(premise) < 10:
            return self._notice(ctx.inbound, (
                "เล่าไอเดียแคมเปญมาหน่อย เช่น\n"
                "`!rv campaign create แคมเปญดาร์กแฟนตาซี ที่ขุนนางผูกขาดเวทมนตร์ "
                "และสามัญชนถูกห้ามเรียน ผู้เล่นเริ่มจากจน ไร้ชื่อเสียง และหมดทางเลือก`"))

        proposal, review = await propose_campaign_world(
            self.provider, premise=premise, campaign_name=campaign.name,
            table_profile=(campaign.config or {}).get("profile", {}),
        )
        async with self.db.unit_of_work() as s:
            row = await CanonImportService(s).create_ai_draft(
                campaign_id=campaign.id, uploader_member_id=member.id,
                premise=premise, proposal=proposal,
            )
            row_id = row.id

        start_key = proposal.starting_location or "-"
        loc_lines = []
        for x in proposal.locations[:12]:
            mark = " ⭐ จุดเริ่มต้น" if x.key == start_key else ""
            loc_lines.append(f"• {x.name}{mark}")
        npc_line = ", ".join(n.name for n in proposal.npcs[:8]) or "-"
        threat_line = ", ".join(t.name for t in proposal.threats[:6]) or "-"
        warn_block = ("\n\n⚠️ ข้อควรรู้\n" + "\n".join(f"- {w}" for w in review.warnings[:8])
                      ) if review.warnings else ""
        count_line = " · ".join(f"{k}: {v}" for k, v in review.counts.items() if v)
        body = (
            f"ข้าเสนอโลกจากไอเดียของเจ้า — **ยังไม่มีอะไรเป็น canon จนกว่าเจ้าจะยืนยัน**\n\n"
            f"**{proposal.identity_name or campaign.name}**\n{proposal.brief}\n\n"
            f"**คำถามใหญ่ของเรื่อง:** {proposal.central_question or '-'}\n\n"
            f"**สถานที่:**\n" + "\n".join(loc_lines) + "\n\n"
            f"**ตัวละครสำคัญ:** {npc_line}\n"
            f"**ภัยที่ขยับอยู่:** {threat_line}\n"
            f"{count_line}{warn_block}\n\n"
            f"ยืนยัน: `!rv campaign import approve {row_id}`\n"
            f"ไม่เอา (แล้วลอง create ใหม่ได้): `!rv campaign import reject {row_id}`"
        )
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id, body, kind=MessageKind.TABLE_NOTICE,
            title="✨ ข้อเสนอโลกแคมเปญ — รอเจ้าของโต๊ะรีวิว",
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
            from app.services.economy import WalletService
            from app.services.economy.wallet_service import format_balances

            char = await CharacterService(s).create_character(
                member_id=member.id, name=name, char_class=char_class,
                abilities=preset["abilities"], proficiencies=preset["proficiencies"],
                max_hp=preset["max_hp"], ac=preset["ac"], set_active=True,
            )
            gear = await InventoryService(s).grant_starting_gear(character=char)
            purse = await WalletService(s).grant_starting_funds(character=char)
        fields = [
            {"name": "สาย", "value": CLASS_TH.get(char_class, char_class), "inline": True},
            {"name": "HP / AC", "value": f"{preset['max_hp']} / {preset['ac']}", "inline": True},
            {"name": "🎒 สัมภาระ", "value": "\n".join(f"• {g}" for g in gear), "inline": False},
            {"name": "💰 ถุงเงิน", "value": format_balances(purse), "inline": False},
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id,
            "-# อยากได้ตัวละครที่มีเรื่องราวลึกกว่านี้ ลอง `!rv character` เฉยๆ แล้วคุยกัน",
            kind=MessageKind.CHARACTER_REVEAL, title=f"🎭 {char.name}",
            data={"fields": fields},
        )])

    async def _resume(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
        if member is None:
            return self._notice(ctx.inbound, "นั่งร่วมโต๊ะก่อนด้วย `!rv join`")
        if self.creation_flow is None:
            return self._notice(
                ctx.inbound, "ระบบสร้างตัวละครแบบคุยกันยังไม่พร้อมในขณะนี้"
            )
        return await self.creation_flow.resume(
            campaign_id=campaign.id,
            member_id=member.id,
            channel_id=ctx.inbound.channel_id,
        )

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

    async def _spells(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if member is None:
            return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")
        async with self.db.session() as s:
            char = await CharacterService(s).get_active_character(member)
            if char is None:
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")
            msg = await build_spells_view(s, character=char,
                                          channel_id=ctx.inbound.channel_id)
        return BridgeResult(handled=True, responses=[msg])

    async def _skill(self, ctx: _Ctx) -> BridgeResult:
        campaign, member = await self._resolve(ctx)
        if member is None:
            return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")
        if not ctx.args:
            return self._notice(ctx.inbound, "ใช้: `!rv skill <ชื่อทักษะ>` เช่น `!rv skill arcana`")
        async with self.db.session() as s:
            char = await CharacterService(s).get_active_character(member)
            if char is None:
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")
            msg = await build_skill_explain(s, character=char, skill=" ".join(ctx.args),
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

    async def _wallet(self, ctx: _Ctx) -> BridgeResult:
        from app.services.economy import WalletService
        from app.services.economy.wallet_service import format_balances

        campaign, member = await self._resolve(ctx)
        if member is None:
            return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")
        async with self.db.unit_of_work() as s:
            char = await CharacterService(s).get_active_character(member)
            if char is None:
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")
            wallets = WalletService(s)
            balances = await wallets.balance(char.id)
            recent = await wallets.recent_transactions(char.id, limit=5)
            lines = []
            for tx in recent:
                sign = {k: v for k, v in (tx.amounts or {}).items()}
                amt = " ".join(f"{'+' if v > 0 else ''}{v} {k}" for k, v in sign.items())
                lines.append(f"• {amt} — {tx.reason or tx.transaction_type}")
            char_name = char.name
        body = f"**{format_balances(balances)}**"
        if lines:
            body += "\n\nรายการล่าสุด:\n" + "\n".join(lines)
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id, body, kind=MessageKind.TABLE_NOTICE,
            title=f"💰 ถุงเงินของ {char_name}",
        )])

    async def _time(self, ctx: _Ctx) -> BridgeResult:
        from app.core.clock import format_game_time_th
        from app.models.location import Location

        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
        async with self.db.session() as s:
            anchor_name = "-"
            if campaign.current_party_anchor_id:
                loc = await s.get(Location, campaign.current_party_anchor_id)
                if loc is not None:
                    anchor_name = loc.name
            active = await SessionService(s).get_active_session(campaign.id)
        lines = [
            f"🕰️ {format_game_time_th(campaign.current_game_time)}",
            f"📍 ปาร์ตี้อยู่แถว: {anchor_name}",
            f"🎲 เซสชัน: {'กำลังเล่นอยู่' if active else 'ยังไม่เริ่ม'}",
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id, "\n".join(lines), kind=MessageKind.TABLE_NOTICE,
            title="เวลาในโลก",
        )])

    async def _follow(self, ctx: _Ctx) -> BridgeResult:
        """Let the requester's active character explicitly consent to follow a PC."""
        wanted = " ".join(ctx.args).strip()
        if not wanted:
            return self._notice(ctx.inbound, "ใช้: `!rv follow <ชื่อตัวละคร>`")

        async with self.db.unit_of_work() as s:
            campaigns = CampaignService(s)
            campaign = await campaigns.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
            member = await campaigns.resolve_member(
                campaign.id, ctx.inbound.author_discord_id)
            if member is None:
                return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")

            follower = await CharacterService(s).get_active_character(member)
            if (
                follower is None
                or follower.campaign_id != campaign.id
                or follower.owner_member_id != member.id
            ):
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")

            # Only active player characters in this campaign are eligible. Resolve
            # names with the same Unicode-safe exact matcher used by scene entities;
            # never accept a substring or a client-supplied character id.
            characters = list((await s.execute(
                select(Character)
                .join(CampaignMember, CampaignMember.active_character_id == Character.id)
                .where(
                    CampaignMember.campaign_id == campaign.id,
                    Character.campaign_id == campaign.id,
                )
            )).scalars().unique())
            normalized = normalize_name(wanted)
            matches = [c for c in characters if normalize_name(c.name) == normalized]
            if not matches:
                matches = [
                    c for c in characters
                    if normalized in {normalize_name(alias) for alias in (c.aliases or [])}
                ]

            if not matches:
                nearby = sorted({
                    c.name for c in characters
                    if c.id != follower.id
                    and follower.location_id is not None
                    and c.location_id == follower.location_id
                })
                hint = (
                    " ตัวละครที่อยู่ด้วยกันตอนนี้: " + ", ".join(nearby)
                    if nearby else " ตอนนี้ไม่มีตัวละครอื่นอยู่ที่เดียวกัน"
                )
                return self._notice(
                    ctx.inbound,
                    f"ไม่พบตัวละครชื่อ **{wanted}** ในแคมเปญนี้.{hint}",
                )
            if len(matches) > 1:
                names = sorted({c.name for c in matches})
                detail = ", ".join(names) if len(names) > 1 else names[0]
                return self._notice(
                    ctx.inbound,
                    f"ชื่อ **{wanted}** ตรงกับตัวละครมากกว่าหนึ่งคน ({detail}) — "
                    "โปรดใช้ชื่อเต็มหรือนามแฝงที่ไม่ซ้ำกัน",
                )

            leader = matches[0]
            if leader.id == follower.id:
                return self._notice(ctx.inbound, f"**{follower.name}** ไม่สามารถตามตัวเองได้")
            if (
                follower.location_id is None
                or leader.location_id != follower.location_id
            ):
                return self._notice(
                    ctx.inbound,
                    f"**{follower.name}** ต้องอยู่ที่เดียวกับ **{leader.name}** ก่อนจึงจะเริ่มตามได้",
                )

            await PositionService(s).set_follow(
                follower_id=follower.id, leader_id=leader.id)
            follower_name, leader_name = follower.name, leader.name

        return self._notice(
            ctx.inbound,
            f"**{follower_name}** จะเดินทางตาม **{leader_name}** ตราบใดที่ทั้งคู่ยังอยู่ด้วยกัน",
        )

    async def _unfollow(self, ctx: _Ctx) -> BridgeResult:
        """Clear only the requester's active character follow consent."""
        if ctx.args:
            return self._notice(
                ctx.inbound,
                "ใช้: `!rv unfollow` — คำสั่งนี้หยุดตามให้เฉพาะตัวละครของเจ้า",
            )
        async with self.db.unit_of_work() as s:
            campaigns = CampaignService(s)
            campaign = await campaigns.resolve_campaign_by_channel(ctx.inbound.channel_id)
            if campaign is None:
                return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
            member = await campaigns.resolve_member(
                campaign.id, ctx.inbound.author_discord_id)
            if member is None:
                return self._notice(ctx.inbound, "ยังไม่ได้ร่วมโต๊ะ — `!rv join`")

            follower = await CharacterService(s).get_active_character(member)
            if (
                follower is None
                or follower.campaign_id != campaign.id
                or follower.owner_member_id != member.id
            ):
                return self._notice(ctx.inbound, "ยังไม่มีตัวละคร — `!rv character`")

            leader = None
            if follower.following_character_id:
                candidate = await s.get(Character, follower.following_character_id)
                if candidate is not None and candidate.campaign_id == campaign.id:
                    leader = candidate
            if follower.following_character_id is None:
                return self._notice(
                    ctx.inbound, f"**{follower.name}** ไม่ได้กำลังตามใครอยู่")

            await PositionService(s).stop_follow(follower_id=follower.id)
            follower_name = follower.name
            leader_name = leader.name if leader is not None else None

        if leader_name is not None:
            text = f"**{follower_name}** ไม่ได้ตาม **{leader_name}** แล้ว"
        else:
            text = f"**{follower_name}** หยุดตามแล้ว"
        return self._notice(ctx.inbound, text)

    async def _diagnostics(self, ctx: _Ctx) -> BridgeResult:
        """Owner-only deploy verification (E7 §23). Never exposes secrets."""
        from sqlalchemy import text as _sql_text

        from app.core.config import get_settings
        from app.core.versions import (
            IMPORTER_VERSION,
            MEMORY_SYSTEM_VERSION,
            PROCESS_STARTED_AT,
            PROMPT_VERSION,
            WORLD_MODEL_VERSION,
            git_sha,
            rules_content_hash,
        )
        from app.rules_content import get_registry
        from app.rules_content.registry import RULESET_ID

        campaign, member = await self._resolve(ctx)
        if campaign is None:
            return self._notice(ctx.inbound, "ยังไม่มีโต๊ะในห้องนี้")
        if member is None or member.role != MemberRole.OWNER.value:
            return self._notice(ctx.inbound, "เฉพาะเจ้าของโต๊ะเท่านั้น")

        settings = get_settings()
        migration_head = "-"
        async with self.db.session() as s:
            try:
                migration_head = (await s.execute(
                    _sql_text("SELECT version_num FROM alembic_version"))).scalar() or "-"
            except Exception:  # noqa: BLE001 - table absent under create_all()
                migration_head = "(schema via create_all)"
        reg = get_registry()
        lines = [
            f"git: `{git_sha()}` · started: {PROCESS_STARTED_AT}",
            f"db migration head: `{migration_head}`",
            f"llm: {settings.llm_provider} / {settings.llm_model}",
            f"prompts: v{PROMPT_VERSION} · importer: v{IMPORTER_VERSION} · memory: v{MEMORY_SYSTEM_VERSION}",
            f"world model: engine v{WORLD_MODEL_VERSION} · campaign v{campaign.world_model_version}",
            f"rules: {RULESET_ID} v{reg.rules_content_version} · content `{rules_content_hash()}` · "
            f"classes={len(reg.classes)} subclasses={len(reg.subclasses)} "
            f"species={len(reg.species)} backgrounds={len(reg.backgrounds)} "
            f"spells={len(reg.spells)}",
        ]
        return BridgeResult(handled=True, responses=[OutboundMessage(
            ctx.inbound.channel_id, "\n".join(lines), kind=MessageKind.TABLE_NOTICE,
            title="🔎 Reverie diagnostics",
        )])

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

            # `!rv session start at <ชื่อสถานที่>` — the owner picks the opening
            # location explicitly (and it becomes the campaign's canonical start).
            if len(ctx.args) >= 2 and ctx.args[1].lower() in ("at", "ที่"):
                wanted = " ".join(ctx.args[2:]).strip()
                named = await LocationService(s).find_by_name(campaign.id, wanted)
                if named is None:
                    return self._notice(
                        ctx.inbound, f"ไม่รู้จักสถานที่ชื่อ '{wanted}' ในแคมเปญนี้")
                if campaign.starting_location_id is None:
                    campaign.starting_location_id = named.id
                campaign.current_party_anchor_id = named.id

            campaign_id = campaign.id

        opener = SessionOpeningService(self.db, self.provider)
        location_id = await opener.resolve_opening_location(
            campaign_id=campaign_id, attendance_member_ids=attending)
        if location_id is None:
            # SETUP INCOMPLETE — never invent a universal tavern. The world is
            # the owner's: import it, create it with AI, or name the start.
            return self._notice(ctx.inbound, (
                "โต๊ะพร้อมแล้ว แต่แคมเปญยังไม่มี 'จุดเริ่มต้นของโลก'\n\n"
                "เลือกหนึ่งอย่างก่อนเริ่มเซสชัน:\n"
                "• `!rv campaign create <เล่าไอเดียแคมเปญสั้นๆ>` — ให้ข้าเสนอโลกให้รีวิว\n"
                "• `!rv campaign import` (แนบไฟล์ .md/.json) — นำเข้าโลกที่เจ้าเขียนเอง\n"
                "• `!rv session start at <ชื่อสถานที่>` — เมื่อแคมเปญมีสถานที่อยู่แล้ว"))
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
