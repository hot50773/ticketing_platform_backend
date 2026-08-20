import enum

class SeatStatus(enum.Enum):
    LOCK = "LOCK" # 暫時鎖定
    RELEASED = "RELEASED"
    RESERVED = "RESERVED" # 已預訂
