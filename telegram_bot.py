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
from contas.contas import URL_CADASTRO
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
    "Ola! Ainda nao encontrei seu cadastro. Cria sua conta e ganha 7 dias grátis "
    "pra usar tudo: " + URL_CADASTRO
)
MSG_SEM_ACESSO = (
    "Seu acesso esta suspenso (pagamento pendente ou plano vencido). "
    "Assim que o pagamento for confirmado, voce volta a usar na hora."
)

# dica curta anexada quando o cliente manda FOTO de cupom (nao em PDF/arquivo):
# ajuda o cliente a enquadrar o QR, melhorando nossa coleta de dados.
DICA_QR = ("\n\n📸 _Dica: deixe o QR code do cupom bem visível na foto — "
           "assim eu identifico a nota certinho e melhoro os preços pra você._")

# dica quando o QR nao foi lido, mas a visao conseguiu ler os itens (cupom foi salvo)
DICA_QR_FALHOU = ("\n\n📸 Consegui registrar os itens do cupom, mas não consegui ler o QR code "
                  "(a foto ficou um pouco escura/dobrada). Pra eu ler o QR direitinho e evitar "
                  "duplicar, da próxima vez tenta:\n"
                  "• Boa luz, sem sombra no cupom\n"
                  "• O cupom esticado (sem dobras), de frente\n"
                  "• O QR code inteiro e bem focado\n\n"
                  "_Mas relaxa — os itens dessa compra já foram salvos! 👍_")

import re as _re
def _parece_convite(texto: str) -> bool:
    """Formato do codigo: 2-4 letras/numeros + hifen + 4 hex (ex: LAR-7K2M)."""
    return bool(_re.fullmatch(r"[A-Z0-9]{2,4}-[A-F0-9]{4}", (texto or "").strip().upper()))


def _extrair_convite(texto: str) -> str | None:
    """Acha o codigo mesmo dentro de uma frase (ex: 'meu codigo e LAR-7K2M')."""
    m = _re.search(r"\b([A-Z0-9]{2,4}-[A-F0-9]{4})\b", (texto or "").upper())
    return m.group(1) if m else None


def _degustar_tg(texto: str) -> str:
    from core.brain import Brain
    from finance.degustacao import responder_degustacao
    return responder_degustacao(_pool, _brain or Brain(), texto)


def _saudacao_lead(restantes: int) -> str:
    return (
        "Opa! 👋 Eu sou o Zaq, seu assistente financeiro aqui no Telegram. "
        "Quer ver como funciona? Me diz um gasto, tipo *almoço 35* ou *uber 22*, "
        "que eu organizo pra você na hora.\n\n"
        f"_(Você tem {restantes} testes grátis. Depois é só criar sua conta em "
        "https://app.zaq-ia.com/cadastro e ganhar 7 dias pra usar tudo, incluindo leitura de cupom.)_"
    )


_pool = None
_brain: Brain | None = None
_transcritor = None

# Detector de rajada (Camada 0): contador de mídia por usuário em janela ~30s
# Se mesmo usuário manda 2+ mídias rápido, avisa UMA VEZ na 2ª
_rajada_midia: dict[int, list[float]] = {}  # user_id -> lista de timestamps (segundos)
_JANELA_RAJADA_SEG = 30


def _processar_rajada_midia(user_id: int) -> bool:
    """Retorna True se é a 2ª+ mídia na janela (deve avisar 'Pode mandar todos')."""
    import time
    agora = time.time()
    if user_id not in _rajada_midia:
        _rajada_midia[user_id] = [agora]
        return False
    # limpa o histórico: mantem apenas eventos dentro da janela
    _rajada_midia[user_id] = [t for t in _rajada_midia[user_id]
                              if agora - t < _JANELA_RAJADA_SEG]
    eh_rajada = len(_rajada_midia[user_id]) >= 1  # ja tem 1+ evento: isso é a 2ª
    _rajada_midia[user_id].append(agora)
    return eh_rajada


def _agente_do(membro, conta):
    livro = LivroCaixa(_pool, conta.id, membro.id)
    lista = ListaCompras(_pool, conta.id, membro.id)
    banco = BancoPrecos(_pool)
    memoria = MemoriaPersistente(_pool, f"tg:{membro.id}")
    cidade = getattr(conta, 'cidade', None)
    ag = criar_agente_financeiro(_brain, livro, memoria, lista, membro.papel, banco, cidade, pool=_pool, conta_id=conta.id, empresa_nome=getattr(conta, "nome", ""))
    ag.canal_atual = "telegram"
    return ag


async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    achado = ct.membro_por_telegram(_pool, update.effective_user.id)
    # deep-link t.me/bot?start=CODIGO -> vincula sem digitar nada
    payload = (_ctx.args[0].strip().upper() if getattr(_ctx, "args", None) else "")
    if achado is None and _parece_convite(payload):
        ok, msg = ct.resgatar_convite(_pool, payload, update.effective_user.id)
        await update.message.reply_text(
            (msg + "Manda um 'oi' que eu te ajudo! 👋") if ok else
            (msg + "\n\nSe voce recebeu um codigo de convite, confira e tente de novo."))
        return
    if achado is None:
        est = ct.lead_estado(_pool, "telegram", str(update.effective_user.id))
        msg = (_saudacao_lead(est["restantes"]) if est["pode_testar"]
               else ("Você já usou seus testes grátis 😊 Curtiu? Cria sua conta e "
                     "ganha 7 dias grátis pra usar tudo (incluindo cupom): https://app.zaq-ia.com/cadastro"))
        await update.message.reply_text(msg, parse_mode="Markdown")
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
    if achado is not None and _parece_convite(texto or ""):
        # membro JA conectado mandou so' o codigo: confirma em vez de ir pra IA
        await update.message.reply_text("Voce ja esta conectado! 👋 Manda um 'oi' que eu te ajudo 😊")
        return
    if achado is None:
        # talvez seja um CODIGO DE CONVITE (ex: "LAR-7K2M"), mesmo dentro de frase
        possivel = _extrair_convite(texto)
        if possivel:
            ok, msg = ct.resgatar_convite(_pool, possivel, update.effective_user.id)
            await update.message.reply_text(
                (msg + "Manda um 'oi' que eu te ajudo! 👋") if ok else
                (msg + "\n\nSe voce recebeu um codigo de convite, confira e tente de novo."))
            return
        # --- LEAD (cliente novo em test-drive) ---
        # cupom/foto é só pós-cadastro (protege custo ~5x)
        if imagem_b64:
            await update.message.reply_text(
                "📸 O leitor de cupom é exclusivo pra quem tem conta! "
                "Cria a sua grátis e ganha 7 dias pra testar tudo: https://app.zaq-ia.com/cadastro")
            return
        est = ct.lead_estado(_pool, "telegram", str(update.effective_user.id))
        if not est["pode_testar"]:
            await update.message.reply_text(
                "Você já testou seus gastos grátis 😊 Curtiu? "
                "Cria sua conta e ganha 7 dias grátis pra usar tudo "
                "(incluindo leitura de cupom): https://app.zaq-ia.com/cadastro")
            return
        # sem texto = saudação inicial
        if not texto:
            await update.message.reply_text(_saudacao_lead(est["restantes"]), parse_mode="Markdown")
            return
        # processa o gasto de teste DE VERDADE
        resp = _degustar_tg(texto)
        novo = ct.lead_registrar_gasto(_pool, "telegram", str(update.effective_user.id))
        extra = ""
        if novo >= ct.LEAD_LIMITE_GASTOS:
            extra = ("\n\n🎉 Curtiu? Esse foi seu último teste grátis. "
                     "Cria sua conta e ganha 7 dias grátis pra usar tudo, "
                     "incluindo a leitura automática de cupom: https://app.zaq-ia.com/cadastro")
        elif novo == ct.LEAD_LIMITE_GASTOS - 1:
            extra = "\n\n_(resta 1 teste grátis — depois é só criar a conta em https://app.zaq-ia.com/cadastro)_"
        await update.message.reply_text(resp + extra)
        return
    membro, conta = achado
    from finance import config_app as _cfg
    if not ct.acesso_liberado(conta, _cfg.beta_gratis_ativo(_pool)):
        await update.message.reply_text(MSG_SEM_ACESSO)
        return
    # check para comandos de pagamento
    cmd = (texto or "").strip().lower()
    if cmd in ("assinar", "pagar", "upgrade", "plano", "assinar plano"):
        try:
            from finance.asaas import criar_link_pagamento
            with _pool.connection() as c:
                plano_row = c.execute(
                    "select nome, preco_base_centavos from planos where codigo=%s",
                    (conta.plano,),
                ).fetchone()
            if plano_row:
                valor_reais = plano_row[1] / 100.0
                link_data = criar_link_pagamento(conta_id=conta.id, nome_plano=plano_row[0],
                                                 valor_reais=valor_reais)
                await update.message.reply_text(f"Pra ativar seu plano {plano_row[0]}, é só pagar aqui 👇\n"
                                               f"{link_data['url']}\n\n"
                                               f"Aceita Pix, boleto ou cartão. Assim que cair, eu libero na hora! 🎉")
            else:
                await update.message.reply_text("Erro ao gerar link de pagamento. Tente novamente.")
        except Exception as e:
            logging.error(f"Erro Asaas Telegram: {e}")
            await update.message.reply_text("Erro ao gerar link de pagamento. Tente novamente.")
        return
    # tem_midia=True (foto/PDF) custa 5x; tipo (cupom vs comprovante) é decidido pelo agente
    tem_midia = bool(imagem_b64)

    # Detector de rajada (Camada 0): se 2ª+ mídia rápido, avisa (UMA VEZ)
    if tem_midia and _processar_rajada_midia(update.effective_user.id):
        await update.message.reply_text("Pode mandar todos! 👍 Vou registrando um a um e te confirmo cada um.")

    ok, _restante = ct.checar_e_registrar_uso(_pool, conta, tem_midia=tem_midia)
    if not ok:
        await update.message.reply_text(
            "Voce atingiu o limite de CUPONS de hoje 📷. Pode continuar mandando texto normalmente!"
            if tem_midia else
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
    # Se lemos uma chave (na trava anterior), passa pro livro pra que tools a usem.
    # AGILIDADE: a leitura do QR e' OpenCV pesado - roda FORA do event loop
    # (to_thread), senao trava o bot inteiro enquanto decodifica (agora que um so'
    # servico atende Telegram+WhatsApp+portal, bloquear o loop atrasa todo mundo).
    chave_nfce = None
    if imagem_b64:
        try:
            import base64
            dados = base64.b64decode(imagem_b64)

            def _ler_chave_sync():
                from finance.nfce_qr import ler_chave_da_imagem, ler_chave
                if media_type == "application/pdf":
                    return ler_chave(dados, media_type)
                if media_type.startswith("image/"):
                    return ler_chave_da_imagem(dados)
                return None

            chave_nfce = await asyncio.to_thread(_ler_chave_sync)
            if chave_nfce:
                agente.livro.chave_nfce_atual = chave_nfce
        except Exception:  # noqa: BLE001
            pass
    # AGILIDADE (percepcao): o loop do agente leva alguns segundos (varias chamadas
    # ao modelo). Mostra "digitando..." na hora pra pessoa saber que estamos nessa.
    try:
        from telegram.constants import ChatAction
        await update.effective_chat.send_action(ChatAction.TYPING)
    except Exception:  # noqa: BLE001 - feedback opcional, nunca atrapalha
        pass
    # O agente e' sincrono (rede + LLM): roda fora do event loop pra nao travar o bot.
    try:
        resposta = await asyncio.to_thread(agente.responder, texto, imagem_b64, media_type)
    except Exception as e:  # noqa: BLE001
        # NUNCA vaza o erro tecnico pro cliente (ex: saldo da Anthropic).
        # tratar_falha_ia devolve mensagem amigavel e avisa o admin se for saldo.
        logging.exception("erro no agente (telegram)")
        from core.falhas import tratar_falha_ia
        await update.message.reply_text(tratar_falha_ia(e, canal="telegram"))
        return
    resposta = resposta or "(sem resposta)"
    # Dica de QR SO' quando foi mesmo um CUPOM FISCAL (chave lida OU itens
    # registrados). Comprovante de Pix/banco nao tem QR - a mensagem tem que
    # bater com o que a pessoa enviou.
    from finance.nfce_qr import deve_mandar_dica_qr
    if deve_mandar_dica_qr(dica_qr, chave_nfce, getattr(agente, "_obs_tools", set())):
        resposta += (DICA_QR if chave_nfce else DICA_QR_FALHOU)
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


async def _baixar_arquivo_tg(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             file_id: str) -> bytes | None:
    """Baixa um arquivo do Telegram (get_file + download). Se o Telegram falhar,
    avisa o cliente com mensagem AMIGAVEL (sem vazar erro tecnico), avisa o admin
    se for falha sistemica, e devolve None (o handler deve abortar)."""
    try:
        arq = await ctx.bot.get_file(file_id)
        dados = await arq.download_as_bytearray()
        return bytes(dados)
    except Exception as e:  # noqa: BLE001
        logging.exception("falha ao baixar arquivo do telegram")
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="Telegram (download)", canal="telegram")
        await update.message.reply_text(
            "Nao consegui baixar seu arquivo agora 😕 Tenta mandar de novo daqui a pouco?")
        return None


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    foto = update.message.photo[-1]            # maior resolucao disponivel
    dados = await _baixar_arquivo_tg(update, ctx, foto.file_id)
    if dados is None:
        return
    b64 = base64.b64encode(dados).decode("ascii")
    legenda = update.message.caption or "Segue o cupom para registrar."
    _disparar_qr(update, dados, "image/jpeg")
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
    dados = await _baixar_arquivo_tg(update, ctx, doc.file_id)
    if dados is None:
        return
    b64 = base64.b64encode(dados).decode("ascii")
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
    dados = await _baixar_arquivo_tg(update, ctx, voz.file_id)
    if dados is None:
        return
    # Dica de vocabulário: se for membro, passa os nomes da agenda dele (títulos +
    # convidados) pra voz acertar 'Mailson', 'Smart Center' etc.
    _vocab = ""
    try:
        _achado = ct.membro_por_telegram(_pool, update.effective_user.id)
        if _achado:
            from finance.agenda import vocabulario_stt
            _vocab = vocabulario_stt(_pool, _achado[1].id)
    except Exception:  # noqa: BLE001
        _vocab = ""
    try:
        texto = await asyncio.to_thread(_transcritor.transcrever, dados, vocab=_vocab)
    except Exception:  # noqa: BLE001 - NUNCA vaza o erro do STT pro cliente
        # o alerta ao admin (se sistemico) ja' sai de dentro do transcrever()
        logging.exception("falha ao transcrever audio (telegram)")
        await update.message.reply_text(
            "Recebi seu audio, mas nao consegui entender dessa vez. 🎧 "
            "Pode repetir com mais calma ou me mandar por texto?")
        return
    if not texto:
        await update.message.reply_text("Nao consegui entender o audio. Tenta de novo?")
        return
    await update.message.reply_text(f"🎤 Entendi: \"{texto}\"")
    await _processar(update, texto)


def preparar_globais(pool, brain, transcritor) -> None:
    """Compartilha pool/brain/transcritor com os handlers deste modulo.

    Os handlers (on_text, on_photo...) leem esses globais. Quem monta a
    Application - o polling (main) OU o webhook servido pelo web/app.py - chama
    isto primeiro pra os handlers terem o nucleo pronto."""
    global _pool, _brain, _transcritor
    _pool, _brain, _transcritor = pool, brain, transcritor


def registrar_handlers(app: "Application") -> None:
    """Registra os mesmos handlers, seja no polling ou no webhook."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


def construir_application(token: str, pool, brain, transcritor, *,
                          com_updater: bool = True):
    """Monta a Application do Telegram com o nucleo compartilhado.

    com_updater=True  -> modo POLLING (worker dedicado, run_polling).
    com_updater=False -> modo WEBHOOK (updater(None)): o web/app.py alimenta os
    updates via update_queue; nao ha poller. Isso permite um SO' servico
    (web) atender Telegram + WhatsApp + portal, em vez de 2."""
    preparar_globais(pool, brain, transcritor)
    builder = Application.builder().token(token)
    if not com_updater:
        builder = builder.updater(None)      # sem poller: entrega via webhook
    app = builder.build()
    registrar_handlers(app)
    return app


def main():
    pool = get_pool()
    init_schema(pool)
    brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
    transcritor = transcritor_se_configurado()
    logging.info("Transcricao de voz: %s", "ATIVA" if transcritor else "desligada")

    app = construir_application(os.environ["TELEGRAM_TOKEN"], pool, brain,
                                transcritor, com_updater=True)
    logging.info("OpenClaw (Telegram/polling) no ar. Aguardando mensagens...")
    app.run_polling()


if __name__ == "__main__":
    main()
