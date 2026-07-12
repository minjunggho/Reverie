"""Unicode-safe, exact choice-name resolution for rules content.

The resolver deliberately never accepts substrings.  Callers provide the legal
options for the current state, and receive either one exact canonical key, an
ambiguity, or close suggestions that still require an explicit player choice.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

_CHOICE_SEPARATOR_RE = re.compile(r"[\s_-]+", re.UNICODE)
_TRAILING_KEY_RE = re.compile(r"\(([^()]*)\)\s*$", re.UNICODE)


def normalize_choice_name(value: str) -> str:
    """Return a stable comparison form for a player-visible choice name.

    NFKC handles compatibility forms (including full-width Latin characters),
    ``casefold`` provides Unicode-aware case-insensitivity, and the separator
    pass makes spaces, underscores, and hyphens equivalent.
    """
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = "".join(
        "-" if unicodedata.category(char) == "Pd" else char
        for char in normalized
    )
    normalized = _CHOICE_SEPARATOR_RE.sub(" ", normalized.strip())
    return unicodedata.normalize("NFKC", normalized)


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    """One canonical choice and every exact name allowed to identify it."""

    key: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChoiceResolution:
    """Result of exact resolution; suggestion keys are never auto-selected."""

    key: str | None = None
    ambiguous_keys: tuple[str, ...] = ()
    suggestion_keys: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.key is not None

    @property
    def ambiguous(self) -> bool:
        return bool(self.ambiguous_keys)


def resolve_choice_name(
    value: str,
    options: Iterable[ChoiceOption],
    *,
    suggestion_limit: int = 3,
) -> ChoiceResolution:
    """Resolve *value* against exact normalized names from *options*.

    A trailing ``(canonical_key)`` is accepted for the labels rendered by the
    Discord UI, but the extracted key still goes through the same exact index.
    """
    alias_index: dict[str, set[str]] = {}
    option_order: list[str] = []
    for option in options:
        if option.key not in option_order:
            option_order.append(option.key)
        for name in (option.key, *option.names):
            normalized = normalize_choice_name(name)
            if normalized:
                alias_index.setdefault(normalized, set()).add(option.key)

    query = normalize_choice_name(value)
    exact_queries = [query] if query else []
    trailing = _TRAILING_KEY_RE.search(value or "")
    if trailing:
        trailing_query = normalize_choice_name(trailing.group(1))
        if trailing_query and trailing_query not in exact_queries:
            exact_queries.append(trailing_query)

    ambiguous: set[str] = set()
    for exact_query in exact_queries:
        matches = alias_index.get(exact_query, set())
        if len(matches) == 1:
            return ChoiceResolution(key=next(iter(matches)))
        if len(matches) > 1:
            ambiguous.update(matches)
    if ambiguous:
        return ChoiceResolution(ambiguous_keys=tuple(
            key for key in option_order if key in ambiguous
        ))

    if not query or suggestion_limit <= 0:
        return ChoiceResolution()

    close_aliases = difflib.get_close_matches(
        query,
        list(alias_index),
        n=max(suggestion_limit * 4, suggestion_limit),
        cutoff=0.55,
    )
    suggestions: list[str] = []
    for alias in close_aliases:
        for key in option_order:
            if key in alias_index[alias] and key not in suggestions:
                suggestions.append(key)
                if len(suggestions) >= suggestion_limit:
                    return ChoiceResolution(suggestion_keys=tuple(suggestions))
    return ChoiceResolution(suggestion_keys=tuple(suggestions))
