#!/usr/bin/env bash
# ============================================================================
# add_alerta_fase_b.sh
# ANEXA (nao reescreve):
#   finance/estatisticas.py  -> resumo_fase_b_texto(pool)   (texto Markdown p/ Telegram)
#   finance/notificar.py     -> notificar_admin(texto), alerta_fase_b(pool, sempre=False)
# Alerta o ADMIN do SaaS no Telegram (env ADMIN_TELEGRAM_ID). Sem migracao, sem tabela.
# RODE NA RAIZ DO REPO:  bash add_alerta_fase_b.sh
# ============================================================================
set -euo pipefail

echo "==> 1/4  Raiz do repo..."
[ -d finance ] || { echo "    ERRO: rode na RAIZ (finance/)." >&2; exit 1; }

echo "==> 2/4  Checando se ja' aplicado..."
if grep -q "def resumo_fase_b_texto" finance/estatisticas.py; then
  echo "    JA EXISTE resumo_fase_b_texto. Nada a fazer." >&2; exit 0; fi
cp finance/estatisticas.py finance/estatisticas.py.bak
cp finance/notificar.py finance/notificar.py.bak
echo "    backups criados."

echo "==> 3/4  Anexando..."
cat >> finance/estatisticas.py << 'PYEOF'


def resumo_fase_b_texto(pool, gatilhos: dict | None = None) -> str:
    """Resumo legivel (Markdown do Telegram) do progresso da Fase B.
    Bom pro alerta diario ao admin ou um comando '/banco' no painel."""
    d = pronto_para_fase_b(pool, gatilhos)

    def _barra(pct: int) -> str:
        cheio = max(0, min(10, int(round((pct or 0) / 10))))
        return "█" * cheio + "░" * (10 - cheio)

    linhas = [f"*Banco de ouro – Fase B: {d['veredito']}*", ""]
    for m in d["metricas"]:
        marca = "✅" if m["ok"] else "⏳"
        u = m["unidade"]
        linhas.append(
            f"{marca} {m['nome']}: {m['valor']}{u}/{m['gatilho']}{u} "
            f"`{_barra(m['pct'])}` {m['pct']}%"
        )
    ctx = d["contexto"]
    linhas += [
        "",
        f"🏆 Produtos multi-loja (ouro): *{ctx['produtos_multiloja']}*",
        f"📊 Confirmados (≥2 leituras): *{ctx['produtos_confirmados']}*",
        f"🧾 Cupons com QR: *{ctx['cupons_com_qr']}* (~{ctx['obs_por_cupom']} itens/cupom)",
    ]
    if ctx.get("cupons_faltando_estimado") is not None:
        linhas.append(f"🎯 Faltam ~*{ctx['cupons_faltando_estimado']}* cupons pro volume")
    if d["faltam"]:
        linhas.append("")
        linhas.append("Ainda falta: " + ", ".join(d["faltam"]))
    return "\n".join(linhas)
PYEOF

cat >> finance/notificar.py << 'PYEOF'


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
PYEOF

echo "==> 4/4  Validando + commit..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
if ! "$PYBIN" -m py_compile finance/estatisticas.py finance/notificar.py 2>/tmp/_alr_err; then
  echo "    ERRO: nao compila. Restaurando." >&2; cat /tmp/_alr_err >&2
  mv finance/estatisticas.py.bak finance/estatisticas.py
  mv finance/notificar.py.bak finance/notificar.py
  exit 1
fi
grep -q "def resumo_fase_b_texto" finance/estatisticas.py && grep -q "def alerta_fase_b" finance/notificar.py || {
  echo "    ERRO: funcoes nao ficaram. Restaurando." >&2
  mv finance/estatisticas.py.bak finance/estatisticas.py
  mv finance/notificar.py.bak finance/notificar.py; exit 1; }
echo "    OK: 2 arquivos compilam e funcoes presentes."

git add finance/estatisticas.py finance/notificar.py
if git diff --cached --quiet; then echo "    Nada a commitar."; else
  git commit -m "feat: alerta da Fase B no Telegram (resumo_fase_b_texto + alerta_fase_b)"
  git push; echo "    Commit + push OK."; fi
rm -f finance/estatisticas.py.bak finance/notificar.py.bak
echo ""
echo "PRONTO. Setar env ADMIN_TELEGRAM_ID (teu chat_id). Cron diario (Claude Code):"
echo "   python -c \"from web.app import _setup; from finance.notificar import alerta_fase_b; alerta_fase_b(_setup(), sempre=True)\""
