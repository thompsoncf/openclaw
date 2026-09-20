"""As respostas rápidas do vendedor: o que ele digita o dia inteiro, a um toque.

POR QUE EXISTE. Valores, endereço, o que está incluso, horário de visita — o
vendedor manda as mesmas frases dezenas de vezes por dia, e hoje copia do
WhatsApp do próprio celular. Isso é o hábito que o app veio substituir: o que sai
por fora não entra no funil e chega sem nome.

DUAS DONAS (migração 301):
    da CONTA (membro_id nulo) — o dono escreve, a equipe inteira vê. É o que a
        empresa diz ao cliente: preço, endereço, condições.
    do VENDEDOR — os jeitos dele, que só ele vê.
Sem as da conta, cada um manda uma versão diferente do preço. Sem as pessoais, a
lista nasce vazia e ninguém preenche.

O TEXTO NÃO SAI SOZINHO. A tela escreve na caixa de resposta e o vendedor manda —
ver `web/painel_cockpit`. Mandar no toque economizaria um segundo e custaria o
dia em que o preço de uma festa sai pro cliente de outra.
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger("openclaw.respostas")

#: teto de tamanho: é texto de mensagem, não documento
LIMITE_TEXTO = 2000
LIMITE_TITULO = 60
#: quantas cabem na lista de uma conta. Passou disso, virou catálogo — e catálogo
#: ninguém lê no meio de um atendimento.
TETO_POR_DONO = 60


def _primeiro_nome(nome: str) -> str:
    return (str(nome or "").strip().split(" ") or [""])[0][:40]


def variaveis(*, cliente: str = "", vendedor: str = "", empresa: str = "") -> dict:
    """O que `{nome}`, `{vendedor}` e `{empresa}` valem nesta conversa."""
    return {"nome": _primeiro_nome(cliente),
            "nome_completo": str(cliente or "").strip()[:80],
            "vendedor": _primeiro_nome(vendedor),
            "empresa": str(empresa or "").strip()[:80]}


_MARCA = re.compile(r"\{([a-z_]{1,20})\}")


def aplicar(texto: str, vals: dict) -> str:
    """Troca `{nome}` e companhia pelos valores da conversa.

    Marca DESCONHECIDA fica como está, de propósito: um `{foo}` que virasse vazio
    silenciosamente faria a frase sair truncada sem ninguém entender por quê —
    aparecendo, o vendedor vê e corrige antes de mandar (a tela escreve na caixa,
    não envia).

    Marca conhecida e VAZIA (cliente sem nome, que é a maioria de quem chega pelo
    WhatsApp) some junto com o espaço que sobraria: "Oi {nome}, tudo bem?" vira
    "Oi, tudo bem?" em vez de "Oi , tudo bem?".
    """
    def _troca(m):
        chave = m.group(1)
        if chave not in vals:
            return m.group(0)
        return str(vals.get(chave) or "")
    saida = _MARCA.sub(_troca, texto or "")
    # o espaço órfão da marca vazia, e a vírgula que sobrou colada
    saida = re.sub(r"[ \t]{2,}", " ", saida)
    saida = re.sub(r"(?<=\w) +([,.!?])", r"\1", saida)
    saida = re.sub(r"([:,]) *([,.!?])", r"\2", saida)
    return saida.strip()


def listar(pool, conta_id: int, membro_id: int | None) -> list[dict]:
    """As da conta MAIS as do vendedor, as mais usadas primeiro.

    Best-effort: tabela que ainda não existe (deploy pela metade) devolve lista
    vazia em vez de derrubar a conversa — a resposta rápida é atalho, e atalho
    que quebra a tela é pior que atalho nenhum.
    """
    try:
        with pool.connection() as c:
            linhas = c.execute(
                """select id, titulo, texto, (membro_id is null) as da_equipe, usos
                     from respostas_rapidas
                    where conta_id=%s and (membro_id is null or membro_id=%s)
                    order by usos desc, id desc
                    limit %s""",
                (conta_id, membro_id, TETO_POR_DONO * 2)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("respostas rápidas indisponíveis (conta %s): %s: %s",
                  conta_id, type(e).__name__, e)
        return []
    return [{"id": r[0], "titulo": r[1] or "", "texto": r[2],
             "equipe": bool(r[3]), "usos": int(r[4] or 0)} for r in linhas]


def da_equipe(pool, conta_id: int) -> list[dict]:
    """Só as DA CONTA — o que a empresa diz ao cliente. É o que a tela do painel
    gere: as pessoais de cada vendedor não passam por aqui, nem pro dono."""
    try:
        with pool.connection() as c:
            linhas = c.execute(
                """select id, titulo, texto, usos, criado_em
                     from respostas_rapidas
                    where conta_id=%s and membro_id is null
                    order by usos desc, id desc""", (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("respostas da equipe indisponíveis (conta %s): %s", conta_id, e)
        return []
    return [{"id": r[0], "titulo": r[1] or "", "texto": r[2],
             "usos": int(r[3] or 0), "criado_em": r[4]} for r in linhas]


def contagem_por_membro(pool, conta_id: int) -> dict:
    """{membro_id: quantas}. A CONTAGEM das pessoais, nunca o texto.

    O dono precisa saber que a equipe está usando a ferramenta; o que cada um
    escreveu pra si é dele. Prometido na tela do vendedor com estas palavras:
    "As sem selo são suas: só você vê".
    """
    try:
        with pool.connection() as c:
            linhas = c.execute(
                """select membro_id, count(*) from respostas_rapidas
                    where conta_id=%s and membro_id is not null
                    group by membro_id""", (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("contagem de respostas pessoais (conta %s): %s", conta_id, e)
        return {}
    return {r[0]: int(r[1]) for r in linhas}


def _titulo_do_texto(texto: str) -> str:
    """O título quando ninguém deu um: as primeiras palavras da frase.

    Título é o que a pessoa lê na lista correndo; pedir pra escrever um antes de
    salvar é o atrito que faz ninguém salvar nada."""
    limpo = " ".join(str(texto or "").split())
    if len(limpo) <= LIMITE_TITULO:
        return limpo
    # as reticências contam no teto: cortar DEPOIS de juntá-las comia justamente
    # elas, e o título saía parecendo uma frase que acabou no meio
    return limpo[:LIMITE_TITULO - 1].rstrip() + "…"


def criar(pool, conta_id: int, membro_id: int, texto: str, *,
          titulo: str = "", da_equipe: bool = False) -> dict:
    """Guarda uma resposta rápida. `da_equipe` só vale pra quem manda na conta.

    Quem chama decide o papel (ver a rota): aqui só se confia no que chegou depois
    de conferido, e o padrão é a resposta ser DO VENDEDOR.
    """
    texto = (texto or "").strip()[:LIMITE_TEXTO]
    if not texto:
        return {"ok": False, "erro": "vazio"}
    dono = None if da_equipe else membro_id
    with pool.connection() as c:
        n = c.execute(
            "select count(*) from respostas_rapidas where conta_id=%s and membro_id is not distinct from %s",
            (conta_id, dono)).fetchone()[0]
        if n >= TETO_POR_DONO:
            return {"ok": False, "erro": "cheio"}
        # a MESMA frase não entra duas vezes: o vendedor toca em "salvar esta"
        # duas vezes sem querer e a lista viraria eco
        ja = c.execute(
            """select id from respostas_rapidas
                where conta_id=%s and membro_id is not distinct from %s and texto=%s""",
            (conta_id, dono, texto)).fetchone()
        if ja:
            return {"ok": True, "id": ja[0], "repetida": True}
        novo = c.execute(
            """insert into respostas_rapidas (conta_id, membro_id, titulo, texto, criado_por)
               values (%s,%s,%s,%s,%s) returning id""",
            (conta_id, dono, (titulo or _titulo_do_texto(texto))[:LIMITE_TITULO],
             texto, membro_id)).fetchone()[0]
        c.commit()
    return {"ok": True, "id": novo}


def usar(pool, conta_id: int, membro_id: int | None, resposta_id: int) -> dict:
    """Conta o uso e devolve o texto — a tela escreve na caixa.

    O `where` é o mesmo da listagem: resposta de OUTRO vendedor não abre, nem por
    id adivinhado.
    """
    with pool.connection() as c:
        r = c.execute(
            """update respostas_rapidas set usos = usos + 1
                where id=%s and conta_id=%s and (membro_id is null or membro_id=%s)
            returning texto""",
            (resposta_id, conta_id, membro_id)).fetchone()
        c.commit()
    if not r:
        return {"ok": False, "erro": "nao_encontrada"}
    return {"ok": True, "texto": r[0]}


def apagar(pool, conta_id: int, membro_id: int, resposta_id: int, *,
           manda_na_conta: bool = False) -> dict:
    """Apaga a resposta. A DA EQUIPE só sai pela mão de quem manda na conta —
    senão um vendedor apagaria o preço oficial da empresa pra todo mundo."""
    with pool.connection() as c:
        if manda_na_conta:
            cur = c.execute(
                "delete from respostas_rapidas where id=%s and conta_id=%s",
                (resposta_id, conta_id))
        else:
            cur = c.execute(
                "delete from respostas_rapidas where id=%s and conta_id=%s and membro_id=%s",
                (resposta_id, conta_id, membro_id))
        c.commit()
    return {"ok": bool(cur.rowcount)} if cur.rowcount else {"ok": False, "erro": "nao_encontrada"}
