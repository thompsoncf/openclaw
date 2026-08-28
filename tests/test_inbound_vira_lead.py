"""O que faz uma mensagem recebida virar lead — e o que não faz.

Duas regras nasceram do mesmo chamado, e são opostas:

1. **Resposta de campanha (Twilio/Cloud).** As empresas prospectadas respondem com
   bot ("no momento não estamos disponíveis") e isso virava lead QUENTE, sujando o
   funil e o placar do vendedor. Agora a primeira mensagem não reconhecível de quem
   ainda está na BASE não promove: promove na segunda, ou depois que a empresa
   responder. Bot manda o automático e cala; pessoa continua.

2. **Contato do histórico (QR).** A importação do WhatsApp traz quem já falava com a
   empresa. Antes, esse contato mandando mensagem era um beco sem saída: anexava na
   conversa órfã e nunca entrava no funil — numa padaria, o cliente pedindo bolo
   ficava invisível pra fila. Agora vira lead e cai na distribuição.

Schema mínimo dos caminhos exercitados; nada de migração.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 5
NUM = "558698392961"

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, cpf text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);

-- unicidade por CONVERSA, não global: o id do WhatsApp é o mesmo nas duas pontas
-- da mensagem, e global fazia a conta que recebe perder a dela (migração 159)
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_inbound_lead_test"
    with admin.connection() as c:
        c.autocommit = True
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


def _alvo_de_campanha(c, *, empresa="Clínica Bem Viver"):
    """Um contato da BASE, como a campanha cria: ainda não é lead do funil."""
    return c.execute(
        """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
           values (%s,%s,%s,'base','frio','google_places') returning id""",
        (CONTA, empresa, "+" + NUM)).fetchone()[0]


def _estado(c, lead_id):
    return c.execute("select estagio, temperatura from prospeccao where id=%s",
                     (lead_id,)).fetchone()


def _recebe(c, texto, *, sid, continuidade):
    # devolve (conversa, nova); estes testes só olham a conversa
    return pp._wa_inbound_conversa(c, CONTA, NUM, texto, sid, "Perfil", False,
                                   exigir_continuidade=continuidade)[0]


# ------------------------------------------------------ 1. resposta de campanha

def test_bot_respondendo_campanha_nao_vira_lead_quente(pool):
    """O caso real: a empresa prospectada tem atendente automático."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "O Dom agradece seu contato, no momento não estamos disponíveis.",
                sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("base", "frio")


def test_pessoa_que_insiste_vira_lead_na_segunda(pool):
    """Bot manda o automático e cala; pessoa continua. É a diferença que a trava usa."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "quem é?", sid="s1", continuidade=True)
        assert _estado(c, lead) == ("base", "frio")          # ainda não
        _recebe(c, "ah sim, quanto custa?", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")        # agora sim


def test_resposta_depois_da_empresa_falar_tambem_conta(pool):
    """Ela respondeu, a empresa retornou, ela voltou — é conversa de verdade."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        conv = _recebe(c, "quem é?", sid="s1", continuidade=True)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','out','humano','Somos da Zaq, tudo bem?')""", (conv,))
        _recebe(c, "entendi", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_no_qr_a_campanha_segue_como_sempre(pool):
    """No QR não existe template com botão — o disparo é texto solto, e a regra de lá
    não muda. Sem a trava, a primeira resposta promove como antes."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "qualquer coisa", sid="s1", continuidade=False)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_quem_chegou_sozinho_esquenta_na_primeira(pool):
    """A trava é pra quem NÓS fomos atrás. Quem procurou a empresa (origem inbound) é
    cliente falando com a gente: esquenta na hora, senão a caixa pararia de reagir a
    quem já está sendo atendido."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
               values (%s,'Chegou sozinho',%s,'lead','frio','whatsapp_inbound') returning id""",
            (CONTA, "+" + NUM)).fetchone()[0]
        _recebe(c, "oi", sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_alvo_de_campanha_ja_promovido_tambem_pega_a_trava(pool):
    """O buraco que a primeira versão tinha: a trava olhava `estagio='base'`, mas a base
    é esvaziada em lote pelo botão "Promover" ANTES da campanha rodar. Quando o bot
    respondia, o alvo já era 'lead' e a trava não pegava nada — no banco de produção não
    existia UMA linha 'base'. O que decide é a origem, não o estágio."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
               values (%s,'Promovido antes da campanha',%s,'lead','frio','google_places')
               returning id""",
            (CONTA, "+" + NUM)).fetchone()[0]
        _recebe(c, "no momento não estamos disponíveis", sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "frio"), "bot não pode esquentar"
        _recebe(c, "quanto custa?", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente"), "pessoa continuando, sim"


# ------------------------------------------------------ 2. contato do histórico

def _conversa_importada(c, nome="Mariêh Louise", msgs=3):
    """Conversa órfã, como a importação do histórico por QR cria: sem lead."""
    conv = c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, contato_nome)
           values (%s, null, 'whatsapp', %s, %s) returning id""",
        (CONTA, NUM, nome)).fetchone()[0]
    for i in range(msgs):
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','in','lead',%s)""", (conv, f"msg antiga {i}"))
    return conv


def test_contato_do_historico_que_fala_vira_lead(pool):
    """O caso da Mariêh: 55 mensagens desde julho, mandou 'tem fatia nuvem?' e o
    pedido ficava fora do funil, sem dono e sem ninguém avisado."""
    with pool.connection() as c:
        conv = _conversa_importada(c)
        conv_id = _recebe(c, "Boa tarde, tem fatia nuvem?", sid="s9", continuidade=False)
        c.commit()
        assert conv_id == conv, "tem que reusar a conversa que já existia"
        lead = c.execute("select prospeccao_id from conversas where id=%s",
                         (conv,)).fetchone()[0]
        assert lead is not None, "a conversa precisa passar a apontar pro lead novo"
        assert _estado(c, lead) == ("lead", "quente")


def test_o_lead_do_historico_herda_o_nome_da_conversa(pool):
    """O nome com que ela já aparecia no celular é melhor que o pushName do momento —
    senão o funil ganha 'Contato WhatsApp' no lugar de 'Mariêh Louise'."""
    with pool.connection() as c:
        _conversa_importada(c, nome="Mariêh Louise")
        _recebe(c, "oi", sid="s9", continuidade=False)
        c.commit()
        assert c.execute("select empresa from prospeccao where conta_id=%s",
                         (CONTA,)).fetchone()[0] == "Mariêh Louise"


def test_o_historico_antigo_nao_se_perde(pool):
    """Vira lead reaproveitando a conversa — as mensagens de antes continuam lá."""
    with pool.connection() as c:
        conv = _conversa_importada(c, msgs=5)
        _recebe(c, "nova", sid="s9", continuidade=False)
        c.commit()
        n = c.execute("select count(*) from mensagens where conversa_id=%s",
                      (conv,)).fetchone()[0]
        assert n == 6, "5 antigas + a que acabou de chegar"


def test_numero_novo_de_verdade_continua_virando_lead(pool):
    """Sem conversa nem base: o caminho de sempre, que já funcionava."""
    with pool.connection() as c:
        _recebe(c, "oi", sid="s9", continuidade=True)
        c.commit()
        r = c.execute("select estagio, temperatura, origem from prospeccao where conta_id=%s",
                      (CONTA,)).fetchone()
        assert r == ("lead", "quente", "whatsapp_inbound")


# ------------------------------------------------------ 3. a mensagem é sagrada

def test_rodizio_quebrado_nao_engole_a_mensagem(pool, monkeypatch):
    """O rodízio roda dentro do webhook, e falhar nele abortava a transação inteira: o
    `except` calava o erro, o commit virava ROLLBACK silencioso e a MENSAGEM RECEBIDA
    sumia — com 200 pro WhatsApp, como se tivesse dado certo. Rodízio sem dono custa um
    lead na fila; mensagem perdida custa o cliente. Agora o rodízio vai em SAVEPOINT."""
    from finance import distribuicao as dist

    def explode(*a, **kw):
        raise RuntimeError("fila travada")

    monkeypatch.setattr(dist, "atribuir_se_sem_dono", explode)
    with pool.connection() as c:
        _recebe(c, "quero fazer um pedido", sid="s9", continuidade=False)
        c.commit()
        lead = c.execute("select id from prospeccao where conta_id=%s", (CONTA,)).fetchone()
        assert lead, "o lead tem que sobreviver ao rodízio quebrado"
        assert c.execute(
            """select texto from mensagens m join conversas cv on cv.id=m.conversa_id
                where cv.prospeccao_id=%s""", (lead[0],)).fetchone()[0] \
            == "quero fazer um pedido"


# --------------------------------------------- 3. o nome que chega depois do lead

def _recebe_sem_nome(c, texto, *, sid):
    """Como na vida real quando nem a agenda nem o pushName chegaram ainda."""
    return pp._wa_inbound_conversa(c, CONTA, NUM, texto, sid, "", False,
                                   exigir_continuidade=False)[0]


def test_o_nome_desce_pro_lead_quando_aparece_depois(pool):
    """O lead é batizado no instante em que a mensagem chega — e nesse instante o
    nome pode não existir em lugar nenhum. Na Doce Mell isso deu 8 leads chamados
    'Contato WhatsApp' cujo nome estava, sete minutos depois, na própria conversa."""
    with pool.connection() as c:
        conv = _recebe_sem_nome(c, "oi", sid="n1")
        lead = c.execute("select id, empresa from prospeccao where conta_id=%s",
                         (CONTA,)).fetchone()
        assert lead[1] == pp.NOME_PROVISORIO, "sem nome nenhum, nasce provisório"

        # o pushName chega na mensagem seguinte — é o caso real
        pp._wa_inbound_conversa(c, CONTA, NUM, "quero um bolo", "n2", "gilmaria cruz",
                                False, exigir_continuidade=False)
        c.commit()
        assert c.execute("select empresa, contato from prospeccao where id=%s",
                         (lead[0],)).fetchone() == ("gilmaria cruz", "gilmaria cruz")


def test_nome_digitado_por_gente_nao_e_sobrescrito(pool):
    """Quem escreveu sabe mais que o WhatsApp: o pushName não derruba nome de gente."""
    with pool.connection() as c:
        _recebe_sem_nome(c, "oi", sid="d1")
        lead = c.execute("select id from prospeccao where conta_id=%s", (CONTA,)).fetchone()[0]
        c.execute("update prospeccao set empresa=%s, contato=%s where id=%s",
                  ("Confeitaria da Ana", "Ana", lead))
        # o WhatsApp insiste com outro nome
        pp._wa_inbound_conversa(c, CONTA, NUM, "oi de novo", "d2", "aninha 🌸",
                                False, exigir_continuidade=False)
        c.commit()
        assert c.execute("select empresa, contato from prospeccao where id=%s",
                         (lead,)).fetchone() == ("Confeitaria da Ana", "Ana")


def test_numero_cru_no_lugar_do_nome_tambem_e_corrigido(pool):
    """Irmão do provisório: o lead que nasceu com o número no lugar do nome."""
    with pool.connection() as c:
        _recebe_sem_nome(c, "oi", sid="c1")
        lead = c.execute("select id from prospeccao where conta_id=%s", (CONTA,)).fetchone()[0]
        c.execute("update prospeccao set empresa=%s, contato=%s where id=%s",
                  ("+" + NUM, "+" + NUM, lead))
        pp._wa_inbound_conversa(c, CONTA, NUM, "oi", "c2", "Joaquim", False,
                                exigir_continuidade=False)
        c.commit()
        assert c.execute("select empresa from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == "Joaquim"


# ------------------------------------- 4. cliente de casa não é lead novo

@pytest.fixture()
def aviso(monkeypatch, pool):
    """Captura o que o rodízio avisaria, com fila e agenda de mentira.

    `atribuir_se_sem_dono` precisa devolver alguém: sem as tabelas de `distribuicao`
    neste schema mínimo ele falharia calado (o SAVEPOINT engole), e o aviso — que é o
    objeto do teste — nunca sairia.

    O aviso sai numa thread solta, então o espião avisa por `Event` e o teste espera
    (ver `_esperar_aviso`). Trocar `threading.Thread` por uma que roda na hora seria
    mais direto e foi a primeira tentativa: quebra o pool do psycopg, que cria as
    próprias threads e passa `name=`."""
    import threading

    from finance import agenda as ag
    from finance import distribuicao as dist

    visto = {"avisos": [], "eventos": [], "saiu": threading.Event()}

    def _espiao(pool, cid, mid, emp, **kw):
        visto["avisos"].append({"empresa": emp, "retomada": kw.get("retomada")})
        visto["saiu"].set()

    # o rodízio passa `get_pool()` pro aviso, e nenhum teste anterior chegava nessa
    # linha (todos param antes, sem dono). Sem isto ela levanta "DATABASE_URL nao
    # configurada" e o SAVEPOINT engole — o teste esperaria um aviso que nunca sai.
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(dist, "atribuir_se_sem_dono", lambda c, cid, lid: 77)
    monkeypatch.setattr(dist, "avisar_vendedor", _espiao)
    monkeypatch.setattr(ag, "criar_evento",
                        lambda *a, **k: visto["eventos"].append(a[2] if len(a) > 2 else ""))
    return visto


def _esperar_aviso(aviso):
    assert aviso["saiu"].wait(10), "o aviso do rodízio não saiu"


def test_cliente_com_historico_nao_e_anunciado_como_lead_novo(aviso, pool):
    """O caso de 22/08: depois de repartear a Doce Mell, o histórico voltou inteiro como
    conversa órfã e a vendedora recebeu 21 "🔥 Novo lead pra você" em duas horas — dez
    de gente que ela atende desde julho. O lead continua nascendo e ganhando dono; o que
    não pode é chamar de novo quem já estava sendo atendido."""
    with pool.connection() as c:
        _conversa_importada(c, msgs=40)
        _recebe(c, "chegou o bolo?", sid="s9", continuidade=False)
        c.commit()
    _esperar_aviso(aviso)
    assert aviso["avisos"] == [{"empresa": "Mariêh Louise", "retomada": True}]


def test_cliente_com_historico_nao_ganha_tarefa_de_retornar_contato(aviso, pool):
    """A outra metade do incômodo: cada um desses leads ganhava um "Retornar contato"
    pra 2h depois, na agenda do vendedor. Ninguém deixou de responder esse cliente."""
    with pool.connection() as c:
        _conversa_importada(c, msgs=40)
        _recebe(c, "chegou o bolo?", sid="s9", continuidade=False)
        c.commit()
    _esperar_aviso(aviso)
    assert aviso["eventos"] == [], "tarefa de retorno inventada pra cliente ativo"


def test_contato_novo_de_verdade_continua_sendo_lead_novo(aviso, pool):
    """A trava do conserto: sem conversa nenhuma antes, nada muda — aviso de lead novo
    e o "Retornar contato" de sempre. É o caminho que paga a conta da padaria."""
    with pool.connection() as c:
        _recebe(c, "oi, faz bolo de pote?", sid="s9", continuidade=False)
        c.commit()
    _esperar_aviso(aviso)
    # "Perfil" é o pushName que `_recebe` manda — sem agenda nem órfã, é o nome que sobra
    assert aviso["avisos"] == [{"empresa": "Perfil", "retomada": False}]
    assert aviso["eventos"] == ["Retornar contato: Perfil"]


def test_conversa_orfa_VAZIA_conta_como_contato_novo(aviso, pool):
    """Órfã sem mensagem nenhuma não é cliente de casa: é conversa que alguém abriu e
    não usou. Sem esta distinção o critério viraria "existe órfã?", e aí o contato novo
    que já tivesse uma conversa vazia deixaria de ser anunciado."""
    with pool.connection() as c:
        _conversa_importada(c, msgs=0)
        _recebe(c, "oi", sid="s9", continuidade=False)
        c.commit()
    _esperar_aviso(aviso)
    assert aviso["avisos"] == [{"empresa": "Mariêh Louise", "retomada": False}]
    assert aviso["eventos"], "contato novo continua ganhando o retorno agendado"


def test_a_mensagem_desta_chamada_nao_conta_como_historico(aviso, pool):
    """A conta das mensagens é feita ANTES de gravar a que acabou de chegar. Se fosse
    depois, TODO contato novo teria 'histórico' de uma mensagem e ninguém mais seria
    anunciado como lead novo — o conserto viraria o bug oposto."""
    with pool.connection() as c:
        conv = _conversa_importada(c, msgs=0)
        _recebe(c, "primeira mensagem", sid="s9", continuidade=False)
        c.commit()
        n = c.execute("select count(*) from mensagens where conversa_id=%s", (conv,)).fetchone()[0]
    _esperar_aviso(aviso)
    assert n == 1, "a mensagem entrou na conversa órfã"
    assert aviso["avisos"][0]["retomada"] is False


def test_o_lead_da_retomada_continua_ganhando_dono(aviso, pool):
    """O que NÃO muda, e é o mais importante: cliente que volta também precisa de dono,
    senão o pedido fica invisível pra fila — que é a razão de este bloco existir."""
    with pool.connection() as c:
        _conversa_importada(c, msgs=40)
        _recebe(c, "chegou o bolo?", sid="s9", continuidade=False)
        c.commit()
        lead = c.execute("select id, estagio, temperatura from prospeccao where conta_id=%s",
                         (CONTA,)).fetchone()
    _esperar_aviso(aviso)
    assert lead and lead[1:] == ("lead", "quente")
    assert len(aviso["avisos"]) == 1, "o rodízio tem que ter rodado igual"
