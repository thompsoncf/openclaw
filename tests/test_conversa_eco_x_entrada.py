"""A conversa não pode nascer duas vezes porque DOIS CAMINHOS diferentes correram.

O arquivo irmão (`test_inbound_entrega_dupla.py`) trata de um provedor reentregando
a mesma mensagem na MESMA porta. Este trata de portas DIFERENTES chegando juntas no
mesmo número — que é o buraco que ficou aberto quando a trava foi posta só no
webhook de entrada.

São quatro portas que resolvem conversa a partir de um número:

    _wa_inbound_conversa    a mensagem do cliente (webhook de entrada)
    _wa_saida_conversa      o ECO: o vendedor respondeu pelo celular, fora do Zaq
    _wa_historico_conversa  o histórico que o Baileys despeja depois do pareamento
    _wa_conversa_simples    o botão "Agora não" do template de 1º contato

Só a primeira estava travada. As outras três abrem transação própria e faziam
select-then-insert soltas ao lado — `pg_advisory_xact_lock` só serializa quem
TAMBÉM a pega, então o eco criava a conversa dele enquanto a entrada estava dentro
da seção travada, achando que estava sozinha.

Na base de produção, em 28/08/2026, 18 mensagens estavam gravadas em DUAS conversas
cada — e 8 delas eram de saída, ou seja, o eco. Casos concretos: três áudios do
vendedor para a Auria gravados nas duas conversas dela (23/08), o "oi" para o Joao
Pedro (20/08), a Renata (18/08), o Carneiro Neto (26/08).

COMO ESTES TESTES FORÇAM A CORRIDA. Igual ao arquivo irmão, e pelo mesmo motivo:
duas chegadas separadas por 1-2 s não colidem sozinhas num teste, porque `now()` é
o início da transação e em produção a primeira demorava mais que isso pra commitar.
Então a corrida é montada à mão — a conexão A entra e SEGURA a transação aberta, a
B chega e tem que ESPERAR. Sem as duas conexões o teste passaria por acidente, com
a segunda porta simplesmente enxergando o que a primeira já gravou.
"""
import os
import threading

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7
NUM = "558694455743"
# as duas grafias do MESMO número (com e sem o nono dígito) — ver _wa_equivalentes
NUM_12 = "558698392961"
NUM_13 = "5586998392961"

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
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
create table distribuicao (conta_id bigint primary key, ativo boolean default false,
  ponteiro int default 0, avisar boolean default true,
  aviso_zap boolean default false, aviso_zap_chip_id bigint, aviso_zap_texto text,
  atualizado_em timestamptz default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int default 0,
  primary key (conta_id, membro_id));
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint, status text, wa_status text, proximo_envio_em timestamptz);

-- as travas que JÁ existiam em produção: é preciso ver que, mesmo com elas, dois
-- caminhos concorrentes abriam conversa em dobro
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create unique index if not exists idx_conversas_lead_canal
  on conversas (conta_id, prospeccao_id, canal) where prospeccao_id is not null;
"""


def _com_limite(conn):
    """Toda conexão destes testes aborta em vez de PENDURAR ao esperar trava.

    Sem isto, uma trava que não solta (a mutação `pg_advisory_xact_lock` →
    `pg_advisory_lock`, que é de SESSÃO e sobrevive ao commit) deixa a conexão
    envenenada no pool: o teste seguinte espera pra sempre, o `drop database` da
    fixture espera por ele, e a suíte inteira morre no timeout do runner sem dizer
    onde. Suíte pendurada é pior que suíte vermelha — ninguém sabe o que aconteceu.
    5 s é folga larga: nestes testes quem espera, espera menos de 1 s.

    O `commit()` no fim NÃO é enfeite, e me custou meia hora: o pool não é
    autocommit, então este `SET` ABRE transação, e a conexão volta pro pool "idle in
    transaction". O `drop database` da fixture seguinte então espera por ela pra
    sempre — a suíte pendura sem dizer uma palavra. `SET` sem `LOCAL` sobrevive ao
    commit e vale pela sessão inteira, que é exatamente o que se quer aqui."""
    conn.execute("set lock_timeout = '5s'")
    conn.commit()


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_conversa_eco_x_entrada_test"
    with admin.connection() as c:
        c.autocommit = True
        # `with (force)` derruba conexão pendurada de um run anterior morto no meio.
        # Sem isso, um único teste que trava deixa a conexão aberta, este `drop`
        # espera por ela pra sempre — e todo run seguinte herda o problema, mesmo
        # com o código já consertado. Foi o que aconteceu ao testar a mutação da
        # trava de SESSÃO: um run morto envenenou os quatro seguintes.
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=4, open=True,
                       configure=_com_limite, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        mid = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'PEDRO YAN','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.execute("insert into distribuicao_fila (conta_id, membro_id) values (%s,%s)",
                  (CONTA, mid))
        c.execute("insert into distribuicao (conta_id, ativo) values (%s, true)", (CONTA,))
        c.commit()
    # mesmo motivo documentado no arquivo irmão: `avisar_vendedor` roda numa thread
    # com `get_pool()`, que exige DATABASE_URL — e a regra 2 da casa proíbe apontar
    # isso pra qualquer banco aqui. Sem neutralizar, o erro estoura DENTRO do
    # savepoint e desfaz a atribuição, escondendo o que o teste quer medir.
    from finance import distribuicao as _dist
    _avisar, _getpool = _dist.avisar_vendedor, pp.get_pool
    _dist.avisar_vendedor = lambda *a, **k: None
    pp.get_pool = lambda: p
    yield p
    _dist.avisar_vendedor, pp.get_pool = _avisar, _getpool
    p.close()


# ----------------------------------------------------------------- as quatro portas

def _entrada(c, numero=NUM, texto="Olá, quero informações", sid="SID-IN"):
    return pp._wa_inbound_conversa(c, CONTA, numero, texto, sid, "Geovanna Vitoria", False)


def _eco(c, numero=NUM, texto="Oi, tudo bem?", sid="SID-ECO"):
    """O vendedor respondeu pelo CELULAR, fora do Zaq — o Baileys ecoa de volta."""
    return pp._wa_saida_conversa(c, CONTA, numero, texto, sid)


def _historico(c, numero=NUM, texto="mensagem antiga", sid="SID-HIST"):
    return pp._wa_historico_conversa(c, CONTA, numero, texto, sid, None)


def _contagem(pool):
    with pool.connection() as c:
        convs = c.execute("select count(*) from conversas where conta_id=%s",
                          (CONTA,)).fetchone()[0]
        leads = c.execute("select count(*) from prospeccao where conta_id=%s",
                          (CONTA,)).fetchone()[0]
        msgs = c.execute("""select count(*) from mensagens m join conversas cv
                             on cv.id=m.conversa_id where cv.conta_id=%s""",
                         (CONTA,)).fetchone()[0]
    return convs, leads, msgs


def _corrida(pool, segura, corre, *, exigir_espera=True):
    """A conexão A chama `segura` e mantém a transação aberta; B chama `corre` numa
    thread e TEM que ficar presa até A commitar. Devolve o que B produziu."""
    saida, erro = [], []
    liberou = threading.Event()

    def thread_b():
        try:
            with pool.connection() as cb:
                saida.append(corre(cb))
                cb.commit()
        except Exception as e:  # noqa: BLE001
            erro.append(e)
        finally:
            liberou.set()

    with pool.connection() as ca:
        a = segura(ca)
        t = threading.Thread(target=thread_b, daemon=True)
        t.start()
        if exigir_espera:
            assert not liberou.wait(timeout=1.0), \
                "o segundo caminho passou com o primeiro ainda aberto — não pegou a trava"
        ca.commit()
    t.join(timeout=10)
    assert not erro, f"o segundo caminho estourou: {erro}"
    return a, (saida[0] if saida else None)


# ------------------------------------------------------------------- eco × entrada

def test_o_eco_e_a_entrada_juntos_nao_abrem_duas_conversas(pool):
    """O caso da Auria: o vendedor escreve primeiro pelo celular e o cliente responde
    no mesmo instante. Sem a trava no eco, nascem duas conversas — a órfã do eco e a
    do lead — e metade do histórico some da tela de quem for atender.

    Este é o cenário de produção descrito no próprio docstring de `_wa_saida_conversa`:
    quem escreve PRIMEIRO pelo celular é o caso normal do vendedor."""
    conv_eco, r_entrada = _corrida(pool, _eco, _entrada)
    conv_entrada, _nova = r_entrada

    convs, leads, msgs = _contagem(pool)
    assert convs == 1, f"o eco e a entrada abriram {convs} conversas"
    assert leads == 1, f"nasceram {leads} leads do mesmo número"
    assert msgs == 2, f"as duas mensagens tinham que estar na MESMA conversa, achei {msgs}"
    assert conv_entrada == conv_eco, \
        "a entrada não achou a conversa que o eco abriu — o histórico nasceu partido"

    with pool.connection() as c:
        dono = c.execute("select prospeccao_id from conversas where id=%s",
                         (conv_eco,)).fetchone()[0]
    assert dono is not None, \
        "a conversa órfã do eco tinha que ser ADOTADA pelo lead, não duplicada"


def test_a_entrada_primeiro_e_o_eco_depois_tambem_da_uma_conversa(pool):
    """O espelho do anterior: o cliente escreve e o vendedor responde pelo celular
    no mesmo instante. Os dois sentidos importam — travar um lado só deixaria a
    corrida viva na outra ordem."""
    _corrida(pool, _entrada, _eco)
    convs, leads, msgs = _contagem(pool)
    assert (convs, leads, msgs) == (1, 1, 2), \
        f"esperava 1 conversa / 1 lead / 2 mensagens, veio {(convs, leads, msgs)}"


def test_o_historico_e_a_entrada_juntos_nao_abrem_duas_conversas(pool):
    """O histórico chega em rajada logo depois do pareamento, que é justamente
    quando o cliente também está escrevendo. A conversa órfã que ele cria é a mesma
    que a entrada adota — órfã duplicada vira uma segunda aba na caixa do vendedor."""
    _corrida(pool, _historico, _entrada)
    convs, leads, _msgs = _contagem(pool)
    assert (convs, leads) == (1, 1), \
        f"o histórico e a entrada abriram {convs} conversas / {leads} leads"


def test_o_botao_agora_nao_e_a_entrada_juntos_nao_abrem_duas_conversas(pool):
    """A quarta porta: `_wa_conversa_simples`, do "Agora não" do template."""
    with pool.connection() as c:
        lead_id = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, estagio) "
            "values (%s,'Alvo da campanha',%s,'base') returning id",
            (CONTA, NUM)).fetchone()[0]
        c.commit()

    def recusa(cb):
        return pp._wa_conversa_simples(cb, CONTA, lead_id, NUM, "agora não", "SID-NAO")

    _corrida(pool, recusa, _entrada)
    convs, _leads, msgs = _contagem(pool)
    assert convs == 1, f'o "Agora não" e a entrada abriram {convs} conversas'
    assert msgs == 2, f"as duas mensagens tinham que ficar na mesma conversa, achei {msgs}"


# ------------------------------------------------- o que a chave da trava tem que ser

def test_as_duas_grafias_do_numero_disputam_a_mesma_trava(pool):
    """O mesmo contato chega ora com o nono dígito, ora sem (ver `_wa_equivalentes`).
    Se a trava fosse pelo número CRU em vez dos 8 finais, as duas grafias pegariam
    chaves diferentes e a corrida continuaria aberta — com o agravante de a busca
    logo abaixo casar as duas, então o resultado seria imprevisível."""
    assert NUM_12[-8:] == NUM_13[-8:], "o cenário só vale se os 8 finais coincidirem"
    _corrida(pool,
             lambda c: _eco(c, numero=NUM_12),
             lambda c: _entrada(c, numero=NUM_13))
    convs, leads, _ = _contagem(pool)
    assert (convs, leads) == (1, 1), \
        f"as duas grafias do mesmo número viraram {convs} conversas / {leads} leads"


def test_numero_vazio_nao_poe_todo_mundo_na_mesma_fila(pool):
    """`hashtext('')` é uma chave só. Travar no número vazio faria todo contato sem
    número esperar todo outro contato sem número — uma fila global escondida dentro
    do que deveria ser uma trava por pessoa."""
    with pool.connection() as ca:
        assert pp._trava_numero(ca, CONTA, "") == ""
        with pool.connection() as cb:
            livre = cb.execute("select pg_try_advisory_xact_lock(%s, hashtext(%s))",
                               (CONTA, "")).fetchone()[0]
            assert livre is True, \
                "número vazio tomou trava — dois contatos sem número passam a se esperar"
            cb.rollback()
        ca.rollback()


def test_a_trava_solta_no_rollback(pool):
    """`xact` e não `pg_advisory_lock`: a trava tem que soltar sozinha quando a
    transação cai. Com trava manual, um erro no meio do handler devolveria a conexão
    ao pool ainda segurando o número — e aquele cliente nunca mais seria atendido."""
    with pool.connection() as ca:
        _eco(ca)
        ca.rollback()
    with pool.connection() as cb:
        livre = cb.execute("select pg_try_advisory_xact_lock(%s, hashtext(%s))",
                           (CONTA, NUM[-8:])).fetchone()[0]
        assert livre is True, \
            "a trava sobreviveu ao rollback — o número ficou preso e a conexão volta " \
            "envenenada pro pool"
        cb.rollback()


def test_a_conversa_do_eco_ainda_nasce_com_o_nome_da_agenda(pool):
    """Guarda contra uma mutação sutil: `_trava_numero` devolve os 8 FINAIS, e esse
    retorno alimenta o subselect `wa_contatos ... numero8=%s` que dá nome à conversa
    recém-criada. Se um dia ela passar a devolver o número inteiro, a trava continua
    funcionando e as conversas passam a nascer sem nome, caladas."""
    with pool.connection() as c:
        c.execute("insert into wa_contatos (conta_id, numero8, nome, da_agenda) "
                  "values (%s,%s,'Auria da Agenda',true)", (CONTA, NUM[-8:]))
        c.commit()
    with pool.connection() as c:
        conv = _eco(c)
        c.commit()
    with pool.connection() as c:
        nome = c.execute("select contato_nome from conversas where id=%s", (conv,)).fetchone()[0]
    assert nome == "Auria da Agenda", \
        f"a conversa do eco nasceu sem o nome da agenda (veio {nome!r}) — confira se " \
        "`_trava_numero` ainda devolve os 8 finais"
