"""
Web API Flask (Fase 2+)
"""
from flask import Flask, jsonify, request
from flask_cors import CORS
from financeiro.transacao import Livro
import os

app = Flask(__name__)
CORS(app)

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({'status': 'ok'}), 200

@app.route('/saldo/<user_id>', methods=['GET'])
def get_saldo(user_id):
    """Retorna saldo do usuário"""
    try:
        livro = Livro(user_id)
        saldo = livro.saldo_total()
        return jsonify({'usuario_id': user_id, 'saldo': saldo}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/gasto/<user_id>', methods=['POST'])
def registrar_gasto(user_id):
    """Registra um novo gasto"""
    try:
        dados = request.json
        livro = Livro(user_id)
        resultado = livro.registrar_gasto(
            valor=float(dados['valor']),
            categoria=dados['categoria'],
            descricao=dados.get('descricao', '')
        )
        return jsonify(resultado), 201
    except Exception as e:
        return jsonify({'erro': str(e)}), 400

@app.route('/transacoes/<user_id>', methods=['GET'])
def listar_transacoes(user_id):
    """Lista últimas transações"""
    try:
        livro = Livro(user_id)
        transacoes = livro.ultimas_transacoes(limite=10)
        return jsonify({'transacoes': transacoes}), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
