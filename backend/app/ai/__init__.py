"""AI layer: the LLMProvider abstraction, jobs, and prompts.

The AI layer is a LEAF the engine calls at specific decision points. It returns
Pydantic-validated proposals and prose. It never writes to the database and never
produces authoritative numbers.
"""
