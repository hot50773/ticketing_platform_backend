import enum


class SeatStatus(enum.StrEnum):
    """
    AVAILABLE -> RESERVED
    RESERVED -> AVAILABLE

    暫時鎖定（購物車保留中）不再是一個 DB 狀態：那是 Redis bitmap 的職責，
    寫進資料庫只會讓兩邊各有一份會漂移的真相。若要改回來，除了打開下面那行
    還得再寫一支 migration 把 LOCKED 加回 seatstatus enum。
    """
    AVAILABLE = "AVAILABLE" # 可預訂
    # LOCKED = "LOCKED" # 暫時鎖定
    RESERVED = "RESERVED" # 已預訂

class OrderStatus(enum.StrEnum):
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    CONFIRMED = "CONFIRMED"

class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class RedisBitUnit(enum.StrEnum):
    # --- Unsigned (無號整數)：只存正數與 0 ---
    UINT1 = "u1"  # 1 位元一組 (存 2 種狀態，值: 0~1)   -> 適合: 開關、布林值
    UINT2 = "u2"  # 2 位元一組 (存 4 種狀態，值: 0~3)   -> 適合: 四種等級、狀態機
    UINT4 = "u4"  # 4 位元一組 (存 16 種狀態，值: 0~15) -> 適合: 月份、16種狀態
    UINT8 = "u8"  # 8 位元一組 (1個字節，值: 0~255)     -> 適合: 年齡、百分比值

    # --- Signed (有號整數)：可存正負數與 0 ---
    INT8 = "i8"   # 8 位元一組 (1個字節，值: -128~127)  -> 適合: 溫度、正負增減值




class RedisSeatExistence(enum.IntEnum):
    """座位是否存在"""
    NOT_EXIST = 0 # 0 一定要保留給不存在的座位，當有人嘗試讀取極大的 offset，仍舊能在不影響效能情況下，回 0 給他
    EXIST = 1


class RedisSeatSoldStatus(enum.IntEnum):
    """座位是否售出"""
    NOT_SOLD = 0 # 未售出
    SOLD = 1 # 已售出
