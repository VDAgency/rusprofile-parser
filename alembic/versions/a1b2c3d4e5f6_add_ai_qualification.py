"""add AI qualification: ai_profiles + tariff/quota fields + ai/yandex/cross-enrichment fields

Этап 2 (v3) — фундамент конвейера ИИ-квалификации.

См. docs/tz_ai_qualification_v3.md (раздел 14).

Revision ID: a1b2c3d4e5f6
Revises: bd00e9275c7c
Create Date: 2026-05-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "bd00e9275c7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── Таблица ai_profiles ──────────────────────────────────────────────
    op.create_table(
        "ai_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("brief_hash", sa.String(length=40), nullable=False),
        sa.Column("icp_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("semantic_criteria", sa.JSON(), nullable=False),
        sa.Column("keywords_positive", sa.JSON(), nullable=False),
        sa.Column("keywords_negative", sa.JSON(), nullable=False),
        sa.Column("extraction_model", sa.String(length=50), nullable=True),
        sa.Column(
            "extraction_tokens_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    with op.batch_alter_table("ai_profiles", schema=None) as batch_op:
        batch_op.create_index("ix_ai_profiles_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_ai_profiles_brief_hash", ["brief_hash"], unique=False)
        batch_op.create_index(
            "ix_ai_profiles_tenant_active", ["tenant_id", "is_active"], unique=False
        )

    # ─── Поля тарифа в tenants ────────────────────────────────────────────
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "tariff_plan",
                sa.String(length=20),
                nullable=False,
                server_default="simple",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_quota_companies_monthly",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_quota_tokens_monthly",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_companies_processed_period",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_tokens_used_period",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("quota_period_start", sa.DateTime(), nullable=True)
        )

    # ─── Поля в companies ─────────────────────────────────────────────────
    with op.batch_alter_table("companies", schema=None) as batch_op:
        # Я.Карты-сигналы
        batch_op.add_column(sa.Column("yandex_rating", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("yandex_reviews_count", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("yandex_last_review_date", sa.Date(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("yandex_hours_filled", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("yandex_coordinates_filled", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("yandex_url", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("yandex_operating_status", sa.String(length=30), nullable=True)
        )

        # Кросс-обогащение
        batch_op.add_column(
            sa.Column("cross_enriched_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cross_enrichment_source", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cross_match_confidence", sa.Float(), nullable=True)
        )

        # ИИ-поля
        batch_op.add_column(sa.Column("ai_score", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ai_status", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("ai_comment", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ai_signals", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("ai_hook", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("ai_keyword_matches_positive", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ai_keyword_matches_negative", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "ai_tokens_used",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("ai_qualified_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "ai_profile_id",
                sa.Integer(),
                sa.ForeignKey(
                    "ai_profiles.id",
                    ondelete="SET NULL",
                    name="fk_companies_ai_profile_id",
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_company_ai_status", ["tenant_id", "ai_status"], unique=False
        )
        batch_op.create_index("ix_company_yandex_url", ["yandex_url"], unique=False)

    # ─── Поля в parse_runs ────────────────────────────────────────────────
    with op.batch_alter_table("parse_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_profile_id",
                sa.Integer(),
                sa.ForeignKey(
                    "ai_profiles.id",
                    ondelete="SET NULL",
                    name="fk_parse_runs_ai_profile_id",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_qualify_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("ai_qualify_stats", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "enable_cross_enrichment",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("parse_runs", schema=None) as batch_op:
        batch_op.drop_column("enable_cross_enrichment")
        batch_op.drop_column("ai_qualify_stats")
        batch_op.drop_column("ai_qualify_enabled")
        batch_op.drop_column("ai_profile_id")

    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_index("ix_company_yandex_url")
        batch_op.drop_index("ix_company_ai_status")
        batch_op.drop_column("ai_profile_id")
        batch_op.drop_column("ai_qualified_at")
        batch_op.drop_column("ai_tokens_used")
        batch_op.drop_column("ai_keyword_matches_negative")
        batch_op.drop_column("ai_keyword_matches_positive")
        batch_op.drop_column("ai_hook")
        batch_op.drop_column("ai_signals")
        batch_op.drop_column("ai_comment")
        batch_op.drop_column("ai_status")
        batch_op.drop_column("ai_score")
        batch_op.drop_column("cross_match_confidence")
        batch_op.drop_column("cross_enrichment_source")
        batch_op.drop_column("cross_enriched_at")
        batch_op.drop_column("yandex_operating_status")
        batch_op.drop_column("yandex_url")
        batch_op.drop_column("yandex_coordinates_filled")
        batch_op.drop_column("yandex_hours_filled")
        batch_op.drop_column("yandex_last_review_date")
        batch_op.drop_column("yandex_reviews_count")
        batch_op.drop_column("yandex_rating")

    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_column("quota_period_start")
        batch_op.drop_column("ai_tokens_used_period")
        batch_op.drop_column("ai_companies_processed_period")
        batch_op.drop_column("ai_quota_tokens_monthly")
        batch_op.drop_column("ai_quota_companies_monthly")
        batch_op.drop_column("tariff_plan")

    with op.batch_alter_table("ai_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_profiles_tenant_active")
        batch_op.drop_index("ix_ai_profiles_brief_hash")
        batch_op.drop_index("ix_ai_profiles_tenant_id")
    op.drop_table("ai_profiles")
