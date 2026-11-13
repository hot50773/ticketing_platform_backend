# 大型場地的售票系統 Backend

## 專案簡介
這是一個針對大型活動的售票系統，支援萬人場地的演唱會售票。
## 功能
後台
- 管理員可建立場地與座位表
- 管理員可以建立演唱會等活動

前台（售票網站）
- 顧客可瀏覽公開售票的演唱會等活動
- 顧客可以一次預訂同一活動的多個座位

## 非功能性需求
- 資料正確性 → 門票超賣問題
- 高併發下的可用性 → 瞬間大流量搶票情境

## 技術 Stack

| 類別           | 使用                                                                          |
|--------------|-----------------------------------------------------------------------------|
| 語言           | Python 3.14                                                                 |
| Web 框架       | FastAPI + Uvicorn                                                           |
| ORM / Schema | SQLModel（SQLAlchemy async）、Pydantic                                          |
| 資料庫          | PostgreSQL 16（asyncpg 驅動）                                                   |
| Migration    | Alembic                                                                     |
| 快取 / 鎖位      | Redis（redis-stack-server）：座位 bitmap、Lua 腳本做原子鎖位與釋放                          |
| 背景工作 (MQ)    | taskiq + taskiq-redis（Redis Streams）：付款期限到期的延遲任務，定時任務也由 taskiq scheduler 負責 |
| 依賴注入         | dependency-injector                                                         |
| 套件管理         | uv（以 `uv.lock` 為準）                                                          |
| 測試           | pytest、pytest-asyncio、httpx                                                 |

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

## API
啟動後開 <http://localhost:8000/docs> 看 Swagger 文件。API 都掛在 `/api/v1` 底下，分成前台與後台：

[//]: # (TODO: 這格表格太冗長，且 Swagger 就能看到，要簡化或直接刪除)
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

customer 只能用 Google 登入；manager 用 email 與密碼登入。

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

設定由 `app/config.py` 讀取，預設讀 `.env`

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
  main.py            FastAPI app
  config.py          環境變數設定
  containers.py      依賴注入容器
  routers/           HTTP 路由（auth / manager / venue / event / order），各分前台與後台
  services/          業務流程；validators/ 放業務規則檢查
  aggregates/        領域聚合（event、order）
  repositories/      Postgres 存取
  models/            SQLModel 資料表
  schemas/           API 與內部用的 Pydantic schema
  cache/             Redis 存取；lua/ 放原子操作腳本
  libs/security/     密碼雜湊、access token、Google ID token 驗證
  libs/payment.py    金流介面（PaymentGateway）與開發用的假金流
  scripts/           CLI 工具（create_admin）
  workers/           taskiq broker 與 worker 入口
  queue/delayed/     延遲任務（付款期限到期）
migrations/          Alembic migration
tests/unit/          單元測試（fake 取代外部依賴）
tests/e2e/           走真實 Postgres / Redis 的測試
```

## 文件
- [`CLAUDE.md`](CLAUDE.md) — 註解規則與 router 命名慣例。

## 下一階段
- 高併發情境下的限流機制
- 活動搜尋功能
