"""A trava do conftest, testada — porque ela ja' falhou duas vezes.

22/08/2026: a checagem de producao so' rodava quando DATABASE_URL existia. Numa
maquina sem DATABASE_URL (container de dev, sessao de agente) TEST_DATABASE_URL
podia apontar pro banco vivo dos clientes e nada barrava. Estes testes fixam o
comportamento novo: FALHA FECHADA.

Chamam pytest_configure direto, com o ambiente monkeypatchado — nao precisam de
banco nenhum.
"""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "conftest_trava", Path(__file__).parent / "conftest.py")
trava = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trava)


PROD_DIRETA = ("postgresql://postgres:senha@db.hkgonlezxdmyjcvdysxs.supabase.co"
               ":5432/postgres")
PROD_POOLER = ("postgresql://postgres.hkgonlezxdmyjcvdysxs:senha@"
               "aws-0-us-east-1.pooler.supabase.com:6543/postgres")
CI_LOCAL = "postgresql://postgres:postgres@localhost:5432/openclaw_test"

_host = trava._host


def _configurar(monkeypatch, *, teste=None, prod=None, escape=None):
    for nome, valor in (("TEST_DATABASE_URL", teste), ("DATABASE_URL", prod),
                        ("PERMITIR_BANCO_NAO_MARCADO", escape)):
        if valor is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valor)
    trava.pytest_configure(None)


def _aborta(monkeypatch, **kw) -> str:
    with pytest.raises(Exception) as e:
        _configurar(monkeypatch, **kw)
    assert type(e.value).__name__ == "Exit", f"esperava pytest.exit, veio {e.value!r}"
    return str(e.value)


# --- portao 1 ---------------------------------------------------------------

def test_sem_url_de_teste_aborta(monkeypatch):
    assert "TEST_DATABASE_URL nao definida" in _aborta(monkeypatch, teste=None)


# --- portao 2 ---------------------------------------------------------------

def test_mesmo_host_da_producao_aborta(monkeypatch):
    outro = "postgresql://postgres:senha@db.hkgonlezxdmyjcvdysxs.supabase.co:5432/teste"
    assert "MESMO host" in _aborta(monkeypatch, teste=outro, prod=PROD_DIRETA)


# --- portao 3: o buraco de 22/08/2026 ---------------------------------------

def test_producao_direta_aborta_mesmo_SEM_database_url(monkeypatch):
    """O caso exato do incidente: DATABASE_URL ausente, alvo = producao."""
    msg = _aborta(monkeypatch, teste=PROD_DIRETA, prod=None)
    assert "PRODUCAO" in msg and "hkgonlezxdmyjcvdysxs" in msg


def test_producao_pelo_pooler_aborta_mesmo_SEM_database_url(monkeypatch):
    """No pooler a referencia vive no USUARIO, nao no host — tem que pegar igual."""
    msg = _aborta(monkeypatch, teste=PROD_POOLER, prod=None)
    assert "PRODUCAO" in msg and "hkgonlezxdmyjcvdysxs" in msg


def test_producao_da_gestora_tambem_aborta(monkeypatch):
    url = "postgresql://postgres:s@db.fzclfyrqrkebatzdzggx.supabase.co:5432/postgres"
    assert "PRODUCAO" in _aborta(monkeypatch, teste=url, prod=None)


def test_escape_NAO_libera_producao(monkeypatch):
    """PERMITIR_BANCO_NAO_MARCADO afrouxa o portao 4, nunca o 3."""
    msg = _aborta(monkeypatch, teste=PROD_POOLER, prod=None, escape="SIM")
    assert "PRODUCAO" in msg


# --- portao 4 ---------------------------------------------------------------

def test_banco_remoto_sem_marca_de_teste_aborta(monkeypatch):
    url = "postgresql://postgres:s@db.abcdefghijklmnopqrst.supabase.co:5432/postgres"
    assert "nada nele diz que e' banco de teste" in _aborta(monkeypatch, teste=url)


def test_banco_remoto_sem_marca_passa_com_o_escape(monkeypatch):
    url = "postgresql://postgres:s@db.abcdefghijklmnopqrst.supabase.co:5432/postgres"
    _configurar(monkeypatch, teste=url, escape="SIM")   # nao levanta


def test_banco_remoto_com_test_no_nome_passa(monkeypatch):
    url = "postgresql://postgres:s@db.abcdefghijklmnopqrst.supabase.co:5432/openclaw_test"
    _configurar(monkeypatch, teste=url)


# --- o caminho feliz que o CI usa -------------------------------------------

def test_postgres_local_do_ci_passa(monkeypatch):
    _configurar(monkeypatch, teste=CI_LOCAL, prod=None)


def test_postgres_local_passa_mesmo_com_database_url_de_producao(monkeypatch):
    """Dev com .env de producao carregado ainda consegue rodar os testes locais."""
    _configurar(monkeypatch, teste=CI_LOCAL, prod=PROD_POOLER)


def test_pooler_de_um_lado_host_direto_do_outro_aborta(monkeypatch):
    """O portao 2 compara HOST e nao veria isto: DATABASE_URL pelo pooler,
    TEST_DATABASE_URL pelo host direto do MESMO projeto. A referencia pega."""
    prod = ("postgresql://postgres.abcdefghijklmnopqrst:s@"
            "aws-0-us-east-1.pooler.supabase.com:6543/postgres")
    teste = "postgresql://postgres:s@db.abcdefghijklmnopqrst.supabase.co:5432/postgres"
    assert _host(prod) != _host(teste)
    assert "PRODUCAO" in _aborta(monkeypatch, teste=teste, prod=prod)
