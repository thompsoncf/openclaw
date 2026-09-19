"""A régua do antes/depois do Cockpit: consultas, conexões e tempo por tela.

Sem ela, "o app ficou mais rápido" seria impressão. Com ela, o log diz por tela
quantas consultas foram e quanto custaram (ver db/medicao.py).
"""
import psycopg
import pytest

from db import medicao


def test_fora_de_requisicao_nao_mede_nada():
    """Poller, scripts e testes rodam sem MEDIDA: contar é um `if` e nada mais."""
    assert medicao.MEDIDA.get() is None
    medicao.contar_conexao()          # não explode sem medida aberta
    assert medicao.MEDIDA.get() is None


def test_abre_soma_e_fecha():
    tk = medicao.abrir()
    medicao.contar_conexao()
    medicao.contar_conexao()
    m = medicao.fechar(tk)
    assert m["conexoes"] == 2 and m["consultas"] == 0
    assert medicao.MEDIDA.get() is None, "fechar devolve o contexto ao que era"


def test_a_conexao_conta_as_proprias_consultas(monkeypatch):
    """A conexão do pool soma cada `execute` — inclusive o que falha, que também
    custou a ida ao banco."""
    chamadas = []

    def falso(self, *a, **k):
        chamadas.append(a[0])
        if a[0] == "quebra":
            raise RuntimeError("erro do banco")
        return "ok"
    monkeypatch.setattr(psycopg.Connection, "execute", falso)
    c = medicao.ConexaoMedida.__new__(medicao.ConexaoMedida)

    assert c.execute("select 1") == "ok"          # sem medida: passa direto
    tk = medicao.abrir()
    assert c.execute("select 2") == "ok"
    with pytest.raises(RuntimeError):
        c.execute("quebra")
    m = medicao.fechar(tk)
    assert chamadas == ["select 1", "select 2", "quebra"]
    assert m["consultas"] == 2
    assert m["ms"] >= 0


def test_o_dict_e_o_mesmo_dentro_da_thread():
    """As rotas são `def` e rodam numa thread com CÓPIA do contexto. Um `.set()`
    lá dentro morreria na cópia; mexer no dict, não — é o que faz a conta chegar
    ao middleware."""
    import contextvars
    import threading
    tk = medicao.abrir()
    ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(medicao.contar_conexao))
    t.start(); t.join()
    assert medicao.fechar(tk)["conexoes"] == 1


def test_o_pool_usa_a_conexao_medida():
    from db import conexao
    import inspect
    fonte = inspect.getsource(conexao.get_pool)
    assert "connection_class=ConexaoMedida" in fonte
    assert "_m.contar_conexao()" in inspect.getsource(conexao._PoolComConta.getconn)


def test_o_middleware_mede_so_as_telas_do_app():
    """Arquivo fixo (css, sw, ícone) não toca no banco e só encheria o log."""
    import inspect
    from web import app as web_app
    fonte = inspect.getsource(web_app._mede_cockpit)
    assert 'p.startswith("/cockpit")' in fonte
    assert "Server-Timing" in fonte
    assert '"/cockpit/{id}"' not in fonte and r'"/\d+", "/{id}"' in fonte
    for fixo in ("/cockpit/app.css", "/cockpit/sw.js", "/cockpit/splash/"):
        assert fixo in web_app._COCKPIT_ESTATICO
