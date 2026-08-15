"""Selo da marca da empresa — um lugar só pra montar logo/iniciais.

A marca aparece no topo de várias telas (proposta, holerite, recibo do PDV,
cockpit). Antes cada uma remontava o HTML do "selo" na mão (logo quando há,
senão as iniciais sobre a cor da marca). Aqui isso vive uma vez:

- `avatar_html`  → só o quadrado (logo OU iniciais sobre a cor).
- `cabecalho_html` → o selo + nome (+ subtítulo), a faixa que PDV/cockpit usam.

Os dois escapam tudo e devolvem HTML pronto — dá pra injetar em template Jinja
(`{{ marca_avatar(marca)|safe }}`) ou concatenar num f-string. O dado da marca
vem de `empresa.marca_empresa` (dict com logo_url, cor, iniciais, nome).
"""
from html import escape


def iniciais(nome: str) -> str:
    """Até 2 iniciais do nome (fallback do avatar sem logo)."""
    partes = [p for p in (nome or "").replace("-", " ").split() if p]
    return "".join(p[0] for p in partes[:2]).upper() or ((nome or "?")[:1].upper() or "?")


def avatar_html(marca: dict, px: int = 40, raio: int = 8,
                so_logo: bool = False, ajuste: str = "contain") -> str:
    """Só o quadrado da marca. A logo quando existe; senão as iniciais sobre a
    cor (ou '' quando so_logo=True, pra documentos que só querem logo de verdade).
    `ajuste`: 'contain' (cabe inteira — o padrão, marca cortada é marca estragada)
    ou 'cover' (preenche o quadrado, pode cortar)."""
    marca = marca or {}
    logo = marca.get("logo_url")
    if logo:
        fit = "contain" if ajuste == "contain" else "cover"
        return (f'<img src="{escape(str(logo), quote=True)}" alt="" '
                f'style="width:{px}px;height:{px}px;border-radius:{raio}px;'
                f'object-fit:{fit};background:#fff;flex-shrink:0">')
    if so_logo:
        return ""
    cor = escape(str(marca.get("cor") or "#2f7d32"), quote=True)
    ini = escape(marca.get("iniciais") or iniciais(marca.get("nome") or ""))
    fs = max(10, round(px * 0.42))
    return (f'<span style="width:{px}px;height:{px}px;border-radius:{raio}px;'
            f'display:inline-flex;align-items:center;justify-content:center;'
            f'flex-shrink:0;font-weight:800;font-size:{fs}px;color:#fff;'
            f'background:{cor}">{ini}</span>')


def cabecalho_html(marca: dict, px: int = 32, raio: int = 8, sub: str = "",
                   nome=None, alinhar: str = "left", cor_nome: str = "inherit",
                   so_logo: bool = False, ajuste: str = "contain") -> str:
    """Selo + nome (+ subtítulo opcional). `alinhar`: 'left' | 'center' | 'right'
    (right empurra pra ponta com margin-left:auto). Devolve '' se so_logo e não há
    logo — assim quem quiser silêncio sem marca fica sem faixa."""
    marca = marca or {}
    selo = avatar_html(marca, px=px, raio=raio, so_logo=so_logo, ajuste=ajuste)
    if not selo:
        return ""
    nome_txt = escape(str(nome if nome is not None else (marca.get("nome") or "")))
    sub_html = (f'<small style="display:block;opacity:.7;font-size:.72rem">'
                f'{escape(str(sub))}</small>') if sub else ""
    ml = "margin-left:auto;" if alinhar == "right" else ""
    just = "justify-content:center;" if alinhar == "center" else ""
    return (f'<div style="display:flex;align-items:center;gap:.5rem;{ml}{just}">'
            f'{selo}<span><b style="color:{escape(cor_nome, quote=True)}">'
            f'{nome_txt}</b>{sub_html}</span></div>')


def registrar_jinja(env) -> None:
    """Deixa `marca_avatar` e `marca_cabecalho` disponíveis nos templates."""
    env.globals["marca_avatar"] = avatar_html
    env.globals["marca_cabecalho"] = cabecalho_html
