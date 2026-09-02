class EntityNotFoundError(Exception):
    """Raised when a requested domain entity does not exist or has been soft-deleted."""
    pass


class EntityConflictError(Exception):
    """Raised when a unique constraint or domain uniqueness rule is violated."""
    pass


class BusinessRuleViolationError(Exception):
    """Raised when an operation is rejected by a domain rule (maps to HTTP 409)."""
    pass


class ActiveEventsExistError(BusinessRuleViolationError):
    """Raised when attempting to delete a venue that has active events linked to it."""
    pass


class InvalidEventTimeError(BusinessRuleViolationError):
    """Raised when start_at/end_at violate the holding-time rules (maps to HTTP 422)."""
    pass


class EventPublishedError(BusinessRuleViolationError):
    """Raised when an operation is only allowed while the event is unpublished."""
    pass


class TicketPriceNotReadyError(BusinessRuleViolationError):
    """Raised when publishing an event whose zones still have no ticket price."""
    pass


class ActiveOrdersExistError(BusinessRuleViolationError):
    """Raised when unpublishing or deleting an event that still has live orders."""
    pass


# Messages
VENUE_NOT_FOUND_MSG = "Venue with id {venue_id} not found"
ZONE_NOT_FOUND_MSG = "Zone with id {zone_id} not found"
SEAT_NOT_FOUND_MSG = "Seat with id {seat_id} not found"
EVENT_NOT_FOUND_MSG = "Event with id {event_id} not found"
EVENT_ZONE_NOT_FOUND_MSG = "EventZone with id {event_zone_id} not found in event {event_id}"
