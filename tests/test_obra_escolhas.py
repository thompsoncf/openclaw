"""A escolha com toque: "de qual obra?" e "que etapa ficou pronta?" viram botões
(finance/escolhas.py, finance/whatsapp_interativo.py; pedido do dono em 02/10/2026).

`test_o_conteudo_do_twilio_e_criado_uma_vez` — cada nota não pode criar um
conteúdo novo no Twilio: a mesma lista de obras reaproveita o mesmo.

`test_sem_canal_de_botao_as_opcoes_vao_escritas` — a pergunta nunca some: canal
sem botão (ou Twilio fora) recebe as opções no texto.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import escolhas as esc
from finance import obras as ob
from finance import tools_pj
from finance import whatsapp_interativo as wi

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "132_plano_contas_centros_custo.sql", "349_plano_obras.sql", "351_obras.sql",
              "478_obra_quadras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_escolhas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    with p.connection() as c:
        c.execute("create table if not exists app_config (chave text primary key, valor text, "
                  "atualizado_em timestamptz default now())")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'PX2') returning id").fetchone()[0]
        c.commit()
    return cid


# ── as opções ─────────────────────────────────────────────────────────────
def test_obras_as_mais_novas_primeiro_e_dividir(pool, conta):
    ob.criar_obra(pool, conta, "Casa 1", "casa")
    ob.criar_obra(pool, conta, "Reforma da Dona Márcia Souza Lima", "reforma")
    e = esc.de_obra(pool, conta)
    assert [o["titulo"] for o in e["opcoes"]] == ["Reforma da Dona Márcia S", "Casa 1", esc.DIVIDIR]
    assert e["botao"] == "Ver obras" and len(e["opcoes"][0]["titulo"]) <= 24
    assert e["opcoes"][0]["descricao"].startswith("Reforma · 0%")


def test_uma_obra_so_nao_tem_dividir_e_sem_obra_nao_tem_lista(pool, conta):
    assert esc.de_obra(pool, conta) is None
    ob.criar_obra(pool, conta, "Casa única", "casa")
    assert [o["titulo"] for o in esc.de_obra(pool, conta)["opcoes"]] == ["Casa única"]


def test_mais_de_dez_obras_avisa_quem_ficou_de_fora(pool, conta):
    for n in range(1, 13):
        ob.criar_obra(pool, conta, f"Casa {n}", "casa")
    e = esc.de_obra(pool, conta)
    assert len(e["opcoes"]) == 10 and e["opcoes"][0]["titulo"] == "Casa 12"
    assert e["opcoes"][-1]["titulo"] == esc.DIVIDIR and "3 obras fora da lista" in e["texto"]


def test_com_quadra_entra_dividir_na_quadra(pool, conta):
    from finance import obra_grupos as og
    ob.criar_obra(pool, conta, "Casa 1", "casa")
    with pool.connection() as c:
        c.execute("insert into obra_grupos (conta_id, nome) values (%s, 'Quadra 4')", (conta,))
        c.commit()
    assert "Quadra 4 (dividir)" in [o["titulo"] for o in esc.de_obra(pool, conta)["opcoes"]]
    assert og.listar_grupos(pool, conta)


def test_etapas_que_faltam(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1", "casa")
    ob.marcar_etapa(pool, conta, o["id"], "fundação")
    e = esc.de_etapa(pool, conta, ob.obter_obra(pool, conta, o["id"]))
    assert e["opcoes"][0]["titulo"] == "Estrutura" and len(e["opcoes"]) == 10
    assert e["botao"] == "Ver etapas" and "Casa 1" in e["texto"]
    for et in ob.obter_obra(pool, conta, o["id"])["etapas"]:
        ob.marcar_etapa(pool, conta, o["id"], et["id"])
    assert esc.de_etapa(pool, conta, ob.obter_obra(pool, conta, o["id"])) is None


# ── o WhatsApp: lista ou botões, criado uma vez ───────────────────────────
ESC3 = {"texto": "Toque na obra.", "botao": "Ver obras",
        "opcoes": [{"titulo": "Casa 1", "descricao": "x"}, {"titulo": "Casa 2", "descricao": "y"}]}


def test_ate_tres_curtas_sao_botoes_e_o_resto_e_lista():
    assert "twilio/quick-reply" in wi._tipos(ESC3)
    longa = dict(ESC3, opcoes=ESC3["opcoes"] + [{"titulo": "Reforma da Dona Márcia S"}])
    lista = wi._tipos(longa)["twilio/list-picker"]
    assert lista["button"] == "Ver obras" and len(lista["items"]) == 3


class _Resp:
    def __init__(self, code, sid="HXabc"):
        self.status_code, self._sid, self.text = code, sid, ""

    def json(self):
        return {"sid": self._sid}


def test_o_conteudo_do_twilio_e_criado_uma_vez(pool, monkeypatch):
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+10000000000")
    posts, msgs = [], []
    post = lambda url, payload: posts.append(payload) or _Resp(201)          # noqa: E731
    criar = lambda **kw: msgs.append(kw)                                      # noqa: E731
    assert wi.enviar(pool, "whatsapp:+5599988887777", ESC3, post=post, criar_mensagem=criar)
    assert wi.enviar(pool, "whatsapp:+5599988887777", ESC3, post=post, criar_mensagem=criar)
    assert len(posts) == 1 and [m["content_sid"] for m in msgs] == ["HXabc", "HXabc"]
    assert msgs[0]["to"] == "whatsapp:+5599988887777"


def test_twilio_recusou_volta_false(pool, monkeypatch):
    outra = dict(ESC3, texto="Outra lista.")
    assert not wi.enviar(pool, "whatsapp:+55", outra, post=lambda u, p: _Resp(400),
                         criar_mensagem=lambda **kw: None)


# ── o agente e o Telegram ─────────────────────────────────────────────────
def _ferramentas(pool, conta, monkeypatch, livro):
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    return {f.nome: f for f in tools_pj.construir_ferramentas_pj(pool, conta, None, livro=livro)}


def test_com_canal_de_botao_a_escolha_vai_pro_livro(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1", "casa")
    ob.criar_obra(pool, conta, "Casa 2", "casa")
    livro = SimpleNamespace(canal_interativo=True, escolha=None)
    txt = _ferramentas(pool, conta, monkeypatch, livro)["oferecer_escolha"].executar({"tipo": "obra"})
    assert "Os botões vão logo depois" in txt and "sem listar" in txt
    assert [o["titulo"] for o in livro.escolha["opcoes"]] == ["Casa 2", "Casa 1", esc.DIVIDIR]


def test_sem_canal_de_botao_as_opcoes_vao_escritas(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1", "casa")
    livro = SimpleNamespace(canal_interativo=False, escolha=None)
    txt = _ferramentas(pool, conta, monkeypatch, livro)["oferecer_escolha"].executar({"tipo": "obra"})
    assert txt.endswith("1. Casa 1") and livro.escolha is None


def test_a_escolha_da_etapa_pelo_agente(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 2", "casa")
    livro = SimpleNamespace(canal_interativo=True, escolha=None)
    f = _ferramentas(pool, conta, monkeypatch, livro)
    f["oferecer_escolha"].executar({"tipo": "etapa", "obra": "casa 2"})
    assert livro.escolha["tipo"] == "etapa" and livro.escolha["opcoes"][0]["titulo"] == "Preliminares e fundação"
    assert "oferecer_escolha" in ob.bloco_persona(pool, conta)


def test_o_telegram_monta_o_teclado():
    import telegram_bot as tb
    ag = SimpleNamespace(livro=SimpleNamespace(escolha=ESC3))
    teclado = tb._teclado_da_escolha(ag)
    assert [linha[0].text for linha in teclado.keyboard] == ["Casa 1", "Casa 2"]
    assert teclado.one_time_keyboard is True
    assert tb._teclado_da_escolha(SimpleNamespace(livro=SimpleNamespace(escolha=None))) is None
