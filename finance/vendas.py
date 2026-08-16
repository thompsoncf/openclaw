"""Módulo Vendas de Serviços — a ponte pipeline → financeiro.

Motor GENÉRICO de venda consultiva de serviço (o nicho 'tecnologia'/Aladdin é o
primeiro caso; o mesmo fluxo serve advocacia, agência, etc., trocando só o
catálogo). Aqui mora a ação que fecha o negócio:

    fechar_orcamento() — o orçamento vira contrato e cai no financeiro que já
    existe (módulo Empresa), como TÍTULOS A RECEBER:
      • setup  (valor único)      -> título a receber não-recorrente
      • mensal (recorrente)       -> título a receber RECORRENTE, que na baixa
                                     se auto-renova (dar_baixa_titulo do Empresa).

    No modo EVENTO (nicho eventos, migração 147) não existe mensalidade: cada
    parcela do plano de pagamento vira um título no vencimento combinado —
    sinal no Pix hoje, 12x no cartão a partir do mês que vem.

    Assim a receita entra pelo caminho de sempre (livro-caixa, fonte única) e o
    relatório de vendas continua unificado — sem PDV novo pra serviço.

Atômico: o status vira num único UPDATE ... WHERE status<>'fechado' RETURNING e
os dois títulos entram na MESMA transação. Fecha a corrida do duplo-clique (não
gera títulos em dobro) e, se algo falhar no meio, faz rollback total.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from .empresa import _mes_seguinte

_log = logging.getLogger("openclaw.vendas")

CAT_SERVICOS = "Serviços"


# Nichos que emitem orçamento de EVENTO (migração 147). O modo é do NICHO da
# conta, não de quem está criando — e orçamento nasce em quatro portas: painel,
# cockpit do vendedor, "gerar orçamento" da prospecção e o agente. Todas passam
# por aqui; senão a empresa de eventos manda pro cliente uma folha de
# mensalidade dependendo de onde o vendedor clicou.
NICHOS_EVENTO = {"eventos"}


def modo_por_nicho(slug: str | None) -> str:
    return "evento" if (slug or "").strip() in NICHOS_EVENTO else "recorrente"


def modo_do_orcamento(pool, conta_id: int) -> str:
    """O modo do orçamento dessa conta: 'evento' ou 'recorrente'.

    Sem try/except de propósito: se a leitura do nicho falhar, é melhor o
    salvamento falhar do que gravar calado um orçamento no modo errado — o
    cliente receberia uma folha de mensalidade por uma festa. Conta que não
    existe volta sem nicho e cai em 'recorrente', que é o padrão da coluna.
    """
    from finance import empresa as emp
    return modo_por_nicho((emp.obter_dados_empresa(pool, conta_id) or {}).get("nicho"))


def vende_data(pool, conta_id: int) -> bool:
    """Esta conta VENDE DATA? (nicho de eventos)

    É o que decide se a tela mostra o vocabulário de data segurada — barra de
    fixado/segurado no calendário, "Só segurar a data" no formulário, a legenda que
    fala de sinal. Clínica, loja e escritório não seguram data esperando sinal:
    pra eles isso é ruído numa tela que já estava certa.

    TOLERANTE, ao contrário de modo_do_orcamento: lá, falhar a leitura do nicho tem
    que derrubar o salvamento (gravar no modo errado manda uma folha de mensalidade
    por uma festa). Aqui o pior caso é a Agenda deixar de mostrar um enfeite — e ela
    precisa abrir de qualquer jeito.
    """
    try:
        return modo_do_orcamento(pool, conta_id) == "evento"
    except Exception:  # noqa: BLE001
        return False


# SQL do título a receber (reusa a tabela titulos do módulo Empresa).
# orcamento_id/parcela_idx (migração 162) só vêm preenchidos no modo evento: são
# eles que deixam voltar do título pra parcela sem casar por texto de descrição.
_SQL_TITULO = """insert into titulos
    (conta_id, tipo, descricao, contraparte, valor_centavos, vencimento,
     categoria, recorrente, criado_por, orcamento_id, parcela_idx)
  values (%s, 'receber', %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id"""


# ---------------------------------------------------------------- o SINAL
# Ponto único da regra "qual parcela é o sinal". O gerador de parcelas do
# orçamento de evento escreve, na PRIMEIRA linha, "Sinal — confirma a reserva da
# data"; é dali que sai o valor e é essa parcela que recebe baixa quando o dono
# confirma o recebimento. web/proposta.sinal_do_orcamento delega pra cá — duas
# leituras diferentes de "o que é o sinal" seria o começo de dois números.

def indice_do_sinal(parcelas) -> int | None:
    """A posição da parcela do sinal em `orcamentos.parcelas`, ou None.

    Só a primeira conta: parcela de sinal no meio do plano não é sinal, é parcela
    com observação parecida. Orçamento montado na mão, sem essa linha, não tem
    sinal — e a data é reservada direto (ver web/proposta._reservar_na_agenda).

    SEMPRE sobre a lista normalizada por `_parcelas` — nunca sobre o jsonb cru,
    mesmo quando ele já vem como list. É `_parcelas` que fechar_orcamento percorre
    pra numerar `parcela_idx`, e ela DESCARTA linha de valor zero. Contando no cru,
    um plano com uma primeira linha vazia daria índices diferentes nos dois lados —
    e o índice aqui é o que escolhe qual título recebe baixa. Errar isso é dar por
    recebida uma parcela que ninguém pagou."""
    itens = _parcelas(parcelas)
    if not itens:
        return None
    return 0 if "sinal" in str(itens[0].get("obs") or "").lower() else None


def valor_do_sinal(parcelas) -> int:
    """Quanto é o sinal, em centavos, ou 0 se o orçamento não pede sinal."""
    i = indice_do_sinal(parcelas)
    return int(_parcelas(parcelas)[i]["valor_centavos"]) if i is not None else 0


def _itens_curtos(bruto, limite: int = 8) -> list[dict]:
    """Só o que a ficha da agenda mostra de cada item: nome e quantidade."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            return []
    out = []
    for it in (bruto or [])[:limite]:
        if not isinstance(it, dict):
            continue
        nome = str(it.get("nome") or "").strip()
        if nome:
            try:
                qtd = int(it.get("qtd") or 1)
            except (TypeError, ValueError):
                qtd = 1
            out.append({"nome": nome, "qtd": max(1, qtd)})
    return out


def fichas_de_eventos(pool, conta_id: int, evento_ids) -> dict[int, dict]:
    """{id_do_compromisso: ficha} — o orçamento e o PAGAMENTO de cada festa.

    Mora aqui, e não em finance/agenda, porque é a mesma ponte que este módulo já
    é: orçamento -> título a receber. A agenda só pede a leitura.

    O QUE ISSO RESOLVE. A agenda guardava o evento e uma FRASE colada na aprovação
    ("Orçamento Nº 1 · 100 convidados · aprovado pelo cliente"). Não é dado: não dá
    pra somar nem filtrar, e envelhece — segue dizendo "aprovado" num orçamento que
    já foi fechado, com o sinal vencendo. Tudo que a ficha mostra já estava gravado;
    faltava alguém ler.

    O PAGAMENTO tem dois estados, e a diferença importa:
      • contrato FECHADO -> existem títulos a receber, e eles são a verdade do
        dinheiro (é neles que a baixa acontece, é deles que sai o livro-caixa);
      • orçamento só APROVADO -> não há título nenhum ainda. Aí a ficha mostra o
        PLANO que o cliente aceitou, dizendo que é plano — inventar "0% recebido"
        de algo que nem virou cobrança seria mentira com cara de número.

    Duas consultas no total, independentemente de quantos eventos — a tela do mês
    pede a lista inteira de uma vez.
    """
    ids = [int(i) for i in (evento_ids or [])]
    if not ids:
        return {}
    with pool.connection() as c:
        try:
            orcs = c.execute(
                """select o.evento_agenda_id, o.id, o.numero, coalesce(o.status,''),
                          coalesce(o.empresa, o.cliente, ''), coalesce(o.whatsapp, o.telefone, ''),
                          o.evento, o.itens, o.parcelas,
                          coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)
                     from orcamentos o
                    where o.conta_id=%s and o.evento_agenda_id = any(%s)""",
                (conta_id, ids)).fetchall()
        except Exception as e:  # noqa: BLE001 — conta sem o módulo de orçamentos
            _log.info("fichas_de_eventos: sem orçamentos legíveis: %s", type(e).__name__)
            return {}
        por_orc = {r[1]: r for r in orcs}
        titulos = []
        if por_orc:
            titulos = c.execute(
                """select orcamento_id, status, valor_centavos, vencimento, pago_em
                     from titulos
                    where conta_id=%s and orcamento_id = any(%s) and status <> 'cancelado'
                    order by vencimento, id""",
                (conta_id, list(por_orc))).fetchall()

    pagto: dict[int, dict] = {}
    hoje = date.today()
    for oid, status, valor, venc, _pago_em in titulos:
        p = pagto.setdefault(oid, {"total": 0, "pago": 0, "aberto": 0,
                                   "vencidas": 0, "vencidas_centavos": 0, "proxima": None})
        v = int(valor or 0)
        p["total"] += v
        if status == "pago":
            p["pago"] += v
        else:
            p["aberto"] += v
            if venc and venc < hoje:
                p["vencidas"] += 1
                p["vencidas_centavos"] += v
            # a PRÓXIMA a vencer: a primeira em aberto na ordem de vencimento (a
            # consulta já vem ordenada), inclusive se já está vencida — é ela que o
            # dono precisa resolver primeiro.
            if p["proxima"] is None:
                p["proxima"] = {"vencimento": venc, "valor_centavos": v,
                                "vencida": bool(venc and venc < hoje)}

    fichas: dict[int, dict] = {}
    for r in orcs:
        (ev_id, oid, numero, status, cliente, contato, evento, itens, parcelas, total) = r
        evento = evento if isinstance(evento, dict) else {}
        pg = pagto.get(oid)
        plano = sum(int(p.get("valor_centavos") or 0)
                    for p in (_parcelas(parcelas) or []))
        ficha = {
            "orcamento_id": oid, "numero": numero, "status": status,
            "cliente": cliente.strip(), "contato": contato.strip(),
            "tipo": str(evento.get("tipo") or "").strip(),
            "convidados": evento.get("convidados"),
            "local": str(evento.get("local") or "").strip(),
            "itens": _itens_curtos(itens),
            "total_centavos": int(total or 0),
            "tem_titulos": bool(pg),
        }
        if pg:
            ficha.update({
                "titulos_centavos": pg["total"], "pago_centavos": pg["pago"],
                "aberto_centavos": pg["aberto"],
                "pct": int(round(100 * pg["pago"] / pg["total"])) if pg["total"] else 0,
                "vencidas": pg["vencidas"], "vencidas_centavos": pg["vencidas_centavos"],
                "proxima": pg["proxima"],
            })
        else:
            # sem título: o que existe é o PLANO aceito, não cobrança. Sem `pct`
            # justamente pra tela não mostrar "0% recebido" de algo que ninguém
            # cobrou ainda.
            ficha.update({"plano_centavos": plano, "pct": None, "vencidas": 0,
                          "proxima": None})
        fichas[ev_id] = ficha
    return fichas


def reabrir_proposta(pool, conta_id: int, orcamento_id: int, evento_agenda_id: int,
                     sinal_pago_em, evento_novo: dict | None = None) -> dict:
    """Editar uma proposta JÁ APROVADA desfaz a assinatura — e a data na agenda tem
    que acompanhar. Este é o caminho de volta que não existia.

    Até aqui, editar uma proposta aprovada revertia o status pra 'enviado' e limpava
    a assinatura, mas NÃO tocava em `evento_agenda_id` nem no sinal: a data seguia
    ocupada por um orçamento que voltou a ser rascunho, e uma re-aprovação nem
    remarcava (o `_reservar_na_agenda` sai fora quando já existe compromisso), nem
    movia a data se o cliente tivesse escolhido outro dia.

    A REGRA é o dinheiro:

      • SINAL PAGO -> a data é do cliente e não se mexe nela. Se a edição mudou
        data/hora, o compromisso é REMARCADO pra janela nova — perder a data de quem
        pagou seria o pior erro possível aqui.
      • SINAL NÃO PAGO -> nada sustenta a reserva. A data é liberada e o vínculo se
        desfaz, pra a próxima aprovação criar do zero (com a data nova, se mudou).

    Devolve {liberou, remarcou} pra quem chamou contar na tela.
    """
    from finance import agenda as ag
    liberou = remarcou = False
    if sinal_pago_em:
        ini, fim = ag.janela_evento((evento_novo or {}).get("data"),
                                    (evento_novo or {}).get("inicio"),
                                    (evento_novo or {}).get("fim"))
        atual = ag.evento_por_id(pool, conta_id, evento_agenda_id)
        if ini and atual and (atual["inicio"] != ini or atual.get("fim") != fim):
            remarcou = ag.remarcar_evento(pool, conta_id, evento_agenda_id, ini, fim)
        return {"liberou": False, "remarcou": remarcou}

    liberou = ag.cancelar_evento(pool, conta_id, evento_agenda_id)
    with pool.connection() as c:
        # o vínculo E o valor congelado do sinal saem juntos: a próxima aprovação
        # relê as parcelas do orçamento editado e grava de novo. Deixar o
        # `sinal_centavos` velho faria a ficha da agenda mostrar o preço de antes.
        c.execute("update orcamentos set evento_agenda_id=null, sinal_centavos=null "
                  " where id=%s and conta_id=%s", (int(orcamento_id), conta_id))
        c.commit()
    return {"liberou": liberou, "remarcou": False}


def confirmar_sinal(pool, conta_id: int, orcamento_id: int) -> dict:
    """O sinal caiu. Ponto único da regra, pros dois botões que a apertam: o do
    funil (Serviços) e o da caixa do dia (Agenda).

    Confirmação MANUAL de propósito. O dono recebe o Pix como já recebe hoje e
    aperta — o sistema não cobra nada nem fica esperando integração. Se um dia a
    cobrança automática existir, ela só passa a chamar esta função.

    Mexe em DUAS coisas, nesta ordem: a DATA (a pré-reserva vira compromisso firme)
    e o DINHEIRO (o título daquela parcela recebe baixa, na data em que o sinal
    caiu). O pagamento é gravado PRIMEIRO; as duas consequências vêm depois, cada
    uma podendo falhar sozinha sem desfazer o registro — apertar de novo retoma o
    que faltou.

    Devolve {ok, ja_estava, reserva_firmada, titulo_baixado} ou {ok: False, erro}.
    """
    from finance import agenda as ag
    with pool.connection() as c:
        r = c.execute("""select sinal_pago_em, evento_agenda_id, parcelas
                           from orcamentos where id=%s and conta_id=%s""",
                      (int(orcamento_id), conta_id)).fetchone()
        if not r:
            return {"ok": False, "erro": "orçamento não encontrado"}
        ja_pago, agenda_id, parcelas = r
        era_novo = not ja_pago
        if era_novo:
            ja_pago = c.execute(
                "update orcamentos set sinal_pago_em=now() where id=%s and conta_id=%s "
                "returning sinal_pago_em", (int(orcamento_id), conta_id)).fetchone()[0]
            c.commit()
    # a data vira firme. Fora da transação: confirmar o pagamento é o que não pode
    # se perder — se a agenda falhar, o registro fica e o botão retoma depois.
    firmou = False
    if agenda_id:
        try:
            firmou = ag.confirmar_pre_reserva(pool, conta_id, int(agenda_id))
        except Exception as e:  # noqa: BLE001
            _log.warning("confirmar_sinal %s: não firmou o compromisso %s: %s: %s",
                         orcamento_id, agenda_id, type(e).__name__, e)
    # e o título daquela parcela recebe baixa NA DATA DO SINAL. Só acha alguma coisa
    # se o contrato já tiver sido fechado (é o fechamento que cria os títulos); no
    # caminho normal — confirma o sinal, fecha o contrato depois — quem dá a baixa é
    # o próprio fechar_orcamento, com a mesma data.
    baixado = None
    try:
        baixado = baixar_titulo_do_sinal(pool, conta_id, int(orcamento_id),
                                         parcelas, ja_pago)
    except Exception as e:  # noqa: BLE001
        _log.warning("confirmar_sinal %s: baixa do título falhou: %s: %s",
                     orcamento_id, type(e).__name__, e)
    return {"ok": True, "ja_estava": not era_novo, "reserva_firmada": firmou,
            "titulo_baixado": baixado}


def baixar_titulo_do_sinal(pool, conta_id: int, orcamento_id: int, parcelas,
                           pago_em) -> int | None:
    """Dá baixa no título da parcela do sinal, NA DATA EM QUE O SINAL CAIU.

    O dinheiro entrou quando o Pix caiu, não quando o dono apertou o botão nem
    quando fechou o contrato — e a data do lançamento decide o MÊS da receita.
    Por isso `pago_em` vem de `orcamentos.sinal_pago_em` e não de `date.today()`.

    Idempotente por baixo (dar_baixa_titulo só age em título 'aberto'), então
    pode ser chamado pelos dois caminhos sem coordenação:
      • "Sinal recebido" com o contrato JÁ fechado -> o título existe, recebe baixa;
      • "Fechar contrato" com o sinal JÁ confirmado -> o título nasce e recebe
        baixa logo em seguida.
    Devolve o id do título baixado, ou None se não havia o que baixar.
    """
    i = indice_do_sinal(parcelas)
    if i is None or not pago_em:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select id from titulos
                where conta_id=%s and orcamento_id=%s and parcela_idx=%s
                  and status='aberto'
                order by id limit 1""",
            (conta_id, orcamento_id, i)).fetchone()
    if not r:
        return None
    from finance import empresa as emp
    quando = pago_em.date() if isinstance(pago_em, datetime) else pago_em
    res = emp.dar_baixa_titulo(pool, conta_id, r[0], data_pagto=quando)
    return r[0] if res.get("ok") else None


def _venc(v, padrao: date) -> date:
    """Vencimento da parcela: aceita date, ISO ('2025-11-13') e 'dd/mm/aaaa'.
    O que não der pra ler cai no padrão — parcela sem data válida vira título
    com vencimento, nunca título perdido."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return padrao


def _parcelas(bruto) -> list[dict]:
    """As parcelas do jsonb, já filtradas: só o que tem valor > 0 vira título."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            bruto = []
    itens = []
    for p in (bruto or []):
        if not isinstance(p, dict):
            continue
        valor = int(p.get("valor_centavos") or 0)
        if valor > 0:
            itens.append({"valor_centavos": valor, "venc": p.get("venc"),
                          "forma": (p.get("forma") or "").strip(),
                          "obs": (p.get("obs") or "").strip()})
    return itens


def fechar_orcamento(pool, conta_id: int, orcamento_id: int,
                     criado_por: int | None = None,
                     dias_setup: int = 7) -> dict:
    """Fecha o orçamento e gera os títulos a receber.

    • modo 'recorrente' (o de sempre): setup (único) + mensalidade (recorrente).
    • modo 'evento': UM título por parcela do plano de pagamento, no vencimento
      combinado — evento não tem mensalidade, tem sinal + parcelas. Orçamento de
      evento sem plano de pagamento cai no título único do valor total.

    conta_id é a conta que EMITE (a empresa de serviço, ex.: Aladdin) — os títulos
    são dela. Idempotente: orçamento já fechado não gera de novo.
    """
    hoje = date.today()
    with pool.connection() as c:
        # trava atômica + escopo por conta: só o dono fecha, e só o primeiro a
        # fechar segue; os demais (duplo-clique) voltam vazios.
        orc = c.execute(
            """update orcamentos set status='fechado', atualizado_em=now()
                where id=%s and conta_id=%s and status <> 'fechado'
             returning empresa, cliente, setup_centavos, mensal_centavos,
                       coalesce(modo,'recorrente'), parcelas, primeiro_ano_centavos,
                       sinal_pago_em""",
            (orcamento_id, conta_id),
        ).fetchone()
        if not orc:
            estado = c.execute(
                "select status from orcamentos where id=%s and conta_id=%s",
                (orcamento_id, conta_id),
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Orçamento não encontrado."}
            return {"ok": False, "erro": f"Orçamento já está '{estado[0]}'."}

        (empresa, cliente, setup_cent, mensal_cent, modo, parcelas_raw, total_cent,
         sinal_pago_em) = orc
        contraparte = (empresa or cliente or "").strip()
        setup_cent = int(setup_cent or 0)
        mensal_cent = int(mensal_cent or 0)

        if modo == "evento":
            parcelas = _parcelas(parcelas_raw)
            base = f"Evento — {contraparte}".strip(" —")
            ids = []
            for i, p in enumerate(parcelas, 1):
                # a observação da parcela ("Sinal", "12x no cartão") é o que a
                # empresa escreveu pro cliente — vale mais na conciliação do que
                # um "parcela 2/13" genérico, então ela manda quando existe.
                rotulo = p["obs"] or f"parcela {i}/{len(parcelas)}"
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, f"{base} · {rotulo}"[:200], contraparte,
                     p["valor_centavos"], _venc(p["venc"], hoje + timedelta(days=dias_setup)),
                     CAT_SERVICOS, False, criado_por, orcamento_id, i - 1),
                ).fetchone()[0])
            # sem plano de pagamento: um título só, com o total do evento — o
            # COM desconto (primeiro_ano_centavos), não a soma bruta dos itens.
            total_evento = int(total_cent or 0) or setup_cent
            if not ids and total_evento > 0:
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, base, contraparte, total_evento,
                     hoje + timedelta(days=dias_setup), CAT_SERVICOS, False, criado_por,
                     orcamento_id, None),
                ).fetchone()[0])
            c.commit()
            evento_ids = ids

        else:
            evento_ids = None
            setup_id = mensal_id = None
            if setup_cent > 0:
                setup_id = c.execute(
                    _SQL_TITULO,
                    (conta_id, f"Setup — {contraparte}".strip(" —"), contraparte,
                     setup_cent, hoje + timedelta(days=dias_setup), CAT_SERVICOS,
                     False, criado_por, None, None),
                ).fetchone()[0]
            if mensal_cent > 0:
                mensal_id = c.execute(
                    _SQL_TITULO,
                    (conta_id, f"Mensalidade — {contraparte}".strip(" —"), contraparte,
                     mensal_cent, _mes_seguinte(hoje), CAT_SERVICOS, True, criado_por,
                     None, None),
                ).fetchone()[0]
            c.commit()

    if evento_ids is None:
        return {"ok": True, "modo": "recorrente", "setup_titulo_id": setup_id,
                "mensal_titulo_id": mensal_id}

    # O SINAL JÁ RECEBIDO não pode nascer como título em aberto: o dinheiro está no
    # bolso desde que o Pix caiu, e o lançamento sai na DATA DO SINAL — é ela que
    # decide o mês da receita, não o dia em que se apertou "Fechar contrato".
    #
    # Fora da transação de propósito, e depois do `with`: o contrato fechado é o
    # que não pode se perder, e dar_baixa_titulo abre a própria conexão. Se a baixa
    # falhar, o título fica aberto e o botão "Sinal recebido" (ou a baixa na mão)
    # resolve depois — nada fica duplicado, porque a baixa só age em 'aberto'.
    sinal_baixado = None
    if sinal_pago_em:
        try:
            sinal_baixado = baixar_titulo_do_sinal(pool, conta_id, orcamento_id,
                                                   parcelas_raw, sinal_pago_em)
        except Exception as e:  # noqa: BLE001
            _log.warning("fechar_orcamento %s: título do sinal não recebeu baixa: %s: %s",
                         orcamento_id, type(e).__name__, e)
    return {"ok": True, "modo": "evento", "titulos": evento_ids,
            "setup_titulo_id": None, "mensal_titulo_id": None,
            "sinal_titulo_id": sinal_baixado}
