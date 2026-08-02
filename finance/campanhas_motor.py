"""Motor de disparo das campanhas de e-mail (Fase 2, etapa 3).

Roda no poller (a cada ~2min): pra cada campanha ATIVA, respeitando o limite/dia,
manda o passo devido de cada lead (D0 com IA, follow-ups por template), grava no
inbox (conversa de e-mail) e agenda o próximo passo. Para no lead que respondeu ou
descadastrou. Best-effort: nunca estoura o poller.

LGPD: todo e-mail leva link de descadastro (token assinado) + identificação da
empresa. WhatsApp entra só como link wa.me (nunca disparo frio).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import date, datetime, timedelta, timezone

from finance import servicos_catalogo as scat
from finance.email_sender import _app_url, enviar_email, remetente_configurado

_log = logging.getLogger("openclaw.campanhas")
_LOCK = 771144  # advisory lock (só um worker dispara por vez)
_MAX_PASS = 12  # teto de envios por passada (o limite/dia é o teto real)


# ------------------------------------------------------------ descadastro (token)

def _seg() -> bytes:
    return (os.environ.get("PORTAL_SECRET") or "zaq-descad-fallback").encode()


def descad_token(conta_id: int, email: str) -> str:
    raw = f"{conta_id}:{(email or '').lower()}"
    sig = hmac.new(_seg(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def descad_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, email, sig = s.rsplit(":", 2)
        good = hmac.new(_seg(), f"{conta_id}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), email
    except Exception:  # noqa: BLE001
        pass
    return None, None


# ------------------------------------------------------------ interesse (CTA)

def interesse_token(conta_id: int, prospeccao_id: int, campanha_id: int) -> str:
    raw = f"{conta_id}:{prospeccao_id}:{campanha_id}"
    sig = hmac.new(_seg(), ("int:" + raw).encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def interesse_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, pid, camp_id, sig = s.rsplit(":", 3)
        good = hmac.new(_seg(), f"int:{conta_id}:{pid}:{camp_id}".encode(),
                        hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), int(pid), int(camp_id)
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


# ------------------------------------------------------------ material (link rastreado)
# O botão de material (PDF/foto/vídeo/link) não pode apontar direto pro arquivo —
# senão a gente nunca sabe se o lead realmente abriu. Esse token faz o link passar
# pela rota /material antes de redirecionar, logando o evento "baixou".

def material_token(conta_id: int, prospeccao_id: int, campanha_id: int) -> str:
    raw = f"{conta_id}:{prospeccao_id}:{campanha_id}"
    sig = hmac.new(_seg(), ("mat:" + raw).encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def material_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, pid, camp_id, sig = s.rsplit(":", 3)
        good = hmac.new(_seg(), f"mat:{conta_id}:{pid}:{camp_id}".encode(),
                        hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), int(pid), int(camp_id)
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


# --------------------------------------------------- abertura (pixel de rastreio)

def abrir_token(conta_id: int, prospeccao_id: int, campanha_id: int) -> str:
    raw = f"{conta_id}:{prospeccao_id}:{campanha_id}"
    sig = hmac.new(_seg(), ("abr:" + raw).encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def abrir_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, pid, camp_id, sig = s.rsplit(":", 3)
        good = hmac.new(_seg(), f"abr:{conta_id}:{pid}:{camp_id}".encode(),
                        hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), int(pid), int(camp_id)
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


# ------------------------------------------------------------ conteúdo

def _fmt(txt: str, lead: dict) -> str:
    try:
        return (txt or "").format_map(_Safe({
            "empresa": lead.get("empresa") or "", "nome": lead.get("empresa") or "",
            "cidade": lead.get("cidade") or "", "uf": lead.get("uf") or "",
            "segmento": lead.get("segmento") or "sua área",
        }))
    except Exception:  # noqa: BLE001 (chave solta no texto do usuário)
        return txt or ""


class _Safe(dict):
    def __missing__(self, k):        # variável desconhecida no template → some
        return ""


def _email_ia(pool, conta_id: int, lead: dict) -> dict:
    """Agente escreve o 1º e-mail (assunto + corpo), único por lead. Fallback simples."""
    fallback = {
        "assunto": f"Uma ideia rápida pra {lead.get('empresa') or 'sua empresa'}",
        "corpo": (f"Oi, {lead.get('empresa') or ''}!\n\nVi que vocês atuam em "
                  f"{lead.get('segmento') or 'sua área'}"
                  f"{(' em ' + lead['cidade']) if lead.get('cidade') else ''}. "
                  "A gente ajuda empresas a atenderem melhor e organizarem o financeiro sem "
                  "aumentar a equipe.\n\nFaz sentido eu te mostrar em 5 min como ficaria aí?"),
    }
    try:
        from finance.agente import _conhecimento
        with pool.connection() as c:
            instr, faqs = _conhecimento(c, conta_id)
        catalogo = scat.listar(pool, conta_id)
        cat = "\n".join(f"- {s['nome']}: mensal R${s['mensal_centavos'] // 100}" for s in catalogo[:8])
        with pool.connection() as c:
            idn = _conta_identidade(c, conta_id)
        remetente = idn.get("empresa") or "nossa empresa"
        system = (
            "Você escreve e-mails de 1º contato (prospecção fria B2B) em português do Brasil, "
            "curtos (4-6 linhas), calorosos e diretos, focados no benefício. Sem enrolação, "
            "sem exagero. Termine com uma pergunta leve de call-to-action. Use SÓ o que está na "
            "base; não invente preço/promessa.\n"
            "REGRAS DA SAUDAÇÃO E ASSINATURA:\n"
            "- Comece cumprimentando a EMPRESA do lead pelo nome (ex.: 'Olá, equipe {nome do lead}!'). "
            "Nunca cumprimente com o nome de uma pessoa.\n"
            f"- Quem envia é a empresa '{remetente}'. NÃO escreva despedida nem assinatura "
            "('Atenciosamente', 'Equipe ...', nome, telefone) — a assinatura é adicionada automaticamente.\n"
            "Responda em JSON: {\"assunto\":\"...\",\"corpo\":\"...\"}\n\n"
            f"QUEM ENVIA (empresa '{remetente}'):\n{instr or '(nada)'}\n\nSERVIÇOS:\n{cat or '(sem catálogo)'}")
        pedir = (f"Escreva pra este lead:\nEmpresa (cumprimente por este nome): {lead.get('empresa')}\n"
                 f"Segmento: {lead.get('segmento') or '—'}\nCidade: {lead.get('cidade') or '—'}\n\n"
                 "Só o JSON.")
        from core.brain import Brain
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        import json
        import re
        txt = re.sub(r"^```json|^```|```$", "", txt, flags=re.M).strip()
        d = json.loads(txt)
        assunto = (d.get("assunto") or "").strip()
        corpo = (d.get("corpo") or "").strip()
        if assunto and corpo:
            return {"assunto": assunto[:200], "corpo": corpo[:4000]}
    except Exception as e:  # noqa: BLE001
        _log.info("campanha IA falhou conta=%s: %s", conta_id, e)
    return fallback


def _conta_identidade(c, conta_id: int) -> dict:
    """Identidade da conta pro rodapé do e-mail e pro 1º contato da prospecção:
    nome da EMPRESA (nome fantasia, não o do responsável), nome do RESPONSÁVEL,
    telefone, e o perfil de prospecção (Instagram + cargo de quem envia)."""
    row = c.execute(
        "select coalesce(nullif(trim(nome_fantasia),''), nome), nome, coalesce(telefone,''), "
        "coalesce(prospec_instagram,''), coalesce(nullif(trim(prospec_cargo),''),'CEO') "
        "from contas where id=%s", (conta_id,)).fetchone() or ("", "", "", "", "CEO")
    return {"empresa": (row[0] or "").strip(),
            "responsavel": (row[1] or "").strip(),
            "telefone": (row[2] or "").strip(),
            "instagram": (row[3] or "").strip(),
            "cargo": (row[4] or "CEO").strip()}


# despedidas que a IA às vezes cola no fim — a gente corta e usa a nossa assinatura
_DESPEDIDAS = ("atenciosamente", "abraços", "abraço", "att", "cordialmente",
               "saudações", "grande abraço", "obrigado", "obrigada")


def _tira_assinatura(corpo: str) -> str:
    """Corta uma despedida/assinatura que a IA tenha escrito no fim (a assinatura
    correta — Equipe/empresa/responsável/telefone — é montada em código)."""
    linhas = (corpo or "").split("\n")
    for i, l in enumerate(linhas):
        s = l.strip().lower().rstrip(",.:!")
        if s in _DESPEDIDAS:
            return "\n".join(linhas[:i]).rstrip()
    return (corpo or "").rstrip()


def _assinatura_texto(idn: dict) -> str:
    emp, resp, tel = idn.get("empresa") or "", idn.get("responsavel") or "", idn.get("telefone") or ""
    out = ["Atenciosamente,"]
    if emp:
        out.append(f"Equipe {emp}")
    if resp and resp != emp:
        out.append(resp)
    if tel:
        out.append(tel)
    return "\n".join(out)


def _assinatura_html(idn: dict) -> str:
    emp, resp, tel = _esc(idn.get("empresa") or ""), _esc(idn.get("responsavel") or ""), _esc(idn.get("telefone") or "")
    p = ['<p style="margin:20px 0 0;line-height:1.5;color:#222">Atenciosamente,']
    if emp:
        p.append(f'<br><b>Equipe {emp}</b>')
    if resp and resp != idn.get("empresa"):
        p.append(f'<br>{resp}')
    if tel:
        p.append(f'<br><span style="color:#555">📞 {tel}</span>')
    p.append('</p>')
    return "".join(p)


def _html(corpo: str, lead: dict, idn, link_descad: str,
          link_interesse: str = "", material_url: str = "",
          material_tipo: str = "", link_abrir: str = "") -> str:
    # idn pode ser o dict de identidade ou (compat) só o nome da empresa em string
    if isinstance(idn, str):
        idn = {"empresa": idn, "responsavel": "", "telefone": ""}
    idn = idn or {}
    conta_nome = idn.get("empresa") or ""
    paras = "".join(
        f'<p style="margin:0 0 12px">{_esc(p)}</p>'
        for p in _tira_assinatura(corpo).split("\n\n") if p.strip())
    cta = ""
    if link_interesse:
        cta = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 4px">'
            f'<tr><td style="border-radius:8px;background:#16a34a">'
            f'<a href="{link_interesse}" style="display:inline-block;padding:11px 22px;color:#fff;'
            'font-weight:bold;font-size:15px;text-decoration:none;border-radius:8px">✅ Tenho interesse</a>'
            '</td></tr></table>'
            '<p style="font-size:12px;color:#999;margin:2px 0 0">Clique acima e a gente te manda o material '
            'na hora.</p>')
    # botão do material (PDF/foto subidos ou link/vídeo colado) — só no e-mail pós-interesse
    mat = ""
    if material_url:
        _rot = {"pdf": "📄 Abrir PDF", "video": "🎬 Assistir ao vídeo",
                "foto": "🖼 Ver imagem"}.get((material_tipo or "").lower(), "🔗 Ver material")
        _href = _esc(material_url).replace('"', "%22")
        mat = ('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:16px 0 4px">'
               '<tr><td style="border-radius:8px;background:#16a34a">'
               f'<a href="{_href}" style="display:inline-block;padding:11px 22px;color:#fff;'
               f'font-weight:bold;font-size:15px;text-decoration:none;border-radius:8px">{_rot}</a>'
               '</td></tr></table>')
    wa = ""
    num = "".join(ch for ch in (lead.get("whatsapp") or "") if ch.isdigit())
    if num:
        wa = (f'<p style="margin:14px 0 0"><a href="https://wa.me/{num}" '
              'style="color:#0f766e;font-weight:bold;text-decoration:none">💬 Falar no WhatsApp</a></p>')
    return (
        '<div style="font-family:system-ui,Arial,sans-serif;font-size:15px;line-height:1.6;color:#222">'
        f'{paras}{cta}{mat}{_assinatura_html(idn)}{wa}'
        f'<hr style="border:0;border-top:1px solid #eee;margin:20px 0 10px">'
        f'<p style="font-size:12px;color:#999;margin:0">{_esc(conta_nome or "")} · Se não quiser mais '
        f'receber, <a href="{link_descad}" style="color:#888">descadastrar</a>.</p>'
        # pixel de rastreio de abertura (1x1) — best-effort; alguns clientes bloqueiam imagens
        + (f'<img src="{link_abrir}" width="1" height="1" alt="" '
           'style="display:none;width:1px;height:1px">' if link_abrir else '')
        + '</div>')


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------ disparo

def _agora():
    return datetime.now(timezone.utc)


def enviar_pendentes(pool) -> int:
    """Uma passada do motor. Best-effort. E-mail e WhatsApp são canais paralelos e
    independentes — um roda mesmo que o outro não esteja configurado."""
    total = 0
    if remetente_configurado():
        try:
            total += _disparar(pool)
        except Exception as e:  # noqa: BLE001
            _log.info("campanhas.enviar_pendentes(email) falhou: %s: %s", type(e).__name__, e)
    try:
        total += _disparar_wa(pool)
    except Exception as e:  # noqa: BLE001
        _log.info("campanhas.enviar_pendentes(wa) falhou: %s: %s", type(e).__name__, e)
    try:
        total += _disparar_reengajamento(pool)
    except Exception as e:  # noqa: BLE001
        _log.info("campanhas.enviar_pendentes(reengajar) falhou: %s: %s", type(e).__name__, e)
    return total


def renovar_tokens_ig(pool) -> int:
    """Renova os tokens de longa duração do Instagram que estão ficando velhos (>45 dias)
    pra nunca expirarem (validade 60 dias). Best-effort — roda no poller."""
    from finance import meta_msg
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select conta_id, token from canais_config
                    where canal='instagram' and ativo and token like 'IGAA%'
                      and atualizado_em < now() - interval '45 days'""").fetchall()
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for (conta_id, tok) in rows:
        res = meta_msg.renovar_token_ig(tok)
        if res.get("ok") and res.get("token"):
            try:
                with pool.connection() as c:
                    c.execute("""update canais_config set token=%s, atualizado_em=now()
                                   where conta_id=%s and canal='instagram'""", (res["token"], conta_id))
                    c.commit()
                n += 1
            except Exception:  # noqa: BLE001
                pass
    return n


def _disparar(pool) -> int:
    enviados = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return 0
        try:
            with pool.connection() as c:
                camps = c.execute(
                    """select id, conta_id, nome, limite_dia, enviados_hoje, dia_contagem
                         from campanhas where status='ativa'""").fetchall()
            hoje = date.today()
            for (cid, conta_id, nome, limite, env_hoje, dia) in camps:
                if dia != hoje:
                    env_hoje = 0
                    with pool.connection() as c:
                        c.execute("update campanhas set enviados_hoje=0, dia_contagem=%s where id=%s",
                                  (hoje, cid))
                        c.commit()
                restante = max(0, (limite or 0) - (env_hoje or 0))
                if restante <= 0:
                    continue
                n = _disparar_campanha(pool, cid, conta_id, nome, min(restante, _MAX_PASS))
                enviados += n
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
            lockc.commit()
    return enviados


# ------------------------------------------------------------ disparo WhatsApp

_LOCK_WA = 771145  # lock separado do e-mail (canais correm em paralelo)


def _so_dig(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _melhor_de_lista(decisor_telefones) -> str | None:
    """Do jsonb decisor_telefones, pega o ⭐ mais provável; senão o 1º com número."""
    provavel, primeiro = None, None
    for t in (decisor_telefones or []):
        if not isinstance(t, dict):
            continue
        f = (t.get("formatado") or "").strip()
        if len(_so_dig(f)) < 10:
            continue
        primeiro = primeiro or f
        if t.get("provavel"):
            provavel = provavel or f
    return provavel or primeiro


def _numero_alvo_wa(c, conta_id, pros) -> tuple[str | None, str]:
    """Escolhe o número do WhatsApp do lead priorizando o DECISOR (opção A):
    1) se já tem os telefones do decisor salvos → usa o ⭐ dele;
    2) senão, se tem CNPJ → busca o decisor na Credify AGORA, salva e usa o ⭐;
    3) senão → cai pro WhatsApp/telefone geral já captado.
    Devolve (numero|None, origem)."""
    pid, empresa, cnpj, whatsapp, telefone, dec_tels = pros
    n = _melhor_de_lista(dec_tels)
    if n:
        return n, "decisor"
    cnpj_d = _so_dig(cnpj)
    if len(cnpj_d) == 14:
        try:
            from finance import credify
            r = credify.decisor_com_telefone(cnpj_d)
        except Exception:  # noqa: BLE001
            r = {"ok": False}
        if r.get("ok"):
            import json
            tels = r.get("telefones") or []
            c.execute(
                """update prospeccao set decisor_nome=coalesce(decisor_nome,%s),
                       decisor_cargo=coalesce(decisor_cargo,%s), decisor_telefone=%s,
                       decisor_whatsapp=%s, decisor_telefones=%s::jsonb, decisor_em=now(),
                       atualizado_em=now() where id=%s and conta_id=%s""",
                (r.get("decisor_nome"), r.get("decisor_qualificacao"),
                 r.get("decisor_telefone"), bool(r.get("decisor_whatsapp")),
                 json.dumps(tels), pid, conta_id))
            c.commit()
            n = _melhor_de_lista(tels) or (r.get("decisor_telefone") or None)
            if n:
                return n, "decisor"
    geral = (whatsapp or telefone or "").strip()
    return (geral or None), ("empresa" if geral else "")


def _disparar_wa(pool) -> int:
    """Passada do WhatsApp: pra cada campanha ATIVA com wa_ativo e canal pronto,
    dispara o template de 1º contato pro melhor número (decisor primeiro), até o
    limite/dia. Dedup por campanha_alvos.wa_status."""
    from finance import prospec_convite
    env_sid = prospec_convite.sid_template()   # fallback global (env)
    from finance import whatsapp_out
    enviados = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK_WA,)).fetchone()[0]:
            return 0
        try:
            with pool.connection() as c:
                camps = c.execute(
                    """select id, conta_id, coalesce(limite_wa_dia,30),
                              coalesce(wa_enviados_hoje,0), wa_dia_contagem,
                              coalesce(wa_template_sid,'')
                         from campanhas where status='ativa' and coalesce(wa_ativo,false)""").fetchall()
            hoje = date.today()
            for (cid, conta_id, limite, env_hoje, dia, camp_sid) in camps:
                sid = (camp_sid or "").strip() or env_sid   # template da campanha > env
                if not sid:
                    continue                                # campanha sem template → pula
                if dia != hoje:
                    env_hoje = 0
                    with pool.connection() as c:
                        c.execute("update campanhas set wa_enviados_hoje=0, wa_dia_contagem=%s where id=%s",
                                  (hoje, cid))
                        c.commit()
                restante = max(0, (limite or 0) - (env_hoje or 0))
                if restante <= 0:
                    continue
                enviados += _disparar_wa_campanha(pool, cid, conta_id, sid,
                                                  min(restante, _MAX_PASS), whatsapp_out)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK_WA,))
            lockc.commit()
    return enviados


def _disparar_wa_campanha(pool, camp_id, conta_id, sid, teto, whatsapp_out) -> int:
    with pool.connection() as c:
        idn = _conta_identidade(c, conta_id)
        _mm = c.execute("select coalesce(wa_mmlite,false) from campanhas where id=%s",
                        (camp_id,)).fetchone()
        mmlite = bool(_mm and _mm[0])
        alvos = c.execute(
            """select a.id, p.id, p.empresa, p.cnpj, p.whatsapp, p.telefone, p.decisor_telefones
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s and a.wa_status is null
                  and a.status in ('fila','enviado') limit %s""", (camp_id, teto)).fetchall()
    feitos = 0
    for (aid, pid, empresa, cnpj, whatsapp, telefone, dec_tels) in alvos:
        # já respondeu por qualquer canal? então não incomoda no WhatsApp
        if _respondeu(pool, conta_id, pid):
            _wa_marca(pool, aid, "pulado")  # já respondeu → não envia (trava reenvio, não é "enviado")
            continue
        with pool.connection() as c:
            numero, _origem = _numero_alvo_wa(c, conta_id,
                                              (pid, empresa, cnpj, whatsapp, telefone, dec_tels))
        if not numero:
            _wa_marca(pool, aid, "sem_numero")
            continue
        variaveis = {"1": (idn.get("responsavel") or idn.get("empresa") or "nós"),
                     "2": (idn.get("cargo") or "CEO"),
                     "3": (idn.get("empresa") or "nós"),
                     "4": (empresa or "sua empresa")}
        with pool.connection() as c:
            res = whatsapp_out.enviar_template(c, conta_id, numero, sid, variaveis, mmlite=mmlite)
        if not res.get("ok"):
            detalhe = res.get("msg") or res.get("erro") or ""
            _wa_marca(pool, aid, "erro", erro_codigo=res.get("codigo"), erro_msg=detalhe)
            with pool.connection() as c:
                evento(c, camp_id, pid, "whatsapp", "erro", detalhe)
                c.commit()
            continue
        # prospecção fria = template de MARKETING. Grava o custo estimado (tarifa BR);
        # o webhook de status da Cloud API corrige com o pricing real quando chega.
        from finance import wa_precos
        _cat = "marketing"
        _wa_marca(pool, aid, "enviado", wa_sid=res.get("sid"),
                  categoria=_cat, custo=wa_precos.custo_brl(_cat, True))
        with pool.connection() as c:
            c.execute("update campanhas set wa_enviados_hoje=coalesce(wa_enviados_hoje,0)+1 where id=%s",
                      (camp_id,))
            evento(c, camp_id, pid, "whatsapp", "enviado")
            c.commit()
        feitos += 1
    return feitos


def evento(c, campanha_id, prospeccao_id, canal: str, evento: str, detalhe: str = "") -> None:
    """Registra um evento no histórico da campanha (append-only). Usa o cursor do
    chamador (mesma transação). Best-effort: nunca quebra o fluxo de envio."""
    if not (campanha_id and prospeccao_id):
        return
    try:
        c.execute("""insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento, detalhe)
                     values (%s,%s,%s,%s,%s)""",
                  (campanha_id, prospeccao_id, canal, evento, (detalhe or "")[:200]))
    except Exception:  # noqa: BLE001
        pass


def _canal_remetente(c, campanha_id) -> str:
    """Caixa de e-mail que a campanha usa pra enviar: 'email' (principal) ou 'email2'
    (secundária), conforme o remetente escolhido na campanha."""
    row = c.execute("select coalesce(remetente_slot,'principal') from campanhas where id=%s",
                    (campanha_id,)).fetchone()
    return "email2" if (row and row[0] == "secundario") else "email"


def _campanha_do_lead(c, prospeccao_id) -> int | None:
    """Campanha mais recente do lead (pra amarrar eventos de resposta/bounce/clique
    que não carregam o campanha_id na mão)."""
    row = c.execute("""select campanha_id from campanha_alvos where prospeccao_id=%s
                        order by id desc limit 1""", (prospeccao_id,)).fetchone()
    return row[0] if row else None


def engajou_lead(pool, prospeccao_id: int, motivo: str, alerta: bool = False) -> None:
    """Sinal de engajamento (abriu e-mail, leu no WhatsApp). Registra na timeline e
    esquenta a TEMPERATURA (frio→morno) — NUNCA mexe no estágio: abrir/ler não vira
    lead (só responder/clicar promove). Com alerta=True, avisa o vendedor por e-mail
    (usar só em sinais confiáveis, ex.: leitura no WhatsApp — abertura de e-mail é
    ruidosa por causa do proxy de imagens do Gmail). Best-effort."""
    conta_id = vendedor_id = None
    empresa = ""
    try:
        with pool.connection() as c:
            row = c.execute("""select conta_id, vendedor_id, coalesce(empresa,''),
                                      coalesce(temperatura,'frio')
                                 from prospeccao where id=%s""", (prospeccao_id,)).fetchone()
            if not row:
                return
            conta_id, vendedor_id, empresa, temp = row
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                         values (%s, null, 'engajamento', %s)""", (prospeccao_id, (motivo or "")[:400]))
            if temp == "frio":
                c.execute("update prospeccao set temperatura='morno', atualizado_em=now() where id=%s",
                          (prospeccao_id,))
            c.commit()
    except Exception:  # noqa: BLE001
        return
    if alerta and vendedor_id:
        try:
            with pool.connection() as c:
                mrow = c.execute("select coalesce(email,''), coalesce(nullif(nome,''),email) "
                                 "from membros where id=%s and conta_id=%s",
                                 (vendedor_id, conta_id)).fetchone()
            email = ((mrow[0] if mrow else "") or "").strip()
            if email and "@" in email:
                from finance import email_sender as es
                es.enviar_aviso(email, f"🔥 {empresa or 'Um lead'} {motivo}",
                                f"{empresa or 'Um lead'} {motivo}. Pode ser uma boa hora pra chamar.",
                                nome=(mrow[1] if mrow else None))
        except Exception:  # noqa: BLE001
            pass


_LOCK_REENG = 771146  # lock do passo de reengajamento (follow-up)


def _disparar_reengajamento(pool) -> int:
    """Passada do follow-up: campanhas ATIVAS com reengajar_ativo. Quem recebeu a
    sequência e não respondeu em N dias leva 1 toque pelo outro canal. Dispara 1x
    por lead (reengajado_em) e respeita os limites/dia da campanha."""
    enviados = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK_REENG,)).fetchone()[0]:
            return 0
        try:
            with pool.connection() as c:
                camps = c.execute(
                    """select id, conta_id, coalesce(reengajar_dias,3), coalesce(wa_ativo,false),
                              coalesce(wa_template_sid,''), limite_dia, coalesce(enviados_hoje,0),
                              dia_contagem
                         from campanhas
                        where status='ativa' and coalesce(reengajar_ativo,false)""").fetchall()
            hoje = date.today()
            for (cid, conta_id, dias, wa_ativo, camp_sid, limite, env_hoje, dia_cont) in camps:
                restante = max(0, (limite or 0) - (0 if dia_cont != hoje else (env_hoje or 0)))
                if restante <= 0:
                    continue
                enviados += _reengajar_campanha(pool, cid, conta_id, int(dias or 3), bool(wa_ativo),
                                                camp_sid, min(restante, _MAX_PASS))
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK_REENG,))
            lockc.commit()
    return enviados


def _reeng_marca(pool, aid) -> None:
    with pool.connection() as c:
        c.execute("update campanha_alvos set reengajado_em=now() where id=%s", (aid,))
        c.commit()


def _reengajar_campanha(pool, camp_id, conta_id, dias, wa_ativo, camp_sid, teto) -> int:
    from finance import prospec_convite, whatsapp_out
    sid = (camp_sid or "").strip() or prospec_convite.sid_template()
    with pool.connection() as c:
        idn = _conta_identidade(c, conta_id)
        canal_rem = _canal_remetente(c, camp_id)
        alvos = c.execute(
            """select a.id, p.id, coalesce(p.empresa,''), coalesce(p.email,''),
                      coalesce(p.whatsapp,''), coalesce(p.telefone,''), p.decisor_telefones,
                      coalesce(a.wa_status,'')
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s and a.reengajado_em is null
                  and a.status in ('enviado','concluido')
                  and a.ultima_msg_em is not null
                  and a.ultima_msg_em < now() - (%s || ' days')::interval
                  and coalesce(p.email_ok, true) is not false
                order by a.ultima_msg_em asc limit %s""",
            (camp_id, str(int(dias)), teto)).fetchall()
    conta_nome = idn.get("empresa") or ""
    feitos = 0
    for (aid, pid, empresa, email, whatsapp, telefone, dec_tels, wa_status) in alvos:
        if _respondeu(pool, conta_id, pid):        # respondeu por qualquer canal → não insiste
            _reeng_marca(pool, aid)
            continue
        numero = _melhor_de_lista(dec_tels) or (whatsapp or telefone or "").strip()
        usou_wa = False
        # 1) prioriza WhatsApp (canal novo, se ativo/pronto/número e ainda não mandado)
        if wa_ativo and sid and numero and not wa_status:
            variaveis = {"1": (idn.get("responsavel") or idn.get("empresa") or "nós"),
                         "2": (idn.get("cargo") or "CEO"),
                         "3": (idn.get("empresa") or "nós"),
                         "4": (empresa or "sua empresa")}
            with pool.connection() as c:
                res = whatsapp_out.enviar_template(c, conta_id, numero, sid, variaveis)
            if res.get("ok"):
                usou_wa = True
                with pool.connection() as c:
                    c.execute("""update campanha_alvos set wa_status='enviado', wa_em=now(),
                                   wa_sid=%s, reengajado_em=now() where id=%s""", (res.get("sid"), aid))
                    c.execute("update campanhas set wa_enviados_hoje=coalesce(wa_enviados_hoje,0)+1 where id=%s",
                              (camp_id,))
                    evento(c, camp_id, pid, "whatsapp", "enviado", "follow-up")
                    c.commit()
                feitos += 1
        # 2) senão, e-mail curto de reforço
        if not usou_wa:
            if not (email and "@" in email):
                _reeng_marca(pool, aid)           # sem canal disponível → não tenta de novo
                continue
            assunto = f"Uma última ideia pra {empresa or 'você'}"
            corpo = (f"Oi{(', ' + empresa) if empresa else ''}! Passei por aqui de novo porque acho que "
                     "faz sentido a gente trocar uma ideia rápida — em 5 minutinhos te mostro como pode "
                     "ajudar aí. Se não for o momento, é só me dizer que eu paro por aqui. 😊")
            link = _app_url() + "/descadastrar?t=" + descad_token(conta_id, email)
            link_int = _app_url() + "/tenho-interesse?t=" + interesse_token(conta_id, pid, camp_id)
            link_abr = _app_url() + "/e/abrir.gif?t=" + abrir_token(conta_id, pid, camp_id)
            link_unsub = _app_url() + "/descadastrar-oc?t=" + descad_token(conta_id, email)
            from finance import email_inbound as _ein
            ok = _ein.enviar_conta(pool, conta_id, email, assunto,
                                   _html(corpo, {"empresa": empresa, "whatsapp": whatsapp}, idn,
                                         link, link_int, link_abrir=link_abr),
                                   texto_alt=corpo + "\n\n" + _assinatura_texto(idn)
                                             + "\n\nTenho interesse: " + link_int,
                                   from_nome=(conta_nome or None), list_unsub=link_unsub, canal=canal_rem)
            with pool.connection() as c:
                if ok:
                    c.execute("update campanhas set enviados_hoje=coalesce(enviados_hoje,0)+1 where id=%s",
                              (camp_id,))
                    conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                                     (conta_id, pid)).fetchone()
                    conv_id = conv[0] if conv else c.execute(
                        """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
                           values (%s,%s,'email','aberta',now()) returning id""", (conta_id, pid)).fetchone()[0]
                    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                                 values (%s,'email','out','bot',%s)""", (conv_id, f"{assunto}\n\n{corpo}"[:8000]))
                    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
                    evento(c, camp_id, pid, "email", "enviado", "follow-up")
                c.execute("update campanha_alvos set reengajado_em=now() where id=%s", (aid,))
                c.commit()
            if ok:
                feitos += 1
    return feitos


def _wa_marca(pool, aid, status, wa_sid=None, erro_codigo=None, erro_msg=None,
              categoria=None, custo=None):
    with pool.connection() as c:
        if erro_codigo or erro_msg:
            c.execute("""update campanha_alvos set wa_status=%s, wa_em=now(),
                           wa_erro_codigo=%s, wa_erro_msg=%s where id=%s""",
                      (status, str(erro_codigo) if erro_codigo else None, erro_msg, aid))
        elif wa_sid:
            # estimativa de custo no envio (categoria/custo). coalesce: se o webhook de
            # status da Meta já tiver corrigido com o valor real, não sobrescreve.
            c.execute("""update campanha_alvos set wa_status=%s, wa_em=now(), wa_sid=%s,
                           wa_categoria=coalesce(wa_categoria,%s),
                           wa_custo=coalesce(wa_custo,%s) where id=%s""",
                      (status, wa_sid, categoria, custo, aid))
        else:
            c.execute("update campanha_alvos set wa_status=%s, wa_em=now() where id=%s", (status, aid))
        c.commit()


def _disparar_campanha(pool, camp_id, conta_id, camp_nome, teto) -> int:
    with pool.connection() as c:
        idn = _conta_identidade(c, conta_id)
        conta_nome = idn.get("empresa") or ""
        canal_rem = _canal_remetente(c, camp_id)
        passos = c.execute(
            "select ordem, dias_apos, assunto, corpo, usar_ia from campanha_passos where campanha_id=%s order by ordem",
            (camp_id,)).fetchall()
        if not passos:
            return 0
        pmap = {p[0]: {"dias": p[1], "assunto": p[2], "corpo": p[3], "ia": p[4]} for p in passos}
        alvos = c.execute(
            """select a.id, a.prospeccao_id, a.passo_atual, a.status,
                      p.empresa, p.segmento, p.cidade, p.uf, p.email, p.whatsapp
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s and a.status in ('fila','enviado')
                  and position('@' in coalesce(p.email,'')) > 1
                  and (a.proximo_envio_em is null or a.proximo_envio_em <= now())
                order by a.proximo_envio_em asc nulls first limit %s""", (camp_id, teto)).fetchall()
    feitos = 0
    for a in alvos:
        (aid, pid, passo_atual, status, empresa, segmento, cidade, uf, email, whatsapp) = a
        if passo_atual not in pmap:
            _finalizar(pool, aid)
            continue
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            # sem e-mail → o e-mail não tem o que fazer; o canal WhatsApp cuida dele.
            # (não marca 'erro' pra não tirar da fila do WhatsApp)
            continue
        # descadastrado? já respondeu? → não manda
        with pool.connection() as c:
            if c.execute("select 1 from descadastros where conta_id=%s and lower(email)=%s",
                         (conta_id, email)).fetchone():
                _marcar(pool, aid, "descadastrou")
                continue
        if _respondeu(pool, conta_id, pid):
            _marcar(pool, aid, "respondeu")
            continue
        lead = {"empresa": empresa, "segmento": segmento, "cidade": cidade, "uf": uf,
                "email": email, "whatsapp": whatsapp}
        passo = pmap[passo_atual]
        if passo["ia"]:
            m = _email_ia(pool, conta_id, lead)
            assunto, corpo = m["assunto"], m["corpo"]
        else:
            assunto = _fmt(passo["assunto"] or f"Contato · {camp_nome}", lead)
            corpo = _fmt(passo["corpo"] or "", lead)
        link = _app_url() + "/descadastrar?t=" + descad_token(conta_id, email)
        link_int = _app_url() + "/tenho-interesse?t=" + interesse_token(conta_id, pid, camp_id)
        link_abr = _app_url() + "/e/abrir.gif?t=" + abrir_token(conta_id, pid, camp_id)
        from finance import email_inbound as _ein
        corpo_txt = _tira_assinatura(corpo) + "\n\n" + _assinatura_texto(idn)
        link_unsub = _app_url() + "/descadastrar-oc?t=" + descad_token(conta_id, email)
        ok = _ein.enviar_conta(pool, conta_id, email, assunto,
                               _html(corpo, lead, idn, link, link_int, link_abrir=link_abr),
                               texto_alt=corpo_txt + "\n\nTenho interesse: " + link_int,
                               from_nome=(conta_nome or None), list_unsub=link_unsub, canal=canal_rem)
        if not ok:
            _marcar(pool, aid, "erro")
            continue
        _registrar_e_avancar(pool, conta_id, camp_id, aid, pid, passo_atual, pmap, assunto, corpo)
        feitos += 1
    return feitos


def _respondeu(pool, conta_id, prospeccao_id) -> bool:
    with pool.connection() as c:
        return bool(c.execute(
            """select 1 from mensagens m join conversas cv on cv.id=m.conversa_id
                where cv.conta_id=%s and cv.prospeccao_id=%s and cv.canal='email'
                  and m.direcao='in' limit 1""", (conta_id, prospeccao_id)).fetchone())


def _prox_passo(pmap, atual):
    """Devolve (proximo_ordem, dias_gap) ou (None, None) se acabou."""
    if (atual + 1) in pmap:
        gap = max(0, pmap[atual + 1]["dias"] - pmap[atual]["dias"])
        return atual + 1, gap
    return None, None


def _registrar_e_avancar(pool, conta_id, camp_id, aid, pid, passo_atual, pmap, assunto, corpo):
    prox, gap = _prox_passo(pmap, passo_atual)
    with pool.connection() as c:
        # grava no inbox (conversa de e-mail do lead)
        conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                         (conta_id, pid)).fetchone()
        conv_id = conv[0] if conv else c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
               values (%s,%s,'email','aberta',now()) returning id""", (conta_id, pid)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'email','out','bot',%s)""", (conv_id, f"{assunto}\n\n{corpo}"[:8000]))
        c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
        evento(c, camp_id, pid, "email", "enviado", f"D{passo_atual}: {assunto}")
        # avança o alvo
        if prox is None:
            c.execute("""update campanha_alvos set status='concluido', passo_atual=%s,
                           ultima_msg_em=now(), proximo_envio_em=null where id=%s""",
                      (passo_atual + 1, aid))
        else:
            c.execute("""update campanha_alvos set status='enviado', passo_atual=%s,
                           ultima_msg_em=now(), proximo_envio_em=now()+(%s || ' days')::interval where id=%s""",
                      (prox, str(gap), aid))
        c.execute("update campanhas set enviados_hoje=enviados_hoje+1, dia_contagem=%s where id=%s",
                  (date.today(), camp_id))
        c.commit()


def _marcar(pool, aid, status):
    with pool.connection() as c:
        c.execute("update campanha_alvos set status=%s, proximo_envio_em=null where id=%s", (status, aid))
        c.commit()


def _finalizar(pool, aid):
    _marcar(pool, aid, "concluido")


def registrar_interesse(pool, conta_id: int, prospeccao_id: int, campanha_id: int) -> dict:
    """Lead clicou 'Tenho interesse': para a sequência, marca QUENTE, manda o material
    da campanha por e-mail e deixa o agente IA assumir a conversa. Best-effort."""
    with pool.connection() as c:
        lead = c.execute(
            "select empresa, email, whatsapp from prospeccao where id=%s and conta_id=%s",
            (prospeccao_id, conta_id)).fetchone()
        if not lead:
            return {"ok": False}
        empresa, email, _wa = lead
        _mrow = (c.execute("""select coalesce(material,''), coalesce(material_tipo,'link')
                                 from campanhas where id=%s and conta_id=%s""",
                           (campanha_id, conta_id)).fetchone() or ["", "link"])
        material, material_tipo = _mrow[0], _mrow[1]
        idn = _conta_identidade(c, conta_id)
        conta_nome = idn.get("empresa") or ""
        canal_rem = _canal_remetente(c, campanha_id)
        # clique repetido / pré-carga do link (GET) → não reenvia o material nem re-loga
        _prev = c.execute("select status from campanha_alvos where campanha_id=%s and prospeccao_id=%s",
                          (campanha_id, prospeccao_id)).fetchone()
        repetido = bool(_prev and _prev[0] == "respondeu")
        # para a sequência deste lead
        c.execute("""update campanha_alvos set status='respondeu', proximo_envio_em=null
                       where campanha_id=%s and prospeccao_id=%s""", (campanha_id, prospeccao_id))
        # esquenta o lead e promove base → lead (entra no funil em Novo, se ainda era base)
        c.execute("""update prospeccao set temperatura='quente', estagio='lead',
                       status=case when estagio='base' then 'novo' else status end,
                       atualizado_em=now() where id=%s and conta_id=%s""",
                  (prospeccao_id, conta_id))
        # conversa de e-mail do lead
        conv = c.execute("select id, agente_ativo from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                         (conta_id, prospeccao_id)).fetchone()
        conv_id = conv[0] if conv else c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
               values (%s,%s,'email','aberta',now()) returning id""", (conta_id, prospeccao_id)).fetchone()[0]
        # registra o clique como entrada (o motor/agente entende que respondeu) — só na 1ª vez
        if not repetido:
            c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                         values (%s,'email','in','lead','[clicou: Tenho interesse]')""", (conv_id,))
            evento(c, campanha_id, prospeccao_id, "email", "respondeu", "Tenho interesse")
        master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                           (conta_id,)).fetchone()
        agente_on = bool(master and master[0])
        if agente_on:
            c.execute("update conversas set agente_ativo=true, status='aberta', ultima_msg_em=now() where id=%s",
                      (conv_id,))
        c.commit()
    # manda o material na hora (por e-mail), gravado como msg do bot — só na 1ª vez
    # (clique repetido/pré-carga não reenvia)
    enviado = False
    if email and "@" in email and not repetido:
        material_link = (_app_url() + "/material?t=" + material_token(conta_id, prospeccao_id, campanha_id)
                          + "&canal=email") if material else ""
        corpo = (f"Que bom, {empresa or 'tudo bem'}! 🎉\n\n"
                 + ("Preparei um material pra você conhecer melhor a gente — é só abrir no botão abaixo.\n\n"
                    if material else "")
                 + "Posso te mostrar em 2 minutinhos como fica aí? É só responder este e-mail. 😊")
        assunto = "Seu material — " + (conta_nome or "ZAQ")
        texto_alt = corpo + (f"\n\nMaterial: {material_link}" if material else "") + "\n\n" + _assinatura_texto(idn)
        try:
            from finance import email_inbound as _ein
            enviado = _ein.enviar_conta(pool, conta_id, email, assunto,
                                        _html(corpo, {"whatsapp": None}, idn,
                                              _app_url() + "/descadastrar?t=" + descad_token(conta_id, email),
                                              material_url=material_link, material_tipo=material_tipo),
                                        texto_alt=texto_alt,
                                        from_nome=(conta_nome or None), canal=canal_rem)
        except Exception:  # noqa: BLE001
            enviado = False
        if enviado:
            with pool.connection() as c:
                conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                                 (conta_id, prospeccao_id)).fetchone()
                if conv:
                    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                                 values (%s,'email','out','bot',%s)""", (conv[0], f"{assunto}\n\n{corpo}"[:8000]))
                    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv[0],))
                    evento(c, campanha_id, prospeccao_id, "email", "enviado", "material")
                    c.commit()
    return {"ok": True, "empresa": empresa, "material_enviado": enviado}


def registrar_descadastro(pool, conta_id: int, email: str, token: str = "") -> None:
    email = (email or "").strip().lower()
    with pool.connection() as c:
        c.execute("""insert into descadastros (conta_id, email, token) values (%s,%s,%s)
                     on conflict (conta_id, lower(email)) do nothing""", (conta_id, email, token[:120]))
        c.execute("""update campanha_alvos set status='descadastrou', proximo_envio_em=null
                       where prospeccao_id in (select id from prospeccao where conta_id=%s and lower(email)=%s)
                         and status in ('fila','enviado')""", (conta_id, email))
        c.commit()
