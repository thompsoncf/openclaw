"""Transcrição de áudio nos DOIS lados da conversa.

A transcrição só era disparada na ENTRADA (`repassarEntrada`, no serviço Node);
`repassarSaida` não chamava nada. Resultado: o áudio que o cliente manda virava
texto, e o áudio que o vendedor grava pelo celular ficava como um "🎤 Áudio (0:09)"
mudo. Quem abrisse a conversa depois lia a pergunta e não lia a resposta — e é
justamente a resposta que diz o que foi combinado.

No dia em que isso apareceu havia 2 áudios na base, os DOIS de saída, os dois sem
uma palavra transcrita.

O conserto no Node é uma linha. O que estes testes prendem é o lado Python, que é
o que torna essa linha suficiente: a rota `/webhooks/wa-qr/audio` casa a mensagem
por `provider_sid` + conta e **não olha direção** — por isso não precisou de rota
nova, e por isso não pode passar a olhar.

As outras três regras da rota continuam valendo e estão aqui porque um áudio de
saída passa por elas igual:

* a guarda de conta em QR (a rota é pública, e transcrever custa dinheiro);
* o texto entra UMA vez (a marca crua deixa de casar depois da primeira);
* mensagem de texto de verdade nunca é tocada.
"""
import asyncio
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SEGREDO = "segredo-de-teste"

_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table conversas (id bigserial primary key, conta_id bigint, canal text,
  contato_ref text, status text, chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, status text,
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, ativo boolean not null default true, token text,
  provedor text not null default 'twilio', wa_phone_id text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_qr_audio_test"
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


class _FakeRequest:
    def __init__(self, corpo: dict, segredo: str | None = _SEGREDO):
        self.headers = {"x-wa-secret": segredo} if segredo is not None else {}
        self._corpo = json.dumps(corpo).encode("utf-8")

    async def body(self):
        return self._corpo


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setenv("WA_QR_SHARED_SECRET", _SEGREDO)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)


@pytest.fixture()
def transcreve(monkeypatch):
    """Transcritor de mentira — o que está em teste é o que a rota FAZ com o texto."""
    class _Tr:
        def transcrever(self, dados, nome):
            return "bom dia, confirmando o salão pra sábado"
    monkeypatch.setattr("core.transcribe.transcritor_se_configurado", lambda: _Tr())
    return _Tr


def _conta(pool, provedor="qr") -> int:
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime') returning id"
                        ).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                     values (%s,'whatsapp','5586990001111',%s)""", (cid, provedor))
        c.commit()
        return cid


def _msg(pool, conta, *, direcao, texto, sid) -> int:
    with pool.connection() as c:
        conv = c.execute("""insert into conversas (conta_id, canal, contato_ref, status)
                            values (%s,'whatsapp','5586981885930','aberta') returning id""",
                         (conta,)).fetchone()[0]
        mid = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor,
                             texto, provider_sid)
                           values (%s,'whatsapp',%s,%s,%s,%s) returning id""",
                        (conv, direcao, "lead" if direcao == "in" else "humano",
                         texto, sid)).fetchone()[0]
        c.commit()
        return mid


def _texto(pool, mid) -> str:
    with pool.connection() as c:
        return c.execute("select texto from mensagens where id=%s", (mid,)).fetchone()[0]


def _chamar(conta, sid):
    return asyncio.run(pp.webhook_wa_qr_audio(
        _FakeRequest({"conta_id": conta, "id": sid, "audio_b64": "eA==", "segundos": 9})))


# ------------------------------------------------------------- os dois lados

def test_audio_do_cliente_vira_texto(pool, transcreve):
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="in", texto="🎤 Áudio (0:09)", sid="A1")
    _chamar(conta, "A1")
    assert _texto(pool, mid) == "🎤 Áudio (0:09)\nbom dia, confirmando o salão pra sábado"


def test_audio_do_vendedor_tambem_vira_texto(pool, transcreve):
    """O que faltava. A rota casa por sid e conta, sem olhar direção — é isso que
    deixa a mesma rota servir os dois lados."""
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A2")
    _chamar(conta, "A2")
    assert "bom dia, confirmando o salão pra sábado" in _texto(pool, mid)


def test_a_marca_de_musica_tambem(pool, transcreve):
    """Áudio enviado como arquivo vem com 🎵 em vez de 🎤."""
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎵 Áudio (1:03)", sid="A3")
    _chamar(conta, "A3")
    assert _texto(pool, mid).startswith("🎵 Áudio (1:03)\n")


# ------------------------------------------------- o que não pode ser tocado

def test_transcricao_nao_entra_duas_vezes(pool, transcreve):
    """A reentrega do mesmo áudio não pode empilhar o texto: depois da primeira
    vez a marca deixa de casar sozinha."""
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A4")
    _chamar(conta, "A4")
    _chamar(conta, "A4")
    assert _texto(pool, mid).count("bom dia") == 1


def test_mensagem_de_texto_de_verdade_fica_intacta(pool, transcreve):
    """Mesmo que alguém escreva a marca no MEIO de uma frase — o regex é ancorado."""
    conta = _conta(pool)
    original = "olha esse 🎤 Áudio (0:09) que te mandei"
    mid = _msg(pool, conta, direcao="out", texto=original, sid="A5")
    _chamar(conta, "A5")
    assert _texto(pool, mid) == original


def test_outra_conta_nao_e_tocada(pool, transcreve):
    """O sid do WhatsApp é o MESMO nas duas pontas quando as duas contas são do
    mesmo Zaq (ver migração 159) — o par (conta, sid) é o que separa."""
    conta = _conta(pool)
    outra = _conta(pool)
    minha = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A6")
    dela = _msg(pool, outra, direcao="in", texto="🎤 Áudio (0:09)", sid="A6")
    _chamar(conta, "A6")
    assert "bom dia" in _texto(pool, minha)
    assert _texto(pool, dela) == "🎤 Áudio (0:09)"


def test_conta_fora_do_qr_nem_chama_o_transcritor(pool, monkeypatch):
    """A rota é pública e transcrever custa dinheiro: a guarda vem ANTES."""
    chamou = []
    monkeypatch.setattr("core.transcribe.transcritor_se_configurado",
                        lambda: chamou.append(1))
    conta = _conta(pool, provedor="twilio")
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A7")
    assert _chamar(conta, "A7").status_code == 200
    assert chamou == []
    assert _texto(pool, mid) == "🎤 Áudio (0:09)"


def test_sem_segredo_ninguem_transcreve(pool, transcreve):
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A8")
    resp = asyncio.run(pp.webhook_wa_qr_audio(
        _FakeRequest({"conta_id": conta, "id": "A8", "audio_b64": "eA=="}, segredo="errado")))
    assert resp.status_code == 403
    assert _texto(pool, mid) == "🎤 Áudio (0:09)"


def test_transcricao_vazia_deixa_a_marca(pool, monkeypatch):
    """Áudio que o provedor não entendeu: melhor a marca sozinha que uma linha em
    branco pendurada."""
    class _Mudo:
        def transcrever(self, dados, nome):
            return "   "
    monkeypatch.setattr("core.transcribe.transcritor_se_configurado", lambda: _Mudo())
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A9")
    _chamar(conta, "A9")
    assert _texto(pool, mid) == "🎤 Áudio (0:09)"


def test_provedor_que_estoura_nao_derruba_a_conversa(pool, monkeypatch):
    class _Quebrado:
        def transcrever(self, dados, nome):
            raise RuntimeError("provedor fora do ar")
    monkeypatch.setattr("core.transcribe.transcritor_se_configurado", lambda: _Quebrado())
    conta = _conta(pool)
    mid = _msg(pool, conta, direcao="out", texto="🎤 Áudio (0:09)", sid="A10")
    assert _chamar(conta, "A10").status_code == 200
    assert _texto(pool, mid) == "🎤 Áudio (0:09)"
