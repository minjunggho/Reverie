"""Application services = the engine's public boundary.

Services accept an `AsyncSession` and operate WITHIN the caller's transaction
(they flush but do not commit). The caller — usually the orchestration layer via
`Database.unit_of_work()` — owns the commit, so a state change and the Event(s)
recording it commit atomically together.
"""
