class EntityNotFoundError(Exception):
    """Raised when a requested domain entity does not exist or has been soft-deleted."""
    pass


class EntityConflictError(Exception):
    """Raised when a unique constraint or domain uniqueness rule is violated."""
    pass


class ActiveEventsExistError(Exception):
    """Raised when attempting to delete a venue that has active events linked to it."""
    pass
