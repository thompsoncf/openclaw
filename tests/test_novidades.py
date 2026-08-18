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
                     criado_em timestamptz not null default now())""")
        c.execute((BASE / "174_novidades.sql").read_text(encoding="utf-8"))
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
    assert antes == depois and len(ids) == 3


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
