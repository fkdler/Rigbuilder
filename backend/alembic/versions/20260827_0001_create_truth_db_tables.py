"""create Phase 1 Truth DB tables

Revision ID: 20260827_0001
Revises:
Create Date: 2026-08-27 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260827_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hardware",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("manufacturer", sa.String(length=100), nullable=False),
        sa.Column("architecture", sa.String(length=100), nullable=True),
        sa.Column("vram_gb", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("memory_type", sa.String(length=100), nullable=True),
        sa.Column("tdp_w", sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.CheckConstraint("vram_gb IS NULL OR vram_gb >= 0", name="vram_non_negative"),
        sa.CheckConstraint("tdp_w IS NULL OR tdp_w >= 0", name="tdp_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_hardware"),
        sa.UniqueConstraint("manufacturer", "name", "type", name="manufacturer_name_type"),
    )
    op.create_index("ix_hardware_type_manufacturer", "hardware", ["type", "manufacturer"], unique=False)

    op.create_table(
        "ai_model",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("parameter_b", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("supports_vision", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("supports_code", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("license", sa.String(length=255), nullable=True),
        sa.CheckConstraint("parameter_b IS NULL OR parameter_b >= 0", name="parameter_non_negative"),
        sa.CheckConstraint("context_length IS NULL OR context_length > 0", name="context_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_model"),
        sa.UniqueConstraint("publisher", "name", name="publisher_name"),
    )
    op.create_index("ix_ai_model_parameter_b", "ai_model", ["parameter_b"], unique=False)
    op.create_index("ix_ai_model_capabilities", "ai_model", ["supports_vision", "supports_code"], unique=False)

    op.create_table(
        "model_variant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("quantization", sa.String(length=100), nullable=False),
        sa.Column("format", sa.String(length=50), nullable=False),
        sa.Column("file_size_gb", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("recommended_vram_gb", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("recommended_ram_gb", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("source", sa.String(length=2048), nullable=False),
        sa.CheckConstraint("file_size_gb IS NULL OR file_size_gb >= 0", name="file_size_non_negative"),
        sa.CheckConstraint("recommended_vram_gb IS NULL OR recommended_vram_gb >= 0", name="vram_non_negative"),
        sa.CheckConstraint("recommended_ram_gb IS NULL OR recommended_ram_gb >= 0", name="ram_non_negative"),
        sa.ForeignKeyConstraint(["model_id"], ["ai_model.id"], name="fk_model_variant_model_id_ai_model", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_model_variant"),
        sa.UniqueConstraint("model_id", "quantization", "format", "source", name="model_quantization_format_source"),
    )
    op.create_index("ix_model_variant_model_id", "model_variant", ["model_id"], unique=False)
    op.create_index("ix_model_variant_recommended_vram", "model_variant", ["recommended_vram_gb"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=False),
        sa.Column("normalized_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("provenance", sa.String(length=20), nullable=False),
        sa.CheckConstraint("provenance IN ('official', 'measured', 'derived')", name="provenance_valid"),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
    )
    op.create_index("ix_evidence_entity_field", "evidence", ["entity_type", "entity_id", "field"], unique=False)
    op.create_index("ix_evidence_provenance", "evidence", ["provenance"], unique=False)

    op.create_table(
        "benchmark",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_variant_id", sa.Uuid(), nullable=False),
        sa.Column("hardware_id", sa.Uuid(), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=False),
        sa.Column("peak_vram_gb", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("ram_gb", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("tokens_per_second", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("first_token_latency", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("test_environment", sa.Text(), nullable=True),
        sa.CheckConstraint("context_length > 0", name="context_positive"),
        sa.CheckConstraint("peak_vram_gb IS NULL OR peak_vram_gb >= 0", name="peak_vram_non_negative"),
        sa.CheckConstraint("ram_gb IS NULL OR ram_gb >= 0", name="ram_non_negative"),
        sa.CheckConstraint("tokens_per_second IS NULL OR tokens_per_second >= 0", name="tokens_per_second_non_negative"),
        sa.CheckConstraint("first_token_latency IS NULL OR first_token_latency >= 0", name="latency_non_negative"),
        sa.ForeignKeyConstraint(["hardware_id"], ["hardware.id"], name="fk_benchmark_hardware_id_hardware", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_variant_id"], ["model_variant.id"], name="fk_benchmark_model_variant_id_model_variant", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark"),
    )
    op.create_index("ix_benchmark_variant_hardware", "benchmark", ["model_variant_id", "hardware_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_benchmark_variant_hardware", table_name="benchmark")
    op.drop_table("benchmark")
    op.drop_index("ix_evidence_provenance", table_name="evidence")
    op.drop_index("ix_evidence_entity_field", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_model_variant_recommended_vram", table_name="model_variant")
    op.drop_index("ix_model_variant_model_id", table_name="model_variant")
    op.drop_table("model_variant")
    op.drop_index("ix_ai_model_capabilities", table_name="ai_model")
    op.drop_index("ix_ai_model_parameter_b", table_name="ai_model")
    op.drop_table("ai_model")
    op.drop_index("ix_hardware_type_manufacturer", table_name="hardware")
    op.drop_table("hardware")
