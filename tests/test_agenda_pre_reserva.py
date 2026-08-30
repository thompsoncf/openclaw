"""Pré-reserva de data: a data só é do cliente depois do sinal.

O gerador de parcelas do orçamento de evento já escrevia, na primeira linha,
"Sinal — confirma a reserva da data". Mas a aprovação criava compromisso FIRME na
agenda sem olhar pagamento nenhum: a folha prometia uma coisa e o sistema fazia
outra. Agora a aprovação cria uma PRÉ-RESERVA com prazo; quem confirma o sinal é
o dono, e o prazo vencido libera a data sozinho.

Aqui testa-se o modelo (finance/agenda.py) e o job de expiração
(finance/lembretes.py). O lado do orçamento/proposta está em
tests/test_orcamento_evento.py, que já monta o schema de `orcamentos`.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance import lembretes as lb
from finance import notificar


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((migr / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _isola(pool):
    """lb.rodar() varre TODAS as contas (certo em produção): limpa entre casos."""
    with pool.connection() as c:
        c.execute("truncate table lembretes_enviados, agenda_config, eventos_agenda "
                  "restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Buffet Teste') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def enviados(monkeypatch):
    saidas = []
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, conta_id, texto: (saidas.append((conta_id, texto)) or True))
    return saidas


def _status(pool, ev_id):
    with pool.connection() as c:
        return c.execute("select status, pre_reserva_ate from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()


# ------------------------------------------------------------ criar pré-reservado

def test_criar_com_prazo_nasce_pre_reservado(pool, conta_id):
    ate = ag.agora_brt() + timedelta(days=3)
    ev = ag.criar_evento(pool, conta_id, "Casamento — Ana", ag.agora_brt() + timedelta(days=20),
                         pre_reserva_ate=ate)
    st, prazo = _status(pool, ev["id"])
    assert st == ag.PRE_RESERVADO and abs((prazo - ate).total_seconds()) < 2
    # sem prazo, nada muda: compromisso firme, como sempre foi
    ev2 = ag.criar_evento(pool, conta_id, "Reunião", ag.agora_brt() + timedelta(days=1))
    assert _status(pool, ev2["id"]) == ("ativo", None)


def test_pre_reserva_aparece_na_agenda_mas_nao_nos_proximos(pool, conta_id):
    """A distinção que dá sentido ao status novo: a data SEGURADA ocupa o dia (senão
    o dono vende a mesma data duas vezes), mas não é compromisso — não entra na
    lista de "próximos", que é a fonte do lembrete e do resumo do dia."""
    quando = ag.agora_brt() + timedelta(days=5)
    ag.criar_evento(pool, conta_id, "Aniversário — Bia", quando,
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    ag.criar_evento(pool, conta_id, "Degustação", quando + timedelta(hours=3))
    de, ate = quando - timedelta(days=1), quando + timedelta(days=1)
    # o calendário do painel pede explicitamente a data ocupada
    titulos = [e["titulo"] for e in ag.listar_eventos(pool, conta_id, de, ate,
                                                      incluir_pre_reserva=True)]
    assert "Aniversário — Bia" in titulos and "Degustação" in titulos
    # o PADRÃO é só compromisso — é dele que saem lembrete, resumo e aviso
    assert [e["titulo"] for e in ag.listar_eventos(pool, conta_id, de, ate)] == ["Degustação"]
    assert [e["titulo"] for e in ag.proximos(pool, conta_id)] == ["Degustação"]


def test_pre_reserva_nao_vira_lembrete_nem_resumo(pool, conta_id, enviados):
    """A consequência prática do de cima, medida no motor de lembretes: data
    segurada não toca o telefone de ninguém — ela ainda não é compromisso."""
    agora = ag.agora_brt().replace(hour=7, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Festa da Bia", agora + timedelta(minutes=20),
                    pre_reserva_ate=agora + timedelta(days=2))
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=30)
    r = lb.rodar(pool, agora=agora)
    # por chave, não por igualdade do dict inteiro: o retorno do rodar cresce quando
    # entra um lembrete novo (o de aniversário entrou assim), e o que este teste
    # afirma é que a data SEGURADA não gerou resumo nem aviso — não quantos tipos de
    # lembrete o motor conhece.
    assert r["resumo"] == 0 and r["aviso"] == 0 and enviados == []


def test_pre_reserva_fica_fora_do_feed_ics(pool, conta_id):
    """O .ics é o calendário sincronizado do dono (Google/Apple). Data segurada não
    pode entrar lá: aparece pra quem compartilha o calendário como se fosse fechado."""
    ag.criar_evento(pool, conta_id, "Segurada", ag.agora_brt() + timedelta(days=6),
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    ag.criar_evento(pool, conta_id, "Firmada", ag.agora_brt() + timedelta(days=7))
    titulos = [e["titulo"] for e in ag.eventos_para_feed(pool, conta_id)]
    assert titulos == ["Firmada"]


# ------------------------------------------------------------ confirmar / expirar

def test_confirmar_vira_compromisso_firme_e_e_idempotente(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Casamento", ag.agora_brt() + timedelta(days=30),
                         pre_reserva_ate=ag.agora_brt() + timedelta(days=3))
    assert ag.confirmar_pre_reserva(pool, conta_id, ev["id"]) is True
    assert _status(pool, ev["id"]) == ("ativo", None)
    # apertar de novo não faz nada (e não é erro): o botão pode ser clicado 2x
    assert ag.confirmar_pre_reserva(pool, conta_id, ev["id"]) is False


def test_confirmar_nao_atravessa_conta(pool, conta_id):
    """Escopo multi-tenant: conta vizinha não firma a data de ninguém."""
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    ev = ag.criar_evento(pool, conta_id, "Festa", ag.agora_brt() + timedelta(days=10),
                         pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    assert ag.confirmar_pre_reserva(pool, outra, ev["id"]) is False
    assert _status(pool, ev["id"])[0] == ag.PRE_RESERVADO


def test_expirar_solta_so_o_prazo_vencido(pool, conta_id):
    agora = ag.agora_brt()
    vencida = ag.criar_evento(pool, conta_id, "Vencida", agora + timedelta(days=10),
                              pre_reserva_ate=agora - timedelta(minutes=1))
    corrente = ag.criar_evento(pool, conta_id, "No prazo", agora + timedelta(days=11),
                               pre_reserva_ate=agora + timedelta(days=1))
    firme = ag.criar_evento(pool, conta_id, "Firme", agora + timedelta(days=12))
    soltas = ag.expirar_pre_reservas(pool, agora)
    assert [e["titulo"] for e in soltas] == ["Vencida"]
    assert soltas[0]["conta_id"] == conta_id
    assert _status(pool, vencida["id"])[0] == "cancelado"
    assert _status(pool, corrente["id"])[0] == ag.PRE_RESERVADO
    assert _status(pool, firme["id"])[0] == "ativo"
    # segunda passada não redevolve (senão o dono seria avisado a cada 2 min)
    assert ag.expirar_pre_reservas(pool, agora) == []


def test_expirar_libera_a_data_pro_proximo(pool, conta_id):
    """O ponto da expiração: depois dela a data não conta mais como ocupada."""
    agora = ag.agora_brt()
    quando = agora + timedelta(days=10)
    ag.criar_evento(pool, conta_id, "Segurada", quando, fim=quando + timedelta(hours=4),
                    pre_reserva_ate=agora - timedelta(minutes=1))
    assert len(ag.conflitos(pool, conta_id, quando, quando + timedelta(hours=2))) == 1
    ag.expirar_pre_reservas(pool, agora)
    assert ag.conflitos(pool, conta_id, quando, quando + timedelta(hours=2)) == []


def test_ticker_expira_e_avisa_mesmo_sem_lembrete_ligado(pool, conta_id, enviados):
    """Soltar a data não é um lembrete — é a regra do negócio acontecendo. Roda pra
    TODA conta, inclusive quem nunca ligou resumo nem aviso (que é a maioria)."""
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Casamento — Ana", agora + timedelta(days=9),
                         pre_reserva_ate=agora - timedelta(minutes=5))
    lb.rodar(pool, agora=agora)
    assert _status(pool, ev["id"])[0] == "cancelado"
    assert len(enviados) == 1 and enviados[0][0] == conta_id
    assert "Casamento — Ana" in enviados[0][1] and "venceu" in enviados[0][1]


def test_aviso_que_falha_nao_segura_a_liberacao(pool, conta_id, monkeypatch):
    """Best-effort de propósito: Telegram fora do ar não pode manter a data presa."""
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram off")))
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Festa", agora + timedelta(days=8),
                         pre_reserva_ate=agora - timedelta(minutes=5))
    lb.rodar(pool, agora=agora)
    assert _status(pool, ev["id"])[0] == "cancelado"


def test_assistente_diz_que_a_data_esta_segurada(pool, conta_id):
    """"Tenho o quê essa semana?" tem que mostrar a data segurada — dizer que o dia
    está livre quando alguém já segurou é justamente o erro caro. Mas dita como o
    que é: segurada, sinal pendente."""
    from finance.agenda_tools import construir_ferramentas_agenda
    amanha = (ag.agora_brt() + timedelta(days=1)).replace(hour=20, minute=0, second=0,
                                                          microsecond=0)
    ag.criar_evento(pool, conta_id, "Casamento — Ana", amanha,
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    txt = ferrs["ver_agenda"].executar({"periodo": "amanha"})
    assert "Casamento — Ana" in txt and "data segurada" in txt


# ------------------------------------------------------------ conflito de data

def test_conflitos_pega_sobreposicao_e_ignora_o_que_nao_encosta(pool, conta_id):
    base = ag.agora_brt().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=3)
    ag.criar_evento(pool, conta_id, "Festa A", base, fim=base + timedelta(hours=4))
    # encosta no meio -> conflito
    assert [e["titulo"] for e in ag.conflitos(pool, conta_id, base + timedelta(hours=2),
                                              base + timedelta(hours=6))] == ["Festa A"]
    # começa exatamente quando a outra acaba -> NÃO é conflito
    assert ag.conflitos(pool, conta_id, base + timedelta(hours=4),
                        base + timedelta(hours=6)) == []
    # dia seguinte -> nada
    assert ag.conflitos(pool, conta_id, base + timedelta(days=1),
                        base + timedelta(days=1, hours=2)) == []


def test_conflitos_enxerga_a_data_segurada(pool, conta_id):
    """Data segurada TEM que contar como ocupada — é pra isso que ela ocupa o dia."""
    base = ag.agora_brt().replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=4)
    ag.criar_evento(pool, conta_id, "Segurada", base, fim=base + timedelta(hours=5),
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    ch = ag.conflitos(pool, conta_id, base + timedelta(hours=1), base + timedelta(hours=3))
    assert [e["titulo"] for e in ch] == ["Segurada"]
    assert ch[0]["status"] == ag.PRE_RESERVADO
    # cancelada não conta
    with pool.connection() as c:
        c.execute("update eventos_agenda set status='cancelado'")
        c.commit()
    assert ag.conflitos(pool, conta_id, base + timedelta(hours=1), base + timedelta(hours=3)) == []


def test_conflitos_sem_fim_conta_uma_hora(pool, conta_id):
    """Compromisso sem fim vale 1h — a mesma convenção do .ics. Com duração zero,
    quem marcasse no MESMO horário não apareceria como choque, que é o pior caso."""
    base = ag.agora_brt().replace(minute=0, second=0, microsecond=0) + timedelta(days=2)
    ev = ag.criar_evento(pool, conta_id, "Sem fim", base)
    assert [e["titulo"] for e in ag.conflitos(pool, conta_id, base, None)] == ["Sem fim"]
    assert ag.conflitos(pool, conta_id, base + timedelta(hours=1), None) == []
    # ignorar_id serve pro remarcar: o próprio evento não conflita consigo
    assert ag.conflitos(pool, conta_id, base, None, ignorar_id=ev["id"]) == []


def test_conflitos_nao_atravessa_conta(pool, conta_id):
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Vizinha 2') "
                          "returning id").fetchone()[0]
        c.commit()
    base = ag.agora_brt() + timedelta(days=5)
    ag.criar_evento(pool, conta_id, "Minha festa", base, fim=base + timedelta(hours=3))
    assert ag.conflitos(pool, outra, base, base + timedelta(hours=1)) == []


# ------------------------------------------------------------ prazo por empresa

def test_prazo_padrao_e_ajustavel_por_empresa(pool, conta_id):
    assert ag.get_config(pool, conta_id)["pre_reserva_dias"] == ag.PRE_RESERVA_DIAS
    assert ag.salvar_pre_reserva_dias(pool, conta_id, 7) == 7
    assert ag.get_config(pool, conta_id)["pre_reserva_dias"] == 7
    # fora de faixa não vira 0 dia (data que nasce vencida) nem prazo eterno
    assert ag.salvar_pre_reserva_dias(pool, conta_id, 0) == 1
    assert ag.salvar_pre_reserva_dias(pool, conta_id, 9999) == 90


def test_qualquer_prazo_da_faixa_serve(pool, conta_id):
    """A tela oferecia uma lista fechada — 1, 2, 3, 5, 7, 10, 15 e 30 dias — e quem
    pratica 4 ou 20 não tinha como dizer. O campo virou livre; o servidor sempre
    aceitou a faixa inteira, e este teste prende isso pra ninguém reintroduzir a
    lista achando que 4 nunca foi possível."""
    for dias in (4, 6, 9, 20, 45, 90, 1):
        assert ag.salvar_pre_reserva_dias(pool, conta_id, dias) == dias
        assert ag.get_config(pool, conta_id)["pre_reserva_dias"] == dias


def test_salvar_lembrete_nao_apaga_o_prazo_da_reserva(pool, conta_id):
    """São dois cards e duas decisões: mexer no lembrete não pode devolver o prazo
    pro padrão de quem já ajustou pra 7 dias."""
    ag.salvar_pre_reserva_dias(pool, conta_id, 7)
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=8, aviso_antes_min=30)
    assert ag.get_config(pool, conta_id)["pre_reserva_dias"] == 7


def test_a_tela_pede_o_prazo_em_campo_livre_e_nao_em_lista():
    """A regra é da CASA, não do sistema: cada empresa segura a data pelo prazo que
    pratica. A lista fechada (1, 2, 3, 5, 7, 10, 15, 30) decidia por ela — quem
    trabalha com 4 ou 20 dias ficava sem opção, num campo onde o servidor sempre
    aceitou de 1 a 90.

    Olha a fonte do template porque é ela que a empresa vê: `salvar_pre_reserva_dias`
    aceitar 4 não adianta nada se a tela não deixa escrever 4."""
    from web.painel_agenda import _AGENDA_TPL as tpl
    assert 'name="pre_reserva_dias"' in tpl, "o campo do prazo sumiu da tela"
    assert 'type="number"' in tpl and 'max="90"' in tpl, (
        "o prazo voltou a ser lista fechada — a faixa aceita é 1 a 90")
    assert "<select name=\"pre_reserva_dias\"" not in tpl


# ================================================= datas CONFIRMADAS + a consulta única
#
# `confirmadas` é a irmã de `pre_reservas`, e não existia: quem quisesse saber
# quantas datas a empresa tinha vendidas contava no calendário, mês a mês. As duas
# alimentam o mesmo card na lateral da Agenda, em duas abas.
#
# E as duas passaram a colar o orçamento de origem numa CONSULTA SÓ. Era uma por
# evento, cada uma pegando conexão nova do pool: numa agenda com 12 datas
# seguradas, 12 idas ao banco a cada carregamento — e a tela recarregava a cada
# clique. Quanto melhor o mês da empresa, mais lenta ficava a Agenda.

def _com_orcamento(pool, conta_id, evento_id, *, numero, sinal=None, total=None):
    """Vincula um orçamento ao compromisso, como a aprovação da proposta faz."""
    with pool.connection() as c:
        # `create if not exists` sozinho não bastava: se outro módulo da suíte
        # tiver criado `orcamentos` antes, com outras colunas, o create vira
        # no-op e o insert abaixo estoura em "column numero does not exist".
        # Os alters garantem as colunas independentemente de quem chegou primeiro.
        c.execute("""create table if not exists orcamentos (
                       id bigserial primary key, conta_id bigint, numero int,
                       evento_agenda_id bigint, sinal_centavos bigint,
                       setup_centavos bigint, primeiro_ano_centavos bigint)""")
        for _col, _tipo in (("conta_id", "bigint"), ("numero", "int"),
                            ("evento_agenda_id", "bigint"),
                            ("sinal_centavos", "bigint"),
                            ("primeiro_ano_centavos", "bigint")):
            c.execute(f"alter table orcamentos add column if not exists {_col} {_tipo}")
        c.execute("""insert into orcamentos (conta_id, numero, evento_agenda_id,
                       sinal_centavos, primeiro_ano_centavos) values (%s,%s,%s,%s,%s)""",
                  (conta_id, numero, evento_id, sinal, total))
        c.commit()


def test_confirmadas_traz_so_data_firme_de_empresa_daqui_pra_frente(pool, conta_id):
    base = ag.agora_brt()
    firme = ag.criar_evento(pool, conta_id, "Casamento", base + timedelta(days=10),
                            tipo="empresa")
    ag.criar_evento(pool, conta_id, "Formatura", base + timedelta(days=20),
                    tipo="empresa", pre_reserva_ate=base + timedelta(days=2))
    ag.criar_evento(pool, conta_id, "Festa de ontem", base - timedelta(days=1),
                    tipo="empresa")
    ag.criar_evento(pool, conta_id, "Dentista", base + timedelta(days=5), tipo="pessoal")
    ids = [e["id"] for e in ag.confirmadas(pool, conta_id)]
    assert ids == [firme["id"]]


def test_confirmadas_vem_da_mais_proxima_pra_mais_distante(pool, conta_id):
    base = ag.agora_brt()
    longe = ag.criar_evento(pool, conta_id, "Dezembro", base + timedelta(days=90), tipo="empresa")
    perto = ag.criar_evento(pool, conta_id, "Semana que vem", base + timedelta(days=7), tipo="empresa")
    assert [e["id"] for e in ag.confirmadas(pool, conta_id)] == [perto["id"], longe["id"]]


def test_a_festa_que_ja_aconteceu_sai_da_lista_sozinha(pool, conta_id):
    """O corte é no AGORA, não no início do dia: a festa de ontem à noite não é
    data confirmada, é festa que já aconteceu. Ninguém precisa arquivar nada."""
    base = ag.agora_brt()
    ag.criar_evento(pool, conta_id, "Ontem", base - timedelta(hours=2), tipo="empresa")
    assert ag.confirmadas(pool, conta_id) == []


def test_confirmada_traz_o_orcamento_e_o_valor(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Casamento", ag.agora_brt() + timedelta(days=10),
                         tipo="empresa")
    _com_orcamento(pool, conta_id, ev["id"], numero=14, sinal=267000, total=890000)
    c, = ag.confirmadas(pool, conta_id)
    assert c["orcamento_numero"] == 14
    assert c["total_centavos"] == 890000
    assert c["sinal_centavos"] == 267000


def test_data_marcada_na_agenda_nao_finge_ter_orcamento(pool, conta_id):
    """"Segura o dia 20", por telefone, não tem orçamento — e a linha simplesmente
    não fala de dinheiro em vez de mostrar zero."""
    ag.criar_evento(pool, conta_id, "Aniversário da Dona Cleide",
                    ag.agora_brt() + timedelta(days=10), tipo="empresa")
    c, = ag.confirmadas(pool, conta_id)
    assert "orcamento_numero" not in c and "total_centavos" not in c


def test_o_orcamento_de_todas_as_datas_sai_numa_consulta_so(pool, conta_id, monkeypatch):
    """O N+1 que fazia a Agenda ficar mais lenta quanto melhor era o mês. Conta
    conexões porque era uma por evento — e cada uma pegava conexão do pool."""
    base = ag.agora_brt()
    for n in range(6):
        ev = ag.criar_evento(pool, conta_id, f"Festa {n}", base + timedelta(days=10 + n),
                             tipo="empresa", pre_reserva_ate=base + timedelta(days=3))
        _com_orcamento(pool, conta_id, ev["id"], numero=n, sinal=1000)

    conexoes = {"n": 0}
    original = pool.connection

    def contando(*a, **kw):
        conexoes["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(pool, "connection", contando)
    reservas = ag.pre_reservas(pool, conta_id)
    assert len(reservas) == 6
    assert all(r.get("orcamento_numero") is not None for r in reservas)
    # uma pra listar os eventos + uma pra colar os orçamentos. Eram 1 + 6.
    assert conexoes["n"] == 2, f"voltou o N+1: {conexoes['n']} conexões pra 6 datas"


def test_sem_o_modulo_de_orcamentos_a_agenda_continua_abrindo(pool, conta_id):
    """A agenda roda em conta que nem tem orçamento. O vínculo é enfeite: falhar
    a leitura não pode derrubar a tela."""
    with pool.connection() as c:
        c.execute("drop table if exists orcamentos")
        c.commit()
    ev = ag.criar_evento(pool, conta_id, "Casamento", ag.agora_brt() + timedelta(days=10),
                         tipo="empresa")
    c, = ag.confirmadas(pool, conta_id)
    assert c["id"] == ev["id"] and "orcamento_numero" not in c
