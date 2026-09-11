"""O modelo de funil do ramo (finance/funil_modelo + raio_x_perfil.etapas_padrao).

O que estes testes protegem, em uma frase cada:
  * cada nicho recebe AS COLUNAS DELE, e a palavra de um não vaza pro outro;
  * eventos são SEIS colunas, com "Fechado" fora do quadro e ligado à Agenda;
  * a conta que já existe NÃO é reescrita — o modelo só propõe;
  * nada some: etapa fora do modelo sai do quadro, nunca é apagada, e nenhum lead
    muda de status;
  * rótulo que o dono trocou à mão vem DESMARCADO no plano;
  * aplicar recalcula o plano — id de formulário velho não muda nada.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_modelo as fm
from finance import raio_x_perfil as rxp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 71

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, status text default 'novo', estagio text default 'lead',
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int not null default 0, fixa boolean not null default false,
  unique (conta_id, chave));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_modelo_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade das duas caixas: é o que faz o teste perceber que
        # ela não chegou, em vez de fingir um schema que produção não tem
        c.execute((MIG / "238_etapa_sai_do_quadro.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from funil_etapas")
        c.execute("delete from prospeccao")
        c.commit()
    return pool


def _etapas(pool, conta=CONTA):
    with pool.connection() as c:
        return c.execute("""select chave, rotulo, ordem, fixa, sai_do_quadro, agenda_ao_entrar
                              from funil_etapas where conta_id=%s order by ordem, id""",
                         (conta,)).fetchall()


def _por(pool, chave, conta=CONTA):
    return {r[0]: r for r in _etapas(pool, conta)}[chave]


def _generico(pool, conta=CONTA):
    """A conta como ela nascia ANTES do modelo por ramo: as seis genéricas."""
    with pool.connection() as c:
        for ch, rot, ordem, fixa, _s, _a in rxp.ETAPAS_GENERICAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                         values (%s,%s,%s,%s,%s)""", (conta, ch, rot, ordem, fixa))
        c.commit()


def _leads(pool, chave, n, conta=CONTA):
    with pool.connection() as c:
        for i in range(n):
            c.execute("""insert into prospeccao (conta_id, contato, status, estagio)
                         values (%s,%s,%s,'lead')""", (conta, f"lead {chave} {i}", chave))
        c.commit()


# ------------------------------------------------------------------ o modelo, puro

def test_eventos_tem_seis_colunas_e_fechado_fora_do_quadro():
    """As seis que o dono aprovou em 11/09/2026. Se alguém acrescentar uma coluna
    ao modelo sem decidir se ela é coluna, este teste conta errado e reclama."""
    m = rxp.etapas_padrao("eventos")
    colunas = [rot for _ch, rot, _o, _f, sai, _a in m if not sai]
    assert colunas == ["Novo", "Contatado", "Follow-up", "Agendado Visita",
                       "Proposta", "Perdido"]
    fechado = [e for e in m if e[0] == "ganho"][0]
    assert fechado[1] == "Fechado"
    assert fechado[4] is True, "Fechado tem que sair do quadro"
    # a ponte ANTES da saída: tirar do quadro sem a agenda receber troca uma coluna
    # cheia por uma agenda vazia (o erro que a migração 238 foi escrita pra evitar)
    assert fechado[5] is True, "Fechado tem que criar o compromisso na Agenda"


def test_a_chave_de_agendado_visita_continua_sendo_qualificado():
    """O rótulo muda por ramo; a CHAVE não. É ela que está em prospeccao.status de
    14 leads da Prime — trocá-la deixaria os 14 apontando pro nada."""
    m = {e[0]: e[1] for e in rxp.etapas_padrao("eventos")}
    assert m["qualificado"] == "Agendado Visita"
    assert "agendado_visita" not in m


def test_recorrente_marca_reuniao_e_nao_visita():
    """CLAUDE.md §6: o vocabulário de um nicho nunca serve pro outro."""
    m = dict((e[0], e[1]) for e in rxp.etapas_padrao("recorrente"))
    assert m["qualificado"] == "Reunião marcada"
    assert "Visita" not in " ".join(m.values())


def test_recorrente_sai_do_quadro_mas_nao_agenda():
    """Quem vende mensalidade não tem data de evento no cadastro: a ponte com a
    Agenda não teria o que ler, e ligá-la seria prometer um compromisso que nunca
    aparece."""
    ganho = [e for e in rxp.etapas_padrao("recorrente") if e[0] == "ganho"][0]
    assert (ganho[4], ganho[5]) == (True, False)


def test_produto_continua_com_o_funil_generico():
    """Produto não tem funil nem vendedor: ninguém ganha "Agendado Visita" numa
    tela que vende caixa. E perfil desconhecido cai no mesmo lugar, nunca em vazio."""
    assert rxp.etapas_padrao("produto") == rxp.ETAPAS_GENERICAS
    assert rxp.etapas_padrao("nao_existe") == rxp.ETAPAS_GENERICAS
    assert rxp.etapas_padrao("") == rxp.ETAPAS_GENERICAS


def test_todo_perfil_tem_entrada_e_os_dois_resultados():
    """'novo', 'ganho' e 'perdido' são fixas (migração 130) e meio produto depende
    delas. Um modelo sem uma das três é um funil que não fecha."""
    for p in ("eventos", "recorrente", "produto"):
        m = {e[0]: e for e in rxp.etapas_padrao(p)}
        assert {"novo", "ganho", "perdido"} <= set(m), p
        for ch in ("novo", "ganho", "perdido"):
            assert m[ch][3] is True, f"{p}/{ch} devia ser fixa"


def test_ganho_e_perdido_ficam_depois_do_corte_da_pos_venda():
    """A régua usa a ORDEM pra separar venda de pós-venda (900/910). Modelo com uma
    etapa de venda em 950 poria o miolo do funil depois do fechamento."""
    for p in ("eventos", "recorrente", "produto"):
        m = {e[0]: e[2] for e in rxp.etapas_padrao(p)}
        assert m["ganho"] == 900 and m["perdido"] == 910, p
        miolo = [o for ch, o in m.items() if ch not in ("ganho", "perdido")]
        assert max(miolo) < 900, p


# ------------------------------------------------------------------ semear

def test_conta_de_eventos_nasce_com_o_funil_de_eventos(limpo):
    """A Doce Mell (35) nasceu DEPOIS de a Prime construir o funil dela e mesmo
    assim recebeu as seis genéricas. É isto que este teste impede de voltar."""
    with limpo.connection() as c:
        fm.semear(c, CONTA, "eventos")
        c.commit()
    assert [r[1] for r in _etapas(limpo)] == [
        "Novo", "Contatado", "Follow-up", "Agendado Visita", "Proposta", "Fechado", "Perdido"]


def test_semear_duas_vezes_nao_duplica(limpo):
    with limpo.connection() as c:
        fm.semear(c, CONTA, "eventos")
        assert fm.semear(c, CONTA, "eventos") == 0
        c.commit()
    assert len(_etapas(limpo)) == 7


def test_semear_nao_reescreve_o_rotulo_de_quem_ja_tem(limpo):
    """Semear é pra conta VAZIA. Chamado por engano numa conta que já tem etapa, não
    pode encostar no que está lá."""
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'ganho','Evento Realizado',900,true)""", (CONTA,))
        fm.semear(c, CONTA, "eventos")
        c.commit()
    assert _por(limpo, "ganho")[1] == "Evento Realizado"


# ------------------------------------------------------------------ o plano

def test_conta_ja_no_modelo_nao_tem_o_que_propor(limpo):
    with limpo.connection() as c:
        fm.semear(c, CONTA, "eventos")
        c.commit()
        assert fm.plano(c, CONTA, "eventos") == []


def test_o_plano_de_uma_conta_generica_de_eventos(limpo):
    """O caso da Doce Mell: seis genéricas, nicho eventos."""
    _generico(limpo)
    with limpo.connection() as c:
        itens = {i["id"]: i for i in fm.plano(c, CONTA, "eventos")}
    assert "criar:follow_up" in itens
    assert itens["rotulo:qualificado"]["para"] == "Agendado Visita"
    assert itens["rotulo:ganho"]["para"] == "Fechado"
    assert itens["quadro:ganho"]["para"] is True
    assert itens["agenda:ganho"]["para"] is True
    # 'novo', 'contatado', 'proposta' e 'perdido' já batem de nome
    assert "rotulo:novo" not in itens and "rotulo:perdido" not in itens


def test_o_plano_nunca_propoe_apagar(limpo):
    """CLAUDE.md §0. Etapa que a conta tem e o modelo não some do QUADRO, não do
    banco — foi assim que o dono pediu pras colunas de pós-venda da Prime."""
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'evento_realizado','Evento A Realizar',60,false)""", (CONTA,))
        c.commit()
    _leads(limpo, "evento_realizado", 5)
    with limpo.connection() as c:
        itens = {i["id"]: i for i in fm.plano(c, CONTA, "eventos")}
    assert not [i for i in itens.values() if i["acao"] not in fm.ACOES]
    fora = itens["quadro:evento_realizado"]
    assert fora["para"] is True and fora["leads"] == 5
    assert "continuam no cadastro" in fora["nota"]


def test_etapa_fora_do_modelo_que_ja_saiu_do_quadro_nao_e_proposta_de_novo(limpo):
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, sai_do_quadro)
                     values (%s,'pos','Pós-venda',950,false,true)""", (CONTA,))
        c.commit()
        assert "quadro:pos" not in {i["id"] for i in fm.plano(c, CONTA, "eventos")}


def test_rotulo_que_o_dono_trocou_a_mao_vem_desmarcado(limpo):
    """A conta 3 chama 'perdido' de "Entregue" e a 34 chama 'ganho' de "Evento
    Realizado". Renomear isso sozinho apagaria uma decisão de quem usa a tela todo
    dia, em nome de um padrão que ninguém pediu."""
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("update funil_etapas set rotulo='Entregue' where conta_id=%s and chave='perdido'",
                  (CONTA,))
        c.commit()
        itens = {i["id"]: i for i in fm.plano(c, CONTA, "eventos")}
    assert itens["rotulo:perdido"]["marcado"] is False
    assert "já renomeou" in itens["rotulo:perdido"]["nota"]
    # e o que ele NÃO tocou continua pré-marcado
    assert itens["rotulo:qualificado"]["marcado"] is True


# ------------------------------------------------------------------ aplicar

def test_aplicar_so_mexe_no_que_foi_marcado(limpo):
    _generico(limpo)
    with limpo.connection() as c:
        feito = fm.aplicar(c, CONTA, "eventos", ["criar:follow_up"])
        c.commit()
    assert feito["criar"] == 1 and sum(feito.values()) == 1
    assert _por(limpo, "follow_up")[1] == "Follow-up"
    assert _por(limpo, "qualificado")[1] == "Qualificado", "renomeou sem ser marcado"


def test_aplicar_sem_nada_marcado_nao_muda_nada(limpo):
    _generico(limpo)
    antes = _etapas(limpo)
    with limpo.connection() as c:
        assert sum(fm.aplicar(c, CONTA, "eventos", []).values()) == 0
        c.commit()
    assert _etapas(limpo) == antes


def test_id_de_formulario_velho_e_ignorado(limpo):
    """Outra aba, botão de voltar: a tela manda id de mudança que já não existe. O
    plano é recalculado aqui, então isso não aplica nada — em vez de renomear uma
    etapa que alguém acabou de renomear pra outra coisa."""
    _generico(limpo)
    with limpo.connection() as c:
        assert sum(fm.aplicar(c, CONTA, "eventos", ["rotulo:nao_existe", "voar:ganho"]).values()) == 0
        c.commit()


def test_adotar_o_modelo_inteiro_nao_move_nenhum_lead(limpo):
    """A trava que importa: adotar o modelo é mudança de TELA. Se mexesse no status,
    o histórico do funil passaria a contar um movimento que ninguém fez."""
    _generico(limpo)
    for ch, n in (("novo", 3), ("contatado", 7), ("qualificado", 2), ("ganho", 4)):
        _leads(limpo, ch, n)
    with limpo.connection() as c:
        antes = dict(c.execute("""select status, count(*) from prospeccao
                                   where conta_id=%s group by status""", (CONTA,)).fetchall())
        itens = [i["id"] for i in fm.plano(c, CONTA, "eventos")]
        fm.aplicar(c, CONTA, "eventos", itens)
        c.commit()
        depois = dict(c.execute("""select status, count(*) from prospeccao
                                    where conta_id=%s group by status""", (CONTA,)).fetchall())
    assert depois == antes
    # e o funil chegou no modelo
    with limpo.connection() as c:
        assert fm.plano(c, CONTA, "eventos") == []


def test_adotar_nao_apaga_etapa_nenhuma(limpo):
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'evento_realizado','Evento A Realizar',60,false)""", (CONTA,))
        c.commit()
    _leads(limpo, "evento_realizado", 5)
    with limpo.connection() as c:
        fm.aplicar(c, CONTA, "eventos", [i["id"] for i in fm.plano(c, CONTA, "eventos")])
        c.commit()
    chaves = {r[0] for r in _etapas(limpo)}
    assert "evento_realizado" in chaves, "a etapa foi apagada"
    assert _por(limpo, "evento_realizado")[4] is True, "devia ter saído do quadro"
    with limpo.connection() as c:
        n = c.execute("""select count(*) from prospeccao
                          where conta_id=%s and status='evento_realizado'""", (CONTA,)).fetchone()[0]
    assert n == 5, "os 5 leads da etapa sumiram"


def test_uma_conta_nao_alcanca_a_outra(limpo):
    """O plano e a aplicação são por conta. Um `update` sem `conta_id` aqui
    renomearia a etapa de todo mundo no mesmo deploy."""
    _generico(limpo, conta=CONTA)
    _generico(limpo, conta=CONTA + 1)
    with limpo.connection() as c:
        fm.aplicar(c, CONTA, "eventos", [i["id"] for i in fm.plano(c, CONTA, "eventos")])
        c.commit()
    assert _por(limpo, "qualificado", CONTA)[1] == "Agendado Visita"
    assert _por(limpo, "qualificado", CONTA + 1)[1] == "Qualificado"


def test_etapa_fora_do_modelo_leva_a_agenda_junto_ao_sair_do_quadro(limpo):
    """Medido na Prime: "Evento A Realizar" tem 5 leads com data de festa e não
    existe no modelo. Propor só a saída do quadro tiraria as 5 festas da tela sem
    pôr nenhuma na agenda — "trocar uma coluna cheia por uma agenda vazia", que é
    o erro que a migração 238 foi escrita pra evitar."""
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'evento_realizado','Evento A Realizar',60,false)""", (CONTA,))
        c.commit()
    _leads(limpo, "evento_realizado", 5)
    with limpo.connection() as c:
        itens = fm.plano(c, CONTA, "eventos")
    ids = [i["id"] for i in itens]
    assert "agenda:evento_realizado" in ids
    # e vem ANTES da saída: é a ordem em que o aviso da 239 manda ligar
    assert ids.index("agenda:evento_realizado") < ids.index("quadro:evento_realizado")


def test_ramo_sem_ponte_com_a_agenda_nao_propoe_agendar(limpo):
    """Recorrente não tem data de evento no cadastro. Propor a ponte ali seria
    prometer um compromisso que nunca aparece (CLAUDE.md §6)."""
    _generico(limpo)
    with limpo.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'implantacao','Implantação',60,false)""", (CONTA,))
        c.commit()
        ids = [i["id"] for i in fm.plano(c, CONTA, "recorrente")]
    assert "quadro:implantacao" in ids
    assert not [i for i in ids if i.startswith("agenda:")]
