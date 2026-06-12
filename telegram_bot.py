"""Interface do piloto: bot do Telegram.

Cada pessoa que da' /start vira um usuario (pelo telegram_id). Mensagens de
texto, FOTOS de cupom/nota e AUDIO sao entregues ao agente financeiro.
Roda assim (depois de configurar o .env):
    python telegram_bot.py
"""
import asyncio
import base64
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

from db.conexao import get_pool, init_schema
from usuarios import usuarios as u
from core.brain import Brain
from core.memory import MemoriaConversa
from finance.livro_caixa import LivroCaixa
from finance.agente_financeiro import criar_agente_financeiro
from core.transcribe import transcritor_se_configurado

logging.basicConfig(level=logging.INFO)
load_dotenv()

_pool = None
_brain: Brain | None = None
_transcritor = None                # transcritor de voz (None se STT nao configurado)
_agentes: dict[int, object] = {}   # usuario_id -> Agente (memoria por sessao)


def _agente_do(usuario):
    ag = _agentes.get(usuario.id)
    if ag is None:
        livro = LivroCaixa(_pool, usuario.id)
        ag = criar_agente_financeiro(_brain, livro, MemoriaConversa())
        _agentes[usuario.id] = ag
    return ag


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u.get_or_create(_pool, telegram_id=user.id, nome=user.first_name)
    await update.message.reply_text(
        "Opa! Sou seu assistente financeiro. Me diga seus gastos e receitas "
        "(ex: \"gastei 50 no mercado\"), me mande a FOTO de um cupom, ou fale "
        "por AUDIO. Pode pedir \"qual meu saldo?\" tambem. Bora?"
    )


async def _processar(update: Update, texto: str, imagem_b64: str | None = None):
    user = update.effective_user
    usuario = u.get_or_create(_pool, telegram_id=user.id, nome=user.first_name)
    if not u.acesso_liberado(usuario):
        await update.message.reply_text(
            "Seu acesso esta suspenso (pagamento pendente ou plano vencido). "
            "Assim que o pagamento for confirmado, voce volta a usar na hora. "
            "Qualquer duvida, e' so' chamar!"
        )
        return
    ok, restante = u.checar_e_registrar_uso(_pool, usuario)
    if not ok:
        await update.message.reply_text(
            "Voce atingiu o limite de mensagens de hoje. A gente se fala amanha!"
        )
        return
    agente = _agente_do(usuario)
    # O agente e' sincrono (rede + LLM): roda fora do event loop pra nao travar o bot.
    resposta = await asyncio.to_thread(agente.responder, texto, imagem_b64)
    await update.message.reply_text(resposta or "(sem resposta)")


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _processar(update, update.message.text or "")


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    foto = update.message.photo[-1]            # maior resolucao disponivel
    arq = await ctx.bot.get_file(foto.file_id)
    dados = await arq.download_as_bytearray()
    b64 = base64.b64encode(bytes(dados)).decode("ascii")
    legenda = update.message.caption or "Segue o cupom para registrar."
    await _processar(update, legenda, imagem_b64=b64)


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _transcritor is None:
        await update.message.reply_text(
            "Ainda nao estou ouvindo audio (transcricao nao configurada). "
            "Pode digitar ou mandar foto do cupom!"
        )
        return
    voz = update.message.voice or update.message.audio
    arq = await ctx.bot.get_file(voz.file_id)
    dados = await arq.download_as_bytearray()
    try:
        texto = await asyncio.to_thread(_transcritor.transcrever, bytes(dados))
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"Nao consegui entender o audio: {e}")
        return
    if not texto:
        await update.message.reply_text("Nao consegui entender o audio. Tenta de novo?")
        return
    # Mostra o que ouviu (pra voce conferir) e ja' processa como se fosse texto.
    await update.message.reply_text(f"🎤 Entendi: \"{texto}\"")
    await _processar(update, texto)


def main():
    global _pool, _brain, _transcritor
    _pool = get_pool()
    init_schema(_pool)
    _brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
    _transcritor = transcritor_se_configurado()
    if _transcritor:
        logging.info("Transcricao de voz: ATIVA")
    else:
        logging.info("Transcricao de voz: desligada (sem STT_API_KEY)")

    token = os.environ["TELEGRAM_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logging.info("OpenClaw no ar. Aguardando mensagens...")
    app.run_polling()


if __name__ == "__main__":
    main()
