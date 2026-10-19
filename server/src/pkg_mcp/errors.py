"""Domain errors. The server layer turns these into MCP tool errors whose
message is shown to the LLM, so every message says what to do next."""


class KnowledgeGraphError(Exception):
    """Base class for errors caused by the caller's input."""


class EntityNotFound(KnowledgeGraphError):
    pass


class AmbiguousEntity(KnowledgeGraphError):
    pass


class InvalidInput(KnowledgeGraphError):
    pass


class PolicyViolation(KnowledgeGraphError):
    pass
