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
import threading
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


def ja_conheco(pool, conta_id: int, pdf_hash: str, numero_apolice: str = "",
               chassi: str = "") -> dict | None:
    """Este documento já passou por aqui? Devolve o que se sabe dele, ou None.

    Pedido do dono em 21/09/2026: "faz a checagem de duplicidade caso mande o mesma
    apolice nao salvar e avisar". Sem isto, reenviar o mesmo arquivo criava um
    pré-cadastro novo a cada vez — e três linhas iguais na fila de conferência são
    o caminho mais curto pra cadastrar a mesma apólice três vezes.

    Três perguntas, nesta ordem:

    1. o MESMO ARQUIVO já foi lido (`pdf_hash`)? É a pergunta barata e exata.
    2. o número da apólice já está na CARTEIRA? Pega o caso que o hash não pega:
       a seguradora reemite o PDF, os bytes mudam, a apólice é a mesma.
    3. o mesmo CHASSI já está na fila? Esta nasceu em 22/09/2026, olhando a fila
       da Liberal: três linhas Mapfre, todas com placa PIR5077 e chassi
       9BFZH55L8J8038259 — a mesma apólice —, mas com BYTES DIFERENTES (276.050 e
       273.567). O hash não via, e o número também não, porque as duas primeiras
       leituras foram feitas antes de eu conhecer o layout e saíram sem número. O
       chassi é o que não muda nem quando o PDF é reexportado nem quando o leitor
       ainda não sabe ler o resto.

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
        ch = (chassi or "").strip().upper()
        if len(ch) >= 11:          # chassi é 17; abaixo disso é lixo de leitura
            r = c.execute(
                """select id, criado_em, coalesce(seguradora,''), coalesce(segurado,''),
                          vigencia_fim
                     from apolice_lida
                    where conta_id=%s and descartado_em is null
                      and upper(coalesce(lido->'campos'->>'chassi','')) = %s
                    order by criado_em limit 1""", (conta_id, ch)).fetchone()
            if r:
                return {"onde": "fila", "lida_id": r[0], "quando": r[1],
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
        ja = ja_conheco(pool, conta_id, "",
                        leitura.campos.get("numero_apolice") or "",
                        leitura.campos.get("chassi") or "")
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
    """A UMA linha que um aviso de WhatsApp cabe: marca, pessoa e prazo.

    É o que `aviso_de_repetida` manda de volta pra quem reenviou uma apólice que já
    está na casa. A LISTA da janela não usa mais isto — ela tem duas linhas, e o que
    ela mostra em cada uma está em `titulo` e `detalhe`, logo abaixo."""
    if d.get("erro"):
        return d["erro"]
    partes = [p for p in (d.get("seguradora"), d.get("segurado")) if p]
    if d.get("vigencia_fim"):
        partes.append("vence " + d["vigencia_fim"].strftime("%d/%m/%Y"))
    return " · ".join(partes)


def titulo(d: dict) -> str:
    """DE QUEM É o documento — a primeira linha da lista, e o que a pessoa procura.

    O segurado quando o leitor achou; senão o nome do arquivo, sem a extensão.

    POR QUE NÃO A SEGURADORA. Até 22/09/2026 a lista mostrava `resumo`, que começa
    pela marca. Nos sete PDFs que chegaram naquela manhã o leitor reconhecia a
    seguradora e mais nada — e a fila virou sete linhas dizendo "HDI", "Azul
    Seguros", "Tokio Marine", "Zurich", "Yelum", uma embaixo da outra, sem um nome
    de cliente em nenhuma. O nome do arquivo, que o resumo tinha substituído, era
    justamente o que dizia de quem era o papel ("APÓLICE MARIO JOSE VANDERLEI.pdf").
    A marca não sumiu: desceu pra segunda linha, em `detalhe`, que é o lugar de
    contexto. Identidade em cima, contexto embaixo."""
    if d.get("segurado"):
        return str(d["segurado"]).strip()
    nome = str(d.get("nome") or d.get("pdf_nome") or "").strip()
    if nome.lower().endswith(".pdf"):
        nome = nome[:-4].strip()
    return nome


def detalhe(d: dict) -> str:
    """A segunda linha: a marca e o prazo. Contexto, não identidade.

    Vazia quando não se leu nada — linha em branco é melhor que linha inventada, e
    a lista já encolhe sozinha (o CSS não reserva altura pra `.det` vazia)."""
    partes = []
    if d.get("seguradora"):
        partes.append(str(d["seguradora"]))
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
                  and l.descartado_em is null
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


def reler(pool, conta_id: int, lida_id: int) -> dict:
    """Lê de novo um pré-cadastro, do PDF que já está no cofre, com os layouts de HOJE.

    POR QUE ISTO EXISTE. Cada seguradora nova é um bloco em `apolice_pdf._LAYOUTS`,
    e ela entra DEPOIS que o primeiro PDF dela chega. Quem já estava na fila
    continuava com a leitura velha pra sempre: em 22/09/2026 a Liberal tinha duas
    Mapfre lidas antes do layout da Mapfre existir (saíram com "MAPFRE SEGUROS
    GERAIS S/A" no lugar da segurada) e uma Porto lida antes do layout da Porto
    (saiu com a própria corretora). O documento certo estava guardado o tempo
    todo; o que estava velho era a leitura.

    Sem isto a única saída era pedir pro corretor reenviar o arquivo — pedir pro
    cliente refazer o que o sistema pode refazer sozinho.

    ATUALIZA A LINHA, não cria outra: o `id` é o mesmo, o caminho no cofre é o
    mesmo, e a fila não ganha uma quarta cópia do que já estava lá. Devolve
    {ok, leitura, erro}.
    """
    with pool.connection() as c:
        r = c.execute("""select pdf_caminho, coalesce(pdf_nome,'') from apolice_lida
                          where conta_id=%s and id=%s""", (conta_id, lida_id)).fetchone()
    if not r or not r[0]:
        return {"ok": False, "erro": "este documento não tem PDF guardado"}
    try:
        conteudo, _ct = _cofre.ler(r[0])
    except Exception as e:  # noqa: BLE001
        _log.info("reler falhou ao buscar no cofre (conta %s, lida %s): %s: %s",
                  conta_id, lida_id, type(e).__name__, e)
        return {"ok": False, "erro": "não consegui buscar o arquivo no cofre"}

    erro, leitura = "", None
    try:
        leitura = apdf.ler(conteudo, _a_propria_casa(pool, conta_id))
    except ValueError as e:
        erro = str(e)
    except Exception as e:  # noqa: BLE001
        _log.info("releitura falhou (conta %s, lida %s): %s: %s",
                  conta_id, lida_id, type(e).__name__, e)
        erro = "não consegui ler este PDF"

    campos = {"erro": erro or None, "pdf_hash": _hash(conteudo)}
    if leitura is not None:
        campos.update({
            "seguradora": leitura.seguradora or leitura.campos.get("seguradora") or None,
            "reconhecida": bool(leitura.reconhecida),
            "segurado": leitura.campos.get("nome") or None,
            "numero_proposta": leitura.campos.get("numero_proposta") or None,
            "vigencia_fim": leitura.campos.get("vigencia_fim"),
            "form": json.dumps(apdf.para_formulario(leitura)),
            "lido": json.dumps(apdf.resumo_para_guardar(leitura)),
        })
    sets = ", ".join(f"{k} = %s" for k in campos)
    with pool.connection() as c:
        # o hash pode colidir com OUTRA linha que já tinha o mesmo arquivo; a
        # releitura não é lugar de resolver isso, então ela cede o hash.
        try:
            c.execute(f"update apolice_lida set {sets} where conta_id=%s and id=%s",
                      (*campos.values(), conta_id, lida_id))
            c.commit()
        except Exception:  # noqa: BLE001
            c.rollback()
            campos.pop("pdf_hash", None)
            sets = ", ".join(f"{k} = %s" for k in campos)
            c.execute(f"update apolice_lida set {sets} where conta_id=%s and id=%s",
                      (*campos.values(), conta_id, lida_id))
            c.commit()
    return {"ok": not erro, "leitura": leitura, "erro": erro}


def esta_velha(lido: dict | None) -> bool:
    """Esta leitura guardada saiu de um leitor mais velho que o de hoje?

    O carimbo é `lido->>'versao'` (ver `apolice_pdf.VERSAO`). Leitura sem carimbo é
    anterior ao carimbo, logo velha — que é exatamente o caso das que estavam na
    fila quando isto nasceu.

    POR QUE O PAINEL PERGUNTA ISSO. O leitor melhora a cada seguradora nova, e até
    22/09/2026 a melhora só alcançava a fila se alguém achasse o botão "reler com o
    leitor de hoje" e clicasse. Naquele dia o genérico subiu ao meio-dia e as sete
    apólices lidas de manhã continuaram mostrando a leitura das 8h — o dono viu a
    tela e disse, com razão, que "o sistema ainda não tá reconhecendo o layout".
    Ferramenta que melhora sozinha não pede licença pra aplicar a melhora.
    """
    try:
        return int((lido or {}).get("versao") or 0) < apdf.VERSAO
    except (TypeError, ValueError):
        return True


def reler_se_velha(pool, conta_id: int, lida_id: int, lido: dict | None) -> bool:
    """Relê quando a leitura guardada é mais velha que o leitor. Devolve se releu.

    Best-effort de propósito: o cofre pode estar fora do ar, e uma releitura que
    falha não pode impedir a pessoa de abrir a conferência que já existe. Pior
    leitura vale mais que tela que não abre.
    """
    if not esta_velha(lido):
        return False
    try:
        return bool(reler(pool, conta_id, lida_id).get("ok"))
    except Exception as e:  # noqa: BLE001
        _log.info("releitura automática falhou (conta %s, lida %s): %s: %s",
                  conta_id, lida_id, type(e).__name__, e)
        return False


def velhas(pool, conta_id: int, limite: int = 30) -> list:
    """Os ids da fila cuja leitura é mais velha que o leitor de hoje.

    A pergunta é feita no BANCO — `lido->>'versao'` — pra não baixar trinta PDFs
    do cofre só pra descobrir que vinte e oito já estavam em dia.
    """
    with pool.connection() as c:
        return [r[0] for r in c.execute(
            """select l.id from apolice_lida l
                where l.conta_id = %s and l.mensagem_id is null
                  and l.descartado_em is null
                  and l.pdf_caminho is not null
                  and coalesce((l.lido->>'versao')::int, 0) < %s
                  and not exists (select 1 from apolices a
                                   where a.conta_id = l.conta_id
                                     and a.pdf_caminho = l.pdf_caminho)
                order by l.criado_em desc limit %s""",
            (conta_id, apdf.VERSAO, int(limite))).fetchall()]


def reler_a_fila(pool, conta_id: int, limite: int = 30, so_velhas: bool = False) -> dict:
    """Relê o que está esperando conferência. O placar: {relidas, falhas}.

    É o que se roda depois de o leitor aprender alguma coisa — a fila inteira
    aproveita, sem ninguém reenviar nada.

    `so_velhas` recorta pelo carimbo do leitor. É o que a tela usa ao abrir a
    janela: releitura baixa PDF do cofre, e refazer o que já está em dia é pagar
    rede pra chegar na mesma leitura.
    """
    placar = {"relidas": 0, "falhas": 0}
    if so_velhas:
        alvos = velhas(pool, conta_id, limite)
    else:
        alvos = [i["lida_id"] for i in sem_mensagem(pool, conta_id, limite=limite)]
    for lida_id in alvos:
        r = reler(pool, conta_id, lida_id)
        placar["relidas" if r["ok"] else "falhas"] += 1
    return placar


#: contas com uma varredura em andamento. Abrir a janela duas vezes seguidas não
#: pode virar dois mutirões baixando os mesmos PDFs em cima um do outro.
_VARRENDO: set = set()


def varrer_em_segundo_plano(pool, conta_id: int) -> int:
    """Põe a fila em dia SEM segurar quem está abrindo a janela. Devolve quantas.

    POR QUE NÃO NA HORA. Cada releitura baixa um PDF do cofre: sete documentos
    seriam uns quinze segundos de janela parada, e a janela existe justamente pra
    não fazer ninguém esperar.

    POR QUE NÃO SÓ AO ABRIR CADA UMA. Era assim até 22/09/2026, e não bastou: o
    dono abriu quatro das seis, e as duas que ele não abriu continuaram mostrando
    na lista a leitura de antes do leitor novo — uma delas dizendo só "HDI". Ele
    perguntou, com razão, por que aquelas não tinham layout. A resposta não podia
    ser "porque você não clicou nelas".

    Best-effort, como todo o resto do leitor: falhou, a fila continua como está e
    a próxima abertura tenta de novo.
    """
    alvos = velhas(pool, conta_id)
    if not alvos or conta_id in _VARRENDO:
        return 0
    _VARRENDO.add(conta_id)

    def trabalho():
        try:
            placar = reler_a_fila(pool, conta_id, so_velhas=True)
            _log.info("fila posta em dia sozinha: conta=%s %s", conta_id, placar)
        except Exception as e:  # noqa: BLE001
            _log.info("varredura da fila falhou (conta %s): %s: %s",
                      conta_id, type(e).__name__, e)
        finally:
            _VARRENDO.discard(conta_id)

    threading.Thread(target=trabalho, daemon=True).start()
    return len(alvos)


def descartar(pool, conta_id: int, lida_id: int, membro_id=None) -> bool:
    """Tira um documento da fila de conferência sem cadastrar nada.

    A fila só esvaziava CADASTRANDO, e uma fila que só cresce para de ser fila. O
    que não deveria virar apólice — a mesma lida três vezes, um boleto que passou,
    uma leitura obsoleta depois que o layout entrou — não tinha saída.

    NÃO APAGA (regra 0): a linha fica com quem descartou e quando, o PDF continua
    no cofre e a leitura continua auditável. `voltar_da_lixeira` desfaz.
    """
    with pool.connection() as c:
        r = c.execute("""update apolice_lida set descartado_em = now(), descartado_por = %s
                          where conta_id = %s and id = %s and descartado_em is null
                          returning id""", (membro_id, conta_id, lida_id)).fetchone()
        c.commit()
    return bool(r)


def voltar_da_lixeira(pool, conta_id: int, lida_id: int) -> bool:
    """Desfaz o descarte. Existe porque descartar é um toque e errar também."""
    with pool.connection() as c:
        r = c.execute("""update apolice_lida set descartado_em = null, descartado_por = null
                          where conta_id = %s and id = %s returning id""",
                      (conta_id, lida_id)).fetchone()
        c.commit()
    return bool(r)


def descartar_irmas(pool, conta_id: int, numero_apolice: str, *, menos=None) -> int:
    """Depois de cadastrar, some da fila TODO pré-cadastro da mesma apólice.

    O caminho já existente — casar `apolices.pdf_caminho` com `apolice_lida.
    pdf_caminho` — só pega o documento exato que foi confirmado. As IRMÃS são as
    outras leituras do mesmo papel, cada uma com seu caminho no cofre: em 21/09 a
    Mapfre tinha três, e cadastrar uma deixava duas na fila convidando a cadastrar
    de novo.

    A chave é o número da apólice, comparado só pelos DÍGITOS.
    """
    num = "".join(ch for ch in (numero_apolice or "") if ch.isdigit())
    if not num:
        return 0
    with pool.connection() as c:
        rs = c.execute(
            """update apolice_lida set descartado_em = now()
                where conta_id = %s and descartado_em is null
                  and (%s::bigint is null or id <> %s::bigint)
                  and regexp_replace(coalesce(form->>'numero_apolice',''),'\\D','','g') = %s
                returning id""",
            (conta_id, menos, menos, num)).fetchall()
        c.commit()
    return len(rs)


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
