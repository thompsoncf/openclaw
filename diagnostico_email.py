"""Diagnóstico do envio de email — rodar no RENDER SHELL.
Testa a config SMTP e tenta enviar um email de teste, mostrando o erro EXATO.
Uso no Render Shell:  python diagnostico_email.py SEU_EMAIL@teste.com
"""
import os, sys, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

print("=" * 50)
print("DIAGNÓSTICO DE EMAIL — Zaq")
print("=" * 50)

# 1. a config existe?
user = os.environ.get("SMTP_USER")
senha = os.environ.get("SMTP_SENHA")
host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
port = int(os.environ.get("SMTP_PORT", "587"))

print(f"SMTP_HOST: {host}")
print(f"SMTP_PORT: {port}")
print(f"SMTP_USER: {user or '*** AUSENTE ***'}")
if senha:
    # O Google entrega a senha de app em 4 blocos de 4 ("abcd efgh ijkl mnop") e
    # ACEITA os espaços no login — confirmado em produção. Por isso o tamanho se
    # confere SEM eles: antes isso acusava "remova!" num caso que funcionava, e
    # mandava procurar problema no lugar errado.
    limpa = "".join(senha.split())
    espacos = " (com espaços, que o Google aceita)" if len(limpa) != len(senha) else ""
    print(f"SMTP_SENHA: presente, {len(limpa)} caracteres{espacos}")
    if len(limpa) != 16:
        print(f"  ⚠️ senha de app do Google tem 16 caracteres; a sua tem {len(limpa)}."
              f" Confira se é a senha de APP (não a senha normal).")
else:
    print("SMTP_SENHA: *** AUSENTE ***")

if not user or not senha:
    print("\n❌ Falta SMTP_USER ou SMTP_SENHA no Render. Configure e rode de novo.")
    sys.exit(1)

# 2. tenta conectar e logar
destino = sys.argv[1] if len(sys.argv) > 1 else user
print(f"\nTestando envio para: {destino}")
try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.set_debuglevel(0)
        print("  ✓ conectou ao servidor SMTP")
        s.starttls(context=ctx)
        print("  ✓ STARTTLS ok")
        s.login(user, senha)
        print("  ✓ LOGIN ok (senha de app aceita)")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Teste de email do Zaq"
        msg["From"] = f"Zaq <{user}>"
        msg["To"] = destino
        msg.attach(MIMEText("Funcionou! O envio de email do Zaq está OK.", "plain", "utf-8"))
        s.sendmail(user, [destino], msg.as_string())
        print("  ✓ ENVIO ok")
    print(f"\n✅ EMAIL ENVIADO! Confira a caixa de {destino} (e o spam).")
except smtplib.SMTPAuthenticationError as e:
    print(f"\n❌ ERRO DE LOGIN: {e}")
    print("  → A senha de app está errada, ou a conta não tem 2FA ativado,")
    print("    ou o SMTP_USER não bate com a conta da senha de app.")
    print("  → Gere uma nova senha de app em: myaccount.google.com/apppasswords")
except Exception as e:
    print(f"\n❌ ERRO: {type(e).__name__}: {e}")
    print("  → Veja a mensagem acima. Pode ser porta bloqueada, host errado, rede.")
