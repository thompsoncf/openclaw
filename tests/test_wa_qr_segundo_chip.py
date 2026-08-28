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
  wa_phone_id text, desconectado_em timestamptz,
  -- migração 172: apelido do chip principal, que não tem linha própria em contas
  rotulo text);
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
  chip_id bigint references contas(id) on delete set null, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, membro_id bigint, texto text, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create unique index on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, atualizado timestamptz default now(),
  primary key (conta_id, numero8));
create table agente_config (conta_id bigint primary key, ativo boolean default false);
-- a faixa do cabeçalho lê o aparelho conectado direto da credencial
create table wa_qr_auth (conta_id bigint not null, arquivo text not null,
  conteudo text not null, atualizado timestamptz default now(),
  primary key (conta_id, arquivo));
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


# ═══════════════════════════════════════════ 5. as rotas da tela

class _Ctx:
    """Sessão de painel de mentira: só o que `_acesso` devolve."""


@pytest.fixture()
def painel(pool, monkeypatch):
    """Liga o painel neste banco e loga como gerência da empresa dada."""
    def _logar(conta_id, gerencia=True):
        monkeypatch.setattr(
            pp, "_acesso",
            lambda req: ({"conta_id": conta_id, "membro_id": 1, "gerencia": gerencia}, None))
    return _logar


def _json(resp):
    import json as _j
    return _j.loads(bytes(resp.body).decode("utf-8"))


def test_criar_chip_2_nao_encosta_no_chip_1(pool, painel):
    emp = _empresa(pool)
    painel(emp)
    r = _json(pp.comunicacao_chip_novo(None, apelido="Agência Beta"))
    assert r["ok"]
    chip = r["chip"]
    with pool.connection() as c:
        # a linha nova é um CHIP, não uma empresa
        dono = c.execute("select chip_de from contas where id=%s", (chip,)).fetchone()[0]
        assert dono == emp
        # e nasce com canal PRÓPRIO e DESLIGADO — parear é o passo seguinte
        ativo = c.execute("""select ativo from canais_config where conta_id=%s and canal='whatsapp'""",
                          (chip,)).fetchone()[0]
        assert ativo is False
        # o canal do chip 1 não foi tocado
        assert c.execute("""select ativo from canais_config where conta_id=%s and canal='whatsapp'""",
                         (emp,)).fetchone()[0] is True


def test_nao_deixa_criar_dois_chips_pendentes(pool, painel):
    """Dois cartões vazios na tela é convite pra parear o mesmo número duas vezes."""
    emp = _empresa(pool)
    painel(emp)
    assert _json(pp.comunicacao_chip_novo(None, apelido="Beta"))["ok"]
    segundo = _json(pp.comunicacao_chip_novo(None, apelido="Gama"))
    assert segundo["ok"] is False and "esperando pareamento" in segundo["msg"]


def test_chip_sem_apelido_e_recusado(pool, painel):
    emp = _empresa(pool)
    painel(emp)
    r = _json(pp.comunicacao_chip_novo(None, apelido="   "))
    assert r["ok"] is False


def test_vendedor_nao_cria_chip(pool, painel):
    emp = _empresa(pool)
    painel(emp, gerencia=False)
    resp = pp.comunicacao_chip_novo(None, apelido="Beta")
    assert resp.status_code == 403


def test_nao_da_pra_mexer_no_chip_de_outra_empresa(pool, painel):
    """Sem esta trava, um id na querystring geraria QR, desconectaria ou renomearia
    o chip de outra empresa."""
    emp = _empresa(pool)
    outra = _empresa(pool, "Outra")
    chip = _chip2(pool, emp)
    with pool.connection() as c:
        assert pp._chip_da_conta(c, emp, chip) == chip
        assert pp._chip_da_conta(c, outra, chip) is None
        assert pp._chip_da_conta(c, emp, 999999) is None
        assert pp._chip_da_conta(c, emp, "") == emp, "vazio = o chip principal"


def test_apelido_do_chip_2_vai_pro_nome_da_linha(pool, painel):
    emp = _empresa(pool)
    chip = _chip2(pool, emp, nome="Beta")
    painel(emp)
    assert _json(pp.comunicacao_chip_apelido(None, chip=str(chip), apelido="Agência Beta"))["ok"]
    with pool.connection() as c:
        assert c.execute("select nome from contas where id=%s", (chip,)).fetchone()[0] == "Agência Beta"


def test_apelido_do_chip_1_NAO_mexe_no_nome_da_empresa(pool, painel):
    """`contas.nome` da empresa aparece em contrato, cobrança, assinatura de e-mail e
    convite. O apelido do chip principal vai pro canal (migração 172)."""
    emp = _empresa(pool, "MANOEL SOARES")
    painel(emp)
    assert _json(pp.comunicacao_chip_apelido(None, chip="", apelido="Agência Alfa"))["ok"]
    with pool.connection() as c:
        assert c.execute("select nome from contas where id=%s", (emp,)).fetchone()[0] == "MANOEL SOARES"
        assert c.execute("""select rotulo from canais_config
                             where conta_id=%s and canal='whatsapp'""", (emp,)).fetchone()[0] == "Agência Alfa"


def test_a_tela_lista_o_principal_primeiro(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp, nome="Agência Beta")
    with pool.connection() as c:
        chips = pp.chips_da_conta(c, emp)
    assert [x["id"] for x in chips] == [emp, chip]
    assert chips[0]["principal"] is True and chips[1]["principal"] is False
    assert chips[1]["rotulo"] == "Agência Beta"
    assert chips[0]["rotulo"] == "Chip 1", "sem apelido, a tela mostra Chip 1"


def test_empresa_de_um_chip_so_lista_um(pool):
    """O estado de produção: a tela desenha o bloco de sempre."""
    emp = _empresa(pool)
    with pool.connection() as c:
        assert len(pp.chips_da_conta(c, emp)) == 1


# ═══════════════════════════════════════════ 6. as 3 informações POR CHIP

def _msg(pool, conv_id, direcao="in", membro_id=None, quando="now()"):
    with pool.connection() as c:
        c.execute(f"""insert into mensagens (conversa_id, canal, direcao, autor, texto,
                        membro_id, criado_em)
                      values (%s,'whatsapp',%s,'lead','oi',%s,{quando})""",
                  (conv_id, direcao, membro_id))
        c.commit()


def _conv_id(pool, contato):
    with pool.connection() as c:
        return c.execute("""select id from conversas where contato_ref like %s
                             order by id desc limit 1""", ("%" + contato[-8:],)).fetchone()[0]


def test_ultima_recebida_e_de_cada_chip_separadamente(pool):
    """No nível da empresa, "última recebida 21:40" não diz em QUAL número entrou —
    e essa é justamente a pergunta de quem desconfia que um dos dois parou."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _entrada(emp, "5586991120001")     # chega no chip 1
    with pool.connection() as c:
        chips = pp.chips_da_conta(c, emp)
    assert chips[0]["ultima"], "o chip 1 recebeu"
    assert not chips[1]["ultima"], "o chip 2 não recebeu nada ainda"

    _entrada(chip, "5586991120002")    # agora chega no chip 2
    with pool.connection() as c:
        chips = pp.chips_da_conta(c, emp)
    assert chips[1]["ultima"], "agora o chip 2 tem a dele"


def test_historico_antigo_conta_pro_chip_principal(pool):
    """`chip_id` nulo é todo o histórico de antes de existir chip 2. Ele não pode
    sumir da tela nem migrar pro chip novo."""
    emp = _empresa(pool)
    _entrada(emp, "5586991120003")
    chip = _chip2(pool, emp)           # o chip 2 nasce DEPOIS da conversa
    with pool.connection() as c:
        chips = pp.chips_da_conta(c, emp)
    assert chips[0]["ultima"], "o histórico ficou com o chip principal"
    assert not chips[1]["ultima"]


def test_saida_por_fora_separa_por_chip(pool):
    """Com dois números, um "95% saiu por fora" somado não diz em qual aparelho bater."""
    from finance import cockpit as ck
    emp = _empresa(pool)
    chip = _chip2(pool, emp)
    _entrada(emp, "5586991120004")
    _entrada(chip, "5586991120005")
    c1, c2 = _conv_id(pool, "5586991120004"), _conv_id(pool, "5586991120005")
    # chip 1: uma saída pelo Zaq (tem membro). chip 2: uma por fora (sem membro).
    _msg(pool, c1, "out", membro_id=7)
    _msg(pool, c2, "out", membro_id=None)

    assert ck.saida_por_fora(pool, emp, chip_id=emp)["pct"] == 0, "o chip 1 respondeu pelo Zaq"
    assert ck.saida_por_fora(pool, emp, chip_id=chip)["pct"] == 100, "o chip 2, por fora"
    assert ck.saida_por_fora(pool, emp)["pct"] == 50, "sem chip, a empresa inteira"


def test_a_tela_nao_pergunta_aparelhos_de_chip_alheio(pool, painel):
    emp = _empresa(pool)
    outra = _empresa(pool, "Outra")
    chip = _chip2(pool, emp)
    painel(outra)
    resp = pp.comunicacao_whatsapp_aparelhos(None, chip=str(chip))
    assert resp.status_code == 403


# ═══════════════════════════════════════════ 7. a faixa e a etiqueta na lista

def test_a_faixa_usa_o_APELIDO_e_nao_o_nome_do_perfil(pool, monkeypatch):
    """O apelido é o que a pessoa digitou em Canais e o que o relatório agrupa. Dois
    nomes pro mesmo chip em telas diferentes é o começo da confusão."""
    emp = _empresa(pool)
    with pool.connection() as c:
        c.execute("""insert into wa_qr_auth (conta_id, arquivo, conteudo)
                     values (%s,'creds',%s)""",
                  (emp, '{"me":{"id":"5586994095516:14@s.whatsapp.net","name":"Perfil do Zap"}}'))
        c.execute("update canais_config set rotulo='Agência Alfa' where conta_id=%s", (emp,))
        c.commit()
    monkeypatch.setattr(pp, "_WA_CHIP_CACHE", {})
    from finance import whatsapp_qr as _qr
    monkeypatch.setattr(_qr, "configurado", lambda: True)
    monkeypatch.setattr(_qr, "status", lambda cid: {"status": "conectado"})
    chip = pp._wa_chip(emp)
    assert chip["nome"] == "Agência Alfa", "o apelido vence o nome do perfil"
    assert chip["apelido"] == "Agência Alfa"


def test_sem_apelido_a_faixa_cai_no_nome_do_perfil(pool, monkeypatch):
    """Reserva: enquanto ninguém batizou, o nome do WhatsApp é melhor que nada."""
    emp = _empresa(pool)
    with pool.connection() as c:
        c.execute("""insert into wa_qr_auth (conta_id, arquivo, conteudo)
                     values (%s,'creds',%s)""",
                  (emp, '{"me":{"id":"5586994095516:14@s.whatsapp.net","name":"Perfil do Zap"}}'))
        c.commit()
    monkeypatch.setattr(pp, "_WA_CHIP_CACHE", {})
    from finance import whatsapp_qr as _qr
    monkeypatch.setattr(_qr, "configurado", lambda: True)
    monkeypatch.setattr(_qr, "status", lambda cid: {"status": "conectado"})
    assert pp._wa_chip(emp)["nome"] == "Perfil do Zap"


def test_sem_segundo_chip_a_faixa_devolve_None(pool, monkeypatch):
    """O estado das 22 contas de hoje. A tela desenha a linha discreta."""
    emp = _empresa(pool)
    monkeypatch.setattr(pp, "_WA_CHIP2_CACHE", {})
    assert pp._wa_chip2(emp) is None


def test_a_faixa_do_chip_2_traz_apelido_e_estado(pool, monkeypatch):
    emp = _empresa(pool)
    chip = _chip2(pool, emp, nome="Agência Beta")
    with pool.connection() as c:
        c.execute("""insert into wa_qr_auth (conta_id, arquivo, conteudo)
                     values (%s,'creds',%s)""",
                  (chip, '{"me":{"id":"5586988124400:9@s.whatsapp.net","name":"outro"}}'))
        c.commit()
    monkeypatch.setattr(pp, "_WA_CHIP2_CACHE", {})
    from finance import whatsapp_qr as _qr
    monkeypatch.setattr(_qr, "configurado", lambda: True)
    monkeypatch.setattr(_qr, "status", lambda cid: {"status": "conectado"})
    c2 = pp._wa_chip2(emp)
    assert c2 and c2["id"] == chip
    assert c2["nome"] == "Agência Beta", "aqui o apelido é o contas.nome da linha do chip"
    assert c2["estado"] == "conectado" and "8812" in c2["numero"]


def test_chip_recem_criado_aparece_como_ainda_sem_parear(pool, monkeypatch):
    """Criado mas sem QR lido: a faixa não pode dizer 'desconectado', que sugere que
    caiu — ele nunca chegou a subir."""
    emp = _empresa(pool)
    chip = _chip2(pool, emp, nome="Agência Beta")
    with pool.connection() as c:
        c.execute("update canais_config set ativo=false where conta_id=%s", (chip,))
        c.commit()
    monkeypatch.setattr(pp, "_WA_CHIP2_CACHE", {})
    c2 = pp._wa_chip2(emp)
    assert c2["estado"] == "sem_chip" and not c2["numero"]


def test_a_etiqueta_de_chip_so_aparece_com_dois(pool):
    """Numa empresa de um chip só seria a mesma palavra repetida em 100 linhas."""
    emp = _empresa(pool)
    with pool.connection() as c:
        c.execute("update canais_config set rotulo='Agência Alfa' where conta_id=%s", (emp,))
        c.commit()
    _entrada(emp, "5586991130001")
    with pool.connection() as c:
        linhas = pp._conversas_list(c, emp, True, None)
    assert linhas and linhas[0]["chip_rot"] == "", "um chip só: sem etiqueta"

    _chip2(pool, emp, nome="Agência Beta")
    with pool.connection() as c:
        linhas = pp._conversas_list(c, emp, True, None)
    assert linhas[0]["chip_rot"] == "Agência Alfa", "com dois, a conversa antiga é do principal"


def test_a_etiqueta_diz_o_chip_de_cada_conversa(pool):
    emp = _empresa(pool)
    chip = _chip2(pool, emp, nome="Agência Beta")
    with pool.connection() as c:
        c.execute("update canais_config set rotulo='Agência Alfa' where conta_id=%s", (emp,))
        c.commit()
    _entrada(emp, "5586991130002")
    _entrada(chip, "5586991130003")
    with pool.connection() as c:
        linhas = pp._conversas_list(c, emp, True, None)
    # chaveia pelo chip, não pelo nome: `empresa` é o nome do lead, não o telefone
    por_chip = {l["chip_id"]: l["chip_rot"] for l in linhas}
    assert por_chip[None] == "Agência Alfa", "a que entrou pelo principal"
    assert por_chip[chip] == "Agência Beta", "a que entrou pelo chip 2"
