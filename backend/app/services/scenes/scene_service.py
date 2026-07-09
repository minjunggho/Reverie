"""Scene working-state service.

A scene is mutable working state. It carries `pending_action` for the
CLARIFICATION_REQUIRED flow and a `version` for optimistic concurrency. Scene
exhaustion / transition logic lives in `app/scenes` behaviours reached from the
orchestration layer; this service is the low-level CRUD + guarded updates.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import SceneMode, SceneStatus
from app.models.scene import Scene


class SceneService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_scene(
        self,
        *,
        session_id: str,
        location_id: str | None = None,
        mode: SceneMode = SceneMode.EXPLORATION,
        purpose: str = "",
        dramatic_question: str = "",
        scene_start_game_time: int = 0,
        participants: list[str] | None = None,
        visible_entity_ids: list[str] | None = None,
        immediate_threat_ids: list[str] | None = None,
    ) -> Scene:
        scene = Scene(
            session_id=session_id,
            location_id=location_id,
            mode=mode.value,
            purpose=purpose,
            dramatic_question=dramatic_question,
            scene_start_game_time=scene_start_game_time,
            participants=participants or [],
            visible_entity_ids=visible_entity_ids or [],
            immediate_threat_ids=immediate_threat_ids or [],
            status=SceneStatus.ACTIVE.value,
        )
        self.session.add(scene)
        await self.session.flush()
        return scene

    async def get_scene(self, scene_id: str) -> Scene:
        scene = await self.session.get(Scene, scene_id)
        if scene is None:
            raise NotFoundError(f"scene {scene_id} not found")
        return scene

    async def get_active_scene(self, session_id: str) -> Scene | None:
        return (
            await self.session.execute(
                select(Scene)
                .where(
                    Scene.session_id == session_id,
                    Scene.status.in_([SceneStatus.ACTIVE.value, SceneStatus.TRANSITIONING.value]),
                )
                .order_by(Scene.created_at.desc())
            )
        ).scalars().first()

    async def update_context(self, scene: Scene, **changes: Any) -> Scene:
        for key, value in changes.items():
            setattr(scene, key, value)
        scene.version += 1
        return scene

    async def set_pending_action(self, scene: Scene, action: dict[str, Any]) -> Scene:
        scene.pending_action = action
        scene.pending_action_id = action.get("id")
        scene.version += 1
        return scene

    async def clear_pending_action(self, scene: Scene) -> Scene:
        scene.pending_action = None
        scene.pending_action_id = None
        scene.version += 1
        return scene

    async def close_scene(self, scene: Scene) -> Scene:
        scene.status = SceneStatus.CLOSED.value
        scene.version += 1
        return scene
