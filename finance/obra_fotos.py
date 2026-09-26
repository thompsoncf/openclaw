"""As fotos da obra, por etapa (migração 369).

Pra que servem, na ordem do que vale dinheiro:
1. REFORMA CASA BRASIL: os últimos 10% do crédito só saem depois que o cliente
   manda à Caixa as fotos da obra pronta (finance/obra_reforma.py, modelo 'rcb').
   Sem foto guardada, a última parcela da empresa fica presa.
2. A prova da etapa: "terminou o telhado" com a foto é a medição que o cliente
   não tem como contestar depois (CC, art. 614).
3. O diário da casa: quem compra casa pronta pergunta como foi feita.

De onde vêm: do WhatsApp (a foto que NÃO é nota — telhado, parede, piso; o
webhook deixa a imagem da mensagem em `livro.midia_atual` e a ferramenta
`guardar_foto_da_obra` guarda) e do painel (a ficha da obra).

Onde ficam: no bucket PRIVADO dos comprovantes (finance/comprovantes.py). O
banco guarda o caminho; quem entrega é a rota, depois de conferir a sessão ou o
token do orçamento. Foto de casa de cliente não vira link público.
"""
from __future__ import annotations

import time
import uuid

from . import obras as _ob

TIPOS_OK = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp",
            "image/heic": "heic"}
MAX_BYTES = 10 * 1024 * 1024
_COLS = ("id", "obra_id", "etapa_chave", "caminho", "content_type", "bytes", "legenda",
         "origem", "membro_id", "criado_em")


def validar(conteudo: bytes, content_type: str) -> str:
    """O content-type normalizado, ou ValueError com a frase pro usuário."""
    if not conteudo:
        raise ValueError("A foto veio vazia.")
    if len(conteudo) > MAX_BYTES:
        raise ValueError("Foto muito grande (máximo 10 MB).")
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in TIPOS_OK:
        raise ValueError("Aceito foto (JPG, PNG, WEBP ou HEIC).")
    return "image/jpeg" if ct == "image/jpg" else ct


def _etapa(obra: dict, etapa: str | None) -> str | None:
    """A chave da etapa pelo que a pessoa falou ("o telhado", "laje"), ou None.
    ValueError se ela disse uma etapa que a obra não tem."""
    if not (etapa or "").strip():
        return None
    e = _ob._achar_etapa(obra["etapas"], etapa)
    if not e:
        nomes = ", ".join(x["nome"].lower() for x in obra["etapas"])
        raise ValueError(f"A {obra['nome']} não tem a etapa “{etapa}”. As etapas são: {nomes}.")
    return e["chave"]


def guardar(pool, conta_id: int, obra_id: int, conteudo: bytes, content_type: str, *,
            etapa: str | None = None, legenda: str = "", origem: str = "painel",
            membro_id: int | None = None, subir=None) -> dict:
    """Sobe a foto pro bucket privado e registra. `subir` é o cano do Storage
    (injetável nos testes); por padrão, `comprovantes.subir_em`."""
    obra = _ob.obter_obra(pool, conta_id, obra_id)
    if not obra:
        raise ValueError("Obra não encontrada.")
    ct = validar(conteudo, content_type)
    chave = _etapa(obra, etapa)
    if subir is None:
        from .comprovantes import subir_em as subir
    caminho = (f"obras/{conta_id}/{obra_id}/{int(time.time())}-"
               f"{uuid.uuid4().hex[:10]}.{TIPOS_OK[ct]}")
    subir(caminho, conteudo, ct)
    with pool.connection() as c:
        fid = c.execute(
            """insert into obra_fotos (conta_id, obra_id, etapa_chave, caminho, content_type,
                                       bytes, legenda, origem, membro_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, obra_id, chave, caminho, ct, len(conteudo), (legenda or "").strip()[:200],
             origem if origem in ("painel", "whatsapp") else "painel", membro_id)).fetchone()[0]
        c.commit()
    nome = next((e["nome"] for e in obra["etapas"] if e["chave"] == chave), None)
    return {"id": fid, "obra": obra["nome"], "etapa": nome, "caminho": caminho}


def listar(pool, conta_id: int, obra_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(f"""select {', '.join(_COLS)} from obra_fotos
                              where conta_id=%s and obra_id=%s
                              order by criado_em desc, id desc""",
                         (conta_id, obra_id)).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


def por_etapa(obra: dict, fotos: list[dict]) -> list[dict]:
    """[{chave, nome, fotos}] na ordem das etapas; as sem etapa no fim."""
    grupos = [{"chave": e["chave"], "nome": e["nome"],
               "fotos": [f for f in fotos if f["etapa_chave"] == e["chave"]]}
              for e in obra["etapas"]]
    conhecidas = {e["chave"] for e in obra["etapas"]}
    soltas = [f for f in fotos if f["etapa_chave"] not in conhecidas]
    grupos = [g for g in grupos if g["fotos"]]
    if soltas:
        grupos.append({"chave": None, "nome": "Sem etapa", "fotos": soltas})
    return grupos


def obter(pool, conta_id: int, obra_id: int, foto_id: int) -> dict | None:
    """A foto só se for DESTA conta e DESTA obra — o id na URL não abre outra."""
    with pool.connection() as c:
        r = c.execute(f"""select {', '.join(_COLS)} from obra_fotos
                           where id=%s and conta_id=%s and obra_id=%s""",
                      (foto_id, conta_id, obra_id)).fetchone()
    return dict(zip(_COLS, r)) if r else None


def apagar(pool, conta_id: int, obra_id: int, foto_id: int, *, remover=None) -> bool:
    f = obter(pool, conta_id, obra_id, foto_id)
    if not f:
        return False
    with pool.connection() as c:
        c.execute("delete from obra_fotos where id=%s and conta_id=%s", (foto_id, conta_id))
        c.commit()
    if remover is None:
        from .comprovantes import apagar as remover
    remover(f["caminho"])
    return True


def contagem(pool, conta_id: int, obra_id: int) -> int:
    with pool.connection() as c:
        return int(c.execute("select count(*) from obra_fotos where conta_id=%s and obra_id=%s",
                             (conta_id, obra_id)).fetchone()[0])
