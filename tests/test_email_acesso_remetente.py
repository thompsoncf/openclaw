"""E-mail de ACESSO sai pelo remetente do Zaq, não pela caixa do cliente.

O QUE ACONTECEU. Em 18/08, na Prime Eventos, um membro pediu o link de acesso pelo
Cockpit: o token nasceu e ficou válido (`cockpit_acesso`), e o e-mail nunca chegou.
Pelo painel, o MESMO pedido chegou na hora — porque a recuperação de senha do portal
manda pelo SMTP do Zaq, sem passar pela caixa do cliente. A caixa daquela empresa
estava com problema (o IMAP dela deu TimeoutError no mesmo dia).

E o plano B não salvava. Ele só entrava quando o envio RECUSAVA; quando o Gmail da
empresa ACEITA e a mensagem morre depois (filtro do destino, spam), a função devolvia
True, a reserva nunca rodava, e a tela dizia "confira seu e-mail". Falha silenciosa
com cara de sucesso — a pior forma de um acesso falhar.

A REGRA que estes testes travam:

  • mensagem pra LEAD sai pela caixa da empresa (a resposta dele tem que cair no
    inbox dela) — isso NÃO muda e não é testado aqui;
  • link de acesso e convite são E-MAIL DE SISTEMA: ninguém responde, só precisam
    chegar. Saem pelo Zaq, com o nome da empresa visível pra quem recebe;
  • a caixa da empresa vira RESERVA, pra quando o Zaq não estiver configurado —
    melhor sair por ela do que não sair.

Sem banco e sem rede: os dois envios são dublês que registram quem foi chamado.
"""
import pytest


class _Pool:
    """Pool mínimo — as funções só leem o nome da empresa."""
    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): return self
        def fetchone(self): return ["Prime Eventos"]
    def connection(self): return self._C()


@pytest.fixture()
def espiao(monkeypatch):
    """Troca os dois remetentes por dublês e devolve o registro das chamadas."""
    from finance import email_sender as es
    from finance import email_inbound as ein
    reg = {"zaq": [], "empresa": [], "zaq_ok": True, "empresa_ok": True,
           "zaq_configurado": True}

    def _zaq(destino, assunto, html, texto_alt=None, reply_to=None,
             from_nome=None, list_unsub=""):
        # sem SMTP configurado, o enviar_email de verdade devolve False sozinho —
        # o dublê imita isso em vez de inventar um gate que o código não tem.
        if not reg["zaq_configurado"]:
            return False
        reg["zaq"].append({"destino": destino, "from_nome": from_nome})
        return reg["zaq_ok"]

    def _empresa(pool, conta_id, destino, assunto, html, texto_alt=None,
                 from_nome=None, reply_to=None, list_unsub="", canal="email"):
        reg["empresa"].append({"destino": destino, "conta_id": conta_id})
        return reg["empresa_ok"]

    monkeypatch.setattr(es, "enviar_email", _zaq)
    monkeypatch.setattr(ein, "enviar_conta", _empresa)
    # o convite pega o pool sozinho pra falar com a caixa da empresa; sem dublar,
    # ele estoura por falta de banco e o teste mediria a exceção, não a ordem.
    from web import painel_equipe as pe
    monkeypatch.setattr(pe, "get_pool", lambda: _Pool())
    return reg


def _link(pool=None):
    from web.painel_cockpit import _enviar_link_email
    return _enviar_link_email(pool or _Pool(), 34, "vendedor@exemplo.com",
                              "https://app.zaq-ia.com/cockpit/entrar/abc")


def _convite():
    from web.painel_equipe import _enviar_email_convite
    return _enviar_email_convite((34, None, "Prime Eventos"), "Fulano",
                                 "vendedor@exemplo.com", "vendedor",
                                 "https://app.zaq-ia.com/convite/xyz")


# --------------------------------------------------- link de acesso (Cockpit)

def test_link_sai_pelo_zaq_e_nao_encosta_na_caixa_da_empresa(espiao):
    """O ponto do conserto: a caixa do cliente deixa de ser a primeira porta."""
    assert _link() is True
    assert len(espiao["zaq"]) == 1
    assert espiao["empresa"] == [], "não pode nem tentar a caixa da empresa"


def test_link_leva_o_nome_da_empresa_no_remetente(espiao):
    """Muda quem CARREGA, não quem ASSINA: o vendedor continua vendo a empresa."""
    _link()
    assert espiao["zaq"][0]["from_nome"] == "Prime Eventos"


def test_se_o_zaq_falhar_a_caixa_da_empresa_entra_de_reserva(espiao):
    espiao["zaq_ok"] = False
    assert _link() is True
    assert len(espiao["zaq"]) == 1 and len(espiao["empresa"]) == 1


def test_sem_smtp_do_zaq_configurado_vai_pela_empresa(espiao):
    """Melhor sair pela caixa do cliente do que não sair."""
    espiao["zaq_configurado"] = False
    assert _link() is True
    assert espiao["zaq"] == [] and len(espiao["empresa"]) == 1


def test_se_ninguem_manda_devolve_false(espiao):
    """Não pode devolver True sem ter enviado: é disso que nasce o
    "confira seu e-mail" pra um e-mail que nunca saiu."""
    espiao["zaq_ok"] = espiao["empresa_ok"] = False
    assert _link() is False


def test_caixa_da_empresa_que_estoura_nao_derruba_a_tela(espiao, monkeypatch):
    from finance import email_inbound as ein
    espiao["zaq_ok"] = False

    def _explode(*a, **k):
        raise RuntimeError("SMTP fora do ar")
    monkeypatch.setattr(ein, "enviar_conta", _explode)
    assert _link() is False          # devolve False, não levanta


# ------------------------------------------------------- convite (Equipe)

def test_convite_tambem_sai_pelo_zaq(espiao):
    """A OUTRA porta da mesma pessoa. Deixar uma pelo Zaq e outra pela caixa do
    cliente faria o acesso funcionar ou não conforme por onde ela entrasse."""
    assert _convite() is True
    assert len(espiao["zaq"]) == 1
    assert espiao["empresa"] == []


def test_convite_cai_na_empresa_quando_o_zaq_nao_manda(espiao):
    espiao["zaq_ok"] = False
    assert _convite() is True
    assert len(espiao["empresa"]) == 1


def test_convite_sem_ninguem_pra_mandar_devolve_false(espiao):
    espiao["zaq_ok"] = espiao["empresa_ok"] = False
    assert _convite() is False
