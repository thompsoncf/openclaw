"""A proposta enviada tem que ter card no funil — e o card certo.

POR QUE ISSO EXISTE
O gatilho `orcamento_enviado` procura a proposta por `prospeccao.orcamento_id`.
Proposta solta não tem card pra mover, e aí não adianta canal nenhum registrar
envio: o funil segue mentindo e alguém arrasta na mão.

Medido na conta 34 em 19/08/2026, com o gatilho ligado: 4 propostas, ZERO ligadas,
e as 4 com um lead de mesmo telefone do outro lado. O card que chegou em "Proposta"
foi arrastado à mão.

O QUE SE PROVA AQUI
 1. as três portas, na ordem — escolhido na busca, achado pelo telefone, criado;
 2. o EMPATE não é adivinhado, porque amarrar no lead errado esconde a proposta;
 3. RASCUNHO não cria card: só o envio cria;
 4. tentativa de envio que FALHOU também não cria;
 5. desfecho (ganho/perdido) não é atropelado por um reenvio;
 6. e nada disso derruba o envio quando o banco reclama.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import proposta_email as pe
from finance import proposta_lead as pl

_SCHEMA = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, whatsapp text, telefone text, email text,
  cidade text, uf text, segmento text, origem text, status text default 'novo',
  estagio text default 'lead', orcamento_id bigint, criado_por bigint,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, status text default 'rascunho',
  cliente text, empresa text, cnpj text, whatsapp text, telefone text, email text,
  cidade text, uf text, segmento text, token text,
  atualizado_em timestamptz default now());
create table orcamento_envios (id bigserial primary key, conta_id bigint,
  orcamento_id bigint, canal text default 'email', destino text default '',
  remetente text default '', ok boolean default true, erro text default '',
  por text default '', criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
"""
CONTA = 7


@pytest.fixture()
def pool():
    dbname = "zaq_proposta_lead"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SCHEMA)
        c.commit()
    yield p
    p.close()


def _orc(pool, **kw):
    campos = {"cliente": "", "empresa": "Festa da Ana", "cnpj": "", "whatsapp": "",
              "telefone": "", "email": "", "cidade": "", "uf": "", "segmento": ""}
    campos.update(kw)
    with pool.connection() as c:
        oid = c.execute(
            "insert into orcamentos (conta_id, cliente, empresa, cnpj, whatsapp, telefone,"
            " email, cidade, uf, segmento) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (CONTA, campos["cliente"], campos["empresa"], campos["cnpj"], campos["whatsapp"],
             campos["telefone"], campos["email"], campos["cidade"], campos["uf"],
             campos["segmento"])).fetchone()[0]
        c.commit()
    return oid


def _lead(pool, **kw):
    campos = {"empresa": "Ana Silva", "whatsapp": "", "telefone": "", "email": "",
              "status": "novo", "estagio": "lead", "orcamento_id": None}
    campos.update(kw)
    with pool.connection() as c:
        lid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, telefone, email, status,"
            " estagio, orcamento_id) values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (CONTA, campos["empresa"], campos["whatsapp"], campos["telefone"],
             campos["email"], campos["status"], campos["estagio"],
             campos["orcamento_id"])).fetchone()[0]
        c.commit()
    return lid


def _lead_do(pool, oid):
    with pool.connection() as c:
        r = c.execute("select id, status from prospeccao where conta_id=%s and orcamento_id=%s",
                      (CONTA, oid)).fetchone()
    return r


# ═══════════════════════ porta 1: escolhido na busca ═══════════════════════

def test_ligar_amarra_e_traz_o_card_pra_proposta(pool):
    lid = _lead(pool, status="contatado")
    oid = _orc(pool)
    with pool.connection() as c:
        assert pl.ligar(c, CONTA, lid, oid) is True
        c.commit()
    assert _lead_do(pool, oid) == (lid, "proposta")


def test_ligar_registra_o_movimento(pool):
    """Sem a linha no histórico, ninguém consegue responder depois por que o card
    andou — e a régua inteira existe pra que isso deixe de ser mistério."""
    lid = _lead(pool, status="novo")
    oid = _orc(pool)
    with pool.connection() as c:
        pl.ligar(c, CONTA, lid, oid, 42)
        c.commit()
    with pool.connection() as c:
        r = c.execute("select de, para, motivo, membro_id from funil_movimentos "
                      "where prospeccao_id=%s", (lid,)).fetchone()
    assert r == ("novo", "proposta", "orcamento", 42)


def test_ligar_nao_rouba_lead_que_ja_tem_proposta(pool):
    """Sobrescrever aqui trocaria a proposta de um cliente pela de outro, calado."""
    antigo = _orc(pool)
    lid = _lead(pool, orcamento_id=antigo, status="proposta")
    novo = _orc(pool)
    with pool.connection() as c:
        assert pl.ligar(c, CONTA, lid, novo) is False
        c.commit()
    with pool.connection() as c:
        assert c.execute("select orcamento_id from prospeccao where id=%s",
                         (lid,)).fetchone()[0] == antigo


def test_ligar_nao_atropela_desfecho(pool):
    """Negócio ganho ou perdido é desfecho. Um reenvio não devolve o card pro meio
    do funil — quem fechou, fechou."""
    for desfecho in ("ganho", "perdido"):
        lid = _lead(pool, status=desfecho)
        oid = _orc(pool)
        with pool.connection() as c:
            assert pl.ligar(c, CONTA, lid, oid) is True
            c.commit()
        assert _lead_do(pool, oid) == (lid, desfecho)


def test_ligar_e_por_conta(pool):
    lid = _lead(pool)
    oid = _orc(pool)
    with pool.connection() as c:
        assert pl.ligar(c, 999, lid, oid) is False
        c.commit()
    assert _lead_do(pool, oid) is None


# ═══════════════════════ porta 2: achado pelo telefone ═══════════════════════

def test_acha_o_lead_mesmo_com_ddi_e_formatacao_diferentes(pool):
    """O caso real: o orçamento guarda "(86) 9 9516-7171" e o lead "5586995167171".
    Comparar o número inteiro não acha nada — por isso a comparação é pelo fim."""
    lid = _lead(pool, whatsapp="5586995167171")
    oid = _orc(pool, whatsapp="(86) 9 9516-7171")
    r = pl.garantir(pool, CONTA, oid)
    assert r == {"lead_id": lid, "como": "ligado"}
    assert _lead_do(pool, oid) == (lid, "proposta")


def test_acha_pelo_email_quando_nao_ha_telefone(pool):
    lid = _lead(pool, email="Ana@Exemplo.com")
    oid = _orc(pool, email="ana@exemplo.com")
    assert pl.garantir(pool, CONTA, oid)["como"] == "ligado"
    assert _lead_do(pool, oid)[0] == lid


def test_dois_leads_com_o_mesmo_telefone_nao_amarra_nenhum(pool):
    """Amarrar no errado enterra a proposta no lead trocado, e ninguém descobre.
    Sem vínculo é visível, e visível se conserta."""
    _lead(pool, empresa="Ana", whatsapp="5586995167171")
    _lead(pool, empresa="Ana (2)", whatsapp="86995167171")
    oid = _orc(pool, whatsapp="86995167171")
    assert pl.garantir(pool, CONTA, oid) == {"lead_id": None, "como": "empate"}
    assert _lead_do(pool, oid) is None


def test_lead_que_bate_mas_ja_tem_proposta_nao_vira_card_duplicado(pool):
    outro = _orc(pool)
    _lead(pool, whatsapp="5586995167171", orcamento_id=outro)
    oid = _orc(pool, whatsapp="86995167171")
    assert pl.garantir(pool, CONTA, oid)["como"] == "empate"
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 1, "criou um card duplicado da mesma pessoa"


def test_nao_atravessa_conta_pra_achar_lead(pool):
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, empresa, whatsapp) "
                  "values (99,'Da vizinha','5586995167171')")
        c.commit()
    oid = _orc(pool, whatsapp="86995167171", empresa="Festa da Ana")
    r = pl.garantir(pool, CONTA, oid)
    assert r["como"] == "criado", "amarrou (ou tentou) num lead de outra empresa"


# ═══════════════════════ porta 3: o card nasce ═══════════════════════

def test_sem_lead_nenhum_o_card_nasce_ja_em_proposta(pool):
    """Nascer em "Novo" poria na primeira coluna um cliente que já está com a
    proposta na mão — e alguém iria fazer primeiro contato com quem já negociou."""
    oid = _orc(pool, empresa="Festa da Bia", whatsapp="86999998888",
               email="bia@exemplo.com", cidade="Teresina", uf="PI")
    r = pl.garantir(pool, CONTA, oid, membro_id=5)
    assert r["como"] == "criado"
    with pool.connection() as c:
        row = c.execute(
            "select empresa, whatsapp, email, cidade, uf, status, estagio, orcamento_id,"
            " vendedor_id from prospeccao where id=%s", (r["lead_id"],)).fetchone()
    assert row == ("Festa da Bia", "86999998888", "bia@exemplo.com", "Teresina", "PI",
                   "proposta", "lead", oid, 5)


def test_proposta_sem_nome_nao_cria_card(pool):
    """Card sem nome não serve pra ninguém trabalhar — vira lixo no funil."""
    oid = _orc(pool, empresa="", cliente="")
    assert pl.garantir(pool, CONTA, oid) == {"lead_id": None, "como": "sem_dados"}
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 0


def test_ja_ligado_nao_faz_nada_e_e_idempotente(pool):
    lid = _lead(pool)
    oid = _orc(pool)
    with pool.connection() as c:
        pl.ligar(c, CONTA, lid, oid)
        c.commit()
    for _ in range(3):
        assert pl.garantir(pool, CONTA, oid) == {"lead_id": lid, "como": "ja_tinha"}
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 1


def test_orcamento_que_nao_existe_nao_quebra(pool):
    assert pl.garantir(pool, CONTA, 999999) == {"lead_id": None, "como": "sem_dados"}


# ═══════════ o registro de envio é quem puxa tudo isso ═══════════

def test_registrar_envio_cria_o_card(pool):
    """`registrar` virou o ponto único por onde todo canal passa — e é ele quem
    garante o card. Espalhar isso pelas rotas faria a próxima esquecer."""
    oid = _orc(pool, empresa="Festa da Carla", whatsapp="86988887777")
    pe.registrar(pool, CONTA, oid, destino="carla@x.com", remetente_usado="eu@x.com",
                 ok=True, canal="email", por="9")
    lead = _lead_do(pool, oid)
    assert lead is not None and lead[1] == "proposta"


def test_envio_que_falhou_nao_cria_card(pool):
    """Tentativa que estourou não é proposta entregue. Criar card aqui encheria o
    funil de negócio que o cliente nunca viu."""
    oid = _orc(pool, empresa="Festa da Duda", whatsapp="86977776666")
    pe.registrar(pool, CONTA, oid, destino="duda@x.com", remetente_usado="eu@x.com",
                 ok=False, erro="caixa recusou", canal="email", por="9")
    assert _lead_do(pool, oid) is None
    with pool.connection() as c:
        assert c.execute("select ok, canal from orcamento_envios where orcamento_id=%s",
                         (oid,)).fetchone() == (False, "email"), "a falha tem que ficar registrada"


def test_rascunho_salvo_e_nunca_enviado_nao_cria_card(pool):
    """A porta 3 abre no ENVIO, não no salvar. Meia proposta montada e abandonada
    não é negociação."""
    oid = _orc(pool, empresa="Festa que não saiu", whatsapp="86966665555")
    assert _lead_do(pool, oid) is None
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 0
    assert oid


def test_cada_canal_grava_o_seu_nome(pool):
    oid = _orc(pool, empresa="Festa da Elis")
    for canal in ("email", "whatsapp", "link"):
        pe.registrar(pool, CONTA, oid, destino="", remetente_usado="", ok=True, canal=canal)
    with pool.connection() as c:
        canais = [r[0] for r in c.execute(
            "select canal from orcamento_envios where orcamento_id=%s order by canal",
            (oid,)).fetchall()]
    assert canais == ["email", "link", "whatsapp"]


def test_o_canal_tem_rotulo_honesto_pro_link(pool):
    """"Link copiado" não é "Enviado": no e-mail e no WhatsApp o Zaq entregou; no
    link ele só sabe que o endereço saiu da tela."""
    assert pe.CANAL_ROT["email"] == "E-mail"
    assert pe.CANAL_ROT["whatsapp"] == "WhatsApp"
    assert pe.CANAL_ROT["link"] == "Link copiado"


def test_historico_devolve_o_canal(pool):
    oid = _orc(pool, empresa="Festa da Fê")
    pe.registrar(pool, CONTA, oid, destino="fe@x.com", remetente_usado="eu@x.com",
                 ok=True, canal="whatsapp")
    h = pe.historico(pool, CONTA, oid)
    assert h and h[0]["canal"] == "whatsapp" and h[0]["canal_rot"] == "WhatsApp"


def test_envio_sobrevive_a_falha_do_funil(pool, monkeypatch):
    """Proposta entregue não pode virar erro na tela porque o funil não conseguiu
    se organizar."""
    def explode(*a, **k):
        raise RuntimeError("funil fora do ar")
    monkeypatch.setattr(pl, "garantir", explode)
    oid = _orc(pool, empresa="Festa da Gi")
    pe.registrar(pool, CONTA, oid, destino="gi@x.com", remetente_usado="eu@x.com", ok=True)
    with pool.connection() as c:
        assert c.execute("select count(*) from orcamento_envios where orcamento_id=%s",
                         (oid,)).fetchone()[0] == 1, "o registro do envio se perdeu"
