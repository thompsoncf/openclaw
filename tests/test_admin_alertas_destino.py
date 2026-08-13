"""Pra ONDE vao os alertas do admin (finance.notificar + finance.config_app).

O destino era so' variavel de ambiente, e a env so' existia no cron de saldos —
alerta disparado pelo web (core.falhas) caia no fallback do remetente SMTP.
Agora o valor mora no banco (configuravel em /admin/comunicacao) e a env virou
fallback. Estes testes travam a PRECEDENCIA: banco → env → remetente SMTP.

Sem banco de verdade: um pool falso responde as consultas de app_config.
"""
import finance.config_app as cfg
import finance.notificar as notificar


class _PoolFalso:
    """Pool minimo que devolve o que app_config 'teria' pra cada chave."""

    def __init__(self, valores: dict | None = None, quebrado: bool = False):
        self.valores = valores or {}
        self.quebrado = quebrado

    def connection(self):
        if self.quebrado:
            raise RuntimeError("banco fora do ar")
        pool = self

        class _Conn:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                chave = params[0]
                valor = pool.valores.get(chave)

                class _Cur:
                    def fetchone(self__):
                        return (valor,) if valor is not None else None

                return _Cur()

        return _Conn()


# --- config_app: banco ganha do env -----------------------------------------

def test_email_do_banco_ganha_do_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "env@exemplo.com")
    pool = _PoolFalso({cfg.ADMIN_EMAIL: "painel@exemplo.com"})
    assert cfg.admin_email(pool) == "painel@exemplo.com"


def test_email_cai_no_env_sem_valor_no_banco(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "env@exemplo.com")
    assert cfg.admin_email(_PoolFalso()) == "env@exemplo.com"
    # valor em branco no banco = "limpo": tambem devolve o env
    assert cfg.admin_email(_PoolFalso({cfg.ADMIN_EMAIL: "  "})) == "env@exemplo.com"


def test_sem_banco_e_sem_env_nao_tem_destino(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    assert cfg.admin_email(_PoolFalso()) is None


def test_telegram_segue_a_mesma_precedencia(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "111")
    assert cfg.admin_telegram_id(_PoolFalso({cfg.ADMIN_TELEGRAM_ID: "222"})) == "222"
    assert cfg.admin_telegram_id(_PoolFalso()) == "111"


# --- notificar: resolucao do destino, tolerante a banco fora ----------------

def _fixar_pool(monkeypatch, pool):
    import db.conexao
    monkeypatch.setattr(db.conexao, "get_pool", lambda: pool)


def test_destino_email_usa_o_valor_do_painel(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "env@exemplo.com")
    _fixar_pool(monkeypatch, _PoolFalso({cfg.ADMIN_EMAIL: "painel@exemplo.com"}))
    assert notificar.destino_email_admin() == "painel@exemplo.com"


def test_destino_email_cai_no_env_com_banco_fora(monkeypatch):
    # banco fora do ar NAO pode engolir o alerta: o env ainda vale
    monkeypatch.setenv("ADMIN_EMAIL", "env@exemplo.com")
    _fixar_pool(monkeypatch, _PoolFalso(quebrado=True))
    assert notificar.destino_email_admin() == "env@exemplo.com"


def test_destino_email_fallback_remetente_smtp(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("SMTP_USER", "contato@zaq-ia.com")
    _fixar_pool(monkeypatch, _PoolFalso())
    assert notificar.destino_email_admin() == "contato@zaq-ia.com"


def test_destino_email_none_sem_nada(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    _fixar_pool(monkeypatch, _PoolFalso())
    assert notificar.destino_email_admin() is None


def test_destino_telegram_vira_int_e_ignora_lixo(monkeypatch):
    monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
    _fixar_pool(monkeypatch, _PoolFalso({cfg.ADMIN_TELEGRAM_ID: "12345"}))
    assert notificar.destino_telegram_admin() == 12345
    _fixar_pool(monkeypatch, _PoolFalso({cfg.ADMIN_TELEGRAM_ID: "abc"}))
    assert notificar.destino_telegram_admin() is None


def test_avisar_admin_manda_pro_destino_do_painel(monkeypatch):
    """Fecha o ciclo: quem chama avisar_admin nao sabe de env nem de banco —
    o e-mail tem que sair pro endereco configurado no /admin."""
    monkeypatch.setenv("ADMIN_EMAIL", "env@exemplo.com")
    _fixar_pool(monkeypatch, _PoolFalso({cfg.ADMIN_EMAIL: "painel@exemplo.com"}))
    enviados = []
    import finance.email_sender as es
    monkeypatch.setattr(es, "enviar_aviso",
                        lambda destino, assunto, html: enviados.append(destino) or True)
    monkeypatch.setattr(notificar, "notificar_admin", lambda texto: False)
    assert notificar.avisar_admin("assunto", "corpo") is True
    assert enviados == ["painel@exemplo.com"]
