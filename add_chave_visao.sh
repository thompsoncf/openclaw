#!/usr/bin/env bash
# ============================================================================
# add_chave_visao.sh — Plano B do QR: o agente entrega a CHAVE DE ACESSO que lê
# do TEXTO do cupom quando o QR falha. Valida modelo (65/55) E digito verificador
# (mod 11) antes de usar. Alimenta o banco de ouro. Tudo finance/. Custo zero.
#  - nfce_qr.py:           extrair_chave robusto (rotulo colado/grupos) + chave_dv_valida
#  - tools.py:             param 'chave' + usa a chave (validada) quando QR nao leu
#  - agente_financeiro.py: 1 instrucao no prompt
# RODE NA RAIZ: bash add_chave_visao.sh
# ============================================================================
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f finance/nfce_qr.py ] && [ -f finance/tools.py ] && [ -f finance/agente_financeiro.py ] || { echo "ERRO: rode na raiz."; exit 1; }
echo "==> 2/4 Ja aplicado?"; if grep -q "def chave_dv_valida" finance/nfce_qr.py; then echo "JA EXISTE. Nada a fazer."; exit 0; fi
cp finance/nfce_qr.py finance/nfce_qr.py.bak; cp finance/tools.py finance/tools.py.bak; cp finance/agente_financeiro.py finance/agente_financeiro.py.bak

echo "==> 3/4 Editando..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
def patch(path, edits):
    src = open(path, encoding="utf-8").read()
    for desc, old, new in edits:
        n = src.count(old)
        if n != 1:
            print(f"ERRO {path} [{desc}]: ancora {n}x (esperava 1).", file=sys.stderr); sys.exit(2)
        src = src.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(src)
    print(f"  {path}: {len(edits)} edicao(oes).")

# ---------- nfce_qr.py: extrair_chave robusto + chave_dv_valida ----------
patch("finance/nfce_qr.py", [("extrair+dv",
'''def extrair_chave(texto: str) -> str | None:
    """Extrai a chave de 44 digitos de uma URL/texto de QR de NFC-e."""
    if not texto:
        return None
    # a chave aparece como p=<44digitos> ou chNFe=<44> ou solta no texto.
    # IMPORTANTE: nao basta juntar todos os digitos do texto - a URL pode ter
    # digitos extras (ex: o sufixo "|3|1" do PI), o que daria != 44 e quebraria.
    import re
    m = (re.search(r"[?&]p=(\\d{44})", texto)
         or re.search(r"chNFe=(\\d{44})", texto)
         or re.search(r"\\b(\\d{44})\\b", texto))
    if not m:
        return None
    chave = m.group(1)
    # modelo 65 (NFC-e) ou 55 (NF-e) fica nas posicoes 20-21
    if chave[20:22] not in ("65", "55"):
        return None
    return chave''',
'''def extrair_chave(texto: str) -> str | None:
    """Extrai a chave de 44 digitos de uma URL de QR OU do TEXTO de um cupom.
    Aceita a chave colada a rotulo ('CHAVE DE ACESSO2226...') e em grupos de 4
    com espacos. Valida o modelo 65 (NFC-e) / 55 (NF-e) nas posicoes 20-21."""
    if not texto:
        return None
    import re
    # 1) URL do QR: a chave vem como p=<44> ou chNFe=<44> (o mais confiavel).
    m = re.search(r"[?&]p=(\\d{44})", texto) or re.search(r"chNFe=(\\d{44})", texto)
    if m and m.group(1)[20:22] in ("65", "55"):
        return m.group(1)
    # 2) Texto solto (cupom/DANFE): procura runs de 44+ digitos apos remover os
    # espacos e valida o modelo. NAO usa \\b (rotulo grudado quebra o boundary).
    for run in re.findall(r"\\d{44,}", "".join(texto.split())):
        for s in range(0, len(run) - 43):
            cand = run[s:s + 44]
            if cand[20:22] in ("65", "55"):
                return cand
    return None


def chave_dv_valida(chave: str) -> bool:
    """Valida o digito verificador (mod 11) de uma chave NFC-e/NF-e de 44 digitos.
    Pega quase todo erro de 1 digito - essencial quando a chave vem da VISAO (o
    agente leu do texto do cupom), nao do QR (que ja' e' confiavel)."""
    if not chave or len(chave) != 44 or not chave.isdigit():
        return False
    corpo, dv = chave[:43], int(chave[43])
    peso, soma = 2, 0
    for d in reversed(corpo):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    calc = 0 if resto in (0, 1) else 11 - resto
    return calc == dv''')])

# ---------- tools.py: usa a chave do agente (validada) quando QR nao leu ----------
patch("finance/tools.py", [
("planoB",
'''    def registrar_itens_cupom(entrada: dict) -> str:
        itens_in = entrada.get("itens") or []
        if not itens_in:
            return "Nenhum item informado."''',
'''    def registrar_itens_cupom(entrada: dict) -> str:
        itens_in = entrada.get("itens") or []
        if not itens_in:
            return "Nenhum item informado."
        # Plano B do QR: se o QR nao leu (chave_nfce_atual vazio) mas o agente leu
        # a CHAVE DE ACESSO impressa no cupom, usa ela - validada por modelo E
        # digito verificador (pega erro de leitura). Alimenta o banco de ouro.
        if not getattr(livro, "chave_nfce_atual", None):
            try:
                from finance.nfce_qr import extrair_chave, chave_dv_valida
                _ch = extrair_chave(str(entrada.get("chave") or ""))
                if _ch and chave_dv_valida(_ch):
                    livro.chave_nfce_atual = _ch
            except Exception:  # noqa: BLE001
                pass'''),
("schema",
'''                    "cnpj_emitente": {"type": "string", "description": "CNPJ do estabelecimento (14 digitos), do cabecalho do cupom - identifica a loja exata"},''',
'''                    "cnpj_emitente": {"type": "string", "description": "CNPJ do estabelecimento (14 digitos), do cabecalho do cupom - identifica a loja exata"},
                    "chave": {"type": "string", "description": "CHAVE DE ACESSO do cupom (44 digitos), do rodape perto do QR. SEMPRE inclua se conseguir ler com certeza - alimenta o banco de precos quando o QR nao foi lido. So' digitos."},''')])

# ---------- agente_financeiro.py: instrucao no prompt ----------
patch("finance/agente_financeiro.py", [("prompt",
'''  (voce consegue ler o CNPJ no texto do cupom).''',
'''  (voce consegue ler o CNPJ no texto do cupom).
  CHAVE DE ACESSO: do mesmo jeito, se o cupom mostrar a CHAVE DE ACESSO (44
  digitos, no rodape perto do QR), passe-a no campo "chave" - SO' os 44 digitos.
  Isso alimenta o banco de precos quando o QR nao foi lido. Se NAO tiver certeza
  de algum digito, OMITA a chave (melhor sem do que com digito errado).''')])
PYEOF

echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile finance/nfce_qr.py finance/tools.py finance/agente_financeiro.py 2>/tmp/_cv || { echo "ERRO compila:"; cat /tmp/_cv; for f in nfce_qr tools agente_financeiro; do mv finance/$f.py.bak finance/$f.py; done; exit 1; }
grep -q "def chave_dv_valida" finance/nfce_qr.py && grep -q '"chave":' finance/tools.py || { echo "ERRO: nao aplicou."; for f in nfce_qr tools agente_financeiro; do mv finance/$f.py.bak finance/$f.py; done; exit 1; }
echo "    OK compila."
git add finance/nfce_qr.py finance/tools.py finance/agente_financeiro.py
git diff --cached --quiet || { git commit -m "feat: plano B do QR - agente entrega CHAVE DE ACESSO lida do texto (validada por DV)"; git push; }
for f in nfce_qr tools agente_financeiro; do rm -f finance/$f.py.bak; done
echo "PRONTO."
