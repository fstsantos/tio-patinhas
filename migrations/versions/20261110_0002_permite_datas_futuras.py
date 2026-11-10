"""permite lançamentos com data futura

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10 18:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.f(): nome final, sem aplicar de novo a convenção de nomes.
NOME = op.f("ck_lancamentos_data_nao_futura")


def upgrade() -> None:
    op.drop_constraint(NOME, "lancamentos", type_="check")


def downgrade() -> None:
    # Falha se já houver lançamento com data futura: corrija-os antes de reverter.
    op.create_check_constraint(
        NOME,
        "lancamentos",
        "data_operacao <= (updated_at AT TIME ZONE 'America/Sao_Paulo')::date",
    )
