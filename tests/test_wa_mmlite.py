"""MM Lite: enviar_template roteia pro endpoint /marketing_messages quando mmlite=True,
e pro /messages (Cloud API comum) por padrão — mesmo payload nos dois."""
from finance import whatsapp_cloud as wc


def test_mmlite_usa_endpoint_marketing_messages(monkeypatch):
    capt = {}

    def fake_post(pid, token, payload, endpoint="messages"):
        capt["endpoint"] = endpoint
        capt["payload"] = payload
        return {"ok": True, "sid": "x"}

    monkeypatch.setattr(wc, "_post", fake_post)
    wc.enviar_template("PID", "TOK", "5586999998888", "meu_template", {"1": "a"}, mmlite=True)
    assert capt["endpoint"] == "marketing_messages"
    # mesmo payload de template do Cloud API
    assert capt["payload"]["type"] == "template"
    assert capt["payload"]["template"]["name"] == "meu_template"


def test_cloud_padrao_usa_endpoint_messages(monkeypatch):
    capt = {}

    def fake_post(pid, token, payload, endpoint="messages"):
        capt["endpoint"] = endpoint
        return {"ok": True, "sid": "x"}

    monkeypatch.setattr(wc, "_post", fake_post)
    wc.enviar_template("PID", "TOK", "5586999998888", "meu_template", {"1": "a"})
    assert capt["endpoint"] == "messages"


def test_enviar_texto_sempre_messages(monkeypatch):
    capt = {}

    def fake_post(pid, token, payload, endpoint="messages"):
        capt["endpoint"] = endpoint
        return {"ok": True, "sid": "x"}

    monkeypatch.setattr(wc, "_post", fake_post)
    wc.enviar_texto("PID", "TOK", "5586999998888", "oi")
    assert capt["endpoint"] == "messages"
