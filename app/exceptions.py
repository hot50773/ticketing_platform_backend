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

class UnknownSeatLockFailureError(Exception):
    """Raised when lock_seats.lua reports a reason the Python side does not
    handle. Not a BusinessRuleViolationError: nobody broke a rule, the script
    and RedisSeatLockFailure have drifted apart."""
    pass


class SeatUnexistError(BusinessRuleViolationError):
    """Raised when ordering a seat that is not on the event's layout at all.
    Distinct from SeatOccupiedError: the seat was never there, so telling the
    customer it is taken would send them back to refresh and try again."""
    pass


class ActiveOrdersExistError(BusinessRuleViolationError):
    """Raised when unpublishing or deleting an event that still has active orders."""
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

class SeatReleaseError(BusinessRuleViolationError):
    """Raised when releasing seats."""
    pass

class SeatNotReleasableError(SeatReleaseError):
    """Raised when seats cannot be handed back because the rows are gone -
    retired with their event, or never there. Its own type so the release
    worker can tell terminal from retryable with `except`."""
    pass

class SeatReserveError(BusinessRuleViolationError):
    """Raised when reserving seats."""
    pass

# Messages
VENUE_NOT_FOUND_MSG = "Venue with id {venue_id} not found"
ZONE_NOT_FOUND_MSG = "Zone with id {zone_id} not found"
SEAT_NOT_FOUND_MSG = "Seat with id {seat_id} not found"
EVENT_NOT_FOUND_MSG = "Event with id {event_id} not found"
EVENT_ZONE_NOT_FOUND_MSG = "EventZone with id {event_zone_id} not found in event {event_id}"
EVENT_SEAT_NOT_RELEASABLE_MSG = (
    "Event seats {event_seat_ids} could not be released: they are soft-deleted "
    "or do not exist."
)
EVENT_SEAT_UNEXIST_MSG = (
    "Seat serial {seat_serial} is not on the layout of event #{event_id} "
    "zone #{zone_id}."
)
EVENT_SEAT_OCCUPIED_MSG = (
    "Seat serial {seat_serial} in event #{event_id} zone #{zone_id} is "
    "already taken."
)
EVENT_SEAT_NOT_FOUND_MSG = "EventSeat(s) {event_seat_ids} not found in event {event_id}"
ORDER_NOT_FOUND_MSG = "Order with id {order_id} not found"
