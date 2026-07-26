"""Rota /webhook/telegram do web/app.py (Telegram por webhook, unifica servidores).

Cobre o essencial sem tocar a rede do Telegram:
- um update do Telegram e' parseado corretamente;
- a rota rejeita quando o bot nao esta' configurado (503);
- valida o secret do header (403 quando nao bate);
- com secret certo, enfileira o update e responde 200 rapido.
"""
import pytest
from fastapi.testclient import TestClient

import web.app as wa


UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 10, "date": 0,
        "chat": {"id": 55, "type": "private"},
        "from": {"id": 55, "is_bot": False, "first_name": "Cliente"},
        "text": "oi",
    },
}


def test_update_parse_offline():
    from telegram import Bot, Update
    u = Update.de_json(UPDATE, Bot("123456:FAKE"))   # Bot offline, sem rede
    assert u.effective_chat.id == 55
    assert u.message.text == "oi"


class _FilaFake:
    def __init__(self):
        self.itens = []

    async def put(self, x):
        self.itens.append(x)


class _AppFake:
    def __init__(self):
        from telegram import Bot
        self.bot = Bot("123456:FAKE")
        self.update_queue = _FilaFake()

    async def stop(self):
        pass

    async def shutdown(self):
        pass


def test_rota_503_quando_nao_configurado(monkeypatch):
    monkeypatch.setattr(wa, "_tg_app", None)
    with TestClient(wa.app) as c:
        monkeypatch.setattr(wa, "_tg_app", None)   # startup nao muda (sem token)
        r = c.post("/webhook/telegram", json=UPDATE)
    assert r.status_code == 503


def test_rota_rejeita_secret_errado(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3gr3d0")
    with TestClient(wa.app) as c:
        monkeypatch.setattr(wa, "_tg_app", _AppFake())
        r = c.post("/webhook/telegram", json=UPDATE,
                   headers={"X-Telegram-Bot-Api-Secret-Token": "errado"})
    assert r.status_code == 403


def test_rota_aceita_e_enfileira(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3gr3d0")
    fake = _AppFake()
    with TestClient(wa.app) as c:
        monkeypatch.setattr(wa, "_tg_app", fake)
        r = c.post("/webhook/telegram", json=UPDATE,
                   headers={"X-Telegram-Bot-Api-Secret-Token": "s3gr3d0"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(fake.update_queue.itens) == 1        # update foi enfileirado
    assert fake.update_queue.itens[0].message.text == "oi"


# --- registro do webhook resiliente a flood (429) ----------------------------
# Regressao: com 2 workers do uvicorn, os dois chamam setWebhook e um leva 429.
# Isso NAO pode derrubar o app do Telegram naquele worker (senao ~metade dos
# updates cai em 503). _registrar_webhook_telegram tolera o flood e nunca levanta.

class _BotFlood:
    """setWebhook levanta RetryAfter nas 1as vezes, depois sucede."""
    def __init__(self, falhas_antes=1):
        self.restam = falhas_antes
        self.chamadas = 0

    async def set_webhook(self, **kw):
        from telegram.error import RetryAfter
        self.chamadas += 1
        if self.restam > 0:
            self.restam -= 1
            raise RetryAfter(0)     # 0s: nao atrasa o teste
        return True


class _BotSempreFalha:
    async def set_webhook(self, **kw):
        raise RuntimeError("boom")


def test_registrar_webhook_tolera_flood_e_sucede():
    import asyncio
    bot = _BotFlood(falhas_antes=1)
    ok = asyncio.run(wa._registrar_webhook_telegram(bot, "https://x/webhook/telegram",
                                                     "s", tentativas=2))
    assert ok is True and bot.chamadas == 2


def test_registrar_webhook_nunca_propaga():
    import asyncio
    # mesmo falhando sempre, retorna False (nao levanta) - o app segue vivo
    ok = asyncio.run(wa._registrar_webhook_telegram(_BotSempreFalha(),
                                                    "https://x/webhook/telegram", None))
    assert ok is False


# --- _setup publica _pool por ultimo (regressao do brain=None) ---------------
# Bug: _setup setava _pool ANTES de _brain. Com o startup do Telegram e a thread
# do poller de e-mail chamando _setup concorrente, o Telegram via _pool setado e
# _brain ainda None -> agente com brain None -> 'NoneType' has no attribute
# 'chamar'. A correcao publica o sentinela _pool DEPOIS de _brain.

def test_setup_publica_pool_por_ultimo(monkeypatch):
    monkeypatch.setattr(wa, "_pool", None)
    monkeypatch.setattr(wa, "_brain", None)
    monkeypatch.setattr(wa, "_transcritor", None)
    monkeypatch.setattr(wa, "get_pool", lambda: object())
    monkeypatch.setattr(wa, "init_schema", lambda p: None)
    monkeypatch.setattr(wa, "transcritor_se_configurado", lambda: None)

    visto = {}

    class _BrainSpy:
        def __init__(self, **kw):
            # no instante em que o Brain e' construido, _pool AINDA deve ser None
            # (sentinela publicado por ultimo) e _brain ainda nao publicado.
            visto["pool_durante_brain"] = wa._pool

    monkeypatch.setattr(wa, "Brain", _BrainSpy)
    wa._setup()

    assert visto["pool_durante_brain"] is None      # _pool NAO visivel antes de _brain
    assert wa._brain is not None and wa._pool is not None
