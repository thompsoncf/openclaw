"""Produto junto do atendimento (finance/clinica_produtos.py e /painel/clinica/produtos).

Reaproveita o banco do test_clinica_pacotes (a semente da Espaço Pelle) e põe por cima
o catálogo/estoque do Zaq (032), a assinatura (384) e a 386. A venda do balcão
(finance/pdv.py, que mexe no livro-caixa) é trocada por uma que só baixa o estoque —
o PDV tem o próprio teste.
"""
from datetime import date, time, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import catalogo as cat
from finance import clinica_agenda as ca
from finance import clinica_assinaturas as cas
from finance import clinica_produtos as cpr
from tests.test_clinica_agenda import BASE, CLINICA
from tests.test_clinica_pacotes import FONE, _finalizar, _paciente, _sessao, _tipo, pool, zap  # noqa: F401

HOJE = date(2026, 9, 25)


@pytest.fixture()
def banco(pool, zap, monkeypatch):  # noqa: F811
    with pool.connection() as c:
        for m in ("032_catalogo_estoque.sql", "384_clinica_assinaturas.sql", "386_clinica_produtos.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("""alter table catalogo_produtos add column if not exists foto_url text,
                                                   add column if not exists descricao_curta text""")
        c.commit()
    from finance import pdv
    zap.vendas = []

    def _venda(pool_, dono_id, itens, **kw):
        for it in itens:
            cat.registrar_movimentacao(pool_, dono_id, it["produto_id"], "saida", it["quantidade"], motivo="venda")
        zap.vendas.append((itens, kw))
        tot = sum(int(it["preco_unit_centavos"] * it["quantidade"]) for it in itens)
        return {"lancamento_id": len(zap.vendas), "total_centavos": tot - kw.get("desconto_centavos", 0)}
    monkeypatch.setattr(pdv, "registrar_venda_balcao", _venda)
    return pool


def _produto(pool, nome="Protetor FPS 60", preco=12000, dias=60, qtd=5, validade=None):
    pid = cat.criar_produto(pool, CLINICA, nome, "unidade", "dermocosmetico", preco)
    with pool.connection() as c:
        assert cpr.salvar_recompra(c, CLINICA, pid, str(dias) if dias else "") is None
        c.commit()
    if qtd:
        assert cpr.entrada(pool, CLINICA, produto_id=pid, quantidade=str(qtd), custo="50",
                           validade=validade.isoformat() if validade else "", membro_id=None, hoje=HOJE) is None
    return pid


def test_lote_que_vence_logo_e_o_mais_antigo_sai_primeiro(banco, zap):  # noqa: F811
    pool = banco
    pid = _produto(pool, validade=HOJE + timedelta(days=40))
    assert cpr.entrada(pool, CLINICA, produto_id=pid, quantidade="5", custo="50",
                       validade=(HOJE + timedelta(days=200)).isoformat(), membro_id=None, hoje=HOJE) is None
    assert "vencido" in cpr.entrada(pool, CLINICA, produto_id=pid, quantidade="1", custo="50",
                                    validade=(HOJE - timedelta(days=1)).isoformat(), membro_id=None, hoje=HOJE)
    with pool.connection() as c:
        v = cpr.perto_de_vencer(c, CLINICA, HOJE)
        assert [(x["nome"], x["quantidade"], x["dias"]) for x in v] == [("Protetor FPS 60", "5", 40)]
    cat.registrar_movimentacao(pool, CLINICA, pid, "saida", 6)          # vendeu 6: o lote de 40 dias foi embora
    with pool.connection() as c:
        assert cpr.perto_de_vencer(c, CLINICA, HOJE) == []
        p = cpr.produtos(c, CLINICA, HOJE)[0]
    assert (p["saldo_txt"], p["validade"], p["recompra_dias"]) == ("4", HOJE + timedelta(days=200), 60)


def test_venda_no_fim_do_atendimento_com_desconto_do_assinante(banco, zap):  # noqa: F811
    pool = banco
    pid = _produto(pool)
    with pool.connection() as c:
        lead, _conv = _paciente(c)
        pl, erro = cas.salvar_plano(c, CLINICA, plano_id=None, nome="Pele em dia", preco="149", dia="10",
                                    servico_id="", sessoes="0", desconto_procedimento="0", desconto_produto="10",
                                    prioridade=False)
        c.commit()
    assert cas.assinar(pool, CLINICA, plano_id=pl, lead=lead, paciente="", fone="", inicio=HOJE.isoformat(),
                       membro_id=None, hoje=HOJE)[1] is None
    with pool.connection() as c:
        eid = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        _finalizar(c, eid)
    r, erro = cpr.vender(pool, CLINICA, evento_id=eid, itens=[(str(pid), "2")], pagamento="pix", membro_id=51,
                         hoje=HOJE)
    assert erro is None and r["desconto"] == 2400 and r["plano"] == "Pele em dia"
    assert zap.vendas[0][1]["cliente_nome"] == "Lúcia Ferreira" and zap.vendas[0][1]["desconto_centavos"] == 2400
    with pool.connection() as c:
        vs = cpr.vendas_do_evento(c, CLINICA, eid)
        assert [(v["nome"], v["qtd"], v["valor"]) for v in vs] == [("Protetor FPS 60", "2", "R$ 216")]
        assert vs[0]["recompra_em"] == HOJE + timedelta(days=120)       # 2 × 60 dias
        assert cpr.produtos(c, CLINICA, HOJE)[0]["saldo_txt"] == "3"
    # mais do que tem: o balcão de verdade recusa; aqui a regra é do produto sem preço e da forma
    assert cpr.vender(pool, CLINICA, evento_id=eid, itens=[(str(pid), "1")], pagamento="bitcoin",
                      membro_id=51, hoje=HOJE)[1] == "Escolha a forma de pagamento."
    sem_preco = _produto(pool, nome="Amostra", preco=0, qtd=0)
    assert "sem preço" in cpr.vender(pool, CLINICA, evento_id=eid, itens=[(str(sem_preco), "1")],
                                     pagamento="pix", membro_id=51, hoje=HOJE)[1]


def test_lembrete_da_reposicao_sem_dizer_o_produto_uma_vez_so(banco, zap):  # noqa: F811
    pool = banco
    pid = _produto(pool)
    with pool.connection() as c:
        lead, conv = _paciente(c)
    assert cpr.vender(pool, CLINICA, lead=lead, itens=[(str(pid), "1")], pagamento="especie", membro_id=51,
                      hoje=HOJE)[1] is None
    with pool.connection() as c:
        c.execute("update clinica_produto_vendas set criado_em = %s", (ca.utc(HOJE, time(10)),))
        c.commit()
        vence = HOJE + timedelta(days=60)                              # 24/11, terça
        assert cpr.lembrar(c, CLINICA, ca.utc(vence - timedelta(days=1), time(9))) == 0   # ainda não chegou
        assert cpr.lembrar(c, CLINICA, ca.utc(vence, time(9))) == 1
        assert cpr.lembrar(c, CLINICA, ca.utc(vence + timedelta(days=1), time(9))) == 0   # uma vez só
        tipo = c.execute("select tipo from clinica_lembretes where conta_id=%s", (CLINICA,)).fetchall()
    assert tipo == [("recompra",)]
    texto = zap.saiu[-1][1]
    assert "repor o produto" in texto and "Protetor" not in texto and texto.startswith("Oi, Lúcia!")


def test_comprou_de_novo_fecha_a_reposicao(banco, zap):  # noqa: F811
    pool = banco
    pid = _produto(pool)
    with pool.connection() as c:
        lead, _conv = _paciente(c)
    assert cpr.vender(pool, CLINICA, lead=lead, itens=[(str(pid), "1")], pagamento="pix", membro_id=51,
                      hoje=HOJE)[1] is None
    # o clique duplo (a mesma venda de novo em segundos) é recusado, e o estoque não baixa duas vezes
    assert "acabou de ser vendido" in cpr.vender(pool, CLINICA, lead=lead, itens=[(str(pid), "1")],
                                                 pagamento="pix", membro_id=51, hoje=HOJE)[1]
    assert len(zap.vendas) == 1
    with pool.connection() as c:
        c.execute("update clinica_produto_vendas set criado_em = now() - interval '50 days'")
        c.commit()
    assert cpr.vender(pool, CLINICA, lead=lead, itens=[(str(pid), "1")], pagamento="pix", membro_id=51,
                      hoje=HOJE + timedelta(days=50))[1] is None
    with pool.connection() as c:
        est = [r[0] for r in c.execute("select recompra_estado from clinica_produto_vendas order by id").fetchall()]
        reps = cpr.recompras(c, CLINICA, HOJE + timedelta(days=100))
    assert est == ["comprou", "aguardando"] and [r["recompra_em"] for r in reps] == [HOJE + timedelta(days=110)]


def test_entradas_sem_lote_contam_na_estimativa_e_custo_vazio_usa_o_medio(banco, zap):  # noqa: F811
    pool = banco
    pid = _produto(pool, validade=HOJE + timedelta(days=20), qtd=10)
    cat.registrar_movimentacao(pool, CLINICA, pid, "saida", 10)          # o lote de 20 dias saiu todo
    cat.registrar_movimentacao(pool, CLINICA, pid, "entrada", 20, 5000)  # entrada pela tela genérica, sem lote
    with pool.connection() as c:
        assert cpr.perto_de_vencer(c, CLINICA, HOJE) == []               # não ressuscita o lote que saiu
    assert cpr.entrada(pool, CLINICA, produto_id=pid, quantidade="NaN", custo="", validade="", membro_id=None,
                       hoje=HOJE) == "Informe o produto e a quantidade."
    assert cpr.entrada(pool, CLINICA, produto_id=pid, quantidade="2", custo="", validade="", membro_id=None,
                       hoje=HOJE) is None
    with pool.connection() as c:
        assert c.execute("select custo_medio_centavos from catalogo_produtos where id=%s", (pid,)).fetchone()[0] == 5000
    novo = _produto(pool, nome="Sérum", qtd=0)
    assert "primeira entrada" in cpr.entrada(pool, CLINICA, produto_id=novo, quantidade="1", custo="",
                                             validade="", membro_id=None, hoje=HOJE)


def test_venda_so_no_atendimento_e_dois_produtos_um_lembrete(banco, zap):  # noqa: F811
    pool = banco
    p1, p2 = _produto(pool), _produto(pool, nome="Hidratante", dias=60)
    with pool.connection() as c:
        lead, conv = _paciente(c)
        eid = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        c.commit()
    assert "presente" in cpr.vender(pool, CLINICA, evento_id=eid, itens=[(str(p1), "1")], pagamento="pix",
                                    membro_id=51, hoje=HOJE)[1]
    for pid in (p1, p2):
        assert cpr.vender(pool, CLINICA, lead=lead, itens=[(str(pid), "1")], pagamento="pix", membro_id=51,
                          hoje=HOJE)[1] is None
    with pool.connection() as c:
        c.execute("update clinica_produto_vendas set criado_em = %s", (ca.utc(HOJE, time(10)),))
        c.commit()
        vence = HOJE + timedelta(days=60)
        assert cpr.lembrar(c, CLINICA, ca.utc(vence, time(9))) == 1
        assert cpr.lembrar(c, CLINICA, ca.utc(vence + timedelta(days=1), time(9))) == 0   # o outro foi junto
        assert {r[0] for r in c.execute("select recompra_estado from clinica_produto_vendas").fetchall()} == {"lembrado"}
        # a resposta do paciente é da recepção; depois que alguém responde, não é mais
        assert cpr.respondeu_recompra(c, CLINICA, conv)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','out','humano','Oi! Separei aqui')""", (conv,))
        c.commit()
        assert not cpr.respondeu_recompra(c, CLINICA, conv)


@pytest.fixture()
def cli(banco, monkeypatch):  # noqa: F811
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_produtos as pw
    monkeypatch.setattr(pw, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pw.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    return TestClient(app, follow_redirects=False)


def test_tela_recepcao_vende_e_so_gerencia_muda_duracao(cli, banco):  # noqa: F811
    pid = _produto(banco)
    with banco.connection() as c:
        lead, _conv = _paciente(c)
    cli.get("/_papel/vendedor")
    assert "Tirar" not in cli.post(f"/painel/clinica/produtos/{pid}/duracao", data={"dias": "30"}).text
    with banco.connection() as c:
        assert cpr.produtos(c, CLINICA, HOJE)[0]["recompra_dias"] == 60
    html = cli.get(f"/painel/clinica/produtos?lead={lead}&produto={pid}").text
    assert "Vender para Lúcia Ferreira" in html and "Vendido no mês" not in html and 'name="dias"' not in html
    r = cli.post("/painel/clinica/produtos/vender", data={"lead": str(lead), "produto_id": str(pid),
                                                          "quantidade": "1", "pagamento": "pix"})
    assert r.headers["location"].endswith("aviso=vendido")
    cli.get("/_papel/dono")
    cli.post(f"/painel/clinica/produtos/{pid}/duracao", data={"dias": "30"})
    with banco.connection() as c:
        assert cpr.produtos(c, CLINICA, HOJE)[0]["recompra_dias"] == 30
    assert "Vendido no mês" in cli.get("/painel/clinica/produtos").text
