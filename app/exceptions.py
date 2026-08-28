class EntityNotFoundError(Exception):
    """Raised when a requested domain entity does not exist or has been soft-deleted."""
    pass


class EntityConflictError(Exception):
    """Raised when a unique constraint or domain uniqueness rule is violated."""
    pass


class ActiveEventsExistError(Exception):
    """Raised when attempting to delete a venue that has active events linked to it."""
    pass


# Messages
VENUE_NOT_FOUND_MSG = "Venue with id {venue_id} not found"
ZONE_NOT_FOUND_MSG = "Zone with id {zone_id} not found"
SEAT_NOT_FOUND_MSG = "Seat with id {seat_id} not found"