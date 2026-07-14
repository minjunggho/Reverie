"""Rules content — the versioned, machine-readable SRD 5.2.1 definitions.

DEFINITIONS live here (what Wizard L1 *offers*). CHARACTER GRANTS record what a
specific character received and from where. CURRENT STATE lives in its own rows.
Markdown never executes; this JSON never narrates.

Every record carries ruleset_id / definition_id / definition_version
(see docs/rules-sources.md). Loaded once into a validated `RulesRegistry`.

This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1")
by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd, licensed
under CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/legalcode).
"""
from app.rules_content.registry import (
    RULESET_ID,
    STANDARD_ARRAY,
    BackgroundDef,
    ClassDef,
    ResourceDef,
    RulesRegistry,
    SkillDef,
    SpeciesDef,
    SpellDef,
    get_registry,
)
from app.rules_content.choice_names import (
    ChoiceOption,
    ChoiceResolution,
    normalize_choice_name,
    resolve_choice_name,
)
from app.rules_content.faith_registry import (
    DeityDefinition,
    DeityRelationship,
    DeityResolution,
    DeityResolutionStatus,
    DeityResolver,
    FaithContentError,
    FaithContentWarning,
    FaithRegistry,
    PantheonActivationStatus,
    PantheonDefinition,
    get_faith_registry,
    normalize_deity_reference,
    parse_pantheon_markdown,
)
from app.rules_content.faith_interactions import (
    DoctrineDefinition,
    FaithInteractionRegistry,
    get_faith_interaction_registry,
)

__all__ = [
    "RULESET_ID",
    "STANDARD_ARRAY",
    "RulesRegistry",
    "get_registry",
    "ClassDef",
    "SpeciesDef",
    "BackgroundDef",
    "SpellDef",
    "ResourceDef",
    "SkillDef",
    "ChoiceOption",
    "ChoiceResolution",
    "normalize_choice_name",
    "resolve_choice_name",
    "PantheonDefinition",
    "DeityDefinition",
    "PantheonActivationStatus",
    "DeityResolutionStatus",
    "DeityResolution",
    "DeityRelationship",
    "DeityResolver",
    "FaithContentError",
    "FaithContentWarning",
    "FaithRegistry",
    "get_faith_registry",
    "normalize_deity_reference",
    "parse_pantheon_markdown",
    "DoctrineDefinition",
    "FaithInteractionRegistry",
    "get_faith_interaction_registry",
]
