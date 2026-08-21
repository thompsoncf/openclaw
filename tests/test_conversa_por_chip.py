"""Mesmo número, dois chips: DOIS leads e DUAS conversas — e um aviso ligando as duas.

O caso que gerou isto (conta 34, Prime Eventos, 21/08/2026): a empresa roda a campanha
"Zarb" pelo chip principal e a "Thiago" pelo segundo chip. O mesmo celular estava nas
duas. A mensagem que ENTROU pelo chip 2 encontrava o lead da campanha do chip 1 (a busca
era só por telefone), ia parar na conversa dele — e a resposta saía pelo número errado,
com as duas campanhas misturadas numa thread só.

A regra, decidida com o dono:

  * o que entra pelo chip 1 sai pelo chip 1; o que entra pelo 2 sai pelo 2;
  * são dois leads no funil e duas conversas no inbox, nunca misturados;
  * o único ponto de atenção é um AVISO cruzado — no card do funil, no topo do chat e
    no contexto do agente — dizendo que a outra campanha já fala com essa pessoa.

Quem marca o chip de um lead é a CONVERSA dele: `prospeccao` não tem coluna de chip e
não precisa ter. Lead sem conversa de WhatsApp ainda não é de ninguém, e o primeiro chip
que falar com ele fica com ele.

E o eixo que importa hoje: numa empresa de UM chip só — o estado de 21 das 22 contas —
tudo aqui responde exatamente o que respondia antes.
"""
import asyncio
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SEGREDO = "segredo-do-servico-qr"

_SQL = """
create table contas (id bigserial primary key, tipo text, nome text,
  chip_de bigint references contas(id) on delete cascade);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text, desconectado_em timestamptz, rotulo text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, membro_id bigint,
  vendedor_id bigint, empresa text, contato text, decisor_nome text, socio text, cnpj text,
  segmento text, telefone text, whatsapp text, email text, cidade text, uf text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  origem text, orcamento_id bigint, tipo text default 'pj',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, contato_ref text, contato_nome text, status text default 'aberta',
  agente_ativo boolean default false, responsavel_membro_id bigint,
  janela_expira_em timestamptz, push_avisado_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(),
  chip_id bigint references contas(id) on delete set null);
-- o índice de produção: é ele que torna "1 lead com 2 conversas" impossível, e por isso
-- a saída certa é DOIS leads (foi a correção do dono sobre a primeira proposta)
create unique index idx_conversas_lead_canal on conversas (conta_id, prospeccao_id, canal)
  where prospeccao_id is not null;
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, membro_id bigint, texto text, provider_sid text,
  status text, criado_em timestamptz default now());
create unique index on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, atualizado timestamptz default now(),
  primary key (conta_id, numero8));
create table agente_config (conta_id bigint primary key, ativo boolean default false);
create table wa_qr_auth (conta_id bigint not null, arquivo text not null,
  conteudo text not null, atualizado timestamptz default now(),
  primary key (conta_id, arquivo));
create table campanhas (id bigserial primary key, conta_id bigint, nome text,
  status text default 'ativa', criado_em timestamptz default now());
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint, status text default 'fila');
"""

_NUM = "5586981885930"      # o número do teste do dono, com o nono dígito


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_conversa_por_chip_test"
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
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


class _Req:
    def __init__(self, corpo):
        self.headers = {"x-wa-secret": _SEGREDO}
        self._corpo = json.dumps(corpo).encode("utf-8")

    async def body(self):
        return self._corpo


class _Bg:
    def add_task(self, *a, **k):
        pass


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setenv("WA_QR_SHARED_SECRET", _SEGREDO)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    from finance import notificar
    monkeypatch.setattr(notificar, "enviar_para_dono", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def limpo(pool):
    """Cada teste começa com o banco vazio — os ids das contas entram nas conversas e
    um resto de teste anterior faria o 'mesmo número' casar entre cenários."""
    with pool.connection() as c:
        c.execute("truncate contas, canais_config, membros, prospeccao, conversas, "
                  "mensagens, wa_contatos, agente_config, wa_qr_auth, campanhas, "
                  "campanha_alvos restart identity cascade")
        c.commit()
    yield


# ─────────────────────────────────────────────────────────────────── montagem

def _empresa(pool, nome="Prime Eventos"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                        (nome,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, rotulo)
                     values (%s,'whatsapp','5586994095516','qr','Número principal')""", (cid,))
        c.commit()
    return cid


def _chip2(pool, empresa_id, nome="Chip Thiago", numero="5586994436805"):
    with pool.connection() as c:
        cid = c.execute("""insert into contas (tipo, nome, chip_de) values ('pj',%s,%s)
                           returning id""", (nome, empresa_id)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                     values (%s,'whatsapp',%s,'qr')""", (cid, numero))
        c.commit()
    return cid


def _campanha_com_lead(pool, empresa_id, nome_camp, numero, *, chip_id=None,
                       vendedor="Manoel", lead_nome="Cliente Zarb"):
    """Um lead já em campanha, com a conversa dele no chip informado — o estado de quem
    já foi disparado. `chip_id=None` é o chip principal (coluna nula, como em produção)."""
    with pool.connection() as c:
        vid = c.execute("""insert into membros (conta_id, nome, papel) values (%s,%s,'vendedor')
                           returning id""", (empresa_id, vendedor)).fetchone()[0]
        lid = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, contato, whatsapp,
                 tipo, origem, estagio) values (%s,%s,%s,%s,%s,'pf','importacao','lead')
               returning id""",
            (empresa_id, vid, lead_nome, lead_nome, "+" + numero)).fetchone()[0]
        cid = c.execute("""insert into campanhas (conta_id, nome) values (%s,%s) returning id""",
                        (empresa_id, nome_camp)).fetchone()[0]
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id) values (%s,%s)",
                  (cid, lid))
        conv = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, chip_id)
               values (%s,%s,'whatsapp',%s,%s) returning id""",
            (empresa_id, lid, numero, chip_id)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','out','humano','disparo da campanha')""", (conv,))
        c.commit()
    return {"lead": lid, "conversa": conv, "campanha": cid, "vendedor": vid}


def _entrada(chip_id, sender=_NUM, texto="oi", sid=None):
    return asyncio.run(pp.webhook_wa_qr(
        _Req({"conta_id": chip_id, "sender": sender, "texto": texto,
              "nome": "Cliente", "id": sid or f"SID-{chip_id}-{texto}"}), _Bg()))


def _conversas(pool, empresa_id):
    with pool.connection() as c:
        return c.execute("""select id, prospeccao_id, chip_id from conversas
                             where conta_id=%s order by id""", (empresa_id,)).fetchall()


# ═════════════════════════════════════════ 1. o coração: dois chips, dois leads

def test_entrada_no_chip_2_nao_cai_na_conversa_do_chip_1(pool):
    """O defeito medido em produção, invertido: agora nasce uma segunda conversa."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM)          # chip principal
    _entrada(chip)                                               # entra pelo chip 2
    convs = _conversas(pool, emp)
    assert len(convs) == 2, "a mensagem do chip 2 tinha que abrir conversa própria"
    nova = [x for x in convs if x[0] != zarb["conversa"]][0]
    assert nova[2] == chip, "a conversa nova tem que ficar marcada com o chip que recebeu"
    assert nova[1] != zarb["lead"], "e pendurada num lead NOVO, não no lead do chip 1"


def test_a_conversa_do_chip_1_nao_recebe_a_mensagem(pool):
    """Nenhuma mensagem atravessa: a thread do Zarb continua só com o disparo dela."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM)
    _entrada(chip, texto="oi, quero saber o preço")
    with pool.connection() as c:
        textos = [r[0] for r in c.execute(
            "select texto from mensagens where conversa_id=%s order by id",
            (zarb["conversa"],)).fetchall()]
    assert textos == ["disparo da campanha"]


def test_segunda_mensagem_do_chip_2_reusa_a_conversa_do_chip_2(pool):
    """Duas conversas, e param em duas: cada chip reencontra a sua."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _campanha_com_lead(pool, emp, "Zarb", _NUM)
    _entrada(chip, texto="oi")
    _entrada(chip, texto="ainda tá aí?")
    assert len(_conversas(pool, emp)) == 2


def test_entrada_no_chip_1_continua_na_conversa_do_chip_1(pool):
    """O outro lado da regra: quem entra pelo principal fica no principal."""
    emp = _empresa(pool)
    _chip2(pool, emp)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM)
    _entrada(emp)
    convs = _conversas(pool, emp)
    assert len(convs) == 1 and convs[0][0] == zarb["conversa"]


def test_lead_sem_conversa_e_do_primeiro_chip_que_falar(pool):
    """Alvo importado e ainda não disparado não pertence a chip nenhum — o chip 2 pode
    adotá-lo em vez de criar um lead duplicado do nada."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    with pool.connection() as c:
        lid = c.execute(
            """insert into prospeccao (conta_id, empresa, contato, whatsapp, tipo, estagio)
               values (%s,'Alvo Frio','Alvo Frio',%s,'pf','base') returning id""",
            (emp, "+" + _NUM)).fetchone()[0]
        c.commit()
    _entrada(chip)
    convs = _conversas(pool, emp)
    assert len(convs) == 1
    assert convs[0][1] == lid, "era pra ter reusado o lead livre, não criado outro"
    assert convs[0][2] == chip


def test_conversa_orfa_do_outro_chip_nao_e_adotada(pool):
    """Histórico importado do chip 1 não pode virar a thread do lead que nasce no chip 2:
    é a conversa do outro número, com o outro histórico."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    with pool.connection() as c:
        orfa = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, chip_id)
               values (%s,null,'whatsapp',%s,null) returning id""", (emp, _NUM)).fetchone()[0]
        c.commit()
    _entrada(chip)
    with pool.connection() as c:
        dono = c.execute("select prospeccao_id from conversas where id=%s", (orfa,)).fetchone()[0]
    assert dono is None, "a órfã do chip 1 não podia ser vinculada ao lead do chip 2"
    assert len(_conversas(pool, emp)) == 2


# ═════════════════════════════════════ 2. empresa de UM chip: nada pode mudar

def test_um_chip_so_reusa_sempre_a_mesma_conversa(pool):
    emp = _empresa(pool)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM)
    _entrada(emp, texto="oi")
    _entrada(emp, texto="tudo bem?")
    convs = _conversas(pool, emp)
    assert len(convs) == 1 and convs[0][0] == zarb["conversa"]


def test_um_chip_so_ainda_adota_a_conversa_orfa(pool):
    """Sem segundo chip o filtro casa nulo com nulo — a adoção de órfã segue de pé."""
    emp = _empresa(pool)
    with pool.connection() as c:
        orfa = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, chip_id)
               values (%s,null,'whatsapp',%s,null) returning id""", (emp, _NUM)).fetchone()[0]
        c.commit()
    _entrada(emp)
    with pool.connection() as c:
        dono = c.execute("select prospeccao_id from conversas where id=%s", (orfa,)).fetchone()[0]
    assert dono is not None
    assert len(_conversas(pool, emp)) == 1


def test_um_chip_so_nao_gera_aviso(pool):
    """`_tem_dois_chips` corta antes de encostar em prospeccao: sem segundo chip não
    existe 'a outra campanha' pra avisar."""
    emp = _empresa(pool)
    z = _campanha_com_lead(pool, emp, "Zarb", _NUM)
    with pool.connection() as c:
        assert pp._gemeos_de_outro_chip(c, emp, [z["lead"]]) == {}


# ═════════════════════════════════════════════════ 3. o aviso cruzado

def _dois_leads(pool):
    """O estado final do cenário do dono: Zarb no chip 1, Thiago no chip 2."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM, vendedor="Manoel",
                              lead_nome="Cliente Zarb")
    _entrada(chip)
    with pool.connection() as c:
        novo = c.execute("""select id, prospeccao_id from conversas
                             where conta_id=%s and chip_id=%s""", (emp, chip)).fetchone()
    return emp, chip, zarb, {"conversa": novo[0], "lead": novo[1]}


def test_gemeo_aponta_o_lead_da_outra_campanha(pool):
    emp, chip, zarb, thiago = _dois_leads(pool)
    with pool.connection() as c:
        g = pp._gemeos_de_outro_chip(c, emp, [thiago["lead"]])[thiago["lead"]]
    assert g["lead_id"] == zarb["lead"]
    assert g["conversa_id"] == zarb["conversa"]
    assert g["campanha"] == "Zarb"
    assert g["dono"] == "Manoel"


def test_gemeo_vale_nos_dois_sentidos(pool):
    """Quem olha o lead do Zarb também tem que ver que o Thiago está falando."""
    emp, chip, zarb, thiago = _dois_leads(pool)
    with pool.connection() as c:
        g = pp._gemeos_de_outro_chip(c, emp, [zarb["lead"]])[zarb["lead"]]
    assert g["lead_id"] == thiago["lead"]
    assert g["chip_rot"] == "Chip Thiago"


def test_gemeo_em_lote_atende_o_funil_inteiro(pool):
    emp, chip, zarb, thiago = _dois_leads(pool)
    with pool.connection() as c:
        g = pp._gemeos_de_outro_chip(c, emp, [zarb["lead"], thiago["lead"]])
    assert set(g) == {zarb["lead"], thiago["lead"]}


def test_lead_de_outro_numero_nao_tem_gemeo(pool):
    emp, chip, zarb, thiago = _dois_leads(pool)
    outro = _campanha_com_lead(pool, emp, "Zarb", "5586999998888",
                               lead_nome="Outra Pessoa")
    with pool.connection() as c:
        assert outro["lead"] not in pp._gemeos_de_outro_chip(c, emp, [outro["lead"]])


def test_frase_do_aviso_cita_campanha_e_dono(pool):
    emp, chip, zarb, thiago = _dois_leads(pool)
    with pool.connection() as c:
        frase = pp._aviso_gemeo(pp._gemeos_de_outro_chip(c, emp, [thiago["lead"]])[thiago["lead"]])
    assert "Zarb" in frase and "Manoel" in frase
    assert frase.endswith(".")


def test_frase_cai_no_rotulo_do_chip_sem_campanha(pool):
    """Lead que não está em campanha nenhuma ainda precisa de um "onde" no aviso."""
    assert pp._aviso_gemeo({"campanha": "", "dono": "", "chip_rot": "Chip Thiago"}) \
        == "Este número também está em atendimento na chip Chip Thiago."
    assert pp._aviso_gemeo({}) == ""


# ═══════════════════════════════════ 4. o eco de saída também respeita o chip

def test_eco_do_celular_do_chip_2_nao_pega_o_lead_do_chip_1(pool):
    """O vendedor escreve pelo aparelho do chip 2; o Baileys ecoa como fromMe. Sem o
    filtro, a mensagem ia pendurar na ficha que é do chip 1."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    zarb = _campanha_com_lead(pool, emp, "Zarb", _NUM)
    with pool.connection() as c:
        conv = pp._wa_saida_conversa(c, emp, _NUM, "bom dia", "SID-ECO", chip_id=chip)
        c.commit()
    assert conv != zarb["conversa"]
    with pool.connection() as c:
        chip_gravado = c.execute("select chip_id from conversas where id=%s", (conv,)).fetchone()[0]
    assert chip_gravado == chip


# ═══════════════════════ 5. o link do aviso só aparece pra quem consegue abrir

def test_link_do_gemeo_some_pro_vendedor_que_nao_e_dono(pool):
    """`_pode_ver` barra o vendedor na ficha dos outros. Oferecer "Abrir →" ali era
    prometer uma porta que só devolve pro funil — pior que aviso sem link."""
    emp, chip, zarb, thiago = _dois_leads(pool)
    with pool.connection() as c:
        g = pp._gemeos_de_outro_chip(c, emp, [thiago["lead"]])[thiago["lead"]]
    assert pp._gemeo_abre(g, {"gerencia": False, "membro_id": 999}) is False
    assert pp._gemeo_abre(g, {"gerencia": False, "membro_id": zarb["vendedor"]}) is True
    assert pp._gemeo_abre(g, {"gerencia": True, "membro_id": 999}) is True


def test_sem_gemeo_nao_ha_link(pool):
    assert pp._gemeo_abre(None, {"gerencia": True, "membro_id": 1}) is False


# ═════════════════ 6. as três telas exibem mesmo (o erro do PR #523 não repete)

def test_card_do_funil_tem_a_faixa():
    """Dado calculado que a tela não desenha é dado que não existe — foi exatamente o
    que aconteceu no PR #523 (rota aberta, botão escondido)."""
    assert "c.gemeo" in pp._KANBAN_TPL
    assert "kbgem" in pp._KANBAN_TPL and ".kbgem{" in pp._KANBAN_TPL


def test_ficha_tem_a_faixa():
    assert "gemeo_aviso" in pp._FICHA_TPL
    assert "gemeo-faixa" in pp._FICHA_TPL and ".gemeo-faixa{" in pp._CSS


def test_chat_tem_a_faixa():
    assert "d.gemeo" in pp._COMUNICACAO_TPL
    assert "cx-gemeo" in pp._COMUNICACAO_TPL and ".cx-gemeo{" in pp._COMUNICACAO_TPL


def test_agente_recebe_o_aviso():
    """O terceiro destinatário que o dono pediu: 'aviso pro vendedor e pro agente'."""
    import inspect
    from finance import agente
    fonte = inspect.getsource(agente._atender)
    assert "_gemeos_de_outro_chip" in fonte
    assert "gemeo_nota" in fonte and "{historico}{gemeo_nota}" in fonte
