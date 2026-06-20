#!/usr/bin/env bash
# ============================================================================
# fix_eh_foto.sh
# Conserta a metrica de custo em core/agent.py:
#   ANTES: _eh_foto = _cw > 4000   (chuta pelo cache_write; erra no cache quente)
#   DEPOIS: _eh_foto = imagem_b64 is not None   (a verdade que a funcao ja tem)
# RODE NA RAIZ DO REPO openclaw:   bash fix_eh_foto.sh
# Aborta e restaura backup se o trecho nao bater exatamente 1x.
# ============================================================================
set -euo pipefail

echo "==> 1/4  Conferindo raiz do repo..."
[ -d core ] && [ -d finance ] || { echo "    ERRO: rode na RAIZ do repo (core/ e finance/)." >&2; exit 1; }

echo "==> 2/4  Backup de core/agent.py..."
cp core/agent.py core/agent.py.bak && echo "    backup: core/agent.py.bak"

echo "==> 3/4  Aplicando o fix..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "core/agent.py"
src = open(p, encoding="utf-8").read()
old = "_eh_foto = _cw > 4000   # cache_write alto = leu imagem (cupom)"
new = "_eh_foto = imagem_b64 is not None   # verdade direta: a interacao tem foto/PDF?"
n = src.count(old)
if n != 1:
    print(f"    ERRO: esperava 1 ocorrencia da ancora, achei {n}. Abortando.", file=sys.stderr)
    sys.exit(2)
open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
print("    fix aplicado: _eh_foto = imagem_b64 is not None")
PYEOF

if ! "$PYBIN" -m py_compile core/agent.py 2>/tmp/_agent_err; then
  echo "    ERRO: core/agent.py nao compila. Restaurando backup." >&2
  cat /tmp/_agent_err >&2
  mv core/agent.py.bak core/agent.py
  exit 1
fi
echo "    OK: core/agent.py compila."

echo "==> 4/4  Commit + push..."
git add core/agent.py
if git diff --cached --quiet; then
  echo "    Nada mudou. Nada a commitar."
else
  git commit -m "fix: _eh_foto usa imagem_b64 (corrige contagem de fotos no cache quente)"
  git push
  echo "    Commit + push OK."
fi

rm -f core/agent.py.bak
echo ""
echo "============================================================================"
echo "PRONTO. Deploy o openclaw-bot-bcu3 (e o web, se aplicavel)."
echo "A partir de agora, TODA chamada de uma interacao com foto/PDF grava eh_foto=true"
echo "no uso_api — inclusive a 2a+ foto de uma rajada (que antes ficava de fora)."
echo "============================================================================"
