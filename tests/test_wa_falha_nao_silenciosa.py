"""Falha de WhatsApp que não pode morrer em silêncio.

Dois pontos cegos que sobraram do mapeamento dos 3 provedores:

* **Cloud API e QR não avisavam ninguém.** `core.falhas.avaliar_falha_provedor` é o
  gancho que leva ao admin uma falha SISTÊMICA de provedor pago (token expirado,
  quota estourada, crédito acabado). Asaas, Credify, Data Market e o Twilio
  chamavam; a Cloud API e o QR, não — justamente os dois que carregam a maior
  parte do volume. O canal inteiro podia estar fora do ar sem uma linha de aviso.

* **Aviso de lead novo pro vendedor sumia num `except: pass`.** Fora da janela de
  24h e sem template, a Meta bloqueia — comportamento esperado. O problema era não
  registrar: o vendedor parava de receber lead e ninguém ficava sabendo.
"""
import logging

from finance import distribuicao as dist
from finance import whatsapp_cloud as wc
from finance import whatsapp_qr as wq


class _Espia:
    """Captura o que foi mandado pro admin, sem sair pra rede."""

    def __init__(self):
        self.chamadas = []

    def __call__(self, erro, servico, canal=""):
        self.chamadas.append({"erro": str(erro), "servico": servico, "canal": canal})
        return "credito"


def _espiar(monkeypatch):
    espia = _Espia()
    monkeypatch.setattr("core.falhas.avaliar_falha_provedor", espia)
    return espia


# ------------------------------------------------------------------- Cloud API

def test_cloud_http_avisa_o_admin(monkeypatch):
    import urllib.error
    espia = _espiar(monkeypatch)

    def explode(*a, **k):
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", explode)
    r = wc._post("PID", "TOK", {"a": 1})
    assert r["ok"] is False
    assert len(espia.chamadas) == 1
    assert espia.chamadas[0]["servico"] == "WhatsApp Cloud API"
    assert espia.chamadas[0]["canal"] == "whatsapp"
    assert "401" in espia.chamadas[0]["erro"]


def test_cloud_erro_de_rede_avisa_o_admin(monkeypatch):
    espia = _espiar(monkeypatch)

    def explode(*a, **k):
        raise OSError("conexão recusada")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    assert wc._post("PID", "TOK", {"a": 1})["ok"] is False
    assert espia.chamadas[0]["servico"] == "WhatsApp Cloud API"


# -------------------------------------------------------------------------- QR

def test_qr_http_avisa_o_admin(monkeypatch):
    import urllib.error
    monkeypatch.setenv("WA_QR_SERVICE_URL", "https://qr.exemplo")
    monkeypatch.setenv("WA_QR_SHARED_SECRET", "s")
    espia = _espiar(monkeypatch)

    def explode(*a, **k):
        raise urllib.error.HTTPError("http://x", 500, "Boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", explode)
    r = wq._req("POST", "/session/1/enviar", {"n": "1"})
    assert r["ok"] is False
    assert espia.chamadas[0]["servico"] == "WhatsApp (QR)"
    assert espia.chamadas[0]["canal"] == "whatsapp"


def test_qr_desligado_nao_alarma(monkeypatch):
    """Sem o serviço configurado não há falha nenhuma pra avisar — é só um no-op."""
    monkeypatch.delenv("WA_QR_SERVICE_URL", raising=False)
    monkeypatch.delenv("WA_QR_SHARED_SECRET", raising=False)
    espia = _espiar(monkeypatch)
    assert wq._req("POST", "/qualquer")["erro"] == "qr_indisponivel"
    assert espia.chamadas == []


# ------------------------------------------------------- aviso de lead ao vendedor

class _PoolFalso:
    """`with pool.connection() as c` sem banco: o envio é substituído no teste."""

    def connection(self):
        class _Ctx:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, *a):
                return False
        return _Ctx()


def test_aviso_de_lead_que_nao_sai_aparece_no_log(monkeypatch, caplog):
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar",
                        lambda c, conta_id, numero, texto: {"ok": False,
                                                            "erro": "fora_da_janela"})
    with caplog.at_level(logging.WARNING, logger=dist.__name__):
        dist._avisar_whatsapp(_PoolFalso(), 7, "5586990001111", "", "Padaria", "Lead novo")
    assert "fora_da_janela" in caplog.text
    assert "conta=7" in caplog.text, "sem a conta no log não dá pra saber quem ficou sem aviso"


def test_aviso_de_lead_que_sai_nao_polui_o_log(monkeypatch, caplog):
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar",
                        lambda c, conta_id, numero, texto: {"ok": True, "sid": "x"})
    with caplog.at_level(logging.WARNING, logger=dist.__name__):
        dist._avisar_whatsapp(_PoolFalso(), 7, "5586990001111", "", "Padaria", "Lead novo")
    assert caplog.records == []


def test_excecao_no_aviso_de_lead_nao_derruba_a_distribuicao(monkeypatch, caplog):
    import finance.whatsapp_out as wo

    def explode(*a, **k):
        raise RuntimeError("pool morreu")

    monkeypatch.setattr(wo, "enviar", explode)
    with caplog.at_level(logging.WARNING, logger=dist.__name__):
        dist._avisar_whatsapp(_PoolFalso(), 7, "5586990001111", "", "Padaria", "Lead novo")
    assert "pool morreu" in caplog.text
