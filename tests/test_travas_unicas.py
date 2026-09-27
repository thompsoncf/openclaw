"""Cada relógio tem a SUA trava (revisão de 27/09/2026).

`pg_try_advisory_lock(n)` com o mesmo `n` em dois motores faz um pular o ciclo
enquanto o outro roda — sem erro, sem log: o relógio da visita e o do sinal
dividiam 771173, a temperatura e o perdido dividiam 771151, o conteúdo e o
reengajamento das campanhas dividiam 771146. Este teste lê o código e falha na
primeira trava repetida.
"""
import re
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_RE = re.compile(r"^(_LOCK\w*)\s*=\s*(\d{5,})", re.M)


def test_nenhuma_trava_de_um_numero_so_se_repete():
    usos = defaultdict(list)
    for pasta in ("finance", "web", "core"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            for nome, n in _RE.findall(arq.read_text(encoding="utf-8")):
                usos[int(n)].append(f"{arq.relative_to(RAIZ)}:{nome}")
    repetidas = {n: onde for n, onde in usos.items() if len(onde) > 1}
    assert not repetidas, f"travas repetidas: {repetidas}"
