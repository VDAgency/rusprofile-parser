"""add billing: trial fields in tenants + subscriptions + payments

Этап 3 (v1) — личный кабинет, тарифы (Trial / Basic / Pro), биллинг
через ЮKassa. См. docs/tz_billing_tariffs.md (раздел 5).

Особенности миграции:
- Существующие tenant'ы НЕ переводятся на Trial автоматически: они
  остаются с tariff_plan='simple' или 'ai' (legacy «бессрочно
  бесплатно» — спасибо за раннюю поддержку). Новых tenant'ов с
  tariff='trial' создаёт уже логика ensure_tenant.
- trial_parses_left default=0, чтобы не выдать бесплатные парсинги
  существующим клиентам. Для новых ensure_tenant выставит 10.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── Таблица subscriptions ────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(
                "tenants.id", ondelete="CASCADE",
                name="fk_subscriptions_tenant_id",
            ),
            nullable=False,
        ),
        sa.Column("tariff_plan", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column(
            "auto_renew",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "yookassa_payment_method_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("price_rub", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="RUB",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_subscriptions_tenant_id", ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_subscriptions_tenant_active",
            ["tenant_id", "status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_subscriptions_expires",
            ["expires_at", "status"],
            unique=False,
        )

    # ─── Таблица payments ─────────────────────────────────────────────────
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(
                "tenants.id", ondelete="CASCADE",
                name="fk_payments_tenant_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey(
                "subscriptions.id", ondelete="SET NULL",
                name="fk_payments_subscription_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "yookassa_payment_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "yookassa_idempotence_key",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "yookassa_status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="RUB",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payment_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column(
            "recurrent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.create_index("ix_payments_tenant", ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_payments_yookassa_id",
            ["yookassa_payment_id"],
            unique=True,
        )

    # ─── Поля trial / blocked / счётчики в tenants ───────────────────────
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("trial_started_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("trial_expires_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "trial_parses_left",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "parses_used_period",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "active_subscription_id",
                sa.Integer(),
                sa.ForeignKey(
                    "subscriptions.id",
                    ondelete="SET NULL",
                    name="fk_tenants_active_subscription_id",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_blocked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("blocked_reason", sa.String(length=50), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_column("blocked_reason")
        batch_op.drop_column("is_blocked")
        batch_op.drop_column("active_subscription_id")
        batch_op.drop_column("parses_used_period")
        batch_op.drop_column("trial_parses_left")
        batch_op.drop_column("trial_expires_at")
        batch_op.drop_column("trial_started_at")

    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.drop_index("ix_payments_yookassa_id")
        batch_op.drop_index("ix_payments_tenant")
    op.drop_table("payments")

    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_index("ix_subscriptions_expires")
        batch_op.drop_index("ix_subscriptions_tenant_active")
        batch_op.drop_index("ix_subscriptions_tenant_id")
    op.drop_table("subscriptions")
