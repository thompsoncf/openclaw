"""
Testes básicos (aponta SEMPRE pra banco de TESTE)
"""
import sys
import os

# Adiciona raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from financeiro.transacao import Livro

def test_registrar_gasto():
    """Testa registro de gasto"""
    livro = Livro(usuario_id="teste_001")

    resultado = livro.registrar_gasto(
        valor=50.00,
        categoria="alimentacao",
        descricao="supermercado"
    )

    assert resultado['valor'] == 50.00
    assert resultado['categoria'] == "alimentacao"
    print("✅ test_registrar_gasto passou")

def test_saldo():
    """Testa cálculo de saldo"""
    livro = Livro(usuario_id="teste_002")

    livro.registrar_gasto(100.0, "compras")
    livro.registrar_gasto(50.0, "alimentacao")

    saldo = livro.saldo_total()
    assert saldo == 150.0, f"Esperado 150.0, obtive {saldo}"
    print("✅ test_saldo passou")

def test_ultimas_transacoes():
    """Testa listagem"""
    livro = Livro(usuario_id="teste_003")

    livro.registrar_gasto(10.0, "cafe")
    livro.registrar_gasto(20.0, "almoço")
    livro.registrar_gasto(15.0, "cafe")

    transacoes = livro.ultimas_transacoes(limite=2)
    assert len(transacoes) == 2
    print("✅ test_ultimas_transacoes passou")

if __name__ == '__main__':
    test_registrar_gasto()
    test_saldo()
    test_ultimas_transacoes()
    print("\n🎉 Todos os testes passaram!")
