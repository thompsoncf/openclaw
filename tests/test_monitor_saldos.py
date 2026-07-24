"""Logica pura do monitor proativo de saldo/gasto (scripts.monitor_saldos).

So' a parte testavel sem rede: parsing do Cost Report e os limiares de alerta.
"""
from scripts import monitor_saldos as m


# --- soma do Cost Report (tolerante ao formato) -----------------------------

def test_soma_gasto_formato_esperado():
    # forma tipica: data[].results[] com {currency, amount}
    payload = {
        "data": [
            {"results": [{"currency": "USD", "amount": "1.50"}]},
            {"results": [{"currency": "USD", "amount": "0.25"}]},
        ]
    }
    assert m.somar_gasto_cost_report(payload) == 1.75


def test_soma_ignora_amount_sem_currency():
    # um 'amount' solto (sem 'currency' irmao) NAO deve ser contado
    payload = {"outro": {"amount": "999"}, "results": [{"currency": "USD", "amount": "2"}]}
    assert m.somar_gasto_cost_report(payload) == 2.0


def test_soma_vazio_e_lixo():
    assert m.somar_gasto_cost_report({}) == 0.0
    assert m.somar_gasto_cost_report({"data": []}) == 0.0
    assert m.somar_gasto_cost_report({"results": [{"currency": "USD", "amount": "abc"}]}) == 0.0


# --- limiares de alerta ------------------------------------------------------

def test_alertar_gasto_alto():
    assert m.alertar_gasto_alto(25.0, 20.0) is True
    assert m.alertar_gasto_alto(15.0, 20.0) is False
    assert m.alertar_gasto_alto(20.0, 20.0) is False  # igual ao teto nao alerta
    # sem dado ou sem teto -> nunca alerta
    assert m.alertar_gasto_alto(None, 20.0) is False
    assert m.alertar_gasto_alto(25.0, None) is False


def test_alertar_saldo_baixo():
    assert m.alertar_saldo_baixo(3.0, 5.0) is True
    assert m.alertar_saldo_baixo(5.0, 5.0) is True   # igual ao minimo alerta
    assert m.alertar_saldo_baixo(9.0, 5.0) is False
    # sem dado ou sem minimo -> nunca alerta (ex: consulta falhou)
    assert m.alertar_saldo_baixo(None, 5.0) is False
    assert m.alertar_saldo_baixo(3.0, None) is False


def test_to_float():
    assert m._to_float("1.5") == 1.5
    assert m._to_float(2) == 2.0
    assert m._to_float(None) is None
    assert m._to_float("abc") is None
