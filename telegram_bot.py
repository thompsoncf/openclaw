"""Interface do piloto: bot do Telegram.

Identifica a pessoa pelo telegram_id ENTRE OS MEMBROS cadastrados (o cadastro
acontece no portal; o chat nao cria conta). Texto, FOTO de cupom e AUDIO sao
entregues ao agente financeiro, que trabalha no caixa da CONTA do membro.
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
from contas import contas as ct
from core.brain import Brain
from core.memory import MemoriaConversa
from finance.livro_caixa import LivroCaixa
from finance.agente_financeiro import criar_agente_financeiro
from core.transcribe import transcritor_se_configurado

logging.basicConfig(level=logging.INFO)
load_dotenv()

MSG_NAO_CADASTRADO = (
    "Ola! Ainda nao encontrei seu cadastro. O acesso e' feito pelo portal: "
    "la voce escolhe seu plano e cadastra seu numero. Depois disso, e' so' "
    "voltar aqui e conversar comigo!"
)
MSG_SEM_ACESSO = (
    "Seu acesso esta suspenso (pagamento pendente ou plano vencido). "
    "Assim que o pagamento for confirmado, voce volta a usar na hora."
)

_pool = None
_brain: Brain | None = None
_transcritor = None
_agentes: dict[int, object] = {}   # membro_id -> Agente (memoria por sessao)


def _agente_do(membro, conta):
    ag = _agentes.get(membro.id)
    if ag is None:
        livro = LivroCaixa(_pool, conta.id, membro.id)
        ag = criar_agente_financeiro(_brain, livro, MemoriaConversa())
        _agentes[membro.id] = ag
    return ag


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    achado = ct.membro_por_telegram(_pool, update.effective_user.id)
    if achado is None:
        await update.message.reply_text(MSG_NAO_CADASTRADO)
        return
    membro, _conta = achado
    _agentes.pop(membro.id, None)   # RESET: descarta a sessao/memoria anterior
    nome = membro.nome or update.effective_user.first_name or ""
    await update.message.reply_text(
        f"Opa, {nome}! Conversa reiniciada. Me diga seus gastos e "
        "receitas (ex: \"gastei 50 no mercado\"), me mande a FOTO de um cupom, "
        "ou fale por AUDIO. Pode pedir \"qual meu saldo?\" tambem. Bora?"
    )


async def _processar(update: Update, texto: str, imagem_b64: str | None = None):
    achado = ct.membro_por_telegram(_pool, update.effective_user.id)
    if achado is None:
        await update.message.reply_text(MSG_NAO_CADASTRADO)
        return
    membro, conta = achado
    if not ct.acesso_liberado(conta):
        await update.message.reply_text(MSG_SEM_ACESSO)
        return
    ok, _restante = ct.checar_e_registrar_uso(_pool, conta)
    if not ok:
        await update.message.reply_text(
            "Voce atingiu o limite de mensagens de hoje. A gente se fala amanha!"
        )
        return
    agente = _agente_do(membro, conta)
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
    await update.message.reply_text(f"🎤 Entendi: \"{texto}\"")
    await _processar(update, texto)


def main():
    global _pool, _brain, _transcritor
    _pool = get_pool()
    init_schema(_pool)
    _brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
    _transcritor = transcritor_se_configurado()
    logging.info("Transcricao de voz: %s", "ATIVA" if _transcritor else "desligada")

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
