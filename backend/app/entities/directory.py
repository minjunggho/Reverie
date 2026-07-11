"""SceneEntityDirectory + EntityResolver.

Hydrates a scene's entity refs into typed `EntityContext`s and resolves the LLM's
free-text `target_references` to canonical entities — deterministically, at the
engine boundary, once. The LLM extracts language mentions; it never chooses IDs.

Name matching (documented):
- Normalization: Unicode NFC + `str.casefold()` + collapse internal whitespace.
  Thai has no case, so casefold is a safe no-op there; NFC folds composed/decomposed
  sequences so วรรณยุกต์/สระ variants compare equal.
- Precedence: (1) exact canonical-name match, (2) exact alias match. No substring or
  fuzzy matching — unrelated fantasy names must never collapse together.
- Multiple present candidates for one mention → ambiguous (one focused clarification).
- A mention matching a KNOWN party character who is not in this scene resolves to a
  not-present entity; the caller refuses it as a physical target.
- The player's Discord display name is a separate namespace and is never an alias.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import entity_ref, parse_entity_ref
from app.models.campaign import CampaignMember
from app.models.character import Character
from app.models.npc import NPC

PLAYER_CHARACTER = "PLAYER_CHARACTER"
NPC_TYPE = "NPC"


def normalize_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", (name or "")).casefold().split())


@dataclass
class EntityContext:
    entity_ref: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    present_in_scene: bool = True
    visible_to_actor: bool = True
    player_controlled: bool = False
    controller_member_id: str | None = None
    is_actor: bool = False
    observable_state: str = ""

    def names_normalized(self) -> set[str]:
        return {normalize_name(self.canonical_name)} | {normalize_name(a) for a in self.aliases}

    def to_public(self) -> dict:
        """Compact, non-secret form safe to persist in a pending action + rehydrate."""
        return {
            "ref": self.entity_ref, "type": self.entity_type,
            "name": self.canonical_name, "present": self.present_in_scene,
            "player_controlled": self.player_controlled,
            "controller_member_id": self.controller_member_id,
            "observable_state": self.observable_state,
        }

    @classmethod
    def from_public(cls, d: dict) -> "EntityContext":
        return cls(
            entity_ref=d["ref"], entity_type=d["type"], canonical_name=d["name"],
            present_in_scene=d.get("present", True),
            player_controlled=d.get("player_controlled", False),
            controller_member_id=d.get("controller_member_id"),
            observable_state=d.get("observable_state", ""),
        )


@dataclass
class TargetResolution:
    resolved: list[EntityContext] = field(default_factory=list)          # present + matched
    not_present: list[EntityContext] = field(default_factory=list)       # known but absent
    ambiguous: list[tuple[str, list[EntityContext]]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def primary(self) -> EntityContext | None:
        return self.resolved[0] if self.resolved else None


@dataclass
class SceneDirectory:
    actor: EntityContext | None
    entities: list[EntityContext]              # present entities only

    @property
    def present_player_characters(self) -> list[EntityContext]:
        return [e for e in self.entities if e.entity_type == PLAYER_CHARACTER and e.present_in_scene]

    @property
    def present_npcs(self) -> list[EntityContext]:
        return [e for e in self.entities if e.entity_type == NPC_TYPE and e.present_in_scene]

    def resolve_mentions(self, mentions: list[str]) -> TargetResolution:
        result = TargetResolution()
        for mention in mentions or []:
            norm = normalize_name(mention)
            if not norm:
                continue
            present = [e for e in self.entities
                       if e.present_in_scene and norm in e.names_normalized()]
            absent = [e for e in self.entities
                      if not e.present_in_scene and norm in e.names_normalized()]
            if len(present) == 1:
                result.resolved.append(present[0])
            elif len(present) > 1:
                result.ambiguous.append((mention, present))
            elif len(absent) == 1:
                result.not_present.append(absent[0])
            elif len(absent) > 1:
                result.ambiguous.append((mention, absent))
            else:
                result.unresolved.append(mention)
        return result


def _observable_state(char: Character) -> str:
    bits = list(char.conditions or [])
    if char.dead:
        bits.append("เสียชีวิต")
    elif char.hp <= 0:
        bits.append("หมดสติ")
    return ", ".join(bits)


class SceneEntityDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(self, scene, *, actor_character_id: str | None,
                    campaign_id: str | None = None) -> SceneDirectory:
        entities: list[EntityContext] = []
        actor: EntityContext | None = None

        present_pc_refs: set[str] = set()
        if scene is not None:
            # PLAYER CHARACTERS physically present in THIS scene.
            for ref in list(scene.participants or []):
                kind, cid = parse_entity_ref(ref)
                if kind != "character" or not cid:
                    continue
                char = await self.session.get(Character, cid)
                if char is None:
                    continue
                present_pc_refs.add(ref)
                ec = await self._pc_context(char, present=True,
                                            is_actor=(cid == actor_character_id))
                entities.append(ec)
                if ec.is_actor:
                    actor = ec

            # NPCs / creatures visible or pressing in this scene.
            for ref in list(scene.visible_entity_ids or []) + list(scene.immediate_threat_ids or []):
                kind, nid = parse_entity_ref(ref)
                if kind == "npc" and nid and ref not in {e.entity_ref for e in entities}:
                    npc = await self.session.get(NPC, nid)
                    if npc is not None:
                        entities.append(EntityContext(
                            entity_ref=ref, entity_type=NPC_TYPE,
                            canonical_name=npc.name, aliases=[],
                            present_in_scene=True, player_controlled=False,
                        ))

        # If the actor wasn't in participants (edge case), still hydrate them.
        if actor is None and actor_character_id is not None:
            char = await self.session.get(Character, actor_character_id)
            if char is not None:
                actor = await self._pc_context(char, present=True, is_actor=True)
                entities.append(actor)
                present_pc_refs.add(actor.entity_ref)

        # KNOWN-BUT-ABSENT party characters: resolvable, but not reachable targets.
        cid_for_party = campaign_id or (
            (await self.session.get(Character, actor_character_id)).campaign_id
            if actor_character_id else None
        )
        if cid_for_party is not None:
            members = (await self.session.execute(
                select(CampaignMember).where(CampaignMember.campaign_id == cid_for_party)
            )).scalars().all()
            for m in members:
                if not m.active_character_id:
                    continue
                ref = entity_ref("character", m.active_character_id)
                if ref in present_pc_refs:
                    continue
                char = await self.session.get(Character, m.active_character_id)
                if char is not None:
                    entities.append(await self._pc_context(char, present=False, is_actor=False))

        return SceneDirectory(actor=actor, entities=entities)

    async def _pc_context(self, char: Character, *, present: bool, is_actor: bool) -> EntityContext:
        return EntityContext(
            entity_ref=entity_ref("character", char.id),
            entity_type=PLAYER_CHARACTER,
            canonical_name=char.name,
            aliases=list(char.aliases or []),
            present_in_scene=present,
            visible_to_actor=present,
            player_controlled=True,
            controller_member_id=char.owner_member_id,
            is_actor=is_actor,
            observable_state=_observable_state(char),
        )
