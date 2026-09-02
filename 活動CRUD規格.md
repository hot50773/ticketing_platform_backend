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
3. 上架 / 下架
   - 下架規則：已有訂單的活動不可下架
   - 上架規則：票價尚未設定完成，不可上架
## API
- update_event_profile PATCH /events/:id/profile
- update_event_time PATCH /events/:id/holding_time
  - flow: validate started_time and ended_time -> update_event_time
- update_event_status PATCH /events/:id/status
  - flow: 
    - 上架：validate all ticket_price is set -> update_event_status
    - 下架：validate no active order -> update_event_status

# 更新活動的場地座位
- precondition: 已上架的活動不可編輯場地的分區、座位；只可以更新票價
1. 新增分區 & 該區座位
2. 批量更新分區座位（依照需求新增 or 更新 or 刪除該分區底下的所有座位）
3. 更新分區詳情（name, price, detail）
## API
- create_event_zone POST /events/:id/event_zones (validate: 已上架的活動不可建立新的分區)
- update_event_zone PATCH /events/:id/event_zones/:id/profile
- update_event_seat PATCH /events/:id/event_zones/:id/seats (validate: 已上架的活動不可更新座位)

# 刪除活動
- precondition -> 已有成立訂單的活動不可刪除
## API
- delete_event DELETE /events/:id