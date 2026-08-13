"""Trava de segurança do "olhinho" que mostra a senha/token salvos dos canais.

O dono precisa reler o que ele mesmo cadastrou (ex.: reaproveitar a senha de app
do Gmail no SMTP) sem gerar tudo de novo. Só que expor credencial guardada é
justamente o tipo de coisa que, se afrouxar, afrouxa em silêncio — por isso as
regras estão fixadas aqui:

  • só dono/gestor (vendedor não vê senha de canal);
  • sem login, nem responde;
  • o campo vem de uma WHITELIST — o nome da coluna nunca sai do request, senão
    viraria uma porta pra ler qualquer coluna de canais_config (senha_hash de
    outra tabela, etc.);
  • a consulta é sempre escopada no conta_id da SESSÃO, nunca num id que o
    cliente mande (multi-tenant).
"""
import pytest

import web.painel_prospeccao as pp


class _FakeReq:
    def __init__(self):
        self.session = {}
        self.state = type("S", (), {})()


def _resposta(monkeypatch, *, papel, campo, valor_no_banco="SEGREDO", conta_id=7):
    """Chama o endpoint com _acesso e banco fingidos; devolve (status, corpo)."""
    import json as _json

    ctx = {"conta": None, "conta_id": conta_id, "papel": papel,
           "membro_id": None, "gerencia": papel in ("dono", "gestor"),
           "pode_atribuir": papel == "dono"}
    monkeypatch.setattr(pp, "_acesso", lambda req: (ctx, None), raising=True)

    consultas = []

    class _Cur:
        def fetchone(self):
            return (valor_no_banco,) if valor_no_banco is not None else None

    class _Conn:
        def execute(self, sql, params=None):
            consultas.append((sql, params))
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    monkeypatch.setattr(pp, "get_pool", lambda: _Pool(), raising=True)

    resp = pp.comunicacao_revelar_segredo(_FakeReq(), campo=campo)
    corpo = _json.loads(bytes(resp.body).decode())
    return resp.status_code, corpo, consultas


def test_dono_ve_o_segredo(monkeypatch):
    status, corpo, _ = _resposta(monkeypatch, papel="dono", campo="email")
    assert status == 200
    assert corpo == {"ok": True, "valor": "SEGREDO"}


def test_gestor_tambem_ve(monkeypatch):
    _, corpo, _ = _resposta(monkeypatch, papel="gestor", campo="email")
    assert corpo["ok"] is True


@pytest.mark.parametrize("papel", ["vendedor", "membro", "financeiro"])
def test_quem_nao_gerencia_nao_ve(monkeypatch, papel):
    status, corpo, consultas = _resposta(monkeypatch, papel=papel, campo="email")
    assert status == 403
    assert corpo["ok"] is False
    assert not consultas, "nem deve chegar a consultar o banco"


def test_sem_login_nao_responde(monkeypatch):
    from starlette.responses import RedirectResponse
    monkeypatch.setattr(pp, "_acesso",
                        lambda req: (None, RedirectResponse("/login", status_code=303)),
                        raising=True)
    resp = pp.comunicacao_revelar_segredo(_FakeReq(), campo="email")
    assert resp.status_code == 401


@pytest.mark.parametrize("campo", [
    "senha_hash", "token; drop table canais_config", "*", "", "imap_senha", "id",
])
def test_campo_fora_da_whitelist_e_recusado(monkeypatch, campo):
    """'imap_senha' entra aqui de propósito: o request manda o SLUG do canal
    ('email'), nunca o nome da coluna."""
    status, corpo, consultas = _resposta(monkeypatch, papel="dono", campo=campo)
    assert status == 400
    assert corpo["ok"] is False
    assert not consultas


def test_consulta_sempre_escopada_na_conta_da_sessao(monkeypatch):
    _, _, consultas = _resposta(monkeypatch, papel="dono", campo="messenger", conta_id=42)
    assert len(consultas) == 1
    sql, params = consultas[0]
    assert "conta_id=%s" in sql.replace(" ", "")
    assert params[0] == 42, "tem que usar o conta_id da SESSÃO"
    assert params[1] == "messenger"


def test_campo_vazio_no_banco_nao_finge_que_tem(monkeypatch):
    _, corpo, _ = _resposta(monkeypatch, papel="dono", campo="whatsapp", valor_no_banco="")
    assert corpo["ok"] is False
    assert "Nada salvo" in corpo["erro"]


def test_whitelist_cobre_os_campos_da_tela():
    """Se alguém adicionar um olhinho novo na tela, tem que registrar aqui."""
    assert set(pp._SEGREDOS_REVELAVEIS) == {
        "email", "email2", "whatsapp", "messenger", "instagram"}
    for canal, coluna in pp._SEGREDOS_REVELAVEIS.values():
        assert coluna in ("imap_senha", "token"), f"coluna inesperada: {coluna}"
