"""Quando o dinheiro que entrou não fecha com a parcela — pedido 9, 23/09/2026.

A regra do dono, nas palavras dele: "continua devendo quando o valor não fechar,
e quando passar fica de crédito e [abate de outra] parcela" — "sempre pergunta
pro gestor".

O QUE ESTE ARQUIVO FIXA:

  * sem escolha do gestor, NADA muda — diferença não se resolve sozinha;
  * a menos + "continua devendo": a parcela vale o que entrou e nasce a conta do
    restante no MESMO vencimento; a soma do que o cliente deve não muda;
  * a mais + "abater": o crédito sai da parcela escolhida e transborda pras
    seguintes; parcela coberta inteira fica cancelada com o valor intacto;
  * crédito nunca abate conta de OUTRO cliente, nem passa do que falta;
  * tudo numa transação: se a baixa falhar, nenhuma parcela muda;
  * cada valor mexido deixa antes/depois em `titulo_ajustes` (regra 0);
  * o sinal baixado pela Empresa avisa o orçamento — só preenchendo o vazio;
  * o caso real do orçamento nº 23 (R$ 75,00 a mais já baixados) aparece como
    crédito pendente e se resolve com o mesmo abater.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import recebido_diferente as rd

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql",
              "162_titulo_parcela_do_orcamento.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql",
              "323_titulo_ajustes.sql")

CONTA = 651
VENC = date(2026, 9, 21)
PAGO = date(2026, 9, 23)

_PLANO = [{"obs": "Sinal — confirma a reserva da data", "valor_centavos": 234000}] + [
    {"obs": f"Parcela {i}/5", "valor_centavos": 114200} for i in range(1, 6)]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_recebido_diferente"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    from contas import equipe as _eq
    _eq.garantir_tabela(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        c.execute("insert into contas (id, nome, tipo) values (%s,'Prime Eventos','pj') "
                  "on conflict (id) do nothing", (CONTA,))
        c.commit()
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    with p.connection() as c:
        # só o que a baixa do sinal lê do orçamento
        c.execute("""create table if not exists orcamentos (id bigserial primary key,
                       conta_id bigint, parcelas jsonb, sinal_pago_em timestamptz)""")
        c.commit()
    yield p
    p.close()


def _orc(pool, sinal_pago=None):
    with pool.connection() as c:
        oid = c.execute("insert into orcamentos (conta_id, parcelas, sinal_pago_em) "
                        "values (%s,%s::jsonb,%s) returning id",
                        (CONTA, json.dumps(_PLANO), sinal_pago)).fetchone()[0]
        c.commit()
    return oid


def _t(pool, descricao, valor, *, orc=None, idx=None, venc=VENC, contraparte="Maria",
       cliente_id=None, status="aberto", recorrente=False):
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte, valor_centavos,
                                    vencimento, status, orcamento_id, parcela_idx,
                                    cliente_id, recorrente, periodicidade, aprovacao)
               values (%s,'receber',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'autorizado')
               returning id""",
            (CONTA, descricao, contraparte, valor, venc, status, orc, idx, cliente_id,
             recorrente, "mensal" if recorrente else None)).fetchone()[0]
        if status == "pago":
            c.execute("update titulos set pago_em=%s where id=%s", (PAGO, tid))
        c.commit()
    return tid


def _negocio(pool, sinal=234000, contraparte="Maria"):
    """Um orçamento com o sinal e as 5 parcelas em aberto."""
    oid = _orc(pool)
    s = _t(pool, "Evento · Sinal — confirma a reserva da data", sinal, orc=oid, idx=0,
           contraparte=contraparte)
    ps = [_t(pool, f"Evento · Parcela {i}/5", 114200, orc=oid, idx=i,
             venc=VENC + timedelta(days=30 * i), contraparte=contraparte)
          for i in range(1, 6)]
    return oid, s, ps


def _linha(pool, tid):
    with pool.connection() as c:
        r = c.execute("select status, valor_centavos, coalesce(acrescimo_centavos,0), "
                      "lancamento_id, descricao from titulos where id=%s", (tid,)).fetchone()
    return {"status": r[0], "valor": r[1], "acrescimo": r[2], "lanc": r[3], "desc": r[4]}


def _lanc(pool, lid):
    with pool.connection() as c:
        return c.execute("select valor_centavos from lancamentos where id=%s",
                         (lid,)).fetchone()[0]


def _soma(pool, oid):
    """O que o cliente deve no total: pago + aberto, sem o que o crédito cancelou."""
    with pool.connection() as c:
        return c.execute("select coalesce(sum(valor_centavos),0) from titulos "
                         "where orcamento_id=%s and status in ('aberto','pago')",
                         (oid,)).fetchone()[0]


def _ajustes(pool, origem):
    with pool.connection() as c:
        return c.execute("select titulo_id, tipo, valor_antes, valor_depois from titulo_ajustes "
                         "where origem_titulo_id=%s order by id", (origem,)).fetchall()


# ═══════════════════════════════════════════ sem diferença, a baixa de sempre

def test_valor_igual_e_a_baixa_de_sempre(pool):
    _, s, _ = _negocio(pool)
    r = rd.baixar(pool, CONTA, s, 234000, data_pagto=PAGO)
    assert r["ok"]
    l = _linha(pool, s)
    assert l["status"] == "pago" and _lanc(pool, l["lanc"]) == 234000
    assert _ajustes(pool, s) == []


# ═══════════════════════════════════════════ sempre pergunta

@pytest.mark.parametrize("recebido,destino", [(200000, ""), (200000, "abater"),
                                              (241500, ""), (241500, "restante")])
def test_sem_a_escolha_certa_nada_muda(pool, recebido, destino):
    oid, s, ps = _negocio(pool)
    r = rd.baixar(pool, CONTA, s, recebido, destino=destino, alvo_id=ps[0])
    assert r["ok"] is False and "escolha o que fazer" in r["erro"]
    assert _linha(pool, s)["status"] == "aberto" and _linha(pool, s)["valor"] == 234000
    assert _linha(pool, ps[0])["valor"] == 114200
    assert _soma(pool, oid) == 234000 + 5 * 114200


# ═══════════════════════════════════════════ a menos: continua devendo

def test_a_menos_continua_devendo_numa_conta_nova_no_mesmo_vencimento(pool):
    oid, s, _ = _negocio(pool)
    total = _soma(pool, oid)
    r = rd.baixar(pool, CONTA, s, 200000, destino="restante", data_pagto=PAGO)
    assert r["ok"] and r["restante_id"]
    l = _linha(pool, s)
    assert (l["status"], l["valor"]) == ("pago", 200000)
    assert _lanc(pool, l["lanc"]) == 200000          # o caixa recebe o que entrou
    rest = _linha(pool, r["restante_id"])
    assert (rest["status"], rest["valor"]) == ("aberto", 34000)
    assert rest["desc"].endswith(" — restante")
    with pool.connection() as c:
        venc, o, idx = c.execute("select vencimento, orcamento_id, parcela_idx from titulos "
                                 "where id=%s", (r["restante_id"],)).fetchone()
    assert (venc, o, idx) == (VENC, oid, None)
    assert _soma(pool, oid) == total                 # ninguém perdoou nada
    assert _ajustes(pool, s) == [(s, "recebido", 234000, 200000),
                                 (r["restante_id"], "restante", 0, 34000)]


def test_a_menos_por_desconto_usa_o_acrescimo_negativo_de_sempre(pool):
    _, s, _ = _negocio(pool)
    r = rd.baixar(pool, CONTA, s, 230000, destino="desconto", data_pagto=PAGO)
    assert r["ok"] and not r.get("restante_id")
    l = _linha(pool, s)
    assert (l["valor"], l["acrescimo"]) == (234000, -4000)


# ═══════════════════════════════════════════ a mais: crédito que abate

def test_a_mais_abate_da_parcela_escolhida(pool):
    oid, s, ps = _negocio(pool)
    total = _soma(pool, oid)
    r = rd.baixar(pool, CONTA, s, 241500, destino="abater", alvo_id=ps[0], data_pagto=PAGO)
    assert r["ok"]
    assert (_linha(pool, s)["valor"], _lanc(pool, _linha(pool, s)["lanc"])) == (241500, 241500)
    assert _linha(pool, ps[0])["valor"] == 106700      # R$ 1.142,00 − R$ 75,00
    assert [_linha(pool, p)["valor"] for p in ps[1:]] == [114200] * 4
    assert _soma(pool, oid) == total
    assert (ps[0], "abatimento", 114200, 106700) in _ajustes(pool, s)


def test_o_gestor_escolhe_qual_parcela(pool):
    _, s, ps = _negocio(pool)
    rd.baixar(pool, CONTA, s, 241500, destino="abater", alvo_id=ps[4], data_pagto=PAGO)
    assert _linha(pool, ps[4])["valor"] == 106700
    assert _linha(pool, ps[0])["valor"] == 114200


def test_credito_maior_que_a_parcela_transborda_e_cancela_sem_apagar(pool):
    oid, s, ps = _negocio(pool)
    total = _soma(pool, oid)
    # entraram R$ 1.200,00 a mais: cobre a parcela 1 inteira e R$ 57,80 da 2
    r = rd.baixar(pool, CONTA, s, 354000, destino="abater", alvo_id=ps[0], data_pagto=PAGO)
    assert r["ok"]
    p1 = _linha(pool, ps[0])
    assert (p1["status"], p1["valor"]) == ("cancelado", 114200)   # valor combinado intacto
    assert p1["desc"].endswith("quitada com crédito")
    assert _linha(pool, ps[1])["valor"] == 114200 - 5800
    # o que o cliente deve = o que entrou + o que falta, e o cancelado fica fora
    with pool.connection() as c:
        abertas = c.execute("select sum(valor_centavos) from titulos where orcamento_id=%s "
                            "and status='aberto'", (oid,)).fetchone()[0]
    assert 354000 + abertas == total


def test_credito_que_passa_de_tudo_e_recusado_e_nada_muda(pool):
    oid, s, ps = _negocio(pool)
    r = rd.baixar(pool, CONTA, s, 234000 + 5 * 114200 + 1, destino="abater",
                  alvo_id=ps[0], data_pagto=PAGO)
    assert r["ok"] is False and "passa de tudo" in r["erro"]
    assert _linha(pool, s)["status"] == "aberto"
    assert [_linha(pool, p)["valor"] for p in ps] == [114200] * 5
    assert _ajustes(pool, s) == []


def test_credito_nunca_abate_conta_de_outro_cliente(pool):
    _, s, _ = _negocio(pool, contraparte="Maria")
    _, _, alheias = _negocio(pool, contraparte="Josinalva")
    r = rd.baixar(pool, CONTA, s, 241500, destino="abater", alvo_id=alheias[0],
                  data_pagto=PAGO)
    assert r["ok"] is False
    assert _linha(pool, alheias[0])["valor"] == 114200
    assert alheias[0] not in [a["id"] for a in rd.alvos_do_credito(pool, CONTA, s)]


def test_sem_orcamento_o_alvo_e_o_mesmo_nome(pool):
    a = _t(pool, "Contrato nº 115 (2/3)", 50000, contraparte="Rone")
    b = _t(pool, "Contrato nº 115 (3/3)", 50000, contraparte=" rone ",
           venc=VENC + timedelta(days=30))
    _t(pool, "Outro", 50000, contraparte="Rita")
    assert [x["id"] for x in rd.alvos_do_credito(pool, CONTA, a)] == [b]
    assert [x["id"] for x in rd.alvos_por_titulo(pool, CONTA)[a]] == [b]


def test_a_mais_por_juros_usa_o_acrescimo_de_sempre(pool):
    _, s, ps = _negocio(pool)
    r = rd.baixar(pool, CONTA, s, 241500, destino="juros", data_pagto=PAGO)
    assert r["ok"]
    assert (_linha(pool, s)["valor"], _linha(pool, s)["acrescimo"]) == (234000, 7500)
    assert _linha(pool, ps[0])["valor"] == 114200


# ═══════════════════════════════════════════ uma transação só

def test_se_a_baixa_falha_nenhuma_parcela_muda(pool, monkeypatch):
    oid, s, ps = _negocio(pool)
    from finance import livro_caixa

    def explode(*a, **k):
        raise RuntimeError("caixa fora do ar")
    monkeypatch.setattr(livro_caixa.LivroCaixa, "adicionar", explode)
    with pytest.raises(RuntimeError):
        rd.baixar(pool, CONTA, s, 241500, destino="abater", alvo_id=ps[0], data_pagto=PAGO)
    monkeypatch.undo()
    assert _linha(pool, s)["status"] == "aberto" and _linha(pool, s)["valor"] == 234000
    assert _linha(pool, ps[0])["valor"] == 114200
    assert _ajustes(pool, s) == []


# ═══════════════════════════════════════════ a mensalidade que repete

def test_mensalidade_paga_a_menos_repete_com_o_valor_combinado(pool):
    m = _t(pool, "Mensalidade — H Pernas", 89000, contraparte="H Pernas", recorrente=True)
    r = rd.baixar(pool, CONTA, m, 80000, destino="restante", data_pagto=PAGO)
    assert r["ok"] and r["proximo_titulo_id"]
    assert _linha(pool, r["proximo_titulo_id"])["valor"] == 89000
    assert _linha(pool, r["restante_id"])["valor"] == 9000


# ═══════════════════════════════════════════ o sinal avisa o orçamento

def test_sinal_baixado_pela_empresa_avisa_o_orcamento(pool):
    oid, s, ps = _negocio(pool)
    assert rd.baixar(pool, CONTA, s, None, data_pagto=PAGO)["ok"]
    with pool.connection() as c:
        quando = c.execute("select sinal_pago_em from orcamentos where id=%s",
                           (oid,)).fetchone()[0]
    assert quando is not None and quando.date() == PAGO


def test_so_o_sinal_avisa_e_nunca_sobrescreve(pool):
    oid, s, ps = _negocio(pool)
    rd.baixar(pool, CONTA, ps[0], None, data_pagto=PAGO)     # parcela, não sinal
    with pool.connection() as c:
        assert c.execute("select sinal_pago_em from orcamentos where id=%s",
                         (oid,)).fetchone()[0] is None
    from datetime import datetime, timezone
    antes = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    oid2 = _orc(pool, sinal_pago=antes)
    s2 = _t(pool, "Evento · Sinal", 234000, orc=oid2, idx=0)
    rd.baixar(pool, CONTA, s2, None, data_pagto=PAGO)
    with pool.connection() as c:
        assert c.execute("select sinal_pago_em from orcamentos where id=%s",
                         (oid2,)).fetchone()[0] == antes


# ═══════════════════════════════════════════ o crédito que já entrou (nº 23)

def test_o_caso_do_orcamento_23_aparece_e_se_resolve(pool):
    oid = _orc(pool)
    # o dono editou o sinal pra R$ 2.415,00 e deu baixa: a diferença ficou solta
    s = _t(pool, "Evento · Sinal — confirma a reserva da data", 241500, orc=oid, idx=0,
           status="pago", contraparte="Maria Carolina")
    ps = [_t(pool, f"Evento · Parcela {i}/5", 114200, orc=oid, idx=i,
             contraparte="Maria Carolina", venc=VENC + timedelta(days=30 * i))
          for i in range(1, 6)]
    pend = rd.creditos_pendentes(pool, CONTA)[s]
    assert (pend["credito"], pend["combinado"]) == (7500, 234000)
    assert [a["id"] for a in pend["alvos"]] == ps
    r = rd.abater_credito_pendente(pool, CONTA, s, ps[0])
    assert r == {"ok": True, "credito": 7500}
    assert _linha(pool, ps[0])["valor"] == 106700
    assert _linha(pool, s)["valor"] == 241500             # o que está no caixa não muda
    assert s not in rd.creditos_pendentes(pool, CONTA)     # resolvido uma vez só
    assert rd.abater_credito_pendente(pool, CONTA, s, ps[1])["ok"] is False


def test_pago_abaixo_do_plano_nao_vira_credito(pool):
    oid = _orc(pool)
    s = _t(pool, "Evento · Sinal", 200000, orc=oid, idx=0, status="pago")
    _t(pool, "Evento · Parcela 1/5", 114200, orc=oid, idx=1)
    assert s not in rd.creditos_pendentes(pool, CONTA)


def test_ajustes_por_titulo_diz_o_porque(pool):
    _, s, ps = _negocio(pool)
    rd.baixar(pool, CONTA, s, 241500, destino="abater", alvo_id=ps[0], data_pagto=PAGO)
    aj = rd.ajustes_por_titulo(pool, CONTA)
    assert aj[ps[0]] == {"tipo": "abatimento", "antes": 114200, "depois": 106700}


# ═══════════════════════════════════════════ a tela

def _empresa(**kw):
    from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
    import web.portal as pt
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(kw)
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


def _aberto(id_, tipo):
    return {"id": id_, "descricao": f"conta {id_}", "contraparte": "",
            "valor_centavos": 234000, "aprovacao": "autorizado", "tipo": tipo,
            "vencimento": VENC, "atrasado": False, "prazo": "", "cliente_nome": None,
            "cliente_id": None, "criado_nome": None, "aprovado_nome": None,
            "aprovacao_motivo": None, "sem_fornecedor": False, "conciliar": None,
            "cobranca_link_url": None, "periodicidade": None, "valor_variavel": False,
            "proxima": None, "categoria": "", "plano_conta_id": None,
            "centro_custo_id": None}


def _form_da_baixa(html, tid):
    i = html.index(f'action="/painel/empresa/titulo/{tid}/baixa"')
    return html[i:html.index("</form>", i)]


def test_a_baixa_de_receber_pergunta_quanto_entrou_sem_escolha_marcada():
    rec, pag = _aberto(1, "receber"), _aberto(2, "pagar")
    blocos = [{"titulo": "Liberadas", "cor": "ok", "decide": False, "itens": [pag],
               "centavos": 234000, "dica": ""},
              {"titulo": "A receber", "cor": "rec", "decide": False, "itens": [rec],
               "centavos": 234000, "dica": ""}]
    html = _empresa(titulos=[pag, rec], tit_blocos=blocos,
                    ajustes={1: {"tipo": "abatimento", "antes": 311500, "depois": 234000}},
                    alvos_credito={1: [{"id": 9, "descricao": "Parcela <1/5>",
                                        "valor_centavos": 114200, "vencimento": VENC}]})
    f = _form_da_baixa(html, 1)
    assert 'name="recebido"' in f and 'value="2.340,00"' in f
    assert 'value="restante"' in f and 'value="abater"' in f and 'value="juros"' in f
    # nenhuma opção vem marcada ("sempre pergunta pro gestor"). O `r.checked`
    # do onchange do select não conta: ele só marca quando a pessoa escolhe.
    import re
    assert not re.search(r"<input[^>]*\schecked", f)
    assert "Parcela &lt;1/5&gt;" in f
    p = _form_da_baixa(html, 2)
    assert 'name="acrescimo"' in p and 'name="recebido"' not in p
    # e a linha diz por que vale o que vale
    assert "− R$ 775,00 de crédito abatido" in html


def test_credito_pendente_aparece_na_linha_do_baixado():
    pago = {"id": 5, "tipo": "receber", "descricao": "Sinal", "contraparte": "",
            "valor_centavos": 241500, "acrescimo_centavos": 0, "pago_em": PAGO,
            "vencimento": VENC, "lancamento_id": 1}
    html = _empresa(titulos_pagos=[pago], creditos_pendentes={5: {
        "credito": 7500, "combinado": 234000,
        "alvos": [{"id": 9, "descricao": "Parcela 1/5", "valor_centavos": 114200,
                   "vencimento": VENC}]}})
    assert 'action="/painel/empresa/titulo/5/credito"' in html
    assert "Entrou R$ 75,00 a mais que o combinado (R$ 2.340,00)" in html
