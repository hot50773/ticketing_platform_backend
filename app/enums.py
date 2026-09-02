import enum


# StrEnum (Python 3.11+) so that str(), f-strings and SQLAlchemy's
# server_default all yield the plain value ("PENDING"), not "OrderStatus.PENDING".
# Only for string-valued enums; int-valued ones must stay IntEnum / plain Enum.
class SeatStatus(enum.StrEnum):
    """
    AVAILABLE -> LOCKED -> RESERVED
    LOCKED -> AVAILABLE
    RESERVED -> AVAILABLE
    """
    AVAILABLE = "AVAILABLE" # 可預訂
    LOCKED = "LOCKED" # 暫時鎖定
    RESERVED = "RESERVED" # 已預訂

class OrderStatus(enum.StrEnum):
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    CONFIRMED = "CONFIRMED"

class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
