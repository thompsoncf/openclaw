"""Qual versão do Zaq este processo está servindo.

POR QUE ISSO EXISTE
Quem estava com o painel aberto durante o deploy segue rodando o JavaScript
ANTIGO contra o servidor novo. Não é hipótese: no desconto por item (#489) eu
deixei o campo `primeiro_ano` ser aceito-e-ignorado no payload só pra não quebrar
aba aberta durante o deploy — programei em volta do problema em vez de resolvê-lo.

A página carrega com esta string dentro. Ela pergunta de tempos em tempos qual é
a atual (GET /painel/versao); se mudou, oferece recarregar.

DE ONDE VEM — e por que NÃO é o relógio
O Render entrega o commit em RENDER_GIT_COMMIT. Quando ele falta, o fallback
tinha que ser IGUAL entre processos irmãos do mesmo deploy: com dois workers, uma
versão por processo (o instante em que cada um subiu) faria a faixa piscar pra
sempre — cada resposta viria de um worker diferente, "versão nova" toda vez.

Então o fallback é o arquivo .py mais recente da aplicação: o deploy escreve os
arquivos, todos os workers leem a MESMA data, e ela muda quando o código muda.
Custa um walk uma vez, no import.

O último fallback ("?") é o silêncio deliberado: sem versão o JavaScript nem
começa a perguntar. Confundir "não sei a versão" com "a versão mudou" é o único
jeito de errar aqui, e as duas portas ficam fechadas.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger("web.versao")

# só o código da aplicação — .venv/, .git/ e node_modules não entram
_PACOTES = ("web", "finance", "contas", "db", "core", "services")


def _do_codigo() -> str:
    """A data do .py mais novo da aplicação, em segundos. Determinística: dois
    workers do mesmo deploy chegam ao mesmo número."""
    raiz = Path(__file__).resolve().parent.parent
    ultimo = 0.0
    for p in _PACOTES:
        d = raiz / p
        if not d.is_dir():
            continue
        for f in d.rglob("*.py"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m > ultimo:
                ultimo = m
    return "c%d" % int(ultimo) if ultimo else ""


def _calcular() -> str:
    commit = (os.environ.get("RENDER_GIT_COMMIT")
              or os.environ.get("GIT_COMMIT") or "").strip()
    if commit:
        return commit[:12]
    try:
        v = _do_codigo()
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra datar o código: %s: %s", type(e).__name__, e)
        v = ""
    if not v:
        _log.warning("sem versão conhecida — a faixa de atualização fica desligada")
    return v


# Calculado UMA vez, no import: é a versão deste processo e ela não muda enquanto
# ele viver. Recalcular a cada chamada faria a faixa aparecer sozinha.
VERSAO: str = _calcular()
