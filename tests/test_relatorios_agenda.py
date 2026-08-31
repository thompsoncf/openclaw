"""Relatórios > Agenda: a Agenda (web/painel_agenda.py) só mostra o que vem —
mês corrente e os próximos compromissos. Esta aba fecha o período: quantos
eventos, quantos viraram presença, quantos não aconteceram e quantos foram
cancelados. Mesmo template genérico de Relatórios (colunas/linhas/métricas),
nenhuma coluna nova no banco — `status`, `desfecho`, `tipo`, `tipo_evento`,
`convidados` e `sinal_centavos` já existem desde as migrações 098-179.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint,
  cliente text, empresa text, evento_agenda_id bigint);
create table prospeccao (id bigserial primary key, conta_id bigint,
  contato text, empresa text not null default 'Empresa');
create table eventos_agenda (id bigserial primary key, conta_id bigint,
  membro_id bigint, titulo text not null, inicio timestamptz not null,
  tipo text default 'pessoal', tipo_evento text, status text default 'ativo',
  desfecho text, convidados int, sinal_centavos int, prospeccao_id bigint,
  cliente_id bigint, sem_cliente boolean not null default false,
  criado_em timestamptz default now());
create table pessoas (id bigserial primary key, nome text, celular text, cpf text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, ativo boolean not null default true);
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_agenda_test"
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
    with pool.connection() as c:
        c.execute("truncate contas, membros, orcamentos, prospeccao, eventos_agenda, clientes, pessoas restart identity")
        conta = c.execute("insert into contas (nome) values ('Prime Eventos') returning id").fetchone()[0]
        jacqueline = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') "
                               "returning id", (conta,)).fetchone()[0]
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro') "
                          "returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "jacqueline": jacqueline, "pedro": pedro}


def _evento(pool, conta, *, titulo="Compromisso", inicio=None, tipo="pessoal",
           tipo_evento=None, status="ativo", desfecho=None, convidados=None,
           sinal=None, membro_id=None, prospeccao_id=None):
    inicio = inicio or datetime.now(timezone.utc)
    with pool.connection() as c:
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, tipo,
                 tipo_evento, status, desfecho, convidados, sinal_centavos, prospeccao_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, membro_id, titulo, inicio, tipo, tipo_evento, status, desfecho,
             convidados, sinal, prospeccao_id),
        ).fetchone()[0]
        c.commit()
    return eid


def _orc(pool, conta, evento_id, *, cliente=None, empresa=None):
    with pool.connection() as c:
        c.execute(
            "insert into orcamentos (conta_id, cliente, empresa, evento_agenda_id) "
            "values (%s,%s,%s,%s)", (conta, cliente, empresa, evento_id),
        )
        c.commit()


def _lead(pool, conta, *, contato=None, empresa="Empresa Lead"):
    with pool.connection() as c:
        lid = c.execute(
            "insert into prospeccao (conta_id, contato, empresa) values (%s,%s,%s) returning id",
            (conta, contato, empresa),
        ).fetchone()[0]
        c.commit()
    return lid


# --------------------------------------------------------------------- lista
def test_lista_tudo_por_padrao_sem_filtro_de_status(pool, cen):
    _evento(pool, cen["conta"], status="ativo")
    _evento(pool, cen["conta"], status="pre_reservado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert len(dados["linhas"]) == 3


def test_filtro_de_status_corta_a_tabela(pool, cen):
    _evento(pool, cen["conta"], status="ativo")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "cancelado", "", "")
    assert len(dados["linhas"]) == 1
    assert dados["linhas"][0]["status"] == "Cancelado"


def test_metricas_de_topo_ignoram_o_filtro_de_status_de_proposito(pool, cen):
    """Mesma garantia de Orçamentos: o filtro corta a TABELA, as métricas
    continuam mostrando a distribuição inteira do período."""
    _evento(pool, cen["conta"], status="ativo", desfecho="realizado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "cancelado", "", "")
    assert len(dados["linhas"]) == 1                       # tabela: só o cancelado
    assert dados["metricas"][0] == ("Eventos no período", "2")  # métrica: os dois


# ------------------------------------------------------------------- métricas
def test_realizados_nao_realizados_e_cancelados_contam_certo(pool, cen):
    _evento(pool, cen["conta"], desfecho="realizado")
    _evento(pool, cen["conta"], desfecho="realizado")
    _evento(pool, cen["conta"], desfecho="nao_realizado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    metricas = dict(dados["metricas"])
    assert metricas["Eventos no período"] == "4"
    assert metricas["Realizados"] == "2 · 50%"
    assert metricas["Não realizados"] == "1 · 25%"
    assert metricas["Cancelados"] == "1 · 25%"


def test_sem_evento_nenhum_percentual_nao_quebra_com_divisao_por_zero(pool, cen):
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    metricas = dict(dados["metricas"])
    assert metricas["Eventos no período"] == "0"
    assert metricas["Realizados"] == "0 · 0%"


def test_sinal_soma_no_total_e_na_metrica(pool, cen):
    _evento(pool, cen["conta"], sinal=50000)
    _evento(pool, cen["conta"], sinal=30000)
    _evento(pool, cen["conta"], sinal=None)  # sem sinal: não quebra a soma
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["total_centavos"] == 80000
    assert dict(dados["metricas"])["Sinal no período"] == "R$ 800,00"


# ------------------------------------------------------------------- colunas
def test_evento_usa_tipo_evento_quando_tem_senao_cai_pro_titulo(pool, cen):
    _evento(pool, cen["conta"], titulo="Compromisso qualquer", tipo_evento="Casamento")
    _evento(pool, cen["conta"], titulo="Visita", tipo_evento=None)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    eventos = {l["evento"] for l in dados["linhas"]}
    assert eventos == {"Casamento", "Visita"}


def test_convidados_nulo_mostra_travessao(pool, cen):
    _evento(pool, cen["conta"], convidados=None)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["convidados"] == "—"


def test_cliente_vem_do_orcamento_vinculado(pool, cen):
    eid = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], eid, empresa="Isabela Silva Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Isabela Silva Mendes"


def test_sem_orcamento_vinculado_cliente_e_travessao(pool, cen):
    """Visita e compromisso pessoal não nascem de orçamento — não pode dar
    erro, tem que mostrar "—" como todo campo sem dado neste relatório."""
    _evento(pool, cen["conta"], tipo="pessoal")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "—"


def test_cliente_do_orcamento_prioriza_empresa_sobre_cliente(pool, cen):
    """Mesma regra `empresa or cliente` de _dados_orcamentos/_dados_contratos."""
    eid = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], eid, cliente="86994160050", empresa="Rafael Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Rafael Mendes"


def test_cliente_vem_do_lead_quando_a_visita_nasceu_de_prospeccao(pool, cen):
    """finance.cockpit.agendar_visita liga prospeccao_id na visita assim que
    marca — sem este join a maioria das "Visita — Fulano" ficava com Cliente
    vazio em produção (relato do dono, 26/08)."""
    lead = _lead(pool, cen["conta"], contato="Fulano de Tal")
    _evento(pool, cen["conta"], titulo="Visita — Fulano de Tal", prospeccao_id=lead)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Fulano de Tal"


def test_cliente_do_lead_cai_pra_empresa_quando_sem_contato(pool, cen):
    lead = _lead(pool, cen["conta"], contato=None, empresa="Buffet da Praça")
    _evento(pool, cen["conta"], prospeccao_id=lead)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Buffet da Praça"


def test_orcamento_manda_quando_o_evento_tem_os_dois(pool, cen):
    """O orçamento é o registro mais firme — o lead pode ter sido reatribuído
    ou desqualificado depois, o orçamento aprovado não."""
    lead = _lead(pool, cen["conta"], contato="Nome do Lead")
    eid = _evento(pool, cen["conta"], prospeccao_id=lead)
    _orc(pool, cen["conta"], eid, empresa="Nome do Orçamento")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Nome do Orçamento"


def test_reuniao_marcada_a_mao_sem_lead_e_sem_orcamento_continua_travessao(pool, cen):
    """finance.agenda_tools.marcar_evento (o "REUNIÃO COM ENGENHEIRA" típico)
    não liga prospeccao_id nenhum — o nome, se existe, está só no título
    digitado à mão, sem onde puxar. "—" aqui é o esperado, não bug."""
    _evento(pool, cen["conta"], titulo="Reunião com engenheira")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "—"


def test_busca_acha_pelo_nome_do_lead_quando_nao_tem_orcamento(pool, cen):
    lead = _lead(pool, cen["conta"], contato="Talila Arrais")
    _evento(pool, cen["conta"], prospeccao_id=lead)
    _evento(pool, cen["conta"])  # sem lead nenhum, não pode aparecer na busca
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "talila")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["cliente"] == "Talila Arrais"


# -------------------------------------------------------------------- filtros
def test_filtro_por_vendedor_via_membro_id(pool, cen):
    _evento(pool, cen["conta"], membro_id=cen["jacqueline"])
    _evento(pool, cen["conta"], membro_id=cen["pedro"])
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", str(cen["jacqueline"]), "")
    assert len(dados["linhas"]) == 1


def test_vendedor_invalido_nao_quebra_a_consulta(pool, cen):
    """Um valor não numérico no filtro (query param adulterado) não pode
    derrubar o relatório — só é tratado como "sem filtro"."""
    _evento(pool, cen["conta"])
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "abc", "")
    assert len(dados["linhas"]) == 1


def test_busca_por_cliente_do_orcamento_vinculado(pool, cen):
    e1 = _evento(pool, cen["conta"])
    e2 = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], e1, empresa="Talila Arrais")
    _orc(pool, cen["conta"], e2, empresa="Rafael Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "talila")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["cliente"] == "Talila Arrais"


def test_periodo_filtra_por_data_de_inicio_do_evento(pool, cen):
    dentro = datetime.now(timezone.utc)
    fora = dentro - timedelta(days=200)
    _evento(pool, cen["conta"], inicio=dentro)
    _evento(pool, cen["conta"], inicio=fora)
    dados = rel._dados_agenda(pool, cen["conta"], "mes", "", "", "")
    assert len(dados["linhas"]) == 1


def test_outra_conta_nao_vaza_no_relatorio(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    _evento(pool, cen["conta"])
    assert rel._dados_agenda(pool, outra, "todos", "", "", "")["linhas"] == []


# ---------------------------------------------------------- aba e formulário
def test_aba_agenda_esta_registrada_em_tipos():
    assert "agenda" in rel.TIPOS
    assert rel.TIPOS["agenda"]["label"] == "Agenda"


# =========================================================================
# OS FILTROS DE 31/08/2026 — espécie, período específico e a data completa
#
# Quatro ajustes pedidos pelo dono. Dois deles carregavam problema maior que o
# pedido: o período nunca alcançava o futuro (38 dos 60 compromissos da Prime
# estavam lá) e a data saía sem ano E em UTC, três horas à frente do que a tela
# de Agenda mostra pra mesma festa.
# =========================================================================

def _daqui(dias, hora=20):
    """Um instante no futuro, no fuso de Brasília — que é como o salão pensa."""
    return datetime.combine(HOJE + timedelta(days=dias),
                            datetime.min.time()).replace(tzinfo=rel._ag.BRT) \
        + timedelta(hours=hora)


# ------------------------------------------------------------------ espécie

def test_visita_e_evento_somados_dao_sempre_o_total(pool, cen):
    """A garantia que sustenta a régua escolhida. Se um dia alguém trocar
    "complemento da visita" por `tipo_evento is not null`, este teste cai."""
    _evento(pool, cen["conta"], titulo="Visita — Erys")
    _evento(pool, cen["conta"], titulo="VISITA TÉCNICA - PEDRO")
    _evento(pool, cen["conta"], titulo="Casamento", tipo_evento="Casamento")
    _evento(pool, cen["conta"], titulo="aniversario Leda")      # festa SEM tipo
    _evento(pool, cen["conta"], titulo="REUNIÃO COM ENGENHEIRA")
    n = lambda esp: len(rel._dados_agenda(  # noqa: E731
        pool, cen["conta"], "todos", "", "", "", especie=esp)["linhas"])
    assert n("visita") == 2
    assert n("evento") == 3
    assert n("visita") + n("evento") == n("") == 5


def test_festa_sem_tipo_preenchido_continua_sendo_evento(pool, cen):
    """O caso que motivou a régua: 12 das 43 festas da Prime foram digitadas no
    título e estão com `tipo_evento` vazio. Pela régua óbvia
    (`tipo_evento is not null`) elas sumiriam dos DOIS filtros."""
    _evento(pool, cen["conta"], titulo="15 Anos - Fernanda")
    _evento(pool, cen["conta"], titulo="Formatura - Beatriz")
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="evento")
    assert len(d["linhas"]) == 2
    assert rel._dados_agenda(pool, cen["conta"], "todos", "", "", "",
                             especie="visita")["linhas"] == []


def test_compromisso_chamado_visita_mas_com_tipo_de_festa_e_evento(pool, cen):
    """`tipo_evento` preenchido desempata: é a FESTA do cliente, não a visita
    dele ao espaço — mesmo que alguém tenha escrito "Visita" no título."""
    _evento(pool, cen["conta"], titulo="Visita de formatura", tipo_evento="Formatura")
    assert rel._dados_agenda(pool, cen["conta"], "todos", "", "", "",
                             especie="visita")["linhas"] == []
    assert len(rel._dados_agenda(pool, cen["conta"], "todos", "", "", "",
                                 especie="evento")["linhas"]) == 1


def test_visita_cancelada_continua_aparecendo_na_especie_visita(pool, cen):
    """O Funil exclui visita cancelada de propósito (não foi agendada pra valer).
    Aqui NÃO: o relatório fecha o período, e quem quiser cortar tem o filtro de
    Status. Somar as duas regras num lugar só faria a soma parar de bater."""
    _evento(pool, cen["conta"], titulo="Visita — Ana", status="cancelado")
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="visita")
    assert len(d["linhas"]) == 1


def test_especie_invalida_cai_em_todos(pool, cen):
    _evento(pool, cen["conta"], titulo="Visita — Ana")
    _evento(pool, cen["conta"], titulo="Casamento", tipo_evento="Casamento")
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="banana")
    assert len(d["linhas"]) == 2
    assert d["filtro_extra"]["especie_sel"] == ""


# --------------------------------------------------- colunas e métricas por espécie

def test_sem_especie_a_tela_continua_a_de_sempre(pool, cen):
    """Quem abre a aba sem escolher nada tem que ver exatamente o que via antes
    de a espécie existir — mesmas oito colunas, mesmas cinco métricas."""
    _evento(pool, cen["conta"], sinal=50000)
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert [c["chave"] for c in d["colunas"]] == [
        "inicio", "evento", "cliente", "tipo", "status", "desfecho",
        "convidados", "sinal_centavos"]
    assert [m[0] for m in d["metricas"]] == [
        "Eventos no período", "Realizados", "Não realizados", "Cancelados",
        "Sinal no período"]
    assert d["col_total"] == "sinal_centavos"


def test_visita_nao_mostra_sinal_nem_convidados_e_mostra_vendedor(pool, cen):
    """Visita nunca segura data (`agendar_visita` não passa sinal), então a
    coluna era sempre R$ 0,00 — metade da tela para ler nada."""
    _evento(pool, cen["conta"], titulo="Visita — Erys", membro_id=cen["pedro"])
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="visita")
    chaves = [c["chave"] for c in d["colunas"]]
    assert "sinal_centavos" not in chaves and "convidados" not in chaves
    assert "vendedor" in chaves
    assert d["col_total"] is None, "sem coluna de dinheiro, não há linha de total"
    assert d["linhas"][0]["vendedor"] == "Pedro"
    assert [m[0] for m in d["metricas"]] == [
        "Visitas no período", "Aconteceram", "Não apareceram", "Sem resposta",
        "Vindas de lead"]


def test_metricas_de_visita_contam_comparecimento_e_origem(pool, cen):
    lead = _lead(pool, cen["conta"], contato="Nayara")
    _evento(pool, cen["conta"], titulo="Visita — Nayara", desfecho="realizado",
            prospeccao_id=lead, inicio=_daqui(-3))
    _evento(pool, cen["conta"], titulo="Visita — Erys", desfecho="nao_realizado",
            inicio=_daqui(-2))
    _evento(pool, cen["conta"], titulo="Visita — Maysa", inicio=_daqui(-1))  # passou, sem resposta
    _evento(pool, cen["conta"], titulo="Visita — Rita", inicio=_daqui(+5))   # ainda vem
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="visita")
    m = dict(d["metricas"])
    assert m["Visitas no período"] == "4"
    assert m["Aconteceram"].startswith("1 ")
    assert m["Não apareceram"].startswith("1 ")
    assert m["Sem resposta"].startswith("1 "), "a que ainda vem não está sem resposta"
    assert m["Vindas de lead"].startswith("1 ")


def test_visita_futura_nao_e_marcada_como_sem_resposta(pool, cen):
    """Antes da data não há o que responder. Pintar de âmbar o que ainda vai
    acontecer seria um alerta que não pede nada de ninguém."""
    _evento(pool, cen["conta"], titulo="Visita — Rita", inicio=_daqui(+5))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="visita")
    assert d["linhas"][0]["desfecho"] == "—"
    _evento(pool, cen["conta"], titulo="Visita — Ana", inicio=_daqui(-5))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="visita")
    passada = [l for l in d["linhas"] if "Ana" in l["evento"]][0]
    assert passada["desfecho"] == "Sem resposta"


def test_evento_mostra_o_tipo_da_festa_e_marca_quem_esta_sem(pool, cen):
    _evento(pool, cen["conta"], titulo="Casamento", tipo_evento="Casamento",
            convidados=200, sinal=150000)
    _evento(pool, cen["conta"], titulo="aniversario Leda", convidados=60)
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie="evento")
    porta = {l["evento"]: l for l in d["linhas"]}
    assert porta["Casamento"]["tipo_evento"] == "Casamento"
    assert porta["Casamento"]["tipo_evento_cor"] == "neutro"
    assert porta["aniversario Leda"]["tipo_evento"] == "sem tipo"
    assert porta["aniversario Leda"]["tipo_evento_cor"] == "aviso"
    assert d["filtro_extra"]["sem_tipo"] == 1
    m = dict(d["metricas"])
    assert m["Convidados"] == "260"
    assert m["Eventos no período"] == "2"


def test_aviso_de_sem_tipo_so_aparece_na_especie_evento(pool, cen):
    """Na aba "Todos" ele diria respeito a linhas que nem são festa."""
    _evento(pool, cen["conta"], titulo="aniversario Leda")
    for esp in ("", "visita"):
        d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "", especie=esp)
        assert d["filtro_extra"]["sem_tipo"] == 0


def test_status_e_vendedor_continuam_sendo_so_recorte(pool, cen):
    """Espécie muda O QUE se conta; status e vendedor só recortam a tabela. Foi
    assim que a aba nasceu e continua sendo."""
    _evento(pool, cen["conta"], titulo="Casamento", tipo_evento="Casamento",
            status="ativo")
    _evento(pool, cen["conta"], titulo="Formatura", tipo_evento="Formatura",
            status="cancelado")
    d = rel._dados_agenda(pool, cen["conta"], "todos", "cancelado", "", "",
                          especie="evento")
    assert len(d["linhas"]) == 1
    assert dict(d["metricas"])["Eventos no período"] == "2"


# -------------------------------------------------------------------- período

def test_este_mes_vai_ate_o_fim_do_mes_so_com_ate_o_fim():
    """A decisão do dono em 31/08/2026, medida direto no cálculo do intervalo —
    sem depender do dia em que a suíte roda. Numa agenda, parar em hoje esconde
    justamente a festa que ainda vai acontecer; num relatório de histórico, ir
    além de hoje não mostraria linha nenhuma."""
    assert rel._intervalo("mes")[1] == HOJE
    assert rel._intervalo("mes", ate_o_fim=True)[1] == rel._fim_do_mes(HOJE)
    assert rel._intervalo("ano")[1] == HOJE
    assert rel._intervalo("ano", ate_o_fim=True)[1] == date(HOJE.year, 12, 31)


def test_so_a_pilula_agenda_oferece_o_periodo_livre(pool, cen):
    """As outras oito abas chamam `_intervalo(periodo)` sem `de`/`ate`: oferecer
    "Período específico" nelas devolveria o mês corrente calado."""
    assert "personalizado" in dict(rel.periodos_da_aba("agenda"))
    assert "prox30" in dict(rel.periodos_da_aba("agenda"))
    for aba in ("vendas", "orcamentos", "contratos", "comissao"):
        assert "personalizado" not in dict(rel.periodos_da_aba(aba)), aba
        assert "prox30" not in dict(rel.periodos_da_aba(aba)), aba


def test_a_agenda_pede_o_mes_inteiro_de_verdade(pool, cen):
    """E o `ate_o_fim` chega mesmo até a consulta — o teste acima mede o cálculo,
    este mede a aba. Só roda quando ainda sobra mês."""
    fim_do_mes = rel._fim_do_mes(HOJE)
    if fim_do_mes == HOJE:                 # rodando no último dia do mês
        pytest.skip("hoje já é o último dia do mês — não há 'resto do mês'")
    _evento(pool, cen["conta"], titulo="Festa no fim do mês",
            inicio=datetime.combine(fim_do_mes, datetime.min.time())
            .replace(tzinfo=rel._ag.BRT) + timedelta(hours=20))
    d = rel._dados_agenda(pool, cen["conta"], "mes", "", "", "")
    assert len(d["linhas"]) == 1, "o resto do mês tem que aparecer"


def test_periodo_especifico_recorta_exatamente_o_pedido(pool, cen):
    _evento(pool, cen["conta"], titulo="Antes", inicio=_daqui(+10))
    _evento(pool, cen["conta"], titulo="Dentro", inicio=_daqui(+40))
    _evento(pool, cen["conta"], titulo="Depois", inicio=_daqui(+80))
    de = (HOJE + timedelta(days=30)).isoformat()
    ate = (HOJE + timedelta(days=50)).isoformat()
    d = rel._dados_agenda(pool, cen["conta"], "personalizado", "", "", "",
                          de=de, ate=ate)
    assert [l["evento"] for l in d["linhas"]] == ["Dentro"]


def test_periodo_especifico_alcanca_o_ano_que_vem(pool, cen):
    """O que nenhum preset alcançava: dezembro do ano que vem, sozinho."""
    _evento(pool, cen["conta"], titulo="Formatura 2027", inicio=_daqui(+400))
    _evento(pool, cen["conta"], titulo="Festa de agora", inicio=_daqui(+1))
    de = (HOJE + timedelta(days=390)).isoformat()
    ate = (HOJE + timedelta(days=410)).isoformat()
    d = rel._dados_agenda(pool, cen["conta"], "personalizado", "", "", "",
                          de=de, ate=ate)
    assert [l["evento"] for l in d["linhas"]] == ["Formatura 2027"]


def test_datas_invertidas_sao_trocadas_em_vez_de_zerar(pool, cen):
    """De 31/12 até 01/12 é engano de digitação, não pedido de lista vazia."""
    _evento(pool, cen["conta"], titulo="Dentro", inicio=_daqui(+40))
    de = (HOJE + timedelta(days=50)).isoformat()
    ate = (HOJE + timedelta(days=30)).isoformat()
    d = rel._dados_agenda(pool, cen["conta"], "personalizado", "", "", "",
                          de=de, ate=ate)
    assert len(d["linhas"]) == 1


def test_data_torta_cai_no_mes_corrente_e_nao_quebra(pool, cen):
    _evento(pool, cen["conta"], titulo="Deste mês",
            inicio=datetime.combine(HOJE, datetime.min.time())
            .replace(tzinfo=rel._ag.BRT) + timedelta(hours=10))
    d = rel._dados_agenda(pool, cen["conta"], "personalizado", "", "", "",
                          de="banana", ate="")
    assert len(d["linhas"]) == 1


def test_proximos_30_dias_nao_traz_o_passado(pool, cen):
    _evento(pool, cen["conta"], titulo="Semana passada", inicio=_daqui(-7))
    _evento(pool, cen["conta"], titulo="Semana que vem", inicio=_daqui(+7))
    d = rel._dados_agenda(pool, cen["conta"], "prox30", "", "", "")
    assert [l["evento"] for l in d["linhas"]] == ["Semana que vem"]


# ----------------------------------------------------------------------- data

def test_a_data_tem_ano_e_esta_no_fuso_de_brasilia(pool, cen):
    """Os dois defeitos que andavam juntos: sem o ano, "16/01 19:00" não diz de
    qual ano é (a Prime tem festa até outubro/2027); e sem converter o fuso, o
    casamento que a Agenda mostra às 16:00 saía 19:00 aqui — três horas à frente,
    com 2 dos 60 caindo no DIA seguinte."""
    quando = datetime(2026, 12, 19, 21, 30, tzinfo=rel._ag.BRT)
    _evento(pool, cen["conta"], titulo="Casamento", inicio=quando)
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["inicio"] == "19/12/2026 21:30"


def test_festa_da_noite_nao_pula_pro_dia_seguinte(pool, cen):
    """21h30 de sábado é 00h30 de domingo em UTC. Formatar a data sem corrigir o
    fuso deixaria a data errada bem escrita."""
    _evento(pool, cen["conta"], titulo="Formatura",
            inicio=datetime(2026, 12, 5, 22, 0, tzinfo=rel._ag.BRT))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["inicio"].startswith("05/12/2026"), "virou o dia"


# =========================================================================
# CAMADA 1 (31/08/2026) — o compromisso passa a saber DE QUEM é
#
# O relatório mostrava "—" em 51 dos 60 compromissos da Prime, e a causa não era
# o relatório: o formulário de novo compromisso não tinha campo de cliente, e o
# nome acabava dentro do texto do título. `eventos_agenda.cliente_id` (192) é o
# vínculo que faltava, e aqui se mede que ele MANDA sobre as duas deduções que já
# existiam (orçamento e lead).
# =========================================================================

def _cliente(pool, dono, nome, *, pessoa_nome=None):
    """Uma relação em `clientes`, com a identidade em `pessoas` — o modelo da 066.
    `pessoa_nome` diferente do cache é de propósito num dos testes: a leitura tem
    que preferir a identidade."""
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (nome) values (%s) returning id",
                        (pessoa_nome or nome,)).fetchone()[0]
        cid = c.execute("insert into clientes (dono_id, pessoa_id, nome) "
                        "values (%s,%s,%s) returning id", (dono, pid, nome)).fetchone()[0]
        c.commit()
    return cid


def _com_cliente(pool, evento_id, cliente_id):
    with pool.connection() as c:
        c.execute("update eventos_agenda set cliente_id=%s where id=%s",
                  (cliente_id, evento_id))
        c.commit()


def test_cliente_ligado_aparece_no_relatorio(pool, cen):
    """O caso que não existia antes: locação de telefonema, sem orçamento e sem
    lead — exatamente as 41 linhas que apareciam vazias."""
    ev = _evento(pool, cen["conta"], titulo="Locação", tipo_evento="Locação")
    _com_cliente(pool, ev, _cliente(pool, cen["conta"], "Jonas Barreto Castro Neto"))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["cliente"] == "Jonas Barreto Castro Neto"


def test_o_vinculo_manda_sobre_o_orcamento_e_sobre_o_lead(pool, cen):
    """As outras duas fontes são DEDUÇÃO; o cliente_id é escolha de alguém. Com as
    três presentes, quem aparece é o escolhido."""
    lead = _lead(pool, cen["conta"], contato="Nome do lead")
    ev = _evento(pool, cen["conta"], titulo="Casamento", tipo_evento="Casamento",
                 prospeccao_id=lead)
    _orc(pool, cen["conta"], ev, empresa="Nome do orçamento")
    _com_cliente(pool, ev, _cliente(pool, cen["conta"], "Nome escolhido na tela"))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["cliente"] == "Nome escolhido na tela"


def test_sem_vinculo_o_orcamento_e_o_lead_seguem_valendo(pool, cen):
    """A 192 acrescenta uma fonte, não substitui as que já funcionavam."""
    ev1 = _evento(pool, cen["conta"], titulo="Formatura", inicio=_daqui(+1))
    _orc(pool, cen["conta"], ev1, empresa="Colégio Aliança")
    lead = _lead(pool, cen["conta"], contato="Nayara")
    _evento(pool, cen["conta"], titulo="Visita — Nayara", prospeccao_id=lead,
            inicio=_daqui(+2))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    nomes = [l["cliente"] for l in d["linhas"]]
    assert nomes == ["Colégio Aliança", "Nayara"]


def test_le_a_identidade_e_nao_o_cache_do_nome(pool, cen):
    """`clientes.nome` é CACHE; a verdade está em `pessoas`. Corrigir o nome na
    ficha tem que aparecer aqui sem tocar no compromisso."""
    cid = _cliente(pool, cen["conta"], "Nome antigo", pessoa_nome="Nome corrigido")
    ev = _evento(pool, cen["conta"], titulo="Locação", tipo_evento="Locação")
    _com_cliente(pool, ev, cid)
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["cliente"] == "Nome corrigido"


def test_cliente_de_outra_loja_nao_vaza(pool, cen):
    """`cliente_id` não tem FK (a relação pode ser arquivada por uma fusão), então
    o isolamento é do JOIN: `cl.dono_id = e.conta_id`."""
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    ev = _evento(pool, cen["conta"], titulo="Locação", tipo_evento="Locação")
    _com_cliente(pool, ev, _cliente(pool, outra, "Cliente da vizinha"))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert d["linhas"][0]["cliente"] == "—", "puxou cliente de outra conta"


def test_a_busca_do_relatorio_acha_pelo_cliente_ligado(pool, cen):
    _evento(pool, cen["conta"], titulo="Locação A", tipo_evento="Locação",
            inicio=_daqui(+1))
    ev = _evento(pool, cen["conta"], titulo="Locação B", tipo_evento="Locação",
                 inicio=_daqui(+2))
    _com_cliente(pool, ev, _cliente(pool, cen["conta"], "Zenilda Rosa"))
    d = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "Zenilda")
    assert [l["evento"] for l in d["linhas"]] == ["Locação"]
    assert d["linhas"][0]["cliente"] == "Zenilda Rosa"


# =========================================================================
# CAMADA 2 — o nome que ficou preso no título
#
# A régua em si está fixada em tests/test_agenda_nome_no_titulo.py (função pura,
# 51 títulos reais). Aqui se mede o que o RELATÓRIO faz com ela: mostra como
# palpite, nunca como dado, e só enquanto houver pergunta a fazer.
# =========================================================================

def test_sem_vinculo_o_nome_vem_do_titulo_marcado_como_palpite(pool, cen):
    _evento(pool, cen["conta"], titulo="Locação — Jonas Barreto Castro Neto",
            tipo_evento="Locação")
    l = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")["linhas"][0]
    assert l["cliente"] == "Jonas Barreto Castro Neto"
    assert l["cliente_do_titulo"] is True
    assert l["cliente_link"], "sem vínculo, a célula tem que levar pra resolver"


def test_com_vinculo_o_nome_nao_e_palpite_e_a_celula_nao_cobra(pool, cen):
    ev = _evento(pool, cen["conta"], titulo="Locação — Jonas Barreto Castro Neto",
                 tipo_evento="Locação")
    _com_cliente(pool, ev, _cliente(pool, cen["conta"], "Jonas Barreto Castro Neto"))
    l = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")["linhas"][0]
    assert l["cliente"] == "Jonas Barreto Castro Neto"
    assert l["cliente_do_titulo"] is False
    assert l["cliente_link"] is None, "com dono, a linha para de perguntar"


def test_o_nome_do_vendedor_nao_vira_cliente(pool, cen):
    """A armadilha: "VISITA TÉCNICA - PEDRO" é o vendedor. O relatório consulta os
    membros da conta antes de ler os títulos justamente por isto."""
    _evento(pool, cen["conta"], titulo="VISITA TÉCNICA - Pedro")
    l = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")["linhas"][0]
    assert l["cliente"] == "—"
    assert l["cliente_do_titulo"] is False


def test_sem_cliente_marcado_cala_a_linha(pool, cen):
    """Reunião interna não tem dono. Depois de dito, a linha para de cobrar — sem
    isso ela pediria atenção pra sempre, e lista que nunca esvazia ninguém olha."""
    ev = _evento(pool, cen["conta"], titulo="REUNIÃO COM ENGENHEIRA")
    l = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")["linhas"][0]
    assert l["cliente_link"], "antes de marcar, ainda há pergunta"
    with pool.connection() as c:
        c.execute("update eventos_agenda set sem_cliente=true where id=%s", (ev,))
        c.commit()
    l = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")["linhas"][0]
    assert l["cliente_link"] is None
    assert l["cliente"] == "—"


def test_o_palpite_nao_e_gravado_em_lugar_nenhum(pool, cen):
    """Ler não é escrever. O `cliente_id` só nasce quando o dono confirma."""
    ev = _evento(pool, cen["conta"], titulo="Casamento — Eva da Silva Fontoura",
                 tipo_evento="Casamento")
    rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    with pool.connection() as c:
        cid, sem = c.execute("select cliente_id, sem_cliente from eventos_agenda "
                             "where id=%s", (ev,)).fetchone()
    assert cid is None and sem is False
