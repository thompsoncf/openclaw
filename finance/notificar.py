"""Notificacoes ativas pro dono da conta (ex: "fulano fechou a lista").

O portal (web) e o bot (worker) sao processos SEPARADOS no Render, entao o portal
nao pode usar o objeto do bot pra mandar mensagem. Em vez disso, fala direto com a
API HTTP do Telegram (api.telegram.org/bot<token>/sendMessage) usando o
TELEGRAM_TOKEN. Funciona de qualquer processo.

WhatsApp fica preparado (esqueleto) pra quando o numero oficial existir.

Tolerante a falha: se nao houver token, dono sem telegram, ou a rede falhar, a
funcao apenas registra no log e retorna False - nunca quebra o fluxo de quem
clicou o botao.
"""
import json
import logging
import os
import urllib.request

_log = logging.getLogger("openclaw.notificar")

_TG_API = "https://api.telegram.org"
_TIMEOUT = 8


def _enviar_telegram(chat_id: int, texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token or not chat_id:
        _log.info("notificar: sem token ou chat_id (token=%s, chat=%s)",
                  bool(token), chat_id)
        return False
    url = f"{_TG_API}/bot{token}/sendMessage"
    dados = json.dumps({
        "chat_id": chat_id, "text": texto, "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status == 200
    except Exception as e:  # noqa: BLE001
        _log.warning("notificar: falha ao enviar telegram: %s", e)
        return False


def _telegram_id_do_dono(pool, conta_id: int) -> int | None:
    with pool.connection() as c:
        row = c.execute(
            "select telegram_id from membros "
            "where conta_id=%s and papel='dono' and telegram_id is not null",
            (conta_id,)).fetchone()
    return row[0] if row else None


def avisar_dono_lista_fechada(pool, conta_id: int, quem_nome: str,
                              n_itens: int = 0) -> bool:
    """Avisa o dono (via Telegram) que alguem fechou/terminou a lista de compras.
    A lista NAO trava - e' so' um aviso. Retorna True se conseguiu notificar.
    """
    chat = _telegram_id_do_dono(pool, conta_id)
    if not chat:
        _log.info("notificar: dono da conta %s sem telegram vinculado", conta_id)
        return False
    qtd = f" ({n_itens} {'item' if n_itens == 1 else 'itens'})" if n_itens else ""
    texto = (f"📋 *{quem_nome}* fechou a lista de compras{qtd}.\n"
             f"Dá uma olhada quando puder! Acesse o painel pra ver e comparar preços.")
    ok = _enviar_telegram(chat, texto)
    # WhatsApp: preparado pra quando o numero oficial existir.
    # if not ok: _enviar_whatsapp(...)
    return ok


def avisar_proposta_assinada(pool, conta_id: int, cliente: str, assinante: str,
                             valor: str = "", criado_por: str | None = None) -> bool:
    """Avisa a empresa que o cliente APROVOU/ASSINOU a proposta.

    Manda pro dono no Telegram + e-mail da conta + e-mail do vendedor que criou
    (se for membro com e-mail). Tolerante a falha: nunca quebra o fluxo do cliente.
    Devolve True se conseguiu notificar por algum canal.
    """
    cliente = (cliente or "cliente").strip()
    val = f" · {valor}" if valor else ""
    texto = (f"✅ *{assinante}* aprovou a proposta de *{cliente}*{val}!\n"
             f"Feche o contrato no painel (Serviços › funil) pra gerar os títulos.")
    ok = False
    chat = _telegram_id_do_dono(pool, conta_id)
    if chat:
        ok = _enviar_telegram(chat, texto) or ok

    # e-mails: da conta (dono) + do membro que criou a proposta, se tiver.
    destinos = set()
    try:
        with pool.connection() as c:
            r = c.execute("select email from contas where id=%s", (conta_id,)).fetchone()
            if r and r[0]:
                destinos.add(r[0])
            if criado_por and str(criado_por).isdigit():
                m = c.execute("select email from membros where id=%s and conta_id=%s",
                              (int(criado_por), conta_id)).fetchone()
                if m and m[0]:
                    destinos.add(m[0])
    except Exception as e:  # noqa: BLE001
        _log.info("avisar_proposta_assinada: falha ao buscar e-mails: %s", e)
    if destinos:
        try:
            from finance.email_sender import enviar_aviso
            msg = (f"{assinante} aprovou e assinou a proposta de {cliente}{val}. "
                   f"Acesse o painel (Serviços › funil) e feche o contrato pra gerar os títulos.")
            for d in destinos:
                ok = enviar_aviso(d, "Proposta aprovada", msg) or ok
        except Exception as e:  # noqa: BLE001
            _log.info("avisar_proposta_assinada: falha no e-mail: %s", e)
    return ok


def notificar_admin(texto: str) -> bool:
    """Manda uma mensagem pro Telegram do ADMIN do SaaS (env ADMIN_TELEGRAM_ID).
    Tolerante a falha: sem env ou sem rede, loga e devolve False."""
    chat = os.environ.get("ADMIN_TELEGRAM_ID")
    try:
        chat = int(chat) if chat else None
    except (TypeError, ValueError):
        chat = None
    if not chat:
        _log.info("notificar_admin: ADMIN_TELEGRAM_ID nao configurado")
        return False
    return _enviar_telegram(chat, texto)


def avisar_admin(assunto: str, mensagem: str) -> bool:
    """Alerta GENERICO pro ADMIN do SaaS por E-MAIL (ADMIN_EMAIL, fallback pro
    remetente SMTP) + Telegram (ADMIN_TELEGRAM_ID). Usado pelos monitores
    proativos (saldo Twilio/Asaas, gasto da IA). Tolerante a falha: nunca
    levanta excecao."""
    ok = False
    destino = os.environ.get("ADMIN_EMAIL")
    if not destino:
        try:
            from finance.email_sender import remetente_configurado
            destino = remetente_configurado()
        except Exception:  # noqa: BLE001
            destino = None
    if destino:
        try:
            from finance.email_sender import enviar_aviso
            ok = enviar_aviso(destino, assunto, mensagem.replace("\n", "<br>")) or ok
        except Exception as e:  # noqa: BLE001
            _log.warning("avisar_admin: falha no e-mail: %s", e)
    try:
        ok = notificar_admin(f"*{assunto}*\n{mensagem}") or ok
    except Exception as e:  # noqa: BLE001
        _log.warning("avisar_admin: falha no telegram: %s", e)
    return ok


# Texto por categoria de falha (assunto do e-mail, frase curta, e acao sugerida).
# Serve pra QUALQUER provedor externo pago (IA, Twilio, Asaas, STT, Credify...).
_TEXTO_CATEGORIA = {
    "credito": (
        "sem credito/saldo",
        "o saldo/credito acabou ou esta' no limite",
        "Recarregue os creditos no painel de billing do provedor.",
    ),
    "auth": (
        "chave invalida",
        "a chave/credencial foi recusada (invalida, expirada ou sem permissao)",
        "Gere/atualize a chave nas variaveis de ambiente (Render).",
    ),
    "quota": (
        "limite (quota) estourado",
        "o limite de uso (rate limit/quota) foi atingido",
        "Considere subir o plano do provedor ou reduzir a frequencia.",
    ),
}


def avisar_admin_falha_provedor(servico: str, categoria: str = "credito",
                                detalhe: str = "", canal: str = "") -> bool:
    """Avisa o ADMIN do SaaS que um PROVEDOR EXTERNO pago falhou de forma
    ACIONAVEL (sem credito / chave invalida / quota estourada). O cliente ja'
    recebeu uma mensagem de instabilidade e NAO viu o erro tecnico. Manda por
    E-MAIL (env ADMIN_EMAIL, fallback pro remetente SMTP) + Telegram
    (ADMIN_TELEGRAM_ID). Tolerante a falha: nunca levanta excecao.

    `servico`: nome do provedor (ex "Anthropic (IA)", "Asaas", "Twilio").
    `categoria`: 'credito' | 'auth' | 'quota' (default 'credito').
    `detalhe`: texto tecnico do erro (so' pro admin, nunca pro cliente).
    `canal`: onde ocorreu, se aplicavel (ex "whatsapp", "telegram")."""
    rotulo, frase, acao = _TEXTO_CATEGORIA.get(categoria, _TEXTO_CATEGORIA["credito"])
    origem = f" ({canal})" if canal else ""
    assunto = f"⚠️ {servico}: {rotulo}"
    corpo = (
        f"O provedor *{servico}* falhou{origem}: {frase}.\n\n"
        "Os clientes estao recebendo uma mensagem de 'instabilidade tecnica' no "
        "lugar do erro — ninguem viu nada de errado, mas essa integracao NAO "
        "funciona ate' resolver.\n\n"
        f"Acao: {acao}\n\n"
        f"Detalhe tecnico:\n{detalhe or '(sem detalhe)'}"
    )
    ok = False

    # 1) E-mail pro admin
    destino = os.environ.get("ADMIN_EMAIL")
    if not destino:
        try:
            from finance.email_sender import remetente_configurado
            destino = remetente_configurado()  # fallback: o proprio remetente SMTP
        except Exception:  # noqa: BLE001
            destino = None
    if destino:
        try:
            from finance.email_sender import enviar_aviso
            ok = enviar_aviso(destino, assunto, corpo.replace("\n", "<br>")) or ok
        except Exception as e:  # noqa: BLE001
            _log.warning("avisar_admin_falha_provedor: falha no e-mail: %s", e)
    else:
        _log.info("avisar_admin_falha_provedor: sem ADMIN_EMAIL nem remetente SMTP")

    # 2) Telegram pro admin (reaproveita notificar_admin)
    try:
        ok = notificar_admin(
            f"⚠️ *{servico}: {rotulo}*{origem}. Os clientes estao vendo "
            f"'instabilidade' no lugar do erro. {acao}"
        ) or ok
    except Exception as e:  # noqa: BLE001
        _log.warning("avisar_admin_falha_provedor: falha no telegram: %s", e)

    return ok


def avisar_admin_saldo_ia(detalhe: str = "", canal: str = "") -> bool:
    """Atalho historico: falha de SALDO da Anthropic (IA). Mantido por compat —
    delega pro aviso generico de provedor."""
    return avisar_admin_falha_provedor("Anthropic (IA)", "credito", detalhe, canal)


def alerta_fase_b(pool, sempre: bool = False) -> bool:
    """Checa o gatilho da Fase B e avisa o admin no Telegram.
    Por padrao SO' avisa quando LIBERADA (nao enche o saco). Com sempre=True,
    manda o progresso de qualquer jeito (ideal pra um cron diario).
    Retorna True se mandou alguma mensagem."""
    from finance.estatisticas import pronto_para_fase_b, resumo_fase_b_texto
    d = pronto_para_fase_b(pool)
    if not d["liberada"] and not sempre:
        return False
    cabecalho = ("🔓 *FASE B LIBERADA!* O banco encheu o suficiente.\n\n"
                 if d["liberada"] else "")
    return notificar_admin(cabecalho + resumo_fase_b_texto(pool))
