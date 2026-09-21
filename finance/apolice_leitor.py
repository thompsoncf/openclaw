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

import hashlib
import json
import logging
import time
import uuid

from zoneinfo import ZoneInfo

from finance import apolice_pdf as apdf
from finance import comprovantes as _cofre
from finance import raio_x_perfil as rxp

_log = logging.getLogger("apolice_leitor")

#: o recado sai no WhatsApp de quem está em Teresina, não em UTC
_TZ = ZoneInfo("America/Sao_Paulo")

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
        select m.id, coalesce(m.midia_meta->>'nome',''), m.midia_ref, m.midia_tipo,
               coalesce(nullif(cv.contato_nome,''), cv.contato_ref, '')
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
             "ref": r[2] or {}, "tipo": r[3] or "documento", "de": r[4] or ""}
            for r in c.execute(sql, tuple(args)).fetchall()]


def _guardar(conta_id: int, conteudo: bytes, nome: str) -> tuple[str, int]:
    """Sobe o PDF pro cofre e devolve (caminho, bytes). MESMO formato de caminho
    que a importação pela janela usa — os dois acabam no mesmo lugar."""
    caminho = f"apolice/{conta_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}.pdf"
    _cofre.subir_em(caminho, conteudo, "application/pdf")
    return caminho, len(conteudo)


def _gravar(c, conta_id: int, mensagem_id, *, nome: str, caminho: str = "",
            bytes_: int = 0, leitura=None, erro: str = "",
            origem: str = "whatsapp", de: str = "", pdf_hash: str = "") -> int | None:
    """Uma linha por documento. `on conflict do nothing`: duas entregas da mesma
    mensagem do WhatsApp não viram duas leituras, e a primeira é a que vale.

    Devolve o id da linha (ou None quando o conflito cortou a inserção)."""
    campos = {"conta_id": conta_id, "mensagem_id": mensagem_id, "pdf_nome": nome[:120],
              "pdf_hash": pdf_hash or None,
              "pdf_caminho": caminho or None, "pdf_bytes": bytes_ or None,
              "erro": (erro or "")[:300] or None,
              "origem": origem, "de": (de or "")[:120] or None}
    if leitura is not None:
        campos.update({
            # a seguradora pode ter saído SÓ do nome no papel, sem layout conhecido
            # (`apolice_pdf.nomear_seguradora`): é o que faz a linha da lista dizer
            # "Azul Seguros · LUZIA AUREA" em vez de mostrar o nome do arquivo.
            "seguradora": leitura.seguradora or leitura.campos.get("seguradora") or None,
            "reconhecida": bool(leitura.reconhecida),
            "segurado": (leitura.campos.get("nome") or None),
            "numero_proposta": (leitura.campos.get("numero_proposta") or None),
            "vigencia_fim": leitura.campos.get("vigencia_fim"),
            "form": json.dumps(apdf.para_formulario(leitura)),
            "lido": json.dumps(apdf.resumo_para_guardar(leitura)),
        })
    cols = ", ".join(campos)
    marcas = ", ".join(["%s"] * len(campos))
    # o índice é PARCIAL (305) — o `on conflict` precisa apontar pro mesmo recorte
    r = c.execute(f"insert into apolice_lida ({cols}) values ({marcas}) "
                  f"on conflict (mensagem_id) where mensagem_id is not null "
                  f"do nothing returning id", tuple(campos.values())).fetchone()
    return r[0] if r else None


def _a_propria_casa(pool, conta_id: int) -> tuple[str, ...]:
    """Nome, documento e e-mail da CORRETORA e dos membros dela.

    A trava que não depende de conhecer o layout. Toda apólice traz um bloco com
    os dados de quem VENDEU — a corretora —, com os mesmos rótulos do bloco de
    quem COMPROU, e quando o leitor erra de bloco é quase sempre esse que ele
    pega. Em 21/09/2026 a Porto Seguro saiu com "LIBERAL NETO CONS E CORG DE SEGS
    LTDA" no lugar da cliente, e o e-mail do corretor junto.

    Nenhuma lista de terceiros resolve isso, porque o corretor é diferente em cada
    instalação. Mas a conta sabe o próprio nome — e nada do que é dela pode ser o
    cliente.

    Só leitura, e tolerante: sem isto o leitor continua funcionando, só perde uma
    rede de proteção.
    """
    try:
        with pool.connection() as c:
            r = c.execute("select coalesce(nome,''), coalesce(documento,'') "
                          "  from contas where id=%s", (conta_id,)).fetchone()
            ms = c.execute("select coalesce(nome,''), coalesce(email,'') "
                           "  from membros where conta_id=%s and coalesce(ativo,true)",
                           (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("não consegui montar a lista da própria casa (conta %s): %s: %s",
                  conta_id, type(e).__name__, e)
        return ()
    fora = list(r or ())
    for m in ms or ():
        fora.extend(m)
    # nome curto demais casa com meio mundo ("ZAQ" dentro de qualquer palavra)
    return tuple(v for v in fora if len((v or "").strip()) >= 5)


def _hash(conteudo: bytes) -> str:
    """A identidade do documento. É o CONTEÚDO e não o nome: o assistente não
    recebe nome de arquivo do WhatsApp (vira "apolice-recebida.pdf") e o corretor
    renomeia à vontade."""
    return hashlib.sha256(conteudo).hexdigest()


def ja_conheco(pool, conta_id: int, pdf_hash: str, numero_apolice: str = "") -> dict | None:
    """Este documento já passou por aqui? Devolve o que se sabe dele, ou None.

    Pedido do dono em 21/09/2026: "faz a checagem de duplicidade caso mande o mesma
    apolice nao salvar e avisar". Sem isto, reenviar o mesmo arquivo criava um
    pré-cadastro novo a cada vez — e três linhas iguais na fila de conferência são
    o caminho mais curto pra cadastrar a mesma apólice três vezes.

    Duas perguntas, nesta ordem:

    1. o MESMO ARQUIVO já foi lido (`pdf_hash`)? É a pergunta barata e exata.
    2. o número da apólice já está na CARTEIRA? Pega o caso que o hash não pega:
       a seguradora reemite o PDF, os bytes mudam, a apólice é a mesma.

    Devolve `{onde, quando, resumo, lida_id?}`. `onde` é 'fila' (lida, esperando
    conferência) ou 'carteira' (já cadastrada) — as duas coisas pedem recados
    diferentes.
    """
    with pool.connection() as c:
        if pdf_hash:
            r = c.execute(
                """select id, criado_em, coalesce(seguradora,''), coalesce(segurado,''),
                          vigencia_fim
                     from apolice_lida
                    where conta_id=%s and pdf_hash=%s order by criado_em limit 1""",
                (conta_id, pdf_hash)).fetchone()
            if r:
                return {"onde": "fila", "lida_id": r[0], "quando": r[1],
                        "resumo": _linha(r[2], r[3], r[4])}
        num = "".join(ch for ch in (numero_apolice or "") if ch.isdigit())
        if num:
            r = c.execute(
                """select id, criado_em, coalesce(seguradora,''),
                          coalesce((select nome from clientes
                                      where id=a.cliente_id and dono_id=a.conta_id),''),
                          vigencia_fim
                     from apolices a
                    where conta_id=%s and numero_apolice is not null
                      and regexp_replace(numero_apolice,'\\D','','g') = %s
                    order by criado_em limit 1""", (conta_id, num)).fetchone()
            if r:
                return {"onde": "carteira", "apolice_id": r[0], "quando": r[1],
                        "resumo": _linha(r[2], r[3], r[4])}
    return None


def _linha(seguradora: str, quem: str, vence) -> str:
    partes = [p for p in (seguradora, quem) if p]
    if vence:
        partes.append("vence " + vence.strftime("%d/%m/%Y"))
    return " · ".join(partes)


def ler_bytes(pool, conta_id: int, conteudo: bytes, nome: str, *,
              origem: str = "whatsapp", de: str = "", mensagem_id=None) -> dict:
    """Lê UM PDF que já está na mão e deixa o pré-cadastro pronto.

    É o miolo das duas portas: o WhatsApp chega aqui depois de baixar do CDN, o
    Telegram chega aqui com os bytes que o membro subiu. Nenhuma das duas cadastra
    apólice — as duas param na conferência.

    NÃO SALVA DUAS VEZES. O mesmo arquivo reenviado devolve `repetida` com o que já
    se sabe dele, e nada novo entra no banco — nem linha, nem cópia no cofre. Quem
    chamou usa isso pra AVISAR em vez de fingir que leu de novo.

    Devolve {ok, id, leitura, erro, repetida?}.
    """
    pdf_hash = _hash(conteudo)
    ja = ja_conheco(pool, conta_id, pdf_hash)
    if ja:
        return {"ok": True, "id": ja.get("lida_id"), "leitura": None,
                "erro": "", "repetida": ja}

    erro, leitura, caminho, tam = "", None, "", 0
    try:
        leitura = apdf.ler(conteudo, _a_propria_casa(pool, conta_id))
        caminho, tam = _guardar(conta_id, conteudo, nome)
    except ValueError as e:
        erro = str(e)
    except Exception as e:  # noqa: BLE001
        _log.info("leitura de apólice falhou (conta %s, %s): %s: %s",
                  conta_id, origem, type(e).__name__, e)
        erro = "não consegui ler este PDF"

    # a SEGUNDA pergunta, que só dá pra fazer depois de ler: o número desta
    # apólice já está na carteira? Pega o caso que o hash não pega — a seguradora
    # reemite o PDF, os bytes mudam, a apólice é a mesma. O documento já subiu pro
    # cofre aqui; deixar subir é mais barato que ler duas vezes, e o cofre
    # desduplica pelo caminho.
    if leitura is not None:
        ja = ja_conheco(pool, conta_id, "", leitura.campos.get("numero_apolice") or "")
        if ja:
            return {"ok": True, "id": None, "leitura": leitura, "erro": "",
                    "repetida": ja}

    with pool.connection() as c:
        lida_id = _gravar(c, conta_id, mensagem_id, nome=nome, caminho=caminho,
                          bytes_=tam, leitura=leitura, erro=erro, origem=origem,
                          de=de, pdf_hash=pdf_hash)
        c.commit()
    return {"ok": not erro, "id": lida_id, "leitura": leitura, "erro": erro}


def tomou(r: dict) -> bool:
    """A porta de conversa fica com este documento — ou deixa o caixa seguir?

    O critério é `e_apolice` (as marcas de um seguro no papel), NÃO `reconhecida`
    (o layout que eu sei ler). Era `reconhecida` até 21/09/2026, e o preço disso
    apareceu no mesmo dia: a apólice da Azul que o Cássio mandou virou lembrete de
    pagar parcela, porque o único layout que eu conheço é o da Allianz. Ler pouco
    de uma apólice é um formulário pela metade; ler ela como cupom é o dado na
    gaveta errada.

    Documento que não tem as marcas continua caindo no caixa — é o comprovante de
    Pix de quem usa o mesmo número pras duas coisas, e ele não pode parar.
    """
    if r.get("repetida"):
        return True          # é apólice sim, e eu já a conheço — o recado é outro
    L = r.get("leitura")
    return bool(r.get("ok") and L is not None and L.e_apolice)


def aviso_de_repetida(ja: dict) -> str:
    """O recado quando o documento já é conhecido. Diz ONDE ele está, porque as
    duas situações pedem coisas diferentes da pessoa: na fila falta conferir, na
    carteira não falta nada."""
    quando = ""
    try:
        quando = " em " + ja["quando"].astimezone(_TZ).strftime("%d/%m")
    except Exception:  # noqa: BLE001
        pass
    resumo = ja.get("resumo") or ""
    if ja.get("onde") == "carteira":
        return ("📄 Essa apólice eu já tenho cadastrada"
                + (f": {resumo}." if resumo else ".")
                + " Não salvei de novo.")
    return ("📄 Essa apólice eu já li" + quando
            + (f": {resumo}." if resumo else ".")
            + " Ela continua esperando conferência em Renovações — não salvei de novo.\n"
            "https://app.zaq-ia.com/painel/renovacoes")


def campos_do_aviso(leitura) -> list[tuple[str, str]]:
    """Os pares (rótulo, valor) que a resposta no WhatsApp/Telegram mostra.

    Um só lugar porque as duas portas têm que dizer a MESMA coisa; o que muda
    entre elas é só o negrito."""
    c = leitura.campos
    pares = []
    seg = leitura.seguradora or c.get("seguradora")
    if seg:
        pares.append(("Seguradora", seg))
    if c.get("nome"):
        pares.append(("Segurado", c["nome"]))
    if c.get("vigencia_fim"):
        pares.append(("Vence", c["vigencia_fim"].strftime("%d/%m/%Y")))
    if c.get("numero_apolice"):
        pares.append(("Apólice", c["numero_apolice"]))
    elif c.get("numero_proposta"):
        pares.append(("Proposta", c["numero_proposta"]))
    if c.get("placa"):
        pares.append(("Placa", c["placa"]))
    return pares


def rodape_do_aviso(leitura) -> str:
    """A última linha: o que ainda falta a pessoa fazer, dito sem enfeite.

    Layout desconhecido aparece aqui e não some: é a diferença entre 'confira' e
    'preencha', e quem lê precisa saber qual dos dois é."""
    if not leitura.reconhecida:
        return ("Não conheço o modelo desta seguradora, então li só o que estava "
                "rotulado — o resto ficou em branco pra você preencher em Renovações. "
                "Nada foi cadastrado ainda.")
    return ("Ela está esperando você conferir em Renovações — nada foi cadastrado "
            "ainda.")


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
        try:
            conteudo = b"".join(_wm.buscar(alvo["ref"], alvo["tipo"]))
        except _wm.Expirou:
            # não é falha nossa e não adianta repetir: o arquivo não existe mais.
            # Fica registrado JUSTAMENTE pra não tentar de novo.
            with pool.connection() as c:
                _gravar(c, conta_id, alvo["mensagem_id"], nome=alvo["nome"],
                        erro="o WhatsApp já apagou este arquivo", de=alvo.get("de", ""))
                c.commit()
            placar["falhas"] += 1
            continue
        except Exception as e:  # noqa: BLE001
            _log.info("download da apólice %s falhou (conta %s): %s: %s",
                      alvo["mensagem_id"], conta_id, type(e).__name__, e)
            with pool.connection() as c:
                _gravar(c, conta_id, alvo["mensagem_id"], nome=alvo["nome"],
                        erro="não consegui baixar este arquivo", de=alvo.get("de", ""))
                c.commit()
            placar["falhas"] += 1
            continue
        r = ler_bytes(pool, conta_id, conteudo, alvo["nome"],
                      origem="whatsapp", de=alvo.get("de", ""),
                      mensagem_id=alvo["mensagem_id"])
        placar["lidas" if r["ok"] else "falhas"] += 1
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


#: como a lista da tela chama cada porta de conversa
PORTAS = {"telegram": "Telegram", "whatsapp": "assistente"}


def sem_mensagem(pool, conta_id: int, dias: int = 30, limite: int = 30) -> list[dict]:
    """Os pré-cadastros que NÃO vieram de mensagem do chip e ainda não viraram apólice.

    O recorte é `mensagem_id is null`, e não a origem. Foi `origem = 'telegram'` até
    21/09/2026, e no dia em que a terceira porta abriu (o assistente no número da
    ZAQ, que grava origem `whatsapp` sem mensagem) esse filtro teria deixado o
    documento invisível: fora daqui por causa da origem, e fora de
    `apolices.pdfs_do_whatsapp` por não ter mensagem. Pré-cadastro que ninguém vê é
    pior que pré-cadastro que não existe.

    A marca de "já cadastrada" é o CAMINHO DO PDF: quando a pessoa confirma, a
    apólice guarda o mesmo arquivo do pré-cadastro. Serve pras três portas e não
    precisa de marcador novo.
    """
    with pool.connection() as c:
        rows = c.execute(
            """select l.id, l.criado_em, coalesce(l.de,''), coalesce(l.pdf_nome,''),
                      coalesce(l.pdf_bytes,0), l.seguradora, l.segurado, l.vigencia_fim,
                      coalesce(l.erro,''), coalesce(l.origem,'')
                 from apolice_lida l
                where l.conta_id = %s and l.mensagem_id is null
                  and l.criado_em > now() - make_interval(days => %s)
                  and not exists (select 1 from apolices a
                                   where a.conta_id = l.conta_id
                                     and a.pdf_caminho is not null
                                     and a.pdf_caminho = l.pdf_caminho)
                order by l.criado_em desc limit %s""",
            (conta_id, int(dias), int(limite))).fetchall()
    return [{"lida_id": r[0], "quando": r[1], "de": r[2] or "—",
             "nome": r[3] or "documento.pdf", "bytes": int(r[4] or 0),
             "seguradora": r[5], "segurado": r[6], "vigencia_fim": r[7],
             "erro_leitura": r[8], "porta": PORTAS.get(r[9], r[9] or "assistente"),
             "lida": True}
            for r in rows]


def por_id(pool, conta_id: int, lida_id: int) -> dict | None:
    """A leitura já feita, pelo id do pré-cadastro. O caminho do Telegram, que não
    tem mensagem pra procurar por ela."""
    with pool.connection() as c:
        r = c.execute(
            """select pdf_caminho, pdf_nome, pdf_bytes, seguradora, reconhecida,
                      segurado, numero_proposta, vigencia_fim, form, lido, erro
                 from apolice_lida where conta_id=%s and id=%s""",
            (conta_id, lida_id)).fetchone()
    if not r:
        return None
    return {"pdf_caminho": r[0], "pdf_nome": r[1], "pdf_bytes": r[2],
            "seguradora": r[3], "reconhecida": bool(r[4]), "segurado": r[5],
            "numero_proposta": r[6], "vigencia_fim": r[7],
            "form": r[8] or {}, "lido": r[9] or {}, "erro": r[10] or ""}
