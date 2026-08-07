"""Convite de equipe agora dispara e-mail automático e o link vem do APP_URL
(não do request.base_url, que atrás do proxy do Render saía errado).

Testa os helpers puros + o endpoint (monkeypatch, sem banco): o e-mail é enviado, o
link fica correto, e o fallback (SMTP off) mostra o link pra copiar.
"""
from types import SimpleNamespace

from contas import equipe as eq  # noqa: F401  (garante import do módulo p/ monkeypatch)
from finance import email_sender
import web.painel_equipe as pe


def test_link_usa_app_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.zaq.com.br/")   # com barra no fim
    assert pe._link("TOK123") == "https://app.zaq.com.br/equipe/convite/TOK123"


def test_enviar_convite_equipe_monta_email(monkeypatch):
    capt = {}

    def fake_enviar(destino, assunto, html, texto, **kw):
        capt.update(destino=destino, assunto=assunto, html=html, texto=texto)
        return True

    monkeypatch.setattr(email_sender, "enviar_email", fake_enviar)
    ok = email_sender.enviar_convite_equipe(
        "pedro@x.com", "Pedro", "Zaq Prospecção", "Gestor",
        "https://z/equipe/convite/T")
    assert ok is True
    assert capt["destino"] == "pedro@x.com"
    assert "Zaq Prospecção" in capt["assunto"]
    assert "https://z/equipe/convite/T" in capt["html"]   # link no corpo
    assert "Gestor" in capt["html"]                        # papel no corpo
    assert "https://z/equipe/convite/T" in capt["texto"]   # e na versão texto


def _req(papel="dono"):
    return SimpleNamespace(session={"conta_id": 7, "papel": papel})


def _mock_dono(monkeypatch, nome_empresa="Zaq Prospecção"):
    monkeypatch.setattr(pe, "conta_logada", lambda req: (7, "pj", nome_empresa))
    monkeypatch.setattr(pe, "get_pool", lambda: None)


def test_convidar_dispara_email_e_link(monkeypatch):
    _mock_dono(monkeypatch)
    monkeypatch.setenv("APP_URL", "https://z.app")
    monkeypatch.setattr(pe.eq, "convidar",
                        lambda pool, cid, nome, email, papel: {"ok": True, "token": "TOK", "ja_tem_login": False})
    sent = {}
    monkeypatch.setattr(pe, "_enviar_email_convite",
                        lambda conta, nome, email, papel, link: sent.update(email=email, link=link) or True)

    req = _req()
    pe.painel_equipe_convidar(req, nome="Pedro", email="pedro@x.com", papel="gestor")

    assert sent["email"] == "pedro@x.com"
    assert req.session["equipe_link"] == "https://z.app/equipe/convite/TOK"
    assert sent["link"] == req.session["equipe_link"]
    assert "e-mail" in req.session["equipe_aviso"].lower()


def test_convidar_sem_smtp_mostra_link(monkeypatch):
    _mock_dono(monkeypatch, "Zaq")
    monkeypatch.setenv("APP_URL", "https://z.app")
    monkeypatch.setattr(pe.eq, "convidar",
                        lambda *a, **k: {"ok": True, "token": "T2", "ja_tem_login": False})
    monkeypatch.setattr(pe, "_enviar_email_convite", lambda *a, **k: False)   # SMTP falhou

    req = _req()
    pe.painel_equipe_convidar(req, nome="", email="x@y.com", papel="vendedor")

    assert req.session["equipe_link"] == "https://z.app/equipe/convite/T2"
    assert "smtp" in req.session["equipe_aviso"].lower()


def test_ja_tem_login_nao_manda_link_nem_email(monkeypatch):
    _mock_dono(monkeypatch)
    monkeypatch.setattr(pe.eq, "convidar", lambda *a, **k: {"ok": True, "ja_tem_login": True})
    chamadas = {"n": 0}
    monkeypatch.setattr(pe, "_enviar_email_convite",
                        lambda *a, **k: chamadas.update(n=chamadas["n"] + 1) or True)

    req = _req()
    pe.painel_equipe_convidar(req, nome="", email="x@y.com", papel="gestor")

    assert "equipe_link" not in req.session
    assert chamadas["n"] == 0                       # ninguém já-logado recebe e-mail de link
    assert "já tem login" in req.session["equipe_aviso"].lower()
