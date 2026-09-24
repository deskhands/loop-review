# Design review rubric

Check the design against the user request, repository reality, and applicable `AGENTS.md`.

Prioritize:
- requirement coverage and internal correctness
- simplicity and avoidance of unnecessary abstraction
- current-need justification; reject architecture for hypothetical future needs
- fit with existing architecture and terminology
- failure modes, error handling, lifecycle and recovery
- compatibility, migration, operability and testability
- scope control and implementation feasibility
- contradictions, unspecified invariants, and hidden coupling

A design can freeze with non-blocking suggestions. Any confirmed binding-rule violation prevents PASS.
