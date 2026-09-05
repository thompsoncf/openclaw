"""Convite de equipe agora dispara e-mail automático e o link vem do APP_URL
(não do request.base_url, que atrás do proxy do Render saía errado).

Testa os helpers puros + o endpoint (monkeypatch, sem banco): o e-mail é enviado, o
link fica correto, e o fallback (SMTP off) mostra o link pra copiar.
"""
from types import SimpleNamespace

from contas import equipe as eq  # noqa: F401  (garante import do módulo p/ monkeypatch)
from finance import email_sender
from finance import email_inbound as ein
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
                        lambda *a, **k: {"ok": True, "token": "TOK", "ja_tem_login": False})
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


def test_convite_usa_o_remetente_do_zaq_primeiro(monkeypatch):
    """A ORDEM VIROU, e este teste virou junto.

    Ele exigia o contrário — caixa da empresa primeiro, SMTP do Zaq só de reserva. Em
    18/08 essa ordem custou um acesso: na Prime Eventos o token de entrada nasceu e
    ficou válido, e o e-mail nunca chegou, porque a caixa daquela empresa estava com
    problema (o IMAP dela deu TimeoutError no mesmo dia). Pelo painel, que manda pelo
    Zaq, o mesmo pedido chegou na hora.

    Convite é e-mail de SISTEMA: ninguém responde, só precisa chegar. Quem sai pela
    caixa da empresa é mensagem pra LEAD, pra resposta cair no inbox dela — e isso
    não muda."""
    monkeypatch.setattr(pe, "get_pool", lambda: object())
    cont = {"conta": 0, "geral": 0}
    monkeypatch.setattr(ein, "enviar_conta",
                        lambda *a, **k: cont.__setitem__("conta", cont["conta"] + 1) or True)
    monkeypatch.setattr(email_sender, "enviar_email",
                        lambda *a, **k: cont.__setitem__("geral", cont["geral"] + 1) or True)

    ok = pe._enviar_email_convite((7, "pj", "Zaq Prospecção"), "Pedro", "pedro@x.com",
                                  "gestor", "https://z/equipe/convite/T")
    assert ok is True
    assert cont["geral"] == 1 and cont["conta"] == 0    # Zaq mandou; nem tocou na empresa


def test_convite_cai_no_smtp_zaq_sem_canais(monkeypatch):
    """Convite sai pelo SMTP de sistema do Zaq.

    Desde 18/08 o Zaq é a PRIMEIRA porta (era reserva): convite e link de acesso são
    e-mail de sistema — ninguém responde, só precisam chegar —, e sair pela caixa do
    cliente fazia o convite sumir quando aquela caixa tinha problema. A caixa da
    empresa virou a reserva. Ver tests/test_email_acesso_remetente.py."""
    monkeypatch.setattr(pe, "get_pool", lambda: object())
    monkeypatch.setattr(ein, "enviar_conta", lambda *a, **k: False)   # sem caixa configurada
    cont = {"geral": 0}
    monkeypatch.setattr(email_sender, "enviar_email",
                        lambda *a, **k: cont.__setitem__("geral", cont["geral"] + 1) or True)

    ok = pe._enviar_email_convite((7, "pj", "Zaq"), "", "x@y.com", "vendedor",
                                  "https://z/equipe/convite/T2")
    assert ok is True and cont["geral"] == 1


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


# ------------------------------------------------------------------ Raio-X de segunda (o grupo)
def test_raio_x_escolher_grupo_salva_e_avisa(monkeypatch):
    """O select manda `jid|nome`; ligado é o checkbox. O que chega em
    `raio_x.definir` é o jid, o nome e o booleano, separados."""
    _mock_dono(monkeypatch)
    capt = {}
    monkeypatch.setattr(pe, "get_pool", lambda: "POOL")
    from finance import raio_x as rx
    monkeypatch.setattr(rx, "definir", lambda pool, conta, jid, nome, ativo: capt.update(
        pool=pool, conta=conta, jid=jid, nome=nome, ativo=ativo) or {"ok": True})
    req = _req()
    r = pe.painel_equipe_raio_x(req, grupo="120363012345@g.us|Vendas Prime", ativo="1")
    assert r.status_code == 303 and r.headers["location"] == "/painel/equipe"
    assert capt == {"pool": "POOL", "conta": 7, "jid": "120363012345@g.us", "nome": "Vendas Prime", "ativo": True}
    assert "ligado" in req.session["equipe_aviso"]
    # desligado
    pe.painel_equipe_raio_x(_req_ := _req(), grupo="120363012345@g.us|Vendas Prime", ativo="")
    assert capt["ativo"] is False and "desligado" in _req_.session["equipe_aviso"]


def test_raio_x_grupo_invalido_vira_erro_na_tela(monkeypatch):
    _mock_dono(monkeypatch)
    from finance import raio_x as rx
    monkeypatch.setattr(rx, "definir", lambda *a: {"ok": False, "erro": "grupo_invalido"})
    req = _req()
    pe.painel_equipe_raio_x(req, grupo="5586999990000@s.whatsapp.net|x", ativo="1")
    assert "equipe_aviso" not in req.session and "não vale" in req.session["equipe_erro"]


def test_raio_x_so_o_dono_mexe(monkeypatch):
    monkeypatch.setattr(pe, "conta_logada", lambda req: (7, "pj", "Emp"))
    from finance import raio_x as rx
    monkeypatch.setattr(rx, "definir", lambda *a: (_ for _ in ()).throw(AssertionError("não devia chamar")))
    r = pe.painel_equipe_raio_x(_req("vendedor"), grupo="120363@g.us|x", ativo="1")
    assert r.status_code == 303
    r = pe.painel_equipe_raio_x_enviar(_req("vendedor"))
    assert r.status_code == 303


def test_raio_x_mandar_agora_traduz_os_erros(monkeypatch):
    _mock_dono(monkeypatch)
    from finance import raio_x as rx
    for erro, frase in (("sem_grupo", "Escolha o grupo"), ("so_numero_proprio", "WhatsApp próprio"),
                        ("desconectado", "desconectado"), ("timeout", "Não consegui mandar: timeout")):
        monkeypatch.setattr(rx, "enviar_agora", lambda pool, conta, _e=erro: {"ok": False, "erro": _e})
        req = _req()
        pe.painel_equipe_raio_x_enviar(req)
        assert frase in req.session["equipe_erro"], erro
    monkeypatch.setattr(rx, "enviar_agora", lambda pool, conta: {"ok": True, "texto": "..."})
    req = _req()
    pe.painel_equipe_raio_x_enviar(req)
    assert "enviado no grupo" in req.session["equipe_aviso"]
    # estourou por dentro: vira erro na tela, não 500
    monkeypatch.setattr(rx, "enviar_agora", lambda pool, conta: (_ for _ in ()).throw(RuntimeError("x")))
    req = _req()
    pe.painel_equipe_raio_x_enviar(req)
    assert "RuntimeError" in req.session["equipe_erro"]
