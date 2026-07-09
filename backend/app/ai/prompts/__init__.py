"""Prompt templates. System strings only — no game logic, no authoritative numbers.

Prompts embed machine-readable markers (`MESSAGE:`, `ACTION:`, `OUTCOME:`,
`EVENTS:`) that the deterministic test double keys off. In production these markers
are simply structured context for the model.
"""
