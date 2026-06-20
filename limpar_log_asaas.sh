#!/usr/bin/env bash
# ============================================================================
# limpar_log_asaas.sh
# Edicao CIRURGICA em web/app.py (NAO reescreve o arquivo):
#   - 'token recebido len=...'   -> debug  (para de poluir em cada ping)
#   - 'corpo vazio (ping/teste)' -> debug
#   + adiciona um RASTRO por evento real (linha-marco pra acompanhar o fluxo)
# RODE NA RAIZ DO REPO openclaw:   bash limpar_log_asaas.sh
# Aborta e restaura backup se qualquer trecho nao bater exatamente.
# ============================================================================
set -euo pipefail

echo "==> 1/4  Conferindo raiz do repo..."
[ -d finance ] && [ -d web ] || { echo "    ERRO: rode na RAIZ do repo (finance/ e web/)." >&2; exit 1; }

echo "==> 2/4  Backup de web/app.py..."
cp web/app.py web/app.py.bak && echo "    backup: web/app.py.bak"

echo "==> 3/4  Aplicando edicoes cirurgicas..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "web/app.py"
src = open(p, encoding="utf-8").read()

edits = [
    # (descricao, antigo, novo, ocorrencias_esperadas)
    ("token->debug",
     '_logw.warning("ASAAS token recebido',
     '_logw.debug("ASAAS token recebido', 1),
    ("corpo-vazio->debug",
     '_logw.warning("ASAAS webhook com corpo vazio',
     '_logw.debug("ASAAS webhook com corpo vazio', 1),
    ("add-rastro",
     '    # processamento BLINDADO: se algo falhar, loga o erro REAL e responde 200',
     '    # RASTRO (fase de testes): uma linha por evento real, pra acompanhar o fluxo\n'
     '    _logw.warning("ASAAS >> evento=%s pay=%s conta=%s valor=%s",\n'
     '                  evento, pay.get("id", ""), conta_id, pay.get("value", ""))\n'
     '\n'
     '    # processamento BLINDADO: se algo falhar, loga o erro REAL e responde 200', 1),
]

for desc, old, new, want in edits:
    got = src.count(old)
    if got != want:
        print(f"    ERRO em '{desc}': esperava {want} ocorrencia(s), achei {got}. Abortando.", file=sys.stderr)
        sys.exit(2)
    src = src.replace(old, new, 1)

open(p, "w", encoding="utf-8").write(src)
print("    edicoes aplicadas: token->debug, corpo-vazio->debug, +rastro")
PYEOF

# valida sintaxe; se falhar, restaura
if ! "$PYBIN" -m py_compile web/app.py 2>/tmp/_app_err; then
  echo "    ERRO: web/app.py nao compila. Restaurando backup." >&2
  cat /tmp/_app_err >&2
  mv web/app.py.bak web/app.py
  exit 1
fi
echo "    OK: web/app.py compila."

echo "==> 4/4  Commit + push..."
git add web/app.py
if git diff --cached --quiet; then
  echo "    Nada mudou. Nada a commitar."
else
  git commit -m "chore: log mais limpo no webhook Asaas (rastro por evento + pings em debug)"
  git push
  echo "    Commit + push OK."
fi

rm -f web/app.py.bak
echo ""
echo "============================================================================"
echo "PRONTO. Deploy o openclaw-web-bcu3 (Manual Deploy se auto-deploy off)."
echo "Num pagamento de teste, o log agora mostra so:"
echo "    ASAAS >> evento=PAYMENT_RECEIVED pay=pay_xxx conta=3 valor=49.9"
echo "    ASAAS conta 3 ATIVADA (pagamento pay_xxx)"
echo "Pings nao poluem mais (foram pra nivel debug)."
echo "============================================================================"
