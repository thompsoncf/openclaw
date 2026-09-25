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
  cidade text, uf text, segmento text, origem text, origem_codigo text, status text default 'novo',
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


# ═════════════ 24/09/2026: o card que não nascia, e as portas novas ═════════════

def _unico_de_cnpj(pool):
    """O índice que existe em produção (075): único parcial por (conta, cnpj)."""
    with pool.connection() as c:
        c.execute("create unique index if not exists uq_prospeccao_conta_cnpj "
                  "on prospeccao (conta_id, cnpj) where cnpj is not null")
        c.commit()


def _membros(pool):
    with pool.connection() as c:
        c.execute("create table if not exists membros (id bigserial primary key, conta_id bigint, nome text)")
        c.execute("alter table orcamentos add column if not exists criado_por text")
        c.commit()


def test_dois_cards_sem_cnpj_nascem_na_mesma_conta(pool):
    """O defeito da Prime: `_criar` gravava cnpj '' e o único parcial só perdoa
    NULO. O primeiro card sem CNPJ (Lillian Paz, 14/09) ocupou a vaga, e daí em
    diante todo card de proposta sem CNPJ morria calado — três propostas, duas já
    com contrato assinado, ficaram sem card."""
    _unico_de_cnpj(pool)
    o1 = _orc(pool, empresa="Lillian Paz", whatsapp="86999990001")
    o2 = _orc(pool, empresa="Viviane Alves", whatsapp="86998479896")
    assert pl.garantir(pool, CONTA, o1)["como"] == "criado"
    assert pl.garantir(pool, CONTA, o2)["como"] == "criado"
    with pool.connection() as c:
        cnpjs = c.execute("select cnpj from prospeccao where conta_id=%s and orcamento_id in (%s,%s)",
                          (CONTA, o1, o2)).fetchall()
    assert cnpjs == [(None,), (None,)]


def test_na_aprovacao_o_card_nasce_com_quem_fez_o_orcamento(pool):
    """Proposta mandada pelo link colado no WhatsApp nunca passava pelo "enviar",
    que era a única porta do card. A aprovação e a assinatura abrem outra — e o
    vendedor do card é quem fez o orçamento (a régua do contrato)."""
    _membros(pool)
    with pool.connection() as c:
        jaque = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') returning id",
                          (CONTA,)).fetchone()[0]
        c.commit()
    oid = _orc(pool, empresa="Josinalva Costa Gomes", whatsapp="8688400719")
    with pool.connection() as c:
        c.execute("update orcamentos set criado_por=%s where id=%s", (str(jaque), oid))
        c.commit()
    r = pl.garantir_pelo_orcamento(pool, CONTA, oid)
    assert r["como"] == "criado"
    with pool.connection() as c:
        vend = c.execute("select vendedor_id from prospeccao where id=%s", (r["lead_id"],)).fetchone()[0]
    assert vend == jaque
    # a assinatura chama de novo: não duplica
    assert pl.garantir_pelo_orcamento(pool, CONTA, oid) == {"lead_id": r["lead_id"], "como": "ja_tinha"}


def test_quem_fez_o_orcamento_de_outra_conta_nao_vira_vendedor(pool):
    _membros(pool)
    with pool.connection() as c:
        de_fora = c.execute("insert into membros (conta_id, nome) values (%s,'Outro') returning id",
                            (CONTA + 1,)).fetchone()[0]
        c.commit()
    oid = _orc(pool, empresa="Cliente X", whatsapp="86911112222")
    with pool.connection() as c:
        c.execute("update orcamentos set criado_por=%s where id=%s", (str(de_fora), oid))
        c.commit()
    r = pl.garantir_pelo_orcamento(pool, CONTA, oid)
    with pool.connection() as c:
        vend = c.execute("select vendedor_id from prospeccao where id=%s", (r["lead_id"],)).fetchone()[0]
    assert r["como"] == "criado" and vend is None


def test_na_aprovacao_ela_amarra_no_lead_que_ja_existia(pool):
    """Porta 2 do `garantir`: um lead só com o mesmo telefone — amarra, não cria."""
    _membros(pool)
    lid = _lead(pool, empresa="Renata", whatsapp="+5586999461564", status="contatado")
    oid = _orc(pool, empresa="Renata Costa", whatsapp="86999461564")
    assert pl.garantir_pelo_orcamento(pool, CONTA, oid) == {"lead_id": lid, "como": "ligado"}


def test_a_assinatura_e_a_aprovacao_garantem_o_card_antes_de_andar():
    """A ORDEM importa: sem card não há o que levar ao fechamento nem a quê ligar a
    festa. Lido na fonte, porque as duas chamadas vivem no meio de fluxos longos
    (financeiro, contrato, agenda) que este arquivo não monta."""
    import inspect
    from finance import contrato as ctr
    from web import proposta as prop
    src = inspect.getsource(ctr.assinar)
    assert src.index("garantir_pelo_orcamento") < src.index("marcar_por_assinatura")
    src = inspect.getsource(prop._pos_assinatura)
    assert src.index("garantir_pelo_orcamento") < src.index("_reservar_na_agenda")


def _etapas(pool):
    with pool.connection() as c:
        c.execute("create table if not exists funil_etapas (conta_id bigint, chave text, ordem int)")
        c.execute("delete from funil_etapas where conta_id=%s", (CONTA,))
        c.execute("insert into funil_etapas values (%s,'contatado',20),(%s,'proposta',50),"
                  "(%s,'orcamento_assinado',60),(%s,'ganho',900),(%s,'evento_realizado',950),"
                  "(%s,'perdido',910)", (CONTA,) * 6)
        c.commit()


def test_card_numa_etapa_depois_da_proposta_nao_volta_quando_o_cliente_aprova(pool):
    """A aprovação passou a chamar o `garantir` (24/09/2026). O vendedor que já
    tinha levado o card pra uma coluna depois da proposta ("Orçamento assinado")
    não pode vê-lo voltar pra "Negociação" justo quando o cliente aprovou."""
    _etapas(pool)
    lid = _lead(pool, empresa="Carolina", whatsapp="86977776666", status="orcamento_assinado")
    oid = _orc(pool, empresa="Carolina Costa", whatsapp="86977776666")
    assert pl.garantir(pool, CONTA, oid) == {"lead_id": lid, "como": "ligado"}
    assert _lead_do(pool, oid) == (lid, "orcamento_assinado")


def test_card_antes_da_proposta_ainda_anda_pra_proposta(pool):
    _etapas(pool)
    lid = _lead(pool, empresa="Dora", whatsapp="86955554444", status="contatado")
    oid = _orc(pool, empresa="Dora Lima", whatsapp="86955554444")
    assert pl.garantir(pool, CONTA, oid)["como"] == "ligado"
    assert _lead_do(pool, oid) == (lid, "proposta")


def test_o_cliente_que_volta_nao_e_amarrado_a_venda_fechada(pool):
    """A segunda festa da mesma cliente. O único lead com o telefone dela é o da
    festa já vendida (pós-venda, sem orçamento amarrado): amarrar esconderia a
    negociação nova ali e trocaria a data da festa vendida pela da nova (regra 0).
    Não amarra e não cria outro card: fica "sem card", que é visível."""
    _etapas(pool)
    lid = _lead(pool, empresa="Bianca", whatsapp="86988887777", status="evento_realizado")
    oid = _orc(pool, empresa="Bianca Oliveira", whatsapp="86988887777")
    assert pl.garantir(pool, CONTA, oid) == {"lead_id": None, "como": "empate"}
    with pool.connection() as c:
        r = c.execute("select status, orcamento_id from prospeccao where id=%s", (lid,)).fetchone()
    assert r == ("evento_realizado", None)


def test_o_cliente_perdido_que_volta_e_o_mesmo_cadastro(pool):
    """Perdido não é venda fechada: a proposta se amarra ao mesmo card (migração
    236), e é a assinatura que o leva ao fechamento (`funil_ganho`)."""
    _etapas(pool)
    lid = _lead(pool, empresa="Elis", whatsapp="86933332222", status="perdido")
    oid = _orc(pool, empresa="Elis Moura", whatsapp="86933332222")
    assert pl.garantir(pool, CONTA, oid) == {"lead_id": lid, "como": "ligado"}
    assert _lead_do(pool, oid) == (lid, "perdido")


def test_na_assinatura_a_festa_que_ja_estava_na_agenda_ganha_o_card_e_o_tipo(pool):
    """Orçamento aprovado ANTES de a festa nascer ligada: a data entrou na agenda
    sem card (e sem tipo, como nasciam 10 das 11 da Prime). Quando o card chega, a
    festa passa a saber de quem é — e como FESTA, pra não virar "visita" ligada ao
    card no app."""
    _membros(pool)
    with pool.connection() as c:
        c.execute("create table if not exists eventos_agenda (id bigserial primary key, conta_id bigint,"
                  " prospeccao_id bigint, tipo_evento text)")
        c.execute("alter table orcamentos add column if not exists evento jsonb")
        c.execute("alter table orcamentos add column if not exists evento_agenda_id bigint")
        ev = c.execute("insert into eventos_agenda (conta_id) values (%s) returning id", (CONTA,)).fetchone()[0]
        ev_outra = c.execute("insert into eventos_agenda (conta_id) values (%s) returning id",
                             (CONTA + 1,)).fetchone()[0]
        c.commit()
    oid = _orc(pool, empresa="Viviane Alves", whatsapp="86998479896")
    with pool.connection() as c:
        c.execute("update orcamentos set evento=%s, evento_agenda_id=%s where id=%s",
                  ('{"tipo": "Casamento", "data": "2026-12-23"}', ev, oid))
        c.commit()
    r = pl.garantir_pelo_orcamento(pool, CONTA, oid)
    assert r["como"] == "criado"
    with pool.connection() as c:
        assert c.execute("select prospeccao_id, tipo_evento from eventos_agenda where id=%s",
                         (ev,)).fetchone() == (r["lead_id"], "Casamento")
        assert c.execute("select prospeccao_id from eventos_agenda where id=%s",
                         (ev_outra,)).fetchone() == (None,)
    # de novo (a assinatura depois da aprovação): nada muda
    assert pl.garantir_pelo_orcamento(pool, CONTA, oid)["como"] == "ja_tinha"
