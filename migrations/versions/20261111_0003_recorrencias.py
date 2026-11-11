"""recorrências mensais (receitas e despesas)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CK_ORIGEM = op.f("ck_lancamentos_origem")
CK_RECORRENCIA = op.f("ck_lancamentos_recorrencia_mes")
FK_RECORRENCIA = op.f("fk_lancamentos_recorrencia_id_recorrencias")
UQ_RECORRENCIA = "uq_lancamentos_recorrencia_mes"
ORIGENS = "'TELEGRAM_TEXTO', 'TELEGRAM_VOZ', 'WEB', 'CLI'"


def upgrade() -> None:
    op.create_table(
        "recorrencias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("valor", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("descricao", sa.String(length=120), nullable=False),
        sa.Column("categoria_id", sa.Integer(), nullable=True),
        sa.Column("membro_id", sa.Integer(), nullable=False),
        sa.Column("criado_por", sa.Integer(), nullable=False),
        sa.Column("dia", sa.SmallInteger(), nullable=False),
        sa.Column("inicio", sa.Date(), nullable=False),
        sa.Column("fim", sa.Date(), nullable=True),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("gerada_ate", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("tipo IN ('RECEITA', 'DEBITO')", name=op.f("ck_recorrencias_tipo")),
        sa.CheckConstraint("valor > 0 AND valor <= 1000000", name=op.f("ck_recorrencias_valor")),
        sa.CheckConstraint(
            "length(descricao) BETWEEN 1 AND 120", name=op.f("ck_recorrencias_descricao_tamanho")
        ),
        sa.CheckConstraint("dia BETWEEN 1 AND 31", name=op.f("ck_recorrencias_dia")),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM inicio) = 1 AND (fim IS NULL OR"
            " (EXTRACT(DAY FROM fim) = 1 AND fim >= inicio))",
            name=op.f("ck_recorrencias_periodo"),
        ),
        sa.CheckConstraint(
            "gerada_ate IS NULL OR EXTRACT(DAY FROM gerada_ate) = 1",
            name=op.f("ck_recorrencias_gerada_ate"),
        ),
        sa.ForeignKeyConstraint(
            ["categoria_id"],
            ["categorias.id"],
            name=op.f("fk_recorrencias_categoria_id_categorias"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membro_id"],
            ["membros.id"],
            name=op.f("fk_recorrencias_membro_id_membros"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            ["membros.id"],
            name=op.f("fk_recorrencias_criado_por_membros"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recorrencias")),
    )
    op.create_index("ix_recorrencias_membro_id", "recorrencias", ["membro_id"], unique=False)

    op.add_column("lancamentos", sa.Column("recorrencia_id", sa.Integer(), nullable=True))
    op.add_column("lancamentos", sa.Column("recorrencia_mes", sa.Date(), nullable=True))
    op.create_foreign_key(
        FK_RECORRENCIA,
        "lancamentos",
        "recorrencias",
        ["recorrencia_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        UQ_RECORRENCIA, "lancamentos", ["recorrencia_id", "recorrencia_mes"]
    )
    op.create_check_constraint(
        CK_RECORRENCIA, "lancamentos", "(recorrencia_id IS NULL) = (recorrencia_mes IS NULL)"
    )
    op.drop_constraint(CK_ORIGEM, "lancamentos", type_="check")
    op.create_check_constraint(CK_ORIGEM, "lancamentos", f"origem IN ({ORIGENS}, 'RECORRENCIA')")


def downgrade() -> None:
    # Os lançamentos gerados ficam, como lançamentos comuns: perdem o vínculo com a
    # recorrência e passam à origem 'CLI' (a origem 'RECORRENCIA' deixa de existir).
    op.execute("UPDATE lancamentos SET origem = 'CLI' WHERE origem = 'RECORRENCIA'")
    op.drop_constraint(CK_ORIGEM, "lancamentos", type_="check")
    op.create_check_constraint(CK_ORIGEM, "lancamentos", f"origem IN ({ORIGENS})")
    op.drop_constraint(CK_RECORRENCIA, "lancamentos", type_="check")
    op.drop_constraint(UQ_RECORRENCIA, "lancamentos", type_="unique")
    op.drop_constraint(FK_RECORRENCIA, "lancamentos", type_="foreignkey")
    op.drop_column("lancamentos", "recorrencia_mes")
    op.drop_column("lancamentos", "recorrencia_id")
    op.drop_index("ix_recorrencias_membro_id", table_name="recorrencias")
    op.drop_table("recorrencias")
