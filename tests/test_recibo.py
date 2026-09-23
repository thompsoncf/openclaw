"""O recibo de conta a receber — pedido 1 do dono, texto aprovado em 23/09/2026.

O caso-base é o de verdade: o sinal da Maria Carolina (orçamento nº 23 da Prime),
R$ 2.415,00 recebidos em 23/09, com 5 parcelas de R$ 1.142,00 ainda em aberto.
O texto que o dono aprovou palavra por palavra:

    Recebemos de MARIA CAROLINA DA SILVA COSTA, CPF …, a importância de
    R$ 2.415,00 (dois mil, quatrocentos e quinze reais), referente ao Sinal —
    confirma a reserva da data do orçamento nº 23: Casamento em 24/07/2027,
    locação de espaço. Forma de pagamento: Pix.
    Damos plena quitação desta parcela. Permanecem em aberto R$ 5.710,00, em 5
    parcelas, conforme o contrato.

O QUE ESTE ARQUIVO FIXA:

  * o texto aprovado sai igual, e o "referente a" segue o que a conta vende
    (evento com data; mensalidade com o mês; conta avulsa com a descrição);
  * o CPF entra quando existe (no orçamento OU no cadastro) e some quando não;
  * só conta a RECEBER e já PAGA tem recibo;
  * o número é por empresa e por ano, e gerar de novo NÃO cria outro número —
    reescreve o texto (é assim que o CPF completado entra no papel);
  * o link público escapa o que o cliente e o dono digitaram;
  * "mandar" sai pelo canal da conta e só carimba quando o WhatsApp aceitou.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import recibo as rb

CONTA, OUTRA = 641, 642

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text,
  chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, whatsapp text, email text, telefone text, cnpj text, cpf text,
  endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'fechado',
  modo text default 'evento', evento jsonb, parcelas jsonb, numero int,
  cliente_id bigint, sinal_pago_em timestamptz, itens jsonb);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, endereco text, cep text, cidade text, uf text);
create table contratos (id bigserial primary key, conta_id bigint not null,
  numero int not null, orcamento_id bigint, token text);
create table lancamentos (id bigserial primary key, conta_id bigint, tipo text,
  valor_centavos bigint, descricao text, data date, pagamento text default '',
  forma_pagamento text default '');
create table titulos (id bigserial primary key, conta_id bigint references contas(id),
  tipo text not null, descricao text not null, contraparte text not null default '',
  valor_centavos int not null, vencimento date not null, status text default 'aberto',
  pago_em date, lancamento_id bigint, cliente_id bigint, orcamento_id bigint,
  parcela_idx int, acrescimo_centavos int not null default 0);
create table prospeccao (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  whatsapp text, telefone text);
"""

_PLANO = [{"obs": "Sinal — confirma a reserva da data", "venc": "2026-09-21",
           "forma": "Pix", "valor_centavos": 234000}] + [
          {"obs": f"Parcela {i}/5", "venc": f"2026-{9 + i:02d}-21" if i < 4 else "2027-01-21",
           "forma": "PIX", "valor_centavos": 114200} for i in range(1, 6)]
_EVENTO = {"tipo": "Casamento", "data": "2027-07-24", "contratos": ["Locação de espaço"],
           "convidados": 150}


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_recibo"
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
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((base / "322_recibos.sql").read_text(encoding="utf-8"))
        c.execute("""insert into contas (id, nome, razao_social, nome_fantasia, documento,
                                         cidade, uf)
                     values (%s,'Prime','PRIME EVENTOS','PRIME EVENTOS','52752898000158',
                             'TERESINA','pi'),
                            (%s,'Vizinha','VIZINHA LTDA','',NULL,'',NULL)""",
                  (CONTA, OUTRA))
        c.execute("insert into membros (id, conta_id, nome) values (7, %s, 'Dono')", (CONTA,))
        c.commit()
    yield p
    p.close()


def _orc(pool, *, cpf="11122233344", cliente_id=None, modo="evento", numero=23,
         conta=CONTA, empresa="Maria Carolina da Silva Costa"):
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cpf, modo, evento, parcelas,
                                       numero, cliente_id)
               values (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) returning id""",
            (conta, empresa, cpf, modo, json.dumps(_EVENTO), json.dumps(_PLANO), numero,
             cliente_id)).fetchone()[0]
        c.commit()
    return oid


def _tit(pool, *, tipo="receber", status="pago", descricao, valor, venc=date(2026, 9, 21),
         pago=date(2026, 9, 23), orc=None, idx=None, forma="", cliente_id=None,
         contraparte="Maria Carolina da Silva Costa", acrescimo=0, conta=CONTA):
    with pool.connection() as c:
        lid = None
        if status == "pago":
            lid = c.execute(
                "insert into lancamentos (conta_id, tipo, valor_centavos, descricao, data, "
                "forma_pagamento) values (%s,'receita',%s,%s,%s,%s) returning id",
                (conta, valor, descricao, pago, forma)).fetchone()[0]
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte, valor_centavos,
                                    vencimento, status, pago_em, lancamento_id, cliente_id,
                                    orcamento_id, parcela_idx, acrescimo_centavos)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, tipo, descricao, contraparte, valor, venc, status,
             pago if status == "pago" else None, lid, cliente_id, orc, idx,
             acrescimo)).fetchone()[0]
        c.commit()
    return tid


def _maria(pool, **kw):
    """O caso real: sinal pago de R$ 2.415,00 e as 5 parcelas abertas, com contrato."""
    oid = _orc(pool, **kw)
    sinal = _tit(pool, descricao="Evento — Maria Carolina da Silva Costa · Sinal — "
                                 "confirma a reserva da data",
                 valor=241500, orc=oid, idx=0)
    for i in range(1, 6):
        _tit(pool, status="aberto", descricao=f"Evento — Maria Carolina · Parcela {i}/5",
             valor=114200, orc=oid, idx=i, venc=date(2026, 10, 21))
    with pool.connection() as c:
        c.execute("insert into contratos (conta_id, numero, orcamento_id) values (%s,%s,%s)",
                  (CONTA, 100 + oid, oid))
        c.commit()
    return sinal


# ═══════════════════════════════════════════ o texto aprovado

def test_o_texto_do_sinal_sai_como_o_dono_aprovou(pool):
    d = rb.montar(pool, CONTA, _maria(pool))
    assert d["pagador"] == {"nome": "MARIA CAROLINA DA SILVA COSTA", "doc": "111.222.333-44"}
    assert d["valor"] == "R$ 2.415,00"
    assert d["extenso"] == "dois mil, quatrocentos e quinze reais"
    assert d["referente"] == ("ao Sinal — confirma a reserva da data do orçamento nº 23: "
                              "Casamento em 24/07/2027, locação de espaço")
    # o lançamento veio sem forma: a do plano do orçamento responde, escrita "Pix"
    assert d["forma"] == "Pix"
    assert d["restante"] == "Permanecem em aberto R$ 5.710,00, em 5 parcelas, conforme o contrato."
    assert d["data_br"] == "23/09/2026"
    assert d["fecho"] == "Teresina, 23 de setembro de 2026."
    assert d["empresa"]["nome"] == "PRIME EVENTOS"
    assert d["empresa"]["doc"] == "52.752.898/0001-58"
    assert (d["empresa"]["cidade"], d["empresa"]["uf"]) == ("Teresina", "PI")


def test_a_parcela_leva_a_crase_e_a_forma_do_lancamento_vence(pool):
    oid = _orc(pool, numero=24)
    tid = _tit(pool, descricao="Evento — Fulana · Parcela 2/5", valor=114200, orc=oid,
               idx=2, forma="credito")
    d = rb.montar(pool, CONTA, tid)
    assert d["referente"].startswith("à Parcela 2/5 do orçamento nº 24: Casamento")
    assert d["forma"] == "Cartão de crédito"
    # sem contrato e sem mais nada aberto: diz que não resta nada
    assert d["restante"] == "Com este pagamento, não resta nada em aberto deste orçamento."


def test_orcamento_que_nao_e_de_evento_nao_fala_de_festa(pool):
    oid = _orc(pool, modo="recorrente", numero=7)
    tid = _tit(pool, descricao="Setup — H Pernas", valor=150000, orc=oid, idx=0)
    d = rb.montar(pool, CONTA, tid)
    assert d["referente"] == "ao Setup — H Pernas do orçamento nº 7"
    assert "Casamento" not in json.dumps(d, ensure_ascii=False)


def test_mensalidade_sem_orcamento_ganha_o_mes(pool):
    tid = _tit(pool, descricao="Mensalidade — H Pernas", valor=89000,
               venc=date(2026, 10, 10), pago=date(2026, 10, 9), contraparte="H Pernas")
    d = rb.montar(pool, CONTA, tid)
    assert d["referente"] == "à Mensalidade de outubro/2026 — H Pernas"
    assert d["restante"] == ""        # conta sem orçamento não promete saldo nenhum
    assert d["pagador"]["nome"] == "H PERNAS"


def test_mensalidade_que_ja_diz_o_mes_nao_ganha_outro(pool):
    tid = _tit(pool, descricao="Mensalidade de agosto — H Pernas", valor=89000,
               venc=date(2026, 9, 10), contraparte="H Pernas")
    assert rb.montar(pool, CONTA, tid)["referente"] == "à Mensalidade de agosto — H Pernas"


def test_conta_avulsa_usa_a_propria_descricao(pool):
    tid = _tit(pool, descricao="Contrato nº 115 (3/3)", valor=50000, contraparte="Rone")
    assert rb.montar(pool, CONTA, tid)["referente"] == "ao Contrato nº 115 (3/3)"


def test_juros_e_desconto_aparecem_no_valor_e_no_detalhe(pool):
    tid = _tit(pool, descricao="Contrato nº 9", valor=100000, acrescimo=2500)
    d = rb.montar(pool, CONTA, tid)
    assert d["valor"] == "R$ 1.025,00"
    assert d["detalhe"] == "R$ 1.000,00 da parcela e R$ 25,00 de juros e multa pelo atraso"
    tid = _tit(pool, descricao="Contrato nº 10", valor=100000, acrescimo=-2000)
    d = rb.montar(pool, CONTA, tid)
    assert d["valor"] == "R$ 980,00"
    assert d["detalhe"] == "R$ 1.000,00 da parcela, com R$ 20,00 de desconto"


# ═══════════════════════════════════════════ o CPF

def test_sem_cpf_a_linha_some(pool):
    d = rb.montar(pool, CONTA, _tit(pool, descricao="Contrato nº 11", valor=1000,
                                    contraparte="Sem Documento"))
    assert d["pagador"]["doc"] == ""


def test_cpf_do_cadastro_completa_o_orcamento(pool):
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (nome, cpf) values ('Ana','99988877766') "
                        "returning id").fetchone()[0]
        cli = c.execute("insert into clientes (dono_id, pessoa_id, nome) values (%s,%s,'Ana') "
                        "returning id", (CONTA, pid)).fetchone()[0]
        c.commit()
    oid = _orc(pool, cpf=None, cliente_id=cli, numero=30, empresa="Ana")
    tid = _tit(pool, descricao="Evento — Ana · Sinal", valor=5000, orc=oid, idx=0,
               contraparte="Ana")
    assert rb.montar(pool, CONTA, tid)["pagador"]["doc"] == "999.888.777-66"
    # e a conta avulsa ligada ao cliente também acha o documento
    tid2 = _tit(pool, descricao="Avulsa", valor=5000, cliente_id=cli, contraparte="Ana")
    assert rb.montar(pool, CONTA, tid2)["pagador"]["doc"] == "999.888.777-66"


# ═══════════════════════════════════════════ quem tem recibo

def test_conta_a_pagar_e_conta_aberta_nao_tem_recibo(pool):
    pagar = _tit(pool, tipo="pagar", descricao="Águas", valor=8622, contraparte="Águas")
    aberta = _tit(pool, status="aberto", descricao="Contrato nº 12", valor=1000)
    assert rb.montar(pool, CONTA, pagar) is None
    assert rb.montar(pool, CONTA, aberta) is None
    assert rb.emitir(pool, CONTA, aberta)["ok"] is False
    # e título de outra empresa não abre por id
    vizinha = _tit(pool, descricao="Da vizinha", valor=1000, conta=OUTRA)
    assert rb.montar(pool, CONTA, vizinha) is None


# ═══════════════════════════════════════════ o número

def test_numero_por_empresa_e_ano_e_gerar_de_novo_mantem(pool):
    with pool.connection() as c:
        c.execute("delete from recibos")
        c.commit()
    a = _tit(pool, descricao="Contrato nº 20", valor=1000, contraparte="Alfa")
    b = _tit(pool, descricao="Contrato nº 21", valor=1000, contraparte="Beta")
    ra = rb.emitir(pool, CONTA, a)["recibo"]
    rb_ = rb.emitir(pool, CONTA, b)["recibo"]
    assert (ra["rotulo"], rb_["rotulo"]) == ("0001/2026", "0002/2026")
    # gerar de novo: mesmo número, mesmo link, texto novo
    with pool.connection() as c:
        c.execute("update titulos set contraparte='Alfa Corrigida' where id=%s", (a,))
        c.commit()
    ra2 = rb.emitir(pool, CONTA, a)["recibo"]
    assert (ra2["rotulo"], ra2["token"]) == ("0001/2026", ra["token"])
    assert ra2["dados"]["pagador"]["nome"] == "ALFA CORRIGIDA"
    # a vizinha começa do 1 dela
    v = _tit(pool, descricao="Da vizinha 2", valor=1000, conta=OUTRA)
    assert rb.emitir(pool, OUTRA, v)["recibo"]["rotulo"] == "0001/2026"
    assert rb.mapa(pool, CONTA) == {a: "0001/2026", b: "0002/2026"}
    # ano novo, número novo
    c_ = _tit(pool, descricao="Contrato nº 22", valor=1000, pago=date(2027, 1, 5))
    assert rb.emitir(pool, CONTA, c_)["recibo"]["rotulo"] == "0001/2027"


def test_o_texto_congela_ate_gerar_de_novo(pool):
    t = _tit(pool, descricao="Contrato nº 40", valor=1000, contraparte="Gama")
    tok = rb.emitir(pool, CONTA, t)["recibo"]["token"]
    with pool.connection() as c:
        c.execute("update titulos set contraparte='Outro Nome' where id=%s", (t,))
        c.commit()
    assert rb.por_token(pool, tok)["dados"]["pagador"]["nome"] == "GAMA"


# ═══════════════════════════════════════════ o link público

def _pagina(pool, monkeypatch, token):
    from web import recibo_publico as rp
    monkeypatch.setattr(rp, "get_pool", lambda: pool)
    return rp.recibo_publico(None, token)


def test_a_pagina_publica_mostra_o_texto_aprovado(pool, monkeypatch):
    tok = rb.emitir(pool, CONTA, _maria(pool, numero=23))["recibo"]["token"]
    r = _pagina(pool, monkeypatch, tok)
    html = r.body.decode()
    assert r.status_code == 200
    assert "Recebemos de <b>MARIA CAROLINA DA SILVA COSTA</b>, CPF 111.222.333-44" in html
    assert "R$ 2.415,00" in html and "(dois mil, quatrocentos e quinze reais)" in html
    assert ("referente ao Sinal — confirma a reserva da data do orçamento nº 23: "
            "Casamento em 24/07/2027, locação de espaço. Forma de pagamento: Pix.") in html
    assert "Damos plena quitação <b>desta parcela</b>." in html
    assert "Permanecem em aberto R$ 5.710,00, em 5 parcelas, conforme o contrato." in html
    assert "Teresina, 23 de setembro de 2026." in html
    assert "CNPJ 52.752.898/0001-58" in html


def test_a_pagina_escapa_o_que_foi_digitado(pool, monkeypatch):
    t = _tit(pool, descricao='Contrato <script>alert(1)</script>', valor=1000,
             contraparte='Zé "<b>x</b>"')
    tok = rb.emitir(pool, CONTA, t)["recibo"]["token"]
    html = _pagina(pool, monkeypatch, tok).body.decode()
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert "<b>X</b>" not in html


def test_token_desconhecido_e_404_sem_vazar_nada(pool, monkeypatch):
    r = _pagina(pool, monkeypatch, "nao-existe")
    assert r.status_code == 404 and "Recibo não encontrado" in r.body.decode()


# ═══════════════════════════════════════════ mandar pro cliente

def _stub_envio(monkeypatch, resultado):
    from finance import whatsapp_out
    import web.painel_prospeccao as pp
    enviados, gravados = [], []
    monkeypatch.setattr(whatsapp_out, "chip_da_conversa", lambda c, conta, conv: None)
    monkeypatch.setattr(whatsapp_out, "preparar",
                        lambda c, conta: {"provedor": "qr", "conta_id": conta})
    monkeypatch.setattr(whatsapp_out, "enviar_pronto",
                        lambda destino, numero, texto, chip_id=None:
                        (enviados.append((numero, texto)) or resultado))
    monkeypatch.setattr(pp, "_conversa_id", lambda c, conta, lead, canal: 999)
    monkeypatch.setattr(pp, "_add_msg", lambda c, conv, *a, **k: gravados.append(a))
    return enviados, gravados


def test_sem_lead_nao_manda_e_diz_pra_copiar(pool, monkeypatch):
    enviados, _ = _stub_envio(monkeypatch, {"ok": True})
    t = _tit(pool, descricao="Contrato nº 50", valor=1000)
    rb.emitir(pool, CONTA, t)
    r = rb.mandar(pool, CONTA, None, t)
    assert r["ok"] is False and "copie o link" in r["erro"]
    assert enviados == []


def test_manda_pela_conversa_do_lead_e_carimba(pool, monkeypatch):
    enviados, gravados = _stub_envio(monkeypatch, {"ok": True, "sid": "S1"})
    t = _maria(pool, numero=60)
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, orcamento_id, whatsapp) "
                  "select %s, orcamento_id, '5586999990000' from titulos where id=%s",
                  (CONTA, t))
        c.commit()
    rec = rb.emitir(pool, CONTA, t)["recibo"]
    assert rb.lead_do_titulo(pool, CONTA, t)
    assert rb.mandar(pool, CONTA, 7, t) == {"ok": True}
    assert enviados[0][0] == "5586999990000"
    assert f"recibo nº {rec['rotulo']}" in enviados[0][1] and rec["link"] in enviados[0][1]
    assert gravados and gravados[0][2] == "humano"
    assert rb.do_titulo(pool, CONTA, t)["enviado_em"] is not None


def test_envio_recusado_nao_carimba(pool, monkeypatch):
    _stub_envio(monkeypatch, {"ok": False, "erro": "sem_numero_empresa"})
    t = _maria(pool, numero=61)
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, orcamento_id, whatsapp) "
                  "select %s, orcamento_id, '5586999990001' from titulos where id=%s",
                  (CONTA, t))
        c.commit()
    rb.emitir(pool, CONTA, t)
    r = rb.mandar(pool, CONTA, 7, t)
    assert r["ok"] is False and "não tem WhatsApp ligado" in r["erro"]
    assert rb.do_titulo(pool, CONTA, t)["enviado_em"] is None


# ═══════════════════════════════════════════ a tela da Empresa

def _empresa(**kw):
    from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
    import web.portal as pt
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(kw)
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


def _pago(id_, tipo):
    return {"id": id_, "tipo": tipo, "descricao": f"conta {id_}", "contraparte": "",
            "valor_centavos": 1000, "acrescimo_centavos": 0, "pago_em": date(2026, 9, 23),
            "vencimento": date(2026, 9, 21), "lancamento_id": 5}


def test_o_botao_do_recibo_so_aparece_em_conta_a_receber():
    html = _empresa(titulos_pagos=[_pago(1, "receber"), _pago(2, "pagar")],
                    recibos_mapa={})
    assert 'action="/painel/empresa/titulo/1/recibo"' in html
    assert 'action="/painel/empresa/titulo/2/recibo"' not in html


def test_conta_com_recibo_mostra_o_numero():
    html = _empresa(titulos_pagos=[_pago(1, "receber")], recibos_mapa={1: "0007/2026"})
    assert "🧾 recibo 0007/2026" in html


def test_depois_da_baixa_a_faixa_oferece_o_recibo():
    html = _empresa(recebido_flash={"titulo_id": 9, "valor_centavos": 241500,
                                    "data": "23/09/2026"})
    i = html.index('id="rcb-recebido"')
    faixa = html[i:html.index("</div>", i)]
    assert "✓ Recebido em 23/09/2026 · R$ 2.415,00 lançados no caixa" in faixa
    assert 'action="/painel/empresa/titulo/9/recibo"' in faixa


def test_o_recibo_nascido_tem_as_tres_acoes_e_escapa_o_nome():
    rec = {"rotulo": "0001/2026", "link": "https://app.zaq-ia.com/recibo/abc",
           "titulo_id": 9, "pode_mandar": True, "enviado_em": None,
           "dados": {"pagador": {"nome": "ZÉ <B>X</B>"}, "valor": "R$ 10,00"}}
    html = _empresa(recibo_flash=rec)
    i = html.index('id="rcb-nasceu"')
    faixa = html[i:html.index("<style>", i)]
    assert "abrir / imprimir" in faixa and "copiar link" in faixa
    assert 'action="/painel/empresa/titulo/9/recibo/mandar"' in faixa
    assert "ZÉ &lt;B&gt;X&lt;/B&gt;" in faixa and "<B>X</B>" not in faixa
    rec["pode_mandar"] = False
    faixa = _empresa(recibo_flash=rec)
    assert "/recibo/mandar" not in faixa and "copie o link" in faixa


# ═══════════════════════════════════════════ as rotas da Empresa

class _Req:
    def __init__(self):
        self.session = {"membro_id": 7}


def _rotas(pool, monkeypatch):
    import web.portal as pt
    from finance import empresa as emp
    monkeypatch.setattr(pt, "_guard_pj", lambda req: ((CONTA,), pool))
    # a baixa em si é de `dar_baixa_titulo`, testada nos arquivos dela; aqui o
    # título já nasce pago e a pergunta é só o que a tela faz DEPOIS
    monkeypatch.setattr(emp, "dar_baixa_titulo", lambda *a, **k: {"ok": True})
    return pt


def test_baixa_de_conta_a_receber_abre_a_faixa_do_recibo(pool, monkeypatch):
    pt = _rotas(pool, monkeypatch)
    t = _tit(pool, descricao="Contrato nº 70", valor=241500)
    req = _Req()
    r = pt.empresa_titulo_baixa(req, t, pago_em="", acrescimo="", recebido="",
                                 destino="", alvo_id="")
    assert r.headers["location"].endswith("#rcb-recebido")
    assert req.session["emp_recebido"] == {"titulo_id": t, "valor_centavos": 241500,
                                           "data": "23/09/2026"}


def test_baixa_de_conta_a_pagar_nao_oferece_recibo(pool, monkeypatch):
    pt = _rotas(pool, monkeypatch)
    t = _tit(pool, tipo="pagar", descricao="Águas", valor=8622, contraparte="Águas")
    req = _Req()
    r = pt.empresa_titulo_baixa(req, t, pago_em="", acrescimo="", recebido="",
                                 destino="", alvo_id="")
    assert r.headers["location"].endswith("#titulos")
    assert "emp_recebido" not in req.session


def test_gerar_recibo_pela_rota_deixa_a_faixa_pronta(pool, monkeypatch):
    pt = _rotas(pool, monkeypatch)
    t = _tit(pool, descricao="Contrato nº 71", valor=1000)
    req = _Req()
    r = pt.empresa_titulo_recibo(req, t)
    assert r.headers["location"].endswith("#rcb-nasceu")
    assert req.session["emp_recibo"] == t
    assert rb.do_titulo(pool, CONTA, t) is not None
    # conta aberta: recusa com recado, sem recibo
    aberta = _tit(pool, status="aberto", descricao="Contrato nº 72", valor=1000)
    req = _Req()
    pt.empresa_titulo_recibo(req, aberta)
    assert "Só conta a receber já recebida" in req.session["emp_aviso"]
    assert rb.do_titulo(pool, CONTA, aberta) is None
