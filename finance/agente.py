"""Motor do Agente IA de atendimento (Fase D).

Ao chegar uma mensagem do lead, lê a config + base de conhecimento + catálogo +
histórico e decide: responder, gerar orçamento prévio, ou escalar pro humano
(handoff). Respeita confiança/horário/máx-trocas. Tudo best-effort: qualquer
falha → não responde (seguro), nunca estoura pro webhook.

Reusa core.brain.Brain (mesma IA do "sugerir escopo"), servicos_catalogo e
whatsapp_twilio. Só WhatsApp por enquanto (único canal ao vivo).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from finance import servicos_catalogo as scat

_log = logging.getLogger("agente")


def _cfg(c, conta_id):
    r = c.execute(
        """select ativo, limiar_confianca, horario, tom, max_trocas, escalar_para,
                  pode_responder, pode_qualificar, pode_agendar, pode_orcamento, orcamento_proativo
             from agente_config where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return None
    ks = ["ativo", "limiar", "horario", "tom", "max_trocas", "escalar_para",
          "pode_responder", "pode_qualificar", "pode_agendar", "pode_orcamento", "proativo"]
    return dict(zip(ks, r))


def _horario_ok(cfg) -> bool:
    if cfg["horario"] != "comercial":
        return True
    agora = datetime.now(timezone.utc) - timedelta(hours=3)   # Brasil (UTC-3)
    return agora.weekday() <= 5 and 8 <= agora.hour < 18       # seg–sáb, 8–18h


def _reais(centavos: int) -> str:
    """R$ 8.800 — sem centavos, com ponto de milhar. É texto que vai pro cliente."""
    return "R$ " + f"{(centavos or 0) // 100:,}".replace(",", ".")


def _linha_catalogo(s) -> str:
    """Uma linha do catálogo do jeito que a IA deve LER — e, por tabela, falar.

    O catálogo nasceu pra serviço recorrente (setup + mensalidade), e a linha era
    sempre "setup R$X, mensal R$Y". Isso quebra em dois casos reais:

    1. LOCAÇÃO. A Prime Eventos tem 26 itens com mensal ZERO — pacote de espaço,
       DJ, hora extra, taxa de limpeza. A linha antiga mandava "mensal R$0" pra IA,
       e "mensal R$ 0" numa conversa de aluguel de salão não quer dizer nada; na
       melhor das hipóteses confunde, na pior o cliente entende que tem uma
       mensalidade zerada que vai virar cobrança depois.

    2. PREÇO A COMBINAR. Serviço cadastrado com os dois valores em zero (é o caso
       de uma assessoria de consórcio na base) virava "setup R$0, mensal R$0" — a
       IA lendo isso pode dizer ao cliente que é DE GRAÇA. Zero aqui nunca
       significa gratuito, significa que ninguém preencheu.

    Então: mostra só o que tem valor, e o que não tem vira "valor sob consulta",
    que é a verdade e ainda deixa a IA saber que precisa perguntar."""
    setup = s.get("setup_centavos") or 0
    mensal = s.get("mensal_centavos") or 0
    if setup and mensal:
        preco = f"{_reais(setup)} de entrada + {_reais(mensal)} por mês"
    elif setup:
        preco = _reais(setup)
    elif mensal:
        preco = f"{_reais(mensal)} por mês"
    else:
        preco = "valor sob consulta (não cadastrado — pergunte, não invente)"
    return f"- {s['nome']} (slug {s['slug']}): {preco}"


def _conhecimento(c, conta_id):
    rows = c.execute(
        "select tipo, pergunta, resposta from agente_conhecimento where conta_id=%s order by ordem, id",
        (conta_id,)).fetchall()
    instr, faqs = "", []
    for (tipo, perg, resp) in rows:
        if tipo == "instrucoes":
            instr = resp or ""
        elif perg and resp:
            faqs.append(f"P: {perg}\nR: {resp}")
    return instr, "\n\n".join(faqs)


def _add_bot_msg(c, conversa_id, canal, texto, sid=None):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,%s,'out','bot',%s,%s)""", (conversa_id, canal, (texto or "")[:8000], sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))


def _mandar(c, conta_id, canal, destino, texto) -> dict:
    """Envia pelo canal certo: WhatsApp (Twilio/Cloud API) ou Messenger/Instagram (Meta)."""
    if canal in ("messenger", "instagram"):
        from finance import meta_msg
        r = c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                      (conta_id, canal)).fetchone()
        return meta_msg.enviar(r[0] if r else None, destino, texto, canal)
    from finance import whatsapp_out
    return whatsapp_out.enviar(c, conta_id, destino, texto)


def _extrair_json(txt: str) -> dict:
    txt = re.sub(r"^```json|^```|```$", "", (txt or "").strip(), flags=re.M).strip()
    return json.loads(txt)


def atender(pool, conta_id: int, conversa_id: int) -> None:
    """Decide e age numa conversa recém-recebida. Nunca estoura (best-effort)."""
    try:
        _atender(pool, conta_id, conversa_id)
    except Exception as e:  # noqa: BLE001
        _log.info("agente.atender falhou conta=%s conversa=%s: %s: %s",
                  conta_id, conversa_id, type(e).__name__, e)


def _atender(pool, conta_id, conversa_id):
    with pool.connection() as c:
        cfg = _cfg(c, conta_id)
        if not cfg or not cfg["ativo"]:
            return
        conv = c.execute(
            """select cv.agente_ativo, cv.prospeccao_id, cv.contato_ref, p.empresa,
                      p.whatsapp, p.telefone, p.segmento, p.cidade, p.uf, cv.canal
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, conta_id)).fetchone()
        if not conv or not conv[0]:      # conversa não existe ou humano assumiu
            return
        if not _horario_ok(cfg):
            return
        # histórico das últimas mensagens (contexto pro Brain)
        msgs = c.execute(
            """select direcao, autor, texto from mensagens
                where conversa_id=%s order by criado_em desc limit 12""", (conversa_id,)).fetchall()
        catalogo = scat.listar(pool, conta_id)
        instr, faqs = _conhecimento(c, conta_id)
        lead_empresa = conv[3] or conv[2] or "cliente"
        # canal + destino: WhatsApp usa nº do lead; Messenger/Instagram usam o PSID/IGSID (contato_ref)
        canal = conv[9] or "whatsapp"
        destino = conv[2] if canal in ("messenger", "instagram") else (conv[4] or conv[5] or conv[2])

        historico = "\n".join(
            ("Cliente: " if a == "lead" else ("Agente: " if a == "bot" else "Vendedor: ")) + (t or "")
            for (_d, a, t) in reversed(msgs))

        cat_txt = "\n".join(_linha_catalogo(s) for s in catalogo) or "(sem catálogo)"
        tom = "informal e próximo" if cfg["tom"] == "informal" else "formal e profissional"
        system = (
            "Você é o atendente virtual da empresa, no WhatsApp. Fala em português do "
            f"Brasil, tom {tom}, mensagens curtas. Use SÓ o que está na base abaixo — "
            "NUNCA invente preço, prazo ou promessa (se não tiver a info na base, diga "
            "que vai confirmar e siga a conversa). Seu objetivo é atender, tirar dúvidas "
            "e qualificar sozinho — NÃO ofereça 'chamar um consultor' a não ser que o "
            "CLIENTE peça explicitamente falar com uma pessoa. "
            "Responda SEMPRE só com JSON válido, sem markdown.\n\n"
            f"INSTRUÇÕES DA EMPRESA:\n{instr or '(nenhuma)'}\n\n"
            f"PERGUNTAS FREQUENTES:\n{faqs or '(nenhuma)'}\n\n"
            f"CATÁLOGO DE SERVIÇOS:\n{cat_txt}")
        pedir = (
            f"Conversa com {lead_empresa}:\n{historico}\n\n"
            "Responda a última mensagem do cliente. Retorne APENAS JSON:\n"
            '{"acao":"responder|orcamento","resposta":"texto pra mandar ao '
            'cliente","servicos":["slug"],"temperatura":"frio|morno|quente"}\n'
            "- acao=orcamento só se o cliente PEDIU preço/orçamento; liste em servicos "
            "os slugs do catálogo que fazem sentido. Senão, acao=responder e responda a "
            "dúvida direto, com base na base.\n"
            "- Sempre preencha resposta com um texto útil pra mandar ao cliente.")

        from core.brain import Brain
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        d = _extrair_json(txt)
        acao = d.get("acao") if d.get("acao") in ("responder", "orcamento") else "responder"
        resposta = (d.get("resposta") or "").strip()

        # qualificação: atualiza a temperatura do lead (se ligado e veio no JSON)
        if cfg["pode_qualificar"] and conv[1] and d.get("temperatura") in ("frio", "morno", "quente"):
            c.execute("update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
                      (d["temperatura"], conv[1], conta_id))

        # o agente fica SEMPRE ativo e segue o painel: NUNCA se desliga sozinho (nem por
        # confiança, nem por trocas, nem por 'achar' que precisa de humano). Quem assume
        # é um humano — botão "Assumir" ou responder pelo chat. Se o dono desligou o
        # "responder dúvidas" no painel, o agente fica quieto (mas continua ativo).
        if not cfg["pode_responder"]:
            return

        if acao == "orcamento" and cfg["pode_orcamento"]:
            return _orcamento(c, conta_id, conversa_id, conv, catalogo, d, canal, destino, resposta)

        # responde tudo (nunca escala/desliga automático)
        _enviar(c, conta_id, conversa_id, canal, destino, resposta or
                "Boa! Me conta um pouquinho mais que já te ajudo 😊")


def _enviar(c, conta_id, conversa_id, canal, destino, texto):
    if not texto:
        return
    res = _mandar(c, conta_id, canal, destino, texto)
    _add_bot_msg(c, conversa_id, canal, texto, res.get("sid") if res.get("ok") else None)
    c.commit()


def _orcamento(c, conta_id, conversa_id, conv, catalogo, d, canal, destino, resposta):
    slugs_ok = {s["slug"]: s for s in catalogo}
    escolhidos = [slugs_ok[s] for s in (d.get("servicos") or []) if s in slugs_ok]
    if not escolhidos:
        # sem serviços válidos → responde o texto e para
        return _enviar(c, conta_id, conversa_id, canal, destino, resposta or
                       "Me conta rapidinho o que você precisa que eu monto um orçamento 😊")
    setup = sum(s["setup_centavos"] for s in escolhidos)
    mensal = sum(s["mensal_centavos"] for s in escolhidos)
    itens = [{"nome": s["nome"], "setup": s["setup_centavos"], "mensal": s["mensal_centavos"]} for s in escolhidos]
    empresa = conv[3] or conv[2] or "Cliente"
    token = secrets.token_urlsafe(16)
    from finance import vendas as _vendas
    _n = c.execute("""select coalesce(n.slug,'') from contas ct
                        left join nichos n on n.id = ct.nicho_id
                       where ct.id=%s""", (conta_id,)).fetchone()
    c.execute(
        """insert into orcamentos (conta_id, cliente, empresa, modulos, itens, escopo,
             setup_centavos, mensal_centavos, n_modulos, criado_por, token, status, modo)
           values (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,'agente',%s,'rascunho',%s)""",
        (conta_id, empresa, empresa, json.dumps([s["slug"] for s in escolhidos]),
         json.dumps(itens), (resposta or "")[:2000], setup, mensal, len(escolhidos), token,
         _vendas.modo_por_nicho(_n[0] if _n else "")))
    from finance.email_sender import _app_url
    link = _app_url() + "/proposta/" + token
    linhas = "\n".join(f"• {s['nome']}: R$ {s['mensal_centavos']//100}/mês" for s in escolhidos)
    corpo = (resposta + "\n\n" if resposta else "") + (
        f"📄 Montei um orçamento prévio pra você:\n{linhas}\n"
        + (f"Setup: R$ {setup//100}\n" if setup else "")
        + f"Total mensal: R$ {mensal//100}\n\n"
        f"Ver a proposta: {link}\n\n"
        "É um valor de referência — quer que eu chame um consultor pra fechar os detalhes?")
    _enviar(c, conta_id, conversa_id, canal, destino, corpo)
