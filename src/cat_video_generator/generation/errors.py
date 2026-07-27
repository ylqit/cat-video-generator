class OrchestrationError(RuntimeError):
    """Raised when a pack cannot safely advance through the real Ark workflow."""
