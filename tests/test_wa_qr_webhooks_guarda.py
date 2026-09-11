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
  nome_fantasia text,
  chip_de bigint references contas(id) on delete cascade);
create table conversas (id bigserial primary key, conta_id bigint, canal text,
  contato text, status text, chip_id bigint references contas(id) on delete set null, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, status text,
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table wa_contatos (id bigserial primary key, conta_id bigint, numero text, nome text);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text,
  -- o apelido do chip ("CP Zarb", "CP Thiago"): é como o dono reconhece QUAL
  -- aparelho parou, quando a empresa tem mais de um
  rotulo text,
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


# ------------------------------------------------------------------ o chip quebrado

def _chip_quebrado(conta_id, segredo=_SEGREDO, aberturas=3):
    return asyncio.run(pp.webhook_wa_qr_chip_quebrado(
        _FakeRequest({"conta_id": conta_id, "aberturas": aberturas}, segredo)))


def _mensagem_presa(conta_id, segredo=_SEGREDO, quedas=3, ack="3A4B3E86598B9C5136C1"):
    return asyncio.run(pp.webhook_wa_qr_chip_quebrado(_FakeRequest(
        {"conta_id": conta_id, "motivo": "mensagem_presa",
         "quedas": quedas, "ack_id": ack}, segredo)))


def test_chip_quebrado_avisa_o_dono_e_o_admin(pool, monkeypatch):
    """O disjuntor do serviço Node desistiu do chip: a sessão do Signal não
    decifra mais e não se conserta sozinha.

    Até 07/09/2026 esse diagnóstico morria numa linha de log. O dono descobria
    pelo e-mail de health check do Render — quando descobria. Este teste fixa que
    ele sai do log e chega em gente."""
    conta = _conta_com_historico(pool, "PRIME EVENTOS", "qr")
    from finance import notificar as nt
    avisos = []
    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, texto: avisos.append(("dono", cid, texto)) or True)
    monkeypatch.setattr(nt, "avisar_admin",
                        lambda assunto, msg: avisos.append(("admin", assunto, msg)) or True)
    resp = _chip_quebrado(conta)
    assert resp.status_code == 200
    quem = {a[0] for a in avisos}
    assert quem == {"dono", "admin"}, (
        "os dois precisam saber: o dono resolve no celular, o admin resolve sem "
        f"depender de o cliente estar por perto — chegou em {quem}")
    texto = next(a[2] for a in avisos if a[0] == "dono")
    assert "Aparelhos conectados" in texto, (
        "o aviso tem que dizer o que fazer, não só que quebrou")
    assert "PRIME EVENTOS" in texto, "e de qual empresa é o chip"


def test_chip_quebrado_diz_o_apelido_do_chip(pool, monkeypatch):
    """Empresa com dois chips (a Prime tem "CP Zarb" e "CP Thiago"): sem o apelido
    o dono não sabe QUAL aparelho pegar."""
    conta = _conta_com_historico(pool, "PRIME EVENTOS", "qr")
    with pool.connection() as c:
        c.execute("""update canais_config set rotulo='CP Zarb'
                      where conta_id=%s and canal='whatsapp'""", (conta,))
        c.commit()
    from finance import notificar as nt
    avisos = []
    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, texto: avisos.append(texto) or True)
    monkeypatch.setattr(nt, "avisar_admin", lambda *a, **k: True)
    _chip_quebrado(conta)
    assert "CP Zarb" in avisos[0] and "PRIME EVENTOS" in avisos[0]


def test_chip_quebrado_nao_apaga_nem_desliga_nada(pool, monkeypatch):
    """O cofre está ÍNTEGRO nesse estado (deslogado=false em todos os fechamentos
    de 07/09). Desligar o canal aqui tiraria a conta da tela sem consertar nada, e
    apagar seria o erro de 22/08 de novo."""
    conta = _conta_com_historico(pool, "Chip que parou", "qr")
    from finance import notificar as nt
    monkeypatch.setattr(nt, "enviar_para_dono", lambda *a, **k: True)
    monkeypatch.setattr(nt, "avisar_admin", lambda *a, **k: True)
    antes = _estado(pool, conta)
    _chip_quebrado(conta)
    assert _estado(pool, conta) == antes, (
        "avisar não pode mexer em canal, conversa, mensagem nem contato")


def test_chip_quebrado_sem_segredo_e_403(pool):
    conta = _conta_com_historico(pool, "Alheia", "qr")
    assert _chip_quebrado(conta, segredo="errado").status_code == 403


def test_chip_quebrado_com_corpo_torto_nao_derruba(pool, monkeypatch):
    """Vem do serviço Node por HTTP: corpo torto não pode virar 500 num caminho
    que existe justamente pra avisar que algo já está ruim."""
    from finance import notificar as nt
    monkeypatch.setattr(nt, "enviar_para_dono", lambda *a, **k: True)
    monkeypatch.setattr(nt, "avisar_admin", lambda *a, **k: True)
    for corpo in ({}, {"conta_id": "abc"}, {"conta_id": 0}, {"aberturas": 3}):
        r = asyncio.run(pp.webhook_wa_qr_chip_quebrado(_FakeRequest(corpo, _SEGREDO)))
        assert r.status_code == 200, corpo


def test_chip_quebrado_tolera_aviso_que_falha(pool, monkeypatch):
    """Telegram fora do ar não pode derrubar o webhook: o dono sem Telegram
    vinculado é o caso NORMAL (o da Prime não tem)."""
    conta = _conta_com_historico(pool, "Sem telegram", "qr")
    from finance import notificar as nt
    def _explode(*a, **k):
        raise RuntimeError("telegram fora do ar")
    monkeypatch.setattr(nt, "enviar_para_dono", _explode)
    monkeypatch.setattr(nt, "avisar_admin", _explode)
    assert _chip_quebrado(conta).status_code == 200


def test_mensagem_presa_manda_o_dono_NAO_parear(pool, monkeypatch):
    """O aviso OPOSTO ao do chip quebrado, e o oposto é o ponto.

    Medido na noite de 08→09/09/2026: a conta 34 caiu onze vezes e as onze linhas
    de `stream errored out` traziam o mesmo id de ack. O WhatsApp reentrega uma
    mensagem, o `ack` é recusado, a conexão morre — e entre uma queda e outra o
    chip volta sozinho e segue atendendo.

    Quem vê o chip caindo faz a coisa mais natural do mundo: pareia de novo. E
    parear é justamente o que INSTALA outro laço — os dois chips no laço naquele
    dia eram os dois pareados nas últimas 24h; o que não era pareado há 20 dias
    não tinha nenhum. Por isso este aviso tem que dizer, com todas as letras, pra
    não parear."""
    conta = _conta_com_historico(pool, "PRIME EVENTOS", "qr")
    from finance import notificar as nt
    avisos = []
    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, texto: avisos.append(("dono", texto)) or True)
    monkeypatch.setattr(nt, "avisar_admin",
                        lambda assunto, msg: avisos.append(("admin", msg)) or True)
    resp = _mensagem_presa(conta, quedas=11)
    assert resp.status_code == 200
    assert {a[0] for a in avisos} == {"dono", "admin"}
    texto = next(a[1] for a in avisos if a[0] == "dono")
    assert "NÃO parear" in texto, (
        "sem isso o dono pareia de novo e instala outro laço — é a ação errada "
        f"mais provável, e o aviso existe pra barrá-la. Saiu: {texto!r}")
    assert "11" in texto, "e quantas quedas a mesma mensagem já causou"
    assert "PRIME EVENTOS" in texto, "e de qual empresa é o chip"


def test_mensagem_presa_nao_repete_o_texto_do_chip_quebrado(pool, monkeypatch):
    """Os dois avisos pedem AÇÕES OPOSTAS: o do chip quebrado manda parear, o da
    mensagem presa manda não parear. Se um dia alguém unificar os textos pra
    economizar código, este teste quebra antes de o dono receber a instrução
    errada."""
    conta = _conta_com_historico(pool, "Chip no laço", "qr")
    from finance import notificar as nt
    textos = {}
    monkeypatch.setattr(nt, "avisar_admin", lambda *a, **k: True)
    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, texto: textos.__setitem__(textos.get("k"), texto) or True)

    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, t: textos.__setitem__("quebrado", t) or True)
    _chip_quebrado(conta)
    monkeypatch.setattr(nt, "enviar_para_dono",
                        lambda pool_, cid, t: textos.__setitem__("presa", t) or True)
    _mensagem_presa(conta)

    assert textos["quebrado"] != textos["presa"], "os dois avisos não podem ser o mesmo"
    assert "Aparelhos conectados" in textos["quebrado"]
    assert "Aparelhos conectados" not in textos["presa"], (
        "mandar mexer em Aparelhos conectados aqui é empurrar pro pareamento, "
        "que é exatamente o que não se deve fazer")


def test_mensagem_presa_nao_apaga_nem_desliga_nada(pool, monkeypatch):
    """A conta está SAUDÁVEL entre as quedas — recebendo e enviando. Desligar o
    canal por causa deste aviso tiraria da tela uma conta que está atendendo."""
    conta = _conta_com_historico(pool, "Chip no laço", "qr")
    from finance import notificar as nt
    monkeypatch.setattr(nt, "enviar_para_dono", lambda *a, **k: True)
    monkeypatch.setattr(nt, "avisar_admin", lambda *a, **k: True)
    with pool.connection() as c:
        antes = c.execute("""select ativo, desconectado_em from canais_config
                              where conta_id=%s and canal='whatsapp'""", (conta,)).fetchone()
        msgs_antes = c.execute("select count(*) from mensagens").fetchone()[0]
    _mensagem_presa(conta)
    with pool.connection() as c:
        depois = c.execute("""select ativo, desconectado_em from canais_config
                               where conta_id=%s and canal='whatsapp'""", (conta,)).fetchone()
        msgs_depois = c.execute("select count(*) from mensagens").fetchone()[0]
    assert antes == depois, "o canal não pode ser mexido: a conta está atendendo"
    assert msgs_antes == msgs_depois, "e nada de mensagem some"
