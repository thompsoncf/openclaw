"""Ícones dos serviços de evento — SVG de traço, desenhado aqui.

Serviço não é produto: não tem embalagem pra fotografar. A foto de banco de
imagem custava rede, sumia na impressão sem fundo e, no fim, metade dos itens
ficava SEM foto mesmo (ninguém cadastra foto de "DJ" ou de "mão de obra de
entradas") — e a linha do orçamento desalinhava. Ícone de traço imprime nítido
em qualquer tamanho, não depende de rede e sai igual em fotocópia P&B.

A escolha é automática e em três degraus, do mais específico pro mais genérico:

  1. pelo NOME do serviço  ("DJ" -> som, "LOCAÇÃO COZINHA" -> cozinha)
  2. pela CATEGORIA        (Buffet -> cloche, Locação de espaço -> salão)
  3. "outros"              (a estrela, que nunca deixa o item sem selo)

O vendedor pode fixar um ícone no item do catálogo; aí o escolhido manda e nada
disso roda. Mesma ideia do vocabulário que escolhia a foto, sem a rede no meio.

Uso:
    from finance import icones_servico as ics
    ics.svg(ics.escolher("DJ", "Serviços terceirizados"), px=24)
"""
from __future__ import annotations

import unicodedata

# chave -> (rótulo humano, corpo do SVG em viewBox 24x24)
ICONES: dict[str, tuple[str, str]] = {
    "espaco": ("Espaço / salão",
        '<path d="M2.5 10.5 12 4l9.5 6.5"/><path d="M4.5 10.5V20"/>'
        '<path d="M19.5 10.5V20"/><path d="M2.5 20h19"/>'
        '<path d="M8 20v-4.5a4 4 0 0 1 8 0V20"/>'),
    "buffet": ("Buffet",
        '<path d="M2.8 18.5h18.4"/><path d="M4.8 18.5a7.2 7.2 0 0 1 14.4 0"/>'
        '<path d="M12 8V6.2"/><circle cx="12" cy="5" r="1.1"/>'),
    "moveis": ("Móveis e utensílios",
        # cadeira de PERFIL: encosto, uma travessa, assento e pé da frente. Uma
        # travessa só — com duas, em 20px, o desenho empasta.
        '<path d="M7.6 20.6V4.6"/><path d="M7.6 13h8.8"/><path d="M16.4 13v7.6"/>'
        '<path d="M7.6 6.8h5.8"/>'),
    "equipe": ("Equipe / terceirizados",
        '<circle cx="12" cy="12" r="2"/><path d="M10.2 10.8 4 7.2v9.6l6.2-3.6"/>'
        '<path d="M13.8 10.8 20 7.2v9.6l-6.2-3.6"/>'),
    "som": ("Som / DJ",
        '<path d="M4 14.5v-2.6a8 8 0 0 1 16 0v2.6"/>'
        '<rect x="2.2" y="13" width="4.2" height="7" rx="1.7"/>'
        '<rect x="17.6" y="13" width="4.2" height="7" rx="1.7"/>'),
    "cozinha": ("Cozinha",
        '<path d="M3.6 10.4h16.8v3.2a6 6 0 0 1-6 6h-4.8a6 6 0 0 1-6-6z"/>'
        '<path d="M20.4 12h2.2"/><path d="M3.6 12H1.4"/>'
        '<path d="M9.4 7.6c0-1.6 1-1.6 1-3.2"/><path d="M13.6 7.6c0-1.6 1-1.6 1-3.2"/>'),
    "bolo": ("Bolo e doces",
        '<path d="M3.4 20.6h17.2"/><path d="M4.8 20.6v-7.4h14.4v7.4"/>'
        '<path d="M4.8 13.2c1.7 0 1.7-1.8 3.4-1.8s1.8 1.8 3.5 1.8 1.8-1.8 3.5-1.8 1.6 1.8 3 1.8"/>'
        '<path d="M12 9.6V7.4"/><circle cx="12" cy="6.1" r="1"/>'),
    "bebidas": ("Bebidas",
        '<path d="M7.8 4h8.4l-1.1 5.4a3.3 3.3 0 0 1-6.2 0z"/>'
        '<path d="M12 12.8v6.4"/><path d="M8.8 20.2h6.4"/>'),
    "decoracao": ("Decoração",
        '<path d="M8.6 20.5h6.8l1-5.4H7.6z"/><path d="M12 15.1V9.4"/>'
        '<path d="M12 12.6 8.9 10.2"/><path d="M12 12.6l3.1-2.4"/>'
        '<circle cx="12" cy="7.6" r="1.7"/><circle cx="7.7" cy="9.2" r="1.4"/>'
        '<circle cx="16.3" cy="9.2" r="1.4"/>'),
    "foto": ("Fotografia e filmagem",
        '<rect x="2.8" y="7" width="18.4" height="12.4" rx="2.2"/>'
        '<circle cx="12" cy="13.2" r="3.2"/><path d="M9 7l1.4-2.2h3.2L15 7"/>'),
    "luz": ("Iluminação",
        '<path d="M12 3v2.6"/><path d="M5.6 12.4a6.4 6.4 0 0 1 12.8 0z"/>'
        '<path d="M5.6 12.4h12.8"/><path d="M9.4 12.4v1.8a2.6 2.6 0 0 0 5.2 0v-1.8"/>'),
    "jantar": ("Jantar / mesa posta",
        '<path d="M7 3.5v6.2a2 2 0 0 0 2 2v8.8"/><path d="M7 3.5v5.4"/>'
        '<path d="M10.4 3.5v5.4"/>'
        '<path d="M16.6 3.5c-1.6 1.1-2.2 3.2-2.2 5.3s.8 3.2 2.2 3.2v8.5"/>'),
    "seguranca": ("Segurança",
        '<path d="M12 3.2 19 6.1v5.8c0 4.4-2.9 7.8-7 8.9-4.1-1.1-7-4.5-7-8.9V6.1z"/>'
        '<path d="m9.2 12.2 2 2 3.6-3.8"/>'),
    "transporte": ("Transporte",
        '<path d="M2.8 16.4V8.2h10.6l4 4h3.8v4.2"/><path d="M6.6 16.4h4.4"/>'
        '<circle cx="4.8" cy="17.4" r="1.9"/><circle cx="17.2" cy="17.4" r="1.9"/>'),
    "convidados": ("Convidados / recepção",
        '<circle cx="9" cy="8.4" r="2.8"/><path d="M3.6 19.4a5.4 5.4 0 0 1 10.8 0"/>'
        '<path d="M16.2 6.2a2.8 2.8 0 0 1 0 5.4"/>'
        '<path d="M17.4 13.6a5.4 5.4 0 0 1 3 4.9"/>'),
    "outros": ("Outros",
        '<path d="M12 3.4 14.6 9l6 .9-4.4 4.2 1.1 5.9L12 17.2 6.7 20l1.1-5.9L3.4 9.9 9.4 9z"/>'),
}

PADRAO = "outros"

# Palavra no nome do serviço -> ícone. A ordem importa: a lista é varrida de
# cima pra baixo e a PRIMEIRA que aparecer no nome ganha, então o específico vem
# antes do genérico ("mesa posta" antes de "mesa").
_POR_NOME: tuple[tuple[str, str], ...] = (
    ("dj", "som"), ("som", "som"), ("audio", "som"), ("sonoriza", "som"),
    ("banda", "som"), ("musica", "som"), ("musico", "som"), ("cantor", "som"),
    ("cozinha", "cozinha"), ("chef", "cozinha"), ("churrasq", "cozinha"),
    ("bolo", "bolo"), ("doce", "bolo"), ("bem casado", "bolo"),
    ("bem-casado", "bolo"), ("sobremesa", "bolo"), ("confeit", "bolo"),
    ("bebida", "bebidas"), ("drink", "bebidas"), ("bar ", "bebidas"),
    ("barman", "bebidas"), ("chopp", "bebidas"), ("open bar", "bebidas"),
    ("agua", "bebidas"), ("refrigerante", "bebidas"),
    ("decora", "decoracao"), ("flor", "decoracao"), ("arranjo", "decoracao"),
    ("cenario", "decoracao"), ("balao", "decoracao"), ("painel", "decoracao"),
    ("foto", "foto"), ("filmagem", "foto"), ("video", "foto"),
    ("drone", "foto"), ("cabine", "foto"),
    ("ilumina", "luz"), ("luz", "luz"), ("lustre", "luz"), ("led", "luz"),
    ("garcom", "equipe"), ("garcon", "equipe"), ("equipe", "equipe"),
    ("mao de obra", "equipe"), ("recepcionista", "equipe"), ("copeira", "equipe"),
    ("cerimonial", "equipe"), ("manobrista", "equipe"), ("monitor", "equipe"),
    ("seguranca", "seguranca"), ("vigilan", "seguranca"), ("brigadista", "seguranca"),
    ("transporte", "transporte"), ("van", "transporte"), ("onibus", "transporte"),
    ("frete", "transporte"),
    ("mesa posta", "jantar"), ("jantar", "jantar"), ("almoco", "jantar"),
    ("prato", "jantar"), ("entrada", "jantar"), ("talher", "jantar"),
    ("buffet", "buffet"), ("bufe", "buffet"), ("coquetel", "buffet"),
    ("petisco", "buffet"), ("salgado", "buffet"), ("comida", "buffet"),
    ("cadeira", "moveis"), ("mesa", "moveis"), ("movel", "moveis"),
    ("moveis", "moveis"), ("toalha", "moveis"), ("lounge", "moveis"),
    ("poltrona", "moveis"), ("banqueta", "moveis"), ("tenda", "moveis"),
    ("utensilio", "moveis"), ("louca", "moveis"), ("taca", "moveis"),
    ("espaco", "espaco"), ("salao", "espaco"), ("locacao", "espaco"),
    ("pacote", "espaco"), ("chacara", "espaco"), ("sitio", "espaco"),
    ("piscina", "espaco"), ("area", "espaco"),
    ("convidado", "convidados"), ("recepcao", "convidados"), ("lista", "convidados"),
)

# Categoria do catálogo -> ícone (o degrau 2).
_POR_CATEGORIA: dict[str, str] = {
    "locacao de espaco": "espaco",
    "buffet": "buffet",
    "locacao de moveis e utensilios": "moveis",
    "servicos terceirizados": "equipe",
    "outros": "outros",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def escolher(nome: str | None = None, categoria: str | None = None,
             fixo: str | None = None) -> str:
    """A chave do ícone do serviço. Nunca devolve vazio — no pior caso, 'outros'.

    `fixo` é o que o vendedor escolheu na mão no catálogo; se veio e existe,
    manda em tudo.
    """
    if fixo and fixo in ICONES:
        return fixo
    alvo = _norm(nome)
    if alvo:
        for termo, chave in _POR_NOME:
            if termo in alvo:
                return chave
    cat = _POR_CATEGORIA.get(_norm(categoria))
    return cat or PADRAO


def rotulo(chave: str) -> str:
    return ICONES.get(chave, ICONES[PADRAO])[0]


def svg(chave: str, px: int = 24, cor: str = "currentColor",
        traco: float = 1.7) -> str:
    """O SVG pronto pra injetar no HTML. Sem id/class: pode repetir na página."""
    corpo = ICONES.get(chave, ICONES[PADRAO])[1]
    return (f'<svg viewBox="0 0 24 24" width="{px}" height="{px}" fill="none" '
            f'stroke="{cor}" stroke-width="{traco}" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{corpo}</svg>')


def paleta() -> list[dict]:
    """A biblioteca inteira, pro seletor do catálogo."""
    return [{"chave": k, "rotulo": v[0], "svg": svg(k, px=22)}
            for k, v in ICONES.items()]
