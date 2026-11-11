"""會員拆成 customer 與 manager 兩張表

既有的 user 都是購票的人，所以 user 直接改名為 customer，訂單的 user_id 跟著
改成 customer_id。後台帳號另開 manager 表（role 分 ADMIN、MANAGER）：兩邊各自
登入、各自的 email 唯一，同一個 email 可以同時是 customer 和 manager。

只用 Google 登入的 customer 沒有密碼，所以 hashed_password 改為可為 NULL。

downgrade 會 drop 整張 manager 表，所有後台帳號跟著消失。

Revision ID: a3f1c2d4e5b6
Revises: 7c4e91ab2d38
Create Date: 2026-11-09 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f1c2d4e5b6"
down_revision: Union[str, Sequence[str], None] = "7c4e91ab2d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

manager_role_enum = sa.Enum("ADMIN", "MANAGER", name="managerrole")


def upgrade() -> None:
    op.rename_table("user", "customer")
    op.execute("ALTER SEQUENCE user_id_seq RENAME TO customer_id_seq")
    op.execute("ALTER INDEX user_pkey RENAME TO customer_pkey")
    op.execute("ALTER TABLE customer RENAME CONSTRAINT user_email_key TO customer_email_key")
    op.alter_column("customer", "hashed_password", nullable=True)
    op.add_column(
        "customer", sa.Column("google_sub", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.create_unique_constraint("customer_google_sub_key", "customer", ["google_sub"])

    op.alter_column("order", "user_id", new_column_name="customer_id")
    op.execute(
        'ALTER TABLE "order" RENAME CONSTRAINT order_user_id_fkey TO order_customer_id_fkey'
    )

    op.create_table(
        "manager",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("hashed_password", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("role", manager_role_enum, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("manager")
    manager_role_enum.drop(op.get_bind())

    op.execute(
        'ALTER TABLE "order" RENAME CONSTRAINT order_customer_id_fkey TO order_user_id_fkey'
    )
    op.alter_column("order", "customer_id", new_column_name="user_id")

    op.drop_constraint("customer_google_sub_key", "customer", type_="unique")
    op.drop_column("customer", "google_sub")
    # "!" matches no hash, so a Google-only customer survives but cannot log in
    op.execute("UPDATE customer SET hashed_password = '!' WHERE hashed_password IS NULL")
    op.alter_column("customer", "hashed_password", nullable=False)
    op.execute("ALTER TABLE customer RENAME CONSTRAINT customer_email_key TO user_email_key")
    op.execute("ALTER INDEX customer_pkey RENAME TO user_pkey")
    op.execute("ALTER SEQUENCE customer_id_seq RENAME TO user_id_seq")
    op.rename_table("customer", "user")
