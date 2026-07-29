"""Envio de emails do Zaq via SMTP (Google Workspace).

Filosofia (igual notificar.py): le' tudo de variavel de ambiente, tolerante a
falha (se faltar config ou a rede cair, loga e retorna False - NUNCA quebra o
fluxo de quem se cadastrou). Comeca com boas-vindas; tem funcoes pra outros tipos.

Variaveis de ambiente (cadastrar no Render, NUNCA no codigo):
- SMTP_HOST      (default smtp.gmail.com)
- SMTP_PORT      (default 587)
- SMTP_USER      (o email remetente, ex contato@seudominio.com.br)
- SMTP_SENHA     (a SENHA DE APP do Google - 16 caracteres, sem espacos)
- SMTP_FROM_NOME (nome de exibicao, ex "Zaq"; default "Zaq")
- APP_URL        (url do portal, pra links nos emails; default a do Render)
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_log = logging.getLogger("openclaw.email")

_TIMEOUT = 15


def _config() -> dict | None:
    """Le a config SMTP do ambiente. Retorna None se faltar o essencial."""
    user = os.environ.get("SMTP_USER")
    senha = os.environ.get("SMTP_SENHA")
    if not user or not senha:
        _log.info("email: SMTP_USER/SMTP_SENHA ausentes - envio desabilitado")
        return None
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "senha": senha,
        "from_nome": os.environ.get("SMTP_FROM_NOME", "Zaq"),
    }


def _app_url() -> str:
    return os.environ.get("APP_URL", "https://openclaw-web-bcu3.onrender.com").rstrip("/")


def remetente_configurado() -> str | None:
    """O endereço remetente configurado (SMTP_USER) — aparece em todo e-mail, não
    é segredo. Retorna None se o envio de e-mail não está configurado."""
    return os.environ.get("SMTP_USER") or None


def enviar_email(destino: str, assunto: str, html: str,
                 texto_alt: str | None = None, reply_to: str | None = None,
                 from_nome: str | None = None) -> bool:
    """Envia um email (HTML + alternativa texto). Tolerante a falha: retorna
    True se enviou, False se faltou config / deu erro (sem levantar excecao).

    reply_to: se informado, as respostas voltam pra esse endereco (ex: o vendedor
      da empresa, no envio de prospeccao — o From continua sendo o SMTP do Zaq).
    from_nome: sobrescreve o nome de exibicao do remetente so' neste envio (ex: o
      nome da empresa que esta' prospectando)."""
    cfg = _config()
    if not cfg or not destino:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{(from_nome or cfg['from_nome'])} <{cfg['user']}>"
        msg["To"] = destino
        if reply_to:
            msg["Reply-To"] = reply_to
        # parte texto (fallback) + parte html
        if texto_alt:
            msg.attach(MIMEText(texto_alt, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=_TIMEOUT) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["senha"])
            s.sendmail(cfg["user"], [destino], msg.as_string())
        _log.info("email enviado para %s (assunto=%s)", destino, assunto)
        return True
    except Exception as e:  # noqa: BLE001 - nunca quebra o fluxo
        _log.warning("email falhou para %s: %s: %s", destino, type(e).__name__, e)
        return False


def enviar_com_creds(user: str, senha: str, host: str, port: int, destino: str,
                     assunto: str, html: str, texto_alt: str | None = None,
                     from_nome: str | None = None, reply_to: str | None = None) -> bool:
    """Envia usando credenciais EXPLÍCITAS (SMTP da própria empresa) — o From é o
    `user`. Mesma tolerância a falha do enviar_email. Usado pelo envio por-conta."""
    if not (user and senha and destino):
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = f"{(from_nome or user)} <{user}>"
        msg["To"] = destino
        if reply_to:
            msg["Reply-To"] = reply_to
        if texto_alt:
            msg.attach(MIMEText(texto_alt, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=_TIMEOUT) as s:
            s.starttls(context=ctx)
            s.login(user, senha)
            s.sendmail(user, [destino], msg.as_string())
        _log.info("email(por-conta) enviado de %s para %s", user, destino)
        return True
    except Exception as e:  # noqa: BLE001
        _log.warning("email(por-conta) falhou de %s para %s: %s: %s", user, destino, type(e).__name__, e)
        return False


# ---------------------------------------------------------------------------
# Templates de email (cada tipo tem sua funcao)
# ---------------------------------------------------------------------------

def _layout(titulo: str, corpo_html: str) -> str:
    """Layout base comum (cabecalho + corpo + rodape). Simples e responsivo."""
    return f"""\
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;">
        <tr><td style="background:#0f766e;padding:24px 32px;">
          <span style="color:#fff;font-size:22px;font-weight:bold;">Zaq</span>
        </td></tr>
        <tr><td style="padding:32px;">
          <h1 style="margin:0 0 16px;font-size:20px;color:#111;">{titulo}</h1>
          <div style="font-size:15px;color:#333;line-height:1.6;">{corpo_html}</div>
        </td></tr>
        <tr><td style="padding:20px 32px;background:#fafafa;border-top:1px solid #eee;">
          <p style="margin:0;font-size:12px;color:#999;">
            Voce recebeu este email porque tem uma conta no Zaq.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def enviar_boas_vindas(destino: str, nome: str | None = None,
                       codigo_telegram: str | None = None) -> bool:
    """Email de boas-vindas pra conta nova. Se houver codigo de convite do
    Telegram, inclui o botao pra conectar."""
    saudacao = f"Ola, {nome}!" if nome else "Ola!"
    url = _app_url()
    bloco_telegram = ""
    if codigo_telegram:
        link_tg = f"https://t.me/clawaladdin_bot?text={codigo_telegram}"
        bloco_telegram = f"""
        <p>Pra comecar a usar pelo Telegram, e' so' conectar:</p>
        <p style="text-align:center;margin:24px 0;">
          <a href="{link_tg}" style="background:#0f766e;color:#fff;text-decoration:none;
             padding:12px 28px;border-radius:8px;font-weight:bold;display:inline-block;">
             Conectar meu Telegram</a>
        </p>
        <p style="font-size:13px;color:#666;">Ou abra o portal:
           <a href="{url}/painel" style="color:#0f766e;">{url}/painel</a></p>
        """
    else:
        bloco_telegram = f"""
        <p style="text-align:center;margin:24px 0;">
          <a href="{url}/painel" style="background:#0f766e;color:#fff;text-decoration:none;
             padding:12px 28px;border-radius:8px;font-weight:bold;display:inline-block;">
             Acessar meu painel</a>
        </p>"""
    corpo = f"""
      <p>{saudacao}</p>
      <p>Sua conta no Zaq foi criada com sucesso! ð</p>
      <p>O Zaq e' seu assistente financeiro: registre gastos, leia cupons,
         monte sua lista de compras e compare precos.</p>
      {bloco_telegram}
      <p style="margin-top:24px;">Qualquer duvida, e' so' responder este email.</p>
    """
    texto = (f"{saudacao}\n\nSua conta no Zaq foi criada com sucesso!\n"
             f"Acesse: {url}/painel")
    return enviar_email(destino, "Bem-vindo ao Zaq! ð",
                        _layout("Bem-vindo ao Zaq!", corpo), texto)


def enviar_convite_zaq(destino: str, nome: str | None, link: str,
                       from_nome: str | None = None, reply_to: str | None = None) -> bool:
    """Convida o lead a CRIAR a conta e acessar o Zaq (link pro /cadastro,
    pré-preenchido). Reusa o cadastro público — sem token/senha extra. O reply_to
    é o vendedor; o from_nome é a empresa que está prospectando."""
    import html as _htmlmod
    saud = f"Olá, {_htmlmod.escape(nome)}!" if nome else "Olá!"
    corpo = f"""
      <p>{saud}</p>
      <p>Preparei um acesso pra você começar a usar o <b>Zaq</b> — o assistente
         financeiro que funciona direto no WhatsApp. Criar a conta leva 1 minuto e
         você já sai usando, com <b>teste grátis</b>.</p>
      <p style="text-align:center;margin:24px 0;">
        <a href="{link}" style="background:#0f766e;color:#fff;text-decoration:none;
           padding:12px 28px;border-radius:8px;font-weight:bold;display:inline-block;">
           Criar minha conta no Zaq</a>
      </p>
      <p style="font-size:13px;color:#666;">Ou copie e cole no navegador:<br>
         <a href="{link}" style="color:#0f766e;">{link}</a></p>
      <p style="margin-top:24px;">Qualquer dúvida, é só responder este e-mail. 😊</p>
    """
    texto = f"{saud}\n\nCrie sua conta no Zaq (teste grátis): {link}"
    return enviar_email(destino, "Seu acesso ao Zaq 🚀",
                        _layout("Comece a usar o Zaq", corpo), texto,
                        reply_to=reply_to, from_nome=from_nome)


def enviar_recuperacao_senha(destino: str, link_reset: str,
                             nome: str | None = None) -> bool:
    """Email de recuperacao de senha (link com token)."""
    saudacao = f"Ola, {nome}!" if nome else "Ola!"
    corpo = f"""
      <p>{saudacao}</p>
      <p>Recebemos um pedido pra redefinir a senha da sua conta. Clique abaixo
         pra criar uma nova senha:</p>
      <p style="text-align:center;margin:24px 0;">
        <a href="{link_reset}" style="background:#0f766e;color:#fff;text-decoration:none;
           padding:12px 28px;border-radius:8px;font-weight:bold;display:inline-block;">
           Redefinir minha senha</a>
      </p>
      <p style="font-size:13px;color:#666;">Se voce nao pediu isso, pode ignorar
         este email - sua senha continua a mesma.</p>
    """
    texto = f"{saudacao}\n\nRedefina sua senha: {link_reset}"
    return enviar_email(destino, "Redefinir sua senha - Zaq",
                        _layout("Redefinir sua senha", corpo), texto)


def enviar_confirmacao_email(destino: str, link_confirma: str,
                             nome: str | None = None) -> bool:
    """Email de confirmacao de um NOVO e-mail (link com token, vale 24h)."""
    saudacao = f"Ola, {nome}!" if nome else "Ola!"
    corpo = f"""
      <p>{saudacao}</p>
      <p>Voce pediu pra usar este e-mail na sua conta Zaq. Confirme clicando abaixo
         (o link vale 24 horas):</p>
      <p style="text-align:center;margin:24px 0;">
        <a href="{link_confirma}" style="background:#0f766e;color:#fff;text-decoration:none;
           padding:12px 28px;border-radius:8px;font-weight:bold;display:inline-block;">
           Confirmar meu e-mail</a>
      </p>
      <p style="font-size:13px;color:#666;">Se voce nao pediu isso, pode ignorar
         este email.</p>
    """
    texto = f"{saudacao}\n\nConfirme seu e-mail: {link_confirma}"
    return enviar_email(destino, "Confirme seu e-mail - Zaq", corpo, texto)


def enviar_aviso(destino: str, titulo: str, mensagem: str,
                 nome: str | None = None) -> bool:
    """Email de aviso generico (cobranca, lista pronta, etc)."""
    saudacao = f"Ola, {nome}!" if nome else "Ola!"
    corpo = f"<p>{saudacao}</p><p>{mensagem}</p>"
    texto = f"{saudacao}\n\n{mensagem}"
    return enviar_email(destino, f"{titulo} - Zaq",
                        _layout(titulo, corpo), texto)
