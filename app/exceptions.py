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


class EventHasNoZoneError(BusinessRuleViolationError):
    """Raised when publishing an event that has no zone at all."""
    pass


class TicketPriceNotReadyError(BusinessRuleViolationError):
    """Raised when publishing an event whose zones still have no ticket price."""
    pass


class SeatOccupiedError(BusinessRuleViolationError):
    """Raised when reshaping a zone whose seats are locked or reserved."""
    pass


class ActiveOrdersExistError(BusinessRuleViolationError):
    """Raised when unpublishing or deleting an event that still has live orders."""
    pass


class EventNotPublishedError(BusinessRuleViolationError):
    """Raised when ordering from an event that is not published yet, so there
    is nothing to buy. The mirror of EventPublishedError, which guards the
    admin operations that only an unpublished event allows."""
    pass


class EventStartedError(BusinessRuleViolationError):
    """Raised when ordering from an event that has already started. Sales close
    at start_at: a seat sold after the doors open is one nobody can occupy."""
    pass


class OrderNotPayableError(BusinessRuleViolationError):
    """Raised when paying an order that is not in a payable state - already
    cancelled, already paid, or with a payment still in flight."""
    pass


class OrderNotCancellableError(BusinessRuleViolationError):
    """Raised when cancelling an order that is already cancelled or already
    paid for. A paid order goes through a refund, not a cancellation."""
    pass


class OrderNotDeletableError(BusinessRuleViolationError):
    """Raised when soft-deleting a paid order. Deleting releases the seats, so
    a settled purchase must be refunded rather than made to disappear."""
    pass


class PaymentDeadlinePassedError(BusinessRuleViolationError):
    """Raised when paying an order whose payment_deadline has gone by; the
    seats it held are no longer guaranteed to be its own."""
    pass


# Messages
VENUE_NOT_FOUND_MSG = "Venue with id {venue_id} not found"
ZONE_NOT_FOUND_MSG = "Zone with id {zone_id} not found"
SEAT_NOT_FOUND_MSG = "Seat with id {seat_id} not found"
EVENT_NOT_FOUND_MSG = "Event with id {event_id} not found"
EVENT_ZONE_NOT_FOUND_MSG = "EventZone with id {event_zone_id} not found in event {event_id}"
EVENT_SEAT_NOT_FOUND_MSG = "EventSeat(s) {event_seat_ids} not found in event {event_id}"
ORDER_NOT_FOUND_MSG = "Order with id {order_id} not found"
