# Ticket Lock MVP 系統設計與開發計畫 (已確認版)

本文件已根據使用者回饋（使用 Redis `BITFIELD` 進行位元組操作、改為 Batch Lock 批次鎖定介面、且同時實作 Cron 排程與 Redis Keyspace Notification）進行更新。

## 1. 系統架構分析 (Three-Tier Architecture)
本系統嚴格遵循經典三層架構：
1. **API 層 (`app/routers/event.py`)**
   - 負責接收 HTTP 請求，進行 Schema 驗證，並呼叫 Service 層。
   - 使用 FastAPI 及 `dependency_injector` 進行相依性注入。
2. **Service 層 (`app/services/event.py`)**
   - 封裝所有核心商業邏輯（如批次鎖票流程、訂單逾期釋放、狀態同步）。
   - 不直接與資料庫或 Redis 互動，透過 Repository 與 Cache 層實現。
3. **Repository/Cache 層**
   - **Repository (`app/repositories/event.py` & `order.py`)**：負責 PostgreSQL 資料庫讀寫（使用 SQLModel 於 Transaction 中執行）。
   - **Cache (`app/cache/seat.py`)**：負責 Redis 上的座位表狀態管理與資源競爭控制。

---

## 2. 核心技術方案設計

### 2.1. Redis Bitfield 座位狀態管理
- **Key 格式**：`event_zone_seats:{event_zone_id}`
- **儲存格式**：以 Redis String (Byte Array) 儲存。
- **座位 Offset**：座位在 Byte Array 中的 `offset` 定義為 `seat.serial_number - 1`。由於每個座位佔 1 個位元組（8 bits），因此其 Bit Offset 為 `(serial_number - 1) * 8`。
- **狀態對應**：
  - `0x00` -> `RELEASED` (AVAILABLE)
  - `0x01` -> `LOCK` (LOCKED)
  - `0x02` -> `RESERVED` (CONFIRMED)
- **避免解碼錯誤**：使用 Redis `BITFIELD` 命令（如 `BITFIELD GET u8` / `BITFIELD SET u8`）。因為 `BITFIELD` 的回傳與參數均為整數型別，因此在 `decode_responses=True` 的連線下亦能安全執行，不會引發字串解碼錯誤。

### 2.2. 原子性批次鎖票 (Atomic Batch Lock)
使用 Lua Script 實作「全成功或全失敗 (All-or-nothing)」的原子性批次鎖定：
```lua
local key = KEYS[1]
local offsets = ARGV -- 傳入一組座位的 offset (0-indexed)

-- 1. 檢查是否所有指定的座位皆為 0x00 (可用)
for _, offset_str in ipairs(offsets) do
    local offset = tonumber(offset_str)
    local bit_offset = offset * 8
    local res = redis.call('BITFIELD', key, 'GET', 'u8', bit_offset)
    local status = res[1] or 0
    if status ~= 0 then
        return 0 -- 只要有一個座位不是 0x00，即鎖定失敗
    end
end

-- 2. 若全部可用，則全部設為 0x01 (LOCK)
for _, offset_str in ipairs(offsets) do
    local offset = tonumber(offset_str)
    local bit_offset = offset * 8
    redis.call('BITFIELD', key, 'SET', 'u8', bit_offset, 1)
end

return 1 -- 鎖定成功
```

### 2.3. 雙軌制 10 分鐘過期釋放機制
1. **Redis Keyspace Notification**：
   - 監聽 `order_ttl:{order_id}` 的 `expired` 事件。
   - 收到事件後，非同步解析出 `order_id`，讀取訂單資訊，將 Redis 座位還原為 `0x00`，並將 DB 中的 EventSeat 狀態設回 `RELEASED` 且軟刪除訂單。
2. **背景排程任務 (Cron/Worker)**：
   - 在 FastAPI lifespan 中啟動一個循環的背景 Task，每 60 秒執行一次。
   - 掃描 DB 中所有 `PENDING` 且建立時間超過 10 分鐘的訂單。
   - 逐筆執行釋放邏輯，確保即使 Redis 事件漏失，DB 資料亦能得到最終一致性清理。

---

## 3. 介面定義 (Interface Definition)

### 3.1. API 請求與回應 Schema (`app/schemas.py`)
```python
class LockSeatsRequest(SQLModel):
    user_id: int
    seat_ids: list[int]

class UnlockSeatsRequest(SQLModel):
    user_id: int
    seat_ids: list[int]
```

### 3.2. Cache 層 Interface (`app/cache/seat.py`)
```python
class SeatLockCache:
    async def init_zone_seats(self, event_zone_id: int, size: int, seat_statuses: dict[int, int]) -> None:
        """初始化該 Zone 在 Redis 中的 byte array。若 key 不存在則填入 DB 當前狀態。"""
        
    async def lock_seats(
        self, event_zone_id: int, seat_offsets: list[int], order_id: int, ttl_seconds: int = 600
    ) -> bool:
        """呼叫 Lua 腳本原子性鎖定一個 Zone 中的多個座位，成功則設置 order_ttl:{order_id} 鍵值。"""

    async def unlock_seats(self, event_zone_id: int, seat_offsets: list[int], order_id: int) -> bool:
        """將 Redis 中對應座位的狀態從 0x01 還原為 0x00，並清除 order_ttl:{order_id} 鍵值。"""
```

### 3.3. Repository 層 Interface (`app/repositories/event.py`)
```python
class EventRepository:
    async def get_event_seats_by_ids(self, session: AsyncSession, seat_ids: list[int]) -> list[EventSeat]:
        """批次取得 EventSeat，並預載入相關的 Zone 資訊。"""
        
    async def get_event_seats_with_lock(self, session: AsyncSession, seat_ids: list[int]) -> list[EventSeat]:
        """以 SELECT FOR UPDATE 鎖定多筆 EventSeat，防止併發更新。"""

    async def update_seats_status(self, session: AsyncSession, seat_ids: list[int], status: SeatStatus) -> None:
        """批次更新 DB 中 EventSeat 的狀態。"""
```

### 3.4. Service 層 Interface (`app/services/event.py`)
```python
class EventService:
    async def lock_seats(self, event_id: int, seat_ids: list[int], user_id: int) -> bool:
        """
        批次鎖定座位流程：
        1. 建立一筆狀態為 PENDING 的 Order。
        2. 將座位按 event_zone_id 分組，計算每個座位的 offset。
        3. 對每個 Zone 呼叫 Cache 層進行 Redis 鎖定。若其中一區鎖定失敗，則回滾已鎖定的所有 Zone 座位。
        4. 寫入 DB 交易：
           - 以 pessimistic lock 鎖定所有 EventSeat。
           - 將 EventSeat 狀態更新為 LOCK。
           - 建立對應的 OrderTicket 關聯，寫入金額。
        5. 若 DB 交易失敗，在 Redis 中回滾已鎖定的座位狀態，並回傳失敗。
        """

    async def release_expired_orders(self) -> None:
        """掃描並釋放已過期的訂單。"""
```

---

## 4. TDD 測試案例與驗證條件 (Acceptance Criteria)

1. **批次鎖票成功 (Success Case)**:
   - 輸入 `seat_ids = [1, 2]`，兩座位皆為可用。
   - 預期：Redis 中對應 bit 被設為 `0x01`，`order_ttl:{order_id}` 建立，DB 中 `EventSeat` 狀態更新為 `LOCK`，且建立 `Order` 與對應的 `OrderTicket`。
2. **鎖票衝突失敗 (Conflict Case)**:
   - 用戶 A 已鎖定座位 1。用戶 B 嘗試鎖定 `[1, 2]`。
   - 預期：鎖定失敗（All-or-nothing），座位 2 仍保持可用，無新訂單建立。
3. **過期釋放 (Expiration Case)**:
   - 用戶 A 鎖定座位 1 與 2。10 分鐘後（或調用釋放邏輯）。
   - 預期：Redis 狀態變回 `0x00`，DB 中 `EventSeat` 狀態更新為 `RELEASED`，`OrderTicket` 依然存在但 `Order` 被軟刪除。
