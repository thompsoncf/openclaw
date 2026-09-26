"""O orçamento da REFORMA: itens, aceite do cliente, aditivo e cobrança por etapa.

PR 4 de 4 do desenho aprovado pelo dono em 25/09/2026
(docs/mockups/nicho_construcao.html, seção 09). A primeira conta é a PX2
(conta 33), que além de casa pra vender faz reforma pra cliente.

AS QUATRO ESCOLHAS:

1. **O orçamento mora na obra**, não em `orcamentos`. Ver a migração 355: um
   terceiro modo no orçamento de todo mundo mexeria em proposta, contrato e funil
   de todas as contas pra atender um ramo. A reforma já é obra, com etapas e
   centro de custo.

2. **O que a lei manda, vira campo, e não parágrafo.** O CDC (art. 40) pede o
   orçamento discriminando mão de obra, material e equipamento, com forma de
   pagamento e datas, e valendo 10 dias: cada item tem `tipo`, os totais saem
   separados, e o link nasce com validade de 10 dias. Material só é incluso se
   estiver escrito (CC, art. 610). O texto das cláusulas é um MODELO que a
   empresa revisa — o Zaq não escreve contrato por ninguém.

3. **Serviço extra é ADITIVO, uma versão nova com aceite próprio** (CC, art. 619),
   e não uma linha enfiada no orçamento que o cliente já aceitou. A cobrança
   extra no meio da obra é a briga mais comum da reforma; o aceite por escrito é
   o que resolve.

4. **A parcela ligada a uma etapa fica LIBERADA pra cobrar quando a etapa é
   concluída.** O título nasce no aceite, com vencimento estimado; quando o
   Pablo diz no WhatsApp "terminou o reboco da reforma", o agente avisa que a
   parcela daquela etapa já pode ser cobrada. Modelo Reforma Casa Brasil: o
   cliente recebe 90% do crédito na contratação e 10% depois das fotos da obra
   pronta, em até 55 dias — então a última parcela é a da entrega, com as fotos.

Multi-tenant sagrado: toda consulta é escopada por `conta_id` — exceto a do
link público, que é escopada pelo TOKEN, e só lê.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, timedelta

from . import obras as _ob

TIPOS_ITEM = {"mao_de_obra": "Mão de obra", "material": "Material",
              "equipamento": "Equipamento"}
UNIDADES = {"m2": "m²", "m": "m", "un": "un", "diaria": "diária",
            "empreitada": "empreitada", "vb": "verba"}
MODELOS = {"etapas": "Entrada + parcelas por etapa",
           "rcb": "Reforma Casa Brasil (90% + 10%, 55 dias)",
           "avista": "À vista, na assinatura"}
STATUS = {"rascunho": "Rascunho", "enviado": "Enviado ao cliente",
          "aceito": "Aceito", "recusado": "Recusado"}
VALIDADE_DIAS = 10          # CDC, art. 40, §1º
PRAZO_RCB_DIAS = 55
GARANTIA_PADRAO = "90 dias para defeito aparente, contados da entrega (CDC, art. 26)."

_COLS = ("id", "obra_id", "versao", "itens", "material_incluso", "prazo_dias",
         "validade_ate", "garantia", "escopo", "modelo_pagamento", "parcelas",
         "total_centavos", "status", "token", "aceito_em", "aceito_nome", "aceito_doc",
         "titulos")


def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def _linha(r) -> dict:
    o = dict(zip(_COLS, r))
    o["itens"] = _json(o["itens"])
    o["parcelas"] = _json(o["parcelas"])
    o["titulos"] = _json(o["titulos"])
    o["totais"] = totais(o["itens"])
    o["rotulo_status"] = STATUS[o["status"]]
    o["rotulo_modelo"] = MODELOS[o["modelo_pagamento"]]
    return o


# ─────────────────────────────────────────────────────────────── as contas
def limpar_itens(itens) -> list[dict]:
    """Os itens como a tela manda, validados: sem serviço, sem item."""
    out = []
    for it in itens or []:
        servico = " ".join(str(it.get("servico") or "").split())
        if not servico:
            continue
        tipo = it.get("tipo") if it.get("tipo") in TIPOS_ITEM else "mao_de_obra"
        unidade = it.get("unidade") if it.get("unidade") in UNIDADES else "un"
        try:
            qtd = max(0.0, float(str(it.get("quantidade") or 0).replace(",", ".")))
        except ValueError:
            qtd = 0.0
        valor = max(0, int(it.get("valor_unit_centavos") or 0))
        out.append({"servico": servico[:160], "tipo": tipo, "unidade": unidade,
                    "quantidade": qtd, "valor_unit_centavos": valor,
                    "subtotal_centavos": int(round(qtd * valor))})
    return out


def totais(itens) -> dict:
    """Mão de obra, material e equipamento separados (CDC, art. 40) e o total."""
    out = {k: 0 for k in TIPOS_ITEM}
    for it in itens:
        out[it.get("tipo", "mao_de_obra")] += int(it.get("subtotal_centavos") or 0)
    out["total"] = sum(out[k] for k in TIPOS_ITEM)
    return out


def _etapa_do_meio_e_fim(etapas: list[dict]) -> tuple:
    if not etapas:
        return None, None
    meio = etapas[max(0, (len(etapas) - 1) // 2)]["chave"]
    return meio, etapas[-1]["chave"]


def parcelas_do_modelo(modelo: str, etapas: list[dict]) -> list[dict]:
    """As parcelas sugeridas de cada modelo, em %. A tela deixa mudar."""
    meio, fim = _etapa_do_meio_e_fim(etapas)
    if modelo == "avista":
        return [{"rotulo": "Na assinatura", "pct": 100, "etapa": None}]
    if modelo == "rcb":
        return [{"rotulo": "Entrada, na assinatura", "pct": 50, "etapa": None},
                {"rotulo": "Na metade da obra", "pct": 40, "etapa": meio},
                {"rotulo": "Na entrega, com as fotos da obra pronta", "pct": 10, "etapa": fim}]
    return [{"rotulo": "Entrada, na assinatura", "pct": 30, "etapa": None},
            {"rotulo": "Na metade da obra", "pct": 40, "etapa": meio},
            {"rotulo": "Na entrega", "pct": 30, "etapa": fim}]


def valorar_parcelas(parcelas: list[dict], total: int) -> list[dict]:
    """Põe o valor em cada parcela pelo %; o centavo que sobra fica na última,
    pra soma fechar exatamente no total."""
    soma_pct = sum(float(p.get("pct") or 0) for p in parcelas)
    if not parcelas or abs(soma_pct - 100) > 0.01:
        raise ValueError("As parcelas precisam somar 100%.")
    out, acumulado = [], 0
    for i, p in enumerate(parcelas):
        v = total - acumulado if i == len(parcelas) - 1 else \
            int(round(total * float(p["pct"]) / 100))
        acumulado += v
        out.append({"rotulo": " ".join(str(p.get("rotulo") or f"Parcela {i + 1}").split())[:80],
                    "pct": float(p["pct"]), "etapa": p.get("etapa") or None,
                    "valor_centavos": v})
    return out


# ─────────────────────────────────────────────────────────────── leitura
def orcamentos(pool, conta_id: int, obra_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(f"select {', '.join(_COLS)} from obra_orcamentos "
                         "where conta_id=%s and obra_id=%s order by versao",
                         (conta_id, obra_id)).fetchall()
    return [_linha(r) for r in rows]


def por_token(pool, token: str) -> dict | None:
    """O link público: o orçamento, a obra e o nome da empresa. Só lê."""
    if not token or len(token) < 10:
        return None
    with pool.connection() as c:
        r = c.execute(f"select {', '.join(_COLS)}, conta_id from obra_orcamentos "
                      "where token=%s", (token,)).fetchone()
        if not r:
            return None
        o = _linha(r[:-1])
        conta_id = r[-1]
        obra = c.execute("select nome, endereco from obras where id=%s and conta_id=%s",
                         (o["obra_id"], conta_id)).fetchone()
        emp = c.execute("select coalesce(nullif(nome_fantasia,''), nullif(razao_social,''), nome) "
                        "from contas where id=%s", (conta_id,)).fetchone()
    etapas = {e["chave"]: e["nome"] for e in
              (_ob.obter_obra(pool, conta_id, o["obra_id"]) or {}).get("etapas", [])}
    for p in o["parcelas"]:
        p["etapa_nome"] = etapas.get(p.get("etapa"))
    o.update(conta_id=conta_id, obra_nome=obra[0] if obra else "",
             obra_endereco=obra[1] if obra else "", empresa=emp[0] if emp else "")
    o["vencido"] = bool(o["validade_ate"] and o["validade_ate"] < date.today()
                        and o["status"] == "enviado")
    o["clausulas"] = clausulas(o)
    try:
        o["pagar"] = pagar_pelo_link(pool, o)
    except Exception:  # noqa: BLE001 — sem a 367, a página abre sem o Pix
        o["pagar"] = []
    return o


def clausulas(o: dict) -> list[tuple[str, str]]:
    """O MODELO das cláusulas que acompanha o orçamento — ver a escolha 2."""
    prazo = f"{o['prazo_dias']} dias corridos, contados do início da obra." \
        if o.get("prazo_dias") else "A combinar entre as partes."
    return [
        ("Escopo", o.get("escopo") or "Os serviços da lista acima, e só eles."),
        ("Material", "Incluso no preço, conforme os itens de material acima."
         if o.get("material_incluso") else
         "NÃO incluso: o material é comprado pelo contratante."),
        ("Prazo", prazo),
        ("Pagamento", "Conforme o cronograma acima. A parcela ligada a uma etapa é devida "
                      "quando a etapa é concluída."),
        ("Serviço extra", "Serviço fora deste orçamento só é feito e cobrado com um aditivo "
                          "aceito pelo contratante, com valor e prazo próprios."),
        ("Responsável técnico", "Quando o serviço mexer em estrutura, paredes ou instalações, "
                                "a empresa providencia a ART ou o RRT da obra."),
        ("Garantia", o.get("garantia") or GARANTIA_PADRAO),
    ]


# ─────────────────────────────────────────────────────────────── escrita
def _obra_reforma(c, conta_id: int, obra_id: int):
    r = c.execute("select id, tipo, nome, centro_custo_id from obras "
                  "where id=%s and conta_id=%s", (obra_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Obra não encontrada.")
    if r[1] != "reforma":
        raise ValueError("Orçamento com aceite é da reforma; a casa pra vender tem a venda.")
    return r


def salvar_rascunho(pool, conta_id: int, obra_id: int, *, itens, material_incluso: bool = True,
                    prazo_dias: int | None = None, garantia: str = "", escopo: str = "",
                    modelo_pagamento: str = "etapas", parcelas=None) -> dict:
    """Cria ou atualiza a versão em aberto (a última). Enquanto o cliente não
    aceitou, ela muda: enviada continua enviada, com o MESMO link (o cliente vê a
    versão nova ao abrir); recusada volta a rascunho. Aceita não muda mais —
    serviço novo é aditivo (`abrir_aditivo`)."""
    if modelo_pagamento not in MODELOS:
        raise ValueError("Modelo de pagamento desconhecido.")
    itens = limpar_itens(itens)
    if not itens:
        raise ValueError("O orçamento precisa de pelo menos um serviço.")
    tot = totais(itens)["total"]
    obra = _ob.obter_obra(pool, conta_id, obra_id)
    if not obra:
        raise ValueError("Obra não encontrada.")
    if not parcelas:
        parcelas = parcelas_do_modelo(modelo_pagamento, obra["etapas"])
    parcelas = valorar_parcelas(parcelas, tot)
    if modelo_pagamento == "rcb" and not prazo_dias:
        prazo_dias = PRAZO_RCB_DIAS
    with pool.connection() as c:
        _obra_reforma(c, conta_id, obra_id)
        ult = c.execute("select id, versao, status from obra_orcamentos where obra_id=%s "
                        "and conta_id=%s order by versao desc limit 1",
                        (obra_id, conta_id)).fetchone()
        campos = (json.dumps(itens), bool(material_incluso), prazo_dias,
                  (garantia or "").strip() or GARANTIA_PADRAO, (escopo or "").strip(),
                  modelo_pagamento, json.dumps(parcelas), tot)
        if ult and ult[2] in ("rascunho", "enviado", "recusado"):
            c.execute("""update obra_orcamentos
                            set itens=%s, material_incluso=%s, prazo_dias=%s, garantia=%s,
                                escopo=%s, modelo_pagamento=%s, parcelas=%s,
                                total_centavos=%s, atualizado_em=now(),
                                status=case when status='recusado' then 'rascunho'
                                            else status end
                          where id=%s and conta_id=%s""", (*campos, ult[0], conta_id))
            oid = ult[0]
        elif ult:
            raise ValueError("Este orçamento já foi aceito. Serviço novo entra como aditivo.")
        else:
            oid = c.execute("""insert into obra_orcamentos
                                   (conta_id, obra_id, versao, itens, material_incluso,
                                    prazo_dias, garantia, escopo, modelo_pagamento,
                                    parcelas, total_centavos)
                                values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                            (conta_id, obra_id, *campos)).fetchone()[0]
        c.commit()
    return next(o for o in orcamentos(pool, conta_id, obra_id) if o["id"] == oid)


def abrir_aditivo(pool, conta_id: int, obra_id: int) -> int:
    """Abre a versão seguinte, em rascunho e vazia — ver a escolha 3. Só depois de
    a anterior ter sido ACEITA: aditivo de orçamento que o cliente nem viu é só um
    orçamento maior. Devolve o número da versão."""
    with pool.connection() as c:
        _obra_reforma(c, conta_id, obra_id)
        ult = c.execute("select versao, status from obra_orcamentos where obra_id=%s "
                        "and conta_id=%s order by versao desc limit 1",
                        (obra_id, conta_id)).fetchone()
        if not ult or ult[1] != "aceito":
            raise ValueError("O aditivo vem depois do orçamento aceito.")
        c.execute("""insert into obra_orcamentos (conta_id, obra_id, versao, garantia)
                          values (%s,%s,%s,%s)""",
                  (conta_id, obra_id, ult[0] + 1, GARANTIA_PADRAO))
        c.commit()
    return ult[0] + 1


def enviar(pool, conta_id: int, orcamento_id: int) -> dict:
    """Gera o link do cliente e fixa a validade de 10 dias (CDC, art. 40)."""
    with pool.connection() as c:
        r = c.execute("select obra_id, status, token, total_centavos from obra_orcamentos "
                      "where id=%s and conta_id=%s", (orcamento_id, conta_id)).fetchone()
        if not r:
            raise ValueError("Orçamento não encontrado.")
        if not r[3]:
            raise ValueError("Orçamento sem valor não sai: preencha os serviços.")
        if r[1] not in ("rascunho", "enviado"):
            raise ValueError("Este orçamento já foi respondido pelo cliente.")
        token = r[2] or secrets.token_urlsafe(18)
        c.execute("""update obra_orcamentos set status='enviado', token=%s,
                            validade_ate=%s, atualizado_em=now()
                      where id=%s and conta_id=%s""",
                  (token, date.today() + timedelta(days=VALIDADE_DIAS), orcamento_id, conta_id))
        c.commit()
    return next(o for o in orcamentos(pool, conta_id, r[0]) if o["id"] == orcamento_id)


def _plano(pool, codigo: str) -> int | None:
    try:
        with pool.connection() as c:
            r = c.execute("select id from plano_contas where codigo=%s", (codigo,)).fetchone()
        return r[0] if r else None
    except Exception:  # noqa: BLE001
        return None


def aceitar(pool, token: str, nome: str, doc: str, ip: str) -> bool:
    """O cliente aceita pelo link. Idempotente: só aceita o que está ENVIADO e
    dentro da validade. Aceito, cada parcela vira um título a receber no centro
    da obra, na conta 1.1.02 (Prestação de Serviços)."""
    nome = " ".join((nome or "").split())
    if not nome:
        return False
    with pool.connection() as c:
        r = c.execute(
            """update obra_orcamentos
                  set status='aceito', aceito_em=now(), aceito_nome=%s, aceito_doc=%s,
                      aceito_ip=%s, atualizado_em=now()
                where token=%s and status='enviado'
                  and (validade_ate is null or validade_ate >= current_date)
            returning id, conta_id, obra_id, versao, parcelas, prazo_dias, total_centavos""",
            (nome[:120], (doc or "").strip()[:40] or None, (ip or "")[:60], token)).fetchone()
        c.commit()
    if not r:
        return False
    oid, conta_id, obra_id, versao, parcelas, prazo, total = r
    _titulos_do_aceite(pool, conta_id, obra_id, oid, versao, _json(parcelas), prazo, nome)
    with pool.connection() as c:
        c.execute("""update obras set valor_centavos=coalesce(valor_centavos, 0) + %s,
                            atualizado_em=now()
                      where id=%s and conta_id=%s""", (int(total), obra_id, conta_id))
        c.commit()
    return True


def _titulos_do_aceite(pool, conta_id: int, obra_id: int, orcamento_id: int, versao: int,
                       parcelas: list[dict], prazo: int | None, cliente: str) -> list[int]:
    from . import empresa as _emp
    with pool.connection() as c:
        nome, centro = c.execute("select nome, centro_custo_id from obras where id=%s "
                                 "and conta_id=%s", (obra_id, conta_id)).fetchone()
    cliente_id = None
    try:
        from . import clientes as _cli
        cliente_id = _cli.achar_cliente_por_nome(pool, conta_id, cliente, papel="cliente") or \
            _cli.criar_cliente(pool, conta_id, cliente)
    except Exception:  # noqa: BLE001 — sem a base de clientes, o título vai sem a ficha
        cliente_id = None
    plano = _plano(pool, "1.1.02")
    hoje = date.today()
    fim = hoje + timedelta(days=prazo or 30)
    ids = []
    rotulo_v = "" if versao == 1 else f" (aditivo {versao - 1})"
    for p in parcelas:
        if int(p.get("valor_centavos") or 0) <= 0:
            continue
        t = _emp.criar_titulo(
            pool, conta_id, "receber", f"{nome}{rotulo_v} — {p['rotulo']}",
            int(p["valor_centavos"]), hoje if not p.get("etapa") else fim,
            contraparte=cliente, categoria="Vendas", cliente_id=cliente_id,
            plano_conta_id=plano, centro_custo_id=centro)
        ids.append(t["id"])
    with pool.connection() as c:
        c.execute("update obra_orcamentos set titulos=%s where id=%s and conta_id=%s",
                  (json.dumps(ids), orcamento_id, conta_id))
        c.commit()
    return ids


def recusar(pool, token: str) -> bool:
    with pool.connection() as c:
        r = c.execute("update obra_orcamentos set status='recusado', atualizado_em=now() "
                      "where token=%s and status='enviado' returning id", (token,)).fetchone()
        c.commit()
    return bool(r)


# ─────────────────────────────────────────────────────────────── a cobrança
def parcelas_liberadas(pool, conta_id: int, obra: dict) -> list[dict]:
    """As parcelas cuja etapa já foi concluída e cujo título ainda está aberto —
    ver a escolha 4. É o que o agente oferece cobrar."""
    feitas = {e["chave"]: e["nome"] for e in obra.get("etapas", []) if e["concluida_em"]}
    out = []
    for o in orcamentos(pool, conta_id, obra["id"]):
        if o["status"] != "aceito":
            continue
        pagaveis = [p for p in o["parcelas"] if int(p.get("valor_centavos") or 0) > 0]
        for p, tid in zip(pagaveis, o["titulos"]):
            if not p.get("etapa") or p["etapa"] not in feitas:
                continue
            with pool.connection() as c:
                st = c.execute("select status from titulos where id=%s and conta_id=%s",
                               (tid, conta_id)).fetchone()
            if st and st[0] == "aberto":
                out.append({"rotulo": p["rotulo"], "etapa": feitas[p["etapa"]],
                            "valor_centavos": int(p["valor_centavos"]), "titulo_id": tid,
                            "versao": o["versao"]})
    return out


# ── cobrar a parcela liberada (Pix da própria empresa, finance/pix.py) ─────
def _app_url() -> str:
    import os
    return (os.environ.get("APP_URL") or "https://app.zaq-ia.com").rstrip("/")


def mensagem_de_cobranca(parcela: dict, cliente: str, empresa: str,
                         pix_copia_cola: str | None, link: str) -> str:
    """O texto que a empresa manda do WhatsApp DELA (decisão do dono em 26/09: o
    cliente recebe de um número que conhece). A etapa pronta vem primeiro: é o
    motivo da cobrança, e é o que o CC art. 614 chama de obra medida."""
    primeiro = (cliente or "").split()[0].title() if (cliente or "").strip() else ""
    linhas = [f"Olá{', ' + primeiro if primeiro else ''}! A etapa *{parcela['etapa'].lower()}* "
              "da sua reforma ficou pronta ✅",
              f"Parcela: {parcela['rotulo']} — *{_ob._brl(parcela['valor_centavos'])}*"]
    if pix_copia_cola:
        linhas += ["", "Pix copia e cola:", pix_copia_cola, "",
                   f"Ou pelo QR code, no seu orçamento: {link}"]
    else:
        linhas += ["", f"Seu orçamento e as parcelas: {link}"]
    linhas += ["", f"Qualquer dúvida, é só me chamar. {empresa}".strip()]
    return "\n".join(linhas)


def cobrancas(pool, conta_id: int, obra: dict) -> list[dict]:
    """Cada parcela liberada e em aberto, com o Pix e a mensagem prontos. O Pix só
    vai se a empresa cadastrou a chave (sem ela, a mensagem vai sem Pix)."""
    from . import pix as _pix
    livres = parcelas_liberadas(pool, conta_id, obra)
    if not livres:
        return []
    versoes = {o["versao"]: o for o in orcamentos(pool, conta_id, obra["id"])}
    chave = _pix.da_conta(pool, conta_id)
    with pool.connection() as c:
        r = c.execute("select coalesce(nullif(nome_fantasia,''), nullif(razao_social,''), nome) "
                      "from contas where id=%s", (conta_id,)).fetchone()
    empresa = r[0] if r else ""
    out = []
    for p in livres:
        v = versoes.get(p["versao"]) or {}
        link = f"{_app_url()}/orcamento-obra/{v.get('token')}" if v.get("token") else _app_url()
        codigo = None
        if chave:
            try:
                codigo = _pix.copia_e_cola(chave["chave"], p["valor_centavos"], chave["recebedor"],
                                           chave["cidade"], txid=f"OBRA{obra['id']}T{p['titulo_id']}")
            except ValueError:
                codigo = None
        msg = mensagem_de_cobranca(p, v.get("aceito_nome") or "", empresa, codigo, link)
        out.append(dict(p, cliente=v.get("aceito_nome") or "", token=v.get("token"),
                        pix=codigo, link=link, mensagem=msg))
    return out


def pagar_pelo_link(pool, orc: dict) -> list[dict]:
    """O que a página pública do orçamento mostra pra pagar: as parcelas DESTA
    versão que a etapa liberou e continuam abertas, com o Pix e o QR."""
    from . import pix as _pix
    if orc.get("status") != "aceito":
        return []
    obra = _ob.obter_obra(pool, orc["conta_id"], orc["obra_id"])
    if not obra:
        return []
    out = []
    for cb in cobrancas(pool, orc["conta_id"], obra):
        if cb["versao"] != orc["versao"] or not cb["pix"]:
            continue
        out.append(dict(cb, qr=_pix.qr_svg(cb["pix"])))
    return out
