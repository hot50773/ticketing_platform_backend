"""帳號安全強化

- 移除 customer.hashed_password：customer 只能用 Google 登入。舊 user 表存的本來
  就是假值 "1"，沒有密碼可以保留；舊帳號第一次用 Google 登入時依 email 綁定。
- email 一律存小寫，並加 CHECK 擋住之後寫進來的大寫。若既有資料轉小寫後撞到
  UNIQUE，migration 會直接失敗：兩個帳號要合併成哪一個得由人決定。
  非 ASCII 的 email 不在這裡處理：Postgres 的 lower() 與程式端結果不同，若既有
  資料有這種 email，CHECK 會建立失敗，需人工處理。
- manager.is_active：停用的 manager 即使 token 還沒過期也不能再操作。
- order.deleted_by_manager_id：記下是哪個 manager 刪除了訂單；系統釋放逾期訂單時為 NULL。

downgrade 會把 customer.hashed_password 加回來但留空，也不會還原 email 的大小寫。

Revision ID: b7d2e9f04c13
Revises: a3f1c2d4e5b6
Create Date: 2026-11-11 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e9f04c13"
down_revision: Union[str, Sequence[str], None] = "a3f1c2d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMAIL_IS_LOWERCASE = "email = lower(email)"


def upgrade() -> None:
    op.drop_column("customer", "hashed_password")

    for table in ("customer", "manager"):
        op.execute(f"UPDATE {table} SET email = lower(email) WHERE email <> lower(email)")
        op.create_check_constraint(f"ck_{table}_email_lowercase", table, EMAIL_IS_LOWERCASE)

    op.add_column(
        "manager", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )

    op.add_column(
        "order",
        sa.Column("deleted_by_manager_id", sa.Integer(), nullable=True, comment="刪除訂單的管理者"),
    )
    op.create_foreign_key(
        "order_deleted_by_manager_id_fkey", "order", "manager", ["deleted_by_manager_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("order_deleted_by_manager_id_fkey", "order", type_="foreignkey")
    op.drop_column("order", "deleted_by_manager_id")
    op.drop_column("manager", "is_active")
    for table in ("customer", "manager"):
        op.drop_constraint(f"ck_{table}_email_lowercase", table, type_="check")
    op.add_column(
        "customer",
        sa.Column("hashed_password", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
