"""A folha da proposta RECORRENTE, redesenhada em 23/09/2026.

Nasceu da proposta nº 10 da ZAQ (conta 3), a da HLED: quatro serviços, R$ 500 de
desconto em cada um "pra ficar R$ 1.000 por mês", sem implantação. A folha saiu
com o nome do dono no alto ("Thompson Cavalcante Fernandes"), o cliente numa
linha, o valor cheio de cada serviço, "Mensalidade R$ 5.700" (o bruto), "Total 1º
ano R$ 65.699" (já com desconto) — e os R$ 3.000/mês combinados em lugar nenhum.
O dono aprovou o mockup (docs/mockups/zaq_proposta_recorrente.html) e respondeu:

1. R$ de desconto no recorrente é POR MÊS — "sim";
2. a folha mostra as DUAS formas (mensal e anual à vista) e o cliente escolhe;
3. as condições saem dos números da casa do contrato;
4. o contrato ganha o mesmo cabeçalho.

O que este arquivo fixa, além disso: proposta já gravada não muda de valor
sozinha (regra 0), e nada de festa vaza pro recorrente — nem o contrário
(seção 6 do CLAUDE.md).
"""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from finance import contrato as ctr
from finance import desconto as dsc
from finance import icones_servico as ics
from finance import vendas
from web import painel_servicos as ps
from web import proposta as pp

ZAQ = 3
TOKEN = "HLED-10"

# os números REAIS da proposta nº 10 (itens como a tela nova grava: R$ por mês)
_HLED = [
    {"nome": "Criação de Conteúdo", "desc": "3 Reels por semana · Edição dos Reels",
     "setup": 0, "mensal": 1500, "desc_tipo": "valor", "desc_val": 500, "desc_mes": True},
    {"nome": "Estratégia e Acompanhamento", "desc": "Planejamento mensal",
     "setup": 0, "mensal": 1000, "desc_tipo": "valor", "desc_val": 500, "desc_mes": True},
    {"nome": "Gestão de Tráfego Pago",
     "desc": "Meta Ads · Google Ads · Verba de anúncios não inclusa, paga pelo cliente",
     "setup": 0, "mensal": 2200, "desc_tipo": "valor", "desc_val": 1200, "desc_mes": True},
    {"nome": "Organização e Gestão das Redes Sociais", "desc": "Calendário editorial",
     "setup": 0, "mensal": 1000, "desc_tipo": "valor", "desc_val": 500, "desc_mes": True},
]
# e como ficaram GRAVADOS em produção, antes da regra do por-mês
_HLED_GRAVADA = [{k: v for k, v in it.items() if k != "desc_mes"} for it in _HLED]

_REGRAS = {"indice_reajuste": "percentual de reajuste do salário mínimo vigente",
           "aviso_previo_dias": "30", "implantacao_dias": "60 a 90",
           "suporte_horario": "24 horas por dia, 7 dias por semana", "setup_parcelas": "3"}


# ================================================================ 1. desconto por mês

def test_no_recorrente_r_de_desconto_e_por_mes():
    """O caso da HLED: R$ 500 numa mensalidade de R$ 1.500 = R$ 1.000 por mês."""
    liq = dsc.liquido_do_item(_HLED[0])
    assert liq["mensal"] == 100000 and liq["setup"] == 0
    assert dsc.somar_itens(_HLED)["mensal"] == 300000


def test_proposta_gravada_antes_nao_muda_de_valor_sozinha():
    """Regra 0: o item sem a marca continua valendo o que valia (R$ 500 era a
    fatia do ano: R$ 41,67 por mês). Só muda quando alguém abre e salva."""
    assert dsc.liquido_do_item(_HLED_GRAVADA[0])["mensal"] == 145833
    assert dsc.somar_itens(_HLED_GRAVADA)["mensal"] == 547499


def test_por_mes_nao_toca_a_implantacao_e_nao_passa_de_zero():
    it = {"setup": 3000, "mensal": 800, "desc_tipo": "valor", "desc_val": 900,
          "desc_mes": True}
    liq = dsc.liquido_do_item(it)
    assert liq["setup"] == 300000       # implantação cheia
    assert liq["mensal"] == 0           # nunca negativo


def test_o_percentual_continua_caindo_nas_duas_pontas():
    it = {"setup": 1000, "mensal": 500, "desc_tipo": "pct", "desc_val": 10,
          "desc_mes": True}             # a marca só vale com R$
    liq = dsc.liquido_do_item(it)
    assert (liq["setup"], liq["mensal"]) == (90000, 45000)


def test_a_tela_manda_a_marca_e_mostra_rs_por_mes():
    js = ps._JS_CRU
    assert "function porMes(r)" in js and "desc_mes:porMes(r)" in js
    assert "R$/mês" in js
    # a conta da tela é a mesma do servidor: por mês sai da mensalidade inteiro
    assert "Math.max(0, mb-num(r.querySelector('.oc-desc')))" in js


# ================================================================ 2. as duas formas

def test_as_duas_formas_da_hled():
    f = dsc.formas_recorrente(_HLED, mensal_centavos=570000)
    assert f["mensal"]["mensal"] == 300000 and f["mensal"]["total_anual"] == 3600000
    assert f["anual"]["total_anual"] == 3060000          # 36.000 − 15%
    assert f["economia_anual"] == 540000
    assert f["tabela_mensal"] == 570000 and f["desconto_itens_mensal"] == 270000


def test_as_formas_saem_iguais_com_o_anual_ja_marcado():
    """A coluna guarda a mensalidade bruta JÁ com o anual quando ele foi marcado —
    refazer as formas a partir dela tem que dar os mesmos números."""
    f = dsc.formas_recorrente(_HLED, mensal_centavos=int(570000 * 0.85), anual=True)
    assert f["mensal"]["mensal"] == 300000 and f["anual"]["total_anual"] == 3060000


def test_as_formas_batem_com_o_salvar():
    """O salvar grava `totais(..., fator_mensal)`; a folha refaz por
    `formas_recorrente`. As duas contas têm de dar o mesmo número."""
    t = dsc.totais(_HLED, tipo="pct", pct=10, fator_mensal=0.85)
    f = dsc.formas_recorrente(_HLED, mensal_centavos=int(570000 * 0.85), anual=True,
                              tipo="pct", pct=10)
    assert f["anual"]["mensal"] == t["mensal"] and f["anual"]["ano1"] == t["total"]


# ================================================================ 3. ícones

@pytest.mark.parametrize("nome,chave", [
    ("Gestão de Tráfego Pago", "anuncio"),
    ("Organização e Gestão das Redes Sociais", "redes"),
    ("Criação de Conteúdo", "conteudo"),
    ("Estratégia e Acompanhamento", "estrategia"),
    ("Agente de Atendimento", "ia"), ("Agente de Voz", "voz"),
    ("CRM / Leads", "funil"), ("BI / Dashboard", "bi"),
    ("Sistema Sob Medida", "sistema"),
    ("Mídia kit", "outros"),            # "ia" solto só casa como palavra
])
def test_o_recorrente_escolhe_o_icone_pelo_nome(nome, chave):
    assert ics.escolher(nome, modo="recorrente") == chave


def test_os_dois_jogos_nao_se_misturam():
    rec, ev = set(ics.CHAVES_RECORRENTE), set(ics.CHAVES_EVENTO)
    assert rec & ev == {"outros"}
    # festa no recorrente não ganha desenho de festa…
    assert ics.escolher("DJ", modo="recorrente") == "outros"
    # …e ícone de festa fixado no catálogo não é aceito lá
    assert ics.escolher("Qualquer", fixo="buffet", modo="recorrente") == "outros"
    # e o evento continua como sempre foi
    assert ics.escolher("DJ") == "som"
    assert ics.escolher("Tráfego pago", fixo="anuncio") != "anuncio"
    assert {p["chave"] for p in ics.paleta("recorrente")} == rec


def test_o_catalogo_do_recorrente_tem_seletor_de_icone():
    html = ps._env.get_template("servicos").render(
        empresa_nome="ZAQ", tem_pj=True, vende_servico=True, servico_avulso=False,
        pode_contrato=True, ve_todos=True, tipo_padrao="pj", tipos_evento=[],
        tipos_contrato=[], local_padrao="", icones_paleta=ics.paleta("recorrente"))
    assert 'id="svc-icones"' in html
    assert 'id="svc-cat"' not in html     # a categoria é do evento


# ================================================================ 4. a lista do que inclui

def test_a_descricao_vira_lista_e_o_nao_incluso_sai_marcado():
    l = pp._incluso_em_lista("Meta Ads · Google Ads · Verba de anúncios não inclusa")
    assert [x["t"] for x in l] == ["Meta Ads", "Google Ads", "Verba de anúncios não inclusa"]
    assert [x["obs"] for x in l] == [False, False, True]
    assert pp._incluso_em_lista("Um parágrafo só, sem separador") == [
        {"t": "Um parágrafo só, sem separador", "obs": False}]


# ================================================================ 5. a folha, pela rota

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text, cnae text,
  nicho_id bigint references nichos(id));
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table eventos_agenda (id bigserial primary key, status text,
  pre_reserva_ate timestamptz);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, endereco text, cep text, cidade text, uf text, email text, telefone text);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '',
  assinar_antes_do_sinal boolean not null default false,
  pedir_assinatura boolean not null default false);
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_proposta_recorrente"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into nichos (nome, slug, tipo) values "
                  "('Consultoria','consultoria','servico')")
        c.execute("""insert into contas (id, nome, nome_fantasia, razao_social, documento,
                       endereco, bairro, cep, cidade, uf, telefone, email_empresa, nicho_id)
                     values (%s,'Thompson Cavalcante Fernandes','ZAQ - SISTEMAS IAs',
                       'T CAVALCANTE FERNANDES LTDA','50379520000125',
                       'VETERINARIO BUGYJA BRITTO, 1229','HORTO','64052410','TERESINA',
                       'PI','86981885930','contato@zaq.test',
                       (select id from nichos where slug='consultoria'))""", (ZAQ,))
        c.commit()
    with p.connection() as c:
        ps._garantir_tabela(c)          # orcamentos como em produção
        c.commit()
    monkeypatch.setattr(pp, "get_pool", lambda: p)
    yield p
    p.close()


def _migrar_311(pool):
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
           / "311_contrato_servico.sql").read_text(encoding="utf-8")
    with pool.connection() as c:
        c.execute(sql)
        c.commit()


def _proposta(pool, itens=_HLED, anual=False):
    _migrar_311(pool)
    mensal = sum(i["mensal"] for i in itens) * 100
    if anual:
        mensal = int(round(mensal * 0.85))
    with pool.connection() as c:
        c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, cnpj, whatsapp, email,
                 cidade, uf, segmento, status, numero, modo, itens, setup_centavos,
                 mensal_centavos, primeiro_ano_centavos, token, criado_por,
                 pagamento_anual)
               values (%s,'Heitor','HLED','20353161000176','(86) 8166-3346',
                 'heitor@hled.test','TERESINA','PI','Material elétrico','enviado',10,
                 'recorrente',%s::jsonb,0,%s,%s,%s,'dono',%s)""",
            (ZAQ, json.dumps(itens), mensal, 3600000, TOKEN, anual))
        c.commit()


def _cliente():
    app = FastAPI()
    app.include_router(pp.router)
    return TestClient(app)


def _html(pool):
    r = _cliente().get(f"/proposta/{TOKEN}")
    assert r.status_code == 200
    return r.text


def test_a_folha_abre_com_a_empresa_e_nao_com_o_dono(pool):
    _proposta(pool)
    html = _html(pool)
    cab = html.split('<div class="bd">', 1)[0]
    assert "ZAQ - SISTEMAS IAs" in cab
    assert "T CAVALCANTE FERNANDES LTDA" in cab and "50.379.520/0001-25" in cab
    assert "Responsável: Thompson Cavalcante Fernandes" in cab
    assert '<div class="lg">Thompson' not in cab


def test_o_contratante_sai_inteiro(pool):
    _proposta(pool)
    html = _html(pool)
    assert "20.353.161/0001-76" in html and "heitor@hled.test" in html
    assert "A/C</span> Heitor" in html and "Teresina/PI" in html


def test_o_desconto_aparece_e_a_conta_fecha(pool):
    _proposta(pool)
    html = _html(pool)
    assert '<span class="risc">R$ 1.500,00</span>' in html      # o cheio riscado
    assert "R$ 5.700,00" in html and "− R$ 2.700,00" in html     # tabela e desconto
    fin = html.split('class="fin"', 1)[1][:400]
    assert "Investimento mensal" in fin and "R$ 3.000,00" in fin
    assert "Sem taxa de implantação" in fin


def test_as_duas_formas_e_o_cliente_escolhe(pool):
    _proposta(pool)
    html = _html(pool)
    assert "Anual à vista" in html and "R$ 30.600,00" in html and "R$ 5.400,00" in html
    form = html.split('class="sign"', 1)[1]
    assert 'name="forma" value="mensal" checked' in form
    assert 'name="forma" value="anual"' in form


def test_as_condicoes_vem_dos_numeros_da_casa(pool):
    _proposta(pool)
    ctr.salvar_modelo(pool, ZAQ, ctr.modelo_padrao(ctr.MODO_SERVICO), _REGRAS,
                      pedir_assinatura=True)
    html = _html(pool)
    assert "aviso prévio de 30 dias" in html
    assert "Reajuste anual pelo percentual de reajuste do salário mínimo vigente" in html
    assert "Suporte 24 horas por dia, 7 dias por semana" in html
    assert "contrato de prestação de serviços é enviado" in html
    # sem implantação na proposta, o prazo da implantação não vira promessa
    assert "Implantação em 60 a 90" not in html


def test_sem_numeros_da_casa_nao_sai_condicao_em_branco(pool):
    _proposta(pool)
    html = _html(pool)
    assert "Condições" not in html


def test_nada_de_festa_na_folha_do_recorrente(pool):
    _proposta(pool)
    # o que a pessoa LÊ: depois do <style> (os comentários do CSS falam de festa)
    html = _html(pool).split("</style>", 1)[1].lower()
    for palavra in ("convidados", "festa", "reservar a data", "orçamento de evento"):
        assert palavra not in html, palavra


def test_o_icone_e_do_jogo_do_recorrente(pool):
    _proposta(pool)
    html = _html(pool)
    assert ics.ICONES["anuncio"][1] in html        # tráfego pago → megafone
    assert ics.ICONES["buffet"][1] not in html


# ================================================================ 6. a escolha do cliente

def _estado(pool):
    with pool.connection() as c:
        return c.execute(
            """select pagamento_anual, mensal_centavos, mensal_liquido_centavos,
                      primeiro_ano_centavos, aprovada_por
                 from orcamentos where token=%s""", (TOKEN,)).fetchone()


def test_o_cliente_escolhe_o_anual_e_os_valores_sao_refeitos(pool):
    _proposta(pool)
    r = _cliente().post(f"/proposta/{TOKEN}/assinar",
                        data={"nome": "Heitor", "aceite": "on", "forma": "anual"},
                        follow_redirects=False)
    assert r.status_code == 303
    anual, bruto, liq, ano1, por = _estado(pool)
    assert anual is True and por == "Heitor"
    assert bruto == 484500              # 5.700 × 0,85 — como o salvar grava
    assert liq == 255000 and ano1 == 3060000


def test_o_cliente_volta_pro_mensal(pool):
    _proposta(pool, anual=True)
    _cliente().post(f"/proposta/{TOKEN}/assinar",
                    data={"nome": "Heitor", "aceite": "on", "forma": "mensal"})
    anual, bruto, liq, ano1, _ = _estado(pool)
    assert anual is False and bruto == 570000 and liq == 300000 and ano1 == 3600000


def test_depois_de_aprovada_a_forma_nao_muda_mais(pool):
    _proposta(pool)
    _cliente().post(f"/proposta/{TOKEN}/assinar",
                    data={"nome": "Heitor", "aceite": "on", "forma": "mensal"})
    assert vendas.aplicar_forma_recorrente(pool, TOKEN, True) is False
    assert _estado(pool)[0] is False


def test_sem_aceite_nao_grava_a_forma(pool):
    _proposta(pool)
    _cliente().post(f"/proposta/{TOKEN}/assinar", data={"nome": "Heitor", "forma": "anual"})
    assert _estado(pool)[0] is False
