"""Os termos da clínica: os textos (da clínica ou o padrão do Zaq), o aceite e a cópia em PDF.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seção 13, ideia 3 ("termos
assinados no link: uso de dados, uso de imagem e termo do procedimento, aceitos no
celular com data, hora e cópia em PDF"). Migrações 411 (os aceites) e 417 (os modelos).
Telas: /painel/clinica/termos (web/painel_clinica_termos.py), o passo 3 do link da ficha
(web/ficha_publica.py) e o PDF de cada aceite.

  - USO DE DADOS (lgpd) e USO DE IMAGEM: a clínica pode escrever o texto dela; sem ele,
    vale o padrão do Zaq (`PADRAO`). Os textos aceitam {clinica} e {paciente}.
  - PROCEDIMENTO: um termo por tipo de atendimento (peeling, laser…). Se o próximo
    agendamento do paciente é desse tipo, o termo entra no passo 3 e conta pra ficha
    completa.
  - O ACEITE guarda o texto EXATO que a pessoa leu, a versão, o nome, o papel (paciente ou
    responsável), a hora, o IP e o navegador. Editar o modelo sobe a versão e não muda
    aceite nenhum.
"""
from __future__ import annotations

import html
import io
import logging

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.termos")

VERSAO_PADRAO = "zaq-2026-09-27"
IMAGEM = (("clinico", "Autorizo as fotos só para o meu tratamento: ficam no prontuário, e só os profissionais "
                      "de saúde da clínica veem."),
          ("divulgacao", "Autorizo as fotos para o meu tratamento e também para divulgação da clínica (redes "
                         "sociais e site). Posso retirar essa autorização quando quiser, pelo WhatsApp."),
          ("nao", "Não autorizo fotos."))
CHAVES = (("lgpd", "Uso de dados (LGPD)"), ("imagem", "Uso de imagem"), ("procedimento", "Termo do procedimento"))


def _padrao(chave: str, empresa: str, paciente: str, menor: bool) -> tuple[str, str]:
    if chave == "lgpd":
        de = f"os dados de {paciente}, de quem sou responsável legal," if menor else "os meus dados"
        return ("Uso de dados (LGPD)",
                f"Autorizo {empresa or 'a clínica'} a guardar e usar {de} pessoais e de saúde (cadastro, respostas "
                "da pré-consulta, prontuário, fotos clínicas e documentos) para o atendimento, para marcar e "
                "lembrar consultas pelo WhatsApp e para emitir nota fiscal e recibos. Os dados de saúde ficam no "
                "prontuário, que só os profissionais de saúde da clínica leem, e são guardados pelo prazo que as "
                "normas de saúde exigem. Posso pedir uma cópia, corrigir os dados ou tirar dúvidas pelo WhatsApp "
                "da clínica.")
    return ("Uso de imagem",
            "Fotos clínicas (antes, durante e depois do tratamento) ajudam o profissional a acompanhar o "
            "resultado. Elas ficam no prontuário, guardadas com sigilo.")


#: o ponto de partida da tela da clínica: com {clinica} e {paciente}, e "os dados de
#: {paciente}" — que vale pro adulto e pro menor ("Como responsável legal por Pedro: …
#: os dados de Pedro"), nunca "os meus dados" (seriam os do responsável)
MODELO_INICIAL = {
    "lgpd": ("Uso de dados (LGPD)",
             "Autorizo {clinica} a guardar e usar os dados pessoais e de saúde de {paciente} (cadastro, "
             "respostas da pré-consulta, prontuário, fotos clínicas e documentos) para o atendimento, para marcar "
             "e lembrar consultas pelo WhatsApp e para emitir nota fiscal e recibos. Os dados de saúde ficam no "
             "prontuário, que só os profissionais de saúde da clínica leem, e são guardados pelo prazo que as "
             "normas de saúde exigem. Posso pedir uma cópia, corrigir os dados ou tirar dúvidas pelo WhatsApp "
             "da clínica."),
    "imagem": ("Uso de imagem",
               "Fotos clínicas (antes, durante e depois do tratamento) ajudam o profissional a acompanhar o "
               "resultado. Elas ficam no prontuário de {paciente}, guardadas com sigilo."),
}
LIMITE = 8000


def _preencher(texto: str, empresa: str, paciente: str) -> str:
    return (texto or "").replace("{clinica}", empresa or "a clínica").replace("{paciente}", paciente or "o paciente")


# ------------------------------------------------------------------ os modelos

def modelos(c, conta_id: int) -> list[dict]:
    """Os modelos ativos da clínica (sem a 417: nenhum, vale o padrão)."""
    try:
        with c.transaction():
            rows = c.execute(
                """select m.id, m.chave, m.servico_id, s.nome, m.titulo, m.texto, m.versao, m.atualizado_em
                     from clinica_termos_modelos m
                     left join servicos_catalogo s on s.id = m.servico_id and s.conta_id = m.conta_id
                    where m.conta_id=%s and m.ativo order by m.chave, s.nome""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "chave": r[1], "servico_id": r[2], "servico": r[3] or "", "titulo": r[4], "texto": r[5],
             "versao": r[6], "quando": ca.local(r[7])} for r in rows]


def _modelo(c, conta_id: int, chave: str, servico_id: int | None = None) -> dict | None:
    return next((m for m in modelos(c, conta_id)
                 if m["chave"] == chave and (m["servico_id"] or None) == (servico_id or None)), None)


def servicos_com_termo(c, conta_id: int) -> set[int]:
    return {m["servico_id"] for m in modelos(c, conta_id) if m["chave"] == "procedimento" and m["servico_id"]}


def salvar_modelo(c, conta_id: int, chave: str, titulo: str, texto: str, *, servico_id: int | None = None,
                  membro_id: int | None = None) -> str | None:
    """Cria ou edita (editar sobe a versão; os aceites antigos ficam com o texto deles)."""
    if chave not in dict(CHAVES):
        return "Termo inválido."
    titulo = " ".join((titulo or "").split())[:120]
    # o <textarea> manda \r\n: vira \n, pra o PDF separar os parágrafos
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(titulo) < 3:
        return "Dê um título ao termo."
    if len(texto) < 30:
        return "O texto do termo está curto demais."
    if len(texto) > LIMITE:
        return f"O texto passou de {LIMITE} caracteres: encurte (nada é cortado sem avisar)."
    if chave == "procedimento":
        if not servico_id or not c.execute("select 1 from servicos_catalogo where id=%s and conta_id=%s",
                                           (servico_id, conta_id)).fetchone():
            return "Escolha o atendimento deste termo."
    else:
        servico_id = None
    atual = _modelo(c, conta_id, chave, servico_id)
    if atual and atual["titulo"] == titulo and atual["texto"] == texto:
        return None
    # a versão nunca repete: depois de "voltar ao padrão", o próximo texto é v3, não v1
    prox = c.execute("""select coalesce(max(versao), 0) + 1 from clinica_termos_modelos
                         where conta_id=%s and chave=%s and coalesce(servico_id, 0) = coalesce(%s, 0)""",
                     (conta_id, chave, servico_id)).fetchone()[0]
    try:
        with c.transaction():
            if atual:
                c.execute("""update clinica_termos_modelos set titulo=%s, texto=%s, versao=%s, atualizado_por=%s,
                                    atualizado_em=now() where id=%s and conta_id=%s""",
                          (titulo, texto, prox, membro_id, atual["id"], conta_id))
            else:
                c.execute("""insert into clinica_termos_modelos (conta_id, chave, servico_id, titulo, texto, versao,
                                                                 atualizado_por)
                             values (%s,%s,%s,%s,%s,%s,%s)""",
                          (conta_id, chave, servico_id, titulo, texto, prox, membro_id))
    except Exception as e:  # noqa: BLE001 — dois salvando o mesmo termo ao mesmo tempo
        if "ux_clinica_termos_modelos_vivo" in str(e):
            return "Alguém salvou este termo agora. Recarregue a página e confira."
        raise
    return None


def versoes_procedimento(c, conta_id: int) -> dict[int, str]:
    """{servico_id: 'clinica-vN'} dos termos de procedimento ativos."""
    return {m["servico_id"]: f"clinica-v{m['versao']}" for m in modelos(c, conta_id)
            if m["chave"] == "procedimento" and m["servico_id"]}


def desativar(c, conta_id: int, modelo_id: int) -> None:
    """Tira o modelo (uso de dados e imagem voltam pro texto padrão do Zaq)."""
    c.execute("update clinica_termos_modelos set ativo=false, atualizado_em=now() where id=%s and conta_id=%s",
              (modelo_id, conta_id))


# ------------------------------------------------------------------ o que o paciente lê

def textos(c, conta_id: int, empresa: str, paciente: str, menor: bool,
           servico_id: int | None = None) -> dict:
    """{'lgpd': {...}, 'imagem': {...}, 'procedimento': {...} | None}, cada um com titulo,
    texto (já preenchido) e versão."""
    out = {}
    for chave in ("lgpd", "imagem"):
        m = _modelo(c, conta_id, chave)
        if m:
            txt = _preencher(m["texto"], empresa, paciente)
            if menor and chave == "lgpd":
                txt = f"Como responsável legal por {paciente}: " + txt
            out[chave] = {"titulo": m["titulo"], "texto": txt, "versao": f"clinica-v{m['versao']}"}
        else:
            t, txt = _padrao(chave, empresa, paciente, menor)
            out[chave] = {"titulo": t, "texto": txt, "versao": VERSAO_PADRAO}
    out["procedimento"] = None
    if servico_id:
        m = _modelo(c, conta_id, "procedimento", servico_id)
        if m:
            txt = _preencher(m["texto"], empresa, paciente)
            if menor:
                txt = f"Como responsável legal por {paciente}: " + txt
            out["procedimento"] = {"titulo": m["titulo"], "texto": txt, "versao": f"clinica-v{m['versao']}",
                                   "servico_id": servico_id}
    return out


def gravar_aceite(c, conta_id: int, cliente_id: int, termo: str, t: dict, *, opcao: str | None, quem: str,
                  papel: str, ip: str, user_agent: str, evento_id: int | None = None) -> None:
    texto = t["texto"] + ("\n\n" + dict(IMAGEM)[opcao] if opcao else "")
    try:
        with c.transaction():
            c.execute("""insert into clinica_termos_aceites (conta_id, cliente_id, termo, opcao, titulo, texto, versao,
                                                             aceito_por_nome, papel, ip, user_agent, servico_id,
                                                             evento_id)
                         values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (conta_id, cliente_id, termo, opcao, t["titulo"], texto, t["versao"], quem, papel,
                       (ip or "")[:60], (user_agent or "")[:300], t.get("servico_id"), evento_id))
            return
    except Exception as e:  # noqa: BLE001 — base sem a 417 (sem servico_id/evento_id)
        if "does not exist" not in str(e) or termo == "procedimento":
            raise
    c.execute("""insert into clinica_termos_aceites (conta_id, cliente_id, termo, opcao, titulo, texto, versao,
                                                     aceito_por_nome, papel, ip, user_agent)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (conta_id, cliente_id, termo, opcao, t["titulo"], texto, t["versao"], quem, papel,
               (ip or "")[:60], (user_agent or "")[:300]))


def aceitos(c, conta_id: int, cliente_id: int) -> list[dict]:
    """O último aceite de cada termo (e de cada procedimento)."""
    try:
        with c.transaction():
            rows = c.execute(
                """select distinct on (termo, coalesce(servico_id, 0)) id, termo, opcao, titulo, aceito_por_nome,
                          papel, aceito_em, servico_id
                     from clinica_termos_aceites where conta_id=%s and cliente_id=%s
                    order by termo, coalesce(servico_id, 0), aceito_em desc""", (conta_id, cliente_id)).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 417
        try:
            with c.transaction():
                rows = [tuple(r) + (None,) for r in c.execute(
                    """select distinct on (termo) id, termo, opcao, titulo, aceito_por_nome, papel, aceito_em
                         from clinica_termos_aceites where conta_id=%s and cliente_id=%s
                        order by termo, aceito_em desc""", (conta_id, cliente_id)).fetchall()]
        except Exception:  # noqa: BLE001
            return []
    return [{"id": r[0], "termo": r[1], "opcao": r[2], "opcao_txt": dict(IMAGEM).get(r[2], "") if r[2] else "",
             "titulo": r[3], "por": r[4], "papel": r[5], "quando": ca.local(r[6]), "servico_id": r[7]}
            for r in rows]


# ------------------------------------------------------------------ a cópia em PDF

def pdf(c, conta_id: int, cliente_id: int, aceite_id: int) -> bytes | None:
    """O PDF do aceite: o texto exato, quem aceitou, quando, a versão. None se o aceite não
    é desta ficha."""
    r = c.execute(
        """select a.titulo, a.texto, a.versao, a.aceito_por_nome, a.papel, a.aceito_em, a.ip,
                  coalesce(p.nome, k.nome), coalesce(ct.nome, '')
             from clinica_termos_aceites a
             join clientes k on k.id = a.cliente_id and k.dono_id = a.conta_id
             left join pessoas p on p.id = k.pessoa_id
             left join contas ct on ct.id = a.conta_id
            where a.id=%s and a.conta_id=%s and a.cliente_id=%s""", (aceite_id, conta_id, cliente_id)).fetchone()
    if not r:
        return None
    titulo, texto, versao, quem, papel, quando, ip, paciente, empresa = r
    e = html.escape
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n")
    corpo = "".join("<p>" + e(par).replace("\n", "<br>") + "</p>" for par in texto.split("\n\n") if par.strip())
    q = ca.local(quando)
    doc = (f"<h2>{e(empresa)}</h2><h3>{e(titulo)}</h3>"
           f"<p><b>Paciente:</b> {e(paciente or '')}</p>{corpo}<hr>"
           f"<p><b>Aceito por:</b> {e(quem)}{' (responsável legal)' if papel == 'responsavel' else ''}<br>"
           f"<b>Em:</b> {q:%d/%m/%Y às %H:%M} (horário de Brasília), pelo link da ficha"
           f"{' · IP ' + e(ip) if ip else ''}<br><b>Versão do texto:</b> {e(versao)}</p>")
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        _log.warning("termos: sem o pymupdf, sem PDF")
        return None
    buf = io.BytesIO()
    story = pymupdf.Story(html=f"<body style='font-family:sans-serif;font-size:11pt'>{doc}</body>")
    writer = pymupdf.DocumentWriter(buf)
    pagina = pymupdf.paper_rect("a4")
    onde = pagina + (50, 50, -50, -50)
    mais = 1
    while mais:
        dev = writer.begin_page(pagina)
        mais, _ = story.place(onde)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()

