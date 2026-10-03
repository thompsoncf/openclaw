"""O TEMA DO PAINEL de cada conta e de cada pessoa (escuro, claro, misto, automático).

Desenho aprovado pelo dono em 03/10/2026: docs/mockups/zaq_temas.html. As cores
moram em `web/tema.py`; aqui fica só QUEM vê QUAL tema.

AS REGRAS:
- A empresa escolhe o tema dela (`contas.tema`); só dono e gestor mudam.
- Cada pessoa da equipe pode seguir a empresa (`membros.tema` vazio) ou ter o dela.
- Vazio em tudo = escuro, que é o painel de antes, sem nenhuma diferença.

O PORTÃO (fase 1). Enquanto as telas ainda têm cor escrita à mão (~2.800 em
`web/`, ver o mockup), o claro mostra pedaços escuros. Por isso a escolha só
existe pra conta com `contas.temas_piloto` (a migração 202610031411_aparencia_temas marca o Espaço Pelle,
conta 39). Pra qualquer outra conta tudo aqui devolve "escuro" e a tela
Aparência nem aparece no menu.

TOLERANTE DE PROPÓSITO. Esta leitura roda em TODA página do painel (`_render`).
Se a coluna ainda não existir, ou o banco piscar, o painel abre no escuro, como
sempre abriu; o tema nunca derruba uma tela.
"""
from __future__ import annotations

import time as _time

from web.tema import TEMAS

PADRAO = "escuro"

ROTULOS = {
    "escuro": ("Escuro", "Como é hoje."),
    "claro": ("Claro", "Fundo claro em tudo."),
    "misto": ("Misto", "Menu escuro, área de trabalho clara."),
    "auto": ("Automático", "Segue o celular ou o computador de cada um."),
}

#: Quem muda o tema da empresa. A pessoa muda só o dela.
PAPEIS_DA_EMPRESA = ("dono", "gestor")

# A marca de piloto quase nunca muda (é a migração que liga). Guardada 5 min por
# processo, como o vocabulário do nicho (finance.raio_x_perfil.voc_da_conta):
# assim a conta que NÃO é piloto, que é quase todas, não paga consulta nenhuma
# por página depois da primeira.
_PILOTO_CACHE: dict[int, tuple[bool, float]] = {}
_PILOTO_TTL = 300
_FALHA_TTL = 15


def normalizar(tema) -> str | None:
    """O tema se for um dos quatro; senão None (que quer dizer "não escolheu")."""
    t = (tema or "").strip().lower()
    return t if t in TEMAS else None


def eh_piloto(pool, conta_id) -> bool:
    if not conta_id:
        return False
    agora = _time.monotonic()
    c = _PILOTO_CACHE.get(conta_id)
    if c and agora - c[1] <= _PILOTO_TTL:
        return c[0]
    try:
        with pool.connection() as cx:
            r = cx.execute("select temas_piloto from contas where id=%s", (conta_id,)).fetchone()
        sim = bool(r and r[0])
    except Exception:  # noqa: BLE001 — sem a coluna (migração não rodou) é "não"
        # Guardado por pouco tempo: uma falha passageira não pode deixar a piloto
        # 5 minutos no escuro, sem o item Aparência, como se fosse um defeito.
        _PILOTO_CACHE[conta_id] = (False, agora - _PILOTO_TTL + _FALHA_TTL)
        return False
    _PILOTO_CACHE[conta_id] = (sim, agora)
    return sim


def ler(pool, conta_id, membro_id=None) -> dict:
    """O tema que vale agora, e de onde veio.

    {'piloto': bool, 'empresa': tema da conta, 'meu': tema da pessoa ou None,
     'tema': o que a página usa}
    """
    fora = {"piloto": False, "empresa": PADRAO, "meu": None, "tema": PADRAO}
    if not eh_piloto(pool, conta_id):
        return fora
    try:
        with pool.connection() as cx:
            r = cx.execute(
                """select co.tema,
                          (select m.tema from membros m where m.id = %s and m.conta_id = co.id)
                     from contas co where co.id = %s""",
                (membro_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001 — o tema nunca derruba a tela
        return {**fora, "piloto": True}
    empresa = normalizar(r[0] if r else None) or PADRAO
    meu = normalizar(r[1] if r else None) if membro_id else None
    return {"piloto": True, "empresa": empresa, "meu": meu, "tema": meu or empresa}


def salvar_empresa(pool, conta_id, tema) -> bool:
    """Grava o tema da empresa. Só em conta piloto e só um dos quatro."""
    t = normalizar(tema)
    if not t or not eh_piloto(pool, conta_id):
        return False
    with pool.connection() as cx:
        n = cx.execute("update contas set tema=%s where id=%s and temas_piloto",
                       (None if t == PADRAO else t, conta_id)).rowcount
        cx.commit()
    return n == 1


def salvar_meu(pool, conta_id, membro_id, tema) -> bool:
    """Grava o tema da pessoa. `tema` vazio = volta a seguir a empresa."""
    if not membro_id or not eh_piloto(pool, conta_id):
        return False
    t = normalizar(tema)
    if tema and not t:
        return False
    with pool.connection() as cx:
        n = cx.execute("update membros set tema=%s where id=%s and conta_id=%s",
                       (t, membro_id, conta_id)).rowcount
        cx.commit()
    return n == 1


def esquecer_cache() -> None:
    """Pros testes: a marca de piloto muda no meio deles."""
    _PILOTO_CACHE.clear()
