# Project Rules: Ticket Lock

根據 `task.md`，本專案必須嚴格遵守以下通用規範：

## 1. 溝通與回應規範 (Communication & Interaction)
* **語言風格**：以繁體中文 (zh-tw) 回答，但**專有名詞與專業術語一律保留原始 English**。
* **精簡回應**：遵循「精簡原則」，提供結論或核心要點即可，避免冗長重複的贅述。
* **官方文件優先**：引進或使用任何第三方庫/API 前，必須優先查詢並遵循官方文件。

## 2. 批判性思維與工作流程 (Critical Thinking & Workflow)
* **批判性審查**：主動指出使用者輸入的潛在邏輯漏洞或設計問題；若提議具備 high risk 或不合理，必須提出警告（「罵醒我」）。
* **範圍界定**：明確劃定修改邊界，只修改討論後一致同意的範圍，不進行無關的發散修改。
* **任務分解與介面先行**：
  1. 將開發任務拆解成細緻的子任務與具體步驟。
  2. 開始任何實作前，**必須先與使用者討論並擬定介面定義 (Interface)**。
  3. 雙方達成共識後，方可開始撰寫實作。
* **TDD 測試驅動開發**：必須使用 `pytest` 撰寫簡潔好維護的測試，且 Test cases 與驗證條件 (Acceptance Criteria) 需與使用者討論並審核。
* **Code Review 與 Commit**：每個子任務實作完成後，先執行 Code Review 確保無誤，再進行 atomic commit。Commit 訊息需遵循意圖導向與格式規範。
* **AI 文件存放規範**：所有過程中產生的規劃、學習與任務文件，必須存放在專案根目錄的 `ai/` 資料夾（如 `ai/plans/`），嚴禁存放在專案外或 `docs/` 中。

## 3. 程式設計與架構規範 (Coding & Architecture Standards)
* **經典三層架構**：
  * **API 層 (`app/routers/`)**：FastAPI 對外接口，需嚴格定義 API Schema。
  * **Service 層 (`app/services/`)**：撰寫所有的**核心商業邏輯**。
    * *禁忌*：Service 層不得直接存取 PostgreSQL (DB) 或 Redis，亦不能直接調用外部 API。
    * *職責分離*：如有多個面向的業務（例如：欄位驗證、票價計算），需拆分成獨立的類別 (Class)，並以 DI (Dependency Injection) 的方式注入 Service。
  * **Repository 層**：
    * 資料庫操作：`app/repositories/`，所有牽涉 DB 的讀寫，每個操作都必須在 Transaction Session 中進行。
    * 快取操作：`app/caches/` (或 `app/cache/`)，所有牽涉 Redis 的讀寫都在這裡。
* **模組劃分**：採用領域驅動設計 (DDD) 的概念進行模組拆分。
* **資料傳輸規範**：不同物件或服務間進行資料傳輸時，**必須使用 SQLModel、Pydantic Model、RedisOM 等結構化資料 Schema**，嚴禁使用無結構的 `dict` 或 `tuple`。
* **SOLID 原則**：嚴格遵守 SOLID（特別是 Single Responsibility Principle 單一職責原則），代碼本身必須清晰展現其設計意圖，並附上高質量的 English 註釋。
