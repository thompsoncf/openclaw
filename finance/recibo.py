"""O recibo de dinheiro que ENTROU — pedido 1 do dono (23/09/2026).

"Recibo do valor do sinal de pagamentos e da parcela", e depois, no mockup:
"quando der baixa abre um botão pra nascer o recibo". O texto foi aprovado pelo
dono palavra por palavra em 23/09/2026, em cima do sinal real da Maria Carolina
(orçamento nº 23):

    Recebemos de MARIA CAROLINA DA SILVA COSTA, CPF …, a importância de
    R$ 2.415,00 (dois mil, quatrocentos e quinze reais), referente ao Sinal —
    confirma a reserva da data do orçamento nº 23: Casamento em 24/07/2027,
    locação de espaço. Forma de pagamento: Pix.
    Damos plena quitação desta parcela. Permanecem em aberto R$ 5.710,00, em 5
    parcelas, conforme o contrato.

AS REGRAS, E POR QUÊ:

* SÓ CONTA A RECEBER, E SÓ PAGA. Recibo é de quem recebe: na conta a pagar quem
  emite é o fornecedor. E recibo de conta aberta seria dizer que um dinheiro
  entrou antes de ele entrar.

* UM POR CONTA. Gerar de novo mantém o número e o link e reescreve o texto — é
  assim que o CPF completado no cadastro entra no papel. Dois números pro mesmo
  dinheiro seriam dois recibos do mesmo pagamento.

* O TEXTO CONGELA. `dados` guarda o recibo como foi entregue; editar o orçamento
  depois não muda o papel que está na mão do cliente.

* O CPF ENTRA QUANDO EXISTE (decisão do mockup, não contestada): sem documento, a
  linha some — recibo não fica bloqueado por falta de cadastro.

* O "REFERENTE A" SEGUE O QUE A CONTA VENDE: parcela de orçamento de evento fala
  do evento e da data; mensalidade fala do mês; conta sem orçamento usa a própria
  descrição. É o `modo` do orçamento que decide — o mesmo fato que separa evento
  de recorrente no resto do sistema.

* "MANDAR PRO CLIENTE" USA O CANAL DA CONTA. `whatsapp_out` escolhe entre os três
  caminhos — WhatsApp QR, Cloud API e Twilio, que seguem três funções distintas —
  e o recibo só entrega o texto ao que estiver de pé. Nada aqui abre, fecha ou
  religa conexão. No Twilio e na Cloud API, fora da janela de 24h a mensagem livre
  não sai; o erro volta pra tela e o "copiar link" continua valendo.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date

_log = logging.getLogger("recibo")

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro")

#: Palavras que pedem "à" no "referente a". O resto que for masculino conhecido
#: pede "ao"; o que não se sabe fica em "a", que nunca erra a concordância.
_FEMININAS = {"parcela", "entrada", "mensalidade", "taxa", "diária", "diaria",
              "locação", "locacao", "anuidade", "reserva", "primeira", "segunda",
              "última", "ultima", "quinzena"}
_MASCULINAS = {"sinal", "setup", "contrato", "aluguel", "serviço", "servico",
               "pagamento", "saldo", "restante", "evento", "pacote", "plano"}


def _artigo(rotulo: str) -> str:
    p = (rotulo or "").strip().split(" ", 1)[0].lower().strip(".,:;—-")
    if p in _FEMININAS:
        return "à"
    if p in _MASCULINAS:
        return "ao"
    return "a"


def _doc(v: str) -> str:
    from web.contrato_publico import _doc as mascara
    return mascara(v or "")


def _cidade(v: str) -> str:
    """"TERESINA" → "Teresina". O cadastro guarda em caixa alta, e "TERESINA, 23
    de setembro de 2026" no fecho leria como grito."""
    v = (v or "").strip()
    if not v:
        return ""
    return " ".join(w if w.lower() in ("de", "da", "do", "das", "dos") else w.capitalize()
                    for w in v.lower().split())


#: Os códigos de `lancamentos.forma_pagamento`, como o papel escreve. A coluna
#: `pagamento` ao lado NÃO entra: ela guarda o que a foto do comprovante disse
#: ("Visa **** 8484", "Pix para Facebook…"), e isso não é texto de recibo.
_FORMAS = {"pix": "Pix", "boleto": "Boleto", "credito": "Cartão de crédito",
           "debito": "Cartão de débito", "transferencia": "Transferência",
           "dinheiro": "Dinheiro", "especie": "Dinheiro", "cheque": "Cheque"}


def _forma(v: str) -> str:
    v = (v or "").strip()
    return _FORMAS.get(v.lower(), v)


def _data_extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def _rotulo_da_parcela(descricao: str, obs: str) -> str:
    """O nome da parcela como o dono vê na lista: o que vem depois do " · " na
    descrição do título ("Evento — Fulana · Parcela 2/5" → "Parcela 2/5"). Sem o
    " · ", a descrição inteira — o dono pode ter renomeado a conta, e o que ele
    escreveu vence o plano do orçamento. O `obs` do plano só entra se a
    descrição estiver vazia."""
    d = (descricao or "").strip()
    if " · " in d:
        return d.rsplit(" · ", 1)[1].strip()
    return d or (obs or "").strip()


def _mensalidade_com_mes(descricao: str, venc: date | None) -> str:
    """"Mensalidade — H Pernas" → "Mensalidade de outubro/2026 — H Pernas". Só
    quando a descrição não diz o mês: quem escreveu "Mensalidade de agosto" sabe
    de que mês é, e o vencimento pode ser do mês seguinte."""
    d = (descricao or "").strip()
    if not venc or not d.lower().startswith("mensalidade"):
        return d
    if any(m in d.lower() for m in MESES) or "/" in d:
        return d
    resto = d[len("mensalidade"):]
    return f"Mensalidade de {MESES[venc.month - 1]}/{venc.year}{resto}"


def _lista(itens: list[str]) -> str:
    itens = [i for i in itens if i]
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def montar(pool, conta_id: int, titulo_id: int) -> dict | None:
    """O recibo daquela conta, pronto pra congelar. None quando não cabe recibo:
    título de outra empresa, conta a pagar ou conta ainda aberta."""
    from finance import contrato as ctr
    from finance.aditivo import valor_por_extenso
    with pool.connection() as c:
        t = c.execute(
            """select t.tipo, t.status, t.descricao, t.contraparte, t.valor_centavos,
                      coalesce(t.acrescimo_centavos, 0), t.vencimento, t.pago_em,
                      t.orcamento_id, t.parcela_idx, t.cliente_id,
                      l.forma_pagamento
                 from titulos t
                 left join lancamentos l on l.id = t.lancamento_id and l.conta_id = t.conta_id
                where t.id=%s and t.conta_id=%s""",
            (titulo_id, conta_id)).fetchone()
        if not t or t[0] != "receber" or t[1] != "pago":
            return None
        (_tipo, _st, descricao, contraparte, valor, acrescimo, venc, pago_em,
         orc_id, idx, cliente_id, forma_lanc) = t
        emp = c.execute(
            """select coalesce(nullif(razao_social,''), nullif(nome_fantasia,''), nome),
                      documento, cidade, uf, logo_url
                 from contas where id=%s""", (conta_id,)).fetchone()
        abertas = None
        tem_contrato = False
        if orc_id:
            abertas = c.execute(
                """select count(*), coalesce(sum(valor_centavos), 0) from titulos
                    where conta_id=%s and orcamento_id=%s and tipo='receber'
                      and status='aberto'""", (conta_id, orc_id)).fetchone()
            try:
                tem_contrato = bool(c.execute(
                    "select 1 from contratos where conta_id=%s and orcamento_id=%s limit 1",
                    (conta_id, orc_id)).fetchone())
            except Exception:  # noqa: BLE001 — base sem contratos: só some a frase
                c.rollback()
                tem_contrato = False

    pagador, doc = (contraparte or "").strip(), ""
    referente = ""
    forma = _forma(forma_lanc or "")
    if orc_id:
        from web.contrato_publico import qualificacao
        q = qualificacao(pool, conta_id, orc_id)
        if q:
            pagador = q["contratante"]["nome"] or pagador
            doc = q["contratante"]["doc"]
            parcelas = q.get("parcelas") if isinstance(q.get("parcelas"), list) else []
            p = parcelas[idx] if (idx is not None and 0 <= idx < len(parcelas)) else {}
            p = p if isinstance(p, dict) else {}
            if not forma:
                forma = _forma(str(p.get("forma") or ""))
            rot = _rotulo_da_parcela(descricao, str(p.get("obs") or ""))
            num = q.get("orcamento_numero")
            base = f"{_artigo(rot)} {rot}" + (f" do orçamento nº {num}" if num else "")
            if q.get("modo_orcamento") == "evento":
                ev = q.get("evento_bruto") or {}
                o_que = [str(x).strip() for x in (ev.get("contratos") or []) if str(x).strip()]
                o_que = _lista([x[:1].lower() + x[1:] for x in o_que])
                tipo_ev = str(ev.get("tipo") or "Evento").strip()
                quando = ctr.data_br(ev.get("data")) if ev.get("data") else ""
                obj = tipo_ev + (f" em {quando}" if quando else "")
                referente = f"{base}: {obj}" + (f", {o_que}" if o_que else "")
            else:
                referente = base
    if not referente:
        rot = _mensalidade_com_mes(descricao, venc)
        referente = f"{_artigo(rot)} {rot}"
    if not doc and cliente_id:
        o = ctr.completar_do_cadastro(pool, conta_id, {"cnpj": ""}, cliente_id)
        doc = _doc(o.get("cnpj") or "")

    total = int(valor) + int(acrescimo)
    detalhe = ""
    if acrescimo > 0:
        detalhe = (f"{ctr.reais(valor)} da parcela e {ctr.reais(acrescimo)} "
                   "de juros e multa pelo atraso")
    elif acrescimo < 0:
        detalhe = f"{ctr.reais(valor)} da parcela, com {ctr.reais(-acrescimo)} de desconto"

    restante = ""
    if abertas is not None:
        n, soma = int(abertas[0]), int(abertas[1])
        if n:
            restante = (f"Permanecem em aberto {ctr.reais(soma)}, em {n} "
                        f"parcela{'s' if n != 1 else ''}"
                        + (", conforme o contrato." if tem_contrato else "."))
        else:
            restante = "Com este pagamento, não resta nada em aberto deste orçamento."

    cidade = _cidade(emp[2] if emp else "")
    quando = pago_em or date.today()
    return {
        "empresa": {"nome": (emp[0] if emp else "") or "", "doc": _doc(emp[1] if emp else ""),
                    "cidade": cidade, "uf": ((emp[3] if emp else "") or "").upper(),
                    "logo": (emp[4] if emp else "") or ""},
        "pagador": {"nome": pagador.upper(), "doc": doc},
        "valor_centavos": total,
        "valor": ctr.reais(total),
        "extenso": valor_por_extenso(total).split(" (", 1)[-1].rstrip(")"),
        "detalhe": detalhe,
        "referente": referente,
        "forma": forma,
        "restante": restante,
        "pago_em": quando.isoformat(),
        "data_br": quando.strftime("%d/%m/%Y"),
        "fecho": (f"{cidade}, " if cidade else "") + _data_extenso(quando) + ".",
    }


def _por_titulo(c, conta_id: int, titulo_id: int):
    return c.execute(
        """select id, ano, numero, token, emitido_em, enviado_em from recibos
            where conta_id=%s and titulo_id=%s""", (conta_id, titulo_id)).fetchone()


def _link(token: str) -> str:
    from finance.email_sender import _app_url
    return f"{_app_url()}/recibo/{token}"


def _saida(row, dados) -> dict:
    return {"id": row[0], "ano": row[1], "numero": row[2],
            "rotulo": f"{row[2]:04d}/{row[1]}", "token": row[3],
            "link": _link(row[3]), "emitido_em": row[4], "enviado_em": row[5],
            "dados": dados}


def emitir(pool, conta_id: int, titulo_id: int, membro_id: int | None = None) -> dict:
    """Gera (ou gera de novo) o recibo daquela conta. {ok, recibo} ou {ok: False, erro}.

    O número sai sob `pg_advisory_xact_lock` da empresa: duas pessoas gerando ao
    mesmo tempo não disputam o mesmo número — e a unique da 322 é a rede embaixo."""
    import json
    d = montar(pool, conta_id, titulo_id)
    if not d:
        return {"ok": False, "erro": "Só conta a receber já recebida tem recibo."}
    with pool.connection() as c:
        c.execute("select pg_advisory_xact_lock(%s, %s)", (32201, int(conta_id)))
        r = _por_titulo(c, conta_id, titulo_id)
        if r:
            c.execute("update recibos set dados=%s::jsonb, atualizado_em=now() where id=%s",
                      (json.dumps(d), r[0]))
        else:
            ano = date.fromisoformat(d["pago_em"]).year
            prox = c.execute(
                "select coalesce(max(numero), 0) + 1 from recibos where conta_id=%s and ano=%s",
                (conta_id, ano)).fetchone()[0]
            c.execute(
                """insert into recibos (conta_id, titulo_id, ano, numero, token, dados, emitido_por)
                   values (%s, %s, %s, %s, %s, %s::jsonb, %s)""",
                (conta_id, titulo_id, ano, int(prox), secrets.token_urlsafe(16),
                 json.dumps(d), membro_id))
            r = _por_titulo(c, conta_id, titulo_id)
        c.commit()
    return {"ok": True, "recibo": _saida(r, d)}


def do_titulo(pool, conta_id: int, titulo_id: int) -> dict | None:
    with pool.connection() as c:
        r = _por_titulo(c, conta_id, titulo_id)
        if not r:
            return None
        dados = c.execute("select dados from recibos where id=%s", (r[0],)).fetchone()[0]
    return _saida(r, dados)


def mapa(pool, conta_id: int) -> dict[int, str]:
    """{titulo_id: "0001/2026"} — pra lista dos baixados dizer qual já tem recibo.
    Tolerante: sem a tabela (base antiga, teste de esquema à mão), mapa vazio."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select titulo_id, ano, numero from recibos "
                "where conta_id=%s and titulo_id is not null", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {int(t): f"{n:04d}/{a}" for t, a, n in rows}


def por_token(pool, token: str) -> dict | None:
    """O recibo do link público. O número vem junto; a empresa, do `dados`."""
    if not token or len(token) > 64:
        return None
    with pool.connection() as c:
        r = c.execute(
            "select ano, numero, dados, emitido_em from recibos where token=%s",
            (token,)).fetchone()
    if not r:
        return None
    from zoneinfo import ZoneInfo
    emitido = r[3].astimezone(ZoneInfo("America/Sao_Paulo")) if r[3] else None
    return {"rotulo": f"{r[1]:04d}/{r[0]}", "dados": r[2], "emitido_em": emitido,
            "token": token, "link": _link(token)}


def lead_do_titulo(pool, conta_id: int, titulo_id: int) -> int | None:
    """A conversa por onde o recibo sai: o lead do orçamento daquela parcela.
    Conta sem orçamento (ou orçamento sem lead) não tem por onde mandar pelo Zaq —
    a tela oferece o "copiar link" e pronto."""
    with pool.connection() as c:
        r = c.execute(
            """select p.id from titulos t
                 join prospeccao p on p.orcamento_id = t.orcamento_id
                                  and p.conta_id = t.conta_id
                where t.id=%s and t.conta_id=%s
                order by p.id desc limit 1""", (titulo_id, conta_id)).fetchone()
    return int(r[0]) if r else None


def texto_da_mensagem(rec: dict) -> str:
    d = rec.get("dados") or {}
    return (f"Olá! Segue o recibo nº {rec['rotulo']} do pagamento de "
            f"{d.get('valor', '')} 🧾\n{rec['link']}")


def mandar(pool, conta_id: int, membro_id: int | None, titulo_id: int) -> dict:
    """Manda o link do recibo na conversa do cliente, pelo canal da conta.

    Grava no inbox (a conversa mostra que o recibo saiu) e carimba `enviado_em`.
    NÃO pausa o agente nem mexe no status da conversa, ao contrário da resposta
    humana do inbox: mandar um recibo não é assumir o atendimento.

    Sem conexão presa durante a rede — o par `preparar`/`enviar_pronto` existe pra
    isso (ver `whatsapp_out`)."""
    from finance import whatsapp_out
    from web.painel_prospeccao import _add_msg, _conversa_id
    rec = do_titulo(pool, conta_id, titulo_id)
    if not rec:
        return {"ok": False, "erro": "Gere o recibo antes de mandar."}
    lead = lead_do_titulo(pool, conta_id, titulo_id)
    if not lead:
        return {"ok": False, "erro": "Esta conta não tem conversa ligada no Zaq — "
                                     "copie o link e mande por onde preferir."}
    texto = texto_da_mensagem(rec)
    with pool.connection() as c:
        p = c.execute("select whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
                      (lead, conta_id)).fetchone()
        numero = (p[0] or p[1] or "") if p else ""
        if not numero:
            return {"ok": False, "erro": "O cliente não tem WhatsApp no cadastro do lead."}
        conv = _conversa_id(c, conta_id, lead, "whatsapp")
        chip = whatsapp_out.chip_da_conversa(c, conta_id, conv)
        destino = whatsapp_out.preparar(c, conta_id)
        c.commit()
    res = whatsapp_out.enviar_pronto(destino, numero, texto, chip_id=chip)
    if not res.get("ok"):
        erro = str(res.get("erro") or "")
        if erro == "sem_numero_empresa":
            erro = "Esta empresa não tem WhatsApp ligado no Zaq."
        return {"ok": False, "erro": erro or "O WhatsApp não aceitou o envio."}
    with pool.connection() as c:
        _add_msg(c, conv, "whatsapp", "out", "humano", texto, membro_id, res.get("sid"))
        c.execute("update recibos set enviado_em=coalesce(enviado_em, now()) where id=%s",
                  (rec["id"],))
        c.commit()
    return {"ok": True}
