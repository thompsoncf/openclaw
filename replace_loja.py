import re, sys

# Loja v3 (design claro do Claude Design). Pagina standalone (nao estende _BASE).
# Gerado – subir verbatim. Roda: python replace_loja.py
novo_loja = """<!doctype html>
<html lang="pt-br"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ fornecedor.nome }} · Zaq</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;padding:0}
  body{background:#eef0ec;font-family:'Poppins',system-ui,-apple-system,sans-serif;color:#23301f;-webkit-font-smoothing:antialiased}
  *{box-sizing:border-box}
  .noscroll{scrollbar-width:none;-ms-overflow-style:none}
  .noscroll::-webkit-scrollbar{display:none}
  .sz-body{display:grid;grid-template-columns:186px 1fr 312px}
  .sz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:13px}
  .sz-shelf{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px}
  .sz-shelfcard{flex:0 0 152px;background:#fff;border:1px solid #e8ece5;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(20,40,15,.05)}
  .sz-foto{background:#eef3ea;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center}
  .sz-chips{display:none}
  .sz-tab{display:none}
  .sz-tab.on{display:block}
  .qadd{position:absolute;bottom:6px;right:6px;width:32px;height:32px;border-radius:50%;background:#2f7d32;color:#fff;border:0;cursor:pointer;font-size:19px;line-height:1;box-shadow:0 2px 6px rgba(0,0,0,.22)}
  @media (max-width:900px){
    .sz-body{grid-template-columns:1fr}
    .sz-nav{display:none !important}
    .sz-cart{position:sticky;bottom:0;border-top:2px solid #31772f !important;border-radius:14px 14px 0 0 !important;box-shadow:0 -10px 26px rgba(0,0,0,.10)}
    .sz-chips{display:flex !important}
    .sz-vitrine{border-right:0 !important;padding:16px 6px !important}
  }
  @media (max-width:520px){ .sz-grid{grid-template-columns:1fr 1fr} }
</style>
</head><body>

<div style="max-width:1180px;margin:0 auto;background:#fff;min-height:100vh;box-shadow:0 0 40px rgba(0,0,0,.06)">
  <p style="text-align:center;color:#8a938a;padding:40px 20px">Loja v3 (Fase 1) - Design claro rodando!</p>
</div>

<script>console.log('Loja v3 template aplicado com sucesso');</script>
</body></html>
"""

ALVO = "web/portal.py"
src = open(ALVO, encoding="utf-8").read()
pat = r'_LOJA = """.*?(?:{% endblock %}|</html>)\s*"""'
achados = re.findall(pat, src, flags=re.DOTALL)
if len(achados) != 1:
    print(f"ABORTADO: encontrei {len(achados)} blocos _LOJA (esperado 1). Nada alterado."); sys.exit(1)
novo = re.sub(pat, lambda m: '_LOJA = """' + novo_loja + '"""', src, count=1, flags=re.DOTALL)
open(ALVO, "w", encoding="utf-8").write(novo)
print("OK: _LOJA v3 (claro) aplicado em web/portal.py")
