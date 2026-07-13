"""Class-specific mechanics built ON the shared framework (never duplicating it).

Each module here uses the reusable systems — ResourceEngine, SpellEngine,
derive, rest — to express one class's distinctive kit (Sorcery Points/Metamagic,
Pact/Invocations, Jack of All Trades, Spellbook learning). No parallel resource,
spell, or persistence system is created.
"""
