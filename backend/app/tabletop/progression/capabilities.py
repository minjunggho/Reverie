"""CharacterCapabilities — the single answer to "what can this character do?"

Composes the three reusable systems for one character at their current level:
- class FEATURES available at this level (from the typed class definition),
  grouped by action-economy activation;
- limited-use RESOURCES (live ResourceState + their definitions);
- the SPELLCASTING profile (model, DC, attack, prepared list).

The sheet, the Discord action surfaces, and level-up all read from here rather
than re-deriving class specifics — no scattered per-class logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character
from app.rules_content import get_registry
from app.rules_content.registry import FeatureDef
from app.tabletop.resources import ResourceEngine
from app.tabletop.spellcasting import SpellcastingProfile, spellcasting_profile


@dataclass
class FeatureView:
    key: str
    name_th: str
    activation: str
    level: int
    execution: str
    resource_id: str | None
    summary_th: str

    @classmethod
    def of(cls, f: FeatureDef) -> "FeatureView":
        return cls(key=f.key, name_th=f.name_th, activation=f.activation, level=f.level,
                   execution=f.execution, resource_id=f.resource_id, summary_th=f.summary_th)


@dataclass
class ResourceView:
    resource_id: str
    name_th: str
    current: int
    max_value: int
    recharge: str


@dataclass
class CharacterCapabilities:
    char_class: str
    level: int
    features: list[FeatureView] = field(default_factory=list)
    resources: list[ResourceView] = field(default_factory=list)
    spellcasting: SpellcastingProfile | None = None

    def features_by_activation(self, activation: str) -> list[FeatureView]:
        return [f for f in self.features if f.activation == activation]

    @property
    def is_caster(self) -> bool:
        return bool(self.spellcasting and self.spellcasting.is_caster)


async def character_capabilities(
    session: AsyncSession, character: Character
) -> CharacterCapabilities:
    reg = get_registry()
    cls = reg.get_class(character.char_class)
    features = [FeatureView.of(f) for f in cls.features_at(character.level)]

    engine = ResourceEngine(session)
    resource_views: list[ResourceView] = []
    for state in await engine.list_for(character.id):
        d = reg.get_resource(state.resource_id)
        resource_views.append(ResourceView(
            resource_id=state.resource_id, name_th=d.name_th,
            current=state.current, max_value=state.max_value, recharge=d.recharge))

    profile = await spellcasting_profile(session, character)
    return CharacterCapabilities(
        char_class=cls.name, level=character.level, features=features,
        resources=resource_views,
        spellcasting=profile if profile.is_caster else None,
    )
