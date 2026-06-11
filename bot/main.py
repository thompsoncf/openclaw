"""
Bot Telegram com Anthropic para gerenciar gastos
"""
import os
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from anthropic import Anthropic
from financeiro.transacao import Livro
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('TELEGRAM_TOKEN')
client = Anthropic()

# Histórico de conversa por usuário (memória de contexto)
user_contexts = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    await update.message.reply_text(
        "🤖 Olá! Sou o OpenClaw, seu assistente financeiro.\n\n"
        "Diga-me seus gastos e dúvidas sobre finanças:\n"
        "• 'gastei 50 no mercado'\n"
        "• 'qual meu saldo?'\n"
        "• 'últimos gastos'"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa mensagens do usuário"""
    user_id = str(update.effective_user.id)
    user_message = update.message.text

    # Inicializa contexto se novo usuário
    if user_id not in user_contexts:
        user_contexts[user_id] = []

    # Adiciona mensagem ao histórico
    user_contexts[user_id].append({
        "role": "user",
        "content": user_message
    })

    # Chama Anthropic com contexto
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system="""Você é um assistente financeiro chamado OpenClaw.
Ajude o usuário a registrar gastos e entender suas finanças.

Quando detectar um gasto (ex: "gastei X em Y"), extraia:
- valor (número)
- categoria (compras, alimentação, transporte, etc)
- descrição (opcional)

Responda sempre em português, de forma amigável e útil.""",
            messages=user_contexts[user_id]
        )

        assistant_message = response.content[0].text

        # Tenta extrair gasto da resposta (padrão: "GASTO: valor categoria descrição")
        gasto_match = re.search(r'GASTO:\s*([\d.]+)\s+(\w+)\s*(.*)', assistant_message)
        if gasto_match:
            livro = Livro(user_id)
            valor = float(gasto_match.group(1))
            categoria = gasto_match.group(2)
            descricao = gasto_match.group(3).strip()

            livro.registrar_gasto(valor, categoria, descricao)
            saldo = livro.saldo_total()

            resposta_final = (
                f"✅ Registrado: R$ {valor:.2f} em {categoria}\n"
                f"💰 Seu saldo total de gastos: R$ {saldo:.2f}"
            )
        else:
            resposta_final = assistant_message

        # Adiciona resposta ao histórico
        user_contexts[user_id].append({
            "role": "assistant",
            "content": resposta_final
        })

        # Mantém apenas últimas 10 mensagens (economia de tokens)
        if len(user_contexts[user_id]) > 20:
            user_contexts[user_id] = user_contexts[user_id][-20:]

        await update.message.reply_text(resposta_final)

    except Exception as e:
        await update.message.reply_text(f"❌ Erro: {str(e)}")

def main():
    """Inicia o bot"""
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Bot iniciado. Aguardando mensagens...")
    app.run_polling()

if __name__ == '__main__':
    main()
