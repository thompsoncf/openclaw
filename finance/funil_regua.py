"""A régua do funil: de quem é a bola, e o que move o card sozinho.

POR QUE ELA LÊ A CONVERSA, E NÃO A "ATIVIDADE REGISTRADA"
Medido na conta 34 em 18/08/2026: 81 leads parados em "Novo", e 74 deles JÁ tinham
resposta nossa na conversa. Os vendedores atendem — mediana de 12 a 18 minutos em
610 mensagens de 21 dias. O que ninguém faz é arrastar o card, e "registrar
atividade" teve 5 usos em toda a história da conta. Uma régua que medisse atividade
registrada acusaria 79 leads abandonados que na verdade estão sendo atendidos, e a
equipe desligaria tudo no primeiro dia. Aqui o relógio lê `mensagens` — inclusive o
eco fromMe do WhatsApp, que grava o que o vendedor responde pelo próprio celular.

OS QUATRO ESTADOS
    sem_resposta   entrou e ninguém falou nada (nem painel, nem celular)
    bola_nossa     o cliente falou por último e ninguém respondeu
    bola_cliente   respondemos e ele sumiu — vira follow-up, não cobrança
    ok             conversa em dia

AS QUATRO TRAVAS DO AUTOMÁTICO (todas exercitadas em tests/test_funil_regua.py)
 1. Gatilho só empurra pra frente: etapa de trás nunca puxa card.
 2. Empate ganha a etapa mais avançada: dois eventos no mesmo ciclo não deixam o
    lead no meio do caminho.
 3. A mão do vendedor manda: evento anterior ao último movimento manual não reverte
    a decisão de quem mexeu no card.
 4. Todo salto vira linha em `funil_movimentos` — inclusive o salto SIMULADO do
    modo observação, que é como se descobre o que a régua faria antes de ligá-la.

NASCE DESLIGADA. `gatilhos_modo` e `cobranca_modo` começam 'off' e cada etapa
começa com `gatilho_ativo=false`: o dono liga um de cada vez. O único que roda com
tudo off é o registro do histórico — anotar o que humanos já fazem não é automação.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

_log = logging.getLogger("openclaw.funil_regua")

# O painel inteiro já trata Brasília como -3 fixo (ver painel_prospeccao._hora_br:
# o país não tem horário de verão desde 2019). A janela de atendimento usa a mesma
# conta — duas convenções de fuso no mesmo produto seria pior que o erro de uma.
_UTC_BR = timedelta(hours=-3)

MODOS = ("off", "observando", "ligado")
FASES = ("venda", "fechamento", "pos")

# Todo evento aqui é um fato que o sistema JÁ grava. A chave é o que vai em
# funil_etapas.gatilho; o rótulo é o que a tela mostra.
EVENTOS = {
    "resposta_nossa":    "primeira resposta nossa na conversa (painel, Cockpit ou celular)",
    "compromisso":       "compromisso criado na agenda vinculado ao lead",
    "compromisso_feito": "compromisso marcado como realizado na agenda",
    "orcamento_enviado": "orçamento enviado ao cliente",
    "orcamento_aprovado": "cliente aprovou a proposta no link público",
    "sinal_pago":        "sinal registrado como pago no orçamento",
    "contrato_assinado": "contrato assinado no link público",
}

_PADRAO = {
    "gatilhos_modo": "off", "cobranca_modo": "off",
    "janela_dias": "1,2,3,4,5,6", "janela_abre": time(8, 0), "janela_fecha": time(19, 0),
    "sem_resposta_min": 120, "bola_nossa_min": 240, "bola_cliente_min": 4320,
    "escala_min": 240, "teto_avisos_dia": 5,
}


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    """Config da régua da conta, semeando o padrão (tudo desligado) na 1ª vez."""
    c.execute("insert into funil_regua (conta_id) values (%s) on conflict (conta_id) do nothing",
              (conta_id,))
    r = c.execute(
        """select gatilhos_modo, cobranca_modo, janela_dias, janela_abre, janela_fecha,
                  sem_resposta_min, bola_nossa_min, bola_cliente_min, escala_min, teto_avisos_dia
             from funil_regua where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(_PADRAO)
    return {"gatilhos_modo": r[0], "cobranca_modo": r[1], "janela_dias": r[2],
            "janela_abre": r[3], "janela_fecha": r[4], "sem_resposta_min": r[5],
            "bola_nossa_min": r[6], "bola_cliente_min": r[7], "escala_min": r[8],
            "teto_avisos_dia": r[9]}


def _dias(cfg) -> set[int]:
    """Dias ISO da janela (1=segunda). String vazia = nenhum dia, e nenhum dia
    significa que nada vence — o que é o comportamento certo pra quem desligou a
    semana toda, e não 'vence sempre'."""
    out = set()
    for p in (cfg.get("janela_dias") or "").split(","):
        p = p.strip()
        if p.isdigit() and 1 <= int(p) <= 7:
            out.add(int(p))
    return out


# ------------------------------------------------------------------ o relógio

def minutos_uteis(inicio: datetime, fim: datetime, cfg: dict) -> int:
    """Minutos de ATENDIMENTO entre dois instantes (UTC).

    Lead que chega 22h de sábado só começa a contar quando o expediente abre —
    senão a régua acorda vendedor de madrugada e vira barulho no primeiro dia.
    Fora da janela o cronômetro não anda; ele não zera.
    """
    if not inicio or not fim or fim <= inicio:
        return 0
    dias, abre, fecha = _dias(cfg), cfg["janela_abre"], cfg["janela_fecha"]
    if not dias or abre >= fecha:
        return 0
    ini, f = inicio + _UTC_BR, fim + _UTC_BR       # trabalha em hora local
    total = 0
    dia = ini.date()
    # teto de segurança: um lead esquecido há dois anos não pode virar um laço de
    # 700 iterações a cada ciclo do poller, a cada lead.
    for _ in range(400):
        if dia > f.date():
            break
        if dia.isoweekday() in dias:
            a = datetime.combine(dia, abre)
            b = datetime.combine(dia, fecha)
            ini_n = ini.replace(tzinfo=None)
            fim_n = f.replace(tzinfo=None)
            corte_ini = max(a, ini_n)
            corte_fim = min(b, fim_n)
            if corte_fim > corte_ini:
                total += int((corte_fim - corte_ini).total_seconds() // 60)
        dia += timedelta(days=1)
    return total


def dentro_da_janela(quando: datetime, cfg: dict) -> bool:
    """A empresa está aberta neste instante? Vale pra COBRANÇA (que acorda gente),
    nunca pro gatilho: fato é fato a qualquer hora — sinal pago às 23h move o card
    às 23h."""
    loc = quando + _UTC_BR
    if loc.isoweekday() not in _dias(cfg):
        return False
    return cfg["janela_abre"] <= loc.time() < cfg["janela_fecha"]


# ------------------------------------------------------------------ a bola

def estado_da_bola(c, lead_id: int) -> tuple[str, datetime | None]:
    """De quem é a vez de falar. Devolve (estado, ref_em).

    `ref_em` é o instante do fato que ligou o cronômetro — a mensagem do cliente
    que ficou sem resposta, ou a nossa que ficou sem retorno. É ele que faz o
    dedup do aviso ser por FATO e não por lead: o cliente volta a escrever daqui a
    um mês, a referência muda, e o aviso pode sair de novo.
    """
    r = c.execute(
        """select max(m.criado_em) filter (where m.direcao='in')  as ult_in,
                  max(m.criado_em) filter (where m.direcao='out') as ult_out,
                  min(m.criado_em)                                as primeira
             from mensagens m join conversas cv on cv.id = m.conversa_id
            where cv.prospeccao_id = %s""", (lead_id,)).fetchone()
    ult_in, ult_out, primeira = (r or (None, None, None))
    if ult_out is None:
        # Nunca falamos nada. Sem conversa nenhuma o cronômetro corre desde que o
        # lead nasceu — é o caso do cadastro manual, que não tem mensagem alguma.
        return "sem_resposta", (ult_in or primeira)
    if ult_in is not None and ult_in > ult_out:
        return "bola_nossa", ult_in
    return "bola_cliente", ult_out


def prazo_do_estado(estado: str, cfg: dict) -> int | None:
    return {"sem_resposta": cfg["sem_resposta_min"],
            "bola_nossa": cfg["bola_nossa_min"],
            "bola_cliente": cfg["bola_cliente_min"]}.get(estado)


# ------------------------------------------------------------------ movimento

def registrar_movimento(c, conta_id: int, lead_id: int, de: str | None, para: str,
                        motivo: str, membro_id: int | None = None) -> None:
    """A linha do histórico que o banco nunca teve. Roda mesmo com a régua
    desligada: sem ela ninguém consegue dizer quanto um lead ficou em cada etapa —
    nem hoje, nem olhando pra trás."""
    if de == para:
        return
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, membro_id)
                 values (%s,%s,%s,%s,%s,%s)""", (conta_id, lead_id, de, para, motivo, membro_id))


def _ultimo_manual(c, lead_id: int):
    r = c.execute("""select criado_em from funil_movimentos
                      where prospeccao_id=%s and motivo='manual'
                      order by criado_em desc limit 1""", (lead_id,)).fetchone()
    return r[0] if r else None


def escolher_etapa(atual_ordem: int, candidatas: list[dict]) -> dict | None:
    """TRAVAS 1 e 2, sem banco no meio pra dar pra testar sozinho.

    `candidatas` são as etapas cujo gatilho disparou neste ciclo, cada uma
    {chave, ordem}. Fica a de MAIOR ordem entre as que estão à frente da atual —
    empate nunca deixa o lead no meio do caminho, e etapa de trás nunca puxa card.
    """
    frente = [e for e in candidatas if e["ordem"] > atual_ordem]
    if not frente:
        return None
    return max(frente, key=lambda e: e["ordem"])


# ------------------------------------------------------------------ os eventos
# Cada consulta devolve (prospeccao_id, quando) — o instante em que o fato
# aconteceu. Nada aqui foi inventado pro funil: são colunas que o produto já
# preenchia antes desta régua existir.
_SQL_EVENTO = {
    "resposta_nossa": """
        select cv.prospeccao_id, min(m.criado_em)
          from mensagens m join conversas cv on cv.id = m.conversa_id
         where cv.conta_id=%(conta)s and cv.prospeccao_id is not null and m.direcao='out'
         group by cv.prospeccao_id""",
    "compromisso": """
        select prospeccao_id, min(criado_em) from eventos_agenda
         where conta_id=%(conta)s and prospeccao_id is not null and status='ativo'
         group by prospeccao_id""",
    "compromisso_feito": """
        select prospeccao_id, max(coalesce(fim, inicio)) from eventos_agenda
         where conta_id=%(conta)s and prospeccao_id is not null and desfecho='realizado'
         group by prospeccao_id""",
    "orcamento_enviado": """
        select p.id, o.atualizado_em from prospeccao p join orcamentos o on o.id = p.orcamento_id
         where p.conta_id=%(conta)s and o.status in ('enviado','aprovada','fechado')""",
    "orcamento_aprovado": """
        select p.id, o.aprovada_em from prospeccao p join orcamentos o on o.id = p.orcamento_id
         where p.conta_id=%(conta)s and o.aprovada_em is not null""",
    "sinal_pago": """
        select p.id, o.sinal_pago_em from prospeccao p join orcamentos o on o.id = p.orcamento_id
         where p.conta_id=%(conta)s and o.sinal_pago_em is not null""",
    "contrato_assinado": """
        select p.id, o.contrato_assinado_em from prospeccao p join orcamentos o on o.id = p.orcamento_id
         where p.conta_id=%(conta)s and o.contrato_assinado_em is not null""",
}


def etapas(c, conta_id: int) -> list[dict]:
    rows = c.execute(
        """select chave, rotulo, ordem, fixa, fase, prazo_min, gatilho, gatilho_ativo, id
             from funil_etapas where conta_id=%s order by ordem, id""", (conta_id,)).fetchall()
    return [{"chave": r[0], "rotulo": r[1], "ordem": r[2], "fixa": r[3], "fase": r[4],
             "prazo_min": r[5], "gatilho": r[6], "gatilho_ativo": r[7], "id": r[8]} for r in rows]


def chaves_fechadas(etapas_: list[dict]) -> list[str]:
    """O que conta como VENDA GANHA. Era `status='ganho'` cru espalhado por
    relatório, comissão e cockpit; com a fase, a pergunta passa a ser uma só — e o
    lead que anda pra uma etapa de pós-venda continua contando como fechado, em vez
    de sumir do "ganhos do mês" no dia em que o vendedor arrasta o card."""
    return [e["chave"] for e in etapas_
            if e["fase"] in ("fechamento", "pos") and e["chave"] != "perdido"]


def _ja_registrado(c, lead_id: int, para: str, motivo: str) -> bool:
    return bool(c.execute(
        """select 1 from funil_movimentos
            where prospeccao_id=%s and para=%s and motivo=%s limit 1""",
        (lead_id, para, motivo)).fetchone())


def aplicar_gatilhos(c, conta_id: int) -> dict:
    """Uma passada de gatilhos na conta. Devolve {movidos, simulados}.

    Em 'observando' calcula tudo e grava o salto como `simulado:<evento>` sem tocar
    no card — é assim que se descobre o que a régua faria antes de deixá-la fazer.
    """
    cfg = config(c, conta_id)
    modo = cfg["gatilhos_modo"]
    if modo == "off":
        return {"movidos": 0, "simulados": 0}

    todas = etapas(c, conta_id)
    ativas = [e for e in todas if e["gatilho_ativo"] and e["gatilho"] in _SQL_EVENTO]
    if not ativas:
        return {"movidos": 0, "simulados": 0}
    ordem_de = {e["chave"]: e["ordem"] for e in todas}

    # lead -> [{chave, ordem, evento, quando}]
    candidatos: dict[int, list[dict]] = {}
    for e in ativas:
        for lead_id, quando in c.execute(_SQL_EVENTO[e["gatilho"]], {"conta": conta_id}).fetchall():
            if lead_id and quando:
                candidatos.setdefault(lead_id, []).append(
                    {"chave": e["chave"], "ordem": e["ordem"], "evento": e["gatilho"], "quando": quando})
    if not candidatos:
        return {"movidos": 0, "simulados": 0}

    atuais = dict(c.execute(
        """select id, status from prospeccao
            where conta_id=%s and estagio='lead' and id = any(%s)""",
        (conta_id, list(candidatos))).fetchall())

    movidos = simulados = 0
    for lead_id, cands in candidatos.items():
        atual = atuais.get(lead_id)
        if atual is None:                       # saiu do funil entre uma consulta e outra
            continue
        # TRAVA 3: o evento anterior ao último movimento manual não desfaz a decisão
        # de quem mexeu no card. Sem isto, o vendedor que puxa um lead de volta pra
        # "Contatado" vê o card saltar pra frente de novo no ciclo seguinte, por
        # causa de um orçamento enviado semana passada.
        corte = _ultimo_manual(c, lead_id)
        if corte:
            cands = [x for x in cands if x["quando"] > corte]
        alvo = escolher_etapa(ordem_de.get(atual, -1), cands)   # TRAVAS 1 e 2
        if not alvo:
            continue
        motivo = ("gatilho:" if modo == "ligado" else "simulado:") + alvo["evento"]
        # Sem esta guarda o modo observação reescreveria a mesma linha a cada 2
        # minutos, para sempre — e o relatório de "o que teria acontecido" viraria
        # um contador de ciclos do poller.
        if _ja_registrado(c, lead_id, alvo["chave"], motivo):
            continue
        if modo == "ligado":
            c.execute("""update prospeccao set status=%s, atualizado_em=now()
                          where id=%s and conta_id=%s""", (alvo["chave"], lead_id, conta_id))
            movidos += 1
        else:
            simulados += 1
        registrar_movimento(c, conta_id, lead_id, atual, alvo["chave"], motivo)
    return {"movidos": movidos, "simulados": simulados}


# ------------------------------------------------------------------ o motor
_LOCK = 771147   # vizinho dos locks das campanhas (771144-771146)


def rodar(pool) -> dict:
    """Uma passada da régua em todas as contas que a ligaram. Chamada pelo poller
    (web/app.py), junto de campanhas e lembretes — sem cron novo no Render.

    Best-effort por conta: uma conta com dado torto não pode parar a passada das
    outras, do mesmo jeito que uma campanha quebrada não derruba o motor inteiro.
    Devolve {contas, movidos, simulados} pro log do ciclo — 0 repetido com conta
    ligada é pista de bug, e sem esse número não haveria como perceber.
    """
    total = {"contas": 0, "movidos": 0, "simulados": 0}
    with pool.connection() as lockc:
        # Dois workers no Render: sem o lock, os dois aplicam o mesmo gatilho no
        # mesmo lead no mesmo segundo e o histórico ganha a linha em duplicidade.
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    "select conta_id from funil_regua where gatilhos_modo <> 'off'").fetchall()]
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        r = aplicar_gatilhos(c, conta_id)
                        c.commit()
                    total["contas"] += 1
                    total["movidos"] += r["movidos"]
                    total["simulados"] += r["simulados"]
                except Exception:  # noqa: BLE001
                    _log.warning("régua falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total


# ------------------------------------------------------------------ "fechado?"
# Era `status='ganho'` cru espalhado por seis arquivos. Com a fase, a pergunta
# passa a ser feita num lugar só — e o lead que anda pra uma etapa de PÓS-VENDA
# continua contando como vendido, em vez de sumir do "ganhos do mês" no dia em que
# o vendedor arrasta o card.
#
# São subconsultas CORRELACIONADAS (fe.conta_id = <alias>.conta_id) de propósito:
# assim não entra parâmetro novo em nenhuma query existente. Trocar um literal por
# um `%s` no meio de uma tupla de dez argumentos é o tipo de mudança que passa nos
# testes e sai errada em produção, com o parâmetro certo na posição errada.
#
# O `union all select 'ganho'` não é redundância: conta que ainda não semeou as
# etapas tem funil_etapas vazio, e `status in ()` faria o relatório responder que
# ninguém nunca vendeu nada. 'ganho' é fechamento por definição, sempre.

def sql_fechadas(alias: str = "p") -> str:
    """As etapas que contam como VENDA GANHA (fechamento e pós-venda, menos perdido)."""
    return (f"(select fe.chave from funil_etapas fe where fe.conta_id = {alias}.conta_id "
            "and fe.fase in ('fechamento','pos') and fe.chave <> 'perdido' "
            "union all select 'ganho')")


def sql_encerradas(alias: str = "p") -> str:
    """Tudo que saiu do jogo: fechado OU perdido. É o que os painéis usam pra dizer
    "lead em aberto" — `status not in <isto>`."""
    return (f"(select fe.chave from funil_etapas fe where fe.conta_id = {alias}.conta_id "
            "and fe.fase in ('fechamento','pos') "
            "union all select 'ganho' union all select 'perdido')")


def sql_encerradas_nao(alias: str = "p") -> str:
    """Açúcar pra `<alias>.status not in <encerradas>` — o filtro de "ainda em jogo",
    que é o mais repetido do produto."""
    return f"{alias}.status not in " + sql_encerradas(alias)
