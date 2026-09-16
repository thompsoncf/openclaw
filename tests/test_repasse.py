"""Passar o lead pro colega: a regra, o rastro e o caminho do dedo (migração 267).

POR QUE ESTE ARQUIVO EXISTE
O pedido do dono em 16/09/2026 foi "preciso que com o próprio vendedor mude de quem
é quem". Duas coisas podem dar errado aqui, e as duas custam caro:

1. O PORTÃO FROUXO. Se um vendedor conseguir puxar o lead do colega, isso é roubo de
   lead com dois campos de formulário — num time comissionado, briga na certa. Por
   isso a regra é testada NO MOTOR, e por POST direto: quem ataca não usa o botão.

2. O RASTRO QUE NÃO ACONTECE. A troca sem registro é o estado de antes — e é ele
   que impedia os vendedores de se entenderem. O teste da transação abaixo falha se
   alguém "melhorar" o insert pra um `try/except` tolerante.

E o percurso inteiro (GET na Fila → o `<form>` lido do HTML → POST → a linha no
banco → o aviso na Fila de quem recebeu) está aqui pelo motivo do #705: naquele dia
havia teste da consulta e teste do HTML, e o defeito morava exatamente no pedaço do
meio que ninguém cobria.
"""
import os
import re

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import repasse as rp
from tests.test_cockpit import _BASE_SQL as _SQL
from web import painel_cockpit as pc

_MIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "db", "migracoes", "267_lead_repasse.sql")


# --------------------------------------------------------------- a regra, sem banco
def test_o_vendedor_passa_o_que_e_dele():
    assert rp.pode_passar("vendedor", membro_id=7, dono_atual=7) is True


def test_o_vendedor_nao_pega_o_do_colega():
    """O coração da regra: dar é abrir mão do que é seu, pegar é mexer no do outro."""
    assert rp.pode_passar("vendedor", membro_id=7, dono_atual=9) is False


def test_o_vendedor_nao_pega_o_orfao():
    """Lead sem dono não é de ninguém — deixar um vendedor repassá-lo seria dar a ele
    o poder de escolher o dono de um lead que nunca foi seu. Órfão é do rodízio."""
    assert rp.pode_passar("vendedor", membro_id=7, dono_atual=None) is False


@pytest.mark.parametrize("papel", ["dono", "gestor"])
def test_dono_e_gestor_passam_nos_dois_sentidos(papel):
    """"passa direto e o gestor também passa" — dono, 16/09/2026."""
    assert rp.pode_passar(papel, membro_id=1, dono_atual=9) is True
    assert rp.pode_passar(papel, membro_id=1, dono_atual=None) is True


def test_papel_desconhecido_nao_passa():
    """Falha FECHADA: papel que a sessão não soube dizer não ganha a carteira alheia."""
    assert rp.pode_passar(None, membro_id=7, dono_atual=7) is False
    assert rp.pode_passar("financeiro", membro_id=7, dono_atual=7) is False


# ------------------------------------------------------------------------ com banco
@pytest.fixture()
def cliente(monkeypatch):
    dbname = "zaq_repasse"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=4, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_SQL)
        from web.painel_servicos import _criar_orcamentos
        _criar_orcamentos(c)
        # a migração DE VERDADE: escrever o create table à mão aqui testaria a minha
        # cópia em vez do que o Render vai aplicar em produção
        with open(_MIG, encoding="utf-8") as f:
            c.execute(f.read())
        c.commit()

    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_selo", lambda conta_id: "")
    from finance import webpush
    monkeypatch.setattr(webpush, "chave_publica", lambda: None)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-do-repasse")
    app.include_router(pc.router)

    @app.post("/_entrar")
    def _entrar(request: Request, conta_id: int, membro_id: int, papel: str = "vendedor"):
        request.session.clear()
        request.session["conta_id"] = conta_id
        request.session["membro_id"] = membro_id
        request.session["papel"] = papel
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _cena(pool, *, numero="5511988887777"):
    """Uma conta de eventos, dois vendedores e um lead do primeiro, conversando."""
    with pool.connection() as c:
        n = c.execute("insert into nichos (nome, slug, tipo) values "
                      "('Eventos','eventos','servico') returning id").fetchone()[0]
        conta = c.execute("insert into contas (nome, nicho_id) values "
                          "('Prime Eventos',%s) returning id", (n,)).fetchone()[0]
        jac = c.execute("insert into membros (conta_id, nome, email, papel, ativo) values "
                        "(%s,'Jacqueline Alves','jac@x.com','vendedor',true) returning id",
                        (conta,)).fetchone()[0]
        thi = c.execute("insert into membros (conta_id, nome, email, papel, ativo) values "
                        "(%s,'Thiago Ramos','thi@x.com','vendedor',true) returning id",
                        (conta,)).fetchone()[0]
        lead = c.execute(
            "insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp, estagio) "
            "values (%s,%s,'Lêda Lopes',%s,'lead') returning id",
            (conta, jac, numero)).fetchone()[0]
        conv = c.execute(
            "insert into conversas (conta_id, prospeccao_id, canal, responsavel_membro_id) "
            "values (%s,%s,'whatsapp',%s) returning id", (conta, lead, jac)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, canal, direcao, texto) "
                  "values (%s,'whatsapp','entrada','oi, queria um orçamento')", (conv,))
        c.commit()
    return conta, jac, thi, lead


def _dono_do_lead(pool, lead):
    with pool.connection() as c:
        return c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0]


def test_passar_troca_o_dono_a_conversa_e_deixa_linha(cliente):
    conta, jac, thi, lead = _cena(cliente.pool)

    r = rp.passar(cliente.pool, conta, lead, thi, por_id=jac, papel="vendedor",
                  motivo="Você já vinha atendendo ela")
    assert r["ok"], r

    with cliente.pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == thi
        # a conversa vai junto: sem isso o inbox segue mostrando o nome antigo
        assert c.execute("select responsavel_membro_id from conversas where prospeccao_id=%s",
                         (lead,)).fetchone()[0] == thi
        linha = c.execute("select de_membro_id, para_membro_id, por_membro_id, motivo, visto_em "
                          "from lead_repasse where prospeccao_id=%s", (lead,)).fetchone()
    assert linha[:3] == (jac, thi, jac)
    assert linha[3] == "Você já vinha atendendo ela"
    assert linha[4] is None, "nasce por ver: é isso que faz o aviso aparecer na Fila dele"


def test_o_rastro_e_a_troca_sao_a_MESMA_transacao(cliente):
    """Se a linha do histórico não puder ser gravada, a troca NÃO acontece.

    Sem isto o módulo poderia repassar sem rastro — que é exatamente o estado de
    antes, e o que impede os vendedores de se entenderem. O jeito de provar é
    quebrar o insert do histórico e conferir que o dono do lead não mudou."""
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute("alter table lead_repasse add column obrigatorio text not null")
        c.commit()

    with pytest.raises(Exception):
        rp.passar(cliente.pool, conta, lead, thi, por_id=jac, papel="vendedor")

    assert _dono_do_lead(cliente.pool, lead) == jac, \
        "o lead trocou de mãos sem deixar rastro — é o estado que a 267 veio acabar"


def test_o_vendedor_nao_puxa_o_lead_do_colega_nem_pelo_motor(cliente):
    conta, jac, thi, lead = _cena(cliente.pool)
    r = rp.passar(cliente.pool, conta, lead, thi, por_id=thi, papel="vendedor")
    assert r == {"ok": False, "erro": "sem_permissao"}
    assert _dono_do_lead(cliente.pool, lead) == jac


def test_destino_fora_da_equipe_nao_recebe(cliente):
    """Membro desativado não recebe lead: o lead ficaria numa Fila que ninguém abre."""
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute("update membros set ativo=false where id=%s", (thi,))
        c.commit()
    r = rp.passar(cliente.pool, conta, lead, thi, por_id=jac, papel="vendedor")
    assert r["erro"] == "destino_invalido"
    assert _dono_do_lead(cliente.pool, lead) == jac


def test_outra_conta_nao_alcanca_o_lead(cliente):
    """O `conta_id` do POST vem da SESSÃO, mas o motor confere assim mesmo: uma rota
    nova que esqueça o escopo não pode virar porta pra carteira de outra empresa."""
    conta, jac, thi, lead = _cena(cliente.pool)
    r = rp.passar(cliente.pool, conta + 999, lead, thi, por_id=jac, papel="dono")
    assert r["erro"] == "lead_invalido"
    assert _dono_do_lead(cliente.pool, lead) == jac


def test_passar_pra_quem_ja_tem_nao_gera_linha(cliente):
    conta, jac, thi, lead = _cena(cliente.pool)
    r = rp.passar(cliente.pool, conta, lead, jac, por_id=jac, papel="vendedor")
    assert r["erro"] == "ja_e_dele"
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from lead_repasse").fetchone()[0] == 0


# ------------------------------------------------- o percurso inteiro, como o dedo
def _form_de_passar(html: str):
    """O `<form>` de passar como o NAVEGADOR o lê. Lido do HTML de propósito: campo
    escrito à mão no teste passaria mesmo que a tela mandasse o POST pra outro
    lugar — que é o defeito que este teste existe pra pegar."""
    bloco = re.search(r"<form[^>]*action='([^']*/passar)'[^>]*>(.*?)</form>", html, re.S)
    if not bloco:
        return None, []
    acao, corpo = bloco.group(1), bloco.group(2)
    opcoes = re.findall(r"<option value='(\d+)'>([^<]*)</option>", corpo)
    return acao, opcoes


def test_do_dedo_ate_o_banco_e_o_aviso_na_fila_de_quem_recebeu(cliente):
    conta, jac, thi, lead = _cena(cliente.pool)
    cliente.post(f"/_entrar?conta_id={conta}&membro_id={jac}")

    html = cliente.get(f"/cockpit/lead/{lead}").text
    acao, opcoes = _form_de_passar(html)
    assert acao, "a folha de ações não trouxe o formulário de passar"
    assert [int(v) for v, _ in opcoes] == [thi], "a lista tem que ser só os COLEGAS"

    r = cliente.post(acao, data={"para": str(thi), "motivo": "Ela já é sua cliente"})
    assert r.status_code == 303
    # ele NÃO volta pro lead: o lead não é mais dele
    assert r.headers["location"].rstrip("/").endswith("/cockpit")
    assert _dono_do_lead(cliente.pool, lead) == thi

    # ...e o lead sai da Fila de quem passou
    assert "Lêda Lopes" not in cliente.get("/cockpit").text

    # QUEM RECEBEU É AVISADO, com o motivo — passar calado é o lead sumir da tela de
    # um e aparecer na do outro, que é o que o dono não quer
    cliente.post(f"/_entrar?conta_id={conta}&membro_id={thi}")
    fila = cliente.get("/cockpit").text
    assert "te passou" in fila, "quem recebeu não foi avisado"
    assert "Jacqueline" in fila and "Ela já é sua cliente" in fila

    # abrir o lead limpa o aviso: ir ver É a ação que o aviso pedia
    assert cliente.get(f"/cockpit/lead/{lead}").status_code == 200
    with cliente.pool.connection() as c:
        assert c.execute("select visto_em from lead_repasse where prospeccao_id=%s",
                         (lead,)).fetchone()[0] is not None
    assert "te passou" not in cliente.get("/cockpit").text


def test_o_post_de_passar_o_lead_alheio_e_recusado_na_rota(cliente):
    """O ataque não usa o botão: manda o POST direto com o id do lead do colega."""
    conta, jac, thi, lead = _cena(cliente.pool)
    cliente.post(f"/_entrar?conta_id={conta}&membro_id={thi}")

    r = cliente.post(f"/cockpit/lead/{lead}/passar", data={"para": str(thi)})
    assert r.status_code == 303
    assert _dono_do_lead(cliente.pool, lead) == jac, "roubo de lead por formulário"
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from lead_repasse").fetchone()[0] == 0


def test_quem_ja_atendeu_acha_o_outro_lead_do_mesmo_numero(cliente):
    """O caso da Lêda: DOIS leads, o mesmo número, nenhum repasse entre eles.

    É o histórico que a tabela nova não tem como saber — quando o mesmo número
    escreve de novo nasce lead novo e o rodízio entrega pro próximo da fila. Sem
    esta leitura o bloco "quem já atendeu" nasceria vazio justamente nos casos que
    motivaram o pedido."""
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        # o mesmo número, escrito de outro jeito: o casamento é pelos 8 últimos
        antigo = c.execute(
            "insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp) "
            "values (%s,%s,'Lêda','+55 (11) 98888-7777') returning id",
            (conta, thi)).fetchone()[0]
        c.commit()

    achou = rp.ja_atendeu_o_numero(cliente.pool, conta, lead)
    assert [a["lead_id"] for a in achou] == [antigo]
    assert achou[0]["vendedor"] == "Thiago Ramos"

    cliente.post(f"/_entrar?conta_id={conta}&membro_id={jac}")
    ficha = cliente.get(f"/cockpit/lead/{lead}/ficha").text
    assert "Quem já atendeu" in ficha
    assert "Thiago" in ficha and f"/cockpit/lead/{antigo}" in ficha


def test_numero_de_outra_conta_nao_aparece_no_quem_ja_atendeu(cliente):
    """Escopo: o mesmo celular pode ser cliente de duas empresas do sistema."""
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') returning id").fetchone()[0]
        c.execute("insert into prospeccao (conta_id, empresa, whatsapp) "
                  "values (%s,'Lêda','5511988887777')", (outra,))
        c.commit()
    assert rp.ja_atendeu_o_numero(cliente.pool, conta, lead) == []


def test_lead_sem_numero_nao_casa_com_todo_mundo(cliente):
    """A armadilha do `right(...,8)` sobre vazio: sem a checagem de comprimento, um
    lead sem telefone casaria com TODOS os outros sem telefone — e a ficha diria que
    meia carteira já atendeu este cliente."""
    conta, jac, thi, _ = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        mudo = c.execute("insert into prospeccao (conta_id, vendedor_id, empresa) "
                         "values (%s,%s,'Sem número') returning id",
                         (conta, jac)).fetchone()[0]
        c.execute("insert into prospeccao (conta_id, vendedor_id, empresa) "
                  "values (%s,%s,'Outro sem número')", (conta, thi))
        c.commit()
    assert rp.ja_atendeu_o_numero(cliente.pool, conta, mudo) == []


def test_o_aviso_nao_volta_pra_quem_passou(cliente):
    """Quem passou não precisa ser avisado do que ele mesmo fez."""
    conta, jac, thi, lead = _cena(cliente.pool)
    assert rp.passar(cliente.pool, conta, lead, thi, por_id=jac, papel="vendedor")["ok"]
    assert rp.recebidos_novos(cliente.pool, conta, jac) == []
    assert len(rp.recebidos_novos(cliente.pool, conta, thi)) == 1


def test_quem_pega_pra_si_nao_se_avisa(cliente):
    """O gestor puxa o lead PRA SI — `para` e `por` são a mesma pessoa.

    É o único caso em que o aviso seria o app dizendo à pessoa o que ela acabou de
    fazer, e é por isso que `recebidos_novos` filtra por `por_membro_id`. Sem esse
    filtro o gestor abriria a Fila com "Gestor te passou a Lêda" — assinado por ele
    mesmo."""
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        gestor = c.execute("insert into membros (conta_id, nome, email, papel, ativo) values "
                           "(%s,'Gestora Rita','rita@x.com','gestor',true) returning id",
                           (conta,)).fetchone()[0]
        c.commit()
    assert rp.passar(cliente.pool, conta, lead, gestor, por_id=gestor, papel="gestor")["ok"]
    assert rp.recebidos_novos(cliente.pool, conta, gestor) == []
    # ...mas a troca continua no histórico: é dela que alguém vai reclamar depois
    assert len(rp.historico(cliente.pool, conta, lead)) == 1


def test_a_troca_do_dono_tambem_vira_historico(cliente):
    """`cockpit_dono.reatribuir` delega pro mesmo motor: guardar só as trocas do
    vendedor deixaria o histórico mentindo por omissão justamente nos casos em que
    alguém questiona a decisão de cima."""
    from finance import cockpit_dono as cd
    conta, jac, thi, lead = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        dono = c.execute("insert into membros (conta_id, nome, email, papel, ativo) values "
                         "(%s,'Manoel','m@x.com','dono',true) returning id",
                         (conta,)).fetchone()[0]
        c.commit()
    assert cd.reatribuir(cliente.pool, conta, lead, thi, por_id=dono, papel="dono")["ok"]
    hist = rp.historico(cliente.pool, conta, lead)
    assert len(hist) == 1
    assert hist[0]["de"] == "Jacqueline Alves" and hist[0]["por"] == "Manoel"
