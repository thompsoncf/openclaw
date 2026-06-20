#!/usr/bin/env bash
# ============================================================================
# add_classe_qr.sh — classificacao honesta da midia + leitor de PDF melhorado.
#  finance/nfce_qr.py:    ler_chave_de_pdf le TEXTO antes do QR (pega DANFE em PDF)
#                         + registrar_leitura grava a coluna 'classe'
#  finance/estatisticas.py: estatisticas_leituras_qr -> assertividade SO' de cupom
# PRE-REQUISITO: migracao 032 aplicada (coluna 'classe' existe).
# RODE NA RAIZ DO REPO: bash add_classe_qr.sh
# ============================================================================
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f finance/nfce_qr.py ] && [ -f finance/estatisticas.py ] || { echo "ERRO: rode na raiz."; exit 1; }
echo "==> 2/4 Ja aplicado?"; if grep -q '"cupom_lido"' finance/nfce_qr.py; then echo "JA EXISTE. Nada a fazer."; exit 0; fi
cp finance/nfce_qr.py finance/nfce_qr.py.bak; cp finance/estatisticas.py finance/estatisticas.py.bak

echo "==> 3/4 Editando finance/*..."
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

# ---------- finance/nfce_qr.py ----------
qr_edits = []

# E1: ler_chave_de_pdf -> tenta TEXTO (chave da DANFE) antes do QR renderizado
qr_edits.append(("pdf-texto",
'''def ler_chave_de_pdf(pdf_bytes: bytes, max_paginas: int = 3) -> str | None:
    """Le o QR de um PDF de NFC-e: converte as primeiras paginas em imagem
    (PyMuPDF, sem dependencia de poppler) e procura a chave. Tolerante a falha."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # noqa: BLE001
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None
    try:
        for i in range(min(max_paginas, doc.page_count)):
            try:
                png = doc[i].get_pixmap(dpi=200).tobytes("png")
            except Exception:  # noqa: BLE001
                continue
            chave = ler_chave_da_imagem(png)
            if chave:
                return chave
        return None
    finally:
        doc.close()''',
'''def ler_chave_de_pdf(pdf_bytes: bytes, max_paginas: int = 3) -> str | None:
    """Le a chave de uma NFC-e/NF-e em PDF. Tenta primeiro o TEXTO do PDF (a
    DANFE digital traz a chave de 44 digitos escrita, as vezes em grupos de 4) e,
    so' se nao achar, renderiza a pagina e le o QR. extrair_chave valida o modelo
    (65/55), entao numero solto nao vira falso positivo. Tolerante a falha."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # noqa: BLE001
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None
    try:
        # 1) TEXTO (custo zero): a DANFE digital traz a chave de 44 digitos escrita
        # (as vezes em grupos de 4). Procura runs de 44+ digitos (apos remover os
        # espacos) e valida o modelo 65/55 nas posicoes 20-21, igual extrair_chave.
        import re as _re
        for i in range(min(max_paginas, doc.page_count)):
            try:
                txt = "".join((doc[i].get_text() or "").split())
            except Exception:  # noqa: BLE001
                txt = ""
            for run in _re.findall(r"\\d{44,}", txt):
                for s in range(0, len(run) - 43):
                    cand = run[s:s + 44]
                    if cand[20:22] in ("65", "55"):
                        return cand
        # 2) QR renderizado (pega PDF escaneado/sem texto, como antes).
        for i in range(min(max_paginas, doc.page_count)):
            try:
                png = doc[i].get_pixmap(dpi=200).tobytes("png")
            except Exception:  # noqa: BLE001
                continue
            chave = ler_chave_da_imagem(png)
            if chave:
                return chave
        return None
    finally:
        doc.close()'''))

# E2: registrar_leitura -> calcula 'classe' logo apos info
qr_edits.append(("classe-calc",
'''        m = metadados(chave) if chave else {}
        info = img_info or {}''',
'''        m = metadados(chave) if chave else {}
        info = img_info or {}
        if chave:
            classe = "cupom_lido"
        elif (media_type or "") == "application/pdf":
            classe = "pdf_indefinido"
        elif (media_type or "").startswith("image/"):
            classe = "cupom_sem_qr"
        else:
            classe = "outro"'''))

# E3: registrar_leitura -> insere a coluna classe
qr_edits.append(("classe-insert",
'''                   (conta_id, chave, uf, cnpj_emitente, data_emissao, media_type,
                    leu, img_largura, img_altura, img_bytes)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (conta_id, chave, m.get("uf"), m.get("cnpj_emitente"),
                 m.get("data_emissao"), media_type, bool(chave),
                 info.get("largura"), info.get("altura"), info.get("bytes")),''',
'''                   (conta_id, chave, uf, cnpj_emitente, data_emissao, media_type,
                    leu, img_largura, img_altura, img_bytes, classe)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (conta_id, chave, m.get("uf"), m.get("cnpj_emitente"),
                 m.get("data_emissao"), media_type, bool(chave),
                 info.get("largura"), info.get("altura"), info.get("bytes"), classe),'''))

patch("finance/nfce_qr.py", qr_edits)

# ---------- finance/estatisticas.py ----------
est_edits = []

# E4: adiciona a query por classe dentro do 'with' (apos a query por tipo)
est_edits.append(("classe-query",
'''        ).fetchall()

    tipos = []''',
'''        ).fetchall()
        _classes = dict(conn.execute(
            "select coalesce(classe,'?'), count(*) from qr_leituras group by 1"
        ).fetchall())

    tipos = []'''))

# E5: troca o return pelo honesto (assertividade so' de cupom + breakdown de classes)
est_edits.append(("return-honesto",
'''    return {
        "tipos": tipos,
        "total_leituras": tot,
        "acertividade_geral_pct": int(round(100 * ok / tot)) if tot else 0,
    }''',
'''    def _cl(k):
        return int(_classes.get(k, 0))
    cupom_lido = _cl("cupom_lido")
    cupom_sem_qr = _cl("cupom_sem_qr")
    cupons_fiscais = cupom_lido + cupom_sem_qr
    assert_cupom = int(round(100 * cupom_lido / cupons_fiscais)) if cupons_fiscais else 0
    return {
        "tipos": tipos,
        "total_leituras": tot,
        # assertividade agora e' SO' sobre cupom fiscal (cupom_lido / cupons fiscais);
        # comprovante/pdf_indefinido/outro NAO entram no denominador.
        "acertividade_geral_pct": assert_cupom,
        "assertividade_cupom_pct": assert_cupom,
        "cupons_fiscais": cupons_fiscais,
        "classes": {
            "cupom_lido": cupom_lido,
            "cupom_sem_qr": cupom_sem_qr,
            "pdf_indefinido": _cl("pdf_indefinido"),
            "outro": _cl("outro"),
            "nao_classificado": _cl("?"),
        },
    }'''))

patch("finance/estatisticas.py", est_edits)
PYEOF

echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile finance/nfce_qr.py finance/estatisticas.py 2>/tmp/_q_err || { echo "ERRO compila:"; cat /tmp/_q_err; mv finance/nfce_qr.py.bak finance/nfce_qr.py; mv finance/estatisticas.py.bak finance/estatisticas.py; exit 1; }
grep -q '"cupom_lido"' finance/nfce_qr.py && grep -q "assertividade_cupom_pct" finance/estatisticas.py || { echo "ERRO: nao aplicou."; mv finance/nfce_qr.py.bak finance/nfce_qr.py; mv finance/estatisticas.py.bak finance/estatisticas.py; exit 1; }
echo "    OK compila."
git add finance/nfce_qr.py finance/estatisticas.py
git diff --cached --quiet || { git commit -m "feat: classe na leitura QR (honesta) + leitor PDF le texto da DANFE"; git push; }
rm -f finance/nfce_qr.py.bak finance/estatisticas.py.bak
echo "PRONTO (finance). Aplicar migracao 032 ANTES de deployar."
