"""As DUAS PORTAS DE FORA da cotação: a API pública e a ferramenta do WhatsApp.

A tela é a terceira, e o gate dela também está aqui — porque é o mesmo tipo de
erro que os três podem cometer, e é o mais caro da base:

* **A chave diz de qual conta é a cotação.** Nenhum `conta_id` vem do corpo. Se
  viesse, quem tivesse a chave de uma corretora cotaria na conta de outra só
  trocando um número no JSON.
* **A comissão não sai no JSON público.** É quanto a corretora ganha, e o
  segurado não vê isso em lugar nenhum do mercado — muito menos num JSON que
  qualquer um lê apertando F12.
* **A tela segue o nicho** (regra 6 do CLAUDE.md): conta que não é corretora não
  abre /painel/cotacoes, e esconder o link do menu não é controle de acesso.
"""
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import cotacao as ct
from finance import cotacao_provedores as cp
from finance import cotacao_tools as ctools
from web import api_cotacao as api
from web import painel_cotacao as pc

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 37
OUTRA = 38
CPF = "52998224725"

_BASE = """
create table contas (id bigint primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint references contas(id),
  nome text, email text, papel text not null default 'membro', ativo boolean not null default true);
create table pessoas (id bigserial primary key, cpf text, cnpj text, tipo text, celular text,
  nome text, email text);
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  pessoa_id bigint references pessoas(id), nome text not null, telefone text, email text,
  endereco text, cidade text, uf text, cep text, obs text,
  ativo boolean not null default true, criado_em timestamptz not null default now());
create table lembretes_enviados (id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso')),
  chave text not null, enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
insert into contas (id, nome) values (37,'Liberal Seguros'), (38,'Outra Corretora');
"""


def _risco_json(**extra):
    d = {"nome": "Fulano de Tal", "cpf": CPF, "nascimento": "1985-04-12",
         "cep": "64000000", "placa": "ABC1D23", "marca_modelo": "FIAT ARGO 1.0",
         "ano_modelo": 2022}
    d.update(extra)
    return d


class _ProvedorFalso(cp.Provedor):
    chave = "falso"
    nome = "Falso"

    def __init__(self, ofertas=None, quebra=False):
        self._ofertas, self._quebra = ofertas or [], quebra

    def cotar(self, risco):
        if self._quebra:
            raise cp.ProvedorErro("a seguradora não respondeu")
        return self._ofertas


@pytest.fixture(scope="module")
def _banco_modulo():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_api_cotacao"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_BASE)
        for m in ("278_apolices.sql", "286_apolices_pdf.sql", "287_apolice_perdida.sql",
                  "304_cotacoes.sql"):
            c.execute((MIG / m).read_text(encoding="utf-8"))
        c.commit()
    yield pool
    pool.close()


@pytest.fixture()
def banco(_banco_modulo):
    """O banco limpo a cada teste. O BANCO é do módulo (criar e derrubar um por
    teste custava 96s e ainda esbarrava em ObjectInUse com a conexão anterior
    ainda aberta); o que zera entre os testes são as LINHAS."""
    with _banco_modulo.connection() as c:
        c.execute("delete from cotacao_ofertas")
        c.execute("delete from cotacao_chaves")
        c.execute("delete from cotacoes")
        c.execute("delete from apolices")
        c.execute("delete from clientes")
        c.execute("delete from membros")
        c.commit()
    return _banco_modulo


@pytest.fixture()
def cliente(banco, monkeypatch):
    monkeypatch.setattr(api, "get_pool", lambda: banco)
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    c.pool = banco
    return c


# ──────────────────────────────────────────────────────────── a API pública

def test_sem_chave_a_api_nao_cota(cliente):
    r = cliente.post("/api/v1/cotacoes", json=_risco_json())
    assert r.status_code == 401
    assert "chave" in r.json()["erro"]


def test_chave_revogada_nao_cota(cliente):
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    chave_id = ct.chaves(cliente.pool, CONTA)[0]["id"]
    ct.revogar_chave(cliente.pool, CONTA, chave_id)
    r = cliente.post("/api/v1/cotacoes", json=_risco_json(),
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_a_chave_diz_de_qual_conta_e_a_cotacao(cliente, monkeypatch):
    """Nenhum conta_id vem do corpo — nem se o corpo insistir."""
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso())
    r = cliente.post("/api/v1/cotacoes", json=_risco_json(conta_id=OUTRA),
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert ct.ler(cliente.pool, CONTA, cid) is not None
    assert ct.ler(cliente.pool, OUTRA, cid) is None


def test_a_cotacao_da_api_nasce_com_origem_api(cliente, monkeypatch):
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso())
    cid = cliente.post("/api/v1/cotacoes", json=_risco_json(),
                       headers={"Authorization": f"Bearer {token}"}).json()["id"]
    assert ct.ler(cliente.pool, CONTA, cid)["origem"] == "api"


def test_o_json_publico_nao_mostra_comissao(cliente, monkeypatch):
    """O segurado não vê quanto o corretor ganha — nem no navegador dele."""
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    p = _ProvedorFalso(ofertas=[ct.Oferta(seguradora="Allianz", premio_total_centavos=408857,
                                          premio_liquido_centavos=380757, iof_centavos=28100,
                                          comissao_pct=20)])
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: p)
    corpo = cliente.post("/api/v1/cotacoes", json=_risco_json(),
                         headers={"Authorization": f"Bearer {token}"}).json()
    assert corpo["ofertas"][0]["premio_total_centavos"] == 408857
    assert "comissao" not in str(corpo)


def test_risco_invalido_volta_422_com_a_frase_em_portugues(cliente):
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    r = cliente.post("/api/v1/cotacoes", json=_risco_json(cpf="11111111111"),
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422 and "CPF" in r.json()["erro"]


def test_provedor_que_cai_ainda_devolve_a_cotacao(cliente, monkeypatch):
    """201 e não 500: a cotação existe na conta da corretora, e é um lead. Devolver
    erro faria o site apagar o que a corretora acabou de ganhar."""
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso(quebra=True))
    r = cliente.post("/api/v1/cotacoes", json=_risco_json(),
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    assert r.json()["situacao"] == "falhou"
    # o site recebe a frase genérica; o motivo REAL (que nomeia o fornecedor da
    # corretora) fica guardado pra tela do corretor
    assert r.json()["erro"] == api.ERRO_PUBLICO
    assert "provedor" not in r.json()
    cot = ct.ler(cliente.pool, CONTA, r.json()["id"])
    assert "não respondeu" in cot["erro"]


def test_a_chave_sem_o_prefixo_Bearer_tambem_serve(cliente, monkeypatch):
    """É o erro mais comum de primeira integração, e 401 por causa dele gasta uma
    tarde de alguém."""
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso())
    assert cliente.post("/api/v1/cotacoes", json=_risco_json(),
                        headers={"Authorization": token}).status_code == 201
    assert cliente.post("/api/v1/cotacoes", json=_risco_json(),
                        headers={"X-API-Key": token}).status_code == 201


def test_ler_cotacao_de_outra_conta_e_404_e_nao_403(cliente, monkeypatch):
    """403 já contaria que ela existe."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso())
    cid = ct.criar(cliente.pool, OUTRA, ct.normalizar_risco(_risco_json()))
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    r = cliente.get(f"/api/v1/cotacoes/{cid}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_o_token_nao_fica_no_banco(cliente):
    """Vazado o banco, ninguém cota no nome da corretora."""
    token = ct.criar_chave(cliente.pool, CONTA, "site")
    with cliente.pool.connection() as c:
        linhas = c.execute("select prefixo, token_hash from cotacao_chaves").fetchall()
    prefixo, h = linhas[0]
    assert token.startswith(prefixo) and token not in h and len(h) == 64


# ─────────────────────────────────────────────── a ferramenta do WhatsApp

def test_a_ferramenta_pede_so_o_que_falta(banco):
    ferr = {f.nome: f for f in ctools.construir_ferramentas_cotacao(banco, CONTA)}
    resposta = ferr["cotar_seguro"].executar({"cpf": CPF, "nascimento": "1985-04-12"})
    assert "CEP" in resposta


def test_a_cotacao_do_whatsapp_nasce_com_origem_whatsapp(banco, monkeypatch):
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso(
        ofertas=[ct.Oferta(seguradora="HDI", premio_total_centavos=280000, parcelas=10)]))
    ferr = {f.nome: f for f in ctools.construir_ferramentas_cotacao(banco, CONTA)}
    resposta = ferr["cotar_seguro"].executar(_risco_json())
    assert "HDI" in resposta and "R$ 2.800,00" in resposta and "/painel/cotacoes/" in resposta
    assert ct.listar(banco, CONTA)[0]["origem"] == "whatsapp"


def test_sem_multicalculo_a_ferramenta_diz_o_que_fazer_em_vez_de_calar(banco, monkeypatch):
    """O caminho normal enquanto não há API contratada: o risco fica guardado e o
    corretor lança as ofertas na tela."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: cp.ProvedorManual())
    ferr = {f.nome: f for f in ctools.construir_ferramentas_cotacao(banco, CONTA)}
    resposta = ferr["cotar_seguro"].executar(_risco_json())
    assert "lance as ofertas" in resposta


def test_ver_cotacao_sem_numero_lista_as_ultimas(banco, monkeypatch):
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: cp.ProvedorManual())
    ferr = {f.nome: f for f in ctools.construir_ferramentas_cotacao(banco, CONTA)}
    ferr["cotar_seguro"].executar(_risco_json())
    resposta = ferr["ver_cotacao"].executar({})
    assert "Fulano" in resposta


# ───────────────────────────────────────────────── a tela segue o nicho

@pytest.fixture()
def painel(banco, monkeypatch):
    monkeypatch.setattr(pc, "get_pool", lambda: banco)
    # o corretor logado precisa existir: `cotacoes.corretor_id` é FK pra `membros`,
    # e em produção quem está na sessão sempre tem linha lá
    with banco.connection() as c:
        c.execute("insert into membros (id, conta_id, nome, papel) "
                  "values (1,%s,'Corretor','vendedor')", (CONTA,))
        c.commit()
    estado = {"nicho": "seguros"}
    conta = tuple([CONTA] + [None] * 14)
    monkeypatch.setattr(pc, "conta_logada", lambda request: conta)
    monkeypatch.setattr(pc, "nicho_da_conta", lambda c: estado["nicho"])
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pc.router)

    @app.post("/_entrar")
    async def _entrar(request: Request, papel: str = "dono"):
        request.session["papel"] = papel
        request.session["membro_id"] = 1
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar")
    c.estado = estado
    return c


def test_conta_que_nao_e_corretora_nao_abre_a_tela(painel):
    """Regra 6: a tela segue o nicho. E a trava está no SERVIDOR — esconder o link
    do menu não é controle de acesso."""
    painel.estado["nicho"] = "eventos"
    r = painel.get("/painel/cotacoes")
    assert r.status_code == 303 and r.headers["location"] == "/painel"
    assert painel.post("/painel/cotacoes", data={"cpf": CPF}).status_code == 303


def test_a_corretora_abre_a_tela(painel):
    r = painel.get("/painel/cotacoes")
    assert r.status_code == 200 and "Cotações" in r.text


def test_o_formulario_cria_e_ja_cota(painel, monkeypatch):
    """Um clique, não dois: 'salvar e depois cotar' obrigaria o corretor a lembrar
    do segundo passo com o cliente no telefone."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: _ProvedorFalso(
        ofertas=[ct.Oferta(seguradora="Porto", premio_total_centavos=310000)]))
    r = painel.post("/painel/cotacoes", data=_risco_json())
    assert r.status_code == 303 and r.headers["location"].startswith("/painel/cotacoes/")
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    cot = ct.ler(pc.get_pool(), CONTA, cid)
    assert cot["situacao"] == "cotada" and cot["ofertas"][0]["seguradora"] == "Porto"


def test_risco_invalido_volta_pra_tela_com_o_motivo(painel):
    r = painel.post("/painel/cotacoes", data=_risco_json(cpf="11111111111"))
    assert r.status_code == 303 and "erro=" in r.headers["location"]


def test_a_oferta_digitada_a_mao_entra_e_vira_proposta(painel, monkeypatch):
    """O caminho do dia 1, inteiro: sem provedor, a corretora digita e fecha."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: cp.ProvedorManual())
    loc = painel.post("/painel/cotacoes", data=_risco_json()).headers["location"]
    cid = int(loc.rsplit("/", 1)[1])
    pool = pc.get_pool()
    painel.post(f"/painel/cotacoes/{cid}/oferta",
                data={"seguradora": "Allianz", "premio_total": "4.088,57",
                      "premio_liquido": "3.807,57", "iof": "281,00", "parcelas": "10"})
    o = ct.ofertas(pool, CONTA, cid)[0]
    assert o["premio_total_centavos"] == 408857 and o["premio_liquido_centavos"] == 380757
    painel.post(f"/painel/cotacoes/{cid}/escolher", data={"oferta_id": str(o["id"])})
    r = painel.post(f"/painel/cotacoes/{cid}/proposta", data={"numero_proposta": "139041981"})
    assert r.headers["location"].startswith("/painel/renovacoes")
    assert ct.ler(pool, CONTA, cid)["situacao"] == "proposta"


def test_a_oferta_manual_ACRESCENTA_em_vez_de_substituir(painel, monkeypatch):
    """Aqui quem cota é a pessoa, uma seguradora de cada vez — apagar as anteriores
    a cada linha tornaria o comparativo impossível de montar à mão."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: cp.ProvedorManual())
    loc = painel.post("/painel/cotacoes", data=_risco_json()).headers["location"]
    cid = int(loc.rsplit("/", 1)[1])
    for seg, v in (("Porto", "4.100,00"), ("HDI", "3.850,00")):
        painel.post(f"/painel/cotacoes/{cid}/oferta",
                    data={"seguradora": seg, "premio_total": v})
    achadas = ct.ofertas(pc.get_pool(), CONTA, cid)
    assert [o["seguradora"] for o in achadas] == ["HDI", "Porto"]


def test_o_vendedor_nao_emite_chave_de_api(painel):
    """Chave de API é credencial da empresa, não ferramenta de venda."""
    painel.post("/_entrar", params={"papel": "vendedor"})
    r = painel.post("/painel/cotacoes/chaves/nova", data={"rotulo": "minha"})
    assert r.status_code == 303 and r.headers["location"] == "/painel/cotacoes"
    assert ct.chaves(pc.get_pool(), CONTA) == []


def test_a_tela_do_detalhe_mostra_a_oferta_e_o_roteiro(painel, monkeypatch):
    """A tela que o corretor mais olha. Renderiza com o template de verdade — é o
    único jeito de pegar um `{% if %}` desbalanceado ou um campo que mudou de nome."""
    monkeypatch.setattr(cp, "provedor_ativo", lambda *a, **k: cp.ProvedorManual())
    cid = int(painel.post("/painel/cotacoes",
                          data=_risco_json()).headers["location"].rsplit("/", 1)[1])
    painel.post(f"/painel/cotacoes/{cid}/oferta",
                data={"seguradora": "Allianz", "premio_total": "4.088,57", "parcelas": "10"})
    corpo = painel.get(f"/painel/cotacoes/{cid}").text
    assert "Allianz" in corpo and "R$ 4.088,57" in corpo
    assert "sem IOF separado" in corpo        # a honestidade sobre a comissão
    o = ct.ofertas(pc.get_pool(), CONTA, cid)[0]
    painel.post(f"/painel/cotacoes/{cid}/escolher", data={"oferta_id": str(o["id"])})
    r = painel.post(f"/painel/cotacoes/{cid}/emitir")
    assert "roteiro=1" in r.headers["location"]
    corpo = painel.get(f"/painel/cotacoes/{cid}?roteiro=1").text
    assert "529.982.247-25" in corpo and "Pra digitar no portal" in corpo
