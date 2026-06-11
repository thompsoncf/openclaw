"""Teste local rapido, sem Telegram.

Simula um usuario e conversa com o agente financeiro no terminal.
Precisa de ANTHROPIC_API_KEY e DATABASE_URL no .env.
Para mandar uma foto: digite  foto: /caminho/do/cupom.jpg
    python cli.py
"""
import base64
import os

from dotenv import load_dotenv

from db.conexao import get_pool, init_schema
from usuarios import usuarios as u
from core.brain import Brain
from core.memory import MemoriaConversa
from finance.livro_caixa import LivroCaixa
from finance.agente_financeiro import criar_agente_financeiro


def main():
    load_dotenv()
    pool = get_pool()
    init_schema(pool)
    usuario = u.get_or_create(pool, telegram_id=999999, nome="Teste local")
    brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
    livro = LivroCaixa(pool, usuario.id)
    agente = criar_agente_financeiro(brain, livro, MemoriaConversa())

    print("Agente financeiro pronto. Ctrl+C pra sair.\n")
    while True:
        try:
            msg = input("voce > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\ntchau!")
            break
        if not msg:
            continue
        imagem_b64 = None
        if msg.lower().startswith("foto:"):
            caminho = msg.split(":", 1)[1].strip()
            with open(caminho, "rb") as f:
                imagem_b64 = base64.b64encode(f.read()).decode("ascii")
            msg = "Segue o cupom para registrar."
        print("agente >", agente.responder(msg, imagem_b64), "\n")


if __name__ == "__main__":
    main()
