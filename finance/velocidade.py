"""Quanto cada tela do app demora, medida no celular de quem usa.

O servidor já se cronometra sozinho (`db.medicao` + o middleware `_mede_cockpit`)
e devolve o número no cabeçalho `Server-Timing`. Falta o outro lado: o aparelho
lê esse cabeçalho, soma o que ele mesmo gastou — conexão, viagem, desenho — e
devolve tudo numa linha só (migração 302). Aqui ficam as duas pontas do banco:
guardar essa linha e resumir o monte.

A régua é a MEDIANA e o P95, nunca a média: uma tela que abre em 300 ms noventa
vezes e em 9 s uma vez tem média de 387 ms, e é a de 9 s que faz o vendedor tocar
de novo. O P95 é o dia ruim dele.
"""
from __future__ import annotations

import re

#: o que uma tela pode demorar antes de o vendedor achar que "travou". Não é
#: número de gosto: abaixo de 1 s a troca parece resposta, acima de 2,5 s ele
#: começa a tocar de novo — foi o que motivou esta medição.
BOM_MS = 1000
RUIM_MS = 2500

#: telemetria não pode custar mais que o que mede: valor fora disso é ruído
#: (aba que dormiu, relógio do aparelho pulando) e entra zerado.
_TETO_MS = 600_000


def limpar_tela(caminho: str) -> str:
    """`/cockpit/lead/812/ficha` → `/cockpit/lead/{id}/ficha`.

    Mesma regra do middleware: duas telas iguais com leads diferentes são UMA
    tela, e é por tela que se compara."""
    caminho = (caminho or "").split("?")[0].split("#")[0].strip()[:120]
    if not caminho.startswith("/cockpit"):
        return ""
    return re.sub(r"/\d+", "/{id}", caminho)


def _ms(v) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 0
    return n if 0 <= n <= _TETO_MS else 0


def gravar(pool, conta_id: int, membro_id: int | None, dados: dict) -> bool:
    """Guarda UMA navegação. Devolve se guardou.

    Best-effort de propósito: medir não pode derrubar a tela de ninguém, e uma
    linha perdida não muda mediana nenhuma."""
    tela = limpar_tela(dados.get("tela") or "")
    if not tela:
        return False
    rede = str(dados.get("rede") or "")[:12]
    with pool.connection() as c:
        c.execute(
            """insert into tempo_tela (conta_id, membro_id, tela, servidor_ms, banco_ms,
                                       consultas, conexao_ms, espera_ms, render_ms,
                                       total_ms, rede)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (conta_id, membro_id, tela, _ms(dados.get("servidor")), _ms(dados.get("banco")),
             _ms(dados.get("consultas")), _ms(dados.get("conexao")), _ms(dados.get("espera")),
             _ms(dados.get("render")), _ms(dados.get("total")), rede))
        c.commit()
    return True


def por_tela(pool, conta_id: int, dias: int = 7, limite: int = 12) -> list[dict]:
    """As telas mais lentas da conta, pela MEDIANA do tempo total.

    Vem com a quebra média de cada pedaço — é ela que diz onde atacar: servidor
    alto é código nosso; espera alta é rede; render alto é o aparelho."""
    with pool.connection() as c:
        linhas = c.execute(
            """select tela, count(*),
                      percentile_cont(0.5) within group (order by total_ms),
                      percentile_cont(0.95) within group (order by total_ms),
                      avg(servidor_ms), avg(banco_ms), avg(consultas),
                      avg(conexao_ms + espera_ms), avg(render_ms)
                 from tempo_tela
                where conta_id = %s and criado_em > now() - make_interval(days => %s)
                group by tela
               having count(*) >= 3
                order by 3 desc
                limit %s""", (conta_id, dias, limite)).fetchall()
    return [{"tela": t, "n": n, "mediana": int(p50 or 0), "p95": int(p95 or 0),
             "servidor": int(srv or 0), "banco": int(bco or 0), "consultas": round(cons or 0, 1),
             "rede": int(rd or 0), "render": int(rnd or 0)}
            for t, n, p50, p95, srv, bco, cons, rd, rnd in linhas]


def resumo(pool, conta_id: int, dias: int = 7) -> dict:
    """O número de cima: quantas navegações, a mediana geral e onde o tempo foi."""
    with pool.connection() as c:
        linha = c.execute(
            """select count(*),
                      percentile_cont(0.5) within group (order by total_ms),
                      percentile_cont(0.95) within group (order by total_ms),
                      sum(servidor_ms), sum(conexao_ms + espera_ms), sum(render_ms),
                      count(*) filter (where total_ms > %s)
                 from tempo_tela
                where conta_id = %s and criado_em > now() - make_interval(days => %s)""",
            (RUIM_MS, conta_id, dias)).fetchone()
    n, p50, p95, srv, rede, rnd, ruins = linha
    soma = (srv or 0) + (rede or 0) + (rnd or 0)
    def fatia(v):
        return round(100 * (v or 0) / soma) if soma else 0
    return {"n": int(n or 0), "mediana": int(p50 or 0), "p95": int(p95 or 0),
            "ruins": int(ruins or 0),
            "pct_servidor": fatia(srv), "pct_rede": fatia(rede), "pct_render": fatia(rnd)}
