import enum

class SeatStatus(enum.Enum):
    LOCK = "LOCK" # 暫時鎖定
    RELEASED = "RELEASED"
    RESERVED = "RESERVED" # 已預訂

class RedisSeatStatus(int, enum.Enum):
    RELEASED = 0
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

