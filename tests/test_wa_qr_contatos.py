"""Regressão do webhook de contatos do WhatsApp QR (_gravar_contatos_wa).

Este handler já derrubou o app uma vez: em 15/08/2026 uma conta pareou o
WhatsApp e o Baileys despejou a agenda inteira do vendedor em lotes de 200,
o que na versão antiga eram 400 idas ao banco e 200 varreduras de conversas por
requisição. O serviço wa-qr registrou 2.446 respostas 502 em dez minutos.

Os testes aqui travam as duas coisas que consertaram isso — o lote virar duas
queries e o índice funcional da migração 156 ser usado — sem deixar a semântica
de nomes (agenda sobrescreve, pushName só preenche) escapar no meio do caminho.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from web.painel_prospeccao import _dedup_contatos_wa, _gravar_contatos_wa

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)  # contas
    with p.connection() as c:
        # conversas referencia prospeccao(id); o módulo inteiro de prospecção
        # não interessa aqui, então entra só o alvo da FK (mesma manha do
        # test_vendas com orcamentos).
        # empresa/contato/atualizado_em entram porque o sincronismo, ao aprender um
        # nome, desce ele pro lead que ainda está com o provisório (_batiza_lead_pendente)
        c.execute("""create table if not exists prospeccao (
                       id bigserial primary key, conta_id bigint,
                       empresa text, contato text,
                       atualizado_em timestamptz default now())""")
        c.execute("""create table if not exists prospeccao_atividades (
                       id bigserial primary key, prospeccao_id bigint, tipo text,
                       membro_id bigint, descricao text,
                       criado_em timestamptz default now())""")
        # 096 é quem acrescenta canais_config.provedor ('twilio'/'cloud'/'qr')
        for mig in ("080_comunicacao_omnichannel.sql", "081_canais_config.sql",
                    "096_whatsapp_cloud.sql", "140_conversa_contato_nome.sql",
                    "141_wa_contatos.sql", "156_conversas_indice_num8.sql"):
            c.execute((BASE / mig).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_qr(pool):
    """Conta com WhatsApp no modo QR e as conversas de sempre. O mesmo número
    aparece escrito de três jeitos porque é assim que o WhatsApp devolve: ora
    com DDI e pontuação, ora cru, ora sem o 9 — daí o casamento pelos 8 finais."""
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Zaq') returning id"
                        ).fetchone()[0]
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                          ).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, ativo, provedor)
                     values (%s,'whatsapp','558698392961',true,'qr')""", (cid,))
        c.execute("""insert into conversas (conta_id, canal, contato_ref, contato_nome) values
            (%s,'whatsapp','+55 86 9839-2961', null),
            (%s,'whatsapp','558698392961',     null),
            (%s,'whatsapp','8698392961',       'Nome Velho'),
            (%s,'whatsapp','5586988887777',    null),
            (%s,'email',   '558698392961',     null),
            (%s,'whatsapp','558698392961',     null)""",
                  (cid, cid, cid, cid, cid, outra))
        c.commit()
    yield cid, outra
    with pool.connection() as c:
        c.execute("delete from conversas where conta_id in (%s,%s)", (cid, outra))
        c.execute("delete from wa_contatos where conta_id in (%s,%s)", (cid, outra))
        c.execute("delete from canais_config where conta_id=%s", (cid,))
        c.commit()


def _nome(pool, conta_id, ref):
    # `and canal='whatsapp'`: a conta do teste tem DUAS conversas com o mesmo
    # contato_ref '558698392961' — uma de WhatsApp e uma de e-mail, de propósito,
    # porque o vazamento entre canais é justamente o que o teste vizinho cobre.
    # Sem o filtro, qual das duas vinha dependia da ordem física das linhas, e o
    # teste passava ou falhava conforme o banco. O e-mail continua conferido logo
    # abaixo, direto, onde é o assunto.
    with pool.connection() as c:
        return c.execute("select contato_nome from conversas "
                         "where conta_id=%s and contato_ref=%s and canal='whatsapp'",
                         (conta_id, ref)).fetchone()[0]


def test_agenda_renomeia_todas_as_grafias_do_numero(pool, conta_qr):
    cid, outra = conta_qr
    assert _gravar_contatos_wa(cid, ["98392961"], ["Cliente Agenda"], True, pool=pool) == 3
    assert _nome(pool, cid, "8698392961") == "Cliente Agenda"   # sobrescreve nome velho
    assert _nome(pool, cid, "5586988887777") is None            # outro número, intacto


def test_nao_vaza_entre_canais_nem_entre_contas(pool, conta_qr):
    cid, outra = conta_qr
    _gravar_contatos_wa(cid, ["98392961"], ["Cliente Agenda"], True, pool=pool)
    assert _nome(pool, cid, "558698392961") == "Cliente Agenda"
    with pool.connection() as c:
        assert c.execute("select contato_nome from conversas where canal='email'"
                         ).fetchone()[0] is None
        assert c.execute("select contato_nome from conversas where conta_id=%s", (outra,)
                         ).fetchone()[0] is None


def test_pushname_nao_derruba_nome_da_agenda(pool, conta_qr):
    cid, _ = conta_qr
    _gravar_contatos_wa(cid, ["98392961"], ["Cliente Agenda"], True, pool=pool)
    assert _gravar_contatos_wa(cid, ["98392961"], ["apelido qualquer"], False, pool=pool) == 0
    assert _nome(pool, cid, "8698392961") == "Cliente Agenda"
    with pool.connection() as c:
        assert c.execute("select nome, da_agenda from wa_contatos where numero8='98392961'"
                         ).fetchone() == ("Cliente Agenda", True)


def test_pushname_preenche_conversa_sem_nome(pool, conta_qr):
    cid, _ = conta_qr
    assert _gravar_contatos_wa(cid, ["88887777"], ["Push Fulano"], False, pool=pool) == 1
    assert _nome(pool, cid, "5586988887777") == "Push Fulano"


def test_guarda_contato_mesmo_sem_conversa(pool, conta_qr):
    """O WhatsApp manda a agenda ANTES das mensagens: quase todo contato chega
    quando a conversa ainda não existe. Guardar é o que faz a importação servir
    pra alguma coisa."""
    cid, _ = conta_qr
    assert _gravar_contatos_wa(cid, ["11112222"], ["Futuro Cliente"], True, pool=pool) == 0
    with pool.connection() as c:
        assert c.execute("select nome from wa_contatos where conta_id=%s and numero8='11112222'",
                         (cid,)).fetchone()[0] == "Futuro Cliente"


def test_conta_fora_do_modo_qr_e_ignorada(pool, conta_qr):
    """O segredo é UM só pro serviço inteiro; sem esta trava um conta_id trocado
    por engano tocaria os dados de outra empresa."""
    _, outra = conta_qr
    assert _gravar_contatos_wa(outra, ["98392961"], ["Invasor"], True, pool=pool) == 0
    assert _nome(pool, outra, "558698392961") is None


def test_dedup_mantem_a_escolha_do_laco_antigo():
    """Sem dedup, `on conflict do update` morre com "cannot affect row a second
    time" — e agenda de celular repete contato o tempo todo. Quem sobra tem que
    ser o mesmo que sobrava antes: agenda = último, pushName = primeiro."""
    repetido = [{"numero": "558698392961", "nome": "Primeiro"},
                {"numero": "8698392961", "nome": "Segundo"}]   # mesmos 8 finais
    assert _dedup_contatos_wa(repetido, True) == {"98392961": "Segundo"}
    assert _dedup_contatos_wa(repetido, False) == {"98392961": "Primeiro"}


def test_dedup_descarta_lixo_e_respeita_o_teto():
    assert _dedup_contatos_wa([None, {}, {"numero": "5586988887777"},
                               {"nome": "sem numero"}], True) == {}
    # nome vem do WhatsApp: corta em 120 pra não estourar a coluna
    grande = _dedup_contatos_wa([{"numero": "5586988887777", "nome": "x" * 500}], True)
    assert len(grande["88887777"]) == 120
    # teto de 500 por lote, independente do que o Node mandar
    demais = [{"numero": f"5586{i:09d}", "nome": f"C{i}"} for i in range(900)]
    assert len(_dedup_contatos_wa(demais, True)) == 500


def test_lote_com_numero_repetido_chega_inteiro_no_banco(pool, conta_qr):
    cid, _ = conta_qr
    peneirado = _dedup_contatos_wa(
        [{"numero": "558698392961", "nome": "Primeiro"},
         {"numero": "8698392961", "nome": "Segundo"},
         {"numero": "5586988887777", "nome": "Outro"}], True)
    n = _gravar_contatos_wa(cid, list(peneirado.keys()), list(peneirado.values()),
                            True, pool=pool)
    assert n == 4  # 3 grafias do primeiro número + 1 do segundo
    assert _nome(pool, cid, "8698392961") == "Segundo"
    assert _nome(pool, cid, "5586988887777") == "Outro"


def test_update_em_lote_usa_o_indice_da_156(pool, conta_qr):
    """O índice da 156 tem que casar EXATAMENTE com a expressão do update. Se
    alguém mexer numa das duas sem mexer na outra, o planner volta a varrer a
    tabela inteira por contato — que foi o que gerou os 502."""
    cid, _ = conta_qr
    nums = [f"{i:08d}" for i in range(200)]
    nomes = [f"Contato {i}" for i in range(200)]
    with pool.connection() as c:
        plano = "\n".join(r[0] for r in c.execute(
            r"""explain (costs off)
                update conversas set contato_nome = t.nome
                  from unnest(%s::text[], %s::text[]) as t(n8, nome)
                 where conversas.conta_id=%s and conversas.canal='whatsapp'
                   and right(regexp_replace(conversas.contato_ref, '\D', '', 'g'), 8) = t.n8""",
            (nums, nomes, cid)).fetchall())
    # Numa tabela pequena o Seq Scan é o plano CERTO, então não dá pra exigir
    # index scan aqui; o que trava a regressão é o índice existir com a
    # expressão certa — se a expressão divergir, ele some do catálogo.
    assert plano  # o update em lote é planejável (sintaxe/tipos batem)
    with pool.connection() as c:
        achou = c.execute(
            """select indexdef from pg_indexes
                where tablename='conversas' and indexname='idx_conversas_num8'"""
        ).fetchone()
    assert achou, "migração 156 não criou idx_conversas_num8"
    idx = achou[0].replace('"', "").replace(" ", "")
    assert "right(regexp_replace(contato_ref,'\\D'::text,''::text,'g'::text),8)" in idx
    assert "conta_id" in idx and "canal" in idx
