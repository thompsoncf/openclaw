"""Webhook de deploys do Render (core/render_eventos + POST /webhook/render).

O que estes testes travam, em ordem de gravidade:

1. ASSINATURA. A rota e' publica: quem descobrir a URL pode POSTar. Se a
   verificacao HMAC afrouxar, qualquer um forja "deploy falhou" e dispara
   alerta no Telegram do dono — ou pior, envenena o historico. Os casos cobrem
   corpo adulterado, segredo errado, replay de entrega velha e header faltando.

2. IDEMPOTENCIA. O Render REENTREGA o webhook quando nao recebe 200 a tempo. Sem
   a trava por `webhook-id`, um deploy lento viraria varias linhas e varios
   alertas do MESMO evento.

3. NAO CHUTAR. Sem RENDER_API_KEY o enriquecimento nao acontece. O evento ainda
   precisa ser gravado (degradar, nao perder), mas `sucesso` tem que ficar NULL
   em vez de virar "falhou" — senao todo deploy geraria alerta falso.

O assinador daqui (`_assinar`) e' uma reimplementacao de 4 linhas do padrao
Standard Webhooks, de proposito: foi conferida contra a lib oficial
`standardwebhooks` durante o desenvolvimento, e mantê-la local evita somar uma
dependencia so' pra teste.

Roda com um banco de TESTE separado (nunca producao) — ver tests/conftest.py:
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from core import render_eventos as re_

_MIGRACOES = ("154_render_evento.sql",)

# Segredo no formato que o Render entrega: prefixo whsec_ + chave em base64.
SEGREDO = "whsec_" + base64.b64encode(b"segredo-de-teste-do-render-0123").decode()


@pytest.fixture(scope="module")
def pool():
    # TEST_DATABASE_URL e' garantida pela trava do conftest (pytest_configure).
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    """Tabela vazia por teste — isola os casos sem depender da ordem."""
    with pool.connection() as c:
        c.execute("truncate render_evento")
        c.commit()
    return pool


def _assinar(wid: str, ts: int, corpo: bytes, segredo: str = SEGREDO) -> str:
    """Assina no padrao Standard Webhooks: HMAC-SHA256 de "id.ts.corpo"."""
    chave = base64.b64decode(segredo.removeprefix("whsec_"))
    assinado = b"%s.%s." % (wid.encode(), str(ts).encode()) + corpo
    return "v1," + base64.b64encode(
        hmac.new(chave, assinado, hashlib.sha256).digest()).decode()


def _corpo(evento="evt-1", servico="srv-xyz", tipo="deploy_ended") -> bytes:
    return json.dumps({
        "type": tipo, "timestamp": "2026-08-15T17:00:00Z",
        "data": {"id": evento, "serviceId": servico},
    }).encode()


def _headers(wid: str, corpo: bytes, ts: int | None = None) -> dict:
    ts = ts if ts is not None else int(time.time())
    return {"webhook-id": wid, "webhook-timestamp": str(ts),
            "webhook-signature": _assinar(wid, ts, corpo)}


# ---------------------------------------------------------------- assinatura

def test_aceita_assinatura_legitima():
    corpo = _corpo()
    assert re_.verificar_assinatura(corpo, _headers("msg_1", corpo), SEGREDO) is True


def test_recusa_corpo_adulterado():
    """Assina um corpo e entrega outro: e' a forja mais obvia."""
    corpo = _corpo()
    h = _headers("msg_1", corpo)
    assert re_.verificar_assinatura(corpo.replace(b"deploy_ended", b"x"), h, SEGREDO) is False


def test_recusa_segredo_errado():
    corpo = _corpo()
    outro = "whsec_" + base64.b64encode(b"segredo-ERRADO-0000000000000000").decode()
    ts = int(time.time())
    h = {"webhook-id": "msg_1", "webhook-timestamp": str(ts),
         "webhook-signature": _assinar("msg_1", ts, corpo, outro)}
    assert re_.verificar_assinatura(corpo, h, SEGREDO) is False


def test_recusa_replay_de_entrega_velha():
    """Assinatura valida, mas de uma hora atras: barra reenvio capturado."""
    corpo = _corpo()
    velho = int(time.time()) - 3600
    assert re_.verificar_assinatura(corpo, _headers("msg_1", corpo, velho), SEGREDO) is False


def test_recusa_webhook_id_trocado():
    """O id entra no HMAC; trocar ele invalida — impede reaproveitar assinatura
    pra furar a trava de idempotencia."""
    corpo = _corpo()
    h = _headers("msg_1", corpo) | {"webhook-id": "msg_OUTRO"}
    assert re_.verificar_assinatura(corpo, h, SEGREDO) is False


def test_recusa_sem_headers():
    assert re_.verificar_assinatura(_corpo(), {}, SEGREDO) is False


def test_recusa_sem_segredo_configurado():
    """Sem segredo NAO se aceita corpo nenhum (falha fechada, nao aberta)."""
    corpo = _corpo()
    assert re_.verificar_assinatura(corpo, _headers("msg_1", corpo), "") is False


def test_aceita_header_com_varias_assinaturas():
    """O padrao permite varias assinaturas (rotacao de segredo); basta uma bater."""
    corpo = _corpo()
    h = _headers("msg_1", corpo)
    h["webhook-signature"] = "v1,naovaleisso " + h["webhook-signature"]
    assert re_.verificar_assinatura(corpo, h, SEGREDO) is True


def test_recusa_timestamp_nao_numerico():
    corpo = _corpo()
    h = _headers("msg_1", corpo) | {"webhook-timestamp": "ontem"}
    assert re_.verificar_assinatura(corpo, h, SEGREDO) is False


# -------------------------------------------------------------- classificacao

@pytest.mark.parametrize("texto,num,esperado", [
    ("live", 2, True),
    ("build_failed", 4, False),
    ("update_failed", 5, False),
    ("pre_deploy_failed", 6, False),
    # vocabulario do proprio webhook (data.status), mais grosso que o do deploy
    ("succeeded", None, True),
    ("failed", None, False),
    # cancelado por gente nao e' falha: nao pode acordar o dono de madrugada
    ("canceled", 5, None),
    # sem texto (API fora de alcance), cai no numero do evento
    ("", 2, True),
    ("", 4, False),
    # sem texto e sem numero: NAO chuta
    ("", None, None),
])
def test_classificacao_de_status(texto, num, esperado):
    assert re_._classificar(texto, num) is esperado


# ------------------------------------------------------------------- fluxo

@pytest.fixture()
def render_api_falsa(monkeypatch):
    """Simula a API do Render. Devolve o dict de cenario, que o teste ajusta."""
    cenario = {"status": "live", "status_num": 2, "alertas": []}
    monkeypatch.setattr(re_, "_detalhes_evento", lambda eid: {
        "id": eid, "details": {"deployId": "dep-111", "status": cenario["status_num"]}})
    monkeypatch.setattr(re_, "_servico", lambda sid: {
        "id": sid, "name": "openclaw-web-bcu3", "ownerId": "own-1"})
    monkeypatch.setattr(re_, "_deploy", lambda sid, did: {
        "id": did, "status": cenario["status"],
        "commit": {"id": "abc123def456", "message": "Ajusta o funil\n\ncorpo"}})
    monkeypatch.setattr(re_, "_log_da_falha", lambda sid, own: "linha 1\nERRO fatal")
    monkeypatch.setattr(re_, "_avisar_falha", lambda linha: cenario["alertas"].append(linha))
    return cenario


def test_deploy_com_sucesso_grava_e_nao_alerta(limpo, render_api_falsa):
    r = re_.processar(_corpo(), {"webhook-id": "msg_ok"}, pool=limpo)
    assert r["gravado"] is True
    assert render_api_falsa["alertas"] == []
    with limpo.connection() as c:
        sucesso, log = c.execute(
            "select sucesso, log_trecho from render_evento").fetchone()
    assert sucesso is True
    # log so' na falha: em deploy que deu certo seria peso morto no banco
    assert log is None


def test_reentrega_nao_duplica(limpo, render_api_falsa):
    """O Render remanda quando nao recebe 200 a tempo."""
    re_.processar(_corpo(), {"webhook-id": "msg_ok"}, pool=limpo)
    r = re_.processar(_corpo(), {"webhook-id": "msg_ok"}, pool=limpo)
    assert r["gravado"] is False
    with limpo.connection() as c:
        assert c.execute("select count(*) from render_evento").fetchone()[0] == 1


def test_deploy_quebrado_alerta_com_commit_e_log(limpo, render_api_falsa):
    render_api_falsa.update(status="build_failed", status_num=4)
    re_.processar(_corpo(), {"webhook-id": "msg_falha"}, pool=limpo)

    assert len(render_api_falsa["alertas"]) == 1
    alerta = render_api_falsa["alertas"][0]
    assert alerta["commit_id"] == "abc123def456"
    # o log junto e' o que evita ter que abrir o dashboard pra saber o motivo
    assert "ERRO fatal" in alerta["log_trecho"]

    with limpo.connection() as c:
        status, sucesso = c.execute(
            "select status, sucesso from render_evento").fetchone()
    assert (status, sucesso) == ("build_failed", False)


def test_reentrega_da_falha_nao_realerta(limpo, render_api_falsa):
    render_api_falsa.update(status="build_failed", status_num=4)
    re_.processar(_corpo(), {"webhook-id": "msg_falha"}, pool=limpo)
    re_.processar(_corpo(), {"webhook-id": "msg_falha"}, pool=limpo)
    assert len(render_api_falsa["alertas"]) == 1


def test_sem_api_key_grava_mas_nao_chuta_status(limpo, monkeypatch):
    """Enriquecimento indisponivel: degrada, nao perde e nao inventa falha."""
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    r = re_.processar(_corpo(), {"webhook-id": "msg_cru"}, pool=limpo)
    assert r["gravado"] is True
    with limpo.connection() as c:
        tipo, sucesso, tipo_json = c.execute(
            "select tipo, sucesso, payload->>'type' from render_evento").fetchone()
    assert tipo == "deploy_ended"
    assert sucesso is None          # NULL, nunca False: alerta falso e' pior
    assert tipo_json == "deploy_ended"   # corpo cru preservado pra auditoria


# --------------------------------------------- payload real (entrega de prod)

# Copiado LITERALMENTE de uma entrega do painel do Render (whe-da0b02924b3s...).
# Guardado aqui porque contradiz a definicao de tipos do exemplo oficial: la' o
# `data` so' tem `id` e `serviceId`, e na vida real vem tambem `serviceName` e
# `status`. E' o que permite classificar e nomear o servico sem API key.
_REAL = json.dumps({
    "type": "deploy_ended",
    "timestamp": "2026-08-15T18:24:23.351041589Z",
    "data": {
        "id": "evt-da0atlvhgfqs738esvsg",
        "serviceId": "srv-d8m41j0g4nts7382k16g",
        "serviceName": "openclaw-web-bcu3",
        "status": "succeeded",
    },
}).encode()


def test_payload_real_sem_api_key_ja_nomeia_e_classifica(limpo, monkeypatch):
    """Sem RENDER_API_KEY, o corpo sozinho tem que bastar pro essencial."""
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    assert re_.processar(_REAL, {"webhook-id": "whe-real"}, pool=limpo)["gravado"] is True

    with limpo.connection() as c:
        nome, status, sucesso, quando = c.execute(
            "select servico_nome, status, sucesso, ocorrido_em from render_evento"
        ).fetchone()
    assert nome == "openclaw-web-bcu3"
    assert status == "succeeded"
    assert sucesso is True          # nao mais NULL: o corpo ja' disse
    # timestamp do Render vem com 9 casas (nanos); o Postgres guarda 6.
    # Se isso virar erro em vez de truncar, TODO evento deixa de ser gravado.
    assert quando is not None and quando.microsecond == 351042


def test_payload_real_de_falha_alerta_sem_api_key(limpo, monkeypatch):
    """O alerta nao pode depender da API: falha e' justamente quando ela pode
    estar indisponivel."""
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    alertas = []
    monkeypatch.setattr(re_, "_avisar_falha", lambda linha: alertas.append(linha))

    corpo = _REAL.replace(b'"succeeded"', b'"failed"')
    re_.processar(corpo, {"webhook-id": "whe-real-falha"}, pool=limpo)

    assert len(alertas) == 1
    assert alertas[0]["servico_nome"] == "openclaw-web-bcu3"


def test_api_refina_o_status_grosso_do_webhook(limpo, render_api_falsa):
    """Com API, "failed" vira "build_failed" — diz em que etapa quebrou."""
    render_api_falsa.update(status="build_failed", status_num=4)
    corpo = _REAL.replace(b'"succeeded"', b'"failed"')
    re_.processar(corpo, {"webhook-id": "whe-refina"}, pool=limpo)

    with limpo.connection() as c:
        status = c.execute("select status from render_evento").fetchone()[0]
    assert status == "build_failed"


def test_corpo_invalido_nao_estoura(limpo):
    """Webhook que estoura viraria reentrega em loop no Render."""
    r = re_.processar(b"nao sou json", {"webhook-id": "msg_lixo"}, pool=limpo)
    assert r["ok"] is False


def test_evento_sem_servico_ainda_grava(limpo, monkeypatch):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    corpo = json.dumps({"type": "server_failed", "data": {}}).encode()
    assert re_.processar(corpo, {"webhook-id": "msg_sem_srv"}, pool=limpo)["gravado"] is True


# --------------------------------------------------------------- historico

def test_historico_filtra_e_ordena(limpo, render_api_falsa, monkeypatch):
    re_.processar(_corpo(evento="e1"), {"webhook-id": "m1"}, pool=limpo)
    render_api_falsa.update(status="build_failed", status_num=4)
    re_.processar(_corpo(evento="e2"), {"webhook-id": "m2"}, pool=limpo)

    todos = re_.historico(pool=limpo, limite=10)
    assert len(todos) == 2
    assert todos[0]["status"] == "build_failed"      # mais novo primeiro

    assert len(re_.historico(pool=limpo, so_falhas=True)) == 1
    assert len(re_.historico(pool=limpo, servico="openclaw-web-bcu3")) == 2
    assert len(re_.historico(pool=limpo, servico="srv-xyz")) == 2
    assert re_.historico(pool=limpo, servico="nao-existe") == []


def test_historico_limita_teto(limpo, render_api_falsa):
    """Teto de 200 protege contra um `--limit 999999` puxar a tabela inteira."""
    re_.processar(_corpo(), {"webhook-id": "m1"}, pool=limpo)
    assert len(re_.historico(pool=limpo, limite=10**9)) == 1


# ------------------------------------------------------------------- rota

@pytest.fixture()
def cliente(limpo, monkeypatch):
    """TestClient com o get_pool apontado pro banco de TESTE.

    A rota chama `processar` sem pool, entao ele cai no `get_pool()` — que le
    DATABASE_URL, propositalmente ausente nos testes (trava do conftest). Aqui
    a gente redireciona pro pool de teste em vez de definir DATABASE_URL, pra
    nao afrouxar essa trava.
    """
    from fastapi.testclient import TestClient
    import db.conexao
    import web.app
    monkeypatch.setenv("RENDER_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    monkeypatch.setattr(db.conexao, "get_pool", lambda: limpo)
    return TestClient(web.app.app)


def _linhas(pool) -> int:
    with pool.connection() as c:
        return c.execute("select count(*) from render_evento").fetchone()[0]


def test_rota_grava_o_que_veio_assinado(cliente, limpo):
    corpo = _corpo()
    r = cliente.post("/webhook/render", content=corpo, headers=_headers("msg_r1", corpo))
    assert r.status_code == 200
    assert _linhas(limpo) == 1


def test_rota_recusa_assinatura_forjada(cliente, limpo):
    """Vale por todos: a rota e' publica, entao o portao e' a unica defesa."""
    r = cliente.post("/webhook/render", content=_corpo(),
                     headers={"webhook-id": "msg_r2",
                              "webhook-timestamp": str(int(time.time())),
                              "webhook-signature": "v1,forjada"})
    assert r.status_code == 400
    assert _linhas(limpo) == 0


def test_rota_recusa_corpo_trocado_apos_assinar(cliente, limpo):
    corpo = _corpo()
    h = _headers("msg_r3", corpo)
    r = cliente.post("/webhook/render", content=corpo.replace(b"srv-xyz", b"srv-OUTRO"),
                     headers=h)
    assert r.status_code == 400
    assert _linhas(limpo) == 0


def test_rota_nao_duplica_reentrega(cliente, limpo):
    corpo = _corpo()
    h = _headers("msg_r4", corpo)
    cliente.post("/webhook/render", content=corpo, headers=h)
    cliente.post("/webhook/render", content=corpo, headers=h)
    assert _linhas(limpo) == 1


def test_rota_inerte_sem_segredo(limpo, monkeypatch):
    """Sem RENDER_WEBHOOK_SECRET: responde 200 e ignora.

    200 (e nao 400) de proposito — enquanto o webhook nao esta' configurado, o
    Render nao deve acumular falha de entrega no painel.
    """
    from fastapi.testclient import TestClient
    import db.conexao
    import web.app
    monkeypatch.delenv("RENDER_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(db.conexao, "get_pool", lambda: limpo)
    r = TestClient(web.app.app).post("/webhook/render", content=_corpo())
    assert r.status_code == 200
    assert _linhas(limpo) == 0
