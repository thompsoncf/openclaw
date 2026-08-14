"""Tokens da marca do Zaq — um lugar só, para o painel inteiro.

Antes existiam CINCO declarações `:root` independentes (portal, admin, agenda,
cockpit, app), com valores diferentes e, pior, nomes diferentes pra mesma coisa:
`--card2` num arquivo e `--card-2` no outro, `--mut` aqui e `--txt-mut` ali. Cada
tela nova herdava o vocabulário do arquivo em que nasceu, e o painel foi ficando
com 394 cores distintas.

Aqui os valores vêm da landing (zaq-landing/index.html) — a marca que o site já
usa. O app rodava a paleta anterior, do hortifruti (#0e0e0f / #1d9e75), e quem
saía do site e entrava no painel achava que tinha trocado de produto.

**Os apelidos são de propósito.** Todo nome que já existia no código continua
válido, apontando pro token novo. Sem isso, migrar exigiria reescrever 2.125
usos de `var()` de uma vez — e qualquer um esquecido viraria cor vazia, que o
navegador resolve como preto sobre preto. Com os apelidos, o valor muda no lugar
certo e nada quebra pelo caminho.

Contraste conferido (WCAG, sobre `--bg`): `--txt` 16,9 · `--txt-mut` 7,1 ·
`--verde` 9,7 · `--ambar` 8,7 · `--coral` 5,2 — todos acima de 4,5, que é o
mínimo pra texto de corpo.
"""

# A marca, como a landing define. Nomes canônicos primeiro; apelidos depois.
_TOKENS = """
  color-scheme: dark;

  /* ---- superfícies ---- */
  --bg:#0A0F0C; --bg-2:#0E1512; --surface:#121A16; --line:#1E2A23;

  /* ---- texto ---- */
  --text:#EAF2ED; --text-dim:#8FA197; --text-faint:#5E6F66;

  /* ---- verde da marca ----
     Um verde só, como no site. A separação verde/verde-claro existia porque o
     verde antigo (#1d9e75) era escuro demais pra ler como texto; este não é. */
  --neon:#25D366; --neon-bright:#46F58A; --neon-deep:#0FA85A;
  --neon-fraco:rgba(37,211,102,.10); --neon-borda:#1E4A3A; --neon-fundo:#10241A;

  /* Tinta sobre o verde cheio. Branco sobre #25D366 dá 1,98 de contraste — some.
     Esta tinta dá 9,47. É o token que os botões verdes usam. */
  --sobre-verde:#04150C;

  /* ---- semânticos ---- */
  --ambar:#E0A32E; --coral:#E0574F; --azul:#229ED9; --roxo:#C9A3E0; --zap:#25D366;
  --ambar-borda:#5A4520; --ambar-fundo:#241C0F;
  --coral-borda:#5A2B2B; --coral-fundo:#241313;
  --azul-borda:#1B3A4A;  --azul-fundo:#0D1B23;

  /* ---- tipografia ---- */
  --display:"Bricolage Grotesque",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --body:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;

  /* ---- apelidos: o vocabulário que o código já usa ---- */
  --card:var(--surface); --card-2:var(--bg-2); --card2:var(--bg-2);
  --borda:var(--line); --bord:var(--line);
  --txt:var(--text); --txt-mut:var(--text-dim); --mut:var(--text-dim);
  --verde:var(--neon); --verde-claro:var(--neon); --verde-cl:var(--neon);
  --verde2:var(--neon-bright); --verde-hover:var(--neon-bright);
  --amar:var(--ambar); --amber:var(--ambar); --verm:var(--coral);
  --fonte:var(--body); --ink:var(--sobre-verde);
"""

# As fontes da marca. Sem elas o navegador cai no stack de sistema e a página
# fica correta mas sem a voz do site — então a tag entra junto dos tokens.
FONTES = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
          '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
          '<link href="https://fonts.googleapis.com/css2?'
          'family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800'
          '&family=Inter:wght@400;500;600'
          '&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">')

# Base que todas as telas herdam: título em Bricolage, corpo em Inter, número em
# mono. Antes cada arquivo repetia — e divergia — na própria regra de body.
_BASE = """
  body{font-family:var(--body);background:var(--bg);color:var(--txt);
       -webkit-font-smoothing:antialiased}
  h1,h2,h3,h4,.logo,.side-logo{font-family:var(--display);letter-spacing:-.02em}
  code,kbd,samp{font-family:var(--mono)}
"""


def css(com_fontes: bool = True, com_base: bool = True) -> str:
    """O bloco pronto pra injetar no <head>. `com_base=False` pra quem só quer
    as variáveis (o Cockpit, por exemplo, define a própria tipografia)."""
    partes = [FONTES] if com_fontes else []
    partes.append("<style>:root{" + _TOKENS + "}" + (_BASE if com_base else "") + "</style>")
    return "".join(partes)


def variaveis(com_base: bool = True) -> str:
    """O `:root{...}` cru (sem <style>), pra concatenar num CSS que já existe.

    `com_base=True` traz junto a tipografia da marca — título em Bricolage, corpo
    em Inter, número em mono. Sem ela os tokens de fonte ficam definidos e nada
    os usa, que foi exatamente o que aconteceu na primeira passada da migração:
    a cor virou e a fonte continuou a de sistema.
    """
    return ":root{" + _TOKENS + "}" + (_BASE if com_base else "")
