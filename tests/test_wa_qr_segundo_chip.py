"""Uma empresa com DOIS chips de WhatsApp.

O serviço Node manda o id do CHIP que recebeu a mensagem. Enquanto cada empresa
tem um chip só, esse id É a empresa — e é assim que o sistema sempre funcionou.
Com um segundo chip, a linha dele em `contas` traz `chip_de` apontando pro dono
(migração 171), e a entrada passa a traduzir.

Os dois eixos que este arquivo protege:

  1. TRADUZ o que é fato sobre o LEAD — senão o lead do chip 2 nasce numa conta
     sem funil, sem equipe e sem agenda.
  2. NÃO TRADUZ o que é fato sobre a CONEXÃO — o /deslogado desliga o canal do id
     que veio; traduzindo, o chip 2 caindo desligaria o canal da EMPRESA, onde
     mora o chip 1 que está no ar.

E o eixo que importa hoje: com `chip_de` nulo em todas as contas — o estado de
produção — tudo isto responde exatamente o que respondia antes.

Banco descartável com o schema mínimo das rotas.
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
  wa_phone_id text, desconectado_em timestamptz);
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
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, membro_id bigint, texto text, provider_sid text,
  status text, criado_em timestamptz default now());
create unique index on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, atualizado timestamptz default now(),
  primary key (conta_id, numero8));
create table agente_config (conta_id bigint primary key, ativo boolean default false);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_segundo_chip_test"
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
    def __init__(self, corpo, segredo=_SEGREDO):
        self.headers = {"x-wa-secret": segredo}
        self._corpo = json.dumps(corpo).encode("utf-8")

    async def body(self):
        return self._corpo


class _Bg:
    """BackgroundTasks de mentira — o agente não é o assunto aqui."""

    def add_task(self, *a, **k):
        pass


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setenv("WA_QR_SHARED_SECRET", _SEGREDO)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    from finance import notificar
    monkeypatch.setattr(notificar, "enviar_para_dono", lambda *a, **k: True)


def _empresa(pool, nome="Prime Eventos"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                        (nome,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                     values (%s,'whatsapp','5586994095516','qr')""", (cid,))
        c.commit()
    return cid


def _chip2(pool, empresa_id, nome="Agência Beta", numero="5586988124400"):
    """O segundo chip: linha própria em contas, apontando pro dono, com canal próprio."""
    with pool.connection() as c:
        cid = c.execute("""insert into contas (tipo, nome, chip_de) values ('pj',%s,%s)
                           returning id""", (nome, empresa_id)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                     values (%s,'whatsapp',%s,'qr')""", (cid, numero))
        c.commit()
    return cid


def _entrada(chip_id, sender, texto="oi"):
    return asyncio.run(pp.webhook_wa_qr(
        _Req({"conta_id": chip_id, "sender": sender, "texto": texto,
              "nome": "Cliente", "id": f"SID{sender}"}), _Bg()))


def _conversa(pool, contato):
    with pool.connection() as c:
        return c.execute("""select conta_id, chip_id from conversas
                             where contato_ref like %s order by id desc limit 1""",
                         ("%" + contato[-8:],)).fetchone()


# ═══════════════════════════════════════════ 1. o lead cai na empresa, não no chip

def test_lead_do_chip_2_nasce_na_empresa(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _entrada(chip, "5586991110001")
    conta_id, chip_id = _conversa(pool, "5586991110001")
    assert conta_id == emp, "o lead tem que ser da empresa, não da linha do chip"
    assert chip_id == chip, "e a conversa tem que lembrar por qual chip entrou"


def test_lead_do_chip_principal_grava_chip_nulo(pool):
    """Nulo já significa 'o chip da própria empresa' nas 1.101 conversas de hoje.
    Gravar o id da empresa ali diria o mesmo com outro valor, e uma consulta que
    filtrasse por um dos dois erraria metade das conversas."""
    emp = _empresa(pool)
    _entrada(emp, "5586991110002")
    conta_id, chip_id = _conversa(pool, "5586991110002")
    assert conta_id == emp and chip_id is None


def test_os_dois_chips_caem_no_mesmo_funil(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _entrada(emp, "5586991110003")
    _entrada(chip, "5586991110004")
    with pool.connection() as c:
        n = c.execute("select count(*) from conversas where conta_id=%s", (emp,)).fetchone()[0]
        fora = c.execute("select count(*) from conversas where conta_id=%s", (chip,)).fetchone()[0]
    assert n == 2, "as duas conversas são da mesma empresa"
    assert fora == 0, "nenhuma conversa pode nascer pendurada na linha do chip"


# ═══════════════════════════════════════════ 2. deslogar é fato sobre a CONEXÃO

def _ativo(pool, conta_id):
    with pool.connection() as c:
        return c.execute("""select ativo from canais_config
                             where conta_id=%s and canal='whatsapp'""", (conta_id,)).fetchone()[0]


def test_deslogar_o_chip_2_NAO_desliga_o_chip_1(pool):
    """O ponto mais perigoso do desenho inteiro. Se o /deslogado traduzisse chip →
    empresa, uma queda no aparelho secundário derrubaria o canal do principal."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    asyncio.run(pp.webhook_wa_qr_deslogado(_Req({"conta_id": chip})))
    assert _ativo(pool, chip) is False, "o chip que caiu tem que desligar"
    assert _ativo(pool, emp) is True, "e o chip 1, conectado, não pode ser tocado"


def test_deslogar_o_chip_1_nao_toca_no_chip_2(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    asyncio.run(pp.webhook_wa_qr_deslogado(_Req({"conta_id": emp})))
    assert _ativo(pool, emp) is False
    assert _ativo(pool, chip) is True


# ═══════════════════════════════════════════ 3. inerte com um chip só (hoje)

def test_com_chip_de_nulo_a_traducao_devolve_a_propria_conta(pool):
    """O estado de produção: 22 contas, `chip_de` nulo em todas."""
    emp = _empresa(pool)
    with pool.connection() as c:
        assert pp._resolver_chip(c, emp) == (emp, emp)


def test_id_que_nao_e_canal_qr_continua_sendo_recusado(pool):
    """A segunda trava do webhook: o segredo é um só pro serviço inteiro, então um
    id forjado não pode alcançar dados de quem não está no QR."""
    with pool.connection() as c:
        solta = c.execute("insert into contas (tipo,nome) values ('pj','Sem canal') returning id"
                          ).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                     values (%s,'whatsapp','5586900000000','twilio')""", (solta,))
        c.commit()
        assert pp._resolver_chip(c, solta) is None, "twilio não passa pelo portão do QR"
        assert pp._resolver_chip(c, 999999) is None, "id inexistente também não"


def test_chip_desligado_para_de_receber_sem_afetar_o_outro(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    with pool.connection() as c:
        c.execute("update canais_config set ativo=false where conta_id=%s", (chip,))
        c.commit()
    _entrada(chip, "5586991110005")
    assert _conversa(pool, "5586991110005") is None, "chip desligado não grava nada"
    _entrada(emp, "5586991110006")
    assert _conversa(pool, "5586991110006") is not None, "e o outro segue recebendo"


# ═══════════════════════════════════════════ 4. a resposta sai pelo chip certo

def test_resposta_sai_pelo_chip_que_recebeu(pool):
    from finance import whatsapp_out as wout
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _entrada(chip, "5586991110007")
    with pool.connection() as c:
        conv = c.execute("""select id from conversas where contato_ref like %s
                             order by id desc limit 1""", ("%91110007",)).fetchone()[0]
        assert wout.chip_da_conversa(c, emp, conv) == chip


def test_conversa_antiga_responde_pela_empresa(pool):
    """`chip_id` nulo — todo o histórico — continua saindo pelo chip principal."""
    from finance import whatsapp_out as wout
    emp = _empresa(pool)
    _entrada(emp, "5586991110008")
    with pool.connection() as c:
        conv = c.execute("""select id from conversas where contato_ref like %s
                             order by id desc limit 1""", ("%91110008",)).fetchone()[0]
        assert wout.chip_da_conversa(c, emp, conv) is None


def test_conversa_de_outra_empresa_nao_escolhe_o_chip(pool):
    """Sem conferir `conta_id`, um id de conversa alheio faria a resposta sair por um
    chip que não é desta empresa."""
    from finance import whatsapp_out as wout
    emp = _empresa(pool)
    outra = _empresa(pool, "Outra")
    chip = _chip2(pool, emp)
    _entrada(chip, "5586991110009")
    with pool.connection() as c:
        conv = c.execute("""select id from conversas where contato_ref like %s
                             order by id desc limit 1""", ("%91110009",)).fetchone()[0]
        assert wout.chip_da_conversa(c, outra, conv) is None
