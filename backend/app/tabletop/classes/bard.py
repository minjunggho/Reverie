"""Bard — Bardic Inspiration die scaling, Jack of All Trades, Song of Rest.

Bardic Inspiration is a ResourceState pool (CHA-mod uses, long-rest recharge) the
shared ResourceEngine already tracks; this module supplies the die that scales with
level and the two passive/utility calculations. Jack of All Trades is folded into
the derivation engine so a bard's non-proficient checks get half proficiency —
one code path, guarded by class+level.
"""
from __future__ import annotations

BARDIC_INSPIRATION = "resource:bardic_inspiration"


def bardic_inspiration_die(level: int) -> int:
    """SRD 5.2.1: d6, then d8 at 5, d10 at 10, d12 at 15."""
    if level >= 15:
        return 12
    if level >= 10:
        return 10
    if level >= 5:
        return 8
    return 6


def has_jack_of_all_trades(char_class: str, level: int) -> bool:
    """Bard gains Jack of All Trades at level 2 (2024)."""
    return (char_class or "").lower() == "bard" and level >= 2


def jack_of_all_trades_bonus(char_class: str, level: int, proficiency_bonus: int) -> int:
    """Half proficiency (rounded down), added to ability checks the bard is NOT
    already proficient in. 0 for non-bards / bards below level 2."""
    return proficiency_bonus // 2 if has_jack_of_all_trades(char_class, level) else 0


def song_of_rest_die(level: int) -> int:
    """Extra healing die on a short rest for allies who spend a Hit Die: d6, then
    d8 at 9, d10 at 13, d12 at 17."""
    if level >= 17:
        return 12
    if level >= 13:
        return 10
    if level >= 9:
        return 8
    return 6
