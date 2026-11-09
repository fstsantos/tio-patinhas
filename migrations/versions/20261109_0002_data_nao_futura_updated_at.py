"""data não futura comparada ao updated_at (permite editar datas retroativas)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOME = "ck_lancamentos_data_nao_futura"


def upgrade() -> None:
    op.drop_constraint(op.f(NOME), "lancamentos", type_="check")
    op.create_check_constraint(
        op.f(NOME),
        "lancamentos",
        "data_operacao <= (updated_at AT TIME ZONE 'America/Sao_Paulo')::date",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(NOME), "lancamentos", type_="check")
    op.create_check_constraint(
        op.f(NOME),
        "lancamentos",
        "data_operacao <= (created_at AT TIME ZONE 'America/Sao_Paulo')::date",
    )
