"""Venda de estandes numerados de feira (finance/evento_stands.py).

A regra central é bloqueia_em='pagamento' (modo da Outlet Chic, conta 40):
o estande fica LIVRE e disputável por qualquer interessado até alguém enviar
o comprovante do sinal — é essa chegada que trava (livre -> pre_reservado,
com prazo). O dono confirma manualmente (pre_reservado -> vendido); se
ninguém confirmar dentro do prazo, o job de expiração libera sozinho.

Clone dos testes de tests/test_agenda_pre_reserva.py, por estande em vez de
por data. Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import evento_stands as es


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)  # contas, membros...
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        # orcamentos ANTES de tudo: 075 (prospeccao) já nasce com FK pra ela.
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, conta_id bigint, cliente text, empresa text,
            segmento text, status text default 'enviado',
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            evento_agenda_id bigint, criado_por bigint,
            criado_em timestamptz default now(), atualizado_em timestamptz default now())""")
        # prospeccao (FK de evento_stands.prospeccao_id)
        c.execute((base / "075_modulo_prospeccao.sql").read_text(encoding="utf-8"))
        c.execute((base / "053_modulo_pj.sql").read_text(encoding="utf-8"))       # titulos
        c.execute((base / "162_titulo_parcela_do_orcamento.sql").read_text(encoding="utf-8"))
        c.execute((base / "147_orcamento_evento.sql").read_text(encoding="utf-8"))
        c.execute((base / "161_orcamento_sinal.sql").read_text(encoding="utf-8"))
        c.execute((base / "448_evento_stands.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _isola(pool):
    with pool.connection() as c:
        c.execute("truncate table evento_stands, evento_stands_config, orcamentos, "
                  "titulos restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Outlet Chic') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _criar_stand(pool, conta_id, codigo="G58", **kw):
    with pool.connection() as c:
        sid = c.execute(
            """insert into evento_stands (conta_id, codigo, pavilhao, zona, tamanho,
                   preco_centavos, status)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, codigo, kw.get("pavilhao", "inferior"), kw.get("zona", "Outlet Grifes"),
             kw.get("tamanho", "3x3"), kw.get("preco_centavos", 350000),
             kw.get("status", "livre"))).fetchone()[0]
        c.commit()
    return sid


def _config(pool, conta_id, **kw):
    with pool.connection() as c:
        c.execute(
            """insert into evento_stands_config (conta_id, slug, bloqueia_em, pre_reserva_dias)
               values (%s,%s,%s,%s)""",
            (conta_id, kw.get("slug", "outlet-chic"), kw.get("bloqueia_em", "pagamento"),
             kw.get("pre_reserva_dias", 3)))
        c.commit()


def _status(pool, conta_id, codigo):
    with pool.connection() as c:
        return c.execute(
            "select status, pre_reserva_ate, comprovante_url from evento_stands "
            "where conta_id=%s and codigo=%s", (conta_id, codigo)).fetchone()


# ------------------------------------------------------------ listar / buscar

def test_listar_e_buscar(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", pavilhao="inferior")
    _criar_stand(pool, conta_id, "S103", pavilhao="superior")
    todos = es.listar(pool, conta_id)
    assert [s["codigo"] for s in todos] == ["G58", "S103"]
    livres = es.listar(pool, conta_id, status="livre")
    assert len(livres) == 2
    assert es.buscar(pool, conta_id, "G58")["pavilhao"] == "inferior"
    assert es.buscar(pool, conta_id, "NADA") is None


def test_config_por_conta_e_por_slug(pool, conta_id):
    assert es.obter_config(pool, conta_id) is None  # opt-in: sem linha, sem feature
    _config(pool, conta_id, slug="outlet-chic")
    cfg = es.obter_config(pool, conta_id)
    assert cfg["slug"] == "outlet-chic" and cfg["bloqueia_em"] == "pagamento"
    assert es.buscar_config_por_slug(pool, "outlet-chic")["conta_id"] == conta_id
    assert es.buscar_config_por_slug(pool, "nao-existe") is None


# ------------------------------------------------------- registrar_comprovante

def test_comprovante_trava_estande_livre(pool, conta_id):
    """O CORAÇÃO do modo 'pagamento': o comprovante chegando é o que trava."""
    _config(pool, conta_id, pre_reserva_dias=3)
    _criar_stand(pool, conta_id, "G58")
    r = es.registrar_comprovante(pool, conta_id, "G58", "comprovantes/40/g58.jpg")
    assert r["ok"] and r["stand"]["status"] == "pre_reservado"
    st, prazo, url = _status(pool, conta_id, "G58")
    assert st == "pre_reservado" and url == "comprovantes/40/g58.jpg"
    assert abs((prazo - (es._ag.agora_brt() + timedelta(days=3))).total_seconds()) < 5


def test_comprovante_sem_config_usa_prazo_padrao(pool, conta_id):
    """Conta que ainda não configurou pre_reserva_dias (ou não tem config
    nenhuma) cai no piso de 3 dias — nunca quebra por falta de config."""
    _criar_stand(pool, conta_id, "G58")
    r = es.registrar_comprovante(pool, conta_id, "G58", "x.jpg")
    assert r["ok"]
    _, prazo, _ = _status(pool, conta_id, "G58")
    assert abs((prazo - (es._ag.agora_brt() + timedelta(days=es.PRE_RESERVA_DIAS_PADRAO))
               ).total_seconds()) < 5


def test_comprovante_em_estande_ja_vendido_falha(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", status="vendido")
    r = es.registrar_comprovante(pool, conta_id, "G58", "x.jpg")
    assert not r["ok"] and "vendido" in r["erro"].lower()
    assert _status(pool, conta_id, "G58")[0] == "vendido"   # nada mudou


def test_comprovante_em_estande_inexistente(pool, conta_id):
    r = es.registrar_comprovante(pool, conta_id, "NAOEXISTE", "x.jpg")
    assert not r["ok"] and "não encontrado" in r["erro"]


def test_reenvio_em_pre_reservado_substitui_arquivo_mas_mantem_prazo(pool, conta_id):
    _criar_stand(pool, conta_id, "G58")
    r1 = es.registrar_comprovante(pool, conta_id, "G58", "primeiro.jpg")
    prazo1 = r1["stand"]["pre_reserva_ate"]
    r2 = es.registrar_comprovante(pool, conta_id, "G58", "segundo.jpg")
    assert r2["ok"] and r2["stand"]["comprovante_url"] == "segundo.jpg"
    assert r2["stand"]["pre_reserva_ate"] == prazo1   # não esticou a reserva


def test_prospeccao_id_liga_o_lead(pool, conta_id):
    with pool.connection() as c:
        pid = c.execute("insert into prospeccao (conta_id, empresa) values (%s,%s) returning id",
                        (conta_id, "Fulano")).fetchone()[0]
        c.commit()
    _criar_stand(pool, conta_id, "G58")
    r = es.registrar_comprovante(pool, conta_id, "G58", "x.jpg", prospeccao_id=pid)
    assert r["stand"]["prospeccao_id"] == pid


# --------------------------------------------------- subir_e_registrar_comprovante

def test_subir_e_registrar_sobe_e_trava(pool, conta_id):
    """Cano único (upload + registrar) usado pela página pública e pela
    ferramenta do WhatsApp — aqui testado com um `subir` fake (sem bater no
    Storage de verdade)."""
    _criar_stand(pool, conta_id, "G58")
    subidos = []

    def _subir_fake(caminho, conteudo, content_type):
        subidos.append((caminho, content_type))
        return caminho

    r = es.subir_e_registrar_comprovante(pool, conta_id, "G58", b"conteudo-fake",
                                         "image/jpeg", subir=_subir_fake)
    assert r["ok"] and r["stand"]["status"] == "pre_reservado"
    assert len(subidos) == 1
    caminho, ct = subidos[0]
    assert caminho.startswith(f"stands/{conta_id}/G58-") and caminho.endswith(".jpg")
    assert ct == "image/jpeg"
    assert r["stand"]["comprovante_url"] == caminho


def test_subir_e_registrar_rejeita_tipo_invalido(pool, conta_id):
    _criar_stand(pool, conta_id, "G58")
    chamou = []
    r = es.subir_e_registrar_comprovante(
        pool, conta_id, "G58", b"x", "text/plain",
        subir=lambda *a: chamou.append(a))
    assert not r["ok"] and chamou == []          # nunca chegou a subir
    assert _status(pool, conta_id, "G58")[0] == "livre"   # nada mudou no estande


def test_subir_e_registrar_arquivo_vazio(pool, conta_id):
    _criar_stand(pool, conta_id, "G58")
    r = es.subir_e_registrar_comprovante(pool, conta_id, "G58", b"", "image/jpeg",
                                         subir=lambda *a: (_ for _ in ()).throw(
                                             AssertionError("não devia subir vazio")))
    assert not r["ok"]


# ------------------------------------------------------------ confirmar_pagamento

def test_confirmar_pagamento_sem_orcamento(pool, conta_id):
    """Sem orçamento vinculado: só troca o status, sem lançamento financeiro —
    a reserva pode ter nascido direto na página, sem passar por proposta."""
    _criar_stand(pool, conta_id, "G58", status="pre_reservado")
    r = es.confirmar_pagamento(pool, conta_id, "G58", membro_id=7)
    assert r["ok"] and r["stand"]["status"] == "vendido"
    assert r["financeiro"] is None
    assert _status(pool, conta_id, "G58")[0] == "vendido"


def test_confirmar_pagamento_gera_titulo_via_fechar_orcamento(pool, conta_id):
    """Com orçamento vinculado, reaproveita finance.vendas.fechar_orcamento —
    mesmo motor de título a receber que todo orçamento de evento usa."""
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, primeiro_ano_centavos,
                   status, modo) values (%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, "", "Fulano da Silva", 350000, "enviado", "evento")).fetchone()[0]
        c.commit()
    _criar_stand(pool, conta_id, "G58", status="pre_reservado", preco_centavos=350000)
    with pool.connection() as c:
        c.execute("update evento_stands set orcamento_id=%s where conta_id=%s and codigo=%s",
                  (oid, conta_id, "G58"))
        c.commit()
    r = es.confirmar_pagamento(pool, conta_id, "G58")
    assert r["ok"] and r["stand"]["status"] == "vendido"
    assert r["financeiro"]["ok"] is True
    with pool.connection() as c:
        titulos = c.execute("select valor_centavos, contraparte from titulos where conta_id=%s",
                            (conta_id,)).fetchall()
    assert len(titulos) == 1 and titulos[0][0] == 350000
    assert "Fulano da Silva" in titulos[0][1]


def test_confirmar_pagamento_idempotente(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", status="vendido")
    r = es.confirmar_pagamento(pool, conta_id, "G58")
    assert r["ok"] and r.get("ja_confirmado") is True


def test_confirmar_pagamento_em_estande_livre_falha(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", status="livre")
    r = es.confirmar_pagamento(pool, conta_id, "G58")
    assert not r["ok"]
    assert _status(pool, conta_id, "G58")[0] == "livre"


# ------------------------------------------------------------------- liberar

def test_liberar_devolve_pra_livre_e_limpa_tudo(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", status="pre_reservado")
    with pool.connection() as c:
        c.execute("update evento_stands set comprovante_url='x.jpg', "
                  "pre_reserva_ate=now() + interval '2 days' where conta_id=%s and codigo=%s",
                  (conta_id, "G58"))
        c.commit()
    assert es.liberar(pool, conta_id, "G58") is True
    st, prazo, url = _status(pool, conta_id, "G58")
    assert st == "livre" and prazo is None and url is None


def test_liberar_estande_ja_livre_nao_faz_nada(pool, conta_id):
    _criar_stand(pool, conta_id, "G58", status="livre")
    assert es.liberar(pool, conta_id, "G58") is False


# ------------------------------------------------------------ expirar_pre_reservas

def test_expira_pre_reserva_vencida(pool, conta_id):
    agora = es._ag.agora_brt()
    _criar_stand(pool, conta_id, "G58", status="pre_reservado")
    with pool.connection() as c:
        c.execute("update evento_stands set pre_reserva_ate=%s, comprovante_url='x.jpg' "
                  "where conta_id=%s and codigo=%s",
                  (agora - timedelta(hours=1), conta_id, "G58"))
        c.commit()
    expirados = es.expirar_pre_reservas(pool, agora)
    assert len(expirados) == 1 and expirados[0]["codigo"] == "G58"
    st, prazo, url = _status(pool, conta_id, "G58")
    # volta a livre, mas o comprovante antigo fica rastreável (ver docstring)
    assert st == "livre" and prazo is None and url == "x.jpg"


def test_nao_expira_pre_reserva_dentro_do_prazo(pool, conta_id):
    agora = es._ag.agora_brt()
    _criar_stand(pool, conta_id, "G58", status="pre_reservado")
    with pool.connection() as c:
        c.execute("update evento_stands set pre_reserva_ate=%s where conta_id=%s and codigo=%s",
                  (agora + timedelta(days=1), conta_id, "G58"))
        c.commit()
    assert es.expirar_pre_reservas(pool, agora) == []
    assert _status(pool, conta_id, "G58")[0] == "pre_reservado"


def test_nao_mexe_em_livre_nem_vendido(pool, conta_id):
    agora = es._ag.agora_brt()
    _criar_stand(pool, conta_id, "G58", status="livre")
    _criar_stand(pool, conta_id, "S103", status="vendido")
    assert es.expirar_pre_reservas(pool, agora) == []
