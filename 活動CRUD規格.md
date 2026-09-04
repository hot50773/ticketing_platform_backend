# 活動
# 建立活動（未上架）
- API 接收活動詳細資訊
- validations -> 
  - started_at: 要大於現在
  - ended_at: 要晚於 started_at
- db -> 建立 Event Entity 和 EventZone Entity 和 EventSeat Entity -> Zone 和 Seat 直接從模板複製一份
- rules: 活動預設為未上架
- TODO: 
  - 需修改 EventZone 和 EventSeat 的 Model，所有資訊都要複製，不要直接參考 Zone 和 Seat
  - Event 新增欄位：is_active
  - EventZone 新增欄位：ZoneName original_zone_id；移除欄位：zone_id
  - EventSeat 新增欄位：row col SerialNumber original_seat_id；移除欄位：seat_id

## API
- create_event
  - flow: create_event -> autogenerate_event_zone, autogenerate_event_seat (should be atomic)

# 更新活動（分段式更新）
1. 更新活動詳情
2. 更新活動時間
   - precondition -> 已上架的活動不可更新時間
   - validations -> 
     - started_at: 要大於現在
     - ended_at: 要晚於 started_at
3. 上架 / 下架（2026-09-04 更正：兩者流程完全不同，拆成獨立 API）
   - 下架規則：已有訂單的活動不可下架
   - 上架規則：票價尚未設定完成，不可上架
## API
- update_event_profile PATCH /events/:id/profile
- update_event_time PATCH /events/:id/holding_time
  - flow: validate started_time and ended_time -> update_event_time
- publish_event POST /events/:id/publish
  - flow: validate 至少一個分區且 all ticket_price is set -> publish
- unpublish_event POST /events/:id/unpublish
  - flow: validate no active order -> unpublish

# 更新活動的場地座位
- precondition: 已上架的活動不可編輯場地的分區、座位；分區的 name 和 price 上架後仍可更新（2026-09-02 更正）
1. 新增分區 & 該區座位
2. 批量更新分區座位（依照需求新增 or 刪除該分區底下的座位）
   - serial_number 由後端配號，是 Redis bitmap 的 offset（offset = serial_number - 1）
   - 從場地模板複製時直接沿用 Seat.serial_number
   - 編號在分區內唯一，但**不要求連續**；刪除座位留下的空洞不回收
   - 只做增刪，不重建、不重新編號，倖存的座位一律保留原編號
     （2026-09-03 更正：原訂為重新編號 1..N）
3. 更新分區詳情（name, price, detail）
## API
- create_event_zone POST /events/:id/event_zones (validate: 已上架的活動不可建立新的分區)
- update_event_zone PATCH /events/:id/event_zones/:id/profile
- update_event_seat PATCH /events/:id/event_zones/:id/seats (validate: 已上架的活動不可更新座位)

# 刪除活動
- precondition -> 已有訂單的活動不可刪除（不限訂單狀態，2026-09-02 更正）
## API
- delete_event DELETE /events/:id