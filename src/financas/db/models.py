"""Modelos ORM. O esquema é criado exclusivamente por migrações Alembic.

Convenções: dinheiro em NUMERIC(14,2); instantes em timestamptz (UTC);
datas financeiras em DATE (fuso de São Paulo). Enumerações são VARCHAR com CHECK,
o que mantém migrações simples.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NOMES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Dinheiro = Numeric(14, 2, asdecimal=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NOMES)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        Decimal: Dinheiro,
        dict[str, Any]: JSONB,
    }


def _check_enum(coluna: str, enum: type[StrEnum]) -> str:
    valores = ", ".join(f"'{v.value}'" for v in enum)
    return f"{coluna} IN ({valores})"


class TimestampsMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------- enumerações


class Papel(StrEnum):
    ADMIN = "admin"
    MEMBRO = "membro"


class TipoCategoria(StrEnum):
    RECEITA = "RECEITA"
    DESPESA = "DESPESA"
    AMBAS = "AMBAS"


class TipoLancamento(StrEnum):
    RECEITA = "RECEITA"
    DEBITO = "DEBITO"
    CREDITO = "CREDITO"


class StatusLancamento(StrEnum):
    ATIVO = "ATIVO"
    CANCELADO = "CANCELADO"


class Origem(StrEnum):
    TELEGRAM_TEXTO = "TELEGRAM_TEXTO"
    TELEGRAM_VOZ = "TELEGRAM_VOZ"
    WEB = "WEB"
    CLI = "CLI"
    RECORRENCIA = "RECORRENCIA"


class StatusParcela(StrEnum):
    PENDENTE = "PENDENTE"
    CANCELADA = "CANCELADA"
    SUBSTITUIDA = "SUBSTITUIDA"


class AcaoAuditoria(StrEnum):
    CRIACAO = "CRIACAO"
    EDICAO = "EDICAO"
    CANCELAMENTO = "CANCELAMENTO"
    RESTAURACAO = "RESTAURACAO"


class TipoConfirmacao(StrEnum):
    LANCAMENTO = "LANCAMENTO"
    CANCELAMENTO = "CANCELAMENTO"


class StatusConfirmacao(StrEnum):
    PENDENTE = "PENDENTE"
    EXECUTADA = "EXECUTADA"
    CANCELADA = "CANCELADA"
    EXPIRADA = "EXPIRADA"


# --------------------------------------------------------------------- tabelas


class Membro(TimestampsMixin, Base):
    __tablename__ = "membros"
    __table_args__ = (
        CheckConstraint(_check_enum("papel", Papel), name="papel"),
        CheckConstraint("length(trim(nome)) > 0", name="nome_nao_vazio"),
        Index("uq_membros_login_web_lower", func.lower(text("login_web")), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    papel: Mapped[str] = mapped_column(String(10), default=Papel.MEMBRO.value)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    login_web: Mapped[str | None] = mapped_column(String(60))
    senha_hash: Mapped[str | None] = mapped_column(Text)

    @property
    def eh_admin(self) -> bool:
        return self.papel == Papel.ADMIN.value


class Categoria(TimestampsMixin, Base):
    __tablename__ = "categorias"
    __table_args__ = (
        CheckConstraint(_check_enum("tipo", TipoCategoria), name="tipo"),
        Index("uq_categorias_nome_lower", func.lower(text("nome")), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(60))
    tipo: Mapped[str] = mapped_column(String(10))
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


# Mês da 1ª parcela (opcional, só crédito): dia 1, do mês da compra até dois meses depois.
PRIMEIRA_COMPETENCIA_CHECK = (
    "primeira_competencia IS NULL OR ("
    "tipo = 'CREDITO' AND EXTRACT(DAY FROM primeira_competencia) = 1"
    " AND primeira_competencia >= date_trunc('month', data_operacao)::date"
    " AND primeira_competencia <= (date_trunc('month', data_operacao)"
    " + interval '2 months')::date)"
)


class Lancamento(TimestampsMixin, Base):
    __tablename__ = "lancamentos"
    __table_args__ = (
        CheckConstraint(_check_enum("tipo", TipoLancamento), name="tipo"),
        CheckConstraint(_check_enum("status", StatusLancamento), name="status"),
        CheckConstraint(_check_enum("origem", Origem), name="origem"),
        CheckConstraint("valor_total > 0 AND valor_total <= 1000000", name="valor_total"),
        CheckConstraint(
            "(tipo = 'CREDITO' AND qtd_parcelas BETWEEN 1 AND 48)"
            " OR (tipo <> 'CREDITO' AND qtd_parcelas IS NULL)",
            name="qtd_parcelas",
        ),
        CheckConstraint(PRIMEIRA_COMPETENCIA_CHECK, name="primeira_competencia"),
        CheckConstraint("length(descricao) BETWEEN 1 AND 120", name="descricao_tamanho"),
        CheckConstraint(
            "(status = 'CANCELADO') = (cancelado_em IS NOT NULL)", name="dados_cancelamento"
        ),
        CheckConstraint(
            "(recorrencia_id IS NULL) = (recorrencia_mes IS NULL)", name="recorrencia_mes"
        ),
        # Um lançamento por recorrência e mês, mesmo cancelado: o mês não é gerado de novo.
        UniqueConstraint(
            "recorrencia_id", "recorrencia_mes", name="uq_lancamentos_recorrencia_mes"
        ),
        Index("ix_lancamentos_data_operacao", "data_operacao"),
        Index("ix_lancamentos_membro_id", "membro_id"),
        Index("ix_lancamentos_criado_por_created_at", "criado_por", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10))
    valor_total: Mapped[Decimal]
    descricao: Mapped[str] = mapped_column(String(120))
    descricao_normalizada: Mapped[str] = mapped_column(String(120))
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias.id", ondelete="RESTRICT")
    )
    membro_id: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    criado_por: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    data_operacao: Mapped[date] = mapped_column(Date)
    qtd_parcelas: Mapped[int | None] = mapped_column(SmallInteger)
    # Mês da 1ª parcela; NULL = regra padrão (mês seguinte ao da compra).
    primeira_competencia: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default=StatusLancamento.ATIVO.value)
    cancelado_em: Mapped[datetime | None]
    cancelado_por: Mapped[int | None] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    motivo_cancelamento: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(String(20))
    # Preenchidos só nos lançamentos gerados por uma recorrência (mês = dia 1).
    recorrencia_id: Mapped[int | None] = mapped_column(
        ForeignKey("recorrencias.id", ondelete="RESTRICT")
    )
    recorrencia_mes: Mapped[date | None] = mapped_column(Date)
    # Controle otimista de concorrência; também identifica a revisão das parcelas.
    revisao: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    membro: Mapped[Membro] = relationship(foreign_keys=[membro_id], lazy="raise")
    autor: Mapped[Membro] = relationship(foreign_keys=[criado_por], lazy="raise")
    categoria: Mapped[Categoria | None] = relationship(lazy="raise")
    parcelas: Mapped[list[Parcela]] = relationship(
        back_populates="lancamento",
        order_by="(Parcela.revisao_lancamento, Parcela.numero)",
        lazy="raise",
    )

    __mapper_args__ = {"version_id_col": revisao}  # noqa: RUF012


class Recorrencia(TimestampsMixin, Base):
    """Molde de uma receita ou despesa mensal; gera lançamentos comuns, um por mês."""

    __tablename__ = "recorrencias"
    __table_args__ = (
        CheckConstraint("tipo IN ('RECEITA', 'DEBITO')", name="tipo"),
        CheckConstraint("valor > 0 AND valor <= 1000000", name="valor"),
        CheckConstraint("length(descricao) BETWEEN 1 AND 120", name="descricao_tamanho"),
        CheckConstraint("dia BETWEEN 1 AND 31", name="dia"),
        CheckConstraint(
            "EXTRACT(DAY FROM inicio) = 1 AND (fim IS NULL OR"
            " (EXTRACT(DAY FROM fim) = 1 AND fim >= inicio))",
            name="periodo",
        ),
        CheckConstraint(
            "gerada_ate IS NULL OR EXTRACT(DAY FROM gerada_ate) = 1", name="gerada_ate"
        ),
        Index("ix_recorrencias_membro_id", "membro_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10))
    valor: Mapped[Decimal]
    descricao: Mapped[str] = mapped_column(String(120))
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias.id", ondelete="RESTRICT")
    )
    membro_id: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    criado_por: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    # Dia do mês (1 a 31); em mês mais curto, vale o último dia.
    dia: Mapped[int] = mapped_column(SmallInteger)
    # Primeiro e último mês (dia 1); fim NULL = sem prazo.
    inicio: Mapped[date] = mapped_column(Date)
    fim: Mapped[date | None] = mapped_column(Date)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Último mês já tratado pela geração (dia 1); NULL = nenhum ainda.
    gerada_ate: Mapped[date | None] = mapped_column(Date)


class Parcela(TimestampsMixin, Base):
    __tablename__ = "parcelas"
    __table_args__ = (
        UniqueConstraint(
            "lancamento_id",
            "revisao_lancamento",
            "numero",
            name="uq_parcelas_lancamento_revisao_numero",
        ),
        CheckConstraint(_check_enum("status", StatusParcela), name="status"),
        CheckConstraint("EXTRACT(DAY FROM competencia) = 1", name="competencia_dia_1"),
        CheckConstraint("numero BETWEEN 1 AND 48", name="numero"),
        CheckConstraint("valor > 0", name="valor_positivo"),
        Index(
            "ix_parcelas_competencia_pendente",
            "competencia",
            postgresql_where=text("status = 'PENDENTE'"),
        ),
        Index("ix_parcelas_lancamento_id", "lancamento_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos.id", ondelete="RESTRICT"))
    revisao_lancamento: Mapped[int] = mapped_column(Integer)
    numero: Mapped[int] = mapped_column(SmallInteger)
    valor: Mapped[Decimal]
    competencia: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=StatusParcela.PENDENTE.value)

    lancamento: Mapped[Lancamento] = relationship(back_populates="parcelas", lazy="raise")


class Auditoria(Base):
    """Somente inserção: um trigger bloqueia UPDATE e DELETE."""

    __tablename__ = "auditoria"
    __table_args__ = (
        CheckConstraint(_check_enum("acao", AcaoAuditoria), name="acao"),
        Index("ix_auditoria_entidade", "entidade", "entidade_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entidade: Mapped[str] = mapped_column(String(30))
    entidade_id: Mapped[int] = mapped_column(BigInteger)
    acao: Mapped[str] = mapped_column(String(15))
    autor_id: Mapped[int | None] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    origem: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[str | None] = mapped_column(Text)
    dados_antes: Mapped[dict[str, Any] | None]
    dados_depois: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UpdateProcessado(Base):
    """Idempotência do Telegram: (chat_id, message_id) gravado na transação do lançamento."""

    __tablename__ = "updates_processados"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_updates_processados_chat_message"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    update_id: Mapped[int | None] = mapped_column(BigInteger)
    lancamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("lancamentos.id", ondelete="RESTRICT")
    )
    resultado: Mapped[str] = mapped_column(Text)
    recebido_em: Mapped[datetime] = mapped_column(server_default=func.now())


class ConfirmacaoPendente(Base):
    __tablename__ = "confirmacoes_pendentes"
    __table_args__ = (
        CheckConstraint(_check_enum("tipo", TipoConfirmacao), name="tipo"),
        CheckConstraint(_check_enum("status", StatusConfirmacao), name="status"),
        Index("ix_confirmacoes_pendentes_status_expira_em", "status", "expira_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo: Mapped[str] = mapped_column(String(15))
    membro_id: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(10), default=StatusConfirmacao.PENDENTE.value)
    expira_em: Mapped[datetime]
    lancamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("lancamentos.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolvida_em: Mapped[datetime | None]


class SessaoWeb(Base):
    __tablename__ = "sessoes_web"
    __table_args__ = (Index("ix_sessoes_web_membro_id", "membro_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    membro_id: Mapped[int] = mapped_column(ForeignKey("membros.id", ondelete="RESTRICT"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expira_em: Mapped[datetime]
    revogada_em: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TentativaLogin(Base):
    """Base do limite de tentativas de login por usuário e por IP."""

    __tablename__ = "tentativas_login"
    __table_args__ = (
        Index("ix_tentativas_login_login_created_at", "login", "created_at"),
        Index("ix_tentativas_login_ip_created_at", "ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(String(60))
    ip: Mapped[str] = mapped_column(String(45))
    sucesso: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
