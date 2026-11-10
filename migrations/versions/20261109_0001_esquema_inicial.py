"""esquema inicial

Revision ID: 0001
Revises:
Create Date: 2026-11-09 00:14:23.135554
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Segunda barreira de integridade (spec 10): constraint triggers DEFERRABLE,
# verificadas no COMMIT, depois que lançamento e parcelas foram gravados.
#  1. soma das parcelas vigentes (não substituídas) = valor_total da compra;
#     vigentes formam uma única revisão com numeração 1..qtd_parcelas;
#  2. lançamento cancelado não tem parcela pendente; ativo não tem vigente cancelada;
#  3. só compras no crédito têm parcelas (órfãs: impedidas pela FK).
CRIAR_TRIGGERS = [
    """
    CREATE FUNCTION financas_verificar_parcelas(p_lancamento_id bigint) RETURNS void
    LANGUAGE plpgsql AS $$
    DECLARE
        l lancamentos%ROWTYPE;
        v_soma numeric(14, 2);
        v_qtd integer;
        v_revisoes integer;
        v_max_numero integer;
        v_pendentes integer;
        v_canceladas integer;
    BEGIN
        SELECT * INTO l FROM lancamentos WHERE id = p_lancamento_id;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        SELECT coalesce(sum(valor), 0), count(*), count(DISTINCT revisao_lancamento),
               coalesce(max(numero), 0),
               count(*) FILTER (WHERE status = 'PENDENTE'),
               count(*) FILTER (WHERE status = 'CANCELADA')
          INTO v_soma, v_qtd, v_revisoes, v_max_numero, v_pendentes, v_canceladas
          FROM parcelas
         WHERE lancamento_id = p_lancamento_id AND status <> 'SUBSTITUIDA';

        IF l.tipo <> 'CREDITO' THEN
            IF EXISTS (SELECT 1 FROM parcelas WHERE lancamento_id = p_lancamento_id) THEN
                RAISE EXCEPTION 'lançamento % do tipo % não pode ter parcelas', l.id, l.tipo
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN;
        END IF;
        IF v_qtd <> l.qtd_parcelas OR v_revisoes <> 1 OR v_max_numero <> l.qtd_parcelas THEN
            RAISE EXCEPTION 'compra %: parcelas vigentes inconsistentes (% de %)',
                l.id, v_qtd, l.qtd_parcelas USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_soma <> l.valor_total THEN
            RAISE EXCEPTION 'compra %: soma das parcelas (%) difere do total (%)',
                l.id, v_soma, l.valor_total USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF l.status = 'CANCELADO' AND v_pendentes > 0 THEN
            RAISE EXCEPTION 'compra % cancelada com parcelas pendentes', l.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF l.status = 'ATIVO' AND v_canceladas > 0 THEN
            RAISE EXCEPTION 'compra % ativa com parcelas vigentes canceladas', l.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END;
    $$
    """,
    """
    CREATE FUNCTION financas_trg_lancamentos() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM financas_verificar_parcelas(NEW.id);
        RETURN NULL;
    END;
    $$
    """,
    """
    CREATE FUNCTION financas_trg_parcelas() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            PERFORM financas_verificar_parcelas(OLD.lancamento_id);
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            PERFORM financas_verificar_parcelas(NEW.lancamento_id);
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_lancamentos_integridade
        AFTER INSERT OR UPDATE ON lancamentos
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION financas_trg_lancamentos()
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_parcelas_integridade
        AFTER INSERT OR UPDATE OR DELETE ON parcelas
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION financas_trg_parcelas()
    """,
    # Auditoria somente de inserção, independentemente do usuário do banco.
    """
    CREATE FUNCTION financas_auditoria_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'auditoria é somente de inserção (% bloqueado)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    """
    CREATE TRIGGER trg_auditoria_imutavel
        BEFORE UPDATE OR DELETE ON auditoria
        FOR EACH ROW EXECUTE FUNCTION financas_auditoria_imutavel()
    """,
]

REMOVER_TRIGGERS = [
    "DROP TRIGGER IF EXISTS trg_auditoria_imutavel ON auditoria",
    "DROP FUNCTION IF EXISTS financas_auditoria_imutavel()",
    "DROP TRIGGER IF EXISTS trg_parcelas_integridade ON parcelas",
    "DROP TRIGGER IF EXISTS trg_lancamentos_integridade ON lancamentos",
    "DROP FUNCTION IF EXISTS financas_trg_parcelas()",
    "DROP FUNCTION IF EXISTS financas_trg_lancamentos()",
    "DROP FUNCTION IF EXISTS financas_verificar_parcelas(bigint)",
]


def upgrade() -> None:
    op.create_table(
        "categorias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "tipo IN ('RECEITA', 'DESPESA', 'AMBAS')", name=op.f("ck_categorias_tipo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categorias")),
    )
    op.create_index(
        "uq_categorias_nome_lower", "categorias", [sa.literal_column("lower(nome)")], unique=True
    )
    op.create_table(
        "membros",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=100), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("papel", sa.String(length=10), nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("login_web", sa.String(length=60), nullable=True),
        sa.Column("senha_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("papel IN ('admin', 'membro')", name=op.f("ck_membros_papel")),
        sa.CheckConstraint("length(trim(nome)) > 0", name=op.f("ck_membros_nome_nao_vazio")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membros")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_membros_telegram_user_id")),
    )
    op.create_index(
        "uq_membros_login_web_lower",
        "membros",
        [sa.literal_column("lower(login_web)")],
        unique=True,
    )
    op.create_table(
        "tentativas_login",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=60), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("sucesso", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tentativas_login")),
    )
    op.create_index(
        "ix_tentativas_login_ip_created_at", "tentativas_login", ["ip", "created_at"], unique=False
    )
    op.create_index(
        "ix_tentativas_login_login_created_at",
        "tentativas_login",
        ["login", "created_at"],
        unique=False,
    )
    op.create_table(
        "auditoria",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("entidade", sa.String(length=30), nullable=False),
        sa.Column("entidade_id", sa.BigInteger(), nullable=False),
        sa.Column("acao", sa.String(length=15), nullable=False),
        sa.Column("autor_id", sa.Integer(), nullable=True),
        sa.Column("origem", sa.String(length=20), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("dados_antes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dados_depois", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "acao IN ('CRIACAO', 'EDICAO', 'CANCELAMENTO', 'RESTAURACAO')",
            name=op.f("ck_auditoria_acao"),
        ),
        sa.ForeignKeyConstraint(
            ["autor_id"],
            ["membros.id"],
            name=op.f("fk_auditoria_autor_id_membros"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auditoria")),
    )
    op.create_index("ix_auditoria_entidade", "auditoria", ["entidade", "entidade_id"], unique=False)
    op.create_table(
        "lancamentos",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("valor_total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("descricao", sa.String(length=120), nullable=False),
        sa.Column("descricao_normalizada", sa.String(length=120), nullable=False),
        sa.Column("categoria_id", sa.Integer(), nullable=True),
        sa.Column("membro_id", sa.Integer(), nullable=False),
        sa.Column("criado_por", sa.Integer(), nullable=False),
        sa.Column("data_operacao", sa.Date(), nullable=False),
        sa.Column("qtd_parcelas", sa.SmallInteger(), nullable=True),
        sa.Column("primeira_competencia", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_por", sa.Integer(), nullable=True),
        sa.Column("motivo_cancelamento", sa.Text(), nullable=True),
        sa.Column("origem", sa.String(length=20), nullable=False),
        sa.Column("revisao", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'CANCELADO') = (cancelado_em IS NOT NULL)",
            name=op.f("ck_lancamentos_dados_cancelamento"),
        ),
        sa.CheckConstraint(
            "(tipo = 'CREDITO' AND qtd_parcelas BETWEEN 1 AND 48) OR (tipo <> 'CREDITO' AND qtd_parcelas IS NULL)",
            name=op.f("ck_lancamentos_qtd_parcelas"),
        ),
        sa.CheckConstraint(
            "origem IN ('TELEGRAM_TEXTO', 'TELEGRAM_VOZ', 'WEB', 'CLI')",
            name=op.f("ck_lancamentos_origem"),
        ),
        sa.CheckConstraint("status IN ('ATIVO', 'CANCELADO')", name=op.f("ck_lancamentos_status")),
        sa.CheckConstraint(
            # Compara com updated_at: na criação é igual a created_at; numa edição, a
            # data não pode passar do dia da edição (permite datas retroativas).
            "data_operacao <= (updated_at AT TIME ZONE 'America/Sao_Paulo')::date",
            name=op.f("ck_lancamentos_data_nao_futura"),
        ),
        sa.CheckConstraint(
            # Mês da 1ª parcela (opcional, só crédito): dia 1, de M até M + 2.
            "primeira_competencia IS NULL OR ("
            "tipo = 'CREDITO' AND EXTRACT(DAY FROM primeira_competencia) = 1"
            " AND primeira_competencia >= date_trunc('month', data_operacao)::date"
            " AND primeira_competencia <= (date_trunc('month', data_operacao)"
            " + interval '2 months')::date)",
            name=op.f("ck_lancamentos_primeira_competencia"),
        ),
        sa.CheckConstraint(
            "tipo IN ('RECEITA', 'DEBITO', 'CREDITO')", name=op.f("ck_lancamentos_tipo")
        ),
        sa.CheckConstraint(
            "length(descricao) BETWEEN 1 AND 120", name=op.f("ck_lancamentos_descricao_tamanho")
        ),
        sa.CheckConstraint(
            "valor_total > 0 AND valor_total <= 1000000", name=op.f("ck_lancamentos_valor_total")
        ),
        sa.ForeignKeyConstraint(
            ["cancelado_por"],
            ["membros.id"],
            name=op.f("fk_lancamentos_cancelado_por_membros"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["categoria_id"],
            ["categorias.id"],
            name=op.f("fk_lancamentos_categoria_id_categorias"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por"],
            ["membros.id"],
            name=op.f("fk_lancamentos_criado_por_membros"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membro_id"],
            ["membros.id"],
            name=op.f("fk_lancamentos_membro_id_membros"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lancamentos")),
    )
    op.create_index(
        "ix_lancamentos_criado_por_created_at",
        "lancamentos",
        ["criado_por", "created_at"],
        unique=False,
    )
    op.create_index("ix_lancamentos_data_operacao", "lancamentos", ["data_operacao"], unique=False)
    op.create_index("ix_lancamentos_membro_id", "lancamentos", ["membro_id"], unique=False)
    op.create_table(
        "sessoes_web",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("membro_id", sa.Integer(), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revogada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["membro_id"],
            ["membros.id"],
            name=op.f("fk_sessoes_web_membro_id_membros"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessoes_web")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessoes_web_token_hash")),
    )
    op.create_index("ix_sessoes_web_membro_id", "sessoes_web", ["membro_id"], unique=False)
    op.create_table(
        "confirmacoes_pendentes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tipo", sa.String(length=15), nullable=False),
        sa.Column("membro_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolvida_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'EXECUTADA', 'CANCELADA', 'EXPIRADA')",
            name=op.f("ck_confirmacoes_pendentes_status"),
        ),
        sa.CheckConstraint(
            "tipo IN ('LANCAMENTO', 'CANCELAMENTO')", name=op.f("ck_confirmacoes_pendentes_tipo")
        ),
        sa.ForeignKeyConstraint(
            ["lancamento_id"],
            ["lancamentos.id"],
            name=op.f("fk_confirmacoes_pendentes_lancamento_id_lancamentos"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membro_id"],
            ["membros.id"],
            name=op.f("fk_confirmacoes_pendentes_membro_id_membros"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmacoes_pendentes")),
    )
    op.create_index(
        "ix_confirmacoes_pendentes_status_expira_em",
        "confirmacoes_pendentes",
        ["status", "expira_em"],
        unique=False,
    )
    op.create_table(
        "parcelas",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=False),
        sa.Column("revisao_lancamento", sa.Integer(), nullable=False),
        sa.Column("numero", sa.SmallInteger(), nullable=False),
        sa.Column("valor", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'CANCELADA', 'SUBSTITUIDA')", name=op.f("ck_parcelas_status")
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM competencia) = 1", name=op.f("ck_parcelas_competencia_dia_1")
        ),
        sa.CheckConstraint("numero BETWEEN 1 AND 48", name=op.f("ck_parcelas_numero")),
        sa.CheckConstraint("valor > 0", name=op.f("ck_parcelas_valor_positivo")),
        sa.ForeignKeyConstraint(
            ["lancamento_id"],
            ["lancamentos.id"],
            name=op.f("fk_parcelas_lancamento_id_lancamentos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parcelas")),
        sa.UniqueConstraint(
            "lancamento_id",
            "revisao_lancamento",
            "numero",
            name="uq_parcelas_lancamento_revisao_numero",
        ),
    )
    op.create_index(
        "ix_parcelas_competencia_pendente",
        "parcelas",
        ["competencia"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )
    op.create_index("ix_parcelas_lancamento_id", "parcelas", ["lancamento_id"], unique=False)
    op.create_table(
        "updates_processados",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=True),
        sa.Column("lancamento_id", sa.BigInteger(), nullable=True),
        sa.Column("resultado", sa.Text(), nullable=False),
        sa.Column(
            "recebido_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lancamento_id"],
            ["lancamentos.id"],
            name=op.f("fk_updates_processados_lancamento_id_lancamentos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_updates_processados")),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_updates_processados_chat_message"),
    )
    for comando in CRIAR_TRIGGERS:
        op.execute(comando)


def downgrade() -> None:
    for comando in REMOVER_TRIGGERS:
        op.execute(comando)
    op.drop_table("updates_processados")
    op.drop_index("ix_parcelas_lancamento_id", table_name="parcelas")
    op.drop_index(
        "ix_parcelas_competencia_pendente",
        table_name="parcelas",
        postgresql_where=sa.text("status = 'PENDENTE'"),
    )
    op.drop_table("parcelas")
    op.drop_index("ix_confirmacoes_pendentes_status_expira_em", table_name="confirmacoes_pendentes")
    op.drop_table("confirmacoes_pendentes")
    op.drop_index("ix_sessoes_web_membro_id", table_name="sessoes_web")
    op.drop_table("sessoes_web")
    op.drop_index("ix_lancamentos_membro_id", table_name="lancamentos")
    op.drop_index("ix_lancamentos_data_operacao", table_name="lancamentos")
    op.drop_index("ix_lancamentos_criado_por_created_at", table_name="lancamentos")
    op.drop_table("lancamentos")
    op.drop_index("ix_auditoria_entidade", table_name="auditoria")
    op.drop_table("auditoria")
    op.drop_index("ix_tentativas_login_login_created_at", table_name="tentativas_login")
    op.drop_index("ix_tentativas_login_ip_created_at", table_name="tentativas_login")
    op.drop_table("tentativas_login")
    op.drop_index("uq_membros_login_web_lower", table_name="membros")
    op.drop_table("membros")
    op.drop_index("uq_categorias_nome_lower", table_name="categorias")
    op.drop_table("categorias")
