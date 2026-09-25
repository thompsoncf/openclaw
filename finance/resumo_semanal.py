"""A semana da empresa, por e-mail (migração 274).

O PEDIDO (dono, 17/09/2026): "vamos criar o email pro gestor, semana de
acompanhamento da sua empresa". Mockup aprovado:
`docs/mockups/email_semanal_do_gestor.html`.

TRÊS DESTINATÁRIOS, TRÊS E-MAILS, escolha dele:

* o DONO recebe tudo, com a tabela por vendedor COM NOME;
* o GESTOR (e-mail cadastrado na engrenagem) recebe a mesma semana com o total da
  equipe, sem os nomes;
* o VENDEDOR recebe só a carteira dele, sem comparação com colega nenhum.

DOIS CAMPOS DE E-MAIL, E NÃO UM (migração 276). "Seu e-mail" alimenta o primeiro
tipo, "E-mails de gestor" o segundo. O campo do dono nasceu de um defeito medido
em produção horas depois da 274: o dono da Prime tem o cadastro SEM E-MAIL — e não
existe tela onde ele possa pôr um —, então os dois endereços que ele cadastrou
entravam pelo único portão que sobrava, o de gestor, que é a versão SEM os nomes.
Dos seis destinatários da primeira segunda, nenhum receberia o que ele pediu. Ver
`docs/mockups/resumo_semanal_quem_ve_os_nomes.html` e a 276.

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

from finance import cockpit_dono as _cd
from finance import visita as _vis

_log = logging.getLogger(__name__)

#: -3h fixo, como o resto do painel. O país não tem horário de verão desde 2019.
_BR = timezone(timedelta(hours=-3))

#: Quantos e-mails uma conta pode cadastrar EM CADA campo. Não é limite técnico: é
#: o ponto em que "quem acompanha a operação" vira lista de distribuição, e aí o
#: resumo passa a ser boletim, que é outro produto.
EMAILS_MAX = 5

#: QUANDO cada opção manda: (dia da semana em Python, hora de Brasília).
#: 'segunda' é o padrão e a escolha do dono: a semana que começa — as festas dos
#: próximos dias viram plano, não retrospectiva.
#:
#: A HORA ENTROU EM 19/09/2026, e o motivo é que a tela mentia. Ela oferece
#: "Sexta, 17h" desde a 274, mas quem decidia era só o cron: um disparo às 12:00
#: UTC (09:00 BRT), e `contas_do_dia` olhava apenas o DIA. Conta que escolhesse
#: sexta receberia às 9h — oito horas antes do que a tela prometeu. Ninguém tinha
#: escolhido sexta ainda, então o defeito nunca apareceu; o dono mandou acertar
#: junto com o nome do serviço no render.yaml.
QUANDO = {"segunda": (0, 9), "sexta": (4, 17)}

#: As chaves, pra validação e pro `in`. Continua sendo o que a migração 274 aceita
#: no check de `contas.resumo_semanal_dia`.
DIAS = tuple(QUANDO)

#: Quanto o disparo pode atrasar e ainda contar como "a hora certa". O Render não
#: promete o minuto, e um cron que saia às 11:58 UTC cairia em 8h de Brasília — com
#: igualdade exata, ninguém receberia nada e o log diria "nenhuma conta hoje".
#: Uma hora de folga não confunde as duas janelas: 9h e 17h são oito horas
#: distantes.
FOLGA_HORAS = 1

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")

_PADRAO = {"resumo_semanal": False, "resumo_semanal_emails": "",
           "resumo_semanal_dono_emails": "",
           "resumo_semanal_vendedor": True, "resumo_semanal_dia": "segunda"}

#: Quanto cada tipo de e-mail MOSTRA, do maior pro menor. É a régua do desempate em
#: `destinatarios`: o mesmo endereço em dois lugares recebe UM e-mail, o de maior
#: alcance. Dois quase iguais no mesmo minuto é o que faz a pessoa desligar tudo.
ALCANCE = {"dono": 0, "gestor": 1, "vendedor": 2}


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
    """Os e-mails de um campo de texto livre (o do dono ou o de gestor).

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
                          resumo_semanal_vendedor, resumo_semanal_dia,
                          resumo_semanal_dono_emails
                     from contas where id=%s""", (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — base sem a 274/276 ainda
        _log.warning("config do resumo semanal da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return dict(_PADRAO, emails=[], emails_dono=[])
    if not r:
        return dict(_PADRAO, emails=[], emails_dono=[])
    return {"resumo_semanal": bool(r[0]), "resumo_semanal_emails": r[1] or "",
            "resumo_semanal_vendedor": bool(r[2]),
            "resumo_semanal_dia": r[3] if r[3] in DIAS else "segunda",
            "resumo_semanal_dono_emails": r[4] or "",
            "emails": parse_emails(r[1]), "emails_dono": parse_emails(r[4])}


def salvar_config(pool, conta_id: int, *, ativo: bool, emails: str,
                  vendedor: bool, dia: str, dono_emails: str = "") -> dict:
    """Grava o que a engrenagem mandou. Devolve a config já normalizada, pra tela
    mostrar o que de fato ficou — e não o que a pessoa digitou.

    `dono_emails` tem padrão vazio de propósito: a chamada que não passar o campo
    novo LIMPA a lista do dono, e é o que se quer — a tela manda o formulário
    inteiro, e um campo apagado lá tem que apagar aqui.
    """
    limpos = parse_emails(emails)
    do_dono = parse_emails(dono_emails)
    dia = dia if dia in DIAS else "segunda"
    with pool.connection() as c:
        c.execute("""update contas set resumo_semanal=%s, resumo_semanal_emails=%s,
                            resumo_semanal_vendedor=%s, resumo_semanal_dia=%s,
                            resumo_semanal_dono_emails=%s
                      where id=%s""",
                  (bool(ativo), ", ".join(limpos), bool(vendedor), dia,
                   ", ".join(do_dono), conta_id))
        c.commit()
    return {"resumo_semanal": bool(ativo), "resumo_semanal_emails": ", ".join(limpos),
            "resumo_semanal_vendedor": bool(vendedor), "resumo_semanal_dia": dia,
            "resumo_semanal_dono_emails": ", ".join(do_dono),
            "emails": limpos, "emails_dono": do_dono}


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
    festa = _vis.vende_festa(pool, conta_id)     # a régua da visita segue o nicho
    with pool.connection() as c:
        def _um(sql, args, chave):
            try:
                with c.transaction():
                    r = c.execute(sql, args).fetchone()
                out[chave] = int(r[0] or 0) if r else 0
            except Exception as e:  # noqa: BLE001
                _log.warning("resumo semanal, bloco %s da conta %s: %s: %s",
                             chave, conta_id, type(e).__name__, e)

        # a visita pela régua de `finance.visita` — a mesma do Raio-X e do
        # Relatório: com ou sem card, festa não é visita
        _um("""select count(*) from eventos_agenda e
                where e.conta_id=%s and """ + _vis.sql_conta("e", festa=festa) + """
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
                           -- AS VISITAS DELE pela régua do Raio-X (24/09/2026): do
                           -- card dele, ou marcadas por ele sem card
                           (select count(*) from eventos_agenda e
                             where e.conta_id = m.conta_id and """ + _vis.sql_conta("e", festa=festa) + """
                               and """ + _vis.sql_vendedor("e") + """ = m.id
                               and e.desfecho='realizado'
                               and e.inicio >= %s and e.inicio < %s),
                           -- OS CONTRATOS DELE pela régua do Raio-X e do cockpit
                           -- (24/09/2026): contrato vivo, com ou sem lead, de quem fez
                           -- o orçamento. Com o join antigo por lead, a capa dizia "3
                           -- contratos" e a Jacqueline, dona de 2 deles, aparecia aqui
                           -- com 0 — os dois foram feitos direto pelo orçamento.
                           (select count(*) from contratos c
                              left join orcamentos o on o.id = c.orcamento_id
                             where c.conta_id = m.conta_id and """ + _cd.SQL_CT_VIVO + """
                               and """ + _cd.SQL_CT_VENDEDOR + """ = m.id
                               and c.assinado_em >= %s and c.assinado_em < %s),
                           (select coalesce(sum(c.valor_centavos),0) from contratos c
                              left join orcamentos o on o.id = c.orcamento_id
                             where c.conta_id = m.conta_id and """ + _cd.SQL_CT_VIVO + """
                               and """ + _cd.SQL_CT_VENDEDOR + """ = m.id
                               and c.assinado_em >= %s and c.assinado_em < %s),
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

    QUATRO FONTES, e nenhuma manda sozinha:

    * o CADASTRO (`membros`): papel dono ou gestor → tipo `dono`; vendedor → tipo
      `vendedor`, e só se a conta tiver ligado essa chave;
    * o campo "Seu e-mail" (`emails_dono`) → tipo `dono`;
    * o campo "E-mails de gestor" (`emails`) → tipo `gestor`.

    O MESMO ENDEREÇO EM DUAS FONTES RECEBE UM E-MAIL SÓ, o de MAIOR alcance (ver
    `ALCANCE`) — e não o da última fonte lida. A ordem em que se varre não pode
    mudar o resultado: quem é vendedor no cadastro e está no campo do dono é o dono
    da empresa que também vende, e o que ele pediu é ver a equipe inteira.
    """
    cfg = cfg or config(pool, conta_id)
    if not cfg.get("resumo_semanal"):
        return []
    fora: dict[str, dict] = {}

    def _por(email: str, nome: str, tipo: str, membro_id) -> None:
        e = (email or "").strip().lower()
        if not e:
            return
        atual = fora.get(e)
        if atual and ALCANCE[atual["tipo"]] <= ALCANCE[tipo]:
            # já entrou com alcance igual ou maior; o nome e o membro do cadastro
            # valem mais que os do campo de texto, que não tem nem um nem outro
            if nome and not atual["nome"]:
                atual["nome"] = nome
            if membro_id and not atual["membro_id"]:
                atual["membro_id"] = membro_id
            return
        fora[e] = {"email": e, "nome": nome or (atual or {}).get("nome") or "",
                   "tipo": tipo,
                   "membro_id": membro_id or (atual or {}).get("membro_id")}

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
        _por(email, nome, "dono" if papel in ("dono", "gestor") else "vendedor", mid)
    for e in cfg.get("emails_dono") or []:
        _por(e, "", "dono", None)
    for e in cfg.get("emails") or []:
        _por(e, "", "gestor", None)
    return sorted(fora.values(), key=lambda x: (ALCANCE[x["tipo"]], x["email"]))


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


def escolha_de_agora(agora: datetime | None = None) -> str:
    """Qual das opções de `QUANDO` bate com este instante — "" se nenhuma.

    DIA **E** HORA, e é a hora que é nova. O cron dispara mais de uma vez por dia
    (ver o docstring de `scripts/resumo_semanal.py`) e pergunta ao banco de quem é
    a vez; sem olhar a hora, o disparo das 17h mandaria de novo o que o das 9h já
    mandou — ou melhor: não mandaria, porque `ja_enviado` barra a repetição, e a
    conta de sexta continuaria recebendo às 9h, que é o defeito original.
    """
    agora = (agora or datetime.now(timezone.utc)).astimezone(_BR)
    for chave, (dia, hora) in QUANDO.items():
        if agora.weekday() == dia and abs(agora.hour - hora) <= FOLGA_HORAS:
            return chave
    return ""


def contas_do_dia(pool, agora: datetime | None = None) -> list[int]:
    """As contas que devem receber AGORA. O dia e a hora de cada uma estão na
    configuração dela — o cron dispara e pergunta, em vez de existir um serviço
    no Render por horário."""
    hoje = escolha_de_agora(agora)
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
