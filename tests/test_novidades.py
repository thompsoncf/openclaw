"""A MIRA do aviso de atualização.

O que se prende aqui não é a tela — é quem recebe. Avisar quem não devia é tão
ruim quanto calar pra quem devia: a pessoa vai procurar na tela uma coisa que ela
não tem. E como cada nicho recebe atualização diferente, o erro só piora conforme
entram nichos novos.

Três travas, e nenhuma delas é "revisar com cuidado":

 1. o banco recusa público inexistente (check da 174);
 2. um teste compara o check com o registro do Python — deriva vira falha;
 3. cada aviso declara a LISTA de nichos que espera alcançar.

A trava 3 compara slugs e não contagem de contas de propósito: hoje 15 dos 22
nichos configurados têm vende_servico=True mas só 3 deles têm conta. Contar
contas passa por sorte e quebra quando alguém se cadastra — o que mede a regra é
a lista de nichos.
"""
import os
import re
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from finance import nichos as nic
from finance import novidades as nv

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


# ══════════════════════════════════════════════ a mira, pura (sem banco)

def test_conta_sem_nicho_so_recebe_aviso_de_todos():
    """A ARMADILHA MEDIDA: 14 das 22 contas em produção nunca escolheram nicho, e
    os portões discordam sobre elas — `vende_produto` inclui (default True, porque
    o painel mostra produto no onboarding), os outros excluem. Herdar esse default
    mandaria um aviso de produto pra 17 contas quando só 3 declararam produto."""
    assert nv.alcanca("todos", None) is True
    for pub in ("produto", "servico", "eventos", "recorrente"):
        assert nv.alcanca(pub, None) is False, pub
        assert nv.alcanca(pub, "") is False, pub


def test_nicho_que_nao_existe_mais_tambem_fica_de_fora():
    """Slug gravado que saiu do NICHOS: `config_do_nicho` cai no genérico e
    responderia vende_produto=True. Não escolher e escolher algo que não existe
    mais são a mesma incerteza — as duas ficam fora do aviso mirado."""
    assert nv.alcanca("produto", "nicho_extinto") is False
    assert nv.alcanca("todos", "nicho_extinto") is True


def test_publico_desconhecido_nao_alcanca_ninguem():
    """Melhor aviso que não sai do que aviso pra quem não devia — e o módulo loga,
    pra a falha não ficar silenciosa."""
    assert nv.alcanca("evento", "eventos") is False       # singular: typo clássico
    assert nv.alcanca("", "eventos") is False


def test_produto_ignora_o_default_permissivo_mas_respeita_quem_declarou():
    assert nv.alcanca("produto", "hortifruti") is True
    assert nv.alcanca("produto", "consultoria") is False
    # eventos vende os dois — quem declarou eventos RECEBE aviso de produto
    assert nv.alcanca("produto", "eventos") is True


# ══════════════════════════════════════════════ trava 3: a lista de cada público

def test_o_publico_eventos_e_exatamente_o_nicho_de_eventos():
    assert nv.nichos_alcancados("eventos") == {"eventos"}


def test_servico_e_recorrente_se_complementam_sem_sobrar_nada():
    """`recorrente` é "serviço menos evento". Se um dia a definição mudar num lado
    só, esta soma para de fechar."""
    servico = nv.nichos_alcancados("servico")
    assert nv.nichos_alcancados("recorrente") | nv.nichos_alcancados("eventos") == servico
    assert nv.nichos_alcancados("recorrente") & nv.nichos_alcancados("eventos") == set()


def test_todos_alcanca_todo_nicho_configurado():
    assert nv.nichos_alcancados("todos") == set(nic.NICHOS)


def test_nicho_novo_com_servico_entra_no_publico_de_servico():
    """A regressão que importa pro futuro: entrou nicho de serviço novo, ele passa
    a receber os avisos de serviço automaticamente — sem ninguém editar lista."""
    novos = {s for s in nic.NICHOS if nic.vende_servico(s)}
    assert nv.nichos_alcancados("servico") == novos
    assert "consultoria" in novos and "advocacia" in novos


# ══════════════════════════════════════════════ trava 2: banco × Python

@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_novidades"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("""create table nichos (id bigserial primary key, nome text,
                     slug text unique, tipo text)""")
        c.execute("""create table contas (id bigserial primary key, nome text,
                     nicho_id bigint references nichos(id),
                     criado_em timestamptz not null default now(), chip_de bigint)""")
        c.execute((BASE / "174_novidades.sql").read_text(encoding="utf-8"))
        # 184 amplia o check de `publico` com o primeiro portão de CONTA
        # (`canal_proprio`). Sem ela aqui, o schema do teste fica com a lista
        # antiga e a paridade banco × Python falha — que é o teste fazendo
        # o trabalho dele.
        c.execute((BASE / "184_novidade_voz_e_porta_fechada.sql"
                   ).read_text(encoding="utf-8"))
        # 199: pra_quem, resumo, link. Só schema e acerto dos avisos que já
        # existem; os avisos de hoje (200) entram por `_hoje`, teste a teste.
        c.execute((BASE / "199_novidades_pra_quem.sql").read_text(encoding="utf-8"))
        for slug in ("eventos", "consultoria", "hortifruti"):
            c.execute("insert into nichos (nome, slug) values (%s,%s)", (slug, slug))
        c.execute("""insert into contas (id, nome, nicho_id, criado_em) values
            (1,'Buffet',(select id from nichos where slug='eventos'),  now() - interval '30 days'),
            (2,'Consultoria',(select id from nichos where slug='consultoria'), now() - interval '30 days'),
            (3,'Sem nicho', null, now() - interval '30 days'),
            (4,'Recém-chegada',(select id from nichos where slug='eventos'), now())""")
        c.commit()
    yield p
    p.close()


def test_o_banco_e_o_python_conhecem_os_mesmos_publicos(pool):
    """A DERIVA que este teste existe pra matar: alguém acrescenta um portão no
    PUBLICOS e esquece o check da migração (o insert passa a estourar), ou o
    contrário (o aviso entra e nunca alcança ninguém). As duas listas são a mesma
    ou o teste falha."""
    with pool.connection() as c:
        regra = c.execute(
            """select pg_get_constraintdef(oid) from pg_constraint
                where conrelid='novidades'::regclass and contype='c'
                  and pg_get_constraintdef(oid) like '%publico%'""").fetchone()[0]
    do_banco = {t.strip().strip("'") for t in
                regra.split("ARRAY[")[1].split("]")[0].replace("::text", "").split(",")}
    assert do_banco == set(nv.PUBLICOS)


def test_o_banco_recusa_publico_que_nao_existe(pool):
    with pytest.raises(psycopg.errors.CheckViolation):
        with pool.connection() as c:
            c.execute("""insert into novidades (chave, publico, titulo, corpo)
                         values ('x','evento','t','c')""")   # singular


def test_o_banco_recusa_tipo_que_nao_existe(pool):
    with pytest.raises(psycopg.errors.CheckViolation):
        with pool.connection() as c:
            c.execute("""insert into novidades (chave, tipo, titulo, corpo)
                         values ('x','urgente','t','c')""")


# ══════════════════════════════════════════════ listar / ler

def _aviso(pool, chave, publico="todos", tipo="novidade", dias=1):
    with pool.connection() as c:
        c.execute("""insert into novidades (chave, tipo, publico, titulo, corpo, publicado_em)
                     values (%s,%s,%s,%s,'corpo', now() - (%s || ' days')::interval)
                     on conflict (chave) do nothing""",
                  (chave, tipo, publico, "T " + chave, dias))
        c.commit()


def test_cada_conta_ve_so_o_que_e_dela(pool):
    _aviso(pool, "geral", "todos")
    _aviso(pool, "so-evento", "eventos")
    _aviso(pool, "so-servico", "servico")
    assert {n["chave"] for n in nv.listar(pool, 1)} == {"geral", "so-evento", "so-servico"}
    assert {n["chave"] for n in nv.listar(pool, 2)} == {"geral", "so-servico"}
    # a conta SEM nicho vê só o geral — é a regra que a armadilha do produto obriga
    assert {n["chave"] for n in nv.listar(pool, 3)} == {"geral"}


def test_conta_nova_nao_recebe_o_historico(pool):
    """Quem se cadastrar mês que vem não pode abrir o painel com 23 avisos não
    lidos de mudanças que nunca viveu — o primeiro contato dele com o sistema
    seria uma pilha de changelog."""
    _aviso(pool, "antigo", "eventos", dias=10)
    assert {n["chave"] for n in nv.listar(pool, 1)} == {"antigo"}
    assert nv.listar(pool, 4) == []          # conta 4 nasceu agora


def test_marcar_lida_e_por_pessoa(pool):
    """Numa conta com dono e gerente, cada um lê o seu."""
    _aviso(pool, "a", "eventos")
    nid = nv.listar(pool, 1)[0]["id"]
    assert nv.marcar_lida(pool, nid, 1, membro_id=None) is True     # o DONO (membro nulo)
    assert nv.listar(pool, 1)[0]["lida"] is True
    assert nv.listar(pool, 1, membro_id=7)[0]["lida"] is False      # o gerente ainda não
    assert nv.nao_lidas(pool, 1) == 0 and nv.nao_lidas(pool, 1, membro_id=7) == 1


def test_marcar_duas_vezes_nao_quebra(pool):
    """O botão é clicável de novo enquanto a resposta não volta."""
    _aviso(pool, "a", "todos")
    nid = nv.listar(pool, 1)[0]["id"]
    assert nv.marcar_lida(pool, nid, 1) is True
    assert nv.marcar_lida(pool, nid, 1) is False
    assert nv.nao_lidas(pool, 1) == 0


def test_a_bolinha_nao_derruba_o_painel(pool, monkeypatch):
    """TOLERANTE de propósito: contagem de aviso é enfeite, e o painel inteiro não
    pode deixar de abrir porque ela falhou. Mesma escolha do `vende_data`."""
    monkeypatch.setattr(nv, "listar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert nv.nao_lidas(pool, 1) == 0


# ══════════════════════════════════════════════ a prévia que sai no PR

def test_a_previa_diz_quem_recebe_por_nome(pool):
    """A trava que o humano opera. As outras pegam erro de código; esta pega erro
    de julgamento — quando o portão está certo e mesmo assim não é quem se queria
    avisar. Por isso devolve NOME, não contagem."""
    alvo = nv.contas_alcancadas(pool, "eventos")
    assert [c["nome"] for c in alvo] == ["Buffet", "Recém-chegada"]
    assert all(c["nicho"] == "eventos" for c in alvo)
    # e a conta sem nicho aparece na de 'todos', com o rótulo explícito
    assert {c["nome"]: c["nicho"] for c in nv.contas_alcancadas(pool, "todos")}["Sem nicho"] \
        == "(sem nicho)"


# ══════════════════════════════ trava 3 aplicada: os avisos que já existem

def _publicados(pool) -> dict:
    """Os avisos gravados pela 175, como o banco os guarda."""
    with pool.connection() as c:
        c.execute((BASE / "175_novidades_do_que_ja_subiu.sql").read_text(encoding="utf-8"))
        c.commit()
        rows = c.execute("select chave, tipo, publico, titulo, corpo from novidades").fetchall()
    return {r[0]: {"tipo": r[1], "publico": r[2], "titulo": r[3], "corpo": r[4]} for r in rows}


def test_o_aviso_do_contrato_so_alcanca_eventos(pool):
    """A assinatura que abre o financeiro (#457, #459) é do nicho de eventos e de
    mais ninguém. Mandá-lo pra uma consultoria faria a pessoa procurar no funil um
    botão que nunca esteve lá."""
    a = _publicados(pool)["contrato-assinatura-fecha"]
    assert nv.nichos_alcancados(a["publico"]) == {"eventos"}
    # é 'mudanca' porque um botão SUMIU — precisa do "Entendi", não de um ✓ passivo
    assert a["tipo"] == "mudanca"


def test_o_aviso_da_agenda_so_alcanca_eventos(pool):
    a = _publicados(pool)["agenda-de-eventos"]
    assert nv.nichos_alcancados(a["publico"]) == {"eventos"}
    assert a["tipo"] == "novidade"


def test_o_aviso_do_desconto_alcanca_todo_mundo_que_vende_servico(pool):
    """O desconto por item NÃO é de eventos: o painel de Serviços inteiro é gateado
    por `vende_servico` (web/painel_servicos:150, conta[14]), então advocacia e
    construção também o receberam. Mirar em 'eventos' calaria pra quem ganhou."""
    a = _publicados(pool)["desconto-por-item"]
    alvo = nv.nichos_alcancados(a["publico"])
    assert alvo == {s for s in nic.NICHOS if nic.vende_servico(s)}
    assert {"eventos", "consultoria", "advocacia", "construcao"} <= alvo
    assert "hortifruti" not in alvo and "minimercado" not in alvo


def test_os_avisos_publicados_nao_alcancam_conta_sem_nicho(pool):
    """Os três são mirados — nenhum deles vai pra quem nunca escolheu nicho (14 das
    22 contas em produção). É a regra que a armadilha do `vende_produto` obriga."""
    for chave, a in _publicados(pool).items():
        assert a["publico"] != "todos", chave
        assert nv.alcanca(a["publico"], None) is False, chave


def test_reaplicar_a_175_nao_duplica_nem_desmarca_quem_leu(pool):
    """`on conflict (chave) do nothing`: a migração roda de novo sem criar aviso
    repetido e sem trocar o id — se o id mudasse, quem já tinha lido veria tudo
    outra vez."""
    antes = _publicados(pool)
    with pool.connection() as c:
        ids = dict(c.execute("select chave, id from novidades").fetchall())
    depois = _publicados(pool)
    with pool.connection() as c:
        assert dict(c.execute("select chave, id from novidades").fetchall()) == ids
    # as TRÊS chaves da 175, e não a contagem da tabela: o schema deste fixture
    # carrega outras migrações de aviso, e contar linhas fazia o teste quebrar toda
    # vez que um aviso novo entrasse — sem nada a ver com a idempotência que ele mede.
    assert antes == depois
    assert {"contrato-assinatura-fecha", "agenda-de-eventos",
            "desconto-por-item"} <= set(ids)


def test_o_aviso_do_desconto_no_cockpit_segue_o_portao_do_montador(pool):
    """O montador do Cockpit é gateado por `vende_servico` — o MESMO portão do
    painel. Mirar em 'eventos' calaria pra advocacia e construção, que também têm
    vendedor montando orçamento no celular."""
    with pool.connection() as c:
        c.execute((BASE / "176_novidade_desconto_no_cockpit.sql").read_text(encoding="utf-8"))
        c.commit()
        a = c.execute("""select tipo, publico from novidades
                          where chave='desconto-no-app-do-vendedor'""").fetchone()
    assert a == ("novidade", "servico")
    assert nv.nichos_alcancados(a[1]) == {s for s in nic.NICHOS if nic.vende_servico(s)}
    assert nv.alcanca(a[1], None) is False


# ═══════════════════════════════════════ pra quem (migração 199) e o site

def _aviso2(pool, chave, **campos):
    """`_aviso` com as colunas da 199: pra_quem, resumo, link."""
    campos.setdefault("publico", "todos")
    campos.setdefault("tipo", "novidade")
    campos.setdefault("pra_quem", ["dono", "gestor"])
    with pool.connection() as c:
        c.execute("""insert into novidades (chave, tipo, publico, pra_quem, titulo, corpo,
                                            resumo, link, publicado_em)
                     values (%s,%s,%s,%s,%s,'corpo',%s,%s, now() - interval '1 day')
                     on conflict (chave) do nothing""",
                  (chave, campos["tipo"], campos["publico"], campos["pra_quem"],
                   "T " + chave, campos.get("resumo"), campos.get("link")))
        c.commit()


def _papeis_do_check(pool) -> set[str]:
    with pool.connection() as c:
        (defn,) = c.execute("""select pg_get_constraintdef(oid) from pg_constraint
                               where conrelid = 'novidades'::regclass
                                 and conname = 'novidades_pra_quem_check'""").fetchone()
    return set(re.findall(r"'(\w+)'", defn))


def test_o_banco_e_o_python_conhecem_os_mesmos_papeis(pool):
    """A mesma trava dos públicos: a lista do check e `PAPEIS` são uma só. Um
    papel novo no Python sem entrar no check é aviso que o banco recusa; um papel
    no check sem o Python saber é aviso que ninguém filtra."""
    assert _papeis_do_check(pool) == set(nv.PAPEIS)


def test_o_banco_recusa_papel_que_nao_existe_e_lista_vazia(pool):
    import psycopg
    for ruim in (["estagiario"], []):
        with pytest.raises(psycopg.errors.CheckViolation):
            with pool.connection() as c:
                c.execute("""insert into novidades (chave, titulo, corpo, pra_quem)
                             values ('x', 't', 'c', %s)""", (ruim,))


def test_o_padrao_e_dono_e_gestor_e_o_vendedor_fica_de_fora(pool):
    """Aviso gravado sem `pra_quem` (todos os anteriores à 199) continua sendo
    de dono e gestor — e só deles. Nenhum aviso antigo muda de mão."""
    _aviso(pool, "antigo")
    assert [n["chave"] for n in nv.listar(pool, 1, papel="dono")] == ["antigo"] or \
        "antigo" in [n["chave"] for n in nv.listar(pool, 1, papel="dono")]
    assert "antigo" in [n["chave"] for n in nv.listar(pool, 1, papel="gestor")]
    assert "antigo" not in [n["chave"] for n in nv.listar(pool, 1, papel="vendedor")]
    # sem papel, a chamada antiga: devolve tudo
    assert "antigo" in [n["chave"] for n in nv.listar(pool, 1)]


def test_o_vendedor_so_recebe_o_que_e_dele_e_o_dono_nao_recebe_o_que_e_so_dele(pool):
    _aviso2(pool, "so-vendedor", pra_quem=["vendedor"])
    _aviso2(pool, "os-tres", pra_quem=["dono", "gestor", "vendedor"])
    vend = [n["chave"] for n in nv.listar(pool, 1, papel="vendedor")]
    dono = [n["chave"] for n in nv.listar(pool, 1, papel="dono")]
    assert "so-vendedor" in vend and "os-tres" in vend
    assert "so-vendedor" not in dono and "os-tres" in dono
    assert nv.nao_lidas(pool, 1, papel="vendedor") >= 2


def test_a_mira_por_publico_continua_valendo_pro_vendedor(pool):
    """`pra_quem` não substitui o público: o vendedor de uma consultoria não recebe
    o aviso de eventos só porque é marcado pra vendedor."""
    _aviso2(pool, "eventos-vend", publico="eventos", pra_quem=["vendedor"])
    assert "eventos-vend" in [n["chave"] for n in nv.listar(pool, 1, papel="vendedor")]
    assert "eventos-vend" not in [n["chave"] for n in nv.listar(pool, 2, papel="vendedor")]


def test_a_lista_publica_so_tem_o_que_tem_resumo_e_nada_de_conta(pool):
    """O site lê `publicas`: só aviso com resumo (o interno sem resumo não vaza),
    e nenhuma chave de conta, leitura ou corpo — o corpo cita número de conta e
    o que sumiu de onde."""
    _aviso2(pool, "com-resumo", resumo="Uma linha pra fora.", link="/painel/x")
    _aviso2(pool, "sem-resumo")
    pub = nv.publicas(pool)
    chaves = [n["chave"] for n in pub]
    assert "com-resumo" in chaves and "sem-resumo" not in chaves
    item = next(n for n in pub if n["chave"] == "com-resumo")
    assert set(item) == {"chave", "tipo", "ramo", "titulo", "resumo", "dia", "pra_quem"}
    assert item["ramo"] == "todos" and item["dia"] and item["pra_quem"] == ["dono", "gestor"]


def test_a_199_leva_os_avisos_de_agosto_pro_vendedor_sem_tirar_o_dono(pool):
    """Os dois da 184 (o microfone e o atalho que saiu) eram do app do vendedor e
    nunca chegaram nele. A 199 acrescenta 'vendedor' e mantém dono e gestor; e
    dá o resumo, que é o que o site mostra."""
    with pool.connection() as c:
        rows = dict(c.execute("""select chave, pra_quem from novidades
                                  where chave in ('voz-no-app-do-vendedor',
                                                  'atalho-whatsapp-fechado')""").fetchall())
    assert rows and all(set(v) == {"dono", "gestor", "vendedor"} for v in rows.values())
    chaves = [n["chave"] for n in nv.publicas(pool)]
    assert {"voz-no-app-do-vendedor", "atalho-whatsapp-fechado"} <= set(chaves)


def test_reaplicar_a_199_nao_duplica_o_vendedor(pool):
    with pool.connection() as c:
        c.execute((BASE / "199_novidades_pra_quem.sql").read_text(encoding="utf-8"))
        c.commit()
        (pq,) = c.execute("select pra_quem from novidades where chave='voz-no-app-do-vendedor'").fetchone()
    assert sorted(pq) == ["dono", "gestor", "vendedor"]


def _hoje(pool) -> dict:
    """Os avisos das entregas de 05/09, como a 200 os grava."""
    with pool.connection() as c:
        c.execute((BASE / "200_novidade_funil_mes_atual.sql").read_text(encoding="utf-8"))
        c.commit()
        rows = c.execute("""select chave, tipo, publico, pra_quem, resumo, link, id from novidades
                            where chave in ('funil-mes-atual', 'card-le-a-data-da-festa',
                                            'fila-no-mes-atual')""").fetchall()
    return {r[0]: {"tipo": r[1], "publico": r[2], "pra_quem": list(r[3]), "resumo": r[4],
                   "link": r[5], "id": r[6]} for r in rows}


def test_os_avisos_de_hoje_miram_certo(pool):
    """O funil é de qualquer conta (todos, dono e gestor); o card que lê a data é
    só de eventos, que é o portão `vende_data`; a Fila é do vendedor, no app."""
    a = _hoje(pool)
    assert set(a) == {"funil-mes-atual", "card-le-a-data-da-festa", "fila-no-mes-atual"}
    assert a["funil-mes-atual"]["publico"] == "todos"
    assert sorted(a["funil-mes-atual"]["pra_quem"]) == ["dono", "gestor"]
    assert nv.nichos_alcancados(a["card-le-a-data-da-festa"]["publico"]) == {"eventos"}
    assert a["fila-no-mes-atual"]["pra_quem"] == ["vendedor"]
    assert a["fila-no-mes-atual"]["publico"] == "todos"
    assert a["fila-no-mes-atual"]["link"] == "/cockpit"
    for chave, x in a.items():
        assert x["tipo"] == "novidade" and x["resumo"] and x["link"], chave


def test_os_avisos_de_hoje_chegam_em_quem_devem(pool):
    """Conta de eventos: o dono vê os dois do painel, o vendedor vê o da Fila.
    Conta sem nicho: só os de 'todos'. Ninguém vê o que não é seu."""
    _hoje(pool)
    dono_ev = {n["chave"] for n in nv.listar(pool, 1, papel="dono")}
    vend_ev = {n["chave"] for n in nv.listar(pool, 1, papel="vendedor")}
    dono_sem = {n["chave"] for n in nv.listar(pool, 3, papel="dono")}
    vend_sem = {n["chave"] for n in nv.listar(pool, 3, papel="vendedor")}
    assert {"funil-mes-atual", "card-le-a-data-da-festa"} <= dono_ev
    assert "fila-no-mes-atual" not in dono_ev
    assert "fila-no-mes-atual" in vend_ev and "card-le-a-data-da-festa" not in vend_ev
    assert "funil-mes-atual" in dono_sem and "card-le-a-data-da-festa" not in dono_sem
    assert vend_sem & {"funil-mes-atual", "card-le-a-data-da-festa", "fila-no-mes-atual"} == {"fila-no-mes-atual"}


def test_reaplicar_a_200_nao_duplica_nem_troca_id(pool):
    antes = {k: v["id"] for k, v in _hoje(pool).items()}
    depois = {k: v["id"] for k, v in _hoje(pool).items()}
    assert antes == depois and len(antes) == 3


# ------------------------------------------------- 202: o aviso do termo aditivo

def _aditivo(pool) -> dict:
    with pool.connection() as c:
        c.execute((BASE / "202_novidade_termo_aditivo.sql").read_text(encoding="utf-8"))
        c.commit()
        r = c.execute("""select tipo, publico, pra_quem, resumo, link, id, titulo
                           from novidades where chave='termo-aditivo'""").fetchone()
    return {"tipo": r[0], "publico": r[1], "pra_quem": list(r[2]), "resumo": r[3],
            "link": r[4], "id": r[5], "titulo": r[6]}


def test_o_aviso_do_aditivo_so_alcanca_eventos(pool):
    """Sem contrato de locação não há o que aditar — anunciar pra quem não tem
    contrato seria prometer uma tela que não abre."""
    a = _aditivo(pool)
    assert nv.nichos_alcancados(a["publico"]) == {"eventos"}
    assert a["tipo"] == "novidade" and a["resumo"] and a["link"]


def test_o_aviso_do_aditivo_vai_pro_vendedor_tambem(pool):
    """O dono escolheu que dono, gestor e vendedor fazem aditivo, e a tela mora
    sob /painel/servicos justamente pra caber no que o vendedor alcança. Avisar
    só dono e gestor repetiria, em forma de aviso, o erro que o recurso evitou em
    forma de rota."""
    a = _aditivo(pool)
    assert sorted(a["pra_quem"]) == ["dono", "gestor", "vendedor"]
    ev_dono = {n["chave"] for n in nv.listar(pool, 1, papel="dono")}
    ev_vend = {n["chave"] for n in nv.listar(pool, 1, papel="vendedor")}
    sem_dono = {n["chave"] for n in nv.listar(pool, 3, papel="dono")}
    assert "termo-aditivo" in ev_dono and "termo-aditivo" in ev_vend
    # conta sem nicho de eventos não recebe
    assert "termo-aditivo" not in sem_dono


def test_o_aviso_do_aditivo_sai_no_site(pool):
    # tem resumo, então é público — a regra que impede aviso interno virar
    # público por esquecimento vale nos dois sentidos
    _aditivo(pool)
    assert "termo-aditivo" in {n["chave"] for n in nv.publicas(pool)}


def test_reaplicar_a_202_nao_duplica_nem_troca_id(pool):
    assert _aditivo(pool)["id"] == _aditivo(pool)["id"]


# ------------------------------------------ 204: o texto do aditivo é do dono

def _aditivo_texto(pool) -> dict:
    with pool.connection() as c:
        c.execute((BASE / "204_novidade_aditivo_texto.sql").read_text(encoding="utf-8"))
        c.commit()
        r = c.execute("""select tipo, publico, pra_quem, resumo, link, id
                           from novidades where chave='aditivo-texto-editavel'""").fetchone()
    return {"tipo": r[0], "publico": r[1], "pra_quem": list(r[2]), "resumo": r[3],
            "link": r[4], "id": r[5]}


def test_o_aviso_do_texto_do_aditivo_nao_vai_pro_vendedor(pool):
    """Ao contrário do aviso do aditivo em si (202), que foi pros três.

    A régua da seção 5 é "aviso de tela que ele não tem, nunca": o card do modelo
    é gateado por `gerir`. O vendedor FAZ aditivo, mas não escreve o texto."""
    a = _aditivo_texto(pool)
    assert sorted(a["pra_quem"]) == ["dono", "gestor"]
    assert nv.nichos_alcancados(a["publico"]) == {"eventos"}
    ev_vend = {n["chave"] for n in nv.listar(pool, 1, papel="vendedor")}
    ev_dono = {n["chave"] for n in nv.listar(pool, 1, papel="dono")}
    assert "aditivo-texto-editavel" not in ev_vend
    assert "aditivo-texto-editavel" in ev_dono


def test_o_aviso_do_texto_do_aditivo_sai_no_site(pool):
    _aditivo_texto(pool)
    assert "aditivo-texto-editavel" in {n["chave"] for n in nv.publicas(pool)}


def test_reaplicar_a_204_nao_duplica(pool):
    assert _aditivo_texto(pool)["id"] == _aditivo_texto(pool)["id"]


# ------------------------------------- 205: a pílula "Outra data" no Cockpit

def _outra_data(pool) -> dict:
    with pool.connection() as c:
        c.execute((BASE / "205_novidade_agendar_visita_outra_data.sql"
                   ).read_text(encoding="utf-8"))
        c.commit()
        r = c.execute("""select tipo, publico, pra_quem, resumo, link, id
                           from novidades where chave='agendar-visita-outra-data'""").fetchone()
    return {"tipo": r[0], "publico": r[1], "pra_quem": list(r[2]), "resumo": r[3],
            "link": r[4], "id": r[5]}


def test_o_aviso_da_outra_data_e_de_qualquer_conta_so_pro_vendedor(pool):
    """A tela é a mesma pra qualquer conta, sem gate de nicho — por isso 'todos',
    igual à 'fila-no-mes-atual'. E é rotina do vendedor no Cockpit, não do dono
    nem do gestor, que não agendam visita por lá."""
    a = _outra_data(pool)
    assert a["publico"] == "todos" and a["pra_quem"] == ["vendedor"]
    assert a["link"] == "/cockpit"
    assert a["tipo"] == "novidade" and a["resumo"]


def test_o_aviso_da_outra_data_chega_ao_vendedor_de_qualquer_conta(pool):
    _outra_data(pool)
    vend_ev = {n["chave"] for n in nv.listar(pool, 1, papel="vendedor")}
    vend_sem = {n["chave"] for n in nv.listar(pool, 3, papel="vendedor")}
    dono_ev = {n["chave"] for n in nv.listar(pool, 1, papel="dono")}
    assert "agendar-visita-outra-data" in vend_ev
    assert "agendar-visita-outra-data" in vend_sem  # 'todos': sem nicho também
    assert "agendar-visita-outra-data" not in dono_ev  # só vendedor


def test_o_aviso_da_outra_data_sai_no_site(pool):
    _outra_data(pool)
    assert "agendar-visita-outra-data" in {n["chave"] for n in nv.publicas(pool)}


def test_reaplicar_a_205_nao_duplica(pool):
    assert _outra_data(pool)["id"] == _outra_data(pool)["id"]


def _raio_x(pool) -> dict:
    """Os avisos do Raio-X (208): a aba do app pro vendedor, o grupo de segunda
    pro dono e gestor."""
    with pool.connection() as c:
        c.execute((BASE / "208_novidade_raio_x.sql").read_text(encoding="utf-8"))
        c.commit()
        rows = c.execute("""select chave, tipo, publico, pra_quem, resumo, link, id from novidades
                            where chave in ('raio-x-no-app', 'raio-x-de-segunda')""").fetchall()
    return {r[0]: {"tipo": r[1], "publico": r[2], "pra_quem": list(r[3]), "resumo": r[4],
                   "link": r[5], "id": r[6]} for r in rows}


def test_os_avisos_do_raio_x_miram_certo(pool):
    """A aba é do vendedor, no app; o grupo de segunda é de quem manda na conta
    (é ele quem escolhe o grupo em Equipe). Os dois são de qualquer nicho."""
    a = _raio_x(pool)
    assert set(a) == {"raio-x-no-app", "raio-x-de-segunda"}
    assert a["raio-x-no-app"]["publico"] == "todos" and a["raio-x-no-app"]["pra_quem"] == ["vendedor"]
    assert a["raio-x-no-app"]["link"] == "/cockpit/raio-x"
    assert a["raio-x-de-segunda"]["publico"] == "todos"
    assert sorted(a["raio-x-de-segunda"]["pra_quem"]) == ["dono", "gestor"]
    assert a["raio-x-de-segunda"]["link"] == "/painel/equipe"
    for chave, x in a.items():
        assert x["tipo"] == "novidade" and x["resumo"], chave


def test_os_avisos_do_raio_x_chegam_em_quem_devem(pool):
    _raio_x(pool)
    for conta in (1, 3):     # com nicho e sem nicho: os dois são 'todos'
        dono = {n["chave"] for n in nv.listar(pool, conta, papel="dono")}
        vend = {n["chave"] for n in nv.listar(pool, conta, papel="vendedor")}
        assert "raio-x-de-segunda" in dono and "raio-x-no-app" not in dono
        assert "raio-x-no-app" in vend and "raio-x-de-segunda" not in vend
    assert "raio-x-de-segunda" in {n["chave"] for n in nv.listar(pool, 1, papel="gestor")}


def test_reaplicar_a_208_nao_duplica_nem_troca_id(pool):
    antes = {k: v["id"] for k, v in _raio_x(pool).items()}
    depois = {k: v["id"] for k, v in _raio_x(pool).items()}
    assert antes == depois and len(antes) == 2


def _raio_x_dono(pool) -> dict:
    """Os avisos da Peça 3 (210): a tela do dono, e o motivo de perda no app."""
    with pool.connection() as c:
        c.execute((BASE / "210_novidade_raio_x_dono.sql").read_text(encoding="utf-8"))
        c.commit()
        rows = c.execute("""select chave, tipo, publico, pra_quem, resumo, link, id from novidades
                            where chave in ('raio-x-do-dono', 'motivo-de-perda-no-app')""").fetchall()
    return {r[0]: {"tipo": r[1], "publico": r[2], "pra_quem": list(r[3]), "resumo": r[4],
                   "link": r[5], "id": r[6]} for r in rows}


def test_os_avisos_da_peca_3_miram_certo(pool):
    a = _raio_x_dono(pool)
    assert set(a) == {"raio-x-do-dono", "motivo-de-perda-no-app"}
    assert a["raio-x-do-dono"]["publico"] == "todos" and sorted(a["raio-x-do-dono"]["pra_quem"]) == ["dono", "gestor"]
    assert a["raio-x-do-dono"]["link"] == "/painel/raio-x"
    assert a["motivo-de-perda-no-app"]["pra_quem"] == ["vendedor"] and a["motivo-de-perda-no-app"]["link"] == "/cockpit"
    for chave, x in a.items():
        assert x["tipo"] == "novidade" and x["resumo"], chave
    vend = {n["chave"] for n in nv.listar(pool, 3, papel="vendedor")}
    dono = {n["chave"] for n in nv.listar(pool, 3, papel="dono")}
    assert "motivo-de-perda-no-app" in vend and "raio-x-do-dono" not in vend
    assert "raio-x-do-dono" in dono and "motivo-de-perda-no-app" not in dono
    antes = {k: v["id"] for k, v in a.items()}
    assert {k: v["id"] for k, v in _raio_x_dono(pool).items()} == antes
