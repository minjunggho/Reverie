"""InventoryService — the only sanctioned mutator of the item ledger.

Grants/removals emit ITEM_GAINED / ITEM_LOST events in the caller's transaction.
Class starting gear gives new characters something real to inspect and lets the
DM reference actual equipment instead of a vacuum.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.ids import entity_ref
from app.models.character import Character
from app.models.enums import EventType, Visibility
from app.models.items import InventoryEntry, ItemDefinition
from app.services.events import EventService

# name -> (kind, description). Small, Thai-facing, per supported class.
STARTING_GEAR: dict[str, list[tuple[str, str, str]]] = {
    "fighter": [("ดาบยาว", "weapon", "ดาบยาวใบตรง คมทั้งสองด้าน"),
                ("เกราะโซ่", "armor", "เกราะโซ่ถักหนัก เสียงกริ่งเบาๆ เวลาเดิน"),
                ("โล่ไม้หุ้มเหล็ก", "armor", "โล่กลม รอยฟันเต็มขอบ")],
    "rogue": [("มีดสั้นคู่", "weapon", "มีดสั้นสองเล่ม ซ่อนในแขนเสื้อได้"),
              ("ชุดเครื่องมืองัดแงะ", "gear", "เหล็กงัด คีมเล็ก และตะขอสารพัดขนาด"),
              ("เสื้อคลุมสีหม่น", "gear", "สีเทาหม่นกลืนกับเงา")],
    "wizard": [("ไม้เท้าเวท", "weapon", "ไม้เท้าเรียบ ปลายฝังหินสีน้ำเงิน"),
               ("ตำราคาถา", "gear", "สมุดปกหนังจดคาถาลายมือหวัดๆ"),
               ("ถุงส่วนผสมเวท", "gear", "สมุนไพร ผงกำมะถัน และของแปลกๆ")],
    "cleric": [("กระบองศึก", "weapon", "กระบองหัวเหล็ก หนักแต่ไว้ใจได้"),
               ("ตราศักดิ์สิทธิ์", "gear", "เหรียญสลักตราเทพที่นับถือ"),
               ("ชุดปฐมพยาบาล", "consumable", "ผ้าพันแผล ยาสมุนไพร เข็มกับด้าย")],
    "ranger": [("ธนูยาว", "weapon", "ธนูไม้เหนียว สายตึงกำลังดี"),
               ("ซองธนู (ลูก 20)", "gear", "ลูกธนูขนนกเทา 20 ดอก"),
               ("มีดล่าสัตว์", "weapon", "มีดใบโค้ง ใช้ถลกหนังและป้องกันตัว")],
    "bard": [("พิณเล็ก", "gear", "พิณไม้เชอร์รี เสียงหวานกว่าหน้าตา"),
             ("มีดสั้น", "weapon", "มีดสั้นธรรมดา เผื่อเพลงเอาไม่อยู่"),
             ("ชุดเสื้อผ้างามพอตัว", "gear", "ไว้เข้าที่ที่ต้องดูดี")],
}


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = EventService(session)

    async def _get_or_create_definition(
        self, *, campaign_id: str, name: str, kind: str = "gear", description: str = ""
    ) -> ItemDefinition:
        existing = (
            await self.session.execute(
                select(ItemDefinition).where(
                    ItemDefinition.campaign_id == campaign_id, ItemDefinition.name == name
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing
        item = ItemDefinition(campaign_id=campaign_id, name=name, kind=kind,
                              description=description)
        self.session.add(item)
        await self.session.flush()
        return item

    async def grant(
        self, *, character_id: str, name: str, kind: str = "gear", description: str = "",
        quantity: int = 1, equipped: bool = False, session_id: str | None = None,
        scene_id: str | None = None, record_event: bool = True,
    ) -> InventoryEntry:
        if quantity < 1:
            raise ValidationError("quantity must be >= 1")
        character = await self.session.get(Character, character_id)
        if character is None:
            raise NotFoundError(f"character {character_id} not found")
        item = await self._get_or_create_definition(
            campaign_id=character.campaign_id, name=name, kind=kind, description=description
        )
        entry = (
            await self.session.execute(
                select(InventoryEntry).where(
                    InventoryEntry.character_id == character_id,
                    InventoryEntry.item_definition_id == item.id,
                )
            )
        ).scalars().first()
        if entry is None:
            entry = InventoryEntry(character_id=character_id, item_definition_id=item.id,
                                   quantity=quantity, equipped=equipped)
            self.session.add(entry)
        else:
            entry.quantity += quantity
        await self.session.flush()
        if record_event:
            await self.events.record(
                campaign_id=character.campaign_id, session_id=session_id, scene_id=scene_id,
                event_type=EventType.ITEM_GAINED,
                actor_entity=entity_ref("character", character_id),
                visibility=Visibility.PARTY,
                payload={"item": name, "quantity": quantity,
                         "summary": f"{character.name} ได้รับ {name}" +
                                    (f" x{quantity}" if quantity > 1 else "")},
                narrative_significance=15,
            )
        return entry

    async def grant_starting_gear(self, *, character: Character) -> list[str]:
        """Starting equipment at character creation. Not evented (setup, not play)."""
        granted = []
        for name, kind, desc in STARTING_GEAR.get(character.char_class, []):
            await self.grant(character_id=character.id, name=name, kind=kind,
                             description=desc, equipped=(kind in ("weapon", "armor")),
                             record_event=False)
            granted.append(name)
        return granted

    async def transfer(
        self, *, from_character_id: str, to_character_id: str, name: str,
        quantity: int = 1, session_id: str | None = None, scene_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> InventoryEntry:
        """Hand an item from one character to another — transactional, authoritative,
        exactly-once. Validates: both characters exist in the SAME campaign; the
        receiver is physically present (authoritative co-location, not narration);
        the sender actually possesses enough; and a repeated ``idempotency_key``
        (a duplicated Discord message) returns without duplicating the item.
        Ownership changes and the canonical ITEM_TRANSFERRED event commit together —
        narration may only ever claim a hand-over that this method committed."""
        if quantity < 1:
            raise ValidationError("quantity must be >= 1")
        sender = await self.session.get(Character, from_character_id)
        receiver = await self.session.get(Character, to_character_id)
        if sender is None or receiver is None:
            raise NotFoundError("sender or receiver not found")
        if sender.id == receiver.id:
            raise ValidationError("cannot transfer an item to oneself")
        if sender.campaign_id != receiver.campaign_id:
            raise NotFoundError("receiver is not in this campaign")
        # Presence is the authoritative co-location invariant: tracked positions must
        # match. (Both-untracked predates position tracking and is not blocked.)
        if (sender.location_id is not None and receiver.location_id is not None
                and sender.location_id != receiver.location_id):
            raise ValidationError(
                f"{receiver.name} ไม่ได้อยู่ตรงนี้ — ต้องอยู่ที่เดียวกันจึงจะส่งของให้กันได้")

        # Exactly-once: the same triggering input can never duplicate the item.
        if idempotency_key is not None:
            from app.models.event import Event

            prior = (await self.session.execute(select(Event).where(
                Event.campaign_id == sender.campaign_id,
                Event.event_type == EventType.ITEM_TRANSFERRED.value,
            ))).scalars().all()
            for ev in prior:
                if (ev.payload or {}).get("idempotency_key") == idempotency_key:
                    entry = (await self.session.execute(select(InventoryEntry).join(
                        ItemDefinition,
                        ItemDefinition.id == InventoryEntry.item_definition_id,
                    ).where(InventoryEntry.character_id == to_character_id,
                            ItemDefinition.name == name))).scalars().first()
                    if entry is not None:
                        return entry

        # The sender must actually possess the item (by its canonical name).
        row = (await self.session.execute(
            select(InventoryEntry, ItemDefinition)
            .join(ItemDefinition, ItemDefinition.id == InventoryEntry.item_definition_id)
            .where(InventoryEntry.character_id == from_character_id,
                   ItemDefinition.name == name)
        )).first()
        if row is None:
            raise ValidationError(f"{sender.name} ไม่มี {name} อยู่กับตัว")
        sender_entry, item = row
        if sender_entry.quantity < quantity:
            raise ValidationError(
                f"{sender.name} มี {name} แค่ {sender_entry.quantity} ชิ้น")

        sender_entry.quantity -= quantity
        if sender_entry.quantity <= 0:
            await self.session.delete(sender_entry)
        receiver_entry = (await self.session.execute(select(InventoryEntry).where(
            InventoryEntry.character_id == to_character_id,
            InventoryEntry.item_definition_id == item.id,
        ))).scalars().first()
        if receiver_entry is None:
            receiver_entry = InventoryEntry(
                character_id=to_character_id, item_definition_id=item.id,
                quantity=quantity)
            self.session.add(receiver_entry)
        else:
            receiver_entry.quantity += quantity
        await self.session.flush()

        payload = {"item": name, "quantity": quantity,
                   "from": sender.name, "to": receiver.name,
                   "summary": f"{sender.name} ส่ง {name} ให้ {receiver.name}"}
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        await self.events.record(
            campaign_id=sender.campaign_id, session_id=session_id, scene_id=scene_id,
            event_type=EventType.ITEM_TRANSFERRED,
            actor_entity=entity_ref("character", from_character_id),
            target_entities=[entity_ref("character", to_character_id)],
            location_id=sender.location_id, visibility=Visibility.PARTY,
            payload=payload, narrative_significance=20,
        )
        return receiver_entry

    async def list_inventory(self, character_id: str) -> list[tuple[InventoryEntry, ItemDefinition]]:
        rows = await self.session.execute(
            select(InventoryEntry, ItemDefinition)
            .join(ItemDefinition, ItemDefinition.id == InventoryEntry.item_definition_id)
            .where(InventoryEntry.character_id == character_id)
            .order_by(InventoryEntry.equipped.desc(), ItemDefinition.name)
        )
        return [(e, i) for e, i in rows.all()]
