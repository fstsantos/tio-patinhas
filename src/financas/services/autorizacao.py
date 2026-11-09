"""Matriz de permissões (spec 6.2), validada em todos os serviços.

"Próprios" = lançamentos em que o membro é o titular (``membro_id``).
Membros inativos não executam nenhuma ação.
"""

from __future__ import annotations

from financas.db.models import Lancamento, Membro
from financas.domain.erros import SemPermissao


def exigir_ativo(ator: Membro | None) -> Membro:
    if ator is None or not ator.ativo:
        raise SemPermissao("Você não está autorizado a usar o sistema.")
    return ator


def exigir_admin(ator: Membro | None) -> Membro:
    ator = exigir_ativo(ator)
    if not ator.eh_admin:
        raise SemPermissao("Somente administradores podem fazer isso.")
    return ator


def pode_consultar(ator: Membro | None) -> bool:
    return ator is not None and ator.ativo


def exigir_criar_para(ator: Membro | None, titular_id: int) -> Membro:
    ator = exigir_ativo(ator)
    if titular_id != ator.id and not ator.eh_admin:
        raise SemPermissao("Você só pode registrar lançamentos em seu próprio nome.")
    return ator


def pode_alterar(ator: Membro | None, lancamento: Lancamento) -> bool:
    """Cancelar (bot e web) e editar (web): próprios para membros; qualquer um para admin."""
    if ator is None or not ator.ativo:
        return False
    return ator.eh_admin or lancamento.membro_id == ator.id


def exigir_alterar(ator: Membro | None, lancamento: Lancamento) -> Membro:
    if not pode_alterar(ator, lancamento):
        raise SemPermissao("Você só pode alterar os seus próprios lançamentos.")
    assert ator is not None
    return ator


def pode_ver_cancelados_e_auditoria(ator: Membro | None) -> bool:
    return ator is not None and ator.ativo and ator.eh_admin
