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
from finance import whatsapp_twilio as wa

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


def _add_bot_msg(c, conversa_id, texto, sid=None):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,'whatsapp','out','bot',%s,%s)""", (conversa_id, (texto or "")[:8000], sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))


def _numero_empresa(c, conta_id):
    r = c.execute("select identificador from canais_config where conta_id=%s and canal='whatsapp' and ativo",
                  (conta_id,)).fetchone()
    return r[0] if r else None


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
                      p.whatsapp, p.telefone, p.segmento, p.cidade, p.uf
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
        numero_dest = conv[4] or conv[5] or conv[2]
        remetente = _numero_empresa(c, conta_id)

        historico = "\n".join(
            ("Cliente: " if a == "lead" else ("Agente: " if a == "bot" else "Vendedor: ")) + (t or "")
            for (_d, a, t) in reversed(msgs))

        cat_txt = "\n".join(
            f"- {s['nome']} (slug {s['slug']}): setup R${s['setup_centavos']//100}, "
            f"mensal R${s['mensal_centavos']//100}" for s in catalogo) or "(sem catálogo)"
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
            return _orcamento(c, conta_id, conversa_id, conv, catalogo, d, remetente, numero_dest, resposta)

        # responde tudo (nunca escala/desliga automático)
        _enviar(c, conta_id, conversa_id, remetente, numero_dest, resposta or
                "Boa! Me conta um pouquinho mais que já te ajudo 😊")


def _enviar(c, conta_id, conversa_id, remetente, numero, texto):
    if not texto:
        return
    res = wa.enviar_texto(remetente, numero, texto)
    _add_bot_msg(c, conversa_id, texto, res.get("sid") if res.get("ok") else None)
    c.commit()


def _handoff(c, conta_id, conversa_id, prospeccao_id, remetente, numero, aviso):
    # desliga o bot nessa conversa e marca pendente pro humano assumir
    c.execute("update conversas set agente_ativo=false, status='pendente', ultima_msg_em=now() where id=%s",
              (conversa_id,))
    if aviso:
        res = wa.enviar_texto(remetente, numero, aviso)
        _add_bot_msg(c, conversa_id, aviso, res.get("sid") if res.get("ok") else None)
    c.commit()


def _orcamento(c, conta_id, conversa_id, conv, catalogo, d, remetente, numero, resposta):
    slugs_ok = {s["slug"]: s for s in catalogo}
    escolhidos = [slugs_ok[s] for s in (d.get("servicos") or []) if s in slugs_ok]
    if not escolhidos:
        # sem serviços válidos → responde o texto e para
        return _enviar(c, conta_id, conversa_id, remetente, numero, resposta or
                       "Me conta rapidinho o que você precisa que eu monto um orçamento 😊")
    setup = sum(s["setup_centavos"] for s in escolhidos)
    mensal = sum(s["mensal_centavos"] for s in escolhidos)
    itens = [{"nome": s["nome"], "setup": s["setup_centavos"], "mensal": s["mensal_centavos"]} for s in escolhidos]
    empresa = conv[3] or conv[2] or "Cliente"
    token = secrets.token_urlsafe(16)
    c.execute(
        """insert into orcamentos (conta_id, cliente, empresa, modulos, itens, escopo,
             setup_centavos, mensal_centavos, n_modulos, criado_por, token, status)
           values (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,'agente',%s,'rascunho')""",
        (conta_id, empresa, empresa, json.dumps([s["slug"] for s in escolhidos]),
         json.dumps(itens), (resposta or "")[:2000], setup, mensal, len(escolhidos), token))
    from finance.email_sender import _app_url
    link = _app_url() + "/proposta/" + token
    linhas = "\n".join(f"• {s['nome']}: R$ {s['mensal_centavos']//100}/mês" for s in escolhidos)
    corpo = (resposta + "\n\n" if resposta else "") + (
        f"📄 Montei um orçamento prévio pra você:\n{linhas}\n"
        + (f"Setup: R$ {setup//100}\n" if setup else "")
        + f"Total mensal: R$ {mensal//100}\n\n"
        f"Ver a proposta: {link}\n\n"
        "É um valor de referência — quer que eu chame um consultor pra fechar os detalhes?")
    _enviar(c, conta_id, conversa_id, remetente, numero, corpo)
