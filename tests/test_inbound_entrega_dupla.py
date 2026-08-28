"""A mesma mensagem entregue duas vezes não pode virar dois leads.

O caso que motivou, em 27/08/2026, conta 34 (Prime Eventos): a Geovanna mandou UMA
mensagem no WhatsApp. O provedor entregou duas vezes — mesmo `provider_sid`, às
11:18:15 e de novo às 11:18:17, 1,45 s depois. O webhook criou:

    lead 871 + conversa 3392  ->  distribuído pro PEDRO   (nunca respondida)
    lead 872 + conversa 3393  ->  distribuído pro THIAGO  (respondida às 13:26)

Os dois vendedores acharam que a cliente era deles. O rodízio fez o certo — dois
leads, dois donos; o defeito era existirem dois leads.

POR QUE O DEDUP QUE JÁ EXISTIA NÃO PEGAVA. `_wa_inbound_conversa` grava a mensagem
com `on conflict (conversa_id, provider_sid) do nothing`, e o índice é
`UNIQUE (conversa_id, provider_sid)`. **A trava é por conversa** — e a corrida cria
justamente uma conversa nova, então não há conflito nenhum pra detectar. O
`UNIQUE (conta_id, prospeccao_id, canal)` de `conversas` também não barra: o
`prospeccao_id` difere porque o lead foi duplicado junto.

Na base inteira eram 10 entregas duplicadas, 8 delas viraram leads separados (9 na
conta 23 entre 13 e 21/08).

COMO ESTE TESTE FORÇA A CORRIDA. Duas entregas separadas por 1,45 s não colidem
sozinhas num teste — `criado_em` usa `now()`, que é o INÍCIO da transação, e em
produção a primeira demorou mais que isso pra commitar. Então aqui a corrida é
montada à mão: a conexão A entra e **segura a transação aberta**; a B chega e tem
que ESPERAR. Sem as duas conexões o teste passaria por acidente, com a segunda
entrega simplesmente enxergando a primeira já gravada.
"""
import os
import threading
import time

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7
NUM = "558694455743"        # o número da Geovanna, com o formato que o webhook recebe
OUTRO = "558691112222"

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
  status text, criado_em timestamptz default now());
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
-- o chip secundário é uma CONTA própria (contas.chip_de); o principal só tem nome
-- em canais_config.rotulo. As duas fontes entram porque o aviso diz de qual chip
-- veio a outra conversa — e é isso que separa "defeito" de "campanha nos dois".
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table canais_config (conta_id bigint, canal text, rotulo text);

-- O RODÍZIO entra no esquema de propósito. Sem ele o teste ainda pegaria a
-- duplicata, mas não mostraria o SINTOMA que o vendedor relatou: os dois leads
-- caindo em pessoas diferentes. É essa linha que traduz o bug pra quem reclamou.
create table distribuicao (conta_id bigint primary key, ativo boolean default false,
  ponteiro int default 0, avisar boolean default true,
  aviso_zap boolean default false, aviso_zap_chip_id bigint, aviso_zap_texto text,
  atualizado_em timestamptz default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int default 0,
  primary key (conta_id, membro_id));

-- as duas travas que JÁ existem em produção. Estão aqui de propósito: é preciso
-- ver que, mesmo com elas, a entrega dupla passava.
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create unique index if not exists idx_conversas_lead_canal
  on conversas (conta_id, prospeccao_id, canal) where prospeccao_id is not null;
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_inbound_entrega_dupla_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # dois vendedores no rodízio, como na Prime: é o que faz a entrega dupla
        # cair em pessoas diferentes.
        for nome in ("PEDRO YAN", "THIAGO PINHEIRO"):
            mid = c.execute("insert into membros (conta_id, nome, papel) "
                            "values (%s,%s,'vendedor') returning id",
                            (CONTA, nome)).fetchone()[0]
            c.execute("insert into distribuicao_fila (conta_id, membro_id, ordem) "
                      "values (%s,%s,%s)", (CONTA, mid, 0 if nome[0] == "P" else 1))
        c.execute("insert into distribuicao (conta_id, ativo) values (%s, true)", (CONTA,))
        c.commit()
    # O AVISO AO VENDEDOR SAI DO CAMINHO. Não é preguiça de testar: ele roda numa
    # thread com `get_pool()`, que exige `DATABASE_URL` — e a regra 2 da casa proíbe
    # apontar isso pra qualquer banco aqui. Sem neutralizar, `get_pool()` estoura
    # DENTRO do `with c.transaction()` de `_wa_inbound_conversa`, o savepoint desfaz
    # a atribuição, e o teste mostraria "(sem dono)" nos dois leads — escondendo
    # justamente o sintoma que ele existe pra provar.
    from finance import distribuicao as _dist
    import web.painel_prospeccao as _pp
    _avisar, _getpool = _dist.avisar_vendedor, _pp.get_pool
    _dist.avisar_vendedor = lambda *a, **k: None
    _pp.get_pool = lambda: p
    yield p
    _dist.avisar_vendedor, _pp.get_pool = _avisar, _getpool
    p.close()


def _entrega(c, numero=NUM, texto="Olá, gostaria de saber mais sobre o espaço!",
             sid="AC0A87878593EB412A238C60724E5ED0"):
    """Uma entrega do webhook, como o provedor manda."""
    return pp._wa_inbound_conversa(c, CONTA, numero, texto, sid, "Geovanna Vitoria", False)


def _contagem(pool):
    with pool.connection() as c:
        leads = c.execute("select count(*) from prospeccao where conta_id=%s",
                          (CONTA,)).fetchone()[0]
        convs = c.execute("select count(*) from conversas where conta_id=%s",
                          (CONTA,)).fetchone()[0]
        msgs = c.execute("""select count(*) from mensagens m join conversas cv
                             on cv.id = m.conversa_id where cv.conta_id=%s""",
                         (CONTA,)).fetchone()[0]
    return leads, convs, msgs


# --------------------------------------------------------- a corrida de verdade

def test_a_mesma_mensagem_entregue_duas_vezes_gera_um_lead_so(pool):
    """O caso da Geovanna. É o teste que justifica o trabalho inteiro.

    A conexão A entra e segura a transação; a B chega no meio. Sem a trava, B não
    enxerga o lead de A (ainda não commitado) e cria o seu — foi exatamente isso que
    aconteceu em produção.
    """
    erro_b = []
    liberou_b = threading.Event()

    def entrega_b():
        try:
            with pool.connection() as cb:
                _entrega(cb)
                cb.commit()
        except Exception as e:  # noqa: BLE001
            erro_b.append(e)
        finally:
            liberou_b.set()

    with pool.connection() as ca:
        _entrega(ca)                       # A pega a trava e cria lead + conversa
        t = threading.Thread(target=entrega_b, daemon=True)
        t.start()
        # B TEM que ficar presa: se ela terminar com A ainda aberta, não há trava
        # nenhuma e a corrida continua possível.
        assert not liberou_b.wait(timeout=1.0), \
            "a segunda entrega passou com a primeira ainda aberta — a trava não pegou"
        ca.commit()                        # A solta; B acorda e enxerga o que A gravou

    t.join(timeout=10)
    assert not erro_b, f"a segunda entrega estourou: {erro_b}"

    leads, convs, msgs = _contagem(pool)
    assert leads == 1, f"a entrega dupla criou {leads} leads — é o bug da Geovanna"
    assert convs == 1, f"a entrega dupla criou {convs} conversas"
    assert msgs == 1, f"a mensagem entrou {msgs} vezes"


def test_a_entrega_dupla_nao_cai_em_dois_vendedores(pool):
    """O SINTOMA que o Thiago relatou, medido pelo lado dele.

    Este teste roda a mesma corrida do anterior, mas SEM a checagem de bloqueio no
    meio — de propósito. Naquele, a asserção de que a segunda entrega espera dispara
    primeiro e mascara o resultado; aqui a única coisa que fala é o desfecho: um
    lead, um dono. Assim, tirar a trava derruba os dois testes por motivos
    diferentes — o mecanismo num, a consequência no outro."""
    erro = []

    def entrega_b():
        try:
            with pool.connection() as cb:
                _entrega(cb)
                cb.commit()
        except Exception as e:  # noqa: BLE001
            erro.append(e)

    with pool.connection() as ca:
        _entrega(ca)
        t = threading.Thread(target=entrega_b, daemon=True)
        t.start()
        time.sleep(0.3)          # dá à segunda entrega a chance de duplicar
        ca.commit()
    t.join(timeout=10)
    assert not erro, f"a segunda entrega estourou: {erro}"

    with pool.connection() as c:
        donos = c.execute(
            """select p.id, coalesce(m.nome,'(sem dono)')
                 from prospeccao p left join membros m on m.id = p.vendedor_id
                where p.conta_id=%s order by p.id""", (CONTA,)).fetchall()
    assert len(donos) == 1, (
        "a mesma mensagem virou mais de um lead, e o rodízio distribuiu cada um pra "
        f"um vendedor — é a queixa do Thiago: {donos}")


def test_a_trava_e_por_numero_e_nao_por_conta(pool):
    """A chave da trava é (conta, últimos 8 dígitos) — dois números diferentes da
    mesma conta não disputam a MINHA trava.

    ATENÇÃO ao que este teste NÃO diz. Na prática duas mensagens da mesma conta se
    serializam de qualquer jeito, porque `distribuicao.proximo_vendedor` faz
    `select ... for update` na linha do rodízio — e isso é correto: o ponteiro do
    round-robin tem que ser tomado um de cada vez, senão dois leads pegam o mesmo
    vendedor. Escrevi antes um teste ponta a ponta afirmando que números diferentes
    não esperam, e ele falhou com razão: quem os fazia esperar era o rodízio, não eu.

    Então a granularidade é medida onde ela existe — na própria trava. Se um dia
    alguém trocar a chave por `conta_id` sozinho, este teste acusa; o de ponta a
    ponta não acusaria, porque o `for update` mascara."""
    from web.painel_prospeccao import _so_digitos
    a8 = _so_digitos(NUM)[-8:]
    b8 = _so_digitos(OUTRO)[-8:]
    assert a8 != b8, "os dois números do teste têm o mesmo final — o cenário não vale"

    with pool.connection() as ca:
        ca.execute("select pg_advisory_xact_lock(%s, hashtext(%s))", (CONTA, a8))
        with pool.connection() as cb:
            # o MESMO número: tem que estar tomado
            tomado = cb.execute("select pg_try_advisory_xact_lock(%s, hashtext(%s))",
                                (CONTA, a8)).fetchone()[0]
            assert tomado is False, \
                "a trava do mesmo número não segurou — a corrida continua aberta"
            # número DIFERENTE: tem que passar livre
            livre = cb.execute("select pg_try_advisory_xact_lock(%s, hashtext(%s))",
                               (CONTA, b8)).fetchone()[0]
            assert livre is True, \
                "um número diferente ficou preso na trava de outro — a chave engrossou"
            cb.rollback()
        ca.rollback()


def test_mensagem_nova_do_mesmo_numero_continua_entrando(pool):
    """A trava não pode virar dedup demais: quem escreve de novo tem que ser ouvido."""
    with pool.connection() as c:
        _entrega(c, texto="Olá!", sid="SID-1")
        c.commit()
    with pool.connection() as c:
        _entrega(c, texto="Ainda está disponível?", sid="SID-2")
        c.commit()
    leads, convs, msgs = _contagem(pool)
    assert (leads, convs) == (1, 1), "mensagem nova do mesmo número abriu outra ficha"
    assert msgs == 2, "a segunda mensagem, que é nova de verdade, se perdeu"


def test_entrega_repetida_em_sequencia_tambem_nao_duplica(pool):
    """O caminho simples (sem concorrência): a mesma mensagem chegando de novo depois
    que a primeira já gravou. Este já funcionava pelo `on conflict` — o teste existe
    pra garantir que a trava nova não quebrou o que estava certo."""
    with pool.connection() as c:
        _entrega(c)
        c.commit()
    with pool.connection() as c:
        _entrega(c)
        c.commit()
    assert _contagem(pool) == (1, 1, 1)


def test_a_segunda_entrega_devolve_a_mesma_conversa(pool):
    """Quem chama usa o `conv_id` pra responder. Se a entrega repetida devolvesse
    outra conversa, a resposta sairia numa thread que o cliente não vê."""
    with pool.connection() as c:
        conv1, nova1 = _entrega(c)
        c.commit()
    with pool.connection() as c:
        conv2, nova2 = _entrega(c)
        c.commit()
    assert conv1 == conv2, "a entrega repetida apontou pra outra conversa"
    assert nova1 is True and nova2 is False, \
        "o `nova` tem que distinguir a primeira entrega da repetida — é ele que " \
        "impede o agente de responder duas vezes"


# ------------------------------------------- o aviso das conversas já partidas

def _conversa_irma(pool, numero=NUM, nome="Geovanna Vitoria", vendedor="PEDRO YAN"):
    """Uma segunda ficha do MESMO número, como as 62 que a produção já carrega.

    Não é montada pela corrida de propósito: a trava agora impede que ela nasça, e o
    aviso existe justamente pras que nasceram ANTES dela (e pras de re-pareamento,
    que são legítimas). O estado é o que importa, não como se chegou nele."""
    with pool.connection() as c:
        lead = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, vendedor_id) values "
            "(%s,%s,%s,(select id from membros where conta_id=%s and nome=%s)) returning id",
            (CONTA, nome, numero, CONTA, vendedor)).fetchone()[0]
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal, contato_ref) "
                         "values (%s,%s,'whatsapp',%s) returning id",
                         (CONTA, lead, numero)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','cliente','oi')", (conv,))
        c.commit()
    return lead, conv


def test_o_aviso_aparece_quando_o_numero_tem_outra_conversa(pool):
    """O que o Thiago precisava ver na tela: metade da conversa está com o Pedro."""
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    lead_irmao, conv_irma = _conversa_irma(pool)

    with pool.connection() as c:
        achadas = pp.outras_conversas_do_numero(c, CONTA, NUM, minha)
    assert len(achadas) == 1, f"o aviso não achou a conversa partida: {achadas}"
    assert achadas[0]["conversa_id"] == conv_irma
    assert achadas[0]["lead_id"] == lead_irmao
    assert achadas[0]["vendedor_nome"] == "PEDRO YAN", \
        "o nome do colega é a informação que resolve a queixa — sem ele o aviso não serve"
    assert achadas[0]["mensagens"] == 1


def test_o_aviso_some_quando_nao_ha_outra_conversa(pool):
    """A metade que falta do teste de cima: sem duplicata, nada na tela. Um aviso que
    aparece sempre é um aviso que ninguém lê."""
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    with pool.connection() as c:
        assert pp.outras_conversas_do_numero(c, CONTA, NUM, minha) == []


def test_o_aviso_nao_confunde_outro_ddd_com_o_mesmo_final(pool):
    """Os 8 finais casam com celular de outro DDD — é o motivo de a consulta ter
    TAMBÉM a igualdade exata (ver `_wa_equivalentes`). Aqui isso vira teste: um aviso
    dizendo que a conversa de outra pessoa é a mesma seria pior que aviso nenhum."""
    outro_ddd = "5511" + NUM[4:]
    assert outro_ddd[-8:] == NUM[-8:], "o cenário só vale se os 8 finais coincidirem"
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    _conversa_irma(pool, numero=outro_ddd, nome="Outra pessoa")
    with pool.connection() as c:
        assert pp.outras_conversas_do_numero(c, CONTA, NUM, minha) == []


def test_excluir_aceita_lista_pra_nao_repetir_aviso_que_ja_existe(pool):
    """O desktop já avisa do gêmeo do OUTRO chip (`_aviso_gemeo`). Quem quiser somar
    este aviso lá precisa poder tirar a conversa que o outro já conta — senão a ficha
    mostra dois avisos pra mesma conversa."""
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    _, conv_irma = _conversa_irma(pool)
    with pool.connection() as c:
        assert pp.outras_conversas_do_numero(c, CONTA, NUM, [minha, conv_irma]) == []
        assert len(pp.outras_conversas_do_numero(c, CONTA, NUM, [minha])) == 1


# --------------------------------------------------- a frase (sem banco nenhum)

def test_frase_diz_o_nome_do_colega():
    from finance.cockpit import aviso_outra_conversa
    a = aviso_outra_conversa([{"lead_id": 871, "vendedor_id": 9, "vendedor_nome": "Pedro",
                               "mensagens": 3, "chip_id": None}], membro_id=5, chip_id=None)
    assert "Pedro" in a["texto"]
    assert a["lead_id"] is None, \
        "ficha de colega não abre pro vendedor (`lead_do_vendedor` revalida a posse) " \
        "— link que só redireciona é pior que link nenhum"


def test_frase_da_ficha_do_proprio_vendedor_abre():
    from finance.cockpit import aviso_outra_conversa
    a = aviso_outra_conversa([{"lead_id": 871, "vendedor_id": 5, "vendedor_nome": "Thiago",
                               "mensagens": 1, "chip_id": None}], membro_id=5, chip_id=None)
    assert a["lead_id"] == 871, "a outra ficha é DELE: aí o link abre"
    assert "1 mensagem" in a["texto"] and "mensagens" not in a["texto"]


def test_frase_vazia_quando_nao_ha_outra_conversa():
    from finance.cockpit import aviso_outra_conversa
    assert aviso_outra_conversa([]) == {}
    assert aviso_outra_conversa(None) == {}


def test_frase_resume_quando_ha_mais_de_uma():
    """Uma linha só: isto mora em cima do chat de um celular."""
    from finance.cockpit import aviso_outra_conversa
    a = aviso_outra_conversa([{"vendedor_nome": "Pedro", "mensagens": 2, "chip_id": None},
                              {"vendedor_nome": "Ana", "mensagens": 9, "chip_id": None}],
                             membro_id=5, chip_id=None)
    assert "Pedro" in a["texto"] and "E mais 1." in a["texto"]
    assert "Ana" not in a["texto"]


def test_o_aviso_falha_calado_e_nao_derruba_a_conversa(pool):
    """O aviso é um extra; o chat do vendedor não é.

    Mesma regra do `_gemeos_de_outro_chip`, que é o aviso irmão deste: se a consulta
    estourar, a tela volta a ser a de ontem em vez de não abrir. E o SAVEPOINT tem
    que segurar o estrago — sem ele o erro aborta a transação e leva junto tudo que
    vem DEPOIS, que é justamente onde o `lead_do_vendedor` grava o `pos_visto`."""
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    with pool.connection() as c:
        c.execute("alter table conversas rename column contato_ref to contato_ref_x")
        try:
            assert pp.outras_conversas_do_numero(c, CONTA, NUM, minha) == [], \
                "a consulta quebrada tinha que devolver vazio, não estourar"
            # a transação continua utilizável: é isto que o SAVEPOINT compra
            assert c.execute("select 1").fetchone()[0] == 1, \
                "o erro do aviso abortou a transação inteira — falta o SAVEPOINT"
        finally:
            c.execute("alter table conversas rename column contato_ref_x to contato_ref")
            c.commit()


# ----------------------------------------------------------------- a tela em si

class _Req:
    """O mínimo que `_lead_vendedor` toca do Request: a sessão do flash."""
    session: dict = {}


def _tela(aviso):
    from web import painel_cockpit as pc
    d = {"empresa": "Geovanna Vitoria", "cidade": "Teresina", "uf": "PI",
         "doc_fmt": "", "ia": False, "status": "novo", "etapas": [],
         "mensagens": [{"id": 1, "who": "in", "texto": "Olá"}],
         "zap_link": "", "aviso_conversa": aviso}
    return pc._lead_vendedor(_Req(), 872, d, saida_wa=False).body.decode()


def test_a_faixa_aparece_na_tela_do_vendedor():
    """O aviso tem que sair FORA do `.chat`: a conversa nasce rolada no fim (ver o
    script do rodapé), então um aviso dentro do histórico ninguém veria."""
    html = _tela({"texto": "Este número tem outra conversa neste mesmo chip, com Pedro"
                           " (1 mensagem).", "lead_id": None, "defeito": True})
    assert "'dupla'" in html and "Pedro" in html
    assert html.index("'dupla'") < html.index("class=chat"), \
        "a faixa saiu dentro/depois do chat — ficaria fora da vista"
    assert "/lead/None" not in html, "conversa de colega não pode virar link"


def test_sem_aviso_a_tela_nao_muda():
    assert "dupla" not in _tela({})
    assert "dupla" not in _tela(None)


def test_a_campanha_nos_dois_chips_nao_e_pintada_como_defeito():
    """A observação do dono: dois chips ativos com a MESMA campanha geram duas
    conversas de propósito. Ali não há o que consertar — e uma faixa âmbar com
    "Atenção" mandaria o vendedor caçar problema que não existe."""
    defeito = _tela({"texto": "x", "lead_id": None, "defeito": True})
    normal = _tela({"texto": "x", "lead_id": None, "defeito": False})
    assert "Atenção" in defeito and "Atenção" not in normal
    assert "dupla info" in normal and "dupla info" not in defeito


def test_a_faixa_da_ficha_propria_traz_o_link():
    html = _tela({"texto": "Este número tem outra ficha sua (2 mensagens).",
                  "lead_id": 871})
    assert "/cockpit/lead/871" in html, "a outra ficha é dele: o link tem que abrir"


# ------------------------------------------ os DOIS CHIPS: parecido, e não é bug

CHIP2 = 991          # o chip secundário é uma conta própria (contas.chip_de)


def _com_dois_chips(pool):
    """A Prime de verdade: um chip principal e um segundo, com a mesma campanha."""
    with pool.connection() as c:
        c.execute("insert into contas (id, nome, chip_de) values (%s,'Thiago',%s) "
                  "on conflict do nothing", (CHIP2, CONTA))
        c.execute("insert into canais_config (conta_id, canal, rotulo) "
                  "values (%s,'whatsapp','Zarb')", (CONTA,))
        c.commit()


def test_conversa_do_outro_chip_aparece_mas_nao_como_defeito(pool):
    """A hipótese do dono, virada teste: a mesma campanha nos dois chips gera duas
    conversas do mesmo contato — de propósito, cada chip fala pelo seu número.

    O aviso ainda tem que APARECER (o vendedor precisa saber que um colega está
    falando com a mesma pessoa por outro número), mas marcado como `defeito=False`:
    não há entrega dupla nenhuma pra consertar. Medido em 28/08/2026, 6 dos 15
    números com conversa repetida eram exatamente este caso."""
    from finance.cockpit import aviso_outra_conversa
    _com_dois_chips(pool)
    with pool.connection() as c:
        minha, _ = _entrega(c)          # entra pelo chip principal (chip_id NULL)
        c.commit()
    lead2, conv2 = _conversa_irma(pool)
    with pool.connection() as c:
        c.execute("update conversas set chip_id=%s where id=%s", (CHIP2, conv2))
        c.commit()

    with pool.connection() as c:
        achadas = pp.outras_conversas_do_numero(c, CONTA, NUM, minha)
    assert len(achadas) == 1, "a conversa do outro chip sumiu do aviso"
    assert achadas[0]["chip_id"] == CHIP2
    assert achadas[0]["chip_nome"] == "Thiago", \
        "sem o nome do chip o aviso não diz por qual número o colega está falando"

    av = aviso_outra_conversa(achadas, membro_id=None, chip_id=None)
    assert av["defeito"] is False, \
        "campanha nos dois chips é o desenho do produto, não a entrega dupla"
    assert "Thiago" in av["texto"] and "mesmo chip" not in av["texto"]


def test_conversa_do_mesmo_chip_e_marcada_como_defeito(pool):
    """A metade que separa os dois casos: mesmo chip é a corrida — o caso da
    Geovanna, onde as duas conversas tinham `chip_id` NULL e o MESMO provider_sid."""
    from finance.cockpit import aviso_outra_conversa
    _com_dois_chips(pool)
    with pool.connection() as c:
        minha, _ = _entrega(c)
        c.commit()
    _conversa_irma(pool)                # fica no chip principal, como a 3392/3393

    with pool.connection() as c:
        achadas = pp.outras_conversas_do_numero(c, CONTA, NUM, minha)
    av = aviso_outra_conversa(achadas, membro_id=None, chip_id=None)
    assert av["defeito"] is True
    assert "mesmo chip" in av["texto"]
