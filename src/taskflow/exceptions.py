"""
Custom exceptions for taskflow operations.
"""

class TaskCancelledError(Exception):
    """Raised when a task is cancelled."""
    pass

class TaskTimeoutError(Exception):
    """Raised when a task times out."""
    pass

class PreExecutionValidationError(Exception):
    """Raised when task fails pre-execution validation."""
    pass

class TaskGroupValidationError(Exception):
    """Raised when task group fails validation."""
    pass

class ResourceUnavailableError(Exception):
    """Raised when required resources are not available."""
    pass

class DependencyError(Exception):
    """Raised when task dependencies are not satisfied."""
    pass
