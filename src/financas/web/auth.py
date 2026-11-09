"""Autenticação web: sessões no banco, revogáveis, com CSRF por sessão (spec 6.4).

O cookie guarda um token aleatório; o banco guarda apenas o SHA-256 dele.
Limite de tentativas: 5 falhas por login ou 20 por IP em 15 minutos.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import Membro, SessaoWeb, TentativaLogin
from financas.services import senhas

COOKIE_SESSAO = "tp_sessao"
COOKIE_CSRF_LOGIN = "tp_csrf_login"
CAMPO_CSRF = "csrf_token"
JANELA_TENTATIVAS = timedelta(minutes=15)
MAX_FALHAS_LOGIN = 5
MAX_FALHAS_IP = 20

# Hash fictício para igualar o tempo de resposta quando o login não existe.
_HASH_FICTICIO = senhas.gerar_hash("senha-ficticia-para-tempo-constante")


class NaoAutenticado(Exception):
    """Sem sessão válida: redirecionar para o login."""


class CsrfInvalido(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Usuario:
    membro: Membro
    sessao_id: int
    csrf_token: str

    @property
    def eh_admin(self) -> bool:
        return self.membro.eh_admin


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_iguais(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


def ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


async def bloqueado(s: AsyncSession, clock: Clock, login: str, ip: str) -> bool:
    desde = clock.agora() - JANELA_TENTATIVAS
    falhas = select(func.count()).where(
        TentativaLogin.sucesso.is_(False), TentativaLogin.created_at >= desde
    )
    por_login = await s.scalar(falhas.where(TentativaLogin.login == login.lower()))
    por_ip = await s.scalar(falhas.where(TentativaLogin.ip == ip))
    return (por_login or 0) >= MAX_FALHAS_LOGIN or (por_ip or 0) >= MAX_FALHAS_IP


async def autenticar(
    s: AsyncSession, clock: Clock, login: str, senha: str, ip: str
) -> Membro | None:
    """Verifica credenciais e registra a tentativa. Não diferencia o motivo da falha."""
    login = login.strip()[:60]
    q = select(Membro).where(func.lower(Membro.login_web) == login.lower())
    membro = (await s.execute(q)).scalar_one_or_none()
    hash_ = membro.senha_hash if membro is not None and membro.senha_hash else _HASH_FICTICIO
    ok = senhas.verificar(hash_, senha) and membro is not None and membro.ativo
    s.add(TentativaLogin(login=login.lower(), ip=ip[:45], sucesso=ok, created_at=clock.agora()))
    if ok and membro is not None and membro.senha_hash and senhas.precisa_rehash(membro.senha_hash):
        membro.senha_hash = senhas.gerar_hash(senha)
    await s.flush()
    return membro if ok else None


async def criar_sessao(
    s: AsyncSession, clock: Clock, membro: Membro, *, horas: int, ip: str, user_agent: str | None
) -> str:
    token = secrets.token_urlsafe(32)
    s.add(SessaoWeb(
        token_hash=hash_token(token), membro_id=membro.id,
        csrf_token=secrets.token_urlsafe(32), expira_em=clock.agora() + timedelta(hours=horas),
        ip=ip[:45], user_agent=(user_agent or "")[:300], created_at=clock.agora(),
    ))  # fmt: skip
    await s.flush()
    return token


async def carregar_usuario(s: AsyncSession, clock: Clock, token: str | None) -> Usuario | None:
    if not token:
        return None
    q = (
        select(SessaoWeb, Membro)
        .join(Membro, Membro.id == SessaoWeb.membro_id)
        .where(
            SessaoWeb.token_hash == hash_token(token),
            SessaoWeb.revogada_em.is_(None),
            SessaoWeb.expira_em > clock.agora(),
            Membro.ativo.is_(True),
        )
    )
    linha = (await s.execute(q)).tuples().first()
    if linha is None:
        return None
    sessao, membro = linha
    return Usuario(membro, sessao.id, sessao.csrf_token)


async def revogar(s: AsyncSession, clock: Clock, token: str | None) -> None:
    if not token:
        return
    sessao = (await s.execute(
        select(SessaoWeb).where(SessaoWeb.token_hash == hash_token(token))
    )).scalar_one_or_none()  # fmt: skip
    if sessao is not None and sessao.revogada_em is None:
        sessao.revogada_em = clock.agora()
        await s.flush()
