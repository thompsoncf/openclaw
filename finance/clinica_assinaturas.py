"""Assinatura da clínica (fase 7b): o que faz o faturamento não depender do movimento
do mês.

Desenho aprovado: docs/mockups/clinica_planos_pacotes_assinatura.html, seção 05.
Migração 384. Tela: /painel/clinica/assinaturas (web/painel_clinica_assinaturas.py).

O PLANO é da clínica: nome, mensalidade, dia de cobrança e o que ele dá —
  * sessões inclusas por mês de um atendimento do catálogo (ex.: 1 limpeza de pele):
    ao finalizar aquele atendimento, o benefício do mês é usado (`ao_finalizar`) e a
    sessão NÃO baixa do pacote;
  * desconto em procedimentos: vale no plano de tratamento, sem pedir aprovação do
    dono até o % do plano (`desconto_procedimento`);
  * desconto em produtos: gravado aqui, aplicado na venda de produto (fase 7c);
  * prioridade na fila de vagas liberadas (`prioritarios`, clinica_vagas.candidatos).

A MENSALIDADE de cada mês vira um título a receber no Financeiro (`gerar`, pelo
poller). Sem cobrança automática no cartão por enquanto: o Asaas de hoje é a conta do
Zaq (ASAAS_API_KEY), e o dinheiro do paciente não pode cair nela. Quando a clínica
tiver a própria conta, a cobrança recorrente entra aqui.

O assinante guarda o preço e o dia de quando assinou: mudar o plano depois não muda o
combinado com quem já assinou.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.assinaturas")
_LOCK = 771167          # 771161..771166: agenda, marcar, conversa, vagas, planos, pacotes
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
MESES_ATRAS = 3         # o poller parado alguns dias não perde a mensalidade do mês anterior


def _falta_migracao(e: Exception) -> bool:
    return "clinica_assin" in str(e) and "does not exist" in str(e)


def _num(txt, padrao, lo, hi, inteiro: bool = False):
    s = str(txt if txt is not None else "").strip().replace("%", "").replace(",", ".")
    if not s:
        return padrao
    try:
        v = int(s) if inteiro else round(float(s), 2)
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def _mes_seguinte(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def beneficios_txt(p: dict) -> str:
    """O que o plano dá, pra tela da recepção (a mensagem ao paciente é outra)."""
    out = []
    if p["sessoes_mes"] and p["servico"]:
        out.append(f"{p['sessoes_mes']} {p['servico']} por mês")
    if p["desconto_procedimento_pct"]:
        out.append(f"{p['desconto_procedimento_pct']:g}% em procedimentos")
    if p["desconto_produto_pct"]:
        out.append(f"{p['desconto_produto_pct']:g}% em produtos")
    if p["prioridade_vagas"]:
        out.append("prioridade nas vagas")
    return " · ".join(out) or "sem benefício cadastrado"


# ------------------------------------------------------------------ os planos

def planos(c, conta_id: int, so_ativos: bool = False) -> list[dict]:
    try:
        with c.transaction():
            rows = c.execute(
                """select p.id, p.nome, p.preco_centavos, p.dia_cobranca, p.servico_id, coalesce(s.nome, ''),
                          p.sessoes_mes, p.desconto_procedimento_pct, p.desconto_produto_pct, p.prioridade_vagas,
                          p.ativo,
                          (select count(*) from clinica_assinantes a
                            where a.plano_id = p.id and a.conta_id = p.conta_id and a.estado = 'ativa')
                     from clinica_assinatura_planos p
                     left join servicos_catalogo s on s.id = p.servico_id and s.conta_id = p.conta_id
                    where p.conta_id=%s""" + (" and p.ativo" if so_ativos else "")
                + " order by p.ativo desc, p.preco_centavos, p.id", (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return []
        raise
    out = []
    for r in rows:
        p = {"id": r[0], "nome": r[1], "preco_centavos": int(r[2]), "preco": cc.reais(r[2]), "dia": int(r[3]),
             "servico_id": r[4], "servico": r[5], "sessoes_mes": int(r[6]),
             "desconto_procedimento_pct": float(r[7]), "desconto_produto_pct": float(r[8]),
             "prioridade_vagas": bool(r[9]), "ativo": bool(r[10]), "assinantes": int(r[11])}
        p["beneficios"] = beneficios_txt(p)
        out.append(p)
    return out


def plano(c, conta_id: int, plano_id: int | None) -> dict | None:
    return next((p for p in planos(c, conta_id) if p["id"] == plano_id), None) if plano_id else None


def salvar_plano(c, conta_id: int, *, plano_id: int | None, nome: str, preco, dia, servico_id, sessoes,
                 desconto_procedimento, desconto_produto, prioridade: bool, ativo: bool = True
                 ) -> tuple[int | None, str | None]:
    nome = " ".join((nome or "").split())[:80]
    if not nome:
        return None, "Dê um nome ao plano (ex.: Pele em dia)."
    preco_c = cc.centavos(str(preco or ""))
    if not preco_c:
        return None, "Mensalidade inválida: use um valor como 149 ou 149,90."
    dia_v = _num(dia, 10, 1, 28, inteiro=True)
    if dia_v is None:
        return None, "Dia de cobrança de 1 a 28."
    sess = _num(sessoes, 0, 0, 10, inteiro=True)
    if sess is None:
        return None, "Sessões inclusas por mês: de 0 a 10."
    dproc, dprod = _num(desconto_procedimento, 0, 0, 50), _num(desconto_produto, 0, 0, 50)
    if dproc is None or dprod is None:
        return None, "Desconto de 0 a 50%."
    sid = int(servico_id) if str(servico_id or "").strip().isdigit() else None
    if sid and not any(t["id"] == sid for t in cc.listar_tipos(c, conta_id)):
        return None, "Atendimento incluso não encontrado no catálogo."
    if sess and not sid:
        return None, "Escolha o atendimento incluso (ou deixe 0 sessões por mês)."
    if not sid:
        sess = 0
    args = (nome, preco_c, dia_v, sid, sess, dproc, dprod, bool(prioridade), bool(ativo))
    if plano_id:
        r = c.execute(
            """update clinica_assinatura_planos set nome=%s, preco_centavos=%s, dia_cobranca=%s, servico_id=%s,
                      sessoes_mes=%s, desconto_procedimento_pct=%s, desconto_produto_pct=%s, prioridade_vagas=%s,
                      ativo=%s, atualizado_em=now()
                where id=%s and conta_id=%s returning id""", args + (plano_id, conta_id)).fetchone()
        return (r[0], None) if r else (None, "Plano não encontrado.")
    r = c.execute(
        """insert into clinica_assinatura_planos (nome, preco_centavos, dia_cobranca, servico_id, sessoes_mes,
                  desconto_procedimento_pct, desconto_produto_pct, prioridade_vagas, ativo, conta_id)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""", args + (conta_id,)).fetchone()
    return r[0], None


# ------------------------------------------------------------------ o assinante

def do_paciente(c, conta_id: int, lead: int | None, fone: str | None, paciente: str = "",
                trava: bool = False) -> dict | None:
    """A assinatura ATIVA deste paciente, com o plano. Pelo card ou pelo celular (8
    últimos dígitos); se o nome do paciente vier, o primeiro nome tem que bater (a mãe
    assinante e o filho no mesmo celular: o benefício é da mãe)."""
    from finance.clinica_pacotes import _primeiro
    dig = ca._digitos(fone)
    try:
        with c.transaction():
            rows = c.execute(
                r"""select a.id, a.plano_id, a.paciente_nome, a.inicio, a.prospeccao_id
                      from clinica_assinantes a
                     where a.conta_id=%s and a.estado='ativa'
                       and (a.prospeccao_id = %s
                            or (length(%s) >= 8 and right(regexp_replace(a.paciente_fone, '\D', '', 'g'), 8) = %s))
                     order by (a.prospeccao_id = %s) desc nulls last, a.id limit 5"""
                + (" for update of a" if trava else ""),
                (conta_id, lead, dig, dig[-8:], lead)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return None
        _log.warning("assinaturas: não consegui achar o assinante", exc_info=True)
        return None
    quem = _primeiro(paciente) if (paciente or "").strip() else ""
    for r in rows:
        if quem and _primeiro(r[2]) and _primeiro(r[2]) != quem:
            continue
        p = plano(c, conta_id, r[1])
        if p:
            return {**p, "assinante_id": r[0], "plano_id": r[1], "paciente": r[2], "inicio": r[3], "lead": r[4]}
    return None


def desconto_procedimento(c, conta_id: int, lead: int | None, fone: str | None, paciente: str = "") -> tuple[float, str]:
    """(o % de desconto em procedimentos do plano do assinante, o nome do plano)."""
    a = do_paciente(c, conta_id, lead, fone, paciente)
    return (a["desconto_procedimento_pct"], a["nome"]) if a and a["desconto_procedimento_pct"] else (0.0, "")


def prioritarios(c, conta_id: int) -> set[int]:
    """Os cards de quem tem assinatura ativa com prioridade na fila de vagas."""
    try:
        with c.transaction():
            return {r[0] for r in c.execute(
                """select a.prospeccao_id from clinica_assinantes a
                     join clinica_assinatura_planos p on p.id = a.plano_id and p.conta_id = a.conta_id
                    where a.conta_id=%s and a.estado='ativa' and p.prioridade_vagas and a.prospeccao_id is not null""",
                (conta_id,)).fetchall()}
    except Exception:  # noqa: BLE001 — sem a 384, ninguém fura a fila
        return set()


def assinantes(c, conta_id: int, hoje: date) -> list[dict]:
    comp = hoje.replace(day=1)
    try:
        with c.transaction():
            rows = c.execute(
                """select a.id, a.plano_id, p.nome, a.prospeccao_id, a.paciente_nome, a.paciente_fone,
                          a.preco_centavos, a.dia_cobranca, a.inicio, a.estado, a.cancelada_em,
                          coalesce(a.cancelada_motivo, ''), p.sessoes_mes, coalesce(s.nome, ''),
                          (select count(*) from clinica_assinatura_usos u
                            where u.assinante_id = a.id and u.conta_id = a.conta_id and u.competencia = %s),
                          (select count(*) from clinica_assinatura_mensalidades m
                             join titulos t on t.id = m.titulo_id and t.conta_id = m.conta_id
                            where m.assinante_id = a.id and m.conta_id = a.conta_id
                              and t.status = 'aberto' and t.vencimento < %s)
                     from clinica_assinantes a
                     join clinica_assinatura_planos p on p.id = a.plano_id and p.conta_id = a.conta_id
                     left join servicos_catalogo s on s.id = p.servico_id and s.conta_id = a.conta_id
                    where a.conta_id=%s
                    order by a.estado <> 'ativa', a.paciente_nome, a.id limit 400""",
                (comp, hoje, conta_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return []
        raise
    return [{"id": r[0], "plano_id": r[1], "plano": r[2], "lead": r[3], "paciente": r[4], "fone": r[5],
             "preco_centavos": int(r[6]), "preco": cc.reais(r[6]), "dia": int(r[7]), "inicio": r[8],
             "estado": r[9], "cancelada_em": r[10], "motivo": r[11], "sessoes_mes": int(r[12]), "servico": r[13],
             "usadas_mes": int(r[14]), "atrasadas": int(r[15])} for r in rows]


def assinar(pool, conta_id: int, *, plano_id: int | None, lead: int | None, paciente: str, fone: str,
            inicio: str | date | None, membro_id: int | None, hoje: date | None = None
            ) -> tuple[int | None, str | None]:
    """Ativa a assinatura e já gera a mensalidade do mês (o título a receber)."""
    hoje = hoje or ca.hoje_br()
    if isinstance(inicio, date):
        ini = inicio
    else:
        try:
            ini = date.fromisoformat((inicio or "").strip()) if (inicio or "").strip() else hoje
        except ValueError:
            return None, "Data de início inválida."
    if not hoje - timedelta(days=31) <= ini <= hoje + timedelta(days=62):
        return None, "O início vai de um mês atrás a dois meses pra frente."
    with pool.connection() as c:
        p = plano(c, conta_id, plano_id)
        if not p or not p["ativo"]:
            return None, "Escolha um plano ativo."
        if lead:
            r = c.execute("""select coalesce(nullif(contato,''), empresa, ''), coalesce(nullif(whatsapp,''), telefone, '')
                               from prospeccao where id=%s and conta_id=%s""", (lead, conta_id)).fetchone()
            if not r:
                return None, "Paciente não encontrado."
            paciente, fone = (paciente or "").strip() or r[0], (fone or "").strip() or r[1]
        paciente = " ".join((paciente or "").split())[:120]
        if not paciente:
            return None, "Informe o nome do paciente."
        if len(ca._digitos(fone)) < 10:
            return None, "Informe o celular do paciente (com DDD)."
        ja = do_paciente(c, conta_id, lead, fone, paciente)
        if ja:
            return None, f"{ja['paciente']} já é assinante do plano {ja['nome']}."
        try:
            aid = c.execute(
                """insert into clinica_assinantes (conta_id, plano_id, prospeccao_id, paciente_nome, paciente_fone,
                                                  preco_centavos, dia_cobranca, inicio, criado_por)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (conta_id, p["id"], lead, paciente, (fone or "")[:40], p["preco_centavos"], p["dia"], ini,
                 membro_id)).fetchone()[0]
            c.commit()
        except Exception as e:  # noqa: BLE001
            c.rollback()
            if "clinica_assinantes_um_ativo" in str(e):
                return None, "Este paciente já tem uma assinatura ativa."
            raise
    gerar(pool, conta_id, hoje, so=aid)
    return aid, None


def cancelar(c, conta_id: int, assinante_id: int, motivo: str, membro_id: int | None) -> bool:
    """Cancela. A mensalidade já lançada fica (o mês em curso é devido); as próximas
    não saem mais."""
    return c.execute(
        """update clinica_assinantes set estado='cancelada', cancelada_em=now(), cancelada_motivo=%s,
                  cancelada_por=%s
            where id=%s and conta_id=%s and estado='ativa' returning id""",
        (" ".join((motivo or "").split())[:200] or "cancelada pela clínica", membro_id, assinante_id,
         conta_id)).fetchone() is not None


def quem_pode_assinar(c, conta_id: int, agora: datetime, limite: int = 15) -> list[dict]:
    """Quem já veio 3 vezes ou mais nos últimos 12 meses e não é assinante: é pra quem
    a recepção oferece no fim do atendimento."""
    try:
        with c.transaction():
            rows = c.execute(
                """select e.prospeccao_id, max(coalesce(e.paciente_nome, '')), count(*), max(e.inicio)
                     from eventos_agenda e
                    where e.conta_id=%s and e.situacao='finalizado' and e.prospeccao_id is not null
                      and e.inicio > %s - interval '365 days'
                      and not exists (select 1 from clinica_assinantes a where a.conta_id = e.conta_id
                                         and a.prospeccao_id = e.prospeccao_id and a.estado = 'ativa')
                    group by e.prospeccao_id having count(*) >= 3
                    order by count(*) desc, max(e.inicio) desc limit %s""", (conta_id, agora, limite)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return []
        raise
    return [{"lead": r[0], "paciente": r[1] or "Paciente", "visitas": int(r[2]), "ultima": ca.local(r[3]).date()}
            for r in rows]


def resumo(lista: list[dict]) -> dict:
    ativos = [a for a in lista if a["estado"] == "ativa"]
    return {"ativos": len(ativos), "recorrente": sum(a["preco_centavos"] for a in ativos),
            "atrasados": sum(1 for a in ativos if a["atrasadas"])}


# ------------------------------------------------------------------ o benefício usado

def ao_finalizar(c, conta_id: int, evento_id: int) -> bool:
    """Chamado por `clinica_agenda.mudar_situacao` ANTES da baixa do pacote. Se o
    atendimento é o incluso no plano do assinante e ainda há sessão do mês, usa o
    benefício e devolve True (aí o pacote não baixa)."""
    ev = ca.evento(c, conta_id, evento_id)
    if not ev or not ev.get("servico_id"):
        return False
    a = do_paciente(c, conta_id, ev["lead"], ev["fone"], ev["paciente"], trava=True)
    if not a or a["servico_id"] != ev["servico_id"] or a["sessoes_mes"] <= 0:
        return False
    dia = ca.local(ev["inicio"]).date()
    if dia < a["inicio"]:
        return False
    comp = dia.replace(day=1)
    usados = c.execute("""select count(*) from clinica_assinatura_usos
                           where conta_id=%s and assinante_id=%s and competencia=%s""",
                       (conta_id, a["assinante_id"], comp)).fetchone()[0]
    if usados >= a["sessoes_mes"]:
        return False
    return c.execute("""insert into clinica_assinatura_usos (conta_id, assinante_id, competencia, evento_id)
                        values (%s,%s,%s,%s) on conflict (evento_id) do nothing returning id""",
                     (conta_id, a["assinante_id"], comp, evento_id)).fetchone() is not None


# ------------------------------------------------------------------ a mensalidade

def _vencimento(comp: date, dia: int, inicio: date) -> date:
    v = comp.replace(day=dia)
    return max(v, inicio) if (comp.year, comp.month) == (inicio.year, inicio.month) else v


def _titulo(pool, conta_id: int, paciente: str, fone: str, plano_nome: str, valor: int, venc: date,
            comp: date) -> int | None:
    from finance import empresa as _emp
    cliente_id = None
    try:
        from finance import clientes as _cli
        achado = _cli.buscar_unico_por_telefone(pool, conta_id, fone) if fone else None
        cliente_id = achado["id"] if achado else _cli.criar_cliente(pool, conta_id, paciente, telefone=fone or None)
    except Exception:  # noqa: BLE001 — sem a base de clientes, o título vai sem a ficha
        cliente_id = None
    try:
        t = _emp.criar_titulo(pool, conta_id, "receber",
                              f"Assinatura {plano_nome} · {paciente} — {MESES[comp.month - 1]}/{comp.year}",
                              int(valor), venc, contraparte=paciente, categoria="Vendas", cliente_id=cliente_id)
        return t["id"]
    except Exception:  # noqa: BLE001 — a mensalidade fica registrada; o próximo ciclo tenta de novo
        _log.warning("assinaturas: título não criado (%s)", paciente, exc_info=True)
        return None


def gerar(pool, conta_id: int, hoje: date, so: int | None = None) -> int:
    """Lança a mensalidade de cada mês (do início da assinatura até o mês de hoje,
    olhando no máximo MESES_ATRAS pra trás) que ainda não saiu. Idempotente: a linha
    da mensalidade é única por mês; o título vai depois, e o que falhou é refeito."""
    comp_hoje = hoje.replace(day=1)
    desde = comp_hoje
    for _ in range(MESES_ATRAS):
        desde = (desde - timedelta(days=1)).replace(day=1)
    novos = []
    with pool.connection() as c:
        rows = c.execute(
            """select a.id, a.preco_centavos, a.dia_cobranca, a.inicio, a.paciente_nome, a.paciente_fone, p.nome
                 from clinica_assinantes a
                 join clinica_assinatura_planos p on p.id = a.plano_id and p.conta_id = a.conta_id
                where a.conta_id=%s and a.estado='ativa' and a.inicio < %s
                  and (%s::bigint is null or a.id = %s)""",
            (conta_id, _mes_seguinte(comp_hoje), so, so)).fetchall()
        for aid, preco, dia, ini, pac, fone, pnome in rows:
            comp = max(desde, ini.replace(day=1))
            while comp <= comp_hoje:
                venc = _vencimento(comp, int(dia), ini)
                m = c.execute(
                    """insert into clinica_assinatura_mensalidades (conta_id, assinante_id, competencia, vencimento,
                                                                   valor_centavos)
                       values (%s,%s,%s,%s,%s) on conflict (assinante_id, competencia) do nothing returning id""",
                    (conta_id, aid, comp, venc, preco)).fetchone()
                if m:
                    novos.append((m[0], pac, fone, pnome, preco, venc, comp))
                comp = _mes_seguinte(comp)
        # o título que não saiu da outra vez (o banco caiu no meio)
        for mid, pac, fone, pnome, valor, venc, comp in c.execute(
                """select m.id, a.paciente_nome, a.paciente_fone, p.nome, m.valor_centavos, m.vencimento, m.competencia
                     from clinica_assinatura_mensalidades m
                     join clinica_assinantes a on a.id = m.assinante_id and a.conta_id = m.conta_id
                     join clinica_assinatura_planos p on p.id = a.plano_id and p.conta_id = a.conta_id
                    where m.conta_id=%s and m.titulo_id is null and m.criado_em < now() - interval '10 minutes'
                      and (%s::bigint is null or a.id = %s)""", (conta_id, so, so)).fetchall():
            novos.append((mid, pac, fone, pnome, valor, venc, comp))
        c.commit()
    for mid, pac, fone, pnome, valor, venc, comp in novos:
        tid = _titulo(pool, conta_id, pac, fone, pnome, valor, venc, comp)
        if tid:
            with pool.connection() as c:
                c.execute("update clinica_assinatura_mensalidades set titulo_id=%s where id=%s and conta_id=%s",
                          (tid, mid, conta_id))
                c.commit()
    return len(novos)


def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "mensalidades": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            "select distinct conta_id from clinica_assinantes where estado='ativa'").fetchall()]
                except Exception:  # noqa: BLE001 — sem a 384
                    contas = []
            for conta_id in contas:
                try:
                    total["mensalidades"] += gerar(pool, conta_id, ca.hoje_br(agora))
                    total["contas"] += 1
                except Exception:  # noqa: BLE001
                    _log.warning("assinaturas: conta %s falhou", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
