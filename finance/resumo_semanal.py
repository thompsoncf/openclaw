"""A semana da empresa, por e-mail (migração 274).

O PEDIDO (dono, 17/09/2026): "vamos criar o email pro gestor, semana de
acompanhamento da sua empresa". Mockup aprovado:
`docs/mockups/email_semanal_do_gestor.html`.

TRÊS DESTINATÁRIOS, TRÊS E-MAILS, escolha dele:

* o DONO recebe tudo, com a tabela por vendedor COM NOME;
* o GESTOR (e-mail cadastrado na engrenagem) recebe a mesma semana com o total da
  equipe, sem os nomes;
* o VENDEDOR recebe só a carteira dele, sem comparação com colega nenhum.

"FECHOU" É CONTRATO ASSINADO, E NÃO ETAPA DO FUNIL. Isto não é detalhe: o primeiro
rascunho deste resumo media pelo `funil_movimentos.para = 'ganho'` e ia anunciar
"nenhuma venda fechada, o Pedro e a Jacqueline não propuseram nenhum" numa semana
em que os dois fecharam R$ 19.000 em contrato. A Prime não move o card pra Ganho —
assina o contrato e deixa o lead onde estava; dos quatro contratos assinados
naquela semana, dois seguem em "Proposta". É a mesma queixa que o dono trouxe do
Follow-up ("tem cliente que já fechou mas está como contato").

A lição, que vale pra qualquer número que saia daqui: **um número medido no lugar
errado não erra sozinho — ele acusa alguém**, por e-mail, no nome de gente de
verdade.

OS NÚMEROS SAEM DO RAIO-X (`raio_x_dono.dono`), e não de consultas próprias. O
e-mail e a tela têm que dizer a mesma coisa; duas leituras do mesmo fato divergem
no dia em que uma delas mudar — foi assim que o defeito do ticket por tipo (#710)
nasceu. O que este módulo acrescenta é só o que o Raio-X não tem por não ser
pergunta de tela: o que está agendado pros PRÓXIMOS dias e o contrato por vendedor.

SAI PELA CAIXA DA PRÓPRIA EMPRESA (`email_inbound.enviar_conta`), e não pelo SMTP
do Zaq. Decisão do dono: "é melhor usar o que já funciona por dentro do Zaq em vez
de configurar toda hora". Com isso o cron precisa só de `DATABASE_URL`, e o resumo
chega com o rosto de quem ele fala. O SMTP global fica de rede pra conta que ainda
não ligou caixa nenhuma — ver `_mandar`.

NASCE DESLIGADO e a semana sem movimento não vira e-mail. Os dois são a mesma
regra, aprendida no dia anterior com os avisos: 30 publicados em 7 dias, o dono com
47 por ler e ZERO lidos. Canal que fala quando não tem o que dizer ensina a ser
ignorado.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

_log = logging.getLogger(__name__)

#: -3h fixo, como o resto do painel. O país não tem horário de verão desde 2019.
_BR = timezone(timedelta(hours=-3))

#: Quantos e-mails de gestor uma conta pode cadastrar. Não é limite técnico: é o
#: ponto em que "quem acompanha a operação" vira lista de distribuição, e aí o
#: resumo passa a ser boletim, que é outro produto.
EMAILS_MAX = 5

#: Os dias possíveis. 'segunda' é o padrão e a escolha do dono: a semana que começa
#: — as festas dos próximos dias viram plano, não retrospectiva.
DIAS = ("segunda", "sexta")

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")

_PADRAO = {"resumo_semanal": False, "resumo_semanal_emails": "",
           "resumo_semanal_vendedor": True, "resumo_semanal_dia": "segunda"}


# ------------------------------------------------------------------ a semana

def chave_semana(quando) -> str:
    """'2026-W38', em hora de Brasília. Mesma convenção de `novidades.semana_de`:
    a semana de uma entrega é a de quem a recebeu, não a do servidor."""
    if getattr(quando, "tzinfo", None) is None:
        quando = quando.replace(tzinfo=timezone.utc)
    loc = quando.astimezone(_BR)
    ano, num, _ = loc.isocalendar()
    return f"{ano}-W{num:02d}"


def semana_passada(agora: datetime | None = None) -> tuple[date, date]:
    """A segunda e o domingo da semana ANTERIOR à de `agora`, em Brasília.

    É sempre a semana fechada, mesmo rodando na sexta: resumo de semana pela
    metade compara meia semana com uma inteira, e todo número desce sem motivo.
    """
    agora = (agora or datetime.now(timezone.utc)).astimezone(_BR)
    seg_desta = agora.date() - timedelta(days=agora.weekday())
    seg = seg_desta - timedelta(days=7)
    return seg, seg + timedelta(days=6)


def rotulo_periodo(ini: date, fim: date) -> str:
    if ini.month == fim.month:
        return f"{ini:%d} a {fim:%d de %B}".replace("January", "janeiro")
    return f"{ini:%d/%m} a {fim:%d/%m}"


# ------------------------------------------------------------------ configuração

def parse_emails(texto: str | None) -> list[str]:
    """Os e-mails de gestor, de um campo de texto livre.

    Aceita vírgula, ponto-e-vírgula e quebra de linha porque é assim que as
    pessoas colam — e o que não parece e-mail é DESCARTADO em silêncio em vez de
    virar erro na tela: um espaço a mais no fim da lista não pode impedir o dono
    de salvar o resto da configuração da aba inteira, que é um formulário só.
    """
    if not texto:
        return []
    bruto = [p.strip().lower() for p in re.split(r"[,;\n]+", texto)]
    fora, vistos = [], set()
    for e in bruto:
        if e and _EMAIL_RE.match(e) and e not in vistos:
            vistos.add(e)
            fora.append(e)
    return fora[:EMAILS_MAX]


def config(pool, conta_id: int) -> dict:
    """A configuração da conta. FALHA FECHADA, ao contrário da faixa de novidades:
    sem a coluna ou sem banco devolve DESLIGADO. Um parâmetro que não pôde ser lido
    não pode fazer a gente mandar e-mail pra fora — o pior caso aqui não é uma
    faixa a mais na tela, é a caixa de entrada de um cliente."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select resumo_semanal, resumo_semanal_emails,
                          resumo_semanal_vendedor, resumo_semanal_dia
                     from contas where id=%s""", (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — base sem a 274 ainda
        _log.warning("config do resumo semanal da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return dict(_PADRAO, emails=[])
    if not r:
        return dict(_PADRAO, emails=[])
    return {"resumo_semanal": bool(r[0]), "resumo_semanal_emails": r[1] or "",
            "resumo_semanal_vendedor": bool(r[2]),
            "resumo_semanal_dia": r[3] if r[3] in DIAS else "segunda",
            "emails": parse_emails(r[1])}


def salvar_config(pool, conta_id: int, *, ativo: bool, emails: str,
                  vendedor: bool, dia: str) -> dict:
    """Grava o que a engrenagem mandou. Devolve a config já normalizada, pra tela
    mostrar o que de fato ficou — e não o que a pessoa digitou."""
    limpos = parse_emails(emails)
    dia = dia if dia in DIAS else "segunda"
    with pool.connection() as c:
        c.execute("""update contas set resumo_semanal=%s, resumo_semanal_emails=%s,
                            resumo_semanal_vendedor=%s, resumo_semanal_dia=%s
                      where id=%s""",
                  (bool(ativo), ", ".join(limpos), bool(vendedor), dia, conta_id))
        c.commit()
    return {"resumo_semanal": bool(ativo), "resumo_semanal_emails": ", ".join(limpos),
            "resumo_semanal_vendedor": bool(vendedor), "resumo_semanal_dia": dia,
            "emails": limpos}


# ------------------------------------------------------------------ os números

def _extras(pool, conta_id: int, ini: datetime, fim: datetime, agora: datetime) -> dict:
    """O que o Raio-X não calcula por não ser pergunta de tela.

    Duas famílias: o que está AGENDADO pra frente (a tela do dono olha o período
    que passou) e o CONTRATO POR VENDEDOR (o Raio-X tem `sua_semana`, que conta
    proposta e não contrato — e é o contrato que diz quem fechou).

    Best-effort por bloco: o resumo sai incompleto antes de não sair.
    """
    out = {"visitas_proximas": 0, "festas_proximas": 0, "festa30_sem_contrato": 0,
           "sem_data": 0, "titulos_vencidos": 0, "titulos_vencidos_valor": 0,
           "sinal_pago": 0, "por_vendedor": []}
    with pool.connection() as c:
        def _um(sql, args, chave):
            try:
                with c.transaction():
                    r = c.execute(sql, args).fetchone()
                out[chave] = int(r[0] or 0) if r else 0
            except Exception as e:  # noqa: BLE001
                _log.warning("resumo semanal, bloco %s da conta %s: %s: %s",
                             chave, conta_id, type(e).__name__, e)

        _um("""select count(*) from eventos_agenda e join prospeccao p on p.id = e.prospeccao_id
                where e.conta_id=%s and e.tipo='empresa' and e.tipo_evento is null
                  and coalesce(e.status,'ativo')='ativo'
                  and e.inicio >= %s and e.inicio < %s""",
            (conta_id, agora, agora + timedelta(days=7)), "visitas_proximas")
        _um("""select count(*) from eventos_agenda e
                where e.conta_id=%s and e.tipo_evento is not null
                  and coalesce(e.status,'ativo')='ativo'
                  and e.inicio >= %s and e.inicio < %s""",
            (conta_id, agora, agora + timedelta(days=7)), "festas_proximas")
        # festa perto e sem contrato: é onde o dinheiro está mais perto e a data
        # trabalha contra. Sem contrato, não "sem proposta" — ver o docstring.
        _um("""select count(*) from prospeccao p
                where p.conta_id=%s and p.status not in ('ganho','perdido')
                  and p.evento_em >= %s and p.evento_em < %s
                  and not exists (select 1 from contratos c
                                    join orcamentos o on o.id = c.orcamento_id
                                   where o.id = p.orcamento_id and c.assinado_em is not null)""",
            (conta_id, agora.date(), agora.date() + timedelta(days=30)), "festa30_sem_contrato")
        _um("""select count(*) from prospeccao p
                where p.conta_id=%s and p.status not in ('ganho','perdido')
                  and p.evento_em is null""", (conta_id,), "sem_data")
        _um("""select count(*) from titulos t where t.conta_id=%s and t.tipo='receber'
                 and t.status='aberto' and t.vencimento < %s""",
            (conta_id, agora.date()), "titulos_vencidos")
        _um("""select coalesce(sum(t.valor_centavos),0) from titulos t
                where t.conta_id=%s and t.tipo='receber' and t.status='aberto'
                  and t.vencimento < %s""", (conta_id, agora.date()), "titulos_vencidos_valor")
        _um("""select count(*) from orcamentos o
                where o.conta_id=%s and o.sinal_pago_em >= %s and o.sinal_pago_em < %s""",
            (conta_id, ini, fim), "sinal_pago")

        try:
            with c.transaction():
                rows = c.execute("""
                    select m.id, coalesce(nullif(m.nome,''), m.email),
                           (select count(*) from prospeccao p
                             where p.vendedor_id = m.id and p.criado_em >= %s and p.criado_em < %s),
                           (select count(*) from eventos_agenda e
                              join prospeccao p on p.id = e.prospeccao_id
                             where p.vendedor_id = m.id and e.tipo='empresa' and e.tipo_evento is null
                               and coalesce(e.status,'ativo')='ativo' and e.desfecho='realizado'
                               and e.inicio >= %s and e.inicio < %s),
                           (select count(*) from contratos c
                              join orcamentos o on o.id = c.orcamento_id
                              join prospeccao p on p.orcamento_id = o.id
                             where p.vendedor_id = m.id and c.assinado_em >= %s and c.assinado_em < %s),
                           (select coalesce(sum(c.valor_centavos),0) from contratos c
                              join orcamentos o on o.id = c.orcamento_id
                              join prospeccao p on p.orcamento_id = o.id
                             where p.vendedor_id = m.id and c.assinado_em >= %s and c.assinado_em < %s),
                           (select count(*) from prospeccao p
                             where p.vendedor_id = m.id and p.status not in ('ganho','perdido'))
                      from membros m
                     where m.conta_id=%s and coalesce(m.ativo,true) and m.papel='vendedor'
                     order by 2""",
                    (ini, fim, ini, fim, ini, fim, ini, fim, conta_id)).fetchall()
            out["por_vendedor"] = [
                {"id": r[0], "nome": r[1], "primeiro": (r[1] or "—").split(" ")[0],
                 "entraram": int(r[2]), "visitas": int(r[3]), "fechou": int(r[4]),
                 "valor": int(r[5]), "carteira": int(r[6])} for r in rows]
        except Exception as e:  # noqa: BLE001
            _log.warning("resumo semanal, por vendedor da conta %s: %s: %s",
                         conta_id, type(e).__name__, e)
    return out


def montar(pool, conta_id: int, agora: datetime | None = None) -> dict | None:
    """A semana da conta, pronta pra virar e-mail. `None` = não há o que mandar.

    Devolver None é uma resposta de primeira classe, não uma falha: semana sem
    NENHUM movimento e sem NADA travado não vira e-mail. A alternativa — mandar uma
    tabela de zeros toda segunda — é exatamente o que ensina a ignorar o
    remetente, e eu tenho a medição do dia anterior provando isso com os avisos.

    Semana com pouco movimento mas com carteira parada AINDA VAI: quando nada
    entrou, o que já está dentro é justamente o assunto.
    """
    from finance import raio_x_dono as rxd
    agora = agora or datetime.now(timezone.utc)
    ini_d, fim_d = semana_passada(agora)
    perfil = rxd.perfil_da_conta(pool, conta_id)
    f = rxd.filtros({"periodo": "datas", "de": ini_d.isoformat(), "ate": fim_d.isoformat()},
                    perfil)
    d = rxd.dono(pool, conta_id, f, agora, perfil=perfil)
    p, ant = d.get("placar") or {}, d.get("anterior") or {}
    if not p:
        return None
    ex = _extras(pool, conta_id, d["ini"], d["fim"], agora)

    movimento = sum(int(p.get(k) or 0) for k in
                    ("leads", "propostas", "contratos", "visitas_ok"))
    travado = (ex["festa30_sem_contrato"] + ex["sem_data"] + ex["titulos_vencidos"]
               + int(p.get("rascunhos") or 0) + int(p.get("sem_assinar") or 0))
    if not movimento and not travado:
        return None

    return {"conta_id": conta_id, "perfil": perfil, "semana": chave_semana(d["ini"]),
            "ini": ini_d, "fim": fim_d, "rotulo": rotulo_periodo(ini_d, fim_d),
            "placar": p, "anterior": ant, "extras": ex,
            "vazia": not movimento, "vende_data": bool(perfil.get("vocab", {}).get("data"))}


# ------------------------------------------------------------------ quem recebe

def destinatarios(pool, conta_id: int, cfg: dict | None = None) -> list[dict]:
    """Pra quem vai, e com qual cara. Três tipos — ver o docstring do módulo.

    O DONO vem do cadastro (`membros.papel='dono'`), o GESTOR do campo de texto, e
    o vendedor só se a conta tiver ligado. E-mail repetido entre eles fica com o
    papel de MAIOR alcance: quem é dono e também está no campo de gestor recebe um
    e-mail só, o do dono — dois e-mails quase iguais no mesmo minuto é o tipo de
    coisa que faz a pessoa desligar tudo.
    """
    cfg = cfg or config(pool, conta_id)
    if not cfg.get("resumo_semanal"):
        return []
    fora: dict[str, dict] = {}
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select coalesce(nullif(nome,''), email), email, papel, id
                     from membros
                    where conta_id=%s and coalesce(ativo,true)
                      and papel in ('dono','gestor','vendedor')
                      and coalesce(email,'') <> ''""", (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("destinatários do resumo da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        rows = []
    for nome, email, papel, mid in rows:
        if papel == "vendedor" and not cfg.get("resumo_semanal_vendedor"):
            continue
        tipo = "dono" if papel in ("dono", "gestor") else "vendedor"
        fora[email.lower()] = {"email": email.lower(), "nome": nome, "tipo": tipo,
                               "membro_id": mid}
    for e in cfg.get("emails") or []:
        if e not in fora:            # quem já é dono/gestor no cadastro não vira gestor-texto
            fora[e] = {"email": e, "nome": "", "tipo": "gestor", "membro_id": None}
    ordem = {"dono": 0, "gestor": 1, "vendedor": 2}
    return sorted(fora.values(), key=lambda x: (ordem[x["tipo"]], x["email"]))


def ja_enviado(pool, conta_id: int, semana: str, destino: str) -> bool:
    """Já mandou este resumo pra esta pessoa? A trava de verdade é a chave primária
    de `resumo_semanal_envio`; esta leitura só evita montar o e-mail à toa."""
    try:
        with pool.connection() as c:
            r = c.execute("""select 1 from resumo_semanal_envio
                              where conta_id=%s and semana=%s and destino=%s""",
                          (conta_id, semana, destino)).fetchone()
        return bool(r)
    except Exception as e:  # noqa: BLE001
        _log.warning("registro de envio (%s/%s): %s: %s", conta_id, semana,
                     type(e).__name__, e)
        # FALHA FECHADA: sem conseguir ler o registro, NÃO manda. Repetir o e-mail
        # é pior que atrasar — e a semana seguinte corrige sozinha.
        return True


def anotar_envio(pool, conta_id: int, semana: str, destino: str, tipo: str,
                 ok: bool = True, motivo: str = "") -> None:
    try:
        with pool.connection() as c:
            c.execute("""insert into resumo_semanal_envio
                           (conta_id, semana, destino, tipo, ok, motivo)
                         values (%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                      (conta_id, semana, destino, tipo, bool(ok), (motivo or "")[:300] or None))
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("anotar envio (%s/%s/%s): %s: %s", conta_id, semana, destino,
                     type(e).__name__, e)


# ------------------------------------------------------------------ o envio

def _nome_conta(pool, conta_id: int) -> str:
    try:
        with pool.connection() as c:
            r = c.execute("select coalesce(nullif(nome_fantasia,''), nome, '') "
                          "  from contas where id=%s", (conta_id,)).fetchone()
        return (r[0] if r else "") or ""
    except Exception:  # noqa: BLE001 — o assunto vira genérico, e está tudo bem
        return ""


def _mandar(pool, conta_id: int, destino: str, assunto: str, html: str,
            empresa: str) -> tuple[bool, str]:
    """Manda, PELA CAIXA DA PRÓPRIA EMPRESA quando ela tem uma.

    Decisão do dono em 17/09/2026, ao ver o passo a passo do Render que eu tinha
    escrito: "é melhor usar o que já funciona por dentro do Zaq, em vez de
    configurar toda hora — o Zaq usa o e-mail da empresa pra esse tipo de
    relatório, e no caso da Prime já está configurado". Ele estava certo, e isso
    apaga um passo inteiro da instalação:

    * a Prime já tem `primeeventosthe@gmail.com` e a ZAQ, `thorconsultoria01@gmail.com`,
      as duas ativas em `canais_config` — nada a configurar;
    * o cron passa a precisar só de `DATABASE_URL`. Sem SMTP no serviço novo, sem
      senha copiada de um lugar pro outro, sem o nome de variável errado que eu
      mesmo documentei (era `SMTP_PASS`; o certo é `SMTP_SENHA`);
    * e o resumo chega COM O ROSTO DA EMPRESA, que é de quem ele fala.

    O SMTP GLOBAL É SÓ A REDE, e não o caminho normal. `email_inbound.enviar_conta`
    se recusa a cair nele de propósito — mas aquela regra é sobre e-mail que vai
    pro LEAD, que não pode sair da caixa de outra conta. Aqui quem recebe é o dono
    da própria conta, e um resumo que não chega porque a empresa ainda não ligou a
    caixa seria pior que um resumo que chega assinado pelo Zaq.

    Devolve (ok, por_onde) — e o `por_onde` fica gravado em
    `resumo_semanal_envio.motivo`, porque "de qual caixa isso saiu" é a primeira
    pergunta quando alguém não recebe.
    """
    try:
        from finance import email_inbound as _ei
        if _ei.enviar_conta(pool, conta_id, destino, assunto, html,
                            from_nome=empresa or "Zaq"):
            return True, "caixa da empresa"
    except Exception as e:  # noqa: BLE001 — cai na rede abaixo
        _log.warning("resumo pela caixa da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
    try:
        from finance import email_sender as _es
        if _es.enviar_email(destino, assunto, html):
            return True, "smtp do zaq"
    except Exception as e:  # noqa: BLE001
        _log.warning("resumo pelo smtp global (conta %s): %s: %s",
                     conta_id, type(e).__name__, e)
    return False, "sem caixa de e-mail"


def enviar_conta(pool, conta_id: int, agora: datetime | None = None,
                 *, simular: bool = False) -> dict:
    """Manda o resumo da semana passada pra uma conta. Devolve o que aconteceu.

    `simular=True` monta tudo e NÃO envia: é como se confere a cara do e-mail numa
    conta real sem gastar a caixa de entrada de ninguém. A trava de repetição
    também não é gravada, então dá pra rodar quantas vezes precisar.

    TOLERA FALHA POR DESTINATÁRIO: um e-mail que o SMTP recusou não pode impedir os
    outros de sair. Cada envio vira uma linha em `resumo_semanal_envio`, inclusive
    o que falhou e por quê — sem isso, "por que fulano não recebeu" não tem
    resposta.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = config(pool, conta_id)
    if not cfg["resumo_semanal"]:
        return {"ok": True, "enviados": 0, "motivo": "desligado"}

    dados = montar(pool, conta_id, agora)
    if dados is None:
        return {"ok": True, "enviados": 0, "motivo": "semana_sem_movimento"}

    from finance import resumo_semanal_html as _html
    empresa = _nome_conta(pool, conta_id)
    semana = dados["semana"]
    assunto_base = f"{empresa or 'Sua empresa'} · a semana de {dados['rotulo']}"
    assin = int((dados["placar"] or {}).get("contratos") or 0)
    if assin:
        assunto_base = (f"{empresa or 'Sua empresa'} · {assin} contrato"
                        f"{'s' if assin != 1 else ''} assinado{'s' if assin != 1 else ''} na semana")

    enviados, falhas = 0, []
    for d in destinatarios(pool, conta_id, cfg):
        if not simular and ja_enviado(pool, conta_id, semana, d["email"]):
            continue
        try:
            html = _html.corpo(dados, d["tipo"], nome=d["nome"], empresa=empresa,
                               membro_id=d["membro_id"])
            assunto = (f"Sua semana · {dados['rotulo']}" if d["tipo"] == "vendedor"
                       else assunto_base)
            if simular:
                enviados += 1
                continue
            ok, por_onde = _mandar(pool, conta_id, d["email"], assunto, html, empresa)
            anotar_envio(pool, conta_id, semana, d["email"], d["tipo"], ok, por_onde)
            enviados += 1 if ok else 0
            if not ok:
                falhas.append(d["email"])
        except Exception as e:  # noqa: BLE001 — um destinatário não derruba os outros
            _log.warning("resumo semanal pra %s (conta %s): %s: %s",
                         d["email"], conta_id, type(e).__name__, e)
            if not simular:
                anotar_envio(pool, conta_id, semana, d["email"], d["tipo"], False,
                             f"{type(e).__name__}: {e}")
            falhas.append(d["email"])
    return {"ok": True, "enviados": enviados, "falhas": falhas, "semana": semana,
            "simulado": simular}


def contas_do_dia(pool, agora: datetime | None = None) -> list[int]:
    """As contas que devem receber HOJE. O dia de cada uma está na configuração
    dela — o cron roda todo dia e pergunta, em vez de existirem dois crons."""
    agora = (agora or datetime.now(timezone.utc)).astimezone(_BR)
    hoje = DIAS[0] if agora.weekday() == 0 else (DIAS[1] if agora.weekday() == 4 else "")
    if not hoje:
        return []
    try:
        with pool.connection() as c:
            rows = c.execute("""select id from contas
                                 where resumo_semanal and resumo_semanal_dia=%s
                                 order by id""", (hoje,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("contas do resumo semanal: %s: %s", type(e).__name__, e)
        return []
    return [int(r[0]) for r in rows]
