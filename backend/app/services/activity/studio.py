"""DM Studio projections — OWNER-authorized views over canonical state.

These builders may read DM_ONLY material (secrets, unrevealed clues, NPC epistemic
records, threat internals). They must ONLY be reachable through routes that have
already verified the member's role server-side. Objective canon vs what-an-NPC-
knows/believes are kept structurally separate in the NPC projection.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import format_game_time
from app.core.ids import entity_ref, parse_entity_ref
from app.models.campaign import Campaign, CampaignMember
from app.models.canon_import import CanonImport
from app.models.character import Character
from app.models.enums import Visibility
from app.models.event import Event
from app.models.knowledge import Secret
from app.models.location import Location
from app.models.npc import NPC
from app.models.npc_epistemic import NPCFact, NPCRelationship
from app.models.scene import Scene
from app.models.session import Session
from app.models.user import User
from app.models.world import ScheduledWorldEvent, Threat
from app.models.world_graph import CampaignCanonRecord, LocationConnection
from app.services.scenes import SceneService
from app.services.sessions.session_service import SessionService


async def _active_session_and_scene(session: AsyncSession, campaign_id: str):
    active = await SessionService(session).get_active_session(campaign_id)
    scene = await SceneService(session).get_active_scene(active.id) if active else None
    return active, scene


async def _party_positions(session: AsyncSession, campaign: Campaign) -> list[dict]:
    members = list((await session.execute(
        select(CampaignMember).where(CampaignMember.campaign_id == campaign.id)
    )).scalars())
    out = []
    for m in members:
        if not m.active_character_id:
            continue
        char = await session.get(Character, m.active_character_id)
        if char is None:
            continue
        user = await session.get(User, m.user_id)
        loc = await session.get(Location, char.location_id) if char.location_id else None
        out.append({
            "character_id": char.id, "name": char.name,
            "player_name": user.display_name if user else "",
            "role": m.role, "level": char.level, "char_class": char.char_class,
            "hp": char.hp, "max_hp": char.max_hp,
            "conditions": list(char.conditions or []),
            "location_id": char.location_id,
            "location_name": loc.name if loc else None,
        })
    return out


async def build_command_center(session: AsyncSession, *, campaign: Campaign) -> dict:
    active, scene = await _active_session_and_scene(session, campaign.id)
    scene_loc = await session.get(Location, scene.location_id) if scene and scene.location_id else None

    threats = list((await session.execute(
        select(Threat).where(Threat.campaign_id == campaign.id, Threat.status == "active")
        .order_by(Threat.progress.desc())
    )).scalars())
    due_events = list((await session.execute(
        select(ScheduledWorldEvent).where(
            ScheduledWorldEvent.campaign_id == campaign.id,
            ScheduledWorldEvent.resolved.is_(False))
        .order_by(ScheduledWorldEvent.due_game_time.asc()).limit(10)
    )).scalars())
    recent = list((await session.execute(
        select(Event).where(Event.campaign_id == campaign.id,
                            Event.narrative_significance >= 15)
        .order_by(Event.seq.desc()).limit(12)
    )).scalars())
    prep = dict(campaign.session_prep or {})
    warnings: list[str] = []
    # Consistency warning: stale NPC refs in the current scene (position mismatch).
    if scene is not None and scene.location_id:
        for ref in list(scene.visible_entity_ids or []):
            kind, nid = parse_entity_ref(ref)
            if kind != "npc" or not nid:
                continue
            npc = await session.get(NPC, nid)
            if npc is not None and npc.current_location_id is not None \
                    and npc.current_location_id != scene.location_id:
                warnings.append(f"NPC '{npc.name}' อยู่ในรายชื่อฉากแต่ตำแหน่งจริงไม่ตรง — จะไม่ถูกแสดงว่าอยู่ในฉาก")

    return {
        "campaign": {"id": campaign.id, "name": campaign.name, "status": campaign.status,
                     "central_question": campaign.central_question or "",
                     "session_purpose": prep.get("purpose", "")},
        "game_time": campaign.current_game_time,
        "game_time_th": format_game_time(campaign.current_game_time),
        "session": ({"id": active.id, "number": active.number, "status": active.status,
                     "play_state": active.active_play_state} if active else None),
        "scene": ({"id": scene.id, "mode": scene.mode, "purpose": scene.purpose,
                   "location_name": scene_loc.name if scene_loc else None} if scene else None),
        "party": await _party_positions(session, campaign),
        "threats": [{"id": t.id, "name": t.name, "goal": t.goal, "progress": t.progress,
                     "next_action": t.next_action,
                     "due_game_time": t.scheduled_game_time} for t in threats],
        "due_events": [{"id": ev.id, "kind": ev.kind, "due_game_time": ev.due_game_time,
                        "due_th": format_game_time(ev.due_game_time),
                        "perceivable": ev.perceivable,
                        "summary": (ev.payload or {}).get("summary", "")}
                       for ev in due_events],
        "warnings": warnings,
        "recent_events": [{"seq": e.seq, "event_type": e.event_type,
                           "visibility": e.visibility,
                           "summary": (e.payload or {}).get("summary", "")}
                          for e in reversed(recent)],
    }


async def build_current_scene(session: AsyncSession, *, campaign: Campaign) -> dict:
    active, scene = await _active_session_and_scene(session, campaign.id)
    if scene is None:
        return {"scene": None}
    loc = await session.get(Location, scene.location_id) if scene.location_id else None

    participants = []
    for ref in list(scene.participants or []):
        kind, cid = parse_entity_ref(ref)
        if kind == "character" and cid:
            char = await session.get(Character, cid)
            if char is not None:
                participants.append({"ref": ref, "name": char.name,
                                     "hp": char.hp, "max_hp": char.max_hp})
    present_npcs, stale_refs = [], []
    for ref in list(scene.visible_entity_ids or []) + list(scene.immediate_threat_ids or []):
        kind, nid = parse_entity_ref(ref)
        if kind != "npc" or not nid:
            continue
        npc = await session.get(NPC, nid)
        if npc is None:
            stale_refs.append({"ref": ref, "reason": "ไม่พบ NPC"})
            continue
        if npc.current_location_id is not None and scene.location_id is not None \
                and npc.current_location_id != scene.location_id:
            stale_refs.append({"ref": ref, "reason": f"'{npc.name}' ตำแหน่งจริงไม่ตรงกับฉาก"})
            continue
        if not any(p["ref"] == ref for p in present_npcs):
            present_npcs.append({"ref": ref, "id": npc.id, "name": npc.name,
                                 "communication_mode": npc.communication_mode,
                                 "emotional_state": npc.emotional_state})

    exits = []
    if scene.location_id:
        conns = list((await session.execute(
            select(LocationConnection).where(
                LocationConnection.from_location_id == scene.location_id)
        )).scalars())
        for c in conns:
            dest = await session.get(Location, c.to_location_id)
            exits.append({"label": c.label or c.direction or "ทางออก",
                          "to_name": dest.name if dest else "?",
                          "travel_minutes": c.travel_minutes,
                          "obvious": c.obvious, "access_state": c.access_state})

    parent_path = []
    cur, guard = loc, 0
    while cur is not None and cur.parent_id and guard < 6:
        cur = await session.get(Location, cur.parent_id)
        if cur is not None:
            parent_path.append(cur.name)
        guard += 1

    recent = list((await session.execute(
        select(Event).where(Event.campaign_id == campaign.id, Event.scene_id == scene.id)
        .order_by(Event.seq.desc()).limit(10)
    )).scalars())

    return {
        "scene": {
            "id": scene.id, "mode": scene.mode, "status": scene.status,
            "purpose": scene.purpose, "dramatic_question": scene.dramatic_question,
            "start_game_time": scene.scene_start_game_time,
            "start_game_time_th": format_game_time(scene.scene_start_game_time),
            "pending_action": (scene.pending_action or {}).get("kind")
                if scene.pending_action else None,
            "allowed_clues": list(scene.allowed_clues or []),
            "spotlight": dict(scene.spotlight or {}),
        },
        "location": ({"id": loc.id, "name": loc.name, "type": loc.location_type,
                      "provenance": loc.provenance,
                      "obvious": loc.description_obvious,
                      "current_activity": loc.current_activity,
                      "parent_path": " · ".join(reversed(parent_path))} if loc else None),
        "participants": participants,
        "present_npcs": present_npcs,
        "stale_refs": stale_refs,
        "exits": exits,
        "recent_events": [{"seq": e.seq, "event_type": e.event_type,
                           "visibility": e.visibility,
                           "summary": (e.payload or {}).get("summary", "")}
                          for e in reversed(recent)],
    }


async def build_world(session: AsyncSession, *, campaign: Campaign) -> dict:
    locs = list((await session.execute(
        select(Location).where(Location.campaign_id == campaign.id).order_by(Location.name)
    )).scalars())
    npc_counts: dict[str, int] = {}
    for nid, loc_id in (await session.execute(
        select(NPC.id, NPC.current_location_id).where(NPC.campaign_id == campaign.id)
    )).all():
        if loc_id:
            npc_counts[loc_id] = npc_counts.get(loc_id, 0) + 1
    party = await _party_positions(session, campaign)
    party_by_loc: dict[str, list[str]] = {}
    for p in party:
        if p["location_id"]:
            party_by_loc.setdefault(p["location_id"], []).append(p["name"])
    conns = list((await session.execute(
        select(LocationConnection).where(LocationConnection.campaign_id == campaign.id)
    )).scalars())
    exits_by_loc: dict[str, list[dict]] = {}
    name_by_id = {l.id: l.name for l in locs}
    for c in conns:
        exits_by_loc.setdefault(c.from_location_id, []).append({
            "label": c.label or c.direction or "ทางออก",
            "to_id": c.to_location_id,
            "to_name": name_by_id.get(c.to_location_id, "?"),
            "travel_minutes": c.travel_minutes,
            "obvious": c.obvious, "access_state": c.access_state,
        })
    return {"locations": [{
        "id": l.id, "name": l.name, "type": l.location_type,
        "parent_id": l.parent_id, "provenance": l.provenance,
        "obvious": l.description_obvious,
        "focused": l.description_focused,
        "hidden": l.description_hidden,
        "weather": l.weather, "current_activity": l.current_activity,
        "npc_count": npc_counts.get(l.id, 0),
        "party_here": party_by_loc.get(l.id, []),
        "exits": exits_by_loc.get(l.id, []),
    } for l in locs]}


async def build_npcs(session: AsyncSession, *, campaign: Campaign) -> dict:
    _, scene = await _active_session_and_scene(session, campaign.id)
    scene_loc_id = scene.location_id if scene else None
    npcs = list((await session.execute(
        select(NPC).where(NPC.campaign_id == campaign.id).order_by(NPC.name)
    )).scalars())
    out = []
    for n in npcs:
        loc = await session.get(Location, n.current_location_id) if n.current_location_id else None
        out.append({
            "id": n.id, "name": n.name,
            "location_id": n.current_location_id,
            "location_name": loc.name if loc else None,
            "communication_mode": n.communication_mode,
            "emotional_state": n.emotional_state,
            "personality": n.personality,
            "voice_register": n.voice_register,
            "goals": list(n.goals or []),
            "present_in_scene": bool(scene_loc_id and n.current_location_id == scene_loc_id),
        })
    return {"npcs": out}


async def build_npc_detail(session: AsyncSession, *, campaign: Campaign, npc_id: str) -> dict | None:
    npc = await session.get(NPC, npc_id)
    if npc is None or npc.campaign_id != campaign.id:
        return None
    loc = await session.get(Location, npc.current_location_id) if npc.current_location_id else None
    facts = list((await session.execute(
        select(NPCFact).where(NPCFact.npc_id == npc.id)
        .order_by(NPCFact.confidence.desc())
    )).scalars())
    rels = list((await session.execute(
        select(NPCRelationship).where(NPCRelationship.npc_id == npc.id)
    )).scalars())
    rel_out = []
    for r in rels:
        kind, cid = parse_entity_ref(r.entity_ref)
        name = r.entity_ref
        if kind == "character" and cid:
            char = await session.get(Character, cid)
            name = char.name if char else r.entity_ref
        rel_out.append({"entity_ref": r.entity_ref, "entity_name": name,
                        "attitude": r.attitude, "trust": r.trust})
    from app.npcs.knowledge_service import NPCKnowledgeService

    protocols = await NPCKnowledgeService(session).protocols_known_by(
        campaign_id=campaign.id, npc_name=npc.name)
    ref = entity_ref("npc", npc.id)
    recent = list((await session.execute(
        select(Event).where(Event.campaign_id == campaign.id)
        .order_by(Event.seq.desc()).limit(60)
    )).scalars())
    npc_events = [{"seq": e.seq, "event_type": e.event_type,
                   "summary": (e.payload or {}).get("summary", ""),
                   "visibility": e.visibility}
                  for e in recent
                  if e.actor_entity == ref or ref in (e.target_entities or [])][:10]
    return {
        # OBJECTIVE CANON — what is true about this NPC.
        "npc": {"id": npc.id, "name": npc.name,
                "location_name": loc.name if loc else None,
                "communication_mode": npc.communication_mode,
                "personality": npc.personality, "voice_register": npc.voice_register,
                "goals": list(npc.goals or []), "emotional_state": npc.emotional_state,
                "attitudes": dict(npc.attitudes or {})},
        # WHAT THIS NPC KNOWS / BELIEVES — its epistemic state, separately.
        "knowledge": [{"subject": f.subject, "fact": f.fact, "status": f.status,
                       "confidence": f.confidence, "source": f.source} for f in facts],
        "relationships": rel_out,
        "protocols": protocols,
        "recent_events": list(reversed(npc_events)),
    }


async def build_threats(session: AsyncSession, *, campaign: Campaign) -> dict:
    threats = list((await session.execute(
        select(Threat).where(Threat.campaign_id == campaign.id)
        .order_by(Threat.progress.desc())
    )).scalars())
    events = list((await session.execute(
        select(ScheduledWorldEvent).where(ScheduledWorldEvent.campaign_id == campaign.id)
        .order_by(ScheduledWorldEvent.due_game_time.asc())
    )).scalars())
    return {
        "threats": [{"id": t.id, "name": t.name, "goal": t.goal, "status": t.status,
                     "progress": t.progress, "next_action": t.next_action,
                     "due_game_time": t.scheduled_game_time,
                     "due_th": format_game_time(t.scheduled_game_time),
                     "tick_amount": t.tick_amount, "tick_interval": t.tick_interval}
                    for t in threats],
        "scheduled_events": [{"id": e.id, "kind": e.kind, "due_game_time": e.due_game_time,
                              "due_th": format_game_time(e.due_game_time),
                              "perceivable": e.perceivable, "resolved": e.resolved,
                              "summary": (e.payload or {}).get("summary", "")}
                             for e in events],
    }


async def build_secrets(session: AsyncSession, *, campaign: Campaign) -> dict:
    secrets = list((await session.execute(
        select(Secret).where(Secret.campaign_id == campaign.id)
    )).scalars())
    clues = list((await session.execute(
        select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign.id,
            CampaignCanonRecord.category == "clue")
    )).scalars())
    protocols = list((await session.execute(
        select(CampaignCanonRecord).where(
            CampaignCanonRecord.campaign_id == campaign.id,
            CampaignCanonRecord.category == "protocol")
    )).scalars())
    clues_by_secret: dict[str | None, list[dict]] = {}
    for c in clues:
        clues_by_secret.setdefault(
            c.scope_id if c.scope_type == "secret" else None, []
        ).append({"id": c.id, "text": c.fact, "visibility": c.visibility,
                  "provenance": c.provenance, "active": c.active})
    # A clue has become party-known when a KNOWLEDGE_GAINED fragment matches it.
    known_fragments = {
        (e.payload or {}).get("fragment", "")
        for e in (await session.execute(
            select(Event).where(Event.campaign_id == campaign.id,
                                Event.event_type == "KNOWLEDGE_GAINED")
        )).scalars()
    }
    def _clue_known(text: str) -> bool:
        return any(f and (f in text or text in f) for f in known_fragments)

    return {
        "secrets": [{
            "id": s.id, "fact": s.fact, "visibility": s.visibility,
            "revealed": s.revealed,
            "known_by": (s.visibility_map or {}).get("characters", []),
            "clues": [{**c, "known": _clue_known(c["text"])}
                      for c in clues_by_secret.get(s.id, [])],
        } for s in secrets],
        "unlinked_clues": [{**c, "known": _clue_known(c["text"])}
                           for c in clues_by_secret.get(None, [])],
        "protocols": [{"id": p.id, "title": p.fact, "visibility": p.visibility,
                       "key": (p.data or {}).get("key", ""),
                       "rules": (p.data or {}).get("rules", []),
                       "known_by": (p.data or {}).get("known_by", [])}
                      for p in protocols],
    }


async def build_events(session: AsyncSession, *, campaign: Campaign,
                       limit: int = 50, before_seq: int | None = None,
                       visibility: str | None = None,
                       event_type: str | None = None) -> dict:
    stmt = select(Event).where(Event.campaign_id == campaign.id)
    if before_seq is not None:
        stmt = stmt.where(Event.seq < before_seq)
    if visibility:
        stmt = stmt.where(Event.visibility == visibility)
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    rows = list((await session.execute(
        stmt.order_by(Event.seq.desc()).limit(limit)
    )).scalars())
    total = (await session.execute(
        select(func.count(Event.id)).where(Event.campaign_id == campaign.id)
    )).scalar_one()
    return {
        "total": total,
        "events": [{
            "seq": e.seq, "event_type": e.event_type, "visibility": e.visibility,
            "actor": e.actor_entity, "targets": list(e.target_entities or []),
            "game_time": e.campaign_time, "game_time_th": format_game_time(e.campaign_time),
            "real_time": e.real_time.isoformat() if e.real_time else None,
            "summary": (e.payload or {}).get("summary", ""),
            "significance": e.narrative_significance,
            "mechanical_changes": dict(e.mechanical_changes or {}),
            "session_id": e.session_id, "scene_id": e.scene_id,
        } for e in reversed(rows)],
    }


async def build_imports(session: AsyncSession, *, campaign: Campaign) -> dict:
    rows = list((await session.execute(
        select(CanonImport).where(CanonImport.campaign_id == campaign.id)
        .order_by(CanonImport.created_at.desc())
    )).scalars())
    out = []
    for r in rows:
        uploader = await session.get(CampaignMember, r.uploader_member_id)
        user = await session.get(User, uploader.user_id) if uploader else None
        review = (r.proposal or {}).get("_review", {})
        out.append({
            "id": r.id, "filename": r.filename, "status": r.status,
            "content_sha256": r.content_sha256[:12],
            "uploaded_at": r.created_at.isoformat() if r.created_at else None,
            "uploader": user.display_name if user else "",
            "counts": review.get("counts", {}),
            "warnings": review.get("warnings", []),
        })
    return {"imports": out}
