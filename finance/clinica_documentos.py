"""Documentos da consulta (prontuário, fase 5): receita, atestado, pedido de exame, laudo…

Desenho aprovado: docs/mockups/clinica_prontuario.html, seção 06 e 11.7. Migração 432.

  - O profissional liberado (finance/clinica_acesso_clinico) escreve e EMITE: vira
    imutável, com a hora do servidor, o conselho e a impressão digital (sha256). O PDF
    sai com o cabeçalho da clínica; a receita de controle especial em 2 vias.
  - Notificação de receita (amarela/azul): no talão de papel; aqui só o registro e o número.
  - Sem certificado digital (a fase 4), o PDF avisa pra imprimir e assinar à mão.
  - ENVIO: um clique de quem atendeu (ou da recepção, a pedido dele) manda o LINK DA
    FICHA, que abre com a data de nascimento; o documento fica lá 30 dias depois de
    enviado. A recepção vê só o TIPO do documento, nunca o conteúdo. O agente do
    WhatsApp nunca envia documento (tests/test_clinica_documentos.py trava).
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import logging
from datetime import datetime, timedelta, timezone

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.documentos")

VALE_DIAS = 30
TIPOS = {
    "receita": ("Receita", "Uso:\n1. "),
    "receita_controle": ("Receita de controle especial", "Uso:\n1. "),
    "notificacao": ("Notificação de receita (talão)", ""),
    "atestado": ("Atestado", "Atesto, para os devidos fins, que {paciente} esteve sob meus cuidados em {data}, "
                             "necessitando de afastamento de suas atividades por ___ dia(s)."),
    "comparecimento": ("Declaração de comparecimento", "Declaro que {paciente} compareceu a esta clínica em {data}, "
                                                       "das ___ às ___, para atendimento."),
    "acompanhante": ("Declaração de acompanhante", "Declaro que ___ acompanhou {paciente} em atendimento nesta "
                                                   "clínica em {data}, das ___ às ___."),
    "pedido_exame": ("Pedido de exame", "Solicito:\n1. "),
    "laudo": ("Laudo", ""),
    "orientacoes": ("Orientações", ""),
}


def _limpo(v, n: int = 20000) -> str:
    return str(v or "").replace("\x00", "").replace("\r\n", "\n").strip()[:n]


def novo(c, conta_id: int, cliente_id: int, quem: dict, tipo: str, paciente: str, agora: datetime,
         evento_id: int | None = None) -> int | None:
    if tipo not in TIPOS:
        return None
    if evento_id and not c.execute("select 1 from eventos_agenda where id=%s and conta_id=%s and cliente_id=%s",
                                   (evento_id, conta_id, cliente_id)).fetchone():
        evento_id = None
    titulo, modelo = TIPOS[tipo]
    corpo = modelo.replace("{paciente}", paciente or "o paciente").replace("{data}", f"{ca.local(agora):%d/%m/%Y}")
    return c.execute("""insert into clinica_documentos (conta_id, cliente_id, evento_id, profissional_id, tipo, titulo,
                                                        corpo)
                        values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                     (conta_id, cliente_id, evento_id, quem["profissional_id"], tipo, titulo, corpo)).fetchone()[0]


def _doc(r) -> dict:
    return {"id": r[0], "evento_id": r[1], "profissional_id": r[2], "tipo": r[3], "tipo_txt": TIPOS.get(r[3], (r[3],))[0],
            "titulo": r[4], "corpo": r[5], "numero_talao": r[6] or "", "status": r[7],
            "assinado_em": ca.local(r[8]) if r[8] else None, "hash": r[9] or "", "prof": r[10] or "",
            "conselho": r[11] or "", "enviado_em": ca.local(r[12]) if r[12] else None, "criado_em": ca.local(r[13]),
            "_assinado_cru": r[8]}


_COLS = """id, evento_id, profissional_id, tipo, titulo, corpo, numero_talao, status, assinado_em, assinatura_hash,
           profissional_nome, conselho, enviado_em, criado_em"""


def documento(c, conta_id: int, cliente_id: int, doc_id: int) -> dict | None:
    r = c.execute(f"select {_COLS} from clinica_documentos where id=%s and conta_id=%s and cliente_id=%s",
                  (doc_id, conta_id, cliente_id)).fetchone()
    return _doc(r) if r else None


def listar(c, conta_id: int, cliente_id: int, quem: dict | None = None) -> list[dict]:
    """Os emitidos (e os rascunhos do próprio autor, se `quem`)."""
    try:
        with c.transaction():
            rows = c.execute(
                f"""select {_COLS} from clinica_documentos
                     where conta_id=%s and cliente_id=%s and (status='assinado' or profissional_id=%s)
                     order by criado_em desc, id desc""",
                (conta_id, cliente_id, (quem or {}).get("profissional_id") or 0)).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 432
        return []
    return [_doc(r) for r in rows]


def emitidos_sem_conteudo(c, conta_id: int, cliente_id: int) -> list[dict]:
    """O que a RECEPÇÃO vê: tipo, data e se foi enviado. Nunca o texto."""
    try:
        with c.transaction():
            rows = c.execute(
                """select id, tipo, assinado_em, enviado_em from clinica_documentos
                    where conta_id=%s and cliente_id=%s and status='assinado' and tipo <> 'notificacao'
                    order by assinado_em desc limit 30""", (conta_id, cliente_id)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "tipo_txt": TIPOS.get(r[1], (r[1],))[0], "quando": ca.local(r[2]),
             "enviado_em": ca.local(r[3]) if r[3] else None} for r in rows]


def salvar(c, conta_id: int, cliente_id: int, doc_id: int, quem: dict, form: dict) -> str | None:
    d = documento(c, conta_id, cliente_id, doc_id)
    if not d or d["profissional_id"] != quem["profissional_id"]:
        return "Documento não encontrado."
    if d["status"] != "rascunho":
        return "Documento emitido não muda: emita outro."
    c.execute("""update clinica_documentos set titulo=%s, corpo=%s, numero_talao=%s, atualizado_em=now()
                  where id=%s and conta_id=%s and status='rascunho'""",
              (_limpo(form.get("titulo"), 120) or d["titulo"], _limpo(form.get("corpo")),
               _limpo(form.get("numero_talao"), 40) or None, doc_id, conta_id))
    return None


def _impressao(d: dict, cliente_id: int, assinado_em: datetime, nome: str, conselho: str) -> str:
    corpo = {"cliente_id": cliente_id, "tipo": d["tipo"], "titulo": d["titulo"], "corpo": d["corpo"],
             "numero_talao": d["numero_talao"], "profissional_id": d["profissional_id"], "profissional_nome": nome,
             "conselho": conselho, "assinado_em": assinado_em.astimezone(timezone.utc).isoformat(timespec="microseconds")}
    return hashlib.sha256(json.dumps(corpo, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def emitir(c, conta_id: int, cliente_id: int, doc_id: int, quem: dict) -> str | None:
    d = documento(c, conta_id, cliente_id, doc_id)
    if not d or d["profissional_id"] != quem["profissional_id"]:
        return "Documento não encontrado."
    if d["status"] != "rascunho":
        return "Este documento já foi emitido."
    if d["tipo"] == "notificacao":
        if not d["numero_talao"] or not d["corpo"]:
            return "Informe o número do talão e o medicamento."
    elif not d["corpo"] or "___" in d["corpo"]:
        return "Complete o texto do documento (troque os ___)."
    r = c.execute("select nome, coalesce(conselho,'') from clinica_profissionais where id=%s and conta_id=%s",
                  (quem["profissional_id"], conta_id)).fetchone()
    nome, conselho = (r[0], r[1]) if r else ("", "")
    if not conselho.strip():
        return "Sem conselho e número no cadastro, não dá pra emitir."
    quando = c.execute("select now()").fetchone()[0]
    c.execute("""update clinica_documentos set status='assinado', assinado_em=%s, assinatura_hash=%s,
                        profissional_nome=%s, conselho=%s, atualizado_em=%s
                  where id=%s and conta_id=%s and status='rascunho'""",
              (quando, _impressao(d, cliente_id, quando, nome, conselho), nome, conselho, quando, doc_id, conta_id))
    return None


def integro(d: dict, cliente_id: int) -> bool:
    if d["status"] != "assinado" or not d["_assinado_cru"]:
        return False
    return _impressao(d, cliente_id, d["_assinado_cru"], d["prof"], d["conselho"]) == d["hash"]


def marcar_enviados(c, conta_id: int, cliente_id: int, ids: list[int]) -> None:
    c.execute("""update clinica_documentos set enviado_em=now() where conta_id=%s and cliente_id=%s
                   and id = any(%s) and status='assinado' and tipo <> 'notificacao'""",
              (conta_id, cliente_id, list(ids)))


def no_link(c, conta_id: int, cliente_id: int, agora: datetime) -> list[dict]:
    """Os documentos que o PACIENTE vê no link da ficha: emitidos, enviados há até 30 dias."""
    try:
        with c.transaction():
            rows = c.execute(
                f"""select {_COLS} from clinica_documentos
                     where conta_id=%s and cliente_id=%s and status='assinado' and tipo <> 'notificacao'
                       and enviado_em >= %s order by assinado_em desc""",
                (conta_id, cliente_id, agora - timedelta(days=VALE_DIAS))).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_doc(r) for r in rows]


def pdf(c, conta_id: int, cliente_id: int, d: dict) -> bytes | None:
    if d["status"] != "assinado" or d["tipo"] == "notificacao":
        return None
    r = c.execute("""select coalesce(ct.nome,''), coalesce(p.nome, k.nome, '') from contas ct
                       left join clientes k on k.id=%s and k.dono_id = ct.id
                       left join pessoas p on p.id = k.pessoa_id
                      where ct.id=%s""", (cliente_id, conta_id)).fetchone()
    empresa, paciente = (r[0], r[1]) if r else ("", "")
    e = html.escape
    corpo = "".join("<p>" + e(par).replace("\n", "<br>") + "</p>" for par in d["corpo"].split("\n\n") if par.strip())
    q = d["assinado_em"]
    via = (["1ª via — farmácia", "2ª via — paciente"] if d["tipo"] == "receita_controle" else [""])
    paginas = []
    for v in via:
        paginas.append(
            f"<h2>{e(empresa)}</h2>{'<p><b>' + e(v) + '</b></p>' if v else ''}<h3>{e(d['titulo'])}</h3>"
            f"<p><b>Paciente:</b> {e(paciente)} · <b>Data:</b> {q:%d/%m/%Y}</p>{corpo}<hr>"
            f"<p>{e(d['prof'])} · {e(d['conselho'])}<br>Emitido pelo Zaq em {q:%d/%m/%Y às %H:%M} · código "
            f"{e(d['hash'][:16])}</p>"
            "<p><i>Documento sem certificado digital: imprima e assine à mão.</i></p><p>&nbsp;</p>"
            "<p>_______________________________________<br>assinatura</p>")
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        return None
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    pagina = pymupdf.paper_rect("a4")
    onde = pagina + (50, 50, -50, -50)
    for conteudo in paginas:
        story = pymupdf.Story(html=f"<body style='font-family:sans-serif;font-size:11pt'>{conteudo}</body>")
        mais = 1
        while mais:
            dev = writer.begin_page(pagina)
            mais, _ = story.place(onde)
            story.draw(dev)
            writer.end_page()
    writer.close()
    return buf.getvalue()


def texto_envio(paciente_primeiro: str, link: str) -> str:
    return (f"Oi{', ' + paciente_primeiro if paciente_primeiro else ''}! Aqui estão os documentos da consulta 😊\n\n"
            f"📄 {link}\n\nAbre com a data de nascimento e vale {VALE_DIAS} dias.")


def enviar(c, conta_id: int, cliente_id: int, ids: list[int], membro_id: int | None) -> str | None:
    """Manda o LINK DA FICHA pelo WhatsApp do paciente (ou do responsável) e marca os
    documentos como enviados. Erro (texto) ou None."""
    from finance import clinica_ficha_link as cfl
    from finance import clinica_pacientes as cpa
    if not cfl.ligado(c, conta_id):
        return "O link da ficha está desligado (Agenda › Link da ficha): ligue pra mandar documentos."
    validos = [d["id"] for d in emitidos_sem_conteudo(c, conta_id, cliente_id) if d["id"] in set(ids)]
    if not validos:
        return "Escolha os documentos."
    agora = datetime.now(timezone.utc)
    p = cpa.ficha(c, conta_id, cliente_id, agora)
    tok = cfl.token(c, conta_id, cliente_id) if p else None
    if not p or not tok:
        return "Paciente não encontrado."
    fone = p["fone"]
    if p["responsavel"] and not ca._digitos(fone):
        r = cpa.ficha(c, conta_id, p["responsavel"]["id"], agora)
        fone = r["fone"] if r else ""
    primeiro = "" if p["responsavel"] else cpa._primeiro(p["nome"]).capitalize()
    res = ca.enviar(c, conta_id, {"id": None, "lead": p["lead"], "fone": fone}, texto_envio(primeiro, cfl.link(tok)),
                    autor="humano", membro_id=membro_id)
    if not res.get("ok"):
        return "Não deu pra mandar: confira o celular da ficha."
    marcar_enviados(c, conta_id, cliente_id, validos)
    return None
