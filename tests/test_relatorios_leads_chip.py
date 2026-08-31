"""Relatórios › Leads do chip: quem chamou pelo QR e quanto esperou.

O painel sempre soube quantas mensagens chegaram. Nunca soube quanto tempo alguém
ficou SEM RESPOSTA — e em 26/08/2026, na conta 34, sete pessoas tinham chamado o
chip principal e ninguém tinha respondido. Nenhuma tela dizia isso.

Três coisas aqui são fáceis de escrever errado, e cada uma tem teste próprio
porque cada uma daria um relatório que MENTE:

1. **`conversas.chip_id` NULO é o chip principal**, não é dado faltando. O chip
   secundário é uma conta inteira (`contas.chip_de`) e a conversa dele guarda o id
   dessa conta; a do principal não guarda nada. Um `where chip_id = %s` esconderia
   174 dos 186 leads da conta 34 — some justamente o chip que mais recebe.
2. **Resposta é `out` de HUMANO.** Havia 18 mensagens de bot na conta 34. Contar o
   bot zeraria a espera de quem, na prática, continuou esperando gente.
3. **"Última msg" vem da conversa**, não de `prospeccao.ultimo_contato_em` — esse
   campo está vazio em 158 dos 174 leads do chip principal, que têm 2.772
   mensagens trocadas. Lido dali, o relatório anunciaria que quase ninguém foi
   atendido.

E dois casos que só apareceram porque eu olhei as linhas de verdade antes de
escrever: conversa com ZERO mensagem (existe, 1 lead) não é "nunca respondido", e
resposta ANTERIOR à primeira entrada (11 leads, conversa que o vendedor começou)
não pode virar espera negativa.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel
from finance import vendas

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, rotulo text);
create table orcamentos (id bigserial primary key, conta_id bigint, numero int);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  orcamento_id bigint, ultimo_contato_em timestamptz,
  criado_em timestamptz not null default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  chip_id bigint, responsavel_membro_id bigint, ultima_msg_em timestamptz,
  criado_em timestamptz not null default now(), visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text, criado_em timestamptz not null default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
"""

#: base de tempo fixa — a espera é medida em minutos, e um teste que lê o relógio
#: passa de manhã e falha à noite.
T0 = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_leads_chip_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool):
    """Uma conta com DOIS chips, como a 34 em produção: o próprio (canal `qr:<id>`,
    rótulo "CP Zarb") e um secundário que é conta própria ("CP Thiago")."""
    with pool.connection() as c:
        c.execute("truncate contas, membros, canais_config, orcamentos, prospeccao, "
                  "conversas, mensagens restart identity")
        conta = c.execute("insert into contas (nome) values ('Prime Eventos') "
                          "returning id").fetchone()[0]
        chip2 = c.execute("insert into contas (nome, chip_de) values ('CP Thiago', %s) "
                          "returning id", (conta,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, rotulo)
                     values (%s,'whatsapp',%s,'CP Zarb')""", (conta, f"qr:{conta}"))
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro') "
                          "returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "chip2": chip2, "pedro": pedro}


def _lead(pool, conta, *, nome="Jamile", chip_id=None, membro=None,
          entrou_min=None, resp_min=None, resp_autor="humano", extras=(),
          orcamento=None, ultimo_contato_em=None, ultima_msg_min=0):
    """Cria lead + conversa + mensagens. Os tempos são MINUTOS a partir de T0."""
    with pool.connection() as c:
        oid = None
        if orcamento is not None:
            oid = c.execute("insert into orcamentos (conta_id, numero) values (%s,%s) "
                            "returning id", (conta, orcamento)).fetchone()[0]
        pid = c.execute(
            """insert into prospeccao (conta_id, empresa, orcamento_id, ultimo_contato_em,
                 criado_em) values (%s,%s,%s,%s,%s) returning id""",
            (conta, nome, oid, ultimo_contato_em, T0)).fetchone()[0]
        cid = c.execute(
            """insert into conversas (conta_id, prospeccao_id, chip_id,
                 responsavel_membro_id, ultima_msg_em, criado_em)
               values (%s,%s,%s,%s,%s,%s) returning id""",
            (conta, pid, chip_id, membro, T0 + timedelta(minutes=ultima_msg_min), T0)).fetchone()[0]
        if entrou_min is not None:
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) "
                      "values (%s,'in','lead',%s)", (cid, T0 + timedelta(minutes=entrou_min)))
        if resp_min is not None:
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) "
                      "values (%s,'out',%s,%s)",
                      (cid, resp_autor, T0 + timedelta(minutes=resp_min)))
        for d, a, m in extras:
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) "
                      "values (%s,%s,%s,%s)", (cid, d, a, T0 + timedelta(minutes=m)))
        c.commit()
    return pid


def _rel(pool, conta, *, chip="", vendedor="", q=""):
    return rel._dados_leads_chip(pool, conta, "todos", chip, vendedor, q)


def _por_nome(d, nome):
    return next(l for l in d["linhas"] if l["lead"] == nome)


def _metrica(d, rotulo):
    return dict(d["metricas"])[rotulo]


# ------------------------------------------------- o chip nulo é o chip principal

def test_lead_do_chip_principal_aparece_mesmo_com_chip_id_nulo(pool, cen):
    """O caso dos 174. Se esta linha sumir, o relatório perde o chip que mais recebe."""
    _lead(pool, cen["conta"], nome="Jamile", chip_id=None, entrou_min=0, resp_min=10)
    d = _rel(pool, cen["conta"])
    assert [l["lead"] for l in d["linhas"]] == ["Jamile"]


def test_chip_nulo_ganha_o_rotulo_do_canal_e_nao_um_traco(pool, cen):
    _lead(pool, cen["conta"], nome="Jamile", chip_id=None, entrou_min=0, resp_min=10)
    assert _por_nome(_rel(pool, cen["conta"]), "Jamile")["chip"] == "CP Zarb"


def test_chip_secundario_ganha_o_nome_da_conta_dele(pool, cen):
    _lead(pool, cen["conta"], nome="Duda", chip_id=cen["chip2"], entrou_min=0, resp_min=5)
    assert _por_nome(_rel(pool, cen["conta"]), "Duda")["chip"] == "CP Thiago"


def test_filtro_principal_traz_so_o_chip_nulo(pool, cen):
    _lead(pool, cen["conta"], nome="Jamile", chip_id=None, entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="Duda", chip_id=cen["chip2"], entrou_min=0, resp_min=5)
    d = _rel(pool, cen["conta"], chip=rel.CHIP_PRINCIPAL)
    assert [l["lead"] for l in d["linhas"]] == ["Jamile"]


def test_filtro_por_id_traz_so_o_secundario(pool, cen):
    _lead(pool, cen["conta"], nome="Jamile", chip_id=None, entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="Duda", chip_id=cen["chip2"], entrou_min=0, resp_min=5)
    d = _rel(pool, cen["conta"], chip=str(cen["chip2"]))
    assert [l["lead"] for l in d["linhas"]] == ["Duda"]


def test_sem_filtro_vem_os_dois_chips(pool, cen):
    _lead(pool, cen["conta"], nome="Jamile", chip_id=None, entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="Duda", chip_id=cen["chip2"], entrou_min=0, resp_min=5)
    assert len(_rel(pool, cen["conta"])["linhas"]) == 2


# --------------------------------------------------------------- a espera

def test_espera_sai_em_minutos_quando_e_curta(pool, cen):
    _lead(pool, cen["conta"], nome="Dinamara", entrou_min=0, resp_min=37)
    l = _por_nome(_rel(pool, cen["conta"]), "Dinamara")
    assert l["esperou"] == "37 min" and l["esperou_cor"] == "ok"


def test_espera_longa_sai_em_horas_e_vira_ambar(pool, cen):
    _lead(pool, cen["conta"], nome="Josielle", entrou_min=0, resp_min=190)
    l = _por_nome(_rel(pool, cen["conta"]), "Josielle")
    assert l["esperou"] == "3h10" and l["esperou_cor"] == "aviso"


def test_nunca_respondido_e_coral(pool, cen):
    """Os 7 da conta 34 — o achado que motivou o relatório."""
    _lead(pool, cen["conta"], nome="Geiza", entrou_min=0, resp_min=None)
    l = _por_nome(_rel(pool, cen["conta"]), "Geiza")
    assert l["esperou"] == "nunca respondido" and l["esperou_cor"] == "erro"


def test_resposta_do_bot_nao_conta_como_resposta(pool, cen):
    """Havia 18 mensagens de bot na conta 34. Se contassem, o lead pareceria
    atendido na hora — e ele continuou esperando gente."""
    _lead(pool, cen["conta"], nome="Geiza", entrou_min=0, resp_min=1, resp_autor="bot")
    assert _por_nome(_rel(pool, cen["conta"]), "Geiza")["esperou"] == "nunca respondido"


def test_bot_antes_do_humano_nao_encurta_a_espera(pool, cen):
    """O bot responde em 1 min, o humano em 90. A espera é 1h30, não 1 min."""
    _lead(pool, cen["conta"], nome="Geiza", entrou_min=0, resp_min=90,
          extras=[("out", "bot", 1)])
    assert _por_nome(_rel(pool, cen["conta"]), "Geiza")["esperou"] == "1h30"


def test_resposta_anterior_a_entrada_vira_ja_falava(pool, cen):
    """11 leads assim em produção: quem começou a conversa foi o vendedor. Sem este
    ramo a coluna mostraria espera NEGATIVA."""
    _lead(pool, cen["conta"], nome="Elaine", entrou_min=60, resp_min=0)
    l = _por_nome(_rel(pool, cen["conta"]), "Elaine")
    assert l["esperou"] == "já falava" and l["esperou_cor"] == "neutro"


def test_conversa_sem_mensagem_nenhuma_nao_e_nunca_respondido(pool, cen):
    """Existe (1 lead na conta 34): conversa criada e nenhuma mensagem. Chamar isso
    de abandono acusaria o vendedor de algo que não aconteceu."""
    _lead(pool, cen["conta"], nome="Beatriz", entrou_min=None, resp_min=None)
    l = _por_nome(_rel(pool, cen["conta"]), "Beatriz")
    assert l["esperou"] == "sem mensagem" and l["esperou_cor"] == "neutro"


def test_resposta_no_mesmo_minuto_diz_na_hora(pool, cen):
    _lead(pool, cen["conta"], nome="Josipio", entrou_min=0, resp_min=0)
    assert _por_nome(_rel(pool, cen["conta"]), "Josipio")["esperou"] == "na hora"


# ----------------------------------------------------- a última mensagem

def test_ultima_msg_vem_da_conversa_e_nao_de_ultimo_contato_em(pool, cen):
    """`ultimo_contato_em` está vazio em 158 dos 174 leads do chip principal, que
    têm 2.772 mensagens trocadas. Se o relatório lesse dali, diria que quase
    ninguém foi atendido."""
    _lead(pool, cen["conta"], nome="Natalia", entrou_min=0, resp_min=26,
          ultimo_contato_em=None, ultima_msg_min=120)
    l = _por_nome(_rel(pool, cen["conta"]), "Natalia")
    assert l["ultima"] != "—", "leu ultimo_contato_em (vazio) em vez da conversa"
    # com o ANO desde 31/08/2026: a Prime tem compromisso até outubro/2027, e
    # "26/08 11:00" não diz de qual ano é
    assert l["ultima"] == (T0 + timedelta(minutes=120)).astimezone(
        rel._ag.BRT).strftime("%d/%m/%Y %H:%M")


# ------------------------------------------------------------- as métricas

def test_metricas_contam_o_que_a_tabela_mostra(pool, cen):
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="B", entrou_min=0, resp_min=None)
    _lead(pool, cen["conta"], nome="C", entrou_min=0, resp_min=None)
    _lead(pool, cen["conta"], nome="D", entrou_min=0, resp_min=30, orcamento=7)
    d = _rel(pool, cen["conta"])
    assert _metrica(d, "Leads recebidos") == "4"
    assert _metrica(d, "Nunca respondidos") == "2"
    assert _metrica(d, "Viraram orçamento") == "1"


def test_mediana_ignora_quem_nunca_foi_respondido(pool, cen):
    """Sem resposta não é espera infinita nem espera zero: é outra coisa, e já tem
    métrica própria. Misturar na mediana inventaria um número."""
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="B", entrou_min=0, resp_min=30)
    _lead(pool, cen["conta"], nome="C", entrou_min=0, resp_min=None)
    assert _metrica(_rel(pool, cen["conta"]), "Espera (mediana)") == "20 min"


def test_mediana_e_nao_media(pool, cen):
    """Uma conversa esquecida por cinco dias não pode descrever o atendimento de
    todo mundo. Média destes seria 1721 min (~28h); a mediana é 20 min."""
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="B", entrou_min=0, resp_min=30)
    _lead(pool, cen["conta"], nome="C", entrou_min=0, resp_min=60 * 24 * 5)
    assert _metrica(_rel(pool, cen["conta"]), "Espera (mediana)") == "30 min"


def test_sem_amostra_a_mediana_nao_inventa_zero(pool, cen):
    _lead(pool, cen["conta"], nome="B", entrou_min=0, resp_min=None)
    assert _metrica(_rel(pool, cen["conta"]), "Espera (mediana)") == "—"


# ------------------------------------------------------- forma do relatório

def test_nao_tem_coluna_de_valor(pool, cen):
    """`valor_estimado_centavos` é zero nos 675 leads da base. Uma coluna somando
    R$ 0,00 é o mesmo ruído que o funil acabou de tirar."""
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    d = _rel(pool, cen["conta"])
    assert not any(c["brl"] for c in d["colunas"])


def test_sem_linha_de_total(pool, cen):
    """`col_total` nulo é o que faz o template pular a linha de Total — sem isso a
    tabela termina numa linha "Total" com todas as células vazias."""
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    assert _rel(pool, cen["conta"])["col_total"] is None


def test_filtro_de_chip_so_existe_quando_ha_mais_de_um(pool, cen):
    """Numa conta de chip único o seletor seria pergunta de resposta única."""
    _lead(pool, cen["conta"], nome="A", entrou_min=0, resp_min=10)
    assert _rel(pool, cen["conta"])["filtro_extra"]["chips"]

    with pool.connection() as c:
        c.execute("delete from contas where chip_de=%s", (cen["conta"],))
        c.commit()
    assert _rel(pool, cen["conta"])["filtro_extra"]["chips"] == []


def test_filtro_por_vendedor(pool, cen):
    _lead(pool, cen["conta"], nome="DoPedro", entrou_min=0, resp_min=10, membro=cen["pedro"])
    _lead(pool, cen["conta"], nome="DeNinguem", entrou_min=0, resp_min=10)
    d = _rel(pool, cen["conta"], vendedor=str(cen["pedro"]))
    assert [l["lead"] for l in d["linhas"]] == ["DoPedro"]


def test_busca_pelo_nome_do_lead(pool, cen):
    _lead(pool, cen["conta"], nome="Jamile Karen", entrou_min=0, resp_min=10)
    _lead(pool, cen["conta"], nome="Geiza", entrou_min=0, resp_min=10)
    assert [l["lead"] for l in _rel(pool, cen["conta"], q="jamile")["linhas"]] == ["Jamile Karen"]


def test_outra_conta_nao_vaza(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Doce Mell') "
                          "returning id").fetchone()[0]
        c.commit()
    _lead(pool, cen["conta"], nome="Meu", entrou_min=0, resp_min=10)
    _lead(pool, outra, nome="Alheio", entrou_min=0, resp_min=10)
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["Meu"]


def test_lead_sem_conversa_nao_entra(pool, cen):
    """A base tem 314 leads garimpados no Google Maps, que nunca chegaram por chip.
    Este relatório é só do que entrou pelo QR."""
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, empresa) values (%s,'Garimpado')",
                  (cen["conta"],))
        c.commit()
    _lead(pool, cen["conta"], nome="DoChip", entrou_min=0, resp_min=10)
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["DoChip"]


def test_aba_esta_registrada(pool, cen):
    assert "leads_chip" in rel.TIPOS
    assert rel.TIPOS["leads_chip"]["label"] == "Leads do chip"


# ------------------------------------------------ a redação, sem banco nenhum

def test_duracao_curta_cobre_as_quatro_faixas():
    assert vendas.duracao_curta(0) == "na hora"
    assert vendas.duracao_curta(37) == "37 min"
    assert vendas.duracao_curta(190) == "3h10"
    assert vendas.duracao_curta(60 * 24 * 5 + 4 * 60) == "5d 4h"
    assert vendas.duracao_curta(None) == "—"


def test_hora_cheia_nao_perde_o_zero_da_direita():
    """"2h00" e não "2h0" — a coluna é lida como hora, e "2h0" parece truncado."""
    assert vendas.duracao_curta(120) == "2h00"


def test_rotulo_do_chip_sem_rotulo_cadastrado_nao_devolve_vazio():
    assert vendas.rotulo_do_chip(None, nome_principal="") == "Chip principal"
    assert vendas.rotulo_do_chip(9, rotulos={}) == "Chip 9"


def test_mediana_par_e_impar():
    assert vendas.mediana([10, 30]) == 20
    assert vendas.mediana([10, 30, 50]) == 30
    assert vendas.mediana([]) is None
    assert vendas.mediana([None]) is None


# --------------------------------------- o template, que Python nenhum testa
#
# As duas mudanças em `web/portal.py` (a linha de Total condicional e o select de
# chip) são Jinja. Teste de Python passa verde com o template quebrado, e o erro
# só aparece quando alguém abre a tela. Estes renderizam de verdade.

def _render(dados, tipo):
    from web.portal import _env
    saida = {}
    for nome in ("relatorios", "relatorio_pdf"):
        kw = dict(dados=dados, tipo=tipo, periodo="mes", periodo_rotulo="Este mês")
        if nome == "relatorios":
            kw.update(tipos=rel.TIPOS, periodos=rel.PERIODOS, conta=(1, "pj", "X"),
                      caps={"financeiro": True, "vendas": True, "gerir": True},
                      tem_pj=True, papel="dono", request=None)
        else:
            kw.update(gerado_em="26/08 15:00", marca={}, empresa_nome="X",
                      cnpj_fmt="", endereco_fmt="")
        saida[nome] = _env.get_template(nome).render(**kw)
    return saida


def _tem_linha_total(html: str) -> bool:
    """A LINHA, não a regra CSS `.rel-tot td{...}` que vive no <style> e sempre
    existe — procurar só por "rel-tot" daria falso positivo em toda página.

    Casa por FORMA (`<tr ... class="rel-tot"`) e não pela string exata: a versão
    antiga procurava `<tr class="rel-tot">` literal e passou a falhar sozinha
    quando a tela ganhou `id="rel-tot"` no mesmo elemento. Teste que quebra
    porque um atributo entrou no meio não está medindo o que diz medir."""
    return bool(re.search(r'<tr\b[^>]*class="[^"]*\brel-tot\b', html)
                or re.search(r"<tfoot>\s*<tr\b", html))


_LEADS_FAKE = {
    "label": "Leads do chip", "mock": False,
    "colunas": [rel._col("lead", "Lead"), rel._col("chip", "Chip"),
                rel._col("esperou", "Esperou", tag=True),
                rel._col("msgs", "Msgs", num=True)],
    "linhas": [{"lead": "Jamile", "chip": "CP Zarb", "esperou": "nunca respondido",
                "esperou_cor": "erro", "msgs": 2}],
    "col_total": None, "total_centavos": 0,
    "metricas": [("Leads recebidos", "186"), ("Nunca respondidos", "7")],
    "filtro_extra": {"chips": [("", "Chip: todos"), ("principal", "CP Zarb"),
                               ("36", "CP Thiago")],
                     "chip_sel": "", "vendedores": [(1, "Pedro")],
                     "vendedor_sel": "", "busca_sel": ""},
}


def test_template_nao_desenha_linha_de_total_sem_col_total():
    for nome, html in _render(_LEADS_FAKE, "leads_chip").items():
        assert not _tem_linha_total(html), f"{nome}: Total apareceu sem col_total"


def test_template_continua_somando_nas_abas_que_tem_dinheiro():
    """A guarda nova não pode ter tirado o total de Orçamentos/Contratos."""
    orc = {"label": "Orçamentos", "mock": False,
           "colunas": [rel._col("numero", "Nº"),
                       rel._col("valor_centavos", "Valor", num=True, brl=True)],
           "linhas": [{"numero": 1, "valor_centavos": 50000}],
           "col_total": "valor_centavos", "total_centavos": 50000,
           "metricas": [("Total geral", "R$ 500,00")],
           "filtro_extra": {"status_opcoes": [("", "Status: todos")], "status_sel": "",
                            "vendedores": [(1, "Pedro")], "vendedor_sel": "",
                            "busca_sel": ""}}
    for nome, html in _render(orc, "orcamentos").items():
        assert _tem_linha_total(html), f"{nome}: o total do orçamento sumiu"
        assert "R$ 500,00" in html


def test_template_mostra_o_seletor_de_chip_e_a_busca_por_lead():
    html = _render(_LEADS_FAKE, "leads_chip")["relatorios"]
    assert "CP Thiago" in html and "Status: todos" not in html
    assert "buscar lead" in html


def test_template_esconde_o_seletor_quando_a_conta_tem_um_chip_so():
    um = dict(_LEADS_FAKE)
    um["filtro_extra"] = {"chips": [], "chip_sel": "", "vendedores": [(1, "Pedro")],
                          "vendedor_sel": "", "busca_sel": ""}
    html = _render(um, "leads_chip")["relatorios"]
    assert "CP Thiago" not in html and "Status: todos" not in html
    assert "Pedro" in html, "o filtro de vendedor não podia sumir junto"
