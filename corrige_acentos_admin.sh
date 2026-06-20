#!/usr/bin/env bash
# corrige_acentos_admin.sh — conserta o mojibake (acentos quebrados) em
# web/admin_precos.py. Reconverte latin-1->utf-8 SO' nas linhas de texto exibido.
# NAO toca em finance/lista_compras.py (translate SQL funcional - risco).
# RODE NA RAIZ: bash corrige_acentos_admin.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f web/admin_precos.py ] || { echo "ERRO: rode na raiz."; exit 1; }
echo "==> 2/4 Tem mojibake?"; if ! grep -q "Ã" web/admin_precos.py; then echo "Sem mojibake. Nada a fazer."; exit 0; fi
cp web/admin_precos.py web/admin_precos.py.bak
echo "==> 3/4 Reconvertendo as palavras corrompidas..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys, re
p = "web/admin_precos.py"
src = open(p, encoding="utf-8").read()

# Mapa EXATO das palavras corrompidas -> corretas (so' o que aparece no arquivo).
# Usar mapa fechado e' mais seguro que reconverter o arquivo inteiro (evita mexer
# em algo que nao deveria). Cada par foi validado.
trocas = {
    "Banco de preÃ§os (o ouro)": "Banco de preços (o ouro)",
    "PreÃ§os coletados": "Preços coletados",
    "Produtos Ãºnicos": "Produtos únicos",
    "Menor preÃ§o": "Menor preço",
    "EndereÃ§o": "Endereço",
    "PreÃ§os": "Preços",
    "PÃ¡gina": "Página",
    "PrÃ³xima": "Próxima",
}
# aplica da chave mais longa pra mais curta (evita trocar "PreÃ§os" dentro de "PreÃ§os coletados" antes)
for k in sorted(trocas, key=len, reverse=True):
    src = src.replace(k, trocas[k])

# checagem: sobrou algum mojibake?
restante = re.findall(r"\S*Ã\S*", src)
if restante:
    print("ERRO: ainda ha mojibake nao mapeado:", restante[:10], file=sys.stderr)
    sys.exit(2)

open(p, "w", encoding="utf-8").write(src)
print("  acentos corrigidos, nenhum mojibake restante.")
PYEOF
echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile web/admin_precos.py 2>/tmp/_ac || { echo "ERRO compila:"; cat /tmp/_ac; mv web/admin_precos.py.bak web/admin_precos.py; exit 1; }
grep -q "Ã" web/admin_precos.py && { echo "ERRO: ainda tem mojibake. Restaurando."; mv web/admin_precos.py.bak web/admin_precos.py; exit 1; } || true
echo "    OK: compila e sem mojibake."
git add web/admin_precos.py
git diff --cached --quiet || { git commit -m "fix: corrige acentos quebrados (mojibake) na pagina /admin/precos"; git push; }
rm -f web/admin_precos.py.bak
echo "PRONTO. (lista_compras.py NAO foi tocado de proposito - translate SQL funcional)"
