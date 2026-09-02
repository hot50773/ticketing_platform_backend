import enum

class SeatStatus(enum.Enum):
    """
    AVAILABLE -> LOCKED -> RESERVED
    LOCKED -> AVAILABLE
    RESERVED -> AVAILABLE
    """
    AVAILABLE = "AVAILABLE" # 可預訂
    LOCKED = "LOCKED" # 暫時鎖定
    RESERVED = "RESERVED" # 已預訂

class RedisSeatStatus(int, enum.Enum):
    AVAILABLE = 0
    LOCKED = 1
    RESERVED = 2

class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    CONFIRMED = "CONFIRMED"

class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

