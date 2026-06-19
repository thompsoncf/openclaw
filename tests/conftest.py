"""tests/conftest.py — TRAVA DE SEGURANCA: testes NUNCA tocam o banco de producao.

CAUSA RAIZ do incidente: as fixtures faziam `truncate contas ... cascade` usando
DATABASE_URL — que no Render e' o banco de PRODUCAO. Rodar pytest apagava o banco.

Esta trava garante:
1. Os testes SO' rodam se houver TEST_DATABASE_URL (um banco separado, descartavel).
2. Se TEST_DATABASE_URL apontar pro mesmo host de DATABASE_URL (producao), ABORTA.
3. A fixture de banco usa TEST_DATABASE_URL, nunca DATABASE_URL.

Como rodar os testes (local ou CI), com um banco SEPARADO:
    export TEST_DATABASE_URL="postgresql://...banco_de_TESTE..."
    pytest

NUNCA setar TEST_DATABASE_URL = DATABASE_URL de producao.
"""
import os
import pytest


def _host(url: str) -> str:
    # extrai o host pra comparar producao x teste (sem expor credenciais)
    try:
        return url.split("@", 1)[1].split("/", 1)[0]
    except Exception:
        return url


def pytest_configure(config):
    test_url = os.environ.get("TEST_DATABASE_URL")
    prod_url = os.environ.get("DATABASE_URL")

    if not test_url:
        pytest.exit(
            "ABORTADO: TEST_DATABASE_URL nao definida. Os testes apagam tabelas "
            "(truncate) e NAO podem rodar contra producao. Defina um banco de TESTE "
            "separado em TEST_DATABASE_URL. (Isso evita o incidente de apagar o banco.)",
            returncode=2,
        )

    if prod_url and _host(test_url) == _host(prod_url):
        pytest.exit(
            "ABORTADO: TEST_DATABASE_URL aponta pro MESMO host de DATABASE_URL "
            "(producao). Os testes fazem truncate cascade e apagariam tudo. Use um "
            "banco SEPARADO pra testes.",
            returncode=2,
        )


@pytest.fixture()
def test_db_url():
    return os.environ["TEST_DATABASE_URL"]
