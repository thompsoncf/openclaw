"""tests/conftest.py — TRAVA DE SEGURANCA: testes NUNCA tocam o banco de producao.

CAUSA RAIZ do primeiro incidente: as fixtures faziam `truncate contas ... cascade`
usando DATABASE_URL — que no Render e' o banco de PRODUCAO. Rodar pytest apagava
o banco.

SEGUNDO INCIDENTE (22/08/2026): a trava tinha um buraco. A checagem de producao
era `if prod_url and _host(test_url) == _host(prod_url)` — ou seja, ela SO' valia
quando DATABASE_URL estava definida. Numa maquina onde DATABASE_URL NAO existe
(container de dev, sessao de agente, runner de CI de outro repo), a comparacao
era pulada e TEST_DATABASE_URL podia apontar pra producao sem disparar nada.
Foi o que aconteceu: das 17:07 as 19:41 UTC a suite (e os replays de migracao)
rodaram contra o banco de producao. As migracoes tomam ACCESS EXCLUSIVE em
contas/membros/clientes/conversas/orcamentos, e o painel inteiro ficou lento pra
todos os clientes. O teardown chegou a tentar apagar `membros` de producao — so'
nao apagou porque a foreign key `prospeccao_vendedor_id_fkey` barrou.

A trava agora FALHA FECHADA. Sao quatro portoes, nesta ordem:

1. Sem TEST_DATABASE_URL: aborta.
2. TEST_DATABASE_URL no mesmo host de DATABASE_URL: aborta (como antes).
3. TEST_DATABASE_URL com qualquer marca de PRODUCAO (a referencia do projeto
   Supabase, no host OU no usuario do pooler): aborta SEMPRE — independente de
   DATABASE_URL existir. Este portao NAO tem escape.
4. TEST_DATABASE_URL que nao se identifica como banco de teste (host local ou
   nome do banco com "test"/"teste"): aborta. Este tem escape explicito, pra
   quem mantem um banco descartavel com outro nome:
       PERMITIR_BANCO_NAO_MARCADO=SIM pytest
   O escape NAO desliga o portao 3.

Como rodar os testes (local ou CI), com um banco SEPARADO:
    export TEST_DATABASE_URL="postgresql://...banco_de_TESTE..."
    pytest

NUNCA setar TEST_DATABASE_URL = DATABASE_URL de producao.
"""
import os
import re
import pytest


# Referencias de projeto Supabase que sao PRODUCAO. A referencia aparece nas
# duas formas de URL que o Supabase entrega:
#   direta: postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres
#   pooler: postgresql://postgres.<ref>:...@aws-0-<regiao>.pooler.supabase.com:6543/postgres
# Procurar a REFERENCIA (e nao o host) pega as duas — e o host do pooler e'
# compartilhado entre projetos, entao bloquear o host cortaria bancos de teste
# legitimos hospedados no proprio Supabase.
REFS_PRODUCAO = (
    "hkgonlezxdmyjcvdysxs",   # openclaw (o banco que roda o Zaq)
    "fzclfyrqrkebatzdzggx",   # gestora-capital
)

_HOSTS_LOCAIS = ("localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal")


def _host(url: str) -> str:
    # extrai o host pra comparar producao x teste (sem expor credenciais)
    try:
        return url.split("@", 1)[1].split("/", 1)[0]
    except Exception:
        return url


def _banco(url: str) -> str:
    """Nome do banco: o que vem depois da ultima barra, sem querystring."""
    try:
        return url.split("@", 1)[1].split("/", 1)[1].split("?", 1)[0]
    except Exception:
        return ""


def _refs_supabase(url: str) -> set:
    """Toda referencia de projeto Supabase que aparece na URL (host ou usuario)."""
    return set(re.findall(r"\b([a-z]{20})\b", url or ""))


def _eh_local(host: str) -> bool:
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return hostname in _HOSTS_LOCAIS or host in _HOSTS_LOCAIS


def pytest_configure(config):
    test_url = os.environ.get("TEST_DATABASE_URL")
    prod_url = os.environ.get("DATABASE_URL")

    # --- Portao 1: sem banco de teste, nao roda ------------------------------
    if not test_url:
        pytest.exit(
            "ABORTADO: TEST_DATABASE_URL nao definida. Os testes apagam tabelas "
            "(truncate) e NAO podem rodar contra producao. Defina um banco de TESTE "
            "separado em TEST_DATABASE_URL. (Isso evita o incidente de apagar o banco.)",
            returncode=2,
        )

    # --- Portao 2: mesmo host da producao ------------------------------------
    if prod_url and _host(test_url) == _host(prod_url):
        pytest.exit(
            "ABORTADO: TEST_DATABASE_URL aponta pro MESMO host de DATABASE_URL "
            "(producao). Os testes fazem truncate cascade e apagariam tudo. Use um "
            "banco SEPARADO pra testes.",
            returncode=2,
        )

    # --- Portao 3: marca de producao, com ou sem DATABASE_URL ----------------
    # Este e' o portao que faltava. Ele NAO depende de DATABASE_URL existir, que
    # foi exatamente o buraco de 22/08/2026.
    # So' a URL DE TESTE e' julgada aqui. DATABASE_URL apontar pra producao e'
    # normal (o dev tem o .env de producao carregado) e nao pode barrar a suite —
    # ela so' contribui a propria referencia pra lista de proibidos, pra pegar o
    # caso de producao pelo pooler de um lado e pelo host direto do outro, que o
    # portao 2 (comparacao de host) deixaria passar.
    proibidas = set(REFS_PRODUCAO) | _refs_supabase(prod_url or "")
    encontradas = sorted(_refs_supabase(test_url).intersection(proibidas))
    if encontradas:
        pytest.exit(
            "ABORTADO: TEST_DATABASE_URL aponta pro banco de PRODUCAO "
            f"(projeto {', '.join(encontradas)}). Nao existe escape pra este portao: "
            "e' o banco vivo dos clientes — conexao de WhatsApp, canal, conversa e "
            "mensagem. Suba um Postgres descartavel (o CI usa "
            "postgres:16 em localhost) e aponte TEST_DATABASE_URL pra ele.",
            returncode=2,
        )

    # --- Portao 4: o alvo tem que se identificar como banco de teste ---------
    host = _host(test_url)
    banco = _banco(test_url)
    marcado = _eh_local(host) or "test" in banco.lower()
    if not marcado and os.environ.get("PERMITIR_BANCO_NAO_MARCADO") != "SIM":
        pytest.exit(
            f"ABORTADO: TEST_DATABASE_URL aponta pro banco '{banco or '?'}' em um host "
            "remoto e nada nele diz que e' banco de teste. Os testes fazem truncate "
            "cascade — se este for um banco de cliente, ele perde tudo. Renomeie o "
            "banco de teste com 'test' no nome, use um Postgres local, ou — se voce "
            "TEM CERTEZA de que e' descartavel — rode com "
            "PERMITIR_BANCO_NAO_MARCADO=SIM. (Isso nao libera producao: o portao da "
            "referencia de producao continua valendo.)",
            returncode=2,
        )


@pytest.fixture()
def test_db_url():
    return os.environ["TEST_DATABASE_URL"]
