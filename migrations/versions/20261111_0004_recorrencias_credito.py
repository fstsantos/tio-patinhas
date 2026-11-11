"""recorrências de compras no crédito

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CK_TIPO = op.f("ck_recorrencias_tipo")


def upgrade() -> None:
    op.drop_constraint(CK_TIPO, "recorrencias", type_="check")
    op.create_check_constraint(CK_TIPO, "recorrencias", "tipo IN ('RECEITA', 'DEBITO', 'CREDITO')")


def downgrade() -> None:
    # As compras já geradas ficam; as recorrências de crédito param de gerar e passam a
    # constar como débito (o tipo 'CREDITO' deixa de existir no molde).
    op.execute("UPDATE recorrencias SET ativa = false, tipo = 'DEBITO' WHERE tipo = 'CREDITO'")
    op.drop_constraint(CK_TIPO, "recorrencias", type_="check")
    op.create_check_constraint(CK_TIPO, "recorrencias", "tipo IN ('RECEITA', 'DEBITO')")
