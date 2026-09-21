"""O PRÉ-CADASTRO LÊ O DOCUMENTO: a apólice que chegou no WhatsApp já vem lida.

POR QUE. A corretora não muda de hábito — o documento JÁ chega no número vinculado.
Medido na Liberal (conta 37) em 18/09/2026, nos 90 dias anteriores: 36 PDFs, entre
eles "PROPOSTA DENISE BARROS DE SOUSA SANTOS.pdf" e "Orçamento MARIA IRACI CORREIA
DE SANTANA.pdf", mandados pelo próprio corretor. Até aqui, escolher um deles na
janela custava baixar e ler na hora, e a lista mostrava só o nome do arquivo.

O MOLDE É O LEITOR DE EVENTOS (`finance/evento_leitor.py`, migração 198): roda
sozinho a cada mensagem que entra, tira o que dá, preenche só o que está vazio e
carimba de onde tirou. A diferença é o que cada um lê — o de eventos lê PALAVRA, o
de seguros lê o DOCUMENTO anexado.

O QUE ELE NÃO FAZ: cadastrar. Gravar sozinho transformaria erro de leitura em dado
errado no banco, e vigência lida errada é alerta que não dispara — o pior defeito
que a tela de Renovações pode ter. Ele deixa a conferência PRONTA; quem confirma é
gente, pelo mesmo formulário e pelo mesmo salvar de sempre.

OS DOIS PORTÕES, os mesmos que a tela já usa:

  * a conta tem que ser do nicho `seguros` (regra 6);
  * o remetente tem que estar LIBERADO (migração 289). Sem isso o leitor baixaria
    os onze boletos por 90 dias que o fornecedor manda pro mesmo número.

DE QUEBRA, ler é o que RESGATA o arquivo: o CDN do WhatsApp expira, e a leitura
sobe o PDF pro cofre. O prazo real de tudo isto é esse.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from finance import apolice_pdf as apdf
from finance import comprovantes as _cofre
from finance import raio_x_perfil as rxp

_log = logging.getLogger("apolice_leitor")

# Quantos PDFs uma passada olha. A passada roda por mensagem que chega, então o
# normal é achar um; o teto existe pro caso de o serviço ter ficado fora do ar e
# voltar com uma fila — melhor ler alguns e deixar o resto pra próxima do que
# segurar o webhook baixando dez arquivos.
_POR_VEZ = 3

# Além disto o CDN do WhatsApp quase certamente já apagou, e a tentativa é banda
# gasta pra receber 404.
_DIAS = 7


def _e_seguros(pool, conta_id: int) -> bool:
    try:
        return rxp.perfil_da_conta(pool, conta_id).get("chave") == "seguros"
    except Exception:  # noqa: BLE001
        return False


def pendentes(c, conta_id: int, conversa_id: int | None = None,
              limite: int = _POR_VEZ) -> list[dict]:
    """PDFs de remetente liberado que ainda não foram lidos.

    `conversa_id` recorta pra conversa que acabou de receber mensagem, que é o
    caminho normal. Sem ele vale a conta inteira, que é o caminho do reprocesso.
    """
    sql = """
        select m.id, coalesce(m.midia_meta->>'nome',''), m.midia_ref, m.midia_tipo
          from mensagens m
          join conversas cv on cv.id = m.conversa_id
         where cv.conta_id = %s
           and m.direcao = 'in'
           and m.midia_tipo = 'documento'
           and m.midia_ref is not null
           and (coalesce(m.midia_ref->>'mimetype','') like 'application/pdf%%'
                or lower(coalesce(m.midia_meta->>'nome','')) like '%%.pdf')
           and m.criado_em > now() - make_interval(days => %s)
           and exists (select 1 from apolice_remetentes r
                        where r.conta_id = cv.conta_id
                          and r.contato_ref = cv.contato_ref)
           and not exists (select 1 from apolice_lida l where l.mensagem_id = m.id)
    """
    args: list = [conta_id, _DIAS]
    if conversa_id is not None:
        sql += " and m.conversa_id = %s"
        args.append(conversa_id)
    sql += " order by m.criado_em desc limit %s"
    args.append(int(limite))
    return [{"mensagem_id": r[0], "nome": r[1] or "documento.pdf",
             "ref": r[2] or {}, "tipo": r[3] or "documento"}
            for r in c.execute(sql, tuple(args)).fetchall()]


def _guardar(conta_id: int, conteudo: bytes, nome: str) -> tuple[str, int]:
    """Sobe o PDF pro cofre e devolve (caminho, bytes). MESMO formato de caminho
    que a importação pela janela usa — os dois acabam no mesmo lugar."""
    caminho = f"apolice/{conta_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}.pdf"
    _cofre.subir_em(caminho, conteudo, "application/pdf")
    return caminho, len(conteudo)


def _gravar(c, conta_id: int, mensagem_id: int, *, nome: str, caminho: str = "",
            bytes_: int = 0, leitura=None, erro: str = "") -> None:
    """Uma linha por mensagem. `on conflict do nothing`: duas entregas da mesma
    mensagem não viram duas leituras, e a primeira é a que vale."""
    campos = {"conta_id": conta_id, "mensagem_id": mensagem_id, "pdf_nome": nome[:120],
              "pdf_caminho": caminho or None, "pdf_bytes": bytes_ or None,
              "erro": (erro or "")[:300] or None}
    if leitura is not None:
        campos.update({
            "seguradora": leitura.seguradora or None,
            "reconhecida": bool(leitura.reconhecida),
            "segurado": (leitura.campos.get("nome") or None),
            "numero_proposta": (leitura.campos.get("numero_proposta") or None),
            "vigencia_fim": leitura.campos.get("vigencia_fim"),
            "form": json.dumps(apdf.para_formulario(leitura)),
            "lido": json.dumps(apdf.resumo_para_guardar(leitura)),
        })
    cols = ", ".join(campos)
    marcas = ", ".join(["%s"] * len(campos))
    c.execute(f"insert into apolice_lida ({cols}) values ({marcas}) "
              f"on conflict (mensagem_id) do nothing", tuple(campos.values()))


def ler_pendentes(pool, conta_id: int, conversa_id: int | None = None) -> dict:
    """Lê os PDFs que faltam e devolve o placar: {lidas, falhas, puladas}.

    A rede fica FORA da transação de propósito: baixar um PDF de 300 KB do CDN do
    WhatsApp leva segundos, e segurar uma conexão do banco por isso é o caminho
    curto pra fila que derrubou o painel em 22/08.
    """
    if not _e_seguros(pool, conta_id):
        return {"lidas": 0, "falhas": 0, "puladas": 0, "motivo": "conta não é de seguros"}
    if not _cofre.configurado():
        return {"lidas": 0, "falhas": 0, "puladas": 0, "motivo": "cofre não configurado"}
    with pool.connection() as c:
        alvos = pendentes(c, conta_id, conversa_id)
    if not alvos:
        return {"lidas": 0, "falhas": 0, "puladas": 0}

    from finance import wa_midia as _wm
    placar = {"lidas": 0, "falhas": 0, "puladas": 0}
    for alvo in alvos:
        erro, leitura, caminho, tam = "", None, "", 0
        try:
            conteudo = b"".join(_wm.buscar(alvo["ref"], alvo["tipo"]))
            leitura = apdf.ler(conteudo)
            caminho, tam = _guardar(conta_id, conteudo, alvo["nome"])
        except _wm.Expirou:
            # não é falha nossa e não adianta repetir: o arquivo não existe mais
            erro = "o WhatsApp já apagou este arquivo"
        except ValueError as e:
            erro = str(e)
        except Exception as e:  # noqa: BLE001
            _log.info("apólice do whatsapp %s falhou (conta %s): %s: %s",
                      alvo["mensagem_id"], conta_id, type(e).__name__, e)
            erro = "não consegui ler este PDF"
        with pool.connection() as c:
            _gravar(c, conta_id, alvo["mensagem_id"], nome=alvo["nome"],
                    caminho=caminho, bytes_=tam, leitura=leitura, erro=erro)
            c.commit()
        placar["falhas" if erro else "lidas"] += 1
    return placar


def ler_pendentes_bg(pool, conta_id: int, conversa_id: int) -> None:
    """O que os webhooks chamam. Best-effort: nunca estoura pro webhook, do mesmo
    jeito que `evento_leitor.ler_conversa_bg`."""
    try:
        r = ler_pendentes(pool, conta_id, conversa_id)
        if r.get("lidas") or r.get("falhas"):
            _log.info("apólice lida sozinha: conta=%s conversa=%s %s", conta_id, conversa_id, r)
    except Exception as e:  # noqa: BLE001
        _log.info("apolice_leitor falhou conta=%s conversa=%s: %s: %s",
                  conta_id, conversa_id, type(e).__name__, e)


def resumo(d: dict) -> str:
    """A linha que a lista mostra no lugar do nome do arquivo.

    Só diz o que foi lido de verdade: sem seguradora reconhecida, o nome do arquivo
    continua sendo a melhor informação que existe."""
    if d.get("erro"):
        return d["erro"]
    partes = [p for p in (d.get("seguradora"), d.get("segurado")) if p]
    if d.get("vigencia_fim"):
        partes.append("vence " + d["vigencia_fim"].strftime("%d/%m/%Y"))
    return " · ".join(partes)


def uma(pool, conta_id: int, mensagem_id: int) -> dict | None:
    """A leitura já feita de uma mensagem, pra janela abrir sem baixar de novo."""
    with pool.connection() as c:
        r = c.execute(
            """select pdf_caminho, pdf_nome, pdf_bytes, seguradora, reconhecida,
                      segurado, numero_proposta, vigencia_fim, form, lido, erro
                 from apolice_lida where conta_id=%s and mensagem_id=%s""",
            (conta_id, mensagem_id)).fetchone()
    if not r:
        return None
    return {"pdf_caminho": r[0], "pdf_nome": r[1], "pdf_bytes": r[2],
            "seguradora": r[3], "reconhecida": bool(r[4]), "segurado": r[5],
            "numero_proposta": r[6], "vigencia_fim": r[7],
            "form": r[8] or {}, "lido": r[9] or {}, "erro": r[10] or ""}
