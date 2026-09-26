"""Os números da clínica (fase 7, "o painel do dono"): o que a agenda, o plano de
tratamento, os pacotes e as vagas já gravam, lido como negócio.

Desenho aprovado: docs/mockups/clinica_visao_geral.html, seção 08 ("os números que
o dono ganha"), e clinica_planos_pacotes_assinatura.html, seção 07 ("o painel do
dono"). Tela: /painel/clinica/numeros (web/painel_clinica_numeros.py).

CADA NÚMERO DIZ DE ONDE VEM. Receita e ticket são ESTIMADOS pelo preço de tabela do
atendimento e pelo valor do plano aceito — o caixa de verdade (o que entrou) é o
Financeiro. O Zaq não lê prontuário: nada aqui tem diagnóstico ou queixa.

LIMITES CONHECIDOS (estão escritos na tela):
- A grade não tem histórico: um mês passado é medido com a grade de HOJE. Quem foi
  desativado mas atendeu no mês aparece, com o que atendeu contado como fora da grade.
- Um item do plano que não é do catálogo não vira pacote (clinica_pacotes); a sessão
  dele, ao finalizar, entra como receita avulsa além do valor do plano.
- "Confirmaram" é quem ficou confirmado depois do lembrete — inclui quem a recepção
  confirmou por telefone depois dele.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.numeros")

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def periodo_do_mes(txt: str | None, hoje: date) -> tuple[date, date]:
    """'2026-09' → (1º de setembro, 1º de outubro). Vazio ou inválido: o mês de hoje."""
    try:
        a, m = (int(x) for x in (txt or "").split("-"))
        ini = date(a, m, 1)
    except (ValueError, TypeError):
        ini = hoje.replace(day=1)
    if not 2020 <= ini.year <= 2100:
        ini = hoje.replace(day=1)
    fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
    return ini, fim


def _pct(parte: float, todo: float) -> int | None:
    return round(100 * parte / todo) if todo else None


def _t(c, sql: str, args: tuple):
    """Consulta tolerante: sem a migração da fase, o número fica vazio (não quebra).
    Qualquer outro erro também não derruba a tela do dono, mas fica no log."""
    try:
        with c.transaction():
            return c.execute(sql, args).fetchall()
    except Exception as e:  # noqa: BLE001
        if "does not exist" not in str(e):
            _log.warning("números da clínica: consulta falhou", exc_info=True)
        return []


def _uniao(intervalos: list[tuple]) -> list[tuple]:
    """Junta intervalos que se sobrepõem (o encaixe em cima de outro atendimento não
    conta o mesmo minuto duas vezes)."""
    out: list[list] = []
    for a, b in sorted(intervalos):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _min(a, b) -> int:
    return max(0, int((b - a).total_seconds() // 60))


def ocupacao(c, conta_id: int, ini: date, fim: date, agora: datetime) -> list[dict]:
    """Por profissional: horas de grade no período, horas vendidas dentro dela (e
    fora dela: encaixe fora do horário, ou quem já saiu da grade), % e os horários de
    30 min da grade que PASSARAM sem nenhum atendimento (o buraco que já não volta)."""
    hoje = ca.hoje_br(agora)
    grade = cc.listar_grade(c, conta_id)
    bloqueios = cc.listar_bloqueios(c, conta_id, ini)
    por_dia: dict[tuple, list] = {}
    for e in ca._eventos(c, conta_id, ca.utc(ini, time(0)), ca.utc(fim, time(0))):
        if e["profissional_id"] and e["situacao"] not in ("cancelou", "faltou"):
            por_dia.setdefault((e["profissional_id"], ca.local(e["inicio"]).date()), []).append((e["inicio"], e["fim"]))
    atendeu = {k[0] for k in por_dia}
    passo = timedelta(minutes=cc.PASSO_MIN)
    out = []
    for p in cc.listar_profissionais(c, conta_id, so_ativos=False):
        if p["funcao"] == "Recepção, não atende" or not (p["ativo"] or p["id"] in atendeu):
            continue
        grade_min = dentro = fora = vazios = 0
        d = ini
        while d < fim:
            ocup = _uniao(por_dia.get((p["id"], d), []))
            faixas = cc.faixas_do_dia(grade, bloqueios, p["id"], d)
            no_dia = 0
            for f in faixas:
                ini_f, fim_f = ca.utc(d, f["inicio"]), ca.utc(d, f["fim"])
                grade_min += _min(ini_f, fim_f)
                no_dia += sum(_min(max(a, ini_f), min(b, fim_f)) for a, b in ocup)
                if d < hoje:
                    t = ini_f
                    while t + passo <= fim_f:
                        if not any(a < t + passo and b > t for a, b in ocup):
                            vazios += 1
                        t += passo
            dentro += no_dia
            fora += sum(_min(a, b) for a, b in ocup) - no_dia
            d += timedelta(days=1)
        if grade_min or dentro or fora:
            out.append({"prof": p["nome"], "ativo": p["ativo"], "grade_h": round(grade_min / 60, 1),
                        "vendido_h": round(dentro / 60, 1), "fora_h": round(fora / 60, 1),
                        "pct": _pct(dentro, grade_min), "vazios": vazios})
    return out


def numeros(c, conta_id: int, ini: date, fim: date, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    a, b = ca.utc(ini, time(0)), ca.utc(fim, time(0))
    ocup = ocupacao(c, conta_id, ini, fim, agora)
    tipos = {t["id"]: t for t in cc.listar_tipos(c, conta_id, so_ativos=False)}
    consulta = next((t for t in tipos.values() if t["ativo"] and t["categoria"] == "consulta"
                     and t["preco_centavos"]), None)
    # um horário vazio de 30 min vale a fração de uma consulta que caberia nele
    por_horario = (int(consulta["preco_centavos"]) * cc.PASSO_MIN // max(cc.PASSO_MIN, int(consulta["duracao_min"]))
                   if consulta else None)

    # ---- a agenda do período (só o que já passou conta pra falta/finalizado)
    ags = _t(c, """select e.id, e.situacao, e.servico_id, coalesce(e.marcado_por, ''), coalesce(e.origem, ''),
                          e.confirmacao_enviada_em is not null,
                          coalesce(e.confirmado_em >= e.confirmacao_enviada_em, false), e.status
                     from eventos_agenda e
                    where e.conta_id=%s and e.situacao is not null and e.inicio >= %s and e.inicio < %s""",
             (conta_id, a, b))
    sit = lambda r: "cancelou" if r[7] == "cancelado" else r[1]  # noqa: E731
    finalizados = [r for r in ags if sit(r) == "finalizado"]
    faltas = sum(1 for r in ags if sit(r) == "faltou")
    cancel = sum(1 for r in ags if sit(r) == "cancelou")
    lembrados = [r for r in ags if r[5]]
    confirmaram = sum(1 for r in lembrados if r[6])      # r[6]: confirmado depois do lembrete (SQL)
    consultas_fin = [r for r in finalizados if (tipos.get(r[2]) or {}).get("categoria") == "consulta"]

    # ---- o que baixou de pacote não é receita nova (o dinheiro entrou no plano). Vale
    #      o dia do ATENDIMENTO, não o da baixa: a sessão de 30/09 finalizada em 01/10 é de setembro.
    do_pacote = {r[0] for r in _t(c, """select u.evento_id from clinica_pacote_consumos u
                                          join eventos_agenda e on e.id = u.evento_id and e.conta_id = u.conta_id
                                         where u.conta_id=%s and e.inicio >= %s and e.inicio < %s""", (conta_id, a, b))}
    receita_avulsa = sum(int((tipos.get(r[2]) or {}).get("preco_centavos") or 0)
                         for r in finalizados if r[0] not in do_pacote)

    # ---- plano de tratamento
    planos = _t(c, """select status, total_centavos, aceito_forma, pix_desconto_pct, enviado_em, aceito_em
                        from clinica_planos where conta_id=%s
                         and ((enviado_em >= %s and enviado_em < %s) or (aceito_em >= %s and aceito_em < %s))""",
                (conta_id, a, b, a, b))
    enviados = [p for p in planos if p[4] and a <= p[4] < b]
    aceitos = [p for p in planos if p[5] and a <= p[5] < b]
    valor_aceito = sum(int(p[1]) - (round(int(p[1]) * float(p[3]) / 100) if p[2] == "pix" else 0) for p in aceitos)

    # ---- sessões: vendidas (pacote criado no período), usadas (atendidas no período);
    #      devidas e vencidas são o retrato de HOJE (a tela diz), seja qual for o mês
    vendidas = sum(int(r[0]) for r in _t(c, """select sessoes_total from clinica_pacotes where conta_id=%s
                                                 and criado_em >= %s and criado_em < %s""", (conta_id, a, b)))
    saldo = _t(c, """select sessoes_total - sessoes_usadas,
                            estado = 'vencido' or (estado = 'ativo' and validade_ate < %s)
                       from clinica_pacotes where conta_id=%s and estado in ('ativo', 'vencido')""", (hoje, conta_id))
    devidas = sum(int(r[0]) for r in saldo if not r[1])
    vencidas = sum(int(r[0]) for r in saldo if r[1])

    # ---- retornos pedidos no período e o que virou
    rets = _t(c, "select estado from clinica_retornos where conta_id=%s and criado_em >= %s and criado_em < %s",
              (conta_id, a, b))

    # ---- vagas liberadas no período
    vagas = _t(c, """select v.estado, coalesce(s.setup_centavos, 0)
                       from clinica_vagas v
                       left join eventos_agenda e on e.id = v.preenchida_evento_id and e.conta_id = v.conta_id
                       left join servicos_catalogo s on s.id = e.servico_id and s.conta_id = v.conta_id
                      where v.conta_id=%s and v.inicio >= %s and v.inicio < %s""", (conta_id, a, b))

    atend = len(finalizados)
    receita = receita_avulsa + valor_aceito
    vazios = sum(o["vazios"] for o in ocup)
    origem = {"recepcao": 0, "ia": 0, "vaga": 0}
    for r in ags:
        if sit(r) != "cancelou":
            origem[r[3] if r[3] in origem else "recepcao"] += 1
    return {
        "ocupacao": ocup,
        "grade_h": round(sum(o["grade_h"] for o in ocup), 1),
        "vendido_h": round(sum(o["vendido_h"] for o in ocup), 1),
        "ocupacao_pct": _pct(sum(o["vendido_h"] for o in ocup), sum(o["grade_h"] for o in ocup)),
        "vazios": vazios,
        "vazios_valor": vazios * por_horario if consulta else None,
        "consulta_preco": int(consulta["preco_centavos"]) if consulta else None,
        "consulta_min": int(consulta["duracao_min"]) if consulta else None,
        "fora_h": round(sum(o["fora_h"] for o in ocup), 1), "passado": fim <= hoje,
        "atendimentos": atend, "receita": receita, "ticket": receita // atend if atend else None,
        "receita_avulsa": receita_avulsa, "valor_aceito": valor_aceito,
        "consultas": len(consultas_fin), "planos_enviados": len(enviados), "planos_aceitos": len(aceitos),
        "consulta_proposta_pct": _pct(len(enviados), len(consultas_fin)),
        "proposta_fechada_pct": _pct(len(aceitos), len(enviados)),
        "vendidas": vendidas, "usadas": len(do_pacote), "devidas": devidas, "vencidas": vencidas,
        "faltas": faltas, "cancelamentos": cancel,
        "falta_pct": _pct(faltas, len(finalizados) + faltas),
        "lembrados": len(lembrados), "confirmaram": confirmaram, "confirmacao_pct": _pct(confirmaram, len(lembrados)),
        "retornos_pedidos": len(rets), "retornos_marcados": sum(1 for r in rets if r[0] == "marcado"),
        "retornos_perdidos": sum(1 for r in rets if r[0] == "vencido"),
        "vagas": len(vagas), "vagas_preenchidas": sum(1 for v in vagas if v[0] == "preenchida"),
        "vagas_valor": sum(int(v[1]) for v in vagas if v[0] == "preenchida"),
        "origem": origem, "agendados": sum(origem.values()),
    }
