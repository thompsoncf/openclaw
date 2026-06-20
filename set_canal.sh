#!/usr/bin/env bash
# ============================================================================
# set_canal.sh — Modulo de Comunicacao, Fase 1: preencher o campo 'canal'
# Seta agente.canal_atual no ponto unico de cada canal (_agente_do):
#   web/app.py      -> "whatsapp"
#   telegram_bot.py -> "telegram"
# Uma linha por arquivo, no choke point. RODE NA RAIZ DO REPO: bash set_canal.sh
# Aborta e restaura se ancora nao bater 1x ou nao compilar.
# ============================================================================
set -euo pipefail

echo "==> 1/4  Raiz do repo..."
[ -f web/app.py ] && [ -f telegram_bot.py ] || { echo "    ERRO: rode na RAIZ (web/app.py + telegram_bot.py)." >&2; exit 1; }

echo "==> 2/4  Checando se ja' aplicado..."
if grep -q 'canal_atual = "whatsapp"' web/app.py && grep -q 'canal_atual = "telegram"' telegram_bot.py; then
  echo "    JA APLICADO nos dois. Nada a fazer." >&2; exit 0; fi
cp web/app.py web/app.py.bak; cp telegram_bot.py telegram_bot.py.bak
echo "    backups criados."

echo "==> 3/4  Aplicando..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys

alvos = [
    ("web/app.py",
     '    return criar_agente_financeiro(_brain, LivroCaixa(_pool, conta.id, membro.id), memoria, lista, membro.papel, banco, cidade)',
     '    ag = criar_agente_financeiro(_brain, LivroCaixa(_pool, conta.id, membro.id), memoria, lista, membro.papel, banco, cidade)\n    ag.canal_atual = "whatsapp"\n    return ag'),
    ("telegram_bot.py",
     '    return criar_agente_financeiro(_brain, livro, memoria, lista, membro.papel, banco, cidade)',
     '    ag = criar_agente_financeiro(_brain, livro, memoria, lista, membro.papel, banco, cidade)\n    ag.canal_atual = "telegram"\n    return ag'),
]

for arq, old, new in alvos:
    src = open(arq, encoding="utf-8").read()
    n = src.count(old)
    if n != 1:
        print(f"    ERRO {arq}: ancora apareceu {n}x (esperava 1). Abortando.", file=sys.stderr)
        sys.exit(2)
    open(arq, "w", encoding="utf-8").write(src.replace(old, new, 1))
    print(f"    {arq}: canal setado.")
PYEOF

echo "==> 4/4  Validando + commit..."
if ! "$PYBIN" -m py_compile web/app.py telegram_bot.py 2>/tmp/_canal_err; then
  echo "    ERRO: nao compila. Restaurando." >&2; cat /tmp/_canal_err >&2
  mv web/app.py.bak web/app.py; mv telegram_bot.py.bak telegram_bot.py
  exit 1
fi
grep -q 'canal_atual = "whatsapp"' web/app.py && grep -q 'canal_atual = "telegram"' telegram_bot.py || {
  echo "    ERRO: nao ficou nos dois. Restaurando." >&2
  mv web/app.py.bak web/app.py; mv telegram_bot.py.bak telegram_bot.py; exit 1; }
echo "    OK: compila e canal setado nos dois."

git add web/app.py telegram_bot.py
if git diff --cached --quiet; then echo "    Nada a commitar."; else
  git commit -m "feat: preenche canal (whatsapp/telegram) no log de interacoes"
  git push; echo "    Commit + push OK."; fi
rm -f web/app.py.bak telegram_bot.py.bak
echo ""
echo "PRONTO. Deploy web + bot. A partir daqui, conversas_log.canal vem preenchido."
