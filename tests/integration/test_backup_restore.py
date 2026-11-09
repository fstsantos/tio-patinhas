"""Teste real: backup.sh → restore.sh num banco novo; dados e relatórios idênticos."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.db.models import Auditoria, Lancamento, Origem, Parcela, TipoLancamento
from financas.db.session import criar_engine, criar_fabrica, normalizar_url
from financas.domain.calendario import Mes
from financas.services import lancamentos as svc
from financas.services import relatorios
from financas.services.integridade import verificar_integridade
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]
RAIZ = Path(__file__).resolve().parents[2]
HOJE = date(2026, 3, 20)


def _bin_postgres() -> str | None:
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return ""
    try:
        import pgserver

        d = Path(pgserver.__file__).parent / "pginstall" / "bin"
        return str(d) if (d / "pg_dump").exists() else None
    except ImportError:
        return None


def _rodar(script: str, *args: str, env: dict[str, str]) -> str:
    r = subprocess.run(
        ["bash", str(RAIZ / "scripts" / script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


async def _fotografia(fabrica: async_sessionmaker[AsyncSession]) -> tuple[object, ...]:
    async with fabrica() as s:
        contagens = tuple(
            [
                int(await s.scalar(select(func.count()).select_from(m)) or 0)
                for m in (Lancamento, Parcela, Auditoria)
            ]
        )
        resumo = await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)
        anual = await relatorios.resumo_anual(s, 2026, HOJE)
        problemas = await verificar_integridade(s)
    return contagens, resumo.orcamento, [m.saldo_acumulado for m in anual.meses], problemas


async def test_backup_e_restauracao_reais(
    fabrica: async_sessionmaker[AsyncSession], banco_migrado: str, tmp_path: Path
) -> None:
    pg_bin = _bin_postgres()
    if pg_bin is None:
        pytest.skip("pg_dump/pg_restore indisponíveis")
    clock = f.relogio(HOJE)
    async with fabrica() as s, s.begin():
        ana = await f.membro(s)
        await f.lancar(s, clock, ana, TipoLancamento.RECEITA, "3500.00", "salário")
        await f.lancar(s, clock, ana, TipoLancamento.DEBITO, "150.00", "mercado")
        r = await f.lancar(s, clock, ana, TipoLancamento.CREDITO, "100.00", "tv", parcelas=3)
        c = await f.lancar(s, clock, ana, TipoLancamento.CREDITO, "50.00", "x", parcelas=2)
        await svc.cancelar(s, clock, ana, c.lancamento.id, origem=Origem.WEB)
        await svc.editar(
            s,
            clock,
            ana,
            r.lancamento.id,
            svc.DadosLancamento(TipoLancamento.CREDITO, r.lancamento.valor_total, "tv", HOJE, 4),
            origem=Origem.WEB,
            revisao_esperada=r.lancamento.revisao,
        )
    original = await _fotografia(fabrica)

    url_origem = make_url(normalizar_url(banco_migrado))
    destino = url_origem.set(database="financas_restaurado")
    admin = criar_engine(url_origem.render_as_string(hide_password=False))
    async with admin.connect() as conexao:
        con = await conexao.execution_options(isolation_level="AUTOCOMMIT")
        await con.execute(text("DROP DATABASE IF EXISTS financas_restaurado"))
        await con.execute(text("CREATE DATABASE financas_restaurado"))
    await admin.dispose()

    env = {
        **os.environ,
        "PG_BIN": pg_bin,
        "BACKUP_RETENCAO_DIAS": "30",
        "DATABASE_URL": url_origem.render_as_string(hide_password=False),
    }
    arquivo = _rodar("backup.sh", str(tmp_path), env=env)
    assert arquivo.endswith(".dump") and Path(arquivo).stat().st_size > 0
    assert oct(Path(arquivo).stat().st_mode)[-3:] == "600"

    sem_confirmar = subprocess.run(
        ["bash", str(RAIZ / "scripts/restore.sh"), arquivo],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sem_confirmar.returncode == 2

    env["RESTORE_DATABASE_URL"] = destino.render_as_string(hide_password=False)
    _rodar("restore.sh", arquivo, "--sim", env=env)
    eng = criar_engine(destino.render_as_string(hide_password=False))
    try:
        restaurado = await _fotografia(criar_fabrica(eng))
        # a restauração preserva os triggers: auditoria segue somente de inserção
        async with eng.connect() as con:
            with pytest.raises(Exception, match="somente de inserção"):
                await con.execute(text("DELETE FROM auditoria"))
    finally:
        await eng.dispose()
    assert restaurado == original
    assert original[3] == []
