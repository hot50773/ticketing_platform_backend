# 售票系統 - 暫時鎖票
各大台灣售票系統的「暫時鎖票」保留時間各有不同。此機制的目的是在您選好座位後，提供有限的時間填寫購票資料並完成結帳。

常見平台的鎖票時間與清票規則如下：
- KKTIX：約 15 分鐘。若選好座位按下一步進入填單畫面，系統會保留 15 分鐘；若逾時未付款，票券會自動釋放回票池。
- 拓元 (tixCraft)：刷卡結帳通常約 10 到 15 分鐘。若訂單逾期未付款或刷卡失敗，系統常會在開賣後的 10 至 15 分鐘 或後續特定時間釋出退票。
- 寬宏售票 / ibon 售票：依照不同活動設定，通常刷卡或虛擬帳號繳費約有 10 到 15 分鐘 的暫時保留期。

### 功能
- 有一個表演場地要開演唱會，需要線上售票系統販售門票。
- 場地內共有 26 個座位，座位編號為：A ~ Z。
- 用戶將特定座位加入購物車後，在該用戶付款完成前，系統會為此用戶保留該座位 15 分鐘。

### DB Schema
User 
user_id

User_Cart
user_id, product_id, seat_id, quantity, price, total_price

Seat_Block
product_id, seat_id, status (available, reserved, sold)

Order_Record
user_id, product_id, seat_id, quantity, price, total_price, status (reserved, paid, cancelled)

### Event Flow
Event1
WHEN
user id:1 -> ADD_TO_CART:A
THEN
ADD Seat A to SEAT_BLOCK (redis key "SEAT_BLOCK:A" 15min)

Event2
WHEN
user id:1 -> PAYMENT_COMPLETE:A
THEN
REMOVE Seat A from SEAT_BLOCK( rm redis key "SEAT_BLOCK:A")

Event3
WHEN
user id:1 -> PAYMENT_TIMEOUT:A
THEN
REMOVE Seat A from SEAT_BLOCK

Event4
WHEN
user id:1 -> REMOVE_FROM_CART:A
THEN
REMOVE Seat A from SEAT_BLOCK

### Dead Lock
在應用程式層級實作座位鎖定（暫時鎖票）機制，確實會遇到死鎖（Deadlock）問題。

雖然單一使用者鎖定單一座位的基本操作看似簡單，但在高併發（High Concurrency）的售票場景下，只要系統的資源鎖定行為符合死鎖的四個必要條件
（互斥、佔有且等待、不可搶佔、循環等待），就會發生死鎖。

以下為在該機制下可能發生死鎖的具體情境，皆為應用程式邏輯設計未妥善規範資源順序所導致：

#### 情境一：多座位加入購物車之交叉等待（Circular Wait on Multiple Seats）
雖然您的事件定義以單一座位（如 Seat A）為例，但在實務上，使用者通常可以一次勾選多個座位加入購物車，或由系統自動劃位。

觸發條件： 應用程式在處理包含多個座位的請求時，是依照「使用者勾選的順序」或「陣列傳入的順序」逐一進行鎖定（加鎖）。

發生過程：

使用者甲發起 ADD_TO_CART: [A, B] 的請求。

使用者乙同時發起 ADD_TO_CART: [B, A] 的請求。

執行緒 1（處理甲的請求）成功鎖定座位 A，準備鎖定座位 B。

執行緒 2（處理乙的請求）成功鎖定座位 B，準備鎖定座位 A。

結果： 雙方各自持有對方需要的資源並持續等待，形成應用程式層級的死鎖。

[//]: # (
解決思路：
    1. 順序隨機、導致死結 -> 能保證資源操作順序嗎？或是能一次把所有想使用的資源全部先鎖起來？（Transaction）
    2. 死結發生時，能設定 Timeout 防止無窮等待嗎？
    3. DB 樂觀鎖？
    4. ......不知道 ="=
)
#### 情境二：狀態更新操作涉及多個實體時的順序衝突（Transaction Deadlock）
在實際的系統架構中，一次事件通常涉及多個資料表或快取結構（例如 SEAT_BLOCK 座位表與 USER_CART 購物車表）。

觸發條件： 不同的事件處理邏輯，對這些實體（Entity）的加鎖順序不一致。

發生過程：

事件 1 (ADD_TO_CART: A) 的邏輯設定為：先鎖定使用者的 USER_CART，再鎖定 SEAT_BLOCK 寫入保留紀錄。

事件 3 (PAYMENT_TIMEOUT: A) 的背景排程任務邏輯設定為：先鎖定逾時的 SEAT_BLOCK 進行釋放，接著去鎖定該使用者的 USER_CART 移除逾時商品。

當使用者剛好在極限時間點發起其他購物車操作（例如加入另一張票），而背景任務同時正在清理他逾時的座位 A 時。

結果： 使用者的操作執行緒持有了購物車鎖等待座位鎖；背景任務執行緒持有了座位鎖等待購物車鎖，導致分散式鎖或資料庫層級死鎖。

#### 情境三：使用者付款與背景釋放機制的競態條件（Race Condition escalating to Deadlock）
發生在 15 分鐘到期的瞬間，系統背景任務與使用者前台操作發生碰撞。

觸發條件： 付款完成邏輯與逾時釋放邏輯需要更新相同的關聯資料（例如訂單狀態與座位狀態），但操作資料的順序相反。

發生過程：

第 15 分鐘整，使用者甲的 PAYMENT_COMPLETE: A 請求送達應用程式（事件 2）。

同一毫秒，系統的 TTL 過期事件或定時任務觸發了 PAYMENT_TIMEOUT: A （事件 3）。

事件 2 的交易（Transaction）開啟，先鎖定了訂單記錄（Order Record）準備更新為「已付款」，接著請求鎖定 SEAT_BLOCK 的座位 A 以移除 15 分鐘的限制狀態。

事件 3 的交易開啟，先鎖定了 SEAT_BLOCK 的座位 A 準備標記為「可販售」，接著請求鎖定訂單記錄以標記為「已取消」。

結果： 兩個並行的資料庫交易（Database Transactions）因為更新順序顛倒，產生 Row-level lock 的死鎖。



