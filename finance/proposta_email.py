"""Mandar a proposta pro cliente por e-mail — pela caixa certa.

POR QUAL CAIXA SAI, E POR QUÊ

O Zaq já mandava e-mail em dois casos, e eles seguem regras OPOSTAS de propósito:

    convite de equipe, link de acesso  →  remetente do Zaq, caixa da empresa
                                          como reserva. É e-mail de SISTEMA:
                                          ninguém responde, só precisa chegar.
    mensagem pra lead                  →  caixa da EMPRESA, pra resposta cair
                                          no inbox dela.

A proposta é o segundo caso, e com força: o cliente vai apertar Responder. Saindo
do endereço do Zaq, a resposta dele morre numa caixa que a empresa não lê — e o
negócio morre junto, calado. Então a ordem aqui é o INVERSO da do convite:

    1. a caixa própria da empresa (canais_config), quando existe;
    2. reserva: o remetente do Zaq, assinado com o nome dela e com Reply-To
       apontando pro e-mail do cadastro. A resposta ainda chega no lugar certo.

VAI O LINK, NÃO UM PDF

O e-mail leva o link da proposta — a mesma página que o cliente lê e onde ele
APROVA E ASSINA. PDF anexo seria cópia morta: ele leria e depois teria que achar
o link de novo pra fechar. E envelhece: corrigir um valor e reabrir o link mostra
o valor certo; o PDF de ontem continua dizendo o preço de ontem.

O LAYOUT É DA EMPRESA

`email_sender._layout` é de e-mail de SISTEMA — verde-Zaq no topo e "você recebeu
porque tem uma conta no Zaq" no rodapé. Isso vai pra quem TEM conta. O cliente do
buffet não tem: pra ele, quem está mandando é a Prime Eventos. Layout próprio, com
o nome, o telefone e o e-mail da empresa.
"""
from __future__ import annotations

import html as _html
import logging

_log = logging.getLogger("openclaw.proposta_email")

# O texto que abre a tela de envio. Editável a cada envio — quando todo mundo
# apagar e escrever a mesma coisa, aí se sabe qual é o texto certo pra virar
# padrão configurável da empresa.
# O documento tem nome diferente conforme o nicho, e o e-mail vai pro CLIENTE —
# chamar de "orçamento" a proposta de mensalidade de uma empresa de tecnologia
# soaria de outro negócio. Mesma palavra que a tela usa em cada modo.
_DOC = {"evento": "orçamento", "recorrente": "proposta"}

TEXTO_PADRAO = (
    "Oi, {cliente}!\n\n"
    "Segue {o_doc} que preparei pra você. É só abrir no link abaixo — dá pra ler "
    "tudo e aprovar por ali mesmo.\n\n"
    "Qualquer dúvida, é só responder este e-mail."
)


def doc(modo: str | None) -> str:
    return _DOC.get(modo or "", _DOC["recorrente"])


def texto_padrao(cliente: str | None, modo: str | None = None) -> str:
    """A mensagem sugerida. Sem nome do cliente vira um "Oi!" — melhor que
    "Oi, !", e melhor que deixar em branco pra alguém mandar assim."""
    nome = (cliente or "").strip().split(" ")[0]
    o_doc = ("o orçamento" if doc(modo) == "orçamento" else "a proposta")
    txt = TEXTO_PADRAO.format(cliente=nome or "", o_doc=o_doc)
    return txt if nome else txt.replace("Oi, !", "Oi!")


def assunto_padrao(numero, empresa: str | None, modo: str | None = None) -> str:
    rot = doc(modo).capitalize()
    n = f"{rot} nº {numero}" if numero else rot
    emp = (empresa or "").strip()
    return f"{n} — {emp}" if emp else n

# Artigo e possessivo de cada documento — "contrato" entra aqui, não em `_DOC`
# (`_DOC` é só orçamento/proposta, por NICHO; contrato não depende de nicho,
# é outro documento inteiro, ver `doc_rotulo` em `montar`).
_GENERO = {"orçamento": ("o", "Seu"), "proposta": ("a", "Sua"), "contrato": ("o", "Seu")}


def montar(*, mensagem: str, link: str, numero, empresa: str, telefone: str = "",
           email_empresa: str = "", resumo: str = "", modo: str | None = None,
           doc_rotulo: str | None = None) -> tuple[str, str]:
    """(html, texto) do e-mail. `resumo` é a linha discreta embaixo do botão —
    tipo do evento, data, valor — pra quem só bate o olho na caixa de entrada
    saber do que se trata sem abrir o link.

    `doc_rotulo`, quando dado, MANDA sobre `modo` — é como "Mandar pra assinar"
    (o contrato) pede o mesmo e-mail que a proposta, com botão e assunto que
    dizem "contrato", não "proposta"/"orçamento"."""
    e = _html.escape
    corpo = "".join(
        f'<p style="margin:0 0 10px">{e(p)}</p>'
        for p in (mensagem or "").split("\n") if p.strip())
    rot_doc = doc_rotulo or doc(modo)
    artigo, possessivo = _GENERO.get(rot_doc, ("a", "Sua"))
    rot = rot_doc.capitalize()
    titulo = f"{rot} nº {numero}" if numero else f"{possessivo} {rot_doc}"
    rodape = " · ".join(x for x in (empresa, telefone, email_empresa) if x)
    html = f"""\
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;">
        <tr><td style="background:#0f766e;padding:20px 32px;">
          <span style="color:#fff;font-size:20px;font-weight:bold;">{e(empresa)}</span>
        </td></tr>
        <tr><td style="padding:28px 32px;">
          <h1 style="margin:0 0 14px;font-size:19px;color:#111;">{e(titulo)}</h1>
          <div style="font-size:15px;color:#333;line-height:1.6;">{corpo}</div>
          <p style="margin:18px 0 4px">
            <a href="{e(link)}" style="display:inline-block;background:#0f766e;color:#fff;
               border-radius:8px;padding:12px 22px;font-size:15px;font-weight:bold;
               text-decoration:none;">Ver {artigo} {e(rot_doc)}</a>
          </p>
          {f'<p style="margin:8px 0 0;font-size:13px;color:#777;">{e(resumo)}</p>' if resumo else ''}
          <p style="margin:16px 0 0;font-size:12px;color:#999;word-break:break-all;">
            Se o botão não abrir, use este endereço: {e(link)}</p>
        </td></tr>
        <tr><td style="padding:16px 32px;background:#fafafa;border-top:1px solid #eee;">
          <p style="margin:0;font-size:12px;color:#999;">{e(rodape)}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    texto = (mensagem or "").strip() + f"\n\nVer {artigo} {rot_doc}: {link}"
    if resumo:
        texto += f"\n{resumo}"
    if rodape:
        texto += f"\n\n{rodape}"
    return html, texto


def remetente(pool, conta_id: int, empresa_email: str = "") -> dict:
    """Por onde este e-mail vai sair — resolvido ANTES de mandar, pra a tela poder
    dizer. `{"caixa": "própria"|"zaq", "endereco": str, "reply_to": str}`.

    A tela mostra isso porque o mesmo botão se comporta diferente em duas
    empresas: a que tem caixa configurada e a que não tem. Sem dizer, o vendedor
    descobriria pelo endereço que o cliente reclamou que respondeu e ninguém viu.
    """
    try:
        from finance import email_inbound as ein
        propria = ein.remetente_conta(pool, conta_id)
    except Exception:  # noqa: BLE001 — conta sem o módulo de e-mail
        propria = None
    if propria:
        return {"caixa": "própria", "endereco": propria, "reply_to": ""}
    from finance import email_sender as es
    return {"caixa": "zaq", "endereco": es.remetente_configurado() or "",
            "reply_to": (empresa_email or "").strip()}


def enviar(pool, conta_id: int, *, destino: str, assunto: str, html: str,
           texto: str, empresa: str, reply_to: str = "") -> dict:
    """Manda, pela caixa da empresa primeiro. `{"ok", "remetente", "erro"}`.

    Best-effort de ponta a ponta: nada aqui levanta. Quem chama registra o
    resultado e devolve o link pra tela — falhar não pode deixar o vendedor sem
    saída, ele tem um cliente esperando.
    """
    from finance import email_inbound as ein
    from finance import email_sender as es

    try:
        propria = ein.remetente_conta(pool, conta_id)
    except Exception:  # noqa: BLE001
        propria = None

    if propria:
        try:
            if ein.enviar_conta(pool, conta_id, destino, assunto, html, texto,
                                from_nome=empresa):
                return {"ok": True, "remetente": propria, "erro": ""}
        except Exception as ex:  # noqa: BLE001
            _log.warning("proposta: caixa da conta %s falhou: %s: %s",
                         conta_id, type(ex).__name__, ex)
        _log.info("proposta: caixa própria (%s) não mandou — caindo pro Zaq", propria)

    # reserva. O Reply-To só existe aqui: saindo pela caixa da empresa, a resposta
    # já volta pra ela sozinha.
    de = es.remetente_configurado() or ""
    if es.enviar_email(destino, assunto, html, texto,
                       reply_to=(reply_to or None), from_nome=empresa):
        return {"ok": True, "remetente": de, "erro": ""}
    return {"ok": False, "remetente": propria or de,
            "erro": ("Não consegui enviar. Confira a caixa de e-mail da empresa "
                     "em Canais → E-mail." if propria else
                     "O envio de e-mail não está configurado.")}


# ------------------------------------------------------------------ o registro

def registrar(pool, conta_id: int, orcamento_id: int, *, destino: str,
              remetente_usado: str, ok: bool, erro: str = "", por: str = "",
              canal: str = "email") -> None:
    """Guarda a tentativa — inclusive a que falhou.

    Sem gravar a falha, uma caixa com senha errada deixaria a tela dizendo "nunca
    enviado": verdade e inútil. O que resolve é "tentou 3 vezes e falhou".

    `canal` já existia na tabela (migração 178) e só o e-mail escrevia. Agora a
    conversa do WhatsApp e o link copiado também passam por aqui — é esta tabela que
    o gatilho `orcamento_enviado` lê pra saber que a proposta saiu, e ela só serve
    se todos os canais escreverem nela. O default continua 'email' pra não mexer em
    quem já chamava."""
    try:
        with pool.connection() as c:
            c.execute(
                """insert into orcamento_envios (conta_id, orcamento_id, destino,
                       remetente, ok, erro, por, canal)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, orcamento_id, (destino or "")[:200], (remetente_usado or "")[:200],
                 bool(ok), (erro or "")[:400], (por or "")[:60], (canal or "email")[:20]))
            c.commit()
        # O CARD DO FUNIL, garantido AQUI e não em cada rota de envio.
        #
        # Depois que todo canal passou a registrar por esta função, ela virou o ponto
        # único por onde "a proposta saiu" passa — e é exatamente onde a garantia do
        # card pertence. Espalhá-la pelas quatro rotas de envio significaria que a
        # quinta, escrita daqui a três meses, esqueceria.
        #
        # Só no sucesso: tentativa que estourou não é proposta entregue, e não
        # justifica criar card nenhum.
        if ok:
            from finance import proposta_lead as _pl
            _pl.garantir(pool, conta_id, orcamento_id,
                         int(por) if str(por).isdigit() else None)
    except Exception as ex:  # noqa: BLE001 — o histórico é registro, não o envio
        _log.warning("proposta: não deu pra registrar o envio %s: %s: %s",
                     orcamento_id, type(ex).__name__, ex)


def historico(pool, conta_id: int, orcamento_id: int, limite: int = 5) -> list[dict]:
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select destino, remetente, ok, erro, criado_em, coalesce(canal,'email')
                     from orcamento_envios
                    where conta_id=%s and orcamento_id=%s
                    order by criado_em desc limit %s""",
                (conta_id, orcamento_id, limite)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"destino": r[0], "remetente": r[1], "ok": r[2], "erro": r[3],
             "quando": r[4], "canal": r[5], "canal_rot": CANAL_ROT.get(r[5], r[5])}
            for r in rows]


# Como cada canal se chama na tela. "Link copiado" é deliberadamente diferente de
# "E-mail" e "WhatsApp": nos dois primeiros o Zaq entregou, no terceiro ele só sabe
# que alguém pegou o link pra mandar. Chamar os três de "enviado" seria prometer uma
# certeza que só dois têm.
CANAL_ROT = {
    "email": "E-mail",
    "whatsapp": "WhatsApp",
    "link": "Link copiado",
}
