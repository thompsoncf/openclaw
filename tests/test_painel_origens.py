"""A tela de Origens: quem entra, o recorte de tempo e o que ela nunca mostra.

A tela existe pra ser lida por gente de FORA da empresa (a agência de tráfego),
o que muda o que vale a pena travar com teste:

* **a fronteira** — investimento, CPL, CAC e ROAS não aparecem, porque não são
  medida do Zaq (e um número nosso divergindo do painel deles nos faz de errados);
* **o sigilo** — nenhum texto de conversa de cliente sai na página;
* **o nicho** (regra 6) — quem vende festa marca "visita", quem vende serviço
  marca "reunião", e quem só vende produto não tem esta tela;
* **o recorte** — a agência compara com a semana que ela fechou, então período
  personalizado que caísse calado no mês corrente seria pior que não existir.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from web import painel_origens as po

CONTA = 7

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, whatsapp text, origem text, origem_codigo text, orcamento_id bigint,
  status text default 'novo', estagio text default 'lead',
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, tipo text, desfecho text,
  prospeccao_id bigint, tipo_evento text, criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint,
  primeiro_ano_centavos bigint default 0, sinal_pago_em timestamptz,
  status text, criado_em timestamptz default now());
"""


def _perfil(compromisso="visita", aplica=True):
    return {"chave": "eventos" if aplica else "produto",
            "vocab": {"compromisso": compromisso, "compromissos": compromisso + "s",
                      "data": True, "pedido": "festa", "compromisso_kpi": ""},
            "aplica": aplica, "filtros": (), "blocos": (), "faixas": (),
            "motivos": (), "nicho": "buffet", "nicho_escolhido": True}


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_painel_origens_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_SQL)
        c.commit()

    estado = {"papel": "dono", "perfil": _perfil()}
    monkeypatch.setattr(po, "get_pool", lambda: pool)
    monkeypatch.setattr(po, "conta_logada", lambda req: (CONTA, "Prime Eventos"))
    monkeypatch.setattr(po.rxp, "perfil_da_conta", lambda p, cid: estado["perfil"])

    app = FastAPI()

    # A ORDEM IMPORTA e é ao contrário do que parece: `add_middleware` insere no
    # começo da pilha, então o ÚLTIMO adicionado é o mais externo. O de sessão
    # precisa rodar antes deste, senão `request.session` nem existe ainda.
    @app.middleware("http")
    async def _papel(request, call_next):
        request.session["papel"] = estado["papel"]
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(po.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    c.estado = estado
    yield c
    pool.close()


def _lead(c, codigo=None, *, respondida_em_min=None, marcou=False, desfecho=None,
          sinal=None, valor=0, texto="oi"):
    orc = None
    if sinal is not None or valor:
        orc = c.execute("insert into orcamentos (conta_id, primeiro_ano_centavos, "
                        "sinal_pago_em) values (%s,%s,%s) returning id",
                        (CONTA, valor, sinal)).fetchone()[0]
    lid = c.execute("insert into prospeccao (conta_id, empresa, origem_codigo, orcamento_id) "
                    "values (%s,'Cliente',%s,%s) returning id",
                    (CONTA, codigo, orc)).fetchone()[0]
    quando = datetime.now(timezone.utc) - timedelta(hours=2)
    cid = c.execute("insert into conversas (conta_id, prospeccao_id, criado_em) "
                    "values (%s,%s,%s) returning id", (CONTA, lid, quando)).fetchone()[0]
    c.execute("insert into mensagens (conversa_id, canal, direcao, texto, criado_em) "
              "values (%s,'whatsapp','in',%s,%s)", (cid, texto, quando))
    if respondida_em_min is not None:
        c.execute("insert into mensagens (conversa_id, canal, direcao, texto, criado_em) "
                  "values (%s,'whatsapp','out','ok',%s)",
                  (cid, quando + timedelta(minutes=respondida_em_min)))
    if marcou:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id, desfecho)
                     values (%s,'Visita — Cliente',%s,%s,%s)""",
                  (CONTA, datetime.now(timezone.utc) - timedelta(hours=1), lid, desfecho))
    return lid


# ------------------------------------------------------------------- quem entra

def test_dono_abre(cliente):
    assert cliente.get("/painel/origens").status_code == 200


def test_gestor_abre(cliente):
    cliente.estado["papel"] = "gestor"
    assert cliente.get("/painel/origens").status_code == 200


@pytest.mark.parametrize("papel", ["vendedor", "financeiro", "restrito", "membro"])
def test_quem_nao_e_dono_nem_gestor_nao_entra(cliente, papel):
    cliente.estado["papel"] = papel
    r = cliente.get("/painel/origens")
    assert r.status_code == 303 and r.headers["location"] == "/painel"


def test_sem_login_vai_pro_login(cliente, monkeypatch):
    monkeypatch.setattr(po, "conta_logada", lambda req: None)
    r = cliente.get("/painel/origens")
    assert r.status_code == 303 and r.headers["location"] == "/login"


# --------------------------------------------------------------- regra 6: nicho

def test_conta_so_de_produto_nao_tem_esta_tela(cliente):
    """Quem vende no caixa não tem funil nem vendedor — a tela não se aplica."""
    cliente.estado["perfil"] = _perfil(aplica=False)
    r = cliente.get("/painel/origens")
    assert r.status_code == 303 and r.headers["location"] == "/painel"


def test_vocabulario_segue_o_nicho(cliente):
    with cliente.pool.connection() as c:
        _lead(c, "A3", marcou=True)
        c.commit()
    assert "Marcaram visita" in cliente.get("/painel/origens").text
    cliente.estado["perfil"] = _perfil(compromisso="reunião")
    html = cliente.get("/painel/origens").text
    assert "Marcaram reunião" in html
    assert "Marcaram visita" not in html


# ------------------------------------------------------------------- o recorte

def test_periodo_desconhecido_cai_no_padrao_sem_quebrar(cliente):
    r = cliente.get("/painel/origens?periodo=banana")
    assert r.status_code == 200
    assert "7 dias" in r.text


def test_personalizado_mostra_os_campos_de_data(cliente):
    html = cliente.get("/painel/origens?periodo=personalizado").text
    assert 'type="date"' in html and 'name="de"' in html and 'name="ate"' in html


def test_personalizado_respeita_as_datas_escolhidas(cliente):
    html = cliente.get("/painel/origens?periodo=personalizado"
                       "&de=2026-08-28&ate=2026-09-03").text
    assert "28/08/2026" in html and "03/09/2026" in html


def test_datas_invertidas_sao_trocadas_e_nao_viram_tela_vazia(cliente):
    html = cliente.get("/painel/origens?periodo=personalizado"
                       "&de=2026-09-03&ate=2026-08-28").text
    assert "28/08/2026" in html and "03/09/2026" in html


# --------------------------------------------------------------- o que aparece

def test_a_linha_do_codigo_aparece_com_o_resultado(cliente):
    with cliente.pool.connection() as c:
        _lead(c, "A3", respondida_em_min=10, marcou=True, desfecho="realizado",
              sinal=datetime.now(timezone.utc), valor=750000)
        c.commit()
    html = cliente.get("/painel/origens").text
    assert "A3" in html
    assert "7.500,00" in html


def test_sem_nenhum_codigo_a_tela_ensina_o_formato(cliente):
    """Tela vazia sem explicação faz a agência achar que o Zaq não funciona."""
    html = cliente.get("/painel/origens").text
    assert "[#A3]" in html


def test_o_aviso_de_cobertura_aparece_quando_falta_resposta(cliente):
    with cliente.pool.connection() as c:
        _lead(c, "A3", marcou=True, desfecho=None)
        c.commit()
    assert "sem resposta" in cliente.get("/painel/origens").text


# ------------------------------------------------------------------- fronteiras

def test_a_tela_nao_fala_de_dinheiro_de_anuncio(cliente):
    """Investimento, CPL, CAC, ROAS e ROI são medida da agência. Se aparecerem
    aqui, alguém cruzou a fronteira sem perceber."""
    with cliente.pool.connection() as c:
        _lead(c, "A3", sinal=datetime.now(timezone.utc), valor=750000)
        c.commit()
    html = cliente.get("/painel/origens").text.lower()
    for proibido in ("investimento", "cpl", "cac", "roas", " roi", "impressões"):
        assert proibido not in html, f"{proibido} não é medida do Zaq"


def test_a_tela_nao_mostra_texto_de_conversa(cliente):
    with cliente.pool.connection() as c:
        _lead(c, "A3", respondida_em_min=5, texto="quero valores pra 150 pessoas")
        c.commit()
    assert "150 pessoas" not in cliente.get("/painel/origens").text


def test_diz_o_que_sem_codigo_mistura(cliente):
    """Prometer que "sem código" é só orgânico seria mentir: junta com quem apagou
    o texto do anúncio antes de enviar."""
    assert "apagou o texto" in cliente.get("/painel/origens").text
