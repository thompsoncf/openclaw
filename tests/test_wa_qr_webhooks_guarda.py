"""Webhooks do WhatsApp por QR: quem pode mexer nos dados de quem.

O serviço Node (services/wa-qr) se identifica por UM segredo compartilhado — o
mesmo pra todas as contas. O `conta_id` vem no corpo do POST. Então o segredo
sozinho não isola nada: quem tiver ele (ou o serviço, com um bug de roteamento)
alcança qualquer empresa. A segunda trava é conferir que a conta está MESMO no
modo QR antes de tocar em qualquer coisa.

Essa trava existia no `/webhooks/wa-qr` e faltava justamente na rota mais
destrutiva, `/deslogado` — que apagava mensagens, conversas e a agenda de
contatos da conta inteira.

E o `/deslogado` agora **não apaga mais nada**. Deslogar acontece sem querer
(trocou de celular, o pareamento caiu, alguém apertou "sair" no aparelho); o
histórico de conversa com os leads é o ativo comercial da empresa e não pode
depender de um pareamento de WhatsApp. Desliga o canal, avisa o dono, e pronto.

Banco dedicado e descartável com o schema MÍNIMO que as rotas usam.
"""
import asyncio
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SEGREDO = "segredo-do-servico-qr"

_BASE_SQL = """
-- `chip_de` e `chip_id` vêm da migração 171: nulos = empresa de um chip só, que é
-- o caso de tudo que existe hoje. As rotas leem as duas colunas desde que a empresa
-- passou a poder ter mais de um chip.
create table contas (id bigserial primary key, tipo text, nome text,
  chip_de bigint references contas(id) on delete cascade);
create table conversas (id bigserial primary key, conta_id bigint, canal text,
  contato text, status text, chip_id bigint references contas(id) on delete set null, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, status text,
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table wa_contatos (id bigserial primary key, conta_id bigint, numero text, nome text);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text,
  -- marco zero da retenção de histórico (migração 165): o /deslogado carimba aqui
  desconectado_em timestamptz);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_qr_guarda_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


class _FakeRequest:
    """Só o que as rotas de webhook usam: cabeçalho e corpo cru."""

    def __init__(self, corpo: dict, segredo: str | None = _SEGREDO):
        self.headers = {"x-wa-secret": segredo} if segredo is not None else {}
        self._corpo = json.dumps(corpo).encode("utf-8")

    async def body(self):
        return self._corpo


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setenv("WA_QR_SHARED_SECRET", _SEGREDO)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    # o aviso ao dono é um extra; aqui o que está em teste é o que acontece no banco
    from finance import notificar
    monkeypatch.setattr(notificar, "enviar_para_dono", lambda pool, conta_id, texto: True)


def _conta_com_historico(pool, nome, provedor):
    """Conta com canal, uma conversa, uma mensagem e um contato na agenda."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                          (nome,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                      values (%s,'whatsapp','5586990001111',%s)""", (conta, provedor))
        conv = c.execute("""insert into conversas (conta_id, canal, contato, status)
                             values (%s,'whatsapp','5586990002222','aberta') returning id""",
                         (conta,)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                      values (%s,'whatsapp','in','lead','oi')""", (conv,))
        c.execute("insert into wa_contatos (conta_id, numero, nome) values (%s,'5586990003333','Zé')",
                  (conta,))
        c.commit()
    return conta


def _estado(pool, conta):
    with pool.connection() as c:
        return {
            "conversas": c.execute("select count(*) from conversas where conta_id=%s",
                                   (conta,)).fetchone()[0],
            "mensagens": c.execute("""select count(*) from mensagens m join conversas cv
                                        on cv.id=m.conversa_id where cv.conta_id=%s""",
                                   (conta,)).fetchone()[0],
            "contatos": c.execute("select count(*) from wa_contatos where conta_id=%s",
                                  (conta,)).fetchone()[0],
            "canal_ativo": c.execute("""select ativo from canais_config
                                         where conta_id=%s and canal='whatsapp'""",
                                     (conta,)).fetchone()[0],
        }


def _deslogar(conta_id, segredo=_SEGREDO):
    return asyncio.run(pp.webhook_wa_qr_deslogado(
        _FakeRequest({"conta_id": conta_id}, segredo)))


# ------------------------------------------------------------------ o que apaga

def test_logout_desliga_o_canal_e_preserva_o_historico(pool):
    conta = _conta_com_historico(pool, "QR que deslogou", "qr")
    resp = _deslogar(conta)
    assert resp.status_code == 200
    e = _estado(pool, conta)
    assert e["canal_ativo"] is False, "o canal precisa sair do ar — a sessão não existe mais"
    assert (e["conversas"], e["mensagens"], e["contatos"]) == (1, 1, 1), (
        "deslogar não pode custar o histórico de conversa com os leads"
    )


def _marco(pool, conta):
    with pool.connection() as c:
        return c.execute("""select desconectado_em from canais_config
                             where conta_id=%s and canal='whatsapp'""",
                         (conta,)).fetchone()[0]


def test_logout_carimba_o_marco_da_retencao(pool):
    """`desconectado_em` é o marco zero dos 30 dias (migração 165). Sem ele o
    relógio não tem de onde partir, e a faxina nunca saberia o que venceu."""
    conta = _conta_com_historico(pool, "QR pra carimbar", "qr")
    assert _marco(pool, conta) is None
    _deslogar(conta)
    assert _marco(pool, conta) is not None


def test_deslogar_de_novo_nao_empurra_o_prazo(pool):
    """`coalesce` no UPDATE: só a PRIMEIRA desconexão carimba. Um pareamento que
    cai em looping renovaria o marco a cada queda e o histórico ficaria retido
    pra sempre — a regra dos 30 dias nunca venceria."""
    conta = _conta_com_historico(pool, "QR em looping", "qr")
    _deslogar(conta)
    primeiro = _marco(pool, conta)
    _deslogar(conta)
    assert _marco(pool, conta) == primeiro


def test_logout_de_conta_twilio_nao_encosta_em_nada(pool):
    """O `conta_id` vem do corpo do POST: sem conferir o provedor, um id trocado
    apagava o histórico de uma empresa que nem usa QR."""
    conta = _conta_com_historico(pool, "Twilio alheia", "twilio")
    antes = _estado(pool, conta)
    resp = _deslogar(conta)
    assert resp.status_code == 200
    assert _estado(pool, conta) == antes


def test_logout_sem_segredo_e_403(pool):
    conta = _conta_com_historico(pool, "Sem segredo", "qr")
    assert _deslogar(conta, segredo="chute").status_code == 403
    assert _deslogar(conta, segredo=None).status_code == 403
    assert _estado(pool, conta)["canal_ativo"] is True


def test_sem_segredo_no_ambiente_ninguem_entra(pool, monkeypatch):
    """Se WA_QR_SHARED_SECRET some do ambiente, a rota não pode virar aberta."""
    monkeypatch.delenv("WA_QR_SHARED_SECRET", raising=False)
    conta = _conta_com_historico(pool, "Ambiente pelado", "qr")
    assert _deslogar(conta, segredo="").status_code == 403
    assert _estado(pool, conta)["canal_ativo"] is True


# ------------------------------------------------------- guarda nas outras rotas

def test_status_so_mexe_em_conta_no_qr(pool):
    alheia = _conta_com_historico(pool, "Twilio recibo", "twilio")
    with pool.connection() as c:
        c.execute("""update mensagens m set provider_sid='MSG1'
                       from conversas cv where cv.id=m.conversa_id and cv.conta_id=%s""",
                  (alheia,))
        c.commit()
    resp = asyncio.run(pp.webhook_wa_qr_status(
        _FakeRequest({"conta_id": alheia, "itens": [{"id": "MSG1", "status": "lido"}]})))
    assert resp.status_code == 200
    with pool.connection() as c:
        st = c.execute("""select m.status from mensagens m join conversas cv
                            on cv.id=m.conversa_id where cv.conta_id=%s""", (alheia,)).fetchone()[0]
    assert st is None, "recibo de QR não pode reescrever o status de outra via"


def test_status_atualiza_conta_no_qr(pool):
    conta = _conta_com_historico(pool, "QR recibo", "qr")
    with pool.connection() as c:
        c.execute("""update mensagens m set provider_sid='MSG2'
                       from conversas cv where cv.id=m.conversa_id and cv.conta_id=%s""",
                  (conta,))
        c.commit()
    asyncio.run(pp.webhook_wa_qr_status(
        _FakeRequest({"conta_id": conta, "itens": [{"id": "MSG2", "status": "lido"}]})))
    with pool.connection() as c:
        st = c.execute("""select m.status from mensagens m join conversas cv
                            on cv.id=m.conversa_id where cv.conta_id=%s""", (conta,)).fetchone()[0]
    assert st == "lido"


def test_audio_so_transcreve_em_conta_no_qr(pool, monkeypatch):
    """A guarda tem que barrar ANTES de chamar o transcritor (que custa dinheiro)."""
    chamou = []
    monkeypatch.setattr("core.transcribe.transcritor_se_configurado",
                        lambda: chamou.append(1))
    alheia = _conta_com_historico(pool, "Twilio audio", "twilio")
    resp = asyncio.run(pp.webhook_wa_qr_audio(
        _FakeRequest({"conta_id": alheia, "id": "MSG3", "audio_b64": "eA=="})))
    assert resp.status_code == 200
    assert chamou == []
