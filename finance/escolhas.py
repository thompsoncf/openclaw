"""As escolhas com toque: "de qual obra?" e "que etapa ficou pronta?" viram botões.

Pedido do dono em 02/10/2026, com o print de um concorrente: depois da nota lida,
em vez de perguntar por texto, o assistente manda um botão "Ver obras" que abre a
lista. E o mesmo pra etapa ("terminei a casa 2" -> a lista das etapas que faltam).

COMO A ESCOLHA ANDA:
1. A ferramenta `oferecer_escolha` (finance/tools_pj.py) monta as opções aqui e as
   deixa em `livro.escolha` — o mesmo jeito da foto da obra (`livro.midia_atual`).
2. Quem responde a mensagem manda os botões depois do texto:
   - WhatsApp: lista do Twilio (até 10 opções) ou botões (até 3, título até 20
     letras) — finance/whatsapp_interativo.py;
   - Telegram: teclado de respostas (telegram_bot.py).
3. O toque volta como TEXTO — o título da opção ("Casa 2") — e o agente segue a
   conversa como se a pessoa tivesse escrito. Não existe estado novo pra guardar.
Canal sem botão, ou botão que falhou: as opções vão escritas (`texto_das_opcoes`).

OS TÍTULOS são o que volta pro agente, então precisam achar a obra: o título é o
nome da obra cortado em 24 letras (o limite da lista do WhatsApp), sem reticências —
o agente acha a obra pelo começo do nome (`obras.obra_por_nome`).
"""
from __future__ import annotations

from . import obras as _ob

MAX_OPCOES = 10          # o limite da lista do WhatsApp
MAX_TITULO = 24          # idem, por linha
MAX_DESCRICAO = 72
DIVIDIR = "Dividir entre todas"


def _titulo(txt: str) -> str:
    return " ".join((txt or "").split())[:MAX_TITULO].rstrip()


def de_obra(pool, conta_id: int) -> dict | None:
    """As obras que recebem despesa (em obra ou prontas), as mais novas primeiro, e
    "Dividir entre todas" quando há duas ou mais em obra. Com quadra, entram as
    quadras (divide pelo m²). None sem obra nenhuma."""
    obras = [o for o in _ob.listar_obras(pool, conta_id, com_custos=False)
             if o["status"] in ("em_obra", "pronta")]
    if not obras:
        return None
    extras = []
    if sum(1 for o in obras if o["status"] == "em_obra") >= 2:
        extras.append({"titulo": DIVIDIR, "descricao": "partes iguais entre as obras em andamento"})
    try:
        from . import obra_grupos as og
        rot = og.rotulo(pool, conta_id)
        for g in og.listar_grupos(pool, conta_id)[:2]:
            extras.append({"titulo": _titulo(f"{g['nome']} (dividir)"),
                           "descricao": f"entre as casas da {rot.lower()}, pelo m²"})
    except Exception:  # noqa: BLE001 — sem quadras
        pass
    lugar = MAX_OPCOES - len(extras)
    opcoes = []
    vistos = set()
    for o in sorted(obras, key=lambda x: x["id"], reverse=True)[:lugar]:
        t = _titulo(o["nome"])
        if t.lower() in vistos:                     # dois nomes iguais nas 24 letras
            continue
        vistos.add(t.lower())
        desc = f"{o['rotulo_tipo']} · {o['pct']}%"
        if o["status"] == "pronta":
            desc += " · pronta"
        opcoes.append({"titulo": t, "descricao": desc[:MAX_DESCRICAO]})
    fora = len(obras) - len(opcoes)
    return {"tipo": "obra", "texto": "Toque na obra pra continuar." + (
                f" ({fora} obra{'s' if fora != 1 else ''} fora da lista: é só escrever o nome.)" if fora else ""),
            "botao": "Ver obras", "opcoes": opcoes + extras}


def de_etapa(pool, conta_id: int, obra: dict) -> dict | None:
    """As etapas que faltam na obra, na ordem. None se todas estão feitas."""
    faltam = [e for e in obra.get("etapas", []) if not e["concluida_em"]]
    if not faltam:
        return None
    opcoes = [{"titulo": _titulo(e["nome"]), "descricao": f"{e['peso']:g}% da obra"}
              for e in faltam[:MAX_OPCOES]]
    return {"tipo": "etapa", "obra": obra["nome"],
            "texto": f"Toque na etapa que ficou pronta em {obra['nome']}.",
            "botao": "Ver etapas", "opcoes": opcoes}


def texto_das_opcoes(escolha: dict) -> str:
    """As opções escritas, pro canal sem botão (ou o botão que falhou)."""
    linhas = [f"{n}. {o['titulo']}" for n, o in enumerate(escolha["opcoes"], start=1)]
    return "\n".join(linhas)
