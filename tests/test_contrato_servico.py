"""O contrato de PRESTAÇÃO DE SERVIÇOS do nicho recorrente (migração 311).

Pedido do dono em 23/09/2026 pra ZAQ (conta 3): "copie o mesmo modelo que já roda
na Prime ... com orçamento e contrato sem o aditivo ainda". O motor é o do contrato
de locação; o que este arquivo fixa é o que muda — e o que NÃO pode mudar:

* **A chave é por conta e nasce desligada.** São oito contas recorrentes; ligar
  pelo nicho poria um contrato que ninguém escreveu na frente de todas. Desligada,
  nada muda: a proposta aprovada fecha pelo botão, como ontem.
* **Número da casa em branco não vai pro cliente.** O de serviço nasce sem
  fidelidade, vencimento ou reajuste — a ZAQ não tem contrato vigente pra copiar.
  A chave não liga com falta, e a folha pública não deixa assinar com falta.
* **Nada de festa** (seção 6 do CLAUDE.md): nem no texto padrão, nem na paleta de
  campos, nem na folha do cliente.
* **A Prime não muda**: o contexto do contrato de locação sai idêntico.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import contrato as ctr
from web import contrato_publico as cp

ZAQ = 3
PRIME = 34
CT_TOKEN = "CT-SERVICO"

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text, cnae text,
  nicho_id bigint references nichos(id));
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, itens jsonb, whatsapp text, email text, telefone text, cnpj text,
  endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'rascunho',
  numero int, modo text default 'recorrente', evento jsonb, parcelas jsonb,
  sinal_pago_em timestamptz, cliente_id bigint,
  pagamento_anual boolean not null default false);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, endereco text, cep text, cidade text, uf text);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '',
  assinar_antes_do_sinal boolean not null default false,
  pedir_assinatura boolean not null default false);
create table contratos (id bigserial primary key, conta_id bigint not null,
  numero int not null, orcamento_id bigint,
  status text not null default 'enviado', texto jsonb, valor_centavos bigint,
  assinado_em timestamptz, assinado_por text, assinado_doc text, assinado_ip text,
  rescindido_em timestamptz, rescisao_motivo text, substitui_id bigint, token text,
  enviado_em timestamptz,
  criado_em timestamptz default now(), criado_por text default '');
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, descricao text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, custo_centavos bigint default 0, ordem int default 0,
  categoria text, foto_url text, icone text, ativo boolean default true);
"""

# os números que a ZAQ vai mandar — aqui, inventados pelo teste
_REGRAS_CHEIAS = {"fidelidade_meses": "12", "dia_vencimento": "10",
                  "indice_reajuste": "IPCA", "aviso_previo_dias": "30",
                  "multa_rescisao": "30", "implantacao_dias": "30",
                  "suporte_horario": "de segunda a sexta, das 8h às 18h",
                  "setup_parcelas": "parcela única"}

_ITENS = [{"nome": "Agente de Atendimento", "desc": "WhatsApp 24h", "setup": 4500,
           "mensal": 1200},
          {"nome": "CRM / Leads", "desc": "", "setup": 3500, "mensal": 900}]


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_contrato_servico"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into nichos (nome, slug, tipo) values "
                  "('Consultoria','consultoria','servico'), ('Eventos','eventos','servico')")
        c.execute("insert into contas (id, nome, razao_social, documento, cidade, uf, nicho_id) "
                  "values (%s,'ZAQ','T CAVALCANTE FERNANDES LTDA','12.345.678/0001-90',"
                  "'Teresina','PI',(select id from nichos where slug='consultoria'))", (ZAQ,))
        c.execute("insert into contas (id, nome, razao_social, documento, nicho_id) "
                  "values (%s,'Prime','PRIME LTDA','52.752.898/0001-58',"
                  "(select id from nichos where slug='eventos'))", (PRIME,))
        c.commit()
    yield p
    p.close()


def _ligar(pool, regras=None, ligado=True):
    ctr.salvar_modelo(pool, ZAQ, ctr.modelo_padrao(ctr.MODO_SERVICO),
                      regras if regras is not None else _REGRAS_CHEIAS,
                      pedir_assinatura=ligado)


def _orcamento(pool, *, status="aprovada", anual=False):
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, cnpj, status, numero,
                 modo, itens, setup_centavos, mensal_centavos, primeiro_ano_centavos,
                 pagamento_anual)
               values (%s,'Ana','Clínica Sorriso','11.222.333/0001-44',%s,12,
                 'recorrente',%s::jsonb,800000,210000,3320000,%s) returning id""",
            (ZAQ, status, json.dumps(_ITENS), anual)).fetchone()[0]
        c.commit()
    return oid


def _com_contrato(pool, oid):
    ct = ctr.criar_para_orcamento(pool, ZAQ, oid)
    with pool.connection() as c:
        c.execute("update contratos set token=%s where id=%s", (CT_TOKEN, ct["id"]))
        c.commit()
    return ct


# ------------------------------------------------------------ a chave por conta

def test_a_chave_nasce_desligada_e_nada_muda(pool):
    """Sem ligar, a proposta aprovada NÃO vira contrato — fecha pelo botão, como
    sempre fechou. Vale inclusive pra ZAQ: quem liga é o dono."""
    oid = _orcamento(pool)
    assert ctr.conta_tem_contrato(pool, ZAQ) is False
    assert ctr.criar_para_orcamento(pool, ZAQ, oid) is None
    assert ctr.exige_assinatura_do_orcamento(pool, ZAQ, oid) is False


def test_ligada_a_proposta_aprovada_vira_contrato_e_prende_o_fechamento(pool):
    _ligar(pool)
    oid = _orcamento(pool)
    assert ctr.conta_tem_contrato(pool, ZAQ) is True
    ct = ctr.criar_para_orcamento(pool, ZAQ, oid)
    assert ct and ct["numero"] == 1
    # nasceu contrato: só a assinatura fecha
    assert ctr.exige_assinatura_do_orcamento(pool, ZAQ, oid) is True


def test_proposta_aprovada_antes_de_ligar_continua_fechando_pelo_botao(pool):
    """A chave liga o que vem; não prende o que já estava aprovado sem contrato —
    senão essa proposta esperaria pra sempre uma assinatura que não vai chegar."""
    antiga = _orcamento(pool)
    _ligar(pool)
    assert ctr.exige_assinatura_do_orcamento(pool, ZAQ, antiga) is False


def test_na_prime_o_contrato_continua_sendo_do_nicho(pool):
    assert ctr.conta_tem_contrato(pool, PRIME) is True
    assert ctr.modo_da_conta(pool, PRIME) == ctr.MODO_LOCACAO
    assert ctr.modo_da_conta(pool, ZAQ) == ctr.MODO_SERVICO


def test_salvar_sem_a_chave_nao_mexe_nela(pool):
    """A tela de eventos não manda o campo — e não pode desligar nada."""
    _ligar(pool)
    ctr.salvar_modelo(pool, ZAQ, ctr.modelo_padrao(ctr.MODO_SERVICO), _REGRAS_CHEIAS)
    assert ctr.pede_assinatura_servico(pool, ZAQ) is True


def test_o_fechamento_pergunta_pelo_orcamento_e_nao_pelo_nicho():
    """A trava de `fechar_orcamento` passou a ser a do orçamento: é ela que sabe
    que no recorrente o contrato nascido prende o fechamento."""
    import inspect

    from finance import vendas
    fonte = inspect.getsource(vendas.fechar_orcamento)
    assert "ctr.exige_assinatura_do_orcamento(pool, conta_id, int(orcamento_id))" in fonte


# ------------------------------------------------ número em branco não vai pro cliente

def test_o_modelo_nasce_com_os_numeros_da_casa_em_branco():
    r = ctr.regras_padrao(ctr.MODO_SERVICO)
    for k in ("fidelidade_meses", "dia_vencimento", "indice_reajuste",
              "aviso_previo_dias", "multa_rescisao", "implantacao_dias",
              "suporte_horario", "setup_parcelas"):
        assert r[k] == "", k
    # o teto do CDC e da prática bancária pode vir pronto
    assert r["multa_atraso_pct"] == 2 and r["juros_mora_pct_mes"] == 1


def test_nao_liga_com_numero_em_branco():
    pend = ctr.pendencias_pra_ligar(ctr.modelo_padrao(ctr.MODO_SERVICO), {},
                                    {"razao_social": "ZAQ LTDA", "documento": "1",
                                     "cidade": "Teresina", "uf": "PI"})
    campos = {p["campo"] for p in pend}
    assert "regra.fidelidade_meses" in campos and "regra.dia_vencimento" in campos
    # e diz ONDE consertar, não só o nome do campo
    assert all("Números da casa" in p["detalhe"] for p in pend if p["campo"].startswith("regra."))


def test_com_tudo_preenchido_nao_ha_pendencia():
    pend = ctr.pendencias_pra_ligar(ctr.modelo_padrao(ctr.MODO_SERVICO), _REGRAS_CHEIAS,
                                    {"razao_social": "ZAQ LTDA", "documento": "1",
                                     "cidade": "Teresina", "uf": "PI"})
    assert pend == []


def test_a_folha_nao_deixa_assinar_com_numero_em_branco(pool):
    _ligar(pool, regras={"fidelidade_meses": "12"})    # o resto em branco
    oid = _orcamento(pool)
    _com_contrato(pool, oid)
    d = cp.carregar(CT_TOKEN, pool)
    assert d["servico"] is True
    assert "regra.dia_vencimento" in d["faltas"]
    assert d["pode_assinar"] is False


def test_com_os_numeros_preenchidos_assina(pool):
    _ligar(pool)
    oid = _orcamento(pool)
    _com_contrato(pool, oid)
    d = cp.carregar(CT_TOKEN, pool)
    assert d["faltas"] == []
    assert d["pode_assinar"] is True


# ------------------------------------------------------------------ o texto

def test_o_contrato_diz_os_numeros_do_orcamento(pool):
    _ligar(pool)
    oid = _orcamento(pool, anual=True)
    _com_contrato(pool, oid)
    texto = " ".join(c["corpo"] for c in cp.carregar(CT_TOKEN, pool)["clausulas"])
    assert "Agente de Atendimento e CRM / Leads" in texto
    assert "R$ 8.000,00" in texto          # implantação
    assert "R$ 2.100,00" in texto          # mensalidade (já com o -15% do anual)
    assert "R$ 33.200,00" in texto         # total do 1º ano
    assert "anual, com 15% de desconto" in texto
    assert "12 meses" in texto and "IPCA" in texto and "30%" in texto


def test_no_mensal_a_forma_e_mensal(pool):
    _ligar(pool)
    oid = _orcamento(pool, anual=False)
    _com_contrato(pool, oid)
    texto = " ".join(c["corpo"] for c in cp.carregar(CT_TOKEN, pool)["clausulas"])
    assert "na forma de pagamento mensal" in texto


@pytest.mark.parametrize("festa", ["evento", "festa", "convidados", "locação",
                                   "LOCATÁRIO", "sinal", "reserva da data"])
def test_nada_de_festa_no_texto_padrao(festa):
    texto = json.dumps(ctr.modelo_padrao(ctr.MODO_SERVICO), ensure_ascii=False).lower()
    assert festa.lower() not in texto


def test_a_paleta_de_campos_do_servico_nao_tem_evento():
    campos = {c["campo"] for c in ctr.campos_disponiveis([{"slug": "x", "nome": "X"}],
                                                         ctr.MODO_SERVICO)}
    assert not any(c.startswith(("evento.", "preco.")) for c in campos)
    assert {"valor.setup", "valor.mensal", "valor.itens", "regra.fidelidade_meses"} <= campos


def test_todo_campo_do_modelo_padrao_existe_na_paleta():
    """Campo citado no texto padrão e ausente da paleta seria falta que o dono
    não tem como descobrir de onde vem."""
    paleta = {c["campo"] for c in ctr.campos_disponiveis(None, ctr.MODO_SERVICO)}
    usados = set(ctr.campos_usados(ctr.modelo_padrao(ctr.MODO_SERVICO)))
    assert usados <= paleta, usados - paleta


def test_a_folha_do_servico_nao_fala_de_evento(pool, monkeypatch):
    monkeypatch.setattr(cp, "get_pool", lambda: pool)
    _ligar(pool)
    oid = _orcamento(pool)
    _com_contrato(pool, oid)
    html = cp.contrato_publico(None, CT_TOKEN).body.decode()
    assert "Contrato de prestação de serviços" in html
    assert "Contratada (prestadora)" in html
    assert "Serviços contratados" in html and "Agente de Atendimento" in html
    for festa in ("Convidados", "Horário", "locadora", "locatário", "utilização excedente"):
        assert festa not in html, festa


# ------------------------------------------------------------ a Prime não muda

def test_o_contexto_da_locacao_sai_igual():
    """As regras passaram a ser montadas por laço; o contrato da Prime tem que
    ler exatamente o que lia."""
    ctx = ctr.contexto(orcamento={"setup_centavos": 1000000}, modelo={"regras": {}})
    assert ctx["regra"] == {
        "sinal_pct": "30%", "multa_cancelamento": "30%", "taxa_reagendamento": "10%",
        "duracao_horas": "5", "tolerancia_min": "30", "quitacao_dias": "7",
        "reagenda_dias": "30", "reagenda_prazo": "180", "retirada_horas": "48",
        "acesso_montagem": "10h00", "multa_atraso_pct": "2%", "juros_mora_pct_mes": "1%"}
    assert ctx["valor"]["entrada"] == "R$ 3.000,00"
    assert "setup" not in ctx["valor"]            # o dinheiro do serviço não vaza


def test_o_modelo_padrao_da_locacao_nao_mudou():
    assert ctr.modelo_padrao()[0]["titulo"] == "Cláusula 1 — Do objeto"
    assert "locação temporária do espaço" in ctr.modelo_padrao()[0]["corpo"]


# ------------------------------------------------------ o card na tela do dono

def test_o_card_do_recorrente_e_o_de_servico_e_sem_aditivo():
    """"Contrato sem o aditivo ainda" (dono, 23/09/2026): o card do contrato
    aparece no recorrente, o do termo aditivo continua só no evento."""
    from finance import icones_servico as ics
    from web import painel_servicos as ps  # noqa: F401 — registra "servicos"
    from web.portal import _env
    base = dict(empresa_nome="ZAQ", tem_pj=True, vende_servico=True, pode_contrato=True,
                ve_todos=True, tipo_padrao="pj", tipos_evento=[], tipos_contrato=[],
                local_padrao="", icones_paleta=ics.paleta())
    rec = _env.get_template("servicos").render(servico_avulso=False, **base)
    ev = _env.get_template("servicos").render(servico_avulso=True, **base)
    assert 'id="ct-card"' in rec and "Contrato de prestação de serviços" in rec
    assert 'id="ad-card"' not in rec
    assert 'id="ad-card"' in ev and "Contrato de locação" in ev


# ------------------------------------------- o dinheiro que o contrato diz é o cobrado

def test_o_anual_entra_no_total_do_primeiro_ano():
    """A tela dizia R$ 29.420 e o banco gravava R$ 33.200: o servidor ignorava o
    -15% do anual. A ordem é a da tela: desconto da linha, anual, desconto no total."""
    from finance import desconto as dsc
    tot = dsc.totais(_ITENS, fator_mensal=0.85)
    assert tot["setup"] == 800000
    assert tot["mensal"] == 178500                 # 2.100 × 0,85
    assert tot["total"] == 800000 + 178500 * 12    # R$ 29.420
    # sem anual, o de sempre
    assert dsc.totais(_ITENS)["total"] == 3320000


def test_as_pontas_liquidas_levam_todos_os_descontos():
    from finance import desconto as dsc
    itens = [dict(_ITENS[0], desc_tipo="pct", desc_val=10), _ITENS[1]]
    tot = dsc.totais(itens, tipo="pct", pct=5, fator_mensal=0.85)
    # linha: 4.500→4.050 e 1.200→1.080; anual: (1.080+900)×0,85 = 1.683; total -5%
    assert tot["mensal"] == round(168300 * 0.95)
    assert tot["setup"] == round((405000 + 350000) * 0.95)


def test_o_contrato_le_as_pontas_liquidas(pool):
    """Quando o orçamento tem as pontas líquidas (311), é delas que sai o número
    do contrato — o mesmo que `fechar_orcamento` usa pro título."""
    _ligar(pool)
    oid = _orcamento(pool)
    with pool.connection() as c:
        c.execute("alter table orcamentos add column setup_liquido_centavos bigint, "
                  "add column mensal_liquido_centavos bigint")
        c.execute("update orcamentos set setup_liquido_centavos=700000, "
                  "mensal_liquido_centavos=150000 where id=%s", (oid,))
        c.commit()
    _com_contrato(pool, oid)
    d = cp.carregar(CT_TOKEN, pool)
    texto = " ".join(c["corpo"] for c in d["clausulas"])
    assert "R$ 7.000,00" in texto and "R$ 1.500,00" in texto
    assert d["setup"] == "R$ 7.000,00" and d["mensal"] == "R$ 1.500,00"


def test_o_titulo_do_recorrente_sai_do_liquido():
    import inspect

    from finance import vendas
    fonte = inspect.getsource(vendas.fechar_orcamento)
    assert "'mensal_liquido_centavos')::bigint" in fonte
    assert "'setup_liquido_centavos')::bigint" in fonte
