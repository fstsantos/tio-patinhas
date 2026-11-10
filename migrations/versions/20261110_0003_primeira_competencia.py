"""mês da 1ª parcela opcional nas compras no crédito

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Cópia fixa da regra (migrações não importam o modelo, que pode mudar depois).
CHECK = (
    "primeira_competencia IS NULL OR ("
    "tipo = 'CREDITO' AND EXTRACT(DAY FROM primeira_competencia) = 1"
    " AND primeira_competencia >= date_trunc('month', data_operacao)::date"
    " AND primeira_competencia <= (date_trunc('month', data_operacao)"
    " + interval '2 months')::date)"
)


def upgrade() -> None:
    op.add_column("lancamentos", sa.Column("primeira_competencia", sa.Date(), nullable=True))
    op.create_check_constraint(op.f("ck_lancamentos_primeira_competencia"), "lancamentos", CHECK)


def downgrade() -> None:
    # Perde a informação: compras com mês da 1ª parcela informado continuam com as
    # parcelas já gravadas, mas uma edição futura voltaria à regra padrão.
    op.drop_constraint(op.f("ck_lancamentos_primeira_competencia"), "lancamentos", type_="check")
    op.drop_column("lancamentos", "primeira_competencia")
