"""A tela do aviso e a faixa de versão — as duas peças, pelo HTTP de verdade.

A mira (quem recebe o quê) está prendida em test_novidades.py. Aqui prende-se o
resto, que é onde erro de tela costuma morar:

* **quem ABRE a tela** — esconder item de menu não é controle de acesso. O
  vendedor não vê o item e também não entra pela URL;
* **o "Entendi" não aceita id de fora** — aviso de outro público não vira linha em
  `novidade_lida` porque alguém digitou o número na URL;
* **abrir marca a 'novidade' e NÃO marca a 'mudanca'** — é a diferença inteira
  entre os dois tipos, e é ela que responde "quem já viu";
* **o que a tela mostra nesta visita é o estado de ANTES** de marcar, senão o
  aviso nasce com ✓ lida na mesma resposta em que apareceu;
* **autoescape** — o template se chama `novidades.html` justamente porque
  `select_autoescape()` decide pela EXTENSÃO do nome. Registrado sem ela (como os
  outros templates do portal), o título de um aviso sairia cru. Já aconteceu duas
  vezes neste repo;
* **o JavaScript existe e roda** — `node --check` no que sai renderizado, das
  duas peças.

Banco dedicado e descartável.
"""
import os
import re
import shutil
import subprocess

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from contas import equipe as eq
from finance import novidades as nv
from tests.test_novidades import BASE
from web import portal as pt
from web import versao as vs

CONTA_EV = 11        # nicho eventos
CONTA_REC = 22       # nicho recorrente (consultoria)


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_novidades_tela"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table nichos (id bigserial primary key, nome text, slug text unique)")
        c.execute("""create table contas (id bigserial primary key, nome text,
                     nicho_id bigint references nichos(id),
                     criado_em timestamptz not null default now(), chip_de bigint)""")
        c.execute((BASE / "174_novidades.sql").read_text(encoding="utf-8"))
        # 184 amplia o check de `publico` com o primeiro portão de CONTA
        # (`canal_proprio`). Sem ela aqui, o schema do teste fica com a lista
        # antiga e a paridade banco × Python falha — que é o teste fazendo
        # o trabalho dele.
        c.execute((BASE / "184_novidade_voz_e_porta_fechada.sql"
                   ).read_text(encoding="utf-8"))
        c.execute("insert into nichos (nome, slug) values ('Eventos','eventos'),"
                  "('Consultoria','consultoria')")
        c.execute("""insert into contas (id, nome, nicho_id, criado_em) values
            (%s,'Prime',(select id from nichos where slug='eventos'),      now() - interval '90 days'),
            (%s,'SaaS', (select id from nichos where slug='consultoria'),  now() - interval '90 days')""",
                  (CONTA_EV, CONTA_REC))
        c.commit()

    monkeypatch.setattr(pt, "get_pool", lambda: pool)
    estado = {"conta": CONTA_EV}

    def _logada(request):
        if not request.session.get("conta_id"):
            return None
        conta = [None] * 16
        conta[0], conta[4] = estado["conta"], "zaq_pj"
        conta[11] = conta[12] = conta[13] = conta[14] = True
        return tuple(conta)
    monkeypatch.setattr(pt, "conta_logada", _logada)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pt.router)

    @app.post("/_entrar")
    async def _entrar(request: Request, dados: dict):
        request.session["conta_id"] = estado["conta"]
        request.session["papel"] = dados.get("papel", "dono")
        request.session["membro_id"] = dados.get("membro_id")
        return {"ok": True}

    @app.post("/_sair")
    async def _sair(request: Request):
        request.session.clear()
        return {"ok": True}

    c = TestClient(app)
    c.post("/_entrar", json={"papel": "dono"})
    c.pool = pool
    c.estado = estado
    yield c
    pool.close()


def _aviso(pool, chave, publico="todos", tipo="novidade", titulo=None):
    with pool.connection() as c:
        c.execute("""insert into novidades (chave, tipo, publico, titulo, corpo)
                     values (%s,%s,%s,%s,'corpo do aviso')""",
                  (chave, tipo, publico, titulo or ("T " + chave)))
        c.commit()


def _lidas(pool):
    with pool.connection() as c:
        return c.execute("""select n.chave, l.conta_id, l.membro_id
                              from novidade_lida l join novidades n on n.id = l.novidade_id
                             order by n.chave""").fetchall()


# ══════════════════════════════════════════════ peça 1: a faixa de versão

def test_a_versao_e_a_mesma_do_processo(cliente):
    r = cliente.get("/painel/versao")
    assert r.status_code == 200 and r.json() == {"v": vs.VERSAO}


def test_sem_sessao_a_versao_nao_responde(cliente):
    """Sem sessão o JS fica quieto — em vez de piscar faixa em tela de login."""
    cliente.post("/_sair")
    r = cliente.get("/painel/versao")
    assert r.status_code == 401 and "v" not in r.json()


def test_a_versao_nao_muda_entre_duas_chamadas(cliente):
    """Se ela mudasse a cada chamada, a faixa apareceria sozinha. O fallback é a
    data do código justamente pra ser IGUAL entre processos do mesmo deploy —
    dois workers com relógios diferentes fariam a faixa piscar pra sempre."""
    assert cliente.get("/painel/versao").json() == cliente.get("/painel/versao").json()


def test_o_fallback_nao_depende_de_quando_o_processo_subiu(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert vs._calcular() == vs._calcular()
    assert vs._calcular().startswith("c")     # data do código, não do relógio


def test_a_faixa_nasce_escondida_e_so_o_js_liga(cliente):
    """HTML servido nunca mostra a faixa: quem decide é a comparação no navegador."""
    h = cliente.get("/painel/novidades").text
    assert 'id="ver-nova"' in h
    assert re.search(r"#ver-nova\{[^}]*display:none", h), "a faixa tem que nascer display:none"
    assert 'class="on"' not in h.split('id="ver-nova"')[1][:400]
    assert vs.VERSAO in h                      # a versão que ESTA página carregou


def test_o_versao_e_livre_pra_todo_papel():
    """O gate de web/app.py é whitelist por papel. Barrado, o vendedor — que é
    quem passa o dia na mesma aba — nunca veria a faixa."""
    for papel in ("gestor", "vendedor", "financeiro", "restrito"):
        assert "/painel/versao" in eq.rotas_do_papel(papel), papel


# ══════════════════════════════════════════════ peça 2: quem abre a tela

def test_dono_e_gestor_recebem_o_aviso_o_resto_nao():
    assert eq.recebe_novidades("dono") and eq.recebe_novidades("gestor")
    assert eq.recebe_novidades(None)                      # login do portal é por conta
    for papel in ("vendedor", "financeiro", "membro", "restrito"):
        assert eq.recebe_novidades(papel) is False, papel


def test_gerir_nao_serviria_de_portao():
    """A armadilha: `caps.gerir` é só do DONO — o gestor tem vendas+financeiro e
    gerir=False. Gatear por ele deixaria de fora justamente quem opera a tela."""
    assert eq.caps_do_papel("gestor")["gerir"] is False


def test_o_gestor_entra_na_tela(cliente):
    _aviso(cliente.pool, "a", "eventos")
    cliente.post("/_entrar", json={"papel": "gestor", "membro_id": 7})
    r = cliente.get("/painel/novidades")
    assert r.status_code == 200 and "T a" in r.text


def test_o_vendedor_nao_entra_nem_pela_url(cliente):
    """Esconder do menu não é controle de acesso: a URL é digitável."""
    _aviso(cliente.pool, "a", "eventos")
    cliente.post("/_entrar", json={"papel": "vendedor", "membro_id": 8})
    r = cliente.get("/painel/novidades", follow_redirects=False)
    assert r.status_code == 303
    assert "T a" not in cliente.get("/painel/novidades").text


def test_o_vendedor_nao_marca_lida(cliente):
    _aviso(cliente.pool, "a", "eventos")
    nid = nv.listar(cliente.pool, CONTA_EV)[0]["id"]
    cliente.post("/_entrar", json={"papel": "vendedor", "membro_id": 8})
    assert cliente.post(f"/painel/novidades/{nid}/lida").status_code == 403
    assert _lidas(cliente.pool) == []


def test_o_item_do_menu_so_aparece_pra_quem_recebe(cliente):
    _aviso(cliente.pool, "a", "eventos")
    h = cliente.get("/painel/novidades").text
    assert 'href="/painel/novidades"' in h and ">Sistema<" in h
    # e o menu obedece ao MESMO portão: sem ele, nem o grupo nem o link saem
    sem = pt._env.get_template("novidades.html").render(
        logado=True, itens=[], caps={}, ve_novidades=False, n_contextos=0,
        papel="vendedor", conta=None, secao_ativa="", versao_app="v")
    assert 'href="/painel/novidades"' not in sem and ">Sistema<" not in sem


# ══════════════════════════════════════════════ o que a tela mostra

def test_a_tela_mostra_so_o_que_e_da_conta(cliente):
    _aviso(cliente.pool, "geral", "todos", titulo="Vale pra todo mundo")
    _aviso(cliente.pool, "ev", "eventos", titulo="So de eventos")
    _aviso(cliente.pool, "rec", "recorrente", titulo="So de recorrente")
    h = cliente.get("/painel/novidades").text
    assert "Vale pra todo mundo" in h and "So de eventos" in h
    assert "So de recorrente" not in h

    cliente.estado["conta"] = CONTA_REC
    cliente.post("/_entrar", json={"papel": "dono"})
    h = cliente.get("/painel/novidades").text
    assert "Vale pra todo mundo" in h and "So de recorrente" in h
    assert "So de eventos" not in h


def test_conta_sem_aviso_ve_a_tela_vazia_e_nao_um_erro(cliente):
    r = cliente.get("/painel/novidades")
    assert r.status_code == 200 and "Nada novo por aqui" in r.text


def test_abrir_marca_a_novidade_e_nao_a_mudanca(cliente):
    """A diferença inteira entre os dois tipos. Ganhar tela nova não precisa de
    confirmação; perder um botão precisa — e é o que permite saber QUEM já viu."""
    _aviso(cliente.pool, "ganhou", "eventos", tipo="novidade")
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    cliente.get("/painel/novidades")
    assert [r[0] for r in _lidas(cliente.pool)] == ["ganhou"]
    assert nv.nao_lidas(cliente.pool, CONTA_EV) == 1        # a mudança segue pendente


def test_nesta_visita_o_aviso_ainda_aparece_como_novo(cliente):
    """Marcar ANTES de renderizar zera a bolinha já nesta resposta; mostrar o
    estado de ANTES evita o aviso nascer com ✓ lida na mesma tela em que apareceu.
    Na visita seguinte, aí sim."""
    _aviso(cliente.pool, "ganhou", "eventos", tipo="novidade")
    assert 'class="nv nova"' in cliente.get("/painel/novidades").text
    assert 'class="nv nova lida"' in cliente.get("/painel/novidades").text


def test_o_botao_entendi_so_sai_na_mudanca_pendente(cliente):
    _aviso(cliente.pool, "ganhou", "eventos", tipo="novidade")
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    h = cliente.get("/painel/novidades").text
    assert h.count("nv-ok") == 2               # a classe do botão + o seletor do JS
    nid = [n["id"] for n in nv.listar(cliente.pool, CONTA_EV) if n["chave"] == "perdeu"][0]
    assert f'data-id="{nid}"' in h


def test_a_bolinha_conta_so_o_que_falta_ler(cliente):
    _aviso(cliente.pool, "ganhou", "eventos", tipo="novidade")
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    h = cliente.get("/painel/novidades").text
    assert 'class="nvb">1<' in h               # a novidade já foi; sobra a mudança
    nid = [n["id"] for n in nv.listar(cliente.pool, CONTA_EV) if n["chave"] == "perdeu"][0]
    cliente.post(f"/painel/novidades/{nid}/lida")
    assert 'class="nvb"' not in cliente.get("/painel/novidades").text


# ══════════════════════════════════════════════ o "Entendi"

def test_entendi_marca_por_pessoa(cliente):
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    nid = nv.listar(cliente.pool, CONTA_EV)[0]["id"]
    cliente.post("/_entrar", json={"papel": "gestor", "membro_id": 7})
    assert cliente.post(f"/painel/novidades/{nid}/lida").json() == {"ok": True}
    assert _lidas(cliente.pool) == [("perdeu", CONTA_EV, 7)]
    # o dono da mesma conta ainda não confirmou
    assert nv.nao_lidas(cliente.pool, CONTA_EV, membro_id=None) == 1


def test_entendi_duas_vezes_continua_ok(cliente):
    """O botão é clicável de novo enquanto a resposta não volta; a segunda
    resposta não pode desfazer a primeira na tela."""
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    nid = nv.listar(cliente.pool, CONTA_EV)[0]["id"]
    assert cliente.post(f"/painel/novidades/{nid}/lida").json() == {"ok": True}
    assert cliente.post(f"/painel/novidades/{nid}/lida").json() == {"ok": True}
    assert len(_lidas(cliente.pool)) == 1


def test_id_de_aviso_que_a_conta_nao_ve_devolve_404(cliente):
    """Aviso de OUTRO público não vira linha em novidade_lida só porque alguém
    digitou o número na URL — e a resposta não conta que ele existe."""
    _aviso(cliente.pool, "rec", "recorrente", tipo="mudanca", titulo="So de recorrente")
    nid = nv.listar(cliente.pool, CONTA_REC)[0]["id"]
    r = cliente.post(f"/painel/novidades/{nid}/lida")
    assert r.status_code == 404
    assert "So de recorrente" not in r.text
    assert _lidas(cliente.pool) == []


def test_id_que_nao_existe_devolve_404(cliente):
    assert cliente.post("/painel/novidades/99999/lida").status_code == 404


def test_sem_sessao_nao_marca(cliente):
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    nid = nv.listar(cliente.pool, CONTA_EV)[0]["id"]
    cliente.post("/_sair")
    assert cliente.post(f"/painel/novidades/{nid}/lida").status_code == 401
    assert _lidas(cliente.pool) == []


# ══════════════════════════════════════════════ escape e JavaScript

def test_o_titulo_do_aviso_sai_escapado(cliente):
    """A armadilha do Jinja que já mordeu duas vezes neste repo:
    `select_autoescape()` decide pela EXTENSÃO do nome do template. Registrado como
    'novidades' (como os outros do portal) o autoescape fica DESLIGADO e isto aqui
    sai cru. O '.html' no nome é o que liga."""
    _aviso(cliente.pool, "x", "eventos", titulo="<script>alert(1)</script>")
    h = cliente.get("/painel/novidades").text
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


def _scripts(html):
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_javascript_das_duas_pecas_compila(cliente):
    _aviso(cliente.pool, "perdeu", "eventos", tipo="mudanca")
    js = _scripts(cliente.get("/painel/novidades").text)
    assert len(js) >= 2, "faltou script na página (faixa de versão e/ou o Entendi)"
    inteiro = "\n;\n".join(js)
    assert "ver-nova" in inteiro and "nv-ok" in inteiro
    p = subprocess.run(["node", "--check", "-"], input=inteiro,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


_HARNESS = r"""
// ---- stubs mínimos: só o que o script da faixa toca de verdade ----
let agora = 1000000;
Date.now = () => agora;
const els = {};
function el(id){
  if(!els[id]) els[id] = {id, cls:new Set(), onclick:null,
    classList:{add:c=>els[id].cls.add(c), remove:c=>els[id].cls.delete(c)}};
  return els[id];
}
let servidor = "__V0__", chamadas = 0;   // a MESMA que a página carregou
globalThis.fetch = () => { chamadas++;
  return Promise.resolve({ok:true, json:() => Promise.resolve({v:servidor})}); };
let recarregou = 0;
globalThis.location = {reload: () => { recarregou++; }};
globalThis.setInterval = () => 0;
const ouvintes = {};
globalThis.document = {
  hidden: false,
  getElementById: el,
  addEventListener: (ev, fn) => { ouvintes[ev] = fn; },
};
const box = el('ver-nova');
const visivel = () => box.cls.has('on');
const flush = () => new Promise(r => setTimeout(r, 0));
const passar = s => { agora += s * 1000; };

__SCRIPT__

(async () => {
  const erros = [];
  const ok = (c, m) => { if(!c) erros.push(m); };

  // mesma versão: a faixa não aparece
  ouvintes['visibilitychange'](); await flush();
  ok(chamadas === 1, "devia ter perguntado ao servidor");
  ok(!visivel(), "faixa apareceu com a MESMA versão");

  // freio: outra pergunta em menos de 60s não vira requisição
  servidor = "V2"; passar(30);
  ouvintes['visibilitychange'](); await flush();
  ok(chamadas === 1, "perguntou de novo dentro dos 60s: " + chamadas);
  ok(!visivel(), "faixa apareceu sem ter perguntado");

  // passados os 60s, a versão nova acende a faixa
  passar(40);
  ouvintes['visibilitychange'](); await flush();
  ok(chamadas === 2, "não perguntou depois dos 60s");
  ok(visivel(), "faixa NÃO apareceu com versão nova");

  // "Depois" some com a faixa e não insiste NESTA versão
  el('vn-depois').onclick(); passar(120);
  ok(!visivel(), "'Depois' não escondeu a faixa");
  ouvintes['visibilitychange'](); await flush();
  ok(!visivel(), "a faixa voltou pra uma versão já dispensada");

  // mas um deploy NOVO volta a avisar
  servidor = "V3"; passar(120);
  ouvintes['visibilitychange'](); await flush();
  ok(visivel(), "faixa não voltou depois de um deploy novo");

  // nada recarrega sozinho: só o botão
  ok(recarregou === 0, "recarregou sem ninguém mandar");
  el('vn-recarregar').onclick();
  ok(recarregou === 1, "o botão Recarregar não recarregou");

  // rede caindo é silêncio, não erro
  globalThis.fetch = () => Promise.reject(new Error("offline"));
  servidor = "V4"; passar(120);
  ouvintes['visibilitychange'](); await flush();

  if(erros.length){ console.log("FALHOU: " + erros.join(" | ")); process.exit(1); }
  console.log("OK");
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_a_faixa_de_versao_roda_e_decide_certo(cliente, tmp_path):
    """`node --check` prova que compila, não que CONCORDA. Aqui o script sai
    renderizado, roda de verdade e responde as quatro perguntas que importam:
    não pisca na mesma versão; não recarrega sozinho (a pessoa pode estar no meio
    de um formulário); "Depois" cala pra ESTA versão e não pra sempre; e rede
    caindo é silêncio."""
    js = [s for s in _scripts(cliente.get("/painel/novidades").text) if "ver-nova" in s]
    assert len(js) == 1
    f = tmp_path / "faixa.mjs"
    f.write_text(_HARNESS.replace("__V0__", vs.VERSAO).replace("__SCRIPT__", js[0]),
                 encoding="utf-8")
    p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "OK" in p.stdout


def _cookie_de_sessao(dados: dict) -> str:
    """Um cookie de sessão VÁLIDO pro app real — o mesmo formato do
    SessionMiddleware (json → base64 → assinado)."""
    import base64
    import json

    import itsdangerous
    bruto = base64.b64encode(json.dumps(dados).encode())
    segredo = os.environ.get("PORTAL_SECRET", "troque-isto-em-producao")
    return itsdangerous.TimestampSigner(str(segredo)).sign(bruto).decode()


def test_as_rotas_existem_no_app_que_sobe_e_o_gate_deixa_passar(cliente, monkeypatch):
    """A FIAÇÃO no `web.app` — e o GATE de papel, que os outros testes deste arquivo
    não veem: eles montam um FastAPI só com o router do portal, sem o middleware
    `_gate_permissoes`. Ele é whitelist por rota; uma rota nova nasce BARRADA pra
    todo membro de equipe, e o gestor cairia num 303 pro /painel/servicos com a
    suíte inteira verde.

    (Conferido antes: `app.routes` não serve pra isto — esta versão do FastAPI
    guarda os routers incluídos como `_IncludedRouter` preguiçoso, sem `path`. Só
    a requisição de verdade responde.)"""
    from fastapi.testclient import TestClient

    from web.app import app
    _aviso(cliente.pool, "a", "eventos", tipo="mudanca", titulo="Aviso de eventos")
    c = TestClient(app, raise_server_exceptions=False)

    # registrada? sem sessão a resposta é 401/303 — 404 seria rota inexistente
    assert c.get("/painel/versao").status_code == 401
    assert c.get("/painel/novidades", follow_redirects=False).status_code == 303

    ses = {"conta_id": CONTA_EV, "papel": "gestor", "membro_id": 7}
    c.cookies.set("session", _cookie_de_sessao(ses))
    r = c.get("/painel/novidades", follow_redirects=False)
    assert r.status_code == 200, f"o gate barrou o gestor ({r.status_code})"
    assert "Aviso de eventos" in r.text
    assert c.get("/painel/versao").json() == {"v": vs.VERSAO}

    # e o vendedor continua fora — pelo gate, antes mesmo da rota
    c.cookies.set("session", _cookie_de_sessao(
        {"conta_id": CONTA_EV, "papel": "vendedor", "membro_id": 8}))
    r = c.get("/painel/novidades", follow_redirects=False)
    assert r.status_code == 303 and "Aviso de eventos" not in r.text
    # mas a faixa de versão é dele também
    assert c.get("/painel/versao").json() == {"v": vs.VERSAO}
