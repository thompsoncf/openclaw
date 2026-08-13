"""Regressão: "esqueci minha senha" não pode dizer "enviamos" quando o envio de
e-mail nem está configurado.

Aconteceu em produção: uma cliente pediu o link de recuperação, o token foi
gravado no banco certinho, a tela disse "✅ enviamos um link" — e o e-mail nunca
saiu, porque o SMTP não estava configurado. Ela ficou esperando, e ninguém
descobriu que estava quebrado (o `enviar_email` engole a falha e devolve False,
e o endpoint jogava o envio num BackgroundTask sem olhar o retorno).

Duas coisas precisam continuar valendo ao mesmo tempo:
  • sem SMTP  -> a tela avisa honestamente, em vez de prometer um e-mail;
  • COM SMTP  -> segue sem revelar se a conta existe (anti-enumeração), que é
    o motivo de a mensagem de sucesso ser condicional ("se esse e-mail tem
    conta..."). O aviso novo é um fato do SISTEMA, então não vaza nada.
"""
import web.portal as portal


class _FakeBackground:
    """Substitui o BackgroundTasks do FastAPI pra ver o que seria enviado."""

    def __init__(self):
        self.tarefas = []

    def add_task(self, fn, *args, **kwargs):
        self.tarefas.append((fn, args, kwargs))


class _FakeRequest:
    """O mínimo que _render() toca (session/url)."""

    def __init__(self):
        self.session = {}
        self.state = type("S", (), {})()

    class url:  # noqa: N801 - imita request.url.path
        path = "/esqueci-senha"

    headers: dict = {}


def _chamar(monkeypatch, *, smtp_configurado: bool):
    """Chama o endpoint com o SMTP ligado/desligado, sem tocar em banco quando
    não precisa. Devolve (html, tarefas_de_envio)."""
    monkeypatch.setattr(portal, "_render",
                        lambda nome, req, **ctx: ctx, raising=True)
    import finance.email_sender as es
    monkeypatch.setattr(es, "remetente_configurado",
                        lambda: ("contato@zaq-ia.com" if smtp_configurado else None),
                        raising=True)
    bg = _FakeBackground()
    ctx = portal.esqueci_senha_envia(_FakeRequest(), bg, email="alguem@exemplo.com")
    return ctx, bg.tarefas


def test_sem_smtp_avisa_em_vez_de_prometer_email(monkeypatch):
    ctx, tarefas = _chamar(monkeypatch, smtp_configurado=False)

    assert ctx["enviado"] is False, "não pode exibir a tela de 'enviamos o link'"
    assert ctx["erro"], "precisa dizer ao cliente que o envio não saiu"
    assert not tarefas, "não faz sentido enfileirar envio sem SMTP configurado"


def test_sem_smtp_nao_revela_se_a_conta_existe(monkeypatch):
    """O aviso é sobre o sistema, não sobre a conta — não pode virar um oráculo
    de 'esse e-mail está cadastrado?'."""
    ctx, _ = _chamar(monkeypatch, smtp_configurado=False)

    texto = str(ctx.get("erro", "")).lower()
    for vazamento in ("não encontrada", "nao encontrada", "não existe", "nao existe",
                      "não cadastrado", "nao cadastrado", "encontramos"):
        assert vazamento not in texto, f"a mensagem sugere se a conta existe: {texto!r}"
