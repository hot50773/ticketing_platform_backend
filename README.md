# 售票系統 - 暫時鎖票
演唱會線上售票的後端 API：場地與座位管理、活動上下架、下單時暫時鎖住座位，
付款期限一過就把座位釋放回票池。

## 技術 Stack

| 類別 | 使用 |
| --- | --- |
| 語言 | Python 3.14 |
| Web 框架 | FastAPI + Uvicorn |
| ORM / Schema | SQLModel（SQLAlchemy 2, async）、Pydantic v2、pydantic-settings |
| 資料庫 | PostgreSQL 16（asyncpg 驅動） |
| Migration | Alembic |
| 快取 / 鎖位 | Redis（redis-stack-server）：座位 bitmap、Lua 腳本做原子鎖位與釋放 |
| 背景工作 | taskiq + taskiq-redis（Redis Streams）：付款期限到期的延遲任務，定時任務也由 taskiq scheduler 負責 |
| 依賴注入 | dependency-injector |
| 套件管理 | uv（以 `uv.lock` 為準） |
| 測試 | pytest、pytest-asyncio、httpx |

## 前置需求

- [uv](https://docs.astral.sh/uv/)：會依 `.python-version` 自動安裝 Python 3.14
- Docker 與 Docker Compose：在本機跑 Postgres 和 Redis

## 快速開始

```bash
# 1. 安裝依賴（含 dev group）
uv sync

# 2. 建立環境變數檔，並填入 JWT_SECRET_KEY（必填，否則 API 無法啟動）
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. 啟動 Postgres（:15432）與 Redis（:16379）
docker compose up -d

# 4. 建立資料表
uv run alembic upgrade head

# 5. 啟動 API
uv run uvicorn main:app --reload
```

啟動後開 <http://localhost:8000/docs> 看 Swagger 文件。API 都掛在 `/api/v1` 底下，分成前台與後台：

| 前台 | 用途 |
| --- | --- |
| `/auth` | customer 以 Google 登入（`/auth/google`）、查看自己（`/auth/me`） |
| `/events` | 販售中活動列表、已上架活動的詳情與座位 |
| `/venues`、`/zones`、`/seats` | 場地、區域、座位查詢 |
| `/orders` | 下單、付款、取消（需 customer 登入） |

| 後台（`/admin`，需 manager 登入） | 用途 |
| --- | --- |
| `/admin/auth` | manager 以密碼登入（`/admin/auth/login`）、查看自己 |
| `/admin/managers` | 新增、列出、停用與重新啟用 manager（限 ADMIN） |
| `/admin/events` | 活動建立、編輯、上下架、座位配置；可讀未上架的活動 |
| `/admin/venues`、`/admin/zones` | 建立場地、區域、座位；刪除場地 |
| `/admin/orders` | 刪除逾期未付款的訂單 |

customer 只能用 Google 登入；manager 用 email 與密碼登入，連續失敗太多次會被暫時擋下（429）。

### 背景 worker

API 只負責把「付款期限到期檢查」排進 Redis，實際執行要另外開兩個 process：

```bash
# 消化佇列裡的任務
uv run taskiq worker app.workers.main:broker app.queue.delayed.process_expired_order

# 把排定時間到了的任務推進佇列（解析度約 1 分鐘）
uv run taskiq scheduler app.workers:scheduler app.queue.delayed.process_expired_order
```

沒開這兩個 process 的話，逾期訂單不會被自動處理。

### 建立第一個 admin

ADMIN 帳號只能從 CLI 建立；API 只讓 ADMIN 新增 MANAGER（`POST /api/v1/admin/managers`）。
資料表建好之後執行：

```bash
uv run python -m app.scripts.create_admin --name Root --email root@example.com
```

指令會提示輸入密碼（至少 8 個字元，輸入時不會顯示）。email 一律存成小寫，之後用
`POST /api/v1/admin/auth/login` 登入。

## 環境變數

設定由 `app/config.py` 讀取，預設讀 `.env`；設 `ENV_FILE` 可以換成別的檔案。

| 變數 | 說明 | 開發預設值 |
| --- | --- | --- |
| `DATABASE_URL` | Postgres 連線字串 | `postgresql+asyncpg://postgres:postgres_password@localhost:15432/ticketing_platform_noai` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT` | 給 docker compose 建立資料庫用 | 見 `.env.example` |
| `REDIS_URL` | Redis 連線字串 | `redis://localhost:16379/0` |
| `REDIS_HOST` / `REDIS_PORT` | 給 docker compose 對外開 port 用 | `localhost` / `16379` |
| `JWT_SECRET_KEY` | 簽 access token 的金鑰，**必填**，至少 32 個字元 | 無，用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 產生 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | access token 有效分鐘數 | `60` |
| `GOOGLE_CLIENT_ID` | Google Sign-In 的 OAuth client ID；空白代表不開放 Google 登入 | 空白 |
| `LOGIN_MAX_FAILURES` | manager 登入同一組（IP、email）允許的失敗次數 | `5` |
| `LOGIN_FAILURE_WINDOW_SECONDS` | 上面失敗次數的計算時間窗（秒） | `900` |
| `DB_ECHO` | 印出 SQL 語句 | `false` |

## 資料庫 Migration

```bash
uv run alembic upgrade head                            # 升到最新
uv run alembic downgrade -1                            # 退一版
uv run alembic revision --autogenerate -m "說明"        # 依 model 變更產生新版本
```

Migration 檔在 `migrations/versions/`。

## 測試

e2e 測試每一題都會 `TRUNCATE` 所有資料表並清空 Redis，所以用的是獨立的測試環境
（`docker-compose.test.yaml`，Postgres :55432、Redis :56379），不會碰到開發資料。
`tests/conftest.py` 會檢查設定，指向的不是測試環境就拒絕執行。

```bash
# 啟動測試用的 Postgres / Redis（--env-file 不能省，否則會用到開發用的資料庫名稱）
docker compose --env-file .env.test -f docker-compose.test.yaml up -d

# 建立測試資料庫的資料表
ENV_FILE=.env.test uv run alembic upgrade head

# 跑全部測試（pytest 會自動改讀 .env.test）
uv run pytest

# 只跑單元測試或 e2e
uv run pytest tests/unit
uv run pytest tests/e2e
```

測試環境的資料不需要保留，壞掉就 `docker compose -f docker-compose.test.yaml down -v` 再重建。

## 專案結構

```
main.py              Uvicorn 入口（re-export app.main:app）
app/
  main.py            FastAPI app、lifespan、例外對應 HTTP 狀態碼
  config.py          環境變數設定
  containers.py      依賴注入容器
  routers/           HTTP 路由（auth / manager / venue / event / order），各分前台與後台
  services/          業務流程；validators/ 放業務規則檢查
  aggregates/        領域聚合（event、order）
  repositories/      Postgres 存取
  models/            SQLModel 資料表
  schemas/           API 與內部用的 Pydantic schema
  cache/             Redis 存取（座位 bitmap、活動快取、登入失敗計數）；lua/ 放原子操作腳本
  libs/security/     密碼雜湊、access token、Google ID token 驗證
  scripts/           CLI 工具（create_admin）
  workers/           taskiq broker 與 worker 入口
  queue/delayed/     延遲任務（付款期限到期）
migrations/          Alembic migration
tests/unit/          單元測試（fake 取代外部依賴）
tests/e2e/           走真實 Postgres / Redis 的測試
```

## 文件

- [`活動CRUD規格.md`](活動CRUD規格.md) — 活動 CRUD 的行為規格與歷次更正紀錄。
- [`CLAUDE.md`](CLAUDE.md) — 註解規則與 router 命名慣例。

## 設計筆記

### 背景：各平台的暫時鎖票
各大台灣售票系統的「暫時鎖票」保留時間各有不同。此機制的目的是在您選好座位後，提供有限的時間填寫購票資料並完成結帳。

常見平台的鎖票時間與清票規則如下：
- KKTIX：約 15 分鐘。若選好座位按下一步進入填單畫面，系統會保留 15 分鐘；若逾時未付款，票券會自動釋放回票池。
- 拓元 (tixCraft)：刷卡結帳通常約 10 到 15 分鐘。若訂單逾期未付款或刷卡失敗，系統常會在開賣後的 10 至 15 分鐘 或後續特定時間釋出退票。
- 寬宏售票 / ibon 售票：依照不同活動設定，通常刷卡或虛擬帳號繳費約有 10 到 15 分鐘 的暫時保留期。
