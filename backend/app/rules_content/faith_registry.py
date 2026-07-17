"""Typed, validated pantheon content packs within the rules-content subsystem.

The registry is static and campaign-agnostic. Campaign activation and isolation
live in :mod:`app.services.faith`; gameplay code must not query this registry and
assume that every loaded pantheon is active.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.rules_content.choice_names import normalize_choice_name

_CONTENT_ROOT = Path(__file__).parent / "pantheons"
log = get_logger(__name__)

VALID_ALIGNMENTS = frozenset({
    "Lawful Good",
    "Neutral Good",
    "Chaotic Good",
    "Lawful Neutral",
    "True Neutral",
    "Chaotic Neutral",
    "Lawful Evil",
    "Neutral Evil",
    "Chaotic Evil",
})

# Phase 1 validates the vocabulary present in owner-provided pantheon packs. A
# domain here is content metadata, not a claim that its subclass mechanics exist.
VALID_DOMAINS = frozenset({
    "Arcana",
    "Death",
    "Grave",
    "Knowledge",
    "Life",
    "Light",
    "Nature",
    "Order",
    "Tempest",
    "Trickery",
    "Twilight",
    "War",
})

_APOSTROPHE_TRANSLATION = str.maketrans({
    "’": "'",
    "‘": "'",
    "`": "'",
    "´": "'",
    "＇": "'",
})


class FaithContentError(ValidationError):
    """A pantheon pack or persisted campaign activation is invalid."""


class PantheonActivationStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DISABLED = "DISABLED"


class DeityResolutionStatus(str, Enum):
    EXACT = "EXACT"
    NORMALIZED_UNIQUE = "NORMALIZED_UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


class DeityRelationship(str, Enum):
    ALLY = "ALLY"
    RIVAL = "RIVAL"
    ENEMY_FAITH = "ENEMY_FAITH"


class PantheonDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    display_name_en: str = Field(min_length=1)
    display_name_th: str = Field(min_length=1)
    setting: str = Field(min_length=1)
    content_pack_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    deity_keys: tuple[str, ...]
    activation_status: PantheonActivationStatus
    source_reference: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    content_status: str = Field(min_length=1)


class DeityDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)
    pantheon_key: str = Field(min_length=1)
    content_pack_id: str = Field(min_length=1)
    canonical_name_en: str = Field(min_length=1)
    name_th: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    alignment: str = Field(min_length=1)
    domains: tuple[str, ...] = ()
    summary: str = Field(min_length=1)
    full_owner_provided_lore: str = Field(min_length=1)
    themes: tuple[str, ...] = ()
    common_followers: tuple[str, ...] = ()
    religious_practices: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    allies: tuple[str, ...] = ()
    rivals: tuple[str, ...] = ()
    enemy_faiths: tuple[str, ...] = ()
    cleric_capable: bool
    selectable_as_belief: bool
    public_or_secret_tendency: Literal["PUBLIC", "SECRET", "MIXED"] | None = None
    source_reference: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    implementation_status: str = Field(min_length=1)


class DeityResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: DeityResolutionStatus
    deity_key: str | None = None
    candidate_keys: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.deity_key is not None


class FaithContentWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_pack_id: str
    deity_key: str
    field: str
    message: str


class _PackManifest(PantheonDefinition):
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)


class _DeityMetadata(BaseModel):
    key: str = Field(min_length=1)
    pantheon_key: str = Field(min_length=1)
    content_pack_id: str = Field(min_length=1)
    source_name_en: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    common_followers: tuple[str, ...] = ()
    religious_practices: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    allies: tuple[str, ...] = ()
    rivals: tuple[str, ...] = ()
    enemy_faiths: tuple[str, ...] = ()
    cleric_capable: bool = True
    selectable_as_belief: bool = True
    public_or_secret_tendency: Literal["PUBLIC", "SECRET", "MIXED"] | None = None
    provenance: str = Field(min_length=1)
    implementation_status: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ParsedDeity:
    canonical_name_en: str
    name_th: str
    summary: str
    alignment: str
    domains: tuple[str, ...]
    titles: tuple[str, ...]
    full_lore: str
    source_line: int


def normalize_deity_reference(value: str) -> str:
    """Reuse rules-content normalization with apostrophe variant equivalence."""
    return normalize_choice_name((value or "").translate(_APOSTROPHE_TRANSLATION))


def parse_pantheon_markdown(text: str) -> tuple[ParsedDeity, ...]:
    """Parse every ``###`` deity section from the owner content-pack format.

    The entire section is retained as lore. Structured values are taken only from
    the explicit heading, Alignment field, Domains field, and supplied title.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("### ")]
    parsed: list[ParsedDeity] = []
    for start in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if (
                lines[index].startswith("### ")
                or lines[index].startswith("## ")
                or lines[index].strip() == "---"
            ):
                end = index
                break

        heading = lines[start][4:].strip()
        if " - " not in heading or "(" not in heading or ")" not in heading:
            raise FaithContentError(
                f"invalid deity heading at line {start + 1}: expected "
                "'### <name> (<Thai name>) - <summary>'"
            )
        name_part, summary = heading.split(" - ", 1)
        open_paren = name_part.rfind("(")
        close_paren = name_part.rfind(")")
        if open_paren <= 0 or close_paren <= open_paren:
            raise FaithContentError(f"invalid deity names at line {start + 1}")
        canonical_name = name_part[:open_paren].strip()
        while canonical_name and not canonical_name[0].isalnum():
            canonical_name = canonical_name[1:].strip()
        name_th = name_part[open_paren + 1:close_paren].strip()
        if not canonical_name or not name_th or not summary.strip():
            raise FaithContentError(
                f"missing canonical deity name or summary at line {start + 1}"
            )

        alignment_value = _explicit_field(lines[start + 1:end], "(Alignment):**")
        domains_value = _explicit_field(lines[start + 1:end], "(Domains):**")
        alignment_match = re.search(r"\(([^()]*)\)", alignment_value)
        if alignment_match is None or not alignment_match.group(1).strip():
            raise FaithContentError(
                f"missing canonical alignment for {canonical_name} at line {start + 1}"
            )
        alignment = alignment_match.group(1).strip()
        if domains_value.startswith("ไม่มี"):
            domains: tuple[str, ...] = ()
        else:
            domain_text = domains_value.split("|", 1)[0]
            domains = tuple(part.strip() for part in domain_text.split(",") if part.strip())

        title_match = re.search(r"\*\*ฉายา:\*\*\s*(.+)$", alignment_value)
        titles = (title_match.group(1).strip(),) if title_match else ()
        full_lore = "\n".join(lines[start:end]).strip()
        parsed.append(ParsedDeity(
            canonical_name_en=canonical_name,
            name_th=name_th,
            summary=summary.strip(),
            alignment=alignment,
            domains=domains,
            titles=titles,
            full_lore=full_lore,
            source_line=start + 1,
        ))
    return tuple(parsed)


def _explicit_field(lines: Iterable[str], marker: str) -> str:
    for line in lines:
        if marker in line:
            return line.split(marker, 1)[1].strip()
    raise FaithContentError(f"deity section missing required field {marker!r}")


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


class DeityResolver:
    """Exact and normalized resolver over an explicitly supplied legal pool."""

    def __init__(self, deities: Iterable[DeityDefinition]) -> None:
        self._order: list[str] = []
        self._exact: dict[str, set[str]] = {}
        self._normalized: dict[str, set[str]] = {}
        for deity in deities:
            self._order.append(deity.key)
            names = (
                deity.key,
                deity.canonical_name_en,
                deity.name_th,
                *deity.aliases,
                *deity.titles,
            )
            for name in names:
                if not name:
                    continue
                self._exact.setdefault(name, set()).add(deity.key)
                normalized = normalize_deity_reference(name)
                if normalized:
                    self._normalized.setdefault(normalized, set()).add(deity.key)

    def resolve(self, reference: str) -> DeityResolution:
        exact = self._exact.get(reference or "", set())
        if len(exact) == 1:
            return DeityResolution(
                status=DeityResolutionStatus.EXACT,
                deity_key=next(iter(exact)),
            )
        if len(exact) > 1:
            return self._ambiguous(exact)

        normalized = normalize_deity_reference(reference)
        matches = self._normalized.get(normalized, set()) if normalized else set()
        if len(matches) == 1:
            return DeityResolution(
                status=DeityResolutionStatus.NORMALIZED_UNIQUE,
                deity_key=next(iter(matches)),
            )
        if len(matches) > 1:
            return self._ambiguous(matches)
        return DeityResolution(status=DeityResolutionStatus.NOT_FOUND)

    def _ambiguous(self, keys: set[str]) -> DeityResolution:
        return DeityResolution(
            status=DeityResolutionStatus.AMBIGUOUS,
            candidate_keys=tuple(key for key in self._order if key in keys),
        )


class FaithRegistry:
    """Load all versioned pantheon packs and fail closed on invalid content."""

    def __init__(self, content_root: Path = _CONTENT_ROOT) -> None:
        self.content_root = Path(content_root)
        self.pantheons: dict[str, PantheonDefinition] = {}
        self.deities: dict[str, DeityDefinition] = {}
        self.warnings: tuple[FaithContentWarning, ...] = ()
        self._content_pack_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        manifest_paths = sorted(self.content_root.glob("*/manifest.json"))
        if not manifest_paths:
            raise FaithContentError(
                f"faith-content validation failed: no pantheon packs in {self.content_root}"
            )
        warnings: list[FaithContentWarning] = []
        for manifest_path in manifest_paths:
            self._load_pack(manifest_path.parent, warnings)
        self._validate_global()
        self.warnings = tuple(warnings)

    def _load_pack(
        self,
        pack_dir: Path,
        warnings: list[FaithContentWarning],
    ) -> None:
        try:
            manifest = _PackManifest.model_validate(json.loads(
                (pack_dir / "manifest.json").read_text(encoding="utf-8")
            ))
            metadata_raw = json.loads((pack_dir / "deities.json").read_text(encoding="utf-8"))
            metadata = [_DeityMetadata.model_validate(item) for item in metadata_raw]
        except (OSError, json.JSONDecodeError, PydanticValidationError, TypeError) as exc:
            raise FaithContentError(
                f"faith-content validation failed: pack={pack_dir.name}; invalid metadata: {exc}"
            ) from exc

        if manifest.content_pack_id in self._content_pack_ids:
            raise FaithContentError(
                "faith-content validation failed: duplicate content-pack key "
                f"{manifest.content_pack_id!r}"
            )
        if manifest.key in self.pantheons:
            raise FaithContentError(
                f"faith-content validation failed: duplicate pantheon key {manifest.key!r}"
            )
        self._content_pack_ids.add(manifest.content_pack_id)

        if len(manifest.deity_keys) != len(set(manifest.deity_keys)):
            raise FaithContentError(
                f"faith-content validation failed: pantheon={manifest.key}; duplicate deity key"
            )
        metadata_keys = [item.key for item in metadata]
        if len(metadata_keys) != len(set(metadata_keys)):
            raise FaithContentError(
                f"faith-content validation failed: pack={manifest.content_pack_id}; "
                "duplicate deity keys in deities.json"
            )
        if set(metadata_keys) != set(manifest.deity_keys):
            raise FaithContentError(
                f"faith-content validation failed: pantheon={manifest.key}; deity_keys do not "
                "match deities.json"
            )

        source_path = pack_dir / manifest.source_file
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FaithContentError(
                f"faith-content validation failed: missing source {manifest.source_file!r}"
            ) from exc
        normalized_source = source_text.rstrip("\r\n").replace("\r\n", "\n")
        source_hash = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
        if source_hash != manifest.source_sha256:
            raise FaithContentError(
                f"faith-content validation failed: source={manifest.source_file}; "
                f"sha256 {source_hash} does not match owner source {manifest.source_sha256}"
            )
        parsed = parse_pantheon_markdown(source_text)
        by_source_name = {entry.canonical_name_en: entry for entry in parsed}
        if len(by_source_name) != len(parsed):
            raise FaithContentError(
                f"faith-content validation failed: source={manifest.source_file}; "
                "duplicate canonical deity names"
            )
        expected_source_names = {item.source_name_en for item in metadata}
        if set(by_source_name) != expected_source_names:
            missing = sorted(expected_source_names - set(by_source_name))
            extra = sorted(set(by_source_name) - expected_source_names)
            raise FaithContentError(
                f"faith-content validation failed: source={manifest.source_file}; "
                f"unmapped deity entries missing={missing} extra={extra}"
            )

        pantheon = PantheonDefinition.model_validate(
            manifest.model_dump(exclude={"source_file", "source_sha256"})
        )
        self.pantheons[pantheon.key] = pantheon

        for item in metadata:
            if item.pantheon_key != manifest.key:
                raise FaithContentError(
                    f"faith-content validation failed: deity={item.key}; unknown pantheon key "
                    f"{item.pantheon_key!r}"
                )
            if item.content_pack_id != manifest.content_pack_id:
                raise FaithContentError(
                    f"faith-content validation failed: deity={item.key}; content_pack_id "
                    f"{item.content_pack_id!r} does not match {manifest.content_pack_id!r}"
                )
            if item.key in self.deities:
                raise FaithContentError(
                    f"faith-content validation failed: duplicate deity key {item.key!r}"
                )
            source = by_source_name[item.source_name_en]
            deity = DeityDefinition(
                key=item.key,
                pantheon_key=item.pantheon_key,
                content_pack_id=item.content_pack_id,
                canonical_name_en=source.canonical_name_en,
                name_th=source.name_th,
                aliases=item.aliases,
                titles=_unique((*source.titles, *item.titles)),
                alignment=source.alignment,
                domains=source.domains,
                summary=source.summary,
                full_owner_provided_lore=source.full_lore,
                themes=item.themes,
                common_followers=item.common_followers,
                religious_practices=item.religious_practices,
                symbols=item.symbols,
                allies=item.allies,
                rivals=item.rivals,
                enemy_faiths=item.enemy_faiths,
                cleric_capable=item.cleric_capable,
                selectable_as_belief=item.selectable_as_belief,
                public_or_secret_tendency=item.public_or_secret_tendency,
                source_reference=f"{manifest.source_file}:{source.source_line}",
                provenance=item.provenance,
                implementation_status=item.implementation_status,
            )
            self.deities[deity.key] = deity
            self._warn_optional(deity, warnings)

    def _validate_global(self) -> None:
        normalized_keys: dict[str, str] = {}
        normalized_aliases: dict[str, str] = {}
        all_names: dict[str, str] = {}
        for deity in self.deities.values():
            canonical_key = normalize_deity_reference(deity.key)
            if canonical_key in normalized_keys:
                raise FaithContentError(
                    f"faith-content validation failed: duplicate deity key {deity.key!r}"
                )
            normalized_keys[canonical_key] = deity.key
            if not deity.provenance.strip():
                raise FaithContentError(
                    f"faith-content validation failed: deity={deity.key}; missing provenance"
                )
            if deity.alignment not in VALID_ALIGNMENTS:
                raise FaithContentError(
                    f"faith-content validation failed: deity={deity.key}; invalid alignment "
                    f"{deity.alignment!r}; expected one of {sorted(VALID_ALIGNMENTS)}"
                )
            invalid_domains = sorted(set(deity.domains) - VALID_DOMAINS)
            if invalid_domains:
                raise FaithContentError(
                    f"faith-content validation failed: deity={deity.key}; invalid domains "
                    f"{invalid_domains}; expected known domain values"
                )
            if deity.cleric_capable and not deity.domains:
                raise FaithContentError(
                    f"faith-content validation failed: deity={deity.key}; Cleric-capable deity "
                    "must have at least one valid domain"
                )
            for relation, keys in (("rival", deity.rivals), ("ally", deity.allies)):
                if deity.key in keys:
                    raise FaithContentError(
                        f"faith-content validation failed: deity={deity.key}; self-{relation}"
                    )
                unknown = sorted(set(keys) - set(self.deities))
                if unknown:
                    raise FaithContentError(
                        f"faith-content validation failed: deity={deity.key}; unknown "
                        f"{relation} references {unknown}"
                    )
            for alias in deity.aliases:
                normalized = normalize_deity_reference(alias)
                previous = normalized_aliases.get(normalized)
                if not normalized or previous is not None:
                    raise FaithContentError(
                        f"faith-content validation failed: duplicate deity alias {alias!r} "
                        f"for {deity.key!r}"
                    )
                normalized_aliases[normalized] = deity.key
            for name in (
                deity.key,
                deity.canonical_name_en,
                deity.name_th,
                *deity.aliases,
                *deity.titles,
            ):
                normalized = normalize_deity_reference(name)
                previous = all_names.get(normalized)
                if previous is not None and previous != deity.key:
                    raise FaithContentError(
                        "faith-content validation failed: ambiguous normalized deity reference "
                        f"{name!r} maps to {previous!r} and {deity.key!r}"
                    )
                all_names[normalized] = deity.key

        for pantheon in self.pantheons.values():
            if not pantheon.provenance.strip():
                raise FaithContentError(
                    f"faith-content validation failed: pantheon={pantheon.key}; missing provenance"
                )
            unknown = sorted(set(pantheon.deity_keys) - set(self.deities))
            if unknown:
                raise FaithContentError(
                    f"faith-content validation failed: pantheon={pantheon.key}; unknown deity "
                    f"keys {unknown}"
                )

    @staticmethod
    def _warn_optional(
        deity: DeityDefinition,
        warnings: list[FaithContentWarning],
    ) -> None:
        for field in (
            "themes",
            "common_followers",
            "religious_practices",
            "symbols",
            "allies",
            "rivals",
            "enemy_faiths",
            "public_or_secret_tendency",
        ):
            if not getattr(deity, field):
                warnings.append(FaithContentWarning(
                    content_pack_id=deity.content_pack_id,
                    deity_key=deity.key,
                    field=field,
                    message="optional owner-source field not supplied; left empty",
                ))

    def get_pantheon(self, key: str) -> PantheonDefinition | None:
        return self.pantheons.get(key)

    def get_deity(self, key: str) -> DeityDefinition | None:
        return self.deities.get(key)

    def resolver(self, deity_keys: Iterable[str] | None = None) -> DeityResolver:
        if deity_keys is None:
            definitions = self.deities.values()
        else:
            definitions = (
                self.deities[key] for key in deity_keys if key in self.deities
            )
        return DeityResolver(definitions)


@lru_cache(maxsize=1)
def get_faith_registry() -> FaithRegistry:
    registry = FaithRegistry()
    if registry.warnings:
        log.warning(
            "Faith content loaded with %s optional-field warnings; "
            "missing values remain empty and grant no mechanics",
            len(registry.warnings),
        )
    return registry
