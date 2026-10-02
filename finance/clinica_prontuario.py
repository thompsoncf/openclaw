"""O prontuário (fase 2): a ficha clínica e a evolução de cada atendimento.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seções 02 (a tela), 03 (a
evolução pelos modelos), 04 (assinado não se apaga) e 15 (parte 2). Migração 423.
Tela: /painel/clinica/prontuario/{paciente} (web/painel_clinica_prontuario.py).

  - SÓ O PROFISSIONAL LIBERADO lê e escreve (finance/clinica_acesso_clinico: leitor e
    ler, que registra cada abertura). Nada aqui confere permissão: quem chama já passou
    pelo portão.
  - FICHA CLÍNICA: alergias, medicamentos em uso, problemas e antecedentes. Cada
    salvamento é uma versão nova (quem, quando); a atual é a última.
  - EVOLUÇÃO: um modelo por tipo de profissional, texto livre sempre possível. Rascunho:
    só o autor vê e edita. ASSINAR (assinatura simples — o certificado é a fase 4): hora
    do servidor, profissional, conselho e a impressão digital (sha256) do conteúdo; o
    banco recusa mudar depois. CORRIGIR é um ADENDO ligado ao original, também assinado.
    APAGAR não existe.
  - O "retorno em N dias" da evolução é o mesmo retorno da agenda (fase 6): vira o
    padrão do "o médico pediu retorno" ao finalizar o atendimento.
  - O agente do WhatsApp nunca importa este módulo (tests/test_clinica_prontuario.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.prontuario")

#: os modelos da seção 03 do desenho (campo, rótulo). O CID e o retorno são à parte.
MODELOS = {
    "dermatologia": ("Dermatologia", (("queixa", "Queixa e história"), ("exame", "Exame dermatológico"),
                                      ("hipotese", "Hipótese / diagnóstico"), ("conduta", "Conduta"))),
    "fisio": ("Fisioterapia dermatofuncional", (("avaliacao", "Avaliação"),
                                                ("procedimento", "Procedimento realizado"),
                                                ("parametros", "Parâmetros (aparelho, intensidade, tempo)"),
                                                ("resposta", "Resposta"), ("orientacao", "Orientação"))),
    "procedimento": ("Procedimento", (("procedimento", "Procedimento"), ("regiao", "Região"),
                                      ("produto_lote", "Produto e lote"), ("quantidade", "Quantidade"),
                                      ("intercorrencias", "Intercorrências"), ("orientacao", "Orientação pós"))),
    "livre": ("Texto livre", (("texto", "Evolução"),)),
    "adendo": ("Adendo", (("texto", "Adendo"),)),
}
LIMITE_CAMPO = 20000


# ------------------------------------------------------------------ a ficha clínica

def ficha_clinica(c, conta_id: int, cliente_id: int) -> dict:
    r = c.execute("""select alergias, medicamentos, problemas, profissional_nome, criado_em
                       from clinica_ficha_clinica where conta_id=%s and cliente_id=%s
                      order by criado_em desc, id desc limit 1""", (conta_id, cliente_id)).fetchone()
    if not r:
        return {"alergias": "", "medicamentos": "", "problemas": "", "por": "", "quando": None}
    return {"alergias": r[0], "medicamentos": r[1], "problemas": r[2], "por": r[3],
            "quando": ca.local(r[4]) if r[4] else None}


def salvar_ficha_clinica(c, conta_id: int, cliente_id: int, quem: dict, form: dict) -> bool:
    """Grava uma versão nova se algo mudou (True) — só aí vai pro registro."""
    novo = {k: _limpo(form.get(k))[:4000] for k in ("alergias", "medicamentos", "problemas")}
    atual = ficha_clinica(c, conta_id, cliente_id)
    if all(atual[k] == novo[k] for k in novo):
        return False
    c.execute("""insert into clinica_ficha_clinica (conta_id, cliente_id, alergias, medicamentos, problemas,
                                                    profissional_id, profissional_nome)
                 values (%s,%s,%s,%s,%s,%s,%s)""",
              (conta_id, cliente_id, novo["alergias"], novo["medicamentos"], novo["problemas"],
               quem.get("profissional_id"), (quem.get("nome") or "")[:120]))
    return True


def tem_alergia(c, conta_id: int, cliente_ids: list[int]) -> set[int]:
    """O AVISO de alergia (sem o texto), pra recepção e agenda. Base sem a 423: nenhum."""
    if not cliente_ids:
        return set()
    try:
        with c.transaction():
            rows = c.execute(
                """select distinct on (cliente_id) cliente_id, alergias from clinica_ficha_clinica
                    where conta_id=%s and cliente_id = any(%s) order by cliente_id, criado_em desc, id desc""",
                (conta_id, list(cliente_ids))).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    return {r[0] for r in rows if (r[1] or "").strip() and (r[1] or "").strip().lower() not in ("nenhuma", "não", "nao")}


# ------------------------------------------------------------------ a evolução

def _limpo(v) -> str:
    return str(v or "").replace("\x00", "").replace("\r\n", "\n").strip()[:LIMITE_CAMPO]


def _campos(modelo: str, form: dict) -> dict:
    return {k: _limpo(form.get(k)) for k, _r in MODELOS[modelo][1]}


def _retorno(txt) -> int | None:
    try:
        n = int(str(txt or "").strip())
    except ValueError:
        return None
    return n if 1 <= n <= 730 else None


def nova(c, conta_id: int, cliente_id: int, quem: dict, modelo: str, evento_id: int | None = None) -> int | None:
    """Um rascunho novo (ou o rascunho que o autor já tem pra este atendimento)."""
    if modelo not in MODELOS or modelo == "adendo":
        return None
    if evento_id:
        # só o atendimento DESTE paciente e DESTE profissional (a evolução é de quem atendeu)
        ok = c.execute("""select 1 from eventos_agenda where id=%s and conta_id=%s and cliente_id=%s
                            and profissional_id = any(%s)""",
                       (evento_id, conta_id, cliente_id, list(quem.get("cadastros") or [quem["profissional_id"]]))
                       ).fetchone()
        if not ok:
            evento_id = None
    # o rascunho que ele já tem (deste atendimento; ou, sem atendimento, um vazio) volta
    r = c.execute("""select id, campos from clinica_evolucoes
                      where conta_id=%s and cliente_id=%s and profissional_id=%s and status='rascunho'
                        and adendo_de is null and evento_id is not distinct from %s
                      order by id limit 1""",
                  (conta_id, cliente_id, quem["profissional_id"], evento_id)).fetchone()
    if r and (evento_id or not any(str(v).strip() for v in (r[1] or {}).values())):
        return r[0]
    try:
        with c.transaction():
            return c.execute("""insert into clinica_evolucoes (conta_id, cliente_id, evento_id, profissional_id, modelo)
                                values (%s,%s,%s,%s,%s) returning id""",
                             (conta_id, cliente_id, evento_id, quem["profissional_id"], modelo)).fetchone()[0]
    except Exception as e:  # noqa: BLE001 — clique duplo: o outro já criou
        if "ux_clinica_evolucoes_rascunho_do_evento" not in str(e):
            raise
    r = c.execute("""select id from clinica_evolucoes where conta_id=%s and evento_id=%s and profissional_id=%s
                      and status='rascunho' and adendo_de is null""",
                  (conta_id, evento_id, quem["profissional_id"])).fetchone()
    return r[0] if r else None


def descartar(c, conta_id: int, cliente_id: int, evo_id: int, quem: dict) -> bool:
    """O autor joga fora o rascunho (assinado, o banco recusa)."""
    r = c.execute("""delete from clinica_evolucoes where id=%s and conta_id=%s and cliente_id=%s
                      and profissional_id=%s and status='rascunho' returning id""",
                  (evo_id, conta_id, cliente_id, quem["profissional_id"])).fetchone()
    return bool(r)


def evolucao(c, conta_id: int, cliente_id: int, evo_id: int) -> dict | None:
    r = c.execute("""select id, evento_id, profissional_id, modelo, campos, cid, retorno_dias, adendo_de, status,
                            assinado_em, assinatura_hash, profissional_nome, conselho, criado_em, atualizado_em
                       from clinica_evolucoes where id=%s and conta_id=%s and cliente_id=%s""",
                  (evo_id, conta_id, cliente_id)).fetchone()
    return _evo(r) if r else None


def _evo(r) -> dict:
    campos = r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}")
    modelo = r[3]
    return {"id": r[0], "evento_id": r[1], "profissional_id": r[2], "modelo": modelo,
            "modelo_txt": MODELOS.get(modelo, (modelo,))[0], "campos": campos,
            "linhas": [(rot, campos.get(k, "")) for k, rot in MODELOS.get(modelo, ("", ()))[1] if campos.get(k)],
            "cid": r[5] or "", "retorno_dias": r[6], "adendo_de": r[7], "status": r[8],
            "assinado_em": ca.local(r[9]) if r[9] else None, "hash": r[10] or "", "prof": r[11] or "",
            "conselho": r[12] or "", "criado_em": ca.local(r[13]), "atualizado_em": ca.local(r[14])}


def salvar(c, conta_id: int, cliente_id: int, evo_id: int, quem: dict, form: dict) -> str | None:
    """O rascunho (o autor, só enquanto é rascunho). O banco também recusa mudar assinada."""
    e = evolucao(c, conta_id, cliente_id, evo_id)
    if not e or e["profissional_id"] != quem["profissional_id"]:
        return "Evolução não encontrada."
    if e["status"] != "rascunho":
        return "Evolução assinada não muda: escreva um adendo."
    modelo = form.get("modelo") if form.get("modelo") in MODELOS and form.get("modelo") != "adendo" else e["modelo"]
    if e["adendo_de"]:
        modelo = "adendo"
    if modelo != e["modelo"]:
        # trocar o modelo não perde o que já estava escrito: vai pro primeiro campo do novo
        novos = {k for k, _r in MODELOS[modelo][1]}
        levar = [f"{rot}: {_limpo(form.get(k) or e['campos'].get(k))}" for k, rot in MODELOS[e["modelo"]][1]
                 if k not in novos and _limpo(form.get(k) or e["campos"].get(k))]
        campos = _campos(modelo, {**e["campos"], **form})
        if levar:
            k0 = MODELOS[modelo][1][0][0]
            campos[k0] = "\n\n".join([x for x in (campos[k0],) if x] + levar)[:LIMITE_CAMPO]
    else:
        campos = _campos(modelo, form)
    c.execute("""update clinica_evolucoes set modelo=%s, campos=%s, cid=%s, retorno_dias=%s, atualizado_em=now()
                  where id=%s and conta_id=%s and status='rascunho'""",
              (modelo, json.dumps(campos, ensure_ascii=False), " ".join(str(form.get("cid") or "").split())[:20] or None,
               _retorno(form.get("retorno_dias")), evo_id, conta_id))
    return None


def _impressao(e: dict, cliente_id: int, assinado_em: datetime, conselho: str, nome: str) -> str:
    """A impressão digital do que foi assinado: qualquer mudança no banco muda o hash."""
    corpo = {"cliente_id": cliente_id, "evento_id": e["evento_id"], "profissional_id": e["profissional_id"],
             "modelo": e["modelo"], "campos": e["campos"], "cid": e["cid"] or "", "retorno_dias": e["retorno_dias"],
             "adendo_de": e["adendo_de"], "conselho": conselho, "profissional_nome": nome,
             "assinado_em": assinado_em.astimezone(timezone.utc).isoformat(timespec="microseconds")}
    return hashlib.sha256(json.dumps(corpo, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def assinar(c, conta_id: int, cliente_id: int, evo_id: int, quem: dict, agora: datetime) -> str | None:
    e = evolucao(c, conta_id, cliente_id, evo_id)
    if not e or e["profissional_id"] != quem["profissional_id"]:
        return "Evolução não encontrada."
    if e["status"] != "rascunho":
        return "Esta evolução já está assinada."
    if not any(v.strip() for v in e["campos"].values()):
        return "A evolução está vazia."
    r = c.execute("select nome, coalesce(conselho,'') from clinica_profissionais where id=%s and conta_id=%s",
                  (quem["profissional_id"], conta_id)).fetchone()
    nome, conselho = (r[0], r[1]) if r else (quem.get("nome") or "", "")
    if not conselho.strip():
        return "Sem conselho e número no cadastro, não dá pra assinar."
    quando = c.execute("select now()").fetchone()[0]           # a hora é a do servidor
    h = _impressao(e, cliente_id, quando, conselho, nome)
    c.execute("""update clinica_evolucoes set status='assinado', assinado_em=%s, assinatura_hash=%s,
                        profissional_nome=%s, conselho=%s, atualizado_em=%s
                  where id=%s and conta_id=%s and status='rascunho'""",
              (quando, h, nome, conselho, quando, evo_id, conta_id))
    return None


def integra(c, conta_id: int, cliente_id: int, evo_id: int) -> bool:
    """A evolução assinada ainda é a que foi assinada (o hash confere)."""
    r = c.execute("""select id, evento_id, profissional_id, modelo, campos, cid, retorno_dias, adendo_de, status,
                            assinado_em, assinatura_hash, profissional_nome, conselho, criado_em, atualizado_em
                       from clinica_evolucoes where id=%s and conta_id=%s and cliente_id=%s""",
                  (evo_id, conta_id, cliente_id)).fetchone()
    if not r or r[8] != "assinado" or not r[9]:
        return False
    return _confere(r, cliente_id)


def _confere(r, cliente_id: int) -> bool:
    """A linha (como veio do banco) ainda é a que foi assinada."""
    if r[8] != "assinado" or not r[9]:
        return False
    e = _evo(r[:15])
    return _impressao(e, cliente_id, r[9], e["conselho"], r[11] or "") == e["hash"]


def pdf_evolucao(c, conta_id: int, cliente_id: int, e: dict, assinatura: dict) -> bytes | None:
    """O PDF da evolução assinada que o certificado assina (fase 4): o que foi escrito,
    quem, quando e o código da assinatura simples (liga o PDF à linha do banco)."""
    import html
    from finance import clinica_documentos as cdoc
    r = c.execute("""select coalesce(ct.nome,''), coalesce(p.nome, k.nome, '') from contas ct
                       left join clientes k on k.id=%s and k.dono_id = ct.id
                       left join pessoas p on p.id = k.pessoa_id
                      where ct.id=%s""", (cliente_id, conta_id)).fetchone()
    empresa, paciente = (r[0], r[1]) if r else ("", "")
    x = html.escape
    linhas = "".join(f"<p><b>{x(rot)}</b><br>{x(txt).replace(chr(10), '<br>')}</p>" for rot, txt in e["linhas"])
    extra = "".join(f"<p><b>{x(a)}:</b> {x(str(b))}</p>" for a, b in
                    (("CID", e["cid"]), ("Retorno", f"{e['retorno_dias']} dias" if e["retorno_dias"] else ""),
                     ("Adendo da evolução", f"#{e['adendo_de']}" if e["adendo_de"] else "")) if b)
    q = e["assinado_em"]
    return cdoc.render_pdf([
        f"<h2>{x(empresa)}</h2><h3>Evolução · {x(e['modelo_txt'])}</h3>"
        f"<p><b>Paciente:</b> {x(paciente)} · <b>Data:</b> {q:%d/%m/%Y às %H:%M}</p>{linhas}{extra}<hr>"
        f"<p>{x(e['prof'])} · {x(e['conselho'])} · código {x(e['hash'][:16])}<br>Assinado digitalmente (ICP-Brasil) "
        f"por <b>{x(assinatura['titular'])}</b> em {ca.local(assinatura['quando']):%d/%m/%Y às %H:%M}</p>"])


def adendo(c, conta_id: int, cliente_id: int, evo_id: int, quem: dict, texto: str, agora: datetime) -> str | None:
    """A correção de uma evolução assinada: ligada a ela, assinada na hora."""
    e = evolucao(c, conta_id, cliente_id, evo_id)
    if not e or e["status"] != "assinado" or e["adendo_de"]:
        return "Só se escreve adendo a uma evolução assinada."
    texto = _limpo(texto)
    if not texto:
        return "Escreva o adendo."
    novo = c.execute("""insert into clinica_evolucoes (conta_id, cliente_id, evento_id, profissional_id, modelo, campos,
                                                       adendo_de)
                        values (%s,%s,%s,%s,'adendo',%s,%s) returning id""",
                     (conta_id, cliente_id, e["evento_id"], quem["profissional_id"],
                      json.dumps({"texto": texto}, ensure_ascii=False), evo_id)).fetchone()[0]
    return assinar(c, conta_id, cliente_id, novo, quem, agora)


def historia(c, conta_id: int, cliente_id: int, quem: dict) -> list[dict]:
    """As evoluções do paciente, da mais nova pra mais antiga, com os adendos embaixo de
    cada uma. Rascunho só aparece pro autor."""
    rows = c.execute(
        """select v.id, v.evento_id, v.profissional_id, v.modelo, v.campos, v.cid, v.retorno_dias, v.adendo_de,
                  v.status, v.assinado_em, v.assinatura_hash, v.profissional_nome, v.conselho, v.criado_em,
                  v.atualizado_em, e.inicio, s.nome, pr.nome
             from clinica_evolucoes v
             left join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
             left join servicos_catalogo s on s.id = e.servico_id and s.conta_id = e.conta_id
             left join clinica_profissionais pr on pr.id = v.profissional_id and pr.conta_id = v.conta_id
            where v.conta_id=%s and v.cliente_id=%s and (v.status='assinado' or v.profissional_id=%s)
            order by coalesce(e.inicio, v.criado_em) desc, v.id desc""",
        (conta_id, cliente_id, quem["profissional_id"])).fetchall()
    evos, adendos = [], {}
    for r in rows:
        e = _evo(r[:15])
        e["integra"] = _confere(r, cliente_id) if e["status"] == "assinado" else None
        e["quando"] = ca.local(r[15]) if r[15] else e["criado_em"]
        e["tipo"] = r[16] or ""
        e["prof"] = e["prof"] or r[17] or ""
        e["meu_rascunho"] = e["status"] == "rascunho" and e["profissional_id"] == quem["profissional_id"]
        if e["adendo_de"]:
            adendos.setdefault(e["adendo_de"], []).append(e)
        else:
            evos.append(e)
    for e in evos:
        e["adendos"] = sorted(adendos.get(e["id"], []), key=lambda x: x["criado_em"])
    return evos


def rascunho_do_evento(c, conta_id: int, evento_id: int) -> dict | None:
    """Há evolução em rascunho neste atendimento, do profissional dele? (o aviso antes de
    finalizar; sem o texto)"""
    try:
        with c.transaction():
            r = c.execute("""select v.id, v.profissional_id from clinica_evolucoes v
                               join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
                              where v.conta_id=%s and v.evento_id=%s and v.status='rascunho'
                                and v.profissional_id = e.profissional_id and v.adendo_de is null
                              order by v.id limit 1""",
                          (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001 — base sem a 423
        return None
    return {"id": r[0], "profissional_id": r[1]} if r else None


def retorno_da_evolucao(c, conta_id: int, evento_id: int) -> int | None:
    """O retorno que o profissional escreveu na evolução assinada (o padrão ao finalizar)."""
    try:
        with c.transaction():
            r = c.execute("""select v.retorno_dias from clinica_evolucoes v
                               join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
                              where v.conta_id=%s and v.evento_id=%s and v.status='assinado'
                                and v.retorno_dias is not null and v.adendo_de is null
                                and v.profissional_id = e.profissional_id
                              order by v.assinado_em desc limit 1""",
                          (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return r[0] if r else None


def retorno_depois_de_finalizar(c, conta_id: int, cliente_id: int, evo_id: int) -> None:
    """A recepção finalizou antes de o profissional assinar: o retorno da evolução vira o
    retorno da agenda agora (se ninguém já pediu um pra este atendimento)."""
    e = evolucao(c, conta_id, cliente_id, evo_id)
    if not e or not e["evento_id"] or not e["retorno_dias"] or e["adendo_de"]:
        return
    ev = ca.evento(c, conta_id, e["evento_id"])
    if not ev or ev["situacao"] != "finalizado" or ev["profissional_id"] != e["profissional_id"]:
        return
    from finance import clinica_pacotes as ckp
    if ckp.agendar_retorno(c, conta_id, ev, e["retorno_dias"]):
        try:
            with c.transaction():
                # a recepção finalizou sem retorno e o card foi pra Concluído: agora há um
                ca.card_do_retorno(c, conta_id, ev.get("lead"))
        except Exception:  # noqa: BLE001 — assinar é o pedido; o card é consequência
            _log.warning("prontuário: card do retorno não andou (evento %s)", e["evento_id"], exc_info=True)
