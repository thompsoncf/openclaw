"""CSS e JS que nunca mudam, servidos como arquivo — não dentro da página.

O PROBLEMA. As telas do painel carregam a folha de estilo e o script INTEIROS
dentro do HTML. Na Agenda são 38 KB de CSS e 38 KB de JS, medidos em 19/08/2026,
e eles vão junto em TODA navegação: trocar de mês, clicar num nome, voltar pro
"Hoje". O navegador não tem como reaproveitar nada — o conteúdo chega colado
numa página que muda, então ele baixa e reinterpreta tudo de novo, toda vez. Era
metade da sensação de a tela piscar a cada toque.

A SAÍDA. O que não depende dos dados sai pra um endereço próprio, com o RESUMO
DO CONTEÚDO no nome:

    /estatico/agenda-3f9c1a2b.css

O navegador guarda por um ano (`immutable`) e nunca mais pergunta. E não existe
versão velha grudada: mudou um caractere do CSS, muda o resumo, muda o endereço,
e a página nova aponta pro arquivo novo. É o oposto de `?v=2` mantido na mão, que
alguém esquece de incrementar exatamente na vez que importava.

O QUE NÃO PODE VIR PRA CÁ. Tudo que tem `{{ }}` dentro. O JS da Agenda começa com
onze linhas de dados do mês (os eventos, o mês atual, a hora do servidor) — essas
continuam inline, que é o lugar delas: mudam a cada carregamento. O resto, que é
99% do arquivo, é código e vem pra cá.

Registrar é uma linha, e a ideia é que as outras telas venham atrás:

    from web import estaticos
    CSS_URL = estaticos.registrar("agenda.css", _CSS_CRU)
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Response

router = APIRouter()

# nome-com-resumo -> (corpo, tipo). O nome real é derivado do conteúdo, então dois
# deploys com o mesmo código servem o mesmo endereço e o cache sobrevive.
_ARQUIVOS: dict[str, tuple[bytes, str]] = {}

_TIPOS = {
    "css": "text/css; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
}


def registrar(nome: str, corpo: str) -> str:
    """Publica o conteúdo e devolve a URL versionada pra página apontar.

    `nome` é só o rótulo legível ("agenda.css"); quem manda no endereço é o
    resumo do conteúdo. Chamar duas vezes com o mesmo corpo devolve a mesma URL —
    importa porque isto roda na importação do módulo, e um reload em
    desenvolvimento não pode multiplicar arquivo na memória."""
    base, _, ext = nome.rpartition(".")
    tipo = _TIPOS.get(ext.lower())
    if tipo is None:
        raise ValueError(f"tipo de estático não suportado: {nome!r}")
    dados = corpo.encode("utf-8")
    resumo = hashlib.sha256(dados).hexdigest()[:8]
    arquivo = f"{base}-{resumo}.{ext}"
    _ARQUIVOS[arquivo] = (dados, tipo)
    return f"/estatico/{arquivo}"


@router.get("/estatico/{arquivo}")
def servir(arquivo: str):
    """Entrega um estático registrado, com cache longo.

    `immutable` só é honesto porque o endereço carrega o resumo do conteúdo: o
    arquivo daquele endereço realmente nunca muda. Sem isso, um ano de cache seria
    a promessa de servir código velho por um ano."""
    achado = _ARQUIVOS.get(arquivo)
    if achado is None:
        # 404 curto e SEM cache: pedir de novo depois de um deploy tem que
        # funcionar — o endereço some quando o conteúdo muda, e um 404 guardado
        # deixaria a tela sem estilo até a pessoa limpar o navegador.
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    corpo, tipo = achado
    return Response(corpo, media_type=tipo, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
    })
