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

  /* ---- a largura da página ----
     Um token porque em 22/09/2026 o painel tinha CINCO larguras diferentes: 960
     (Serviços e o topo), 1040 (Renovações), 1120 (o cadastro), 1180 (o Cockpit) e
     o Raio-X sem teto nenhum, do tamanho do próprio conteúdo. O dono reparou
     trocando de tela — "a página ficar tamanho do raio-x" —, e a resposta certa
     pra isso não é acertar uma tela: é ter um lugar só onde a largura é decidida.
     1180 é a maior que já existia, e é o que o Raio-X pede no desktop. */
  --pag:1180px;

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
#
# Servidas por NÓS, não pelo Google. A folha do fonts.googleapis.com era um
# <link rel=stylesheet> em outro domínio: bloqueia a renderização e cobra DNS +
# TLS + download de um host que não controlamos antes de a tela pintar. No app
# do vendedor, aberto em 4G no meio da rua, isso é a maior fatia da abertura —
# e um service worker não resolve, porque resposta cross-origin vem opaca.
#
# São três arquivos, não oito: o Google serve a MESMA fonte variável repetida
# uma vez por peso (conferido por md5 — 8 URLs, 3 arquivos distintos). Aqui cada
# família entra uma vez, com a faixa de pesos declarada. 160 KB no total, contra
# 428 KB do jeito que vinha.
#
# `swap` mantém o texto legível desde o primeiro quadro, com a fonte de sistema,
# e troca quando a nossa chega — a página nunca fica em branco esperando fonte.
FONTES = ("<style>"
          "@font-face{font-family:'Bricolage Grotesque';font-style:normal;"
          "font-weight:200 800;font-stretch:100%;font-display:swap;"
          "src:url(/estatico/fontes/bricolage.woff2) format('woff2')}"
          "@font-face{font-family:'Inter';font-style:normal;"
          "font-weight:100 900;font-display:swap;"
          "src:url(/estatico/fontes/inter.woff2) format('woff2')}"
          "@font-face{font-family:'JetBrains Mono';font-style:normal;"
          "font-weight:100 800;font-display:swap;"
          "src:url(/estatico/fontes/jetbrains.woff2) format('woff2')}"
          "</style>")

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


# ---------------------------------------------------------------------------
# TEMAS: escuro, claro, misto e automático
# (docs/mockups/zaq_temas.html, aprovado pelo dono em 03/10/2026; fase 1)
#
# O escuro é o `_TOKENS` acima, sem nenhuma mudança: quem não escolhe tema
# recebe o HTML e o CSS de antes, byte a byte. Os outros temas só valem quando
# o servidor escreve `<html data-tema="...">` (web/portal.py, `_BASE`), e isso
# só acontece pra conta marcada como piloto (contas/aparencia.py).
#
# **Por que os apelidos se repetem dentro do misto.** Variável CSS que aponta
# pra outra (`--card:var(--surface)`) é resolvida no elemento ONDE FOI DECLARADA
# e herdada já resolvida. Declarada só no `:root`, o `--card` do menu herdaria
# o valor claro da página, mesmo com o `--surface` do menu escuro. Por isso o
# bloco do menu redeclara os apelidos junto: ali eles resolvem com os valores
# escuros. No `<html>` não precisa (é o mesmo elemento do `:root`), mas repetir
# não custa e deixa os três blocos iguais de ler.
# ---------------------------------------------------------------------------

#: As cores do escuro, recortadas do próprio `_TOKENS` pra não existir uma
#: segunda cópia que poderia divergir. Vão do `color-scheme` até a tipografia.
_ESCURO_CORES = _TOKENS[_TOKENS.index("color-scheme"):_TOKENS.index("/* ---- tipografia")]

#: Os apelidos (`--card`, `--txt`, `--verde`...), também recortados do `_TOKENS`.
_APELIDOS = _TOKENS[_TOKENS.index("/* ---- apelidos"):]

# O claro. Mesmos nomes do escuro, outros valores. Contraste conferido (WCAG,
# sobre `--bg-2`, que é o fundo mais escuro onde texto aparece): `--text` 16,0 ·
# `--text-dim` 6,1 · `--neon` 4,7 · `--ambar` 5,0 · `--coral` 5,1 · `--azul` 5,2.
#
# O verde muda de tom: o #25D366 da marca dá 1,9 como texto sobre branco e some.
# O #0B7A3E é o mesmo verde mais escuro, e por isso a tinta do botão verde vira
# branca (5,4 sobre ele); a tinta escura de antes daria 3,2.
_CLARO = """
  color-scheme: light;

  /* ---- superfícies ---- */
  --bg:#F3F6F4; --bg-2:#E9EFEB; --surface:#FFFFFF; --line:#D7E0DA;

  /* ---- texto ---- */
  --text:#101A14; --text-dim:#4D5E54; --text-faint:#7B8B82;

  /* ---- verde da marca, no tom que se lê sobre branco ---- */
  --neon:#0B7A3E; --neon-bright:#096B36; --neon-deep:#075C2E;
  --neon-fraco:rgba(11,122,62,.10); --neon-borda:#A9D9BC; --neon-fundo:#E2F3E8;
  --sobre-verde:#FFFFFF;

  /* ---- semânticos ---- */
  --ambar:#8F6200; --coral:#B3372F; --azul:#17689F; --roxo:#7A4AA0; --zap:#0B7A3E;
  --ambar-borda:#E6CD8F; --ambar-fundo:#FBF1DA;
  --coral-borda:#EBB9B4; --coral-fundo:#FBE8E6;
  --azul-borda:#B4D2E7;  --azul-fundo:#E4F0F8;
"""

#: Os temas que existem. `escuro` é o padrão e não escreve nada no `<html>`.
TEMAS = ("escuro", "claro", "misto", "auto")

#: O que fica escuro no misto: o menu lateral, a barra de baixo do celular e a
#: folha do "Mais", que é o menu do celular aberto.
_MENU = (".side", ".btmnav", ".mais-sheet")

# A logo tem a cor escrita no próprio SVG (#3ee0a6, um verde-água claro), que
# some sobre fundo claro. Regra de CSS ganha de atributo de SVG, então dá pra
# trocar só onde o fundo é claro, sem mexer na logo de quem está no escuro.
_LOGO = ('.logo svg path[stroke="#3ee0a6"]{stroke:var(--neon)}'
         '.logo svg path[fill="#3ee0a6"]{fill:var(--neon)}')


def _logo_em(prefixos: list[str]) -> str:
    regras = []
    for regra in _LOGO.split("}")[:-1]:
        seletor, corpo = regra.split("{")
        regras.append(",".join(f"{p} {seletor}" for p in prefixos) + "{" + corpo + "}")
    return "".join(regras)


def temas() -> str:
    """O CSS dos temas claro, misto e automático, pra concatenar depois de
    `variaveis()`. Sem `data-tema` no `<html>`, nada aqui se aplica."""
    claro = _CLARO + _APELIDOS
    escuro = _ESCURO_CORES + _APELIDOS
    claro_sel = 'html[data-tema="claro"],html[data-tema="misto"]'
    menu_sel = ",".join(f'html[data-tema="misto"] {m}' for m in _MENU)
    return (
        claro_sel + "{" + claro + "}"
        + menu_sel + "{" + escuro + "}"
        # o automático segue o aparelho: claro se ele estiver claro, senão o escuro de sempre
        + '@media (prefers-color-scheme: light){html[data-tema="auto"]{' + claro + "}"
        + _logo_em(['html[data-tema="auto"]']) + "}"
        # a logo da barra de cima do celular fica sobre a página, que no misto é clara
        + _logo_em(['html[data-tema="claro"]', 'html[data-tema="misto"] .topo-mob'])
    )
