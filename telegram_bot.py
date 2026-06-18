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
from core.memory import MemoriaPersistente
from finance.livro_caixa import LivroCaixa
from finance.lista_compras import ListaCompras
from finance.banco_precos import BancoPrecos
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

# dica curta anexada quando o cliente manda FOTO de cupom (nao em PDF/arquivo):
# ajuda o cliente a enquadrar o QR, melhorando nossa coleta de dados.
DICA_QR = ("\n\n📸 _Dica: deixe o QR code do cupom bem visível na foto — "
           "assim eu identifico a nota certinho e melhoro os preços pra você._")

import re as _re
def _parece_convite(texto: str) -> bool:
    """Formato do codigo: 2-4 letras/numeros + hifen + 4 hex (ex: LAR-7K2M)."""
    return bool(_re.fullmatch(r"[A-Z0-9]{2,4}-[A-F0-9]{4}", (texto or "").strip().upper()))




_pool = None
_brain: Brain | None = None
_transcritor = None


def _agente_do(membro, conta):
    livro = LivroCaixa(_pool, conta.id, membro.id)
    lista = ListaCompras(_pool, conta.id, membro.id)
    banco = BancoPrecos(_pool)
    memoria = MemoriaPersistente(_pool, f"tg:{membro.id}")
    cidade = getattr(conta, 'cidade', None)
    return criar_agente_financeiro(_brain, livro, memoria, lista, membro.papel, banco, cidade)


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    achado = ct.membro_por_telegram(_pool, update.effective_user.id)
    if achado is None:
        await update.message.reply_text(MSG_NAO_CADASTRADO)
        return
    membro, _conta = achado
    MemoriaPersistente(_pool, f"tg:{membro.id}").limpar()   # RESET no banco
    nome = membro.nome or update.effective_user.first_name or ""
    await update.message.reply_text(
        f"Opa, {nome}! Conversa reiniciada. Me diga seus gastos e "
        "receitas (ex: \"gastei 50 no mercado\"), me mande a FOTO de um cupom, "
        "ou fale por AUDIO. Pode pedir \"qual meu saldo?\" tambem. Bora?"
    )


async def _processar(update: Update, texto: str, imagem_b64: str | None = None,
                     media_type: str = "image/jpeg", dica_qr: bool = False, eh_cupom: bool | None = None):
    # Idempotencia: se esta mensagem ja' foi processada (reentrega ou multiplas instancias),
    # pula. Faz isso ANTES de qualquer trabalho caro (download, API).
    if not ct.reivindicar_mensagem(_pool, f"tg:{update.update_id}"):
        return  # mensagem repetida: ja' tratada

    achado = ct.membro_por_telegram(_pool, update.effective_user.id)
    if achado is None:
        # talvez seja um CODIGO DE CONVITE (ex: "LAR-7K2M")
        possivel = (texto or "").strip().upper()
        if _parece_convite(possivel):
            ok, msg = ct.resgatar_convite(_pool, possivel, update.effective_user.id)
            await update.message.reply_text(
                (msg + "Manda um 'oi' que eu te ajudo! 😊") if ok else
                (msg + "\n\nSe voce recebeu um codigo de convite, confira e tente de novo."))
            return
        await update.message.reply_text(MSG_NAO_CADASTRADO)
        return
    membro, conta = achado
    if not ct.acesso_liberado(conta):
        await update.message.reply_text(MSG_SEM_ACESSO)
        return
    # Detecta automaticamente se é cupom (há imagem) se não foi especificado
    eh_cupom = eh_cupom if eh_cupom is not None else bool(imagem_b64)
    ok, _restante = ct.checar_e_registrar_uso(_pool, conta, eh_cupom=eh_cupom)
    if not ok:
        await update.message.reply_text(
            "Voce atingiu o limite de CUPONS de hoje 📷. Pode continuar mandando texto normalmente!"
            if eh_cupom else
            "Voce atingiu o limite de mensagens de hoje. A gente se fala amanha!"
        )
        return

    # TRAVA DUPLICIDADE + REPLICA: se temos imagem (cupom/PDF), verifica por chave NFC-e ANTES do agente
    if imagem_b64:
        try:
            import base64
            dados = base64.b64decode(imagem_b64)
            from finance.nfce_qr import ler_chave_da_imagem
            from finance.livro_caixa import LivroCaixa
            chave_nfce = ler_chave_da_imagem(dados) if media_type.startswith("image/") else None
            # PDF: tenta extrair chave via OCR/texto (nfce_qr.ler_chave tenta isso)
            if media_type == "application/pdf":
                try:
                    from finance.nfce_qr import ler_chave
                    chave_nfce = ler_chave(dados, media_type)
                except Exception:  # noqa: BLE001
                    chave_nfce = None
            if chave_nfce:
                liv = LivroCaixa(_pool, conta["id"], membro.id)
                dup = liv.lancamento_por_chave(chave_nfce, global_=True)
                if dup:
                    if dup["conta_id"] == conta["id"]:
                        # mesma conta -> ja' registrado
                        data_str = dup["data"].strftime("%d/%m/%Y")
                        desc = dup["descricao"][:50]
                        await update.message.reply_text(
                            f"✓ Esse cupom ja foi registrado em {data_str} ({desc}...)")
                        return
                    # outra conta -> tenta replicar (zero API)
                    rep = liv.replicar_cupom(chave_nfce)
                    if rep:
                        await update.message.reply_text(
                            f"✓ Esse cupom ja foi lido no sistema. Salvei uma copia na sua "
                            f"conta: {rep['descricao']} - R$ {rep['valor']/100:.2f} "
                            f"({rep['n_itens']} itens). Nao precisei reler a foto.")
                        return
                    # dup existe mas SEM itens (parse falhou) -> cai pro fluxo normal (parseia)
        except Exception:  # noqa: BLE001
            pass  # trava falha gracefully, segue o fluxo normal

    agente = _agente_do(membro, conta)
    # Se lemos uma chave (na trava anterior), passa pro livro pra que tools a usem
    chave_nfce = None
    if imagem_b64:
        try:
            import base64
            dados = base64.b64decode(imagem_b64)
            from finance.nfce_qr import ler_chave_da_imagem
            chave_nfce = ler_chave_da_imagem(dados) if media_type.startswith("image/") else None
            if media_type == "application/pdf":
                try:
                    from finance.nfce_qr import ler_chave
                    chave_nfce = ler_chave(dados, media_type)
                except Exception:  # noqa: BLE001
                    chave_nfce = None
            if chave_nfce:
                agente.livro.chave_nfce_atual = chave_nfce
        except Exception:  # noqa: BLE001
            pass
    # O agente e' sincrono (rede + LLM): roda fora do event loop pra nao travar o bot.
    resposta = await asyncio.to_thread(agente.responder, texto, imagem_b64, media_type)
    resposta = resposta or "(sem resposta)"
    if dica_qr:
        resposta += DICA_QR
    await update.message.reply_text(resposta)


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _processar(update, update.message.text or "")


def _ler_qr_e_auditar(telegram_id: int, dados: bytes, media_type: str) -> None:
    """Roda POR TRAS (thread separada): le o QR com calma (todas as tecnicas) e
    registra a auditoria pra enriquecer nosso banco de dados. NUNCA bloqueia o
    fluxo do cliente - o cupom ja' foi processado normalmente. Tolerante a tudo."""
    chave = None
    import logging
    _log = logging.getLogger("openclaw.qr")
    try:
        from finance.nfce_qr import ler_chave
        chave = ler_chave(dados, media_type)
        _log.info("QR leitura: chave=%s media=%s bytes=%d",
                  chave or "NAO_LEU", media_type, len(dados))
    except Exception as e:  # noqa: BLE001
        _log.exception("QR ERRO na leitura: %s", e)
        chave = None
    try:
        from finance.nfce_qr import registrar_leitura, medir_imagem
        import contas.contas as ct
        achado = ct.membro_por_telegram(get_pool(), telegram_id)
        conta_id = achado[1].id if achado else None
        info = medir_imagem(dados, media_type)
        registrar_leitura(get_pool(), conta_id, chave, media_type, info)
    except Exception as e:  # noqa: BLE001
        _log.exception("QR ERRO na auditoria: %s", e)


def _disparar_qr(update: Update, dados: bytes, media_type: str) -> None:
    """Dispara a leitura do QR em background (nao espera o resultado)."""
    try:
        tid = update.effective_user.id
        asyncio.get_event_loop().run_in_executor(
            None, _ler_qr_e_auditar, tid, dados, media_type)
    except Exception:  # noqa: BLE001
        pass


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    foto = update.message.photo[-1]            # maior resolucao disponivel
    arq = await ctx.bot.get_file(foto.file_id)
    dados = await arq.download_as_bytearray()
    b64 = base64.b64encode(bytes(dados)).decode("ascii")
    legenda = update.message.caption or "Segue o cupom para registrar."
    _disparar_qr(update, bytes(dados), "image/jpeg")
    await _processar(update, legenda, imagem_b64=b64, dica_qr=True)


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Recebe PDF (comprovante de banco) ou imagem enviada como arquivo."""
    doc = update.message.document
    nome = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    legenda = update.message.caption or "Segue o comprovante para registrar."
    if doc.file_size and doc.file_size > 18 * 1024 * 1024:
        await update.message.reply_text("Esse arquivo e' muito grande. Pode mandar uma foto do comprovante? 📸")
        return
    arq = await ctx.bot.get_file(doc.file_id)
    dados = await arq.download_as_bytearray()
    b64 = base64.b64encode(bytes(dados)).decode("ascii")
    if "pdf" in mime or nome.endswith(".pdf"):
        _disparar_qr(update, bytes(dados), "application/pdf")
        await _processar(update, legenda, imagem_b64=b64, media_type="application/pdf")
    elif mime.startswith("image/") or nome.endswith((".jpg", ".jpeg", ".png", ".webp")):
        mt = mime if mime.startswith("image/") else "image/jpeg"
        _disparar_qr(update, bytes(dados), mt)
        await _processar(update, legenda, imagem_b64=b64, media_type=mt)
    else:
        await update.message.reply_text(
            "Recebi o arquivo, mas só consigo ler PDF ou imagem. "
            "Pode mandar o comprovante em PDF ou foto? 📄📸")


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
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logging.info("OpenClaw no ar. Aguardando mensagens...")
    app.run_polling()


if __name__ == "__main__":
    main()
