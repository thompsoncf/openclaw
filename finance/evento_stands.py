"""Venda de ESTANDES numerados de feira/evento (migração 448).

O PEDIDO (Outlet Chic, conta 40, 28/09/2026): mapa da planta oficial, cada
estande reservável por 3 dias e confirmação MANUAL de pagamento pelo dono (sem
gateway automático). Genérico o bastante pra qualquer conta do nicho 'eventos'
que venda espaço numerado — não é exclusivo da Outlet Chic, só nasce com ela.

O desenho é CLONADO do padrão de pré-reserva de DATA (finance/agenda.py +
160_agenda_pre_reserva.sql): mesmo status/prazo/job de expiração, aplicado por
ESTANDE em vez de por dia da agenda. Ver 448_evento_stands.sql pro "porquê" de
cada coluna — este módulo só implementa a regra de negócio em cima do schema.

BLOQUEIA_EM, a decisão central (config por conta, migração 448):
  'pagamento' (padrão, e o modo da Outlet Chic) — o estande fica LIVRE,
    disputável por qualquer interessado, até alguém ENVIAR um comprovante do
    sinal (pela página pública ou pelo WhatsApp). É o comprovante chegando que
    dispara livre -> pre_reservado, com prazo = agora + pre_reserva_dias como
    REDE DE SEGURANÇA pro dono não esquecer de conferir.
  'pedido' — o estande trava assim que o cliente PEDE, antes de qualquer
    comprovante (fluxo de "pedir" fica fora deste módulo por ora — ver TODO
    abaixo); pre_reserva_dias aqui é o prazo que o CLIENTE tem pra pagar.

Sinal e parcelas do estande vivem em orcamentos.sinal_centavos/parcelas
(migrações 147/161) — o MESMO motor que todo orçamento de evento já usa (ver
finance/vendas.py::fechar_orcamento). evento_stands.orcamento_id só aponta pra
qual orçamento é o dono da venda; este módulo nunca grava sinal/parcela.

# TODO(zaq-tool): modo 'pedido' não tem, ainda, um caminho que TRAVA o estande
# no momento do pedido (antes do comprovante) — só o disparo por comprovante
# está implementado (registrar_comprovante). Nenhuma conta usa 'pedido' hoje
# (só a Outlet Chic existe, e ela é 'pagamento'), então não há urgência; quando
# a primeira conta pedir esse modo, this function precisa de uma irmã tipo
# `pedir(pool, conta_id, codigo, prospeccao_id)` que trava sem exigir arquivo.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta

from . import agenda as _ag

_log = logging.getLogger("openclaw.evento_stands")

STATUS = ("livre", "pre_reservado", "vendido")
BLOQUEIA_EM = ("pedido", "pagamento")

# Piso de segurança quando a conta não tem config (ou zerou o campo) — mesmo
# raciocínio de agenda.PRE_RESERVA_DIAS: 3 dias é o padrão de quem não mexer.
PRE_RESERVA_DIAS_PADRAO = 3

_COLS = ("id, conta_id, codigo, pavilhao, zona, tamanho, preco_centavos, status, "
        "pre_reserva_ate, comprovante_url, comprovante_em, prospeccao_id, "
        "orcamento_id, ordem, criado_em, atualizado_em, cliente_id, grupo_id")


def _fmt(row) -> dict:
    return {"id": row[0], "conta_id": row[1], "codigo": row[2], "pavilhao": row[3],
            "zona": row[4], "tamanho": row[5], "preco_centavos": row[6], "status": row[7],
            "pre_reserva_ate": row[8], "comprovante_url": row[9], "comprovante_em": row[10],
            "prospeccao_id": row[11], "orcamento_id": row[12], "ordem": row[13],
            "criado_em": row[14], "atualizado_em": row[15],
            "cliente_id": row[16] if len(row) > 16 else None,
            "grupo_id": row[17] if len(row) > 17 else None}


_CFG_COLS = ("conta_id, slug, bloqueia_em, pre_reserva_dias, whatsapp_numero, pix_chave, "
            "pix_titular, edicao_label, evento_inicio, evento_fim, evento_local, "
            "sinal_minimo_centavos, max_por_empresa, saldo_ate, evento_horario")


def _fmt_cfg(row) -> dict:
    return {"conta_id": row[0], "slug": row[1], "bloqueia_em": row[2],
            "pre_reserva_dias": row[3], "whatsapp_numero": row[4], "pix_chave": row[5],
            "pix_titular": row[6], "edicao_label": row[7], "evento_inicio": row[8],
            "evento_fim": row[9], "evento_local": row[10],
            "sinal_minimo_centavos": row[11], "max_por_empresa": row[12],
            "saldo_ate": row[13], "evento_horario": row[14]}


# ---------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------

def listar(pool, conta_id: int, status: str | None = None) -> list[dict]:
    """Os estandes da conta, na ordem do MAPA (pavilhão, depois zona, depois a
    ordem de exibição que o dono definiu, depois o código como desempate)."""
    where = "conta_id=%s"
    args: list = [conta_id]
    if status:
        where += " and status=%s"
        args.append(status)
    with pool.connection() as c:
        rows = c.execute(
            f"select {_COLS} from evento_stands where {where} "
            "order by pavilhao, zona nulls last, ordem, codigo", args).fetchall()
    return [_fmt(r) for r in rows]


def buscar(pool, conta_id: int, codigo: str) -> dict | None:
    """Um estande pelo código oficial da planta ('G58', 'i05'...). Escopado por
    conta — o mesmo código pode existir em duas contas diferentes."""
    with pool.connection() as c:
        r = c.execute(f"select {_COLS} from evento_stands where conta_id=%s and codigo=%s",
                      (conta_id, codigo)).fetchone()
    return _fmt(r) if r else None


def obter_config(pool, conta_id: int) -> dict | None:
    """A config da conta, ou None se ela não vende estande (feature opt-in —
    nem toda conta do nicho 'eventos' vende espaço numerado)."""
    with pool.connection() as c:
        r = c.execute(f"select {_CFG_COLS} from evento_stands_config where conta_id=%s",
                      (conta_id,)).fetchone()
    return _fmt_cfg(r) if r else None


# ---------------------------------------------------------------------------
# LINK DE VENDAS DO VENDEDOR
#
# O vendedor manda pro CLIENTE DELE um link da página pública; o cliente
# escolhe qualquer stand, compra, e a venda cai na conta do vendedor (pedido
# do dono, 30/09/2026 — o link NÃO é login no app, é a vitrine com a marca do
# vendedor). A marca vai na URL (`?v=`), e URL é entrada não confiável: um
# `v=2` cru seria adivinhável e qualquer um poderia mandar comissão pra
# qualquer colega. Por isso o código leva uma ASSINATURA (HMAC do id com o
# segredo do servidor): só quem o servidor gerou é aceito, e nenhum
# vendedor forja o código do outro.
# ---------------------------------------------------------------------------

def _segredo_vendedor() -> bytes:
    # mesma fonte do segredo de sessão (web/app._segredo_sessao); em produção
    # ele é obrigatório e forte — o default só existe no ambiente local/testes
    return (os.environ.get("PORTAL_SECRET") or "troque-isto-em-producao").encode()


def codigo_vendedor(membro_id: int) -> str:
    """O código do link de vendas do vendedor: '<id>-<assinatura>'."""
    assinatura = hmac.new(_segredo_vendedor(), f"stand-v:{int(membro_id)}".encode(),
                          hashlib.sha256).hexdigest()[:10]
    return f"{int(membro_id)}-{assinatura}"


def vendedor_do_codigo(pool, conta_id: int, codigo: str) -> dict | None:
    """Resolve o `v` da URL em {'id', 'nome'} — ou None se não vale.

    Vale quando (1) a assinatura confere, (2) o membro é DESTA conta, está
    ATIVO e é vendedor/gestor. Desativou o vendedor na Equipe? O link dele
    para de atribuir na hora (a venda cai sem dono, e o gestor atribui)."""
    try:
        id_txt, _, assinatura = (codigo or "").strip().partition("-")
        membro_id = int(id_txt)
    except (TypeError, ValueError):
        return None
    if not hmac.compare_digest(codigo_vendedor(membro_id), f"{membro_id}-{assinatura}"):
        return None
    with pool.connection() as c:
        r = c.execute(
            "select id, nullif(nome,'') from membros where id=%s and conta_id=%s "
            "and ativo and papel in ('vendedor','gestor')", (membro_id, conta_id)).fetchone()
    return {"id": r[0], "nome": r[1]} if r else None


def codigo_cliente(cliente_id: int) -> str:
    """O código do CLIENTE no link que a vendedora manda pra ele (`?c=`), pra a
    página abrir com o cadastro dele (01/10/2026): '<id>-<assinatura>'. Mesmo
    desenho do `codigo_vendedor` — um `c=12` cru deixaria qualquer um abrir a
    página com o nome e o WhatsApp de outro cliente, ou grudar a reserva nele."""
    assinatura = hmac.new(_segredo_vendedor(), f"stand-c:{int(cliente_id)}".encode(),
                          hashlib.sha256).hexdigest()[:10]
    return f"{int(cliente_id)}-{assinatura}"


def cliente_do_codigo(pool, conta_id: int, codigo: str) -> dict | None:
    """Resolve o `c` da URL em {'id', 'nome', 'whatsapp'} — ou None se a
    assinatura não confere ou o cliente não é DESTA conta."""
    try:
        id_txt, _, assinatura = (codigo or "").strip().partition("-")
        cliente_id = int(id_txt)
    except (TypeError, ValueError):
        return None
    if not hmac.compare_digest(codigo_cliente(cliente_id), f"{cliente_id}-{assinatura}"):
        return None
    with pool.connection() as c:
        r = c.execute("select id, nome, telefone from clientes where id=%s and dono_id=%s",
                      (cliente_id, conta_id)).fetchone()
    return {"id": int(r[0]), "nome": r[1] or "", "whatsapp": r[2] or ""} if r else None


def buscar_config_por_slug(pool, slug: str) -> dict | None:
    """A config pelo SLUG da URL pública (/e/<slug>) — é como a página resolve
    de qual conta ela está falando, sem o conta_id na URL."""
    with pool.connection() as c:
        r = c.execute(f"select {_CFG_COLS} from evento_stands_config where slug=%s",
                      (slug,)).fetchone()
    return _fmt_cfg(r) if r else None


# ---------------------------------------------------------------------------
# escrita
# ---------------------------------------------------------------------------

def registrar_comprovante(pool, conta_id: int, codigo: str, comprovante_url: str,
                          prospeccao_id: int | None = None) -> dict:
    """O comprovante do sinal chegou (pela página pública ou pelo WhatsApp) —
    aplica a regra de bloqueio (ver o cabeçalho do módulo).

    Devolve {"ok": True, "stand": {...}} quando funcionou, ou
    {"ok": False, "erro": "..."} — inclusive quando o estande já não estava
    disponível (VENDIDO por outra pessoa, ou não existe). O `erro` é o que a
    tela/o agente devolvem pro cliente; nunca uma exceção pro chamador tratar.

    ATÔMICO de propósito: dois interessados podem mandar comprovante do MESMO
    estande livre quase ao mesmo tempo (é o próprio ponto do modo 'pagamento' —
    o estande fica disputável até o sinal entrar). Um UPDATE só, com o WHERE
    checando o status, decide quem chega primeiro; o outro recebe o erro e o
    dono resolve na conversa/WhatsApp, olhando os dois comprovantes.

    Reenvio (o mesmo interessado manda o comprovante de novo, ou um melhor)
    quando o estande já está pre_reservado por ELE é aceito — substitui o
    arquivo e MANTÉM o prazo original (não estica a reserva de graça a cada
    reenvio). Não dá pra distinguir "é o mesmo interessado" com o que este
    módulo tem à mão (sem sessão de comprador); no pior caso, dois comprovantes
    concorrentes na mesma pré-reserva é exatamente o tipo de coisa que o dono
    decide olhando os dois — o `confirmar_pagamento` é sempre manual.
    """
    cfg = obter_config(pool, conta_id)
    dias = int((cfg or {}).get("pre_reserva_dias") or PRE_RESERVA_DIAS_PADRAO)
    agora = _ag.agora_brt()
    prazo = agora + timedelta(days=dias)
    with pool.connection() as c:
        r = c.execute(
            f"""update evento_stands
                   set status = case when status='livre' then 'pre_reservado' else status end,
                       pre_reserva_ate = case when status='livre' then %s else pre_reserva_ate end,
                       comprovante_url = %s,
                       comprovante_em = %s,
                       prospeccao_id = coalesce(%s, prospeccao_id),
                       grupo_id = case when status='livre' then null else grupo_id end,
                       aviso_vence_em = case when status='livre' then null else aviso_vence_em end,
                       -- RESERVA NOVA NASCE LIMPA (01/10/2026): o stand que foi liberado
                       -- (ou venceu) ainda aponta pra proposta e pro cliente de quem
                       -- estava lá, e `garantir_orcamento_e_contrato` reaproveitaria o
                       -- contrato dessa pessoa. Reenvio no MESMO pre_reservado mantém.
                       orcamento_id = case when status='livre' then null else orcamento_id end,
                       cliente_id = case when status='livre' then null else cliente_id end,
                       atualizado_em = now()
                 where conta_id=%s and codigo=%s and status in ('livre','pre_reservado')
                 returning {_COLS}""",
            (prazo, comprovante_url, agora, prospeccao_id, conta_id, codigo)).fetchone()
        if r is None:
            atual = c.execute(
                f"select {_COLS} from evento_stands where conta_id=%s and codigo=%s",
                (conta_id, codigo)).fetchone()
            c.commit()
            if atual is None:
                return {"ok": False, "erro": "Estande não encontrado."}
            return {"ok": False, "erro": "Esse estande já foi vendido.",
                    "stand": _fmt(atual)}
        c.commit()
    stand = _fmt(r)
    _log.info("evento_stands: comprovante recebido — conta %s, estande %s (status %s)",
              conta_id, codigo, stand["status"])
    return {"ok": True, "stand": stand}


def _subir_comprovante(conta_id: int, codigo: str, conteudo: bytes, content_type: str,
                       subir=None) -> tuple[str | None, str | None]:
    """Valida e sobe o arquivo pro bucket PRIVADO. Devolve (caminho, None) ou
    (None, "a frase do erro")."""
    from . import comprovantes as _comprov
    import time
    import uuid
    try:
        ct = _comprov.validar(conteudo, content_type)
    except ValueError as e:
        return None, str(e)
    ext = _EXT_COMPROVANTE.get(ct, "bin")
    caminho = f"stands/{conta_id}/{codigo}-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}"
    try:
        (subir or _comprov.subir_em)(caminho, conteudo, ct)
    except ValueError as e:
        return None, str(e)
    return caminho, None


def anexar_comprovante_da_reserva(pool, conta_id: int, codigo: str, conteudo: bytes,
                                  content_type: str, *, subir=None) -> dict:
    """O COMPROVANTE DE UMA RESERVA QUE JÁ EXISTE (03/10/2026, relato do dono da
    Outlet Chic: a vendedora não conseguia anexar o comprovante do cliente na
    reserva lançada pela lista — o app só tinha "Registrar venda", e só pra stand
    livre). Sobe o arquivo e liga a TODOS os stands da reserva (a mesma proposta)
    que ainda estão reservados; status e prazo não mudam — confirmar o sinal
    continua sendo da gestão. Sem proposta ainda, ela nasce agora (com contrato).
    Devolve {"ok", "codigos", ...} ou {"ok": False, "erro"}."""
    stand = buscar(pool, conta_id, codigo)
    if not stand:
        return {"ok": False, "erro": "Estande não encontrado."}
    if stand["status"] == "livre":
        return {"ok": False, "erro": "Este stand está livre — use \"Registrar venda\"."}
    if stand["status"] != "pre_reservado":
        return {"ok": False, "erro": "Este stand já está vendido: o sinal já foi confirmado."}
    caminho, erro = _subir_comprovante(conta_id, codigo, conteudo, content_type, subir)
    if erro:
        return {"ok": False, "erro": erro}
    oid = stand.get("orcamento_id")
    with pool.connection() as c:
        rows = c.execute(
            "update evento_stands set comprovante_url=%s, comprovante_em=%s, atualizado_em=now() "
            "where conta_id=%s and status='pre_reservado' and (codigo=%s or "
            "(%s::bigint is not null and orcamento_id=%s::bigint)) returning codigo",
            (caminho, _ag.agora_brt(), conta_id, codigo, oid, oid)).fetchall()
        c.commit()
    r = {"ok": True, "codigos": sorted(x[0] for x in rows) or [codigo]}
    if not oid:
        try:
            extra = garantir_orcamento_e_contrato(pool, conta_id, buscar(pool, conta_id, codigo))
            if extra:
                r.update(extra)
        except Exception as e:  # noqa: BLE001 — o comprovante já está no stand
            _log.warning("evento_stands: comprovante anexado mas a proposta do %s/%s "
                         "falhou: %s: %s", conta_id, codigo, type(e).__name__, e)
    _log.info("evento_stands: comprovante anexado à reserva — conta %s, estande(s) %s",
              conta_id, ",".join(r["codigos"]))
    return r


def _registrar_reserva_do_stand(pool, conta_id: int, stand: dict, nome: str, whatsapp,
                                vendedor_id=None) -> int | None:
    """O stand ocupado SEM prospecção (o comprovante entrou pelo painel sem o nome do
    lojista — S116, S78 e i14 em 02/10/2026) ganha a reserva registrada: é nela que
    moram a marca, a vendedora e, depois, a proposta. Devolve o id, ou None."""
    nome = (nome or "").strip()
    if not nome:
        return None
    oid = stand.get("orcamento_id")
    with pool.connection() as c:
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem, vendedor_id) "
            "values (%s,%s,%s,'novo','pagina_stands',%s) returning id",
            (conta_id, nome[:200], (whatsapp or "").strip()[:40] or None,
             int(vendedor_id) if vendedor_id else None)).fetchone()[0]
        c.execute("update evento_stands set prospeccao_id=%s, atualizado_em=now() "
                  "where conta_id=%s and prospeccao_id is null and (codigo=%s or "
                  "(%s::bigint is not null and orcamento_id=%s::bigint))",
                  (pid, conta_id, stand["codigo"], oid, oid))
        c.commit()
    return int(pid)


def cadastro_pelo_documento(pool, conta_id: int, doc: str) -> dict | None:
    """O cadastro que a conta JÁ TEM em Clientes (cliente ou fornecedor, feito à mão
    ou por outra venda) com este CPF/CNPJ, no formato do botão de busca do
    formulário do stand (o mesmo de `receita_do_cnpj`, mais o WhatsApp). None se a
    conta não tem. Quem também é cliente de OUTRA conta: WhatsApp e e-mail não
    vêm (são o que a outra conta registrou)."""
    from . import clientes as _cli
    from . import validadoc
    ok, tipo, digitos = validadoc.valida(doc)
    if not ok or tipo not in ("pf", "pj"):
        return None
    dono = _dono_do_documento(pool, conta_id, tipo, digitos)
    if not dono or not dono.get("cliente_id"):
        return None
    cli = _cli.obter_cliente(pool, conta_id, int(dono["cliente_id"]))
    if not cli:
        return None
    fora = bool(_clientes_de_outra_conta(pool, conta_id, {cli["id"]: cli}))
    g = lambda k: (cli.get(k) or "").strip() or None  # noqa: E731
    cep = "".join(ch for ch in str(cli.get("cep") or "") if ch.isdigit())
    return {"ok": True, "fonte": "clientes", "cliente": g("nome"),
            "razao": g("razao_social"), "fantasia": g("nome"), "rep": g("representante"),
            "end": g("endereco"),
            "cep": f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else (cep or None),
            "cidade": g("cidade"), "uf": g("uf"),
            "email": None if fora else g("email"), "whats": None if fora else g("telefone")}


def buscar_dados_do_documento(pool, conta_id: int, doc: str) -> dict:
    """O botão de busca do formulário do cliente do stand (painel e app — o link
    público do contrato continua só na Receita): primeiro o cadastro que a conta
    já tem em Clientes (03/10/2026, o dono: o cliente cadastrado à mão na aba
    Clientes/Fornecedor "a vendedora não consegue puxar os dados"), depois a
    Receita. CPF só existe no cadastro: a Receita não consulta pessoa física."""
    from . import validadoc
    achado = None
    if pool is not None:
        try:
            achado = cadastro_pelo_documento(pool, conta_id, doc)
        except Exception as e:  # noqa: BLE001 — sem o cadastro, a Receita ainda responde
            _log.info("evento_stands: busca em Clientes falhou: %s: %s", type(e).__name__, e)
    if achado:
        return achado
    ok, tipo, _dig = validadoc.valida(doc)
    if ok and tipo == "pf":
        return {"ok": False, "erro": "Esse CPF não está em Clientes — digite os dados."}
    return receita_do_cnpj(doc)


# Mesmos tipos aceitos de finance/comprovantes.py (o comprovante do estande é
# prova de pagamento, não foto de obra — não reaproveita finance/obra_fotos.py,
# que aceita HEIC e não aceita PDF).
_EXT_COMPROVANTE = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg",
                    "image/png": "png", "image/webp": "webp"}


def subir_e_registrar_comprovante(pool, conta_id: int, codigo: str, conteudo: bytes,
                                  content_type: str, *, prospeccao_id: int | None = None,
                                  subir=None, junto_com: list[str] | None = None,
                                  sinal_centavos: int | None = None,
                                  cliente_id: int | None = None) -> dict:
    """Sobe o arquivo pro bucket PRIVADO dos comprovantes e, se subiu, chama
    `registrar_comprovante`. É o CANO ÚNICO dos dois canais que recebem arquivo
    direto do cliente — a página pública (web/loja_stands.py) e a ferramenta do
    agente no WhatsApp (finance/tools_pj.py) — pra não duplicar validação de
    tipo/tamanho nem o formato do caminho no bucket em dois lugares que um dia
    discordariam.

    `subir` é injetável (testes) — por padrão `finance.comprovantes.subir_em`,
    o MESMO bucket privado que o comprovante de sinal de orçamento já usa (só
    muda o prefixo do caminho: 'stands/' em vez de 'comprovantes/').

    `junto_com`: os OUTROS estandes da mesma reserva (no máximo 1: são 2 por
    empresa, num contrato só). Travam juntos, tudo ou nada, com o mesmo
    comprovante. `sinal_centavos`: o valor do sinal que o cliente disse ter
    pago — o painel confere no comprovante na hora de confirmar. `cliente_id`:
    o cliente que a vendedora cadastrou ANTES (link com `?c=`) — a reserva vai
    pro cadastro dele em vez de nascer um cliente novo pelo WhatsApp.

    Devolve {"ok": False, "erro": "..."} tanto pra upload inválido (arquivo
    vazio, tipo não aceito) quanto pra estande indisponível — quem chama não
    precisa saber em qual das duas etapas falhou pra mostrar a mensagem."""
    caminho, erro = _subir_comprovante(conta_id, codigo, conteudo, content_type, subir)
    if erro:
        return {"ok": False, "erro": erro}
    if junto_com:
        r = registrar_comprovante_grupo(pool, conta_id, [codigo] + list(junto_com), caminho,
                                        prospeccao_id=prospeccao_id)
    else:
        r = registrar_comprovante(pool, conta_id, codigo, caminho, prospeccao_id=prospeccao_id)
    if r.get("ok"):
        # o sinal "pagou" (comprovante na mão): nasce a proposta + o contrato
        # pra assinatura — best-effort, o estande já está travado de qualquer
        # jeito e o contrato é consequência, não condição.
        try:
            extra = garantir_orcamento_e_contrato(pool, conta_id, r["stand"],
                                                  sinal_centavos=sinal_centavos,
                                                  cliente_id=cliente_id)
            if extra:
                r.update(extra)
                r["stand"]["orcamento_id"] = extra["orcamento_id"]
        except Exception as e:  # noqa: BLE001
            _log.warning("evento_stands: comprovante ok mas proposta/contrato do "
                         "%s/%s falhou: %s: %s", conta_id, codigo, type(e).__name__, e)
    return r


# ---------------------------------------------------------------------------
# ATÉ 2 ESTANDES POR EMPRESA, SINAL MÍNIMO E SALDO (aprovado em 30/09/2026)
#
# Uma reserva = 1 ou 2 estandes da mesma empresa, com UM comprovante, UM cadastro,
# UMA proposta e UM contrato. O sinal é no mínimo R$ 1.500 POR ESTANDE; o saldo
# vence na data-limite da config (padrão: o dia do evento). O estande fica
# 'vendido' quando o gestor confirma o sinal; a quitação é uma marca a mais.
# ---------------------------------------------------------------------------

SINAL_MINIMO_PADRAO = 150000
MAX_POR_EMPRESA_PADRAO = 2
OBS_SINAL_ESTANDE = "Sinal — reserva do estande"


def app_de_stands(pool, conta_id: int) -> bool:
    """Esta conta usa o app de VENDA DE ESTANDES (perfil 'stands')? É o portão de tudo
    que o dono pediu só pro Outlet Chic: o app do vendedor, os campos de cliente e
    os campos do contrato de estande. Conta sem a marca (a Prime e as demais) segue
    exatamente como estava. Tolerante: base sem a coluna, ou erro, é 'não'."""
    def _ler():
        try:
            with pool.connection() as c:
                r = c.execute("select app_perfil from contas where id=%s",
                              (conta_id,)).fetchone()
            return bool(r and r[0] == "stands")
        except Exception:  # noqa: BLE001 — sem a coluna, ninguém tem o perfil
            return False
    from db.conexao import memo
    return memo(("app_perfil", conta_id), _ler)


def regras_de_pagamento(cfg: dict | None) -> dict:
    """As regras desta conta, com piso quando a config não trouxe o campo."""
    cfg = cfg or {}
    saldo_ate = cfg.get("saldo_ate") or cfg.get("evento_inicio")
    return {"sinal_minimo_centavos": int(cfg.get("sinal_minimo_centavos")
                                         or SINAL_MINIMO_PADRAO),
            "max_por_empresa": int(cfg.get("max_por_empresa") or MAX_POR_EMPRESA_PADRAO),
            "saldo_ate": saldo_ate}


def _digitos(txt) -> str:
    return "".join(ch for ch in str(txt or "") if ch.isdigit())


def stands_da_empresa(pool, conta_id: int, whatsapp) -> list[dict]:
    """Os estandes que já NÃO estão livres e pertencem à mesma empresa — mesmo
    WhatsApp (últimos 11 dígitos) na prospecção ou no cadastro do cliente. É como
    o limite de 2 por empresa não é burlado abrindo duas reservas separadas."""
    d = _digitos(whatsapp)[-11:]
    if len(d) < 10:
        return []
    with pool.connection() as c:
        rows = c.execute(
            f"""select {', '.join('s.' + x.strip() for x in _COLS.split(','))}
                  from evento_stands s
                  left join prospeccao p on p.id = s.prospeccao_id and p.conta_id = s.conta_id
                  left join clientes cl on cl.id = s.cliente_id and cl.dono_id = s.conta_id
                  left join pessoas pe on pe.id = cl.pessoa_id
                 where s.conta_id=%s and s.status <> 'livre'
                   and (right(regexp_replace(coalesce(p.whatsapp,''), '\\D', '', 'g'), 11) = %s
                     or right(regexp_replace(coalesce(pe.celular,''), '\\D', '', 'g'), 11) = %s)
                 order by s.codigo""",
            (conta_id, d, d)).fetchall()
    return [_fmt(r) for r in rows]


def grupo_do_stand(pool, conta_id: int, stand: dict) -> list[dict]:
    """Os estandes da mesma reserva (inclui o próprio); sozinho, só ele."""
    if not stand.get("grupo_id"):
        return [stand]
    with pool.connection() as c:
        rows = c.execute(
            f"select {_COLS} from evento_stands where conta_id=%s and grupo_id=%s "
            "order by codigo", (conta_id, stand["grupo_id"])).fetchall()
    return [_fmt(r) for r in rows] or [stand]


def validar_reserva(pool, conta_id: int, codigos: list[str], whatsapp,
                    sinal_centavos: int | None = None, cliente_id=None) -> dict:
    """Confere a reserva ANTES de criar prospecção ou subir arquivo. Devolve
    {"ok": True, "stands", "total", "sinal_minimo", "sinal"} ou {"ok": False,
    "erro": "..."} com a frase que a página mostra. `cliente_id` é o cadastro do
    link `?c=`: a empresa que já tem stand nele também conta (02/10/2026 — antes só
    o WhatsApp contava, e o link com outro WhatsApp passava do limite)."""
    cfg = obter_config(pool, conta_id)
    reg = regras_de_pagamento(cfg)
    codigos = [c.strip() for c in codigos if (c or "").strip()]
    if len(set(codigos)) != len(codigos) or not codigos:
        return {"ok": False, "cod": "generico", "erro": "Escolha 1 ou 2 estandes diferentes."}
    if len(codigos) > reg["max_por_empresa"]:
        return {"ok": False, "cod": "max",
                "erro": f"O máximo é {reg['max_por_empresa']} estandes por empresa."}
    stands = []
    for cod in codigos:
        st = buscar(pool, conta_id, cod)
        if st is None:
            return {"ok": False, "cod": "generico", "erro": "Estande não encontrado."}
        if st["status"] != "livre":
            return {"ok": False, "cod": "indisponivel",
                    "erro": f"O estande {cod} não está mais livre."}
        stands.append(st)
    ja = stands_da_empresa(pool, conta_id, whatsapp)
    if cliente_id:
        with pool.connection() as c:
            rows = c.execute(f"select {_COLS} from evento_stands where conta_id=%s "
                             "and cliente_id=%s and status <> 'livre' order by codigo",
                             (conta_id, cliente_id)).fetchall()
        vistos = {x["codigo"] for x in ja}
        ja = ja + [x for x in (_fmt(r) for r in rows) if x["codigo"] not in vistos]
    if ja:
        return {"ok": False, "cod": "empresa", "erro": (
            "Esta empresa já tem o estande " + ", ".join(x["codigo"] for x in ja)
            + " reservado. Cada empresa fecha um contrato só, com no máximo "
            f"{reg['max_por_empresa']} estandes — fale com a organização pelo WhatsApp "
            "para incluir mais um.")}
    total = sum(int(x["preco_centavos"] or 0) for x in stands)
    minimo = reg["sinal_minimo_centavos"] * len(stands)
    sinal = minimo if sinal_centavos is None else int(sinal_centavos)
    if sinal < minimo:
        return {"ok": False, "cod": "sinal", "erro": "O sinal mínimo é " + _reais(minimo)
                + (" (R$ 1.500,00 por estande)." if len(stands) > 1 else ".")}
    if total and sinal > total:
        return {"ok": False, "cod": "sinal",
                "erro": "O sinal não pode passar do valor total da reserva."}
    return {"ok": True, "stands": stands, "total": total, "sinal_minimo": minimo,
            "sinal": sinal}


def registrar_comprovante_grupo(pool, conta_id: int, codigos: list[str],
                                comprovante_url: str,
                                prospeccao_id: int | None = None) -> dict:
    """Trava 2 (ou mais) estandes juntos: TUDO OU NADA, numa transação. Se um deles
    foi reservado por outra pessoa no meio tempo, nenhum trava e o cliente escolhe
    de novo. Devolve {"ok", "stand" (o primeiro), "stands"}."""
    import uuid
    cfg = obter_config(pool, conta_id)
    dias = int((cfg or {}).get("pre_reserva_dias") or PRE_RESERVA_DIAS_PADRAO)
    agora = _ag.agora_brt()
    prazo = agora + timedelta(days=dias)
    grupo = uuid.uuid4().hex[:12]
    with pool.connection() as c:
        rows = c.execute(
            f"""update evento_stands
                   set status='pre_reservado', pre_reserva_ate=%s, comprovante_url=%s,
                       comprovante_em=%s, prospeccao_id=%s, grupo_id=%s,
                       aviso_vence_em=null, atualizado_em=now(),
                       -- reserva nova nasce limpa (ver `registrar_comprovante`)
                       orcamento_id=null, cliente_id=null
                 where conta_id=%s and codigo = any(%s) and status='livre'
                 returning {_COLS}""",
            (prazo, comprovante_url, agora, prospeccao_id, grupo, conta_id,
             list(codigos))).fetchall()
        if len(rows) != len(set(codigos)):
            c.rollback()
            return {"ok": False, "erro": "Um dos estandes acabou de ser reservado por "
                                         "outra pessoa. Escolha de novo."}
        c.commit()
    stands = sorted((_fmt(r) for r in rows), key=lambda x: codigos.index(x["codigo"]))
    _log.info("evento_stands: comprovante do grupo %s — conta %s, estandes %s",
              grupo, conta_id, ",".join(codigos))
    return {"ok": True, "stand": stands[0], "stands": stands}


def plano_de_pagamento(total_centavos: int, sinal_centavos: int, saldo_ate,
                       hoje) -> list[dict]:
    """As parcelas do orçamento do estande: o sinal (paga agora) e o saldo, que
    vence na data-limite. É o formato que `vendas.fechar_orcamento` transforma em
    títulos a receber (o sinal recebe baixa quando o gestor confirma)."""
    total, sinal = int(total_centavos), int(sinal_centavos)
    plano = [{"valor_centavos": sinal, "venc": hoje.isoformat(), "forma": "Pix",
              "obs": OBS_SINAL_ESTANDE}]
    if total - sinal > 0:
        quando = saldo_ate.strftime("%d/%m") if saldo_ate else ""
        plano.append({"valor_centavos": total - sinal,
                      "venc": saldo_ate.isoformat() if saldo_ate else hoje.isoformat(),
                      "forma": "", "obs": "Saldo" + (f" — até {quando}" if quando else "")})
    return plano


def garantir_orcamento_e_contrato(pool, conta_id: int, stand: dict, *,
                                  sinal_centavos: int | None = None,
                                  cliente_id: int | None = None,
                                  cadastro_pronto: bool = False) -> dict | None:
    """Quando o comprovante do SINAL chega, nascem a PROPOSTA e o CONTRATO do
    estande (pedido do dono, 29/09/2026: "colocar o contrato quando pagar o
    sinal") — reaproveitando o motor que a Prime Eventos já usa: a linha vai
    pra `orcamentos` (mesma tabela/token da folha de proposta) e o contrato
    nasce por `finance.contrato.criar_para_orcamento`, com o texto do
    `contrato_modelo` da conta e o link público /contrato/<token> pronto pra
    assinar.

    IDEMPOTENTE: estande que já tem orcamento_id só devolve o contrato que
    existe (reenvio de comprovante não duplica proposta). Sem lojista
    identificado (prospeccao sem nome) devolve None — contrato sem parte não
    faz sentido; o dono cria a proposta à mão no painel se quiser.

    BEST-EFFORT no chamador: o comprovante — a parte que trava o estande — já
    foi registrado; proposta e contrato são consequência, e falhar aqui não
    pode desfazer a reserva."""
    from . import contrato as ctr
    from . import vendas
    if stand.get("orcamento_id"):
        ct = ctr.por_orcamento(pool, conta_id, stand["orcamento_id"])
        return {"orcamento_id": stand["orcamento_id"],
                "contrato_token": (ct or {}).get("token")}
    pid = stand.get("prospeccao_id")
    nome = zap = None
    if pid:
        with pool.connection() as c:
            r = c.execute(
                "select empresa, whatsapp from prospeccao where id=%s and conta_id=%s",
                (pid, conta_id)).fetchone()
        if r:
            nome, zap = (r[0] or "").strip(), r[1]
    if not nome:
        return None

    import json as _json
    import secrets as _secrets
    # 1 OU 2 ESTANDES na mesma reserva: um item por estande, o total é a soma
    # (sem desconto) e o contrato é um só.
    grupo = grupo_do_stand(pool, conta_id, stand)
    preco = sum(int(x.get("preco_centavos") or 0) for x in grupo)
    # O OBJETO do contrato: vira o nome do item da proposta, e é dele que o
    # contrato tira "Espaço locado" e o campo {objeto.descricao} das cláusulas.
    # mesmo shape de item do cockpit/painel: nome + setup em REAIS (total da linha)
    itens = [{"nome": descricao_objeto(x), "setup": int(x.get("preco_centavos") or 0) / 100,
              "mensal": 0} for x in grupo]
    # o EVENTO completo pro quadro "Objeto": tipo (marca + edição), período de
    # vários dias e local (mesmo jsonb da proposta de evento do painel). `data`
    # segue ISO (a data de início) porque é o que o resto do sistema lê.
    cfg = obter_config(pool, conta_id) or {}
    evento = _json.dumps(_evento_da_config(pool, conta_id, cfg)) if cfg.get("evento_inicio") else None
    modo = vendas.modo_do_orcamento(pool, conta_id)
    token = _secrets.token_urlsafe(16)
    # O PLANO: sinal (o que o cliente disse ter pago; o gestor confere no
    # comprovante) + saldo até a data-limite
    reg = regras_de_pagamento(cfg)
    sinal = int(sinal_centavos) if sinal_centavos else reg["sinal_minimo_centavos"] * len(grupo)
    sinal = min(max(sinal, 0), preco) if preco else sinal
    plano = plano_de_pagamento(preco, sinal, reg["saldo_ate"], _ag.agora_brt().date())
    with pool.connection() as c:
        # mesmas colunas que o cockpit garante antes de inserir (espelho das
        # migrações de orcamentos) — base velha não derruba o comprovante
        try:
            from web.painel_servicos import _garantir_tabela
            _garantir_tabela(c)
        except Exception:  # noqa: BLE001 — colunas já existem em produção
            # o DDL falhado deixa a TRANSAÇÃO abortada — sem o rollback, o
            # insert de baixo morreria com InFailedSqlTransaction mesmo com a
            # tabela em ordem.
            try:
                c.rollback()
            except Exception:  # noqa: BLE001
                pass
        # nasce APROVADA, com o aceite datado: mandar o comprovante do sinal É a
        # aprovação do lojista (não existe uma segunda folha pra ele aceitar) —
        # e é o que libera a assinatura do contrato na página pública, em vez de
        # travá-la esperando uma aprovação de proposta que nunca viria.
        r = vendas.com_retry_numero(c, lambda: c.execute(
            f"""insert into orcamentos
                 (conta_id, cliente, empresa, whatsapp, itens, evento,
                  setup_centavos, mensal_centavos, primeiro_ano_centavos,
                  status, aprovada_por, aprovada_em,
                  criado_por, canal, token, modo, parcelas, sinal_centavos, numero)
               values (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,0,%s,
                       'aprovada',%s,now(),
                       'pagina_stands','pagina_stands',%s,%s,%s::jsonb,%s,
                       {vendas.NUMERO_SQL})
               returning id""",
            (conta_id, nome[:200], nome[:200], (zap or None),
             _json.dumps(itens), evento, preco, preco, nome[:120], token, modo,
             _json.dumps(plano), sinal, conta_id)).fetchone())
        if not r:
            return None
        oid = int(r[0])
        c.execute("update evento_stands set orcamento_id=%s, atualizado_em=now() "
                  "where conta_id=%s and codigo = any(%s)",
                  (oid, conta_id, [x["codigo"] for x in grupo]))
        c.execute("update prospeccao set orcamento_id=%s where id=%s and conta_id=%s",
                  (oid, pid, conta_id))
        c.commit()
    # QUEM RESERVA JÁ VIRA CADASTRO (aprovado na maquete, 30/09/2026): nome
    # fantasia + WhatsApp entram em `clientes` agora, sem esperar o gestor. O
    # Zaq junta por WhatsApp — o mesmo lojista comprando um segundo stand não
    # vira dois clientes. O gestor completa o resto em "Dados do cliente".
    # Cliente CADASTRADO ANTES pela vendedora (link com `?c=`): a reserva vai pro
    # cadastro dele, que já traz o que o contrato pede.
    if cliente_id:
        # `cadastro_pronto`: quem chama acabou de salvar o cadastro (o formulário do
        # stand) — o nome do cadastro pode ser o da EMPRESA e a reserva só tem a marca
        _vincular_cliente_da_reserva(pool, conta_id, [x["codigo"] for x in grupo], oid,
                                     int(cliente_id), "" if cadastro_pronto else nome,
                                     None if cadastro_pronto else zap)
    else:
        for x in grupo:
            _garantir_cliente_do_stand(pool, conta_id, x["codigo"], oid, nome, zap)
    ct = ctr.criar_para_orcamento(pool, conta_id, oid, valor_centavos=preco,
                                  criado_por="pagina_stands")
    _log.info("evento_stands: proposta %s + contrato %s nasceram do comprovante do "
              "estande %s/%s", oid, (ct or {}).get("id"), conta_id, stand["codigo"])
    return {"orcamento_id": oid, "contrato_token": (ct or {}).get("token")}


# ---------------------------------------------------------------------------
# O CADASTRO DO CLIENTE DO STAND
#
# Aprovado na maquete (30/09/2026): quem reserva vira cliente na hora; o
# gestor/vendedor completa os dados que o CONTRATO pede; salvar já cria ou
# atualiza o cliente na aba Clientes (sem duplicar) e leva os dados ao
# contrato. O formulário é o de Clientes que já existe, mais razão social e
# representante legal (migração 451).
# ---------------------------------------------------------------------------

#: o que o contrato precisa do lojista. Só a ordem e os rótulos moram aqui — a
#: tela (painel e cockpit) e o aviso de "confirmar pagamento" leem esta lista.
CAMPOS_CONTRATO = (
    ("fantasia", "Nome fantasia"), ("whats", "WhatsApp"), ("razao", "Razão social"),
    ("doc", "CNPJ / CPF"), ("rep", "Representante legal"), ("end", "Endereço"),
    ("cidade", "Cidade"),
)

_ROTULO_TAM = {"tenda": "Espaço em tenda", "personalizado": "Stand personalizado"}


def receita_do_cnpj(doc: str) -> dict:
    """O que o botão "Receita" devolve pros formulários do cliente do stand (painel,
    app e link do contrato) — o cadastro que o CONTRATO pede, como a Receita tem:
    razão social (não o nome fantasia), nome fantasia, endereço completo, CEP,
    cidade, UF, e-mail e o representante sugerido pelo quadro de sócios.
    Antes (até 02/10/2026) as três rotas devolviam só nome/e-mail/cidade/UF, e o
    "nome" era o fantasia: caía no campo Razão social. Sempre {"ok": bool, ...}."""
    from . import cnpj_info, validadoc
    ok, tipo, digitos = validadoc.valida(doc)
    if tipo == "pf":
        return {"ok": False, "erro": "A busca na Receita é só pra CNPJ — com CPF, digite os dados."}
    if tipo != "pj" or not ok:
        return {"ok": False, "erro": "CNPJ inválido — confira os 14 números."}
    info = cnpj_info.consultar_cnpj(digitos)
    if not info:
        return {"ok": False, "erro": "Não consegui achar esse CNPJ na Receita agora — "
                                     "tente de novo ou digite os dados."}
    cep = "".join(c for c in str(info.get("cep") or "") if c.isdigit())
    return {"ok": True,
            "razao": info.get("razao_social") or info.get("nome"),
            "fantasia": info.get("fantasia"),
            "rep": info.get("representante"),
            "end": info.get("endereco_completo"),
            "cep": f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else (cep or None),
            "cidade": info.get("cidade"), "uf": info.get("uf"),
            "email": info.get("email")}


def rotulo_tamanho(tam: str) -> str:
    """'4x3' -> '4x3m'; tenda/personalizado por extenso."""
    return _ROTULO_TAM.get(tam) or (f"{tam}m" if tam else "")


def rotulo_pavilhao(pav: str) -> str:
    """'inferior' -> 'Pavilhão Inferior'; 'outlet_car' -> 'Outlet Car' (não é
    um pavilhão — é o nome da área, como a página pública o mostra)."""
    nome = (pav or "").replace("_", " ").title()
    return f"Pavilhão {nome}" if (pav or "") in ("inferior", "superior") else nome


def descricao_objeto(stand: dict) -> str:
    """O stand como o contrato o descreve: 'Stand G60 — 4x3m — Outlet Grifes
    (Pavilhão Inferior)'."""
    partes = [f"Stand {stand['codigo']}", rotulo_tamanho(stand.get("tamanho") or "")]
    if stand.get("zona"):
        partes.append(stand["zona"])
    txt = " — ".join(p for p in partes if p)
    pav = rotulo_pavilhao(stand.get("pavilhao") or "")
    return f"{txt} ({pav})" if pav else txt


def _periodo(ini, fim) -> str:
    """13/11/2026 + 15/11/2026 -> '13 a 15/11/2026' (mesmo mês) — como um
    contrato escreve o período de uma feira de vários dias."""
    if not ini:
        return ""
    if not fim or fim == ini:
        return ini.strftime("%d/%m/%Y")
    if (ini.year, ini.month) == (fim.year, fim.month):
        return f"{ini:%d} a {fim:%d/%m/%Y}"
    return f"{ini:%d/%m} a {fim:%d/%m/%Y}"


def _evento_da_config(pool, conta_id: int, cfg: dict) -> dict:
    """O jsonb `orcamentos.evento` do estande: tipo, data, período e local."""
    # A MARCA do evento vem do SLUG ("outlet-chic" -> "Outlet Chic"), como na página
    # pública: o nome fantasia da conta é da empresa organizadora (a assessoria), não
    # do evento — saía "ROBERTA ROCHA ASSESSORIA… — 32ª edição" no contrato.
    marca = (cfg.get("slug") or "").replace("-", " ").title()
    tipo = " — ".join(x for x in (marca, cfg.get("edicao_label")) if x)
    return {"tipo": tipo, "data": cfg["evento_inicio"].isoformat(),
            "periodo": _periodo(cfg["evento_inicio"], cfg.get("evento_fim")),
            "local": cfg.get("evento_local") or "",
            "horario": cfg.get("evento_horario") or ""}


def _garantir_cliente_do_stand(pool, conta_id: int, codigo: str, orcamento_id,
                               nome: str, whatsapp) -> int | None:
    """Cria (ou reaproveita, por WhatsApp) o cliente de quem reservou e o liga
    ao stand e à proposta. Best-effort: o comprovante já travou o stand."""
    try:
        from . import clientes as _cli
        r = _cli.salvar_cliente(pool, conta_id, nome, telefone=(whatsapp or None))
        cid = int(r["id"])
        with pool.connection() as c:
            c.execute("update evento_stands set cliente_id=%s, atualizado_em=now() "
                      "where conta_id=%s and codigo=%s", (cid, conta_id, codigo))
            if orcamento_id:
                c.execute("update orcamentos set cliente_id=%s where id=%s and conta_id=%s",
                          (cid, orcamento_id, conta_id))
            c.commit()
        return cid
    except Exception as e:  # noqa: BLE001
        _log.warning("evento_stands: não deu pra criar o cliente do %s/%s: %s: %s",
                     conta_id, codigo, type(e).__name__, e)
        return None


def _vincular_cliente_da_reserva(pool, conta_id: int, codigos: list[str], orcamento_id,
                                 cliente_id: int, nome: str, zap) -> int | None:
    """A reserva pelo link de um cliente CADASTRADO ANTES pela vendedora (`?c=`,
    01/10/2026): os stands e a proposta vão pro cadastro dele. O que ele confirmou
    na página (nome fantasia e WhatsApp) atualiza o cadastro, e o resto (razão
    social, CNPJ, representante, endereço) desce pra proposta — é o que o contrato
    lê —, então a venda já nasce com o cadastro do jeito que estava. Best-effort,
    como o caminho de sempre: o comprovante já travou o stand."""
    from . import clientes as _cli
    from . import validadoc
    digitos_de = lambda s: "".join(ch for ch in str(s or "") if ch.isdigit())  # noqa: E731
    try:
        cli = _cli.obter_clientes(pool, conta_id, [cliente_id]).get(cliente_id)
        if not cli:
            return None
        with pool.connection() as c:
            c.execute("update evento_stands set cliente_id=%s, atualizado_em=now() "
                      "where conta_id=%s and codigo = any(%s)",
                      (cliente_id, conta_id, list(codigos)))
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("evento_stands: não deu pra ligar a reserva %s ao cliente %s: %s: %s",
                     codigos, cliente_id, type(e).__name__, e)
        return None
    fora = False
    try:
        fora = bool(_clientes_de_outra_conta(pool, conta_id, {cliente_id: cli}))
        novo = {}
        if (nome or "").strip() and (nome or "").strip() != (cli.get("nome") or "").strip():
            novo["nome"] = nome.strip()
        if digitos_de(zap) and digitos_de(zap) != digitos_de(cli.get("telefone")):
            novo["telefone"] = zap
        if novo and fora:
            # a pessoa também é cliente de OUTRA conta: nome e WhatsApp dela são o que a
            # outra conta registrou (a identidade é única no sistema). Os desta reserva
            # já ficam na prospecção
            novo = {}
        if novo:
            _cli.atualizar_cliente(pool, conta_id, cliente_id, **novo)
            cli = _cli.obter_clientes(pool, conta_id, [cliente_id]).get(cliente_id) or cli
    except Exception as e:  # noqa: BLE001 — o cadastro como estava continua valendo
        _log.info("evento_stands: o cliente %s não aceitou o nome/WhatsApp da página: %s: %s",
                  cliente_id, type(e).__name__, e)
    try:
        doc = cli.get("documento_fmt") or ""
        tipo, digitos = validadoc.classificar(doc) if doc else (None, "")
        g = lambda k: (cli.get(k) or "").strip()  # noqa: E731
        campos = {"nome": g("nome") or (nome or "").strip(), "telefone": g("telefone"),
                  "email": g("email"), "endereco": g("endereco"), "cep": g("cep"),
                  "cidade": g("cidade"), "uf": g("uf"), "obs": g("obs"),
                  "razao_social": g("razao_social"), "representante": g("representante")}
        if fora:
            # contato que a OUTRA conta registrou não vai pro contrato desta: vai o que o
            # lojista confirmou na página (a mesma regra de `_campos_da_proposta`)
            campos.update(nome=(nome or "").strip() or campos["nome"],
                          telefone=(zap or "").strip(), email="")
        _espelhar_no_orcamento(pool, conta_id, orcamento_id, cliente_id, campos,
                               tipo if tipo in ("pf", "pj") else None, digitos, False)
    except Exception as e:  # noqa: BLE001
        _log.warning("evento_stands: não deu pra levar o cadastro %s pra proposta %s: %s: %s",
                     cliente_id, orcamento_id, type(e).__name__, e)
    return cliente_id


def _montar_cadastro(d: dict, cliente_id=None) -> dict:
    """Normaliza os campos do cadastro e diz o que ainda falta pro contrato."""
    cad = {"fantasia": d.get("fantasia") or "", "whats": d.get("whats") or "",
           "razao": d.get("razao") or "", "doc": d.get("doc") or "",
           "rep": d.get("rep") or "", "email": d.get("email") or "",
           "end": d.get("end") or "", "cep": d.get("cep") or "",
           "cidade": d.get("cidade") or "", "uf": d.get("uf") or "",
           "obs": d.get("obs") or "", "cliente_id": cliente_id}
    cad["faltam"] = [{"k": k, "l": rot} for k, rot in CAMPOS_CONTRATO
                     if not str(cad.get(k) or "").strip()]
    cad["n_total"] = len(CAMPOS_CONTRATO)
    cad["n_ok"] = cad["n_total"] - len(cad["faltam"])
    return cad


def _cadastro_do_cliente(c: dict, marca: str | None = None, whats: str | None = None,
                         email: str | None = None) -> dict:
    """Um cliente de `clientes` no formato do cadastro do stand. `marca` é o nome
    fantasia DESTE stand (o da reserva); `whats`/`email`, quando vêm, substituem os
    do cadastro (ver `cadastros_dos_stands`)."""
    return _montar_cadastro({
        "fantasia": (marca or "").strip() or c.get("nome"),
        "whats": c.get("telefone") if whats is None else whats,
        "razao": c.get("razao_social"), "doc": c.get("documento_fmt") or "",
        "rep": c.get("representante"),
        "email": c.get("email") if email is None else email,
        "end": c.get("endereco"), "cep": c.get("cep"),
        "cidade": c.get("cidade"), "uf": c.get("uf"), "obs": c.get("obs")},
        cliente_id=c["id"])


def cadastros_dos_stands(pool, conta_id: int, stands: list[dict]) -> dict[str, dict]:
    """{codigo: cadastro} dos stands que NÃO estão livres — lido de `clientes`
    (o cadastro de verdade) e, enquanto o cliente não existir, da prospecção
    (só nome e WhatsApp). Em lote: a lista de estandes mostra tudo de uma vez.

    A MARCA (02/10/2026). O nome fantasia de cada stand é o da RESERVA — a
    `prospeccao.empresa`, que o formulário grava a cada salvar —, e o nome do
    cadastro só quando a reserva não tem nome. Assim a mesma empresa (um CNPJ) tem
    até 2 stands com marcas diferentes (EM ESSENCE no i04, OCEAN BEACH no S97):
    razão social, documento, representante, contato e endereço são do cadastro,
    que é um só; a marca é de cada stand, e não se perde quando um dos stands é
    liberado ou separado. `dividido_com` lista os stands das OUTRAS reservas do
    mesmo cadastro.

    Quem também é cliente de OUTRA conta (a identidade em `pessoas` é única no
    sistema): WhatsApp e e-mail mostrados são os desta reserva (prospecção e
    proposta), nunca os que a outra conta registrou."""
    from . import clientes as _cli
    ocup = [s for s in stands if s["status"] != "livre"]
    clientes = _cli.obter_clientes(pool, conta_id,
                                   [s.get("cliente_id") for s in ocup])
    divididos = _clientes_divididos(pool, conta_id, list(clientes))
    de_fora = _clientes_de_outra_conta(pool, conta_id, clientes)
    pids = [s["prospeccao_id"] for s in ocup if s.get("prospeccao_id")]
    prosp = {}
    if pids:
        with pool.connection() as c:
            prosp = {r[0]: r for r in c.execute(
                "select id, empresa, whatsapp from prospeccao "
                "where conta_id=%s and id = any(%s)", (conta_id, pids)).fetchall()}
    emails = {}
    oids = [s["orcamento_id"] for s in ocup
            if s.get("orcamento_id") and s.get("cliente_id") in de_fora]
    if oids:
        with pool.connection() as c:
            emails = {r[0]: r[1] for r in c.execute(
                "select id, email from orcamentos where conta_id=%s and id = any(%s)",
                (conta_id, oids)).fetchall()}
    out = {}
    for s in ocup:
        p = prosp.get(s.get("prospeccao_id"))
        c = clientes.get(s.get("cliente_id"))
        if not c:
            out[s["codigo"]] = _montar_cadastro(
                {"fantasia": p[1] if p else "", "whats": p[2] if p else ""})
            continue
        fora = c["id"] in de_fora
        cad = _cadastro_do_cliente(
            c, marca=p[1] if p else None,
            whats=((p[2] if p else "") or "") if fora else None,
            email=(emails.get(s.get("orcamento_id")) or "") if fora else None)
        mapa = divididos.get(c["id"])
        if mapa:
            minha = mapa.get(s["codigo"])
            cad["dividido_com"] = [cod for cod, r in mapa.items() if r != minha]
        out[s["codigo"]] = cad
    return out


def _clientes_divididos(pool, conta_id: int, cliente_ids: list[int]) -> dict[int, dict[str, int]]:
    """{cliente_id: {codigo: reserva}} dos cadastros que servem a MAIS DE UMA reserva
    (propostas diferentes). Dois stands da mesma reserva são uma marca só."""
    ids = [int(i) for i in cliente_ids if i]
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            "select cliente_id, codigo, coalesce(orcamento_id, -id) from evento_stands "
            "where conta_id=%s and cliente_id = any(%s) and status <> 'livre' order by codigo",
            (conta_id, ids)).fetchall()
    por: dict[int, dict[str, int]] = {}
    for cid, codigo, reserva in rows:
        por.setdefault(cid, {})[codigo] = reserva
    return {cid: m for cid, m in por.items() if len(set(m.values())) > 1}


def _clientes_de_outra_conta(pool, conta_id: int, clientes: dict) -> set[int]:
    """Os cadastros cuja pessoa (identidade) também é cliente de OUTRA conta: nome,
    WhatsApp e e-mail dela são o que a outra conta registrou — esta conta não lê nem
    troca (ver `cadastros_dos_stands` e `salvar_cadastro_stand`)."""
    pessoas: dict[int, list[int]] = {}
    for cid, c in clientes.items():
        if c and c.get("pessoa_id"):
            pessoas.setdefault(c["pessoa_id"], []).append(cid)
    if not pessoas:
        return set()
    with pool.connection() as c:
        rows = c.execute("select distinct pessoa_id from clientes where pessoa_id = any(%s) "
                         "and dono_id <> %s", (list(pessoas), conta_id)).fetchall()
    return {cid for r in rows for cid in pessoas[r[0]]}


def _outras_reservas(pool, conta_id: int, cliente_id, orcamento_id, codigo: str) -> list[dict]:
    """Os stands ocupados de OUTRAS reservas ligados a este cliente:
    [{codigo, vendedor_id, orcamento_id, prospeccao_id}]. Os stands da mesma
    proposta são a mesma reserva."""
    if not cliente_id:
        return []
    with pool.connection() as c:
        rows = c.execute(
            """select s.codigo, pr.vendedor_id, s.orcamento_id, s.prospeccao_id
                 from evento_stands s
                 left join prospeccao pr on pr.id = s.prospeccao_id and pr.conta_id = s.conta_id
                where s.conta_id=%s and s.cliente_id=%s and s.status <> 'livre'
                  and s.codigo <> %s
                  and (%s::bigint is null or s.orcamento_id is distinct from %s::bigint)
                order by s.codigo""",
            (conta_id, cliente_id, codigo, orcamento_id, orcamento_id)).fetchall()
    return [{"codigo": r[0], "vendedor_id": r[1], "orcamento_id": r[2],
             "prospeccao_id": r[3]} for r in rows]


def _dono_do_documento(pool, conta_id: int, tipo: str, digitos: str) -> dict | None:
    """Quem já tem este CPF/CNPJ: {"pessoa_id", "cliente_id", "vendedor_id"}. A pessoa
    (identidade) é única no sistema; `cliente_id` é o cadastro ATIVO dela nesta conta
    — None quando ela não tem (só é cliente de outra conta, ou o daqui foi arquivado)."""
    col = "cnpj" if tipo == "pj" else "cpf"
    with pool.connection() as c:
        p = c.execute(f"select id from pessoas where {col}=%s", (digitos,)).fetchone()
        if not p:
            return None
        cl = c.execute("select id, vendedor_id from clientes where pessoa_id=%s and dono_id=%s "
                       "and ativo order by id limit 1", (p[0], conta_id)).fetchone()
    return {"pessoa_id": p[0], "cliente_id": cl[0] if cl else None,
            "vendedor_id": cl[1] if cl else None}


def _arquivar_se_sobrou(pool, conta_id: int, cliente_id) -> bool:
    """O cadastro provisório do stand (só nome, sem documento) que ficou sem nada
    depois que o stand entrou no cadastro da empresa: sai da lista de Clientes
    (arquivado, não apagado). Um documento, a carteira de uma vendedora ou
    QUALQUER linha que aponte pra ele — procurada no catálogo, em toda tabela com
    `cliente_id` (stands, propostas, títulos, lançamentos, agenda, apólices…) — e
    ele fica como está."""
    from . import clientes as _cli
    if not cliente_id:
        return False
    try:
        with pool.connection() as c:
            r = c.execute(
                "select coalesce(p.cnpj, p.cpf), c.vendedor_id from clientes c "
                "left join pessoas p on p.id = c.pessoa_id where c.id=%s and c.dono_id=%s and c.ativo",
                (cliente_id, conta_id)).fetchone()
            if not r or r[0] or r[1]:
                return False
            tabelas = c.execute(
                """select table_name, bool_or(column_name = 'conta_id'),
                          bool_or(column_name = 'dono_id')
                     from information_schema.columns
                    where table_schema = 'public'
                      and table_name in (select table_name from information_schema.columns
                                          where table_schema = 'public' and column_name = 'cliente_id')
                    group by table_name""").fetchall()
            for tabela, tem_conta, tem_dono in tabelas:
                q = f'select 1 from "{tabela}" where cliente_id=%s'
                if tem_conta:
                    q += f" and conta_id={int(conta_id)}"
                elif tem_dono:
                    q += f" and dono_id={int(conta_id)}"
                if c.execute(q + " limit 1", (cliente_id,)).fetchone():
                    return False
        return _cli.arquivar_cliente(pool, conta_id, int(cliente_id))
    except Exception as e:  # noqa: BLE001 — limpeza: sem ela o cadastro só fica na lista
        _log.info("evento_stands: não arquivei o cadastro provisório %s: %s: %s",
                  cliente_id, type(e).__name__, e)
        return False


def expositores_publicos(pool, conta_id: int, stands: list[dict], *,
                        reservados_tambem: bool = False) -> dict[str, str]:
    """{codigo: nome fantasia} dos stands que a página pública pode nomear.

    Por padrão só aparece quem CUMPRIU os dois passos: o pagamento foi confirmado
    (status 'vendido') e o contrato foi ASSINADO. Reservado ou com contrato
    pendente continua anônimo — nome de empresa na vitrine pública é o sinal de
    que o espaço é dela de fato, e não uma intenção. O nome é a MARCA do stand (o
    fantasia do formulário 'Dados do cliente', ver `cadastros_dos_stands`) —
    nunca razão social, documento ou contato.

    `reservados_tambem` (02/10/2026, pedido do dono da Outlet Chic, só pra ela —
    o portão é o slug em web/loja_stands): todo stand ocupado, reservado ou
    vendido, mostra a marca de quem está nele."""
    if reservados_tambem:
        ocup = [s for s in stands if s["status"] != "livre"]
        if not ocup:
            return {}
        cads = cadastros_dos_stands(pool, conta_id, ocup)
        return {s["codigo"]: cads[s["codigo"]]["fantasia"] for s in ocup
                if (cads.get(s["codigo"], {}).get("fantasia") or "").strip()}
    vend = [s for s in stands if s["status"] == "vendido" and s.get("orcamento_id")]
    if not vend:
        return {}
    with pool.connection() as c:
        assinados = {r[0] for r in c.execute(
            "select orcamento_id from contratos where conta_id=%s and orcamento_id = any(%s) "
            "and assinado_em is not null and substitui_id is null",
            (conta_id, [s["orcamento_id"] for s in vend])).fetchall()}
    vend = [s for s in vend if s["orcamento_id"] in assinados]
    if not vend:
        return {}
    cads = cadastros_dos_stands(pool, conta_id, vend)
    return {s["codigo"]: cads[s["codigo"]]["fantasia"] for s in vend
            if cads.get(s["codigo"], {}).get("fantasia")}


# o que o formulário do stand grava no cadastro (chave de `campos` = chave do cliente)
_CAMPOS_DO_CADASTRO = ("nome", "telefone", "email", "endereco", "cep", "cidade", "uf",
                       "obs", "razao_social", "representante")


def _campos_da_proposta(cli: dict, marca: str, telefone=None, email=None) -> dict:
    """O que `_espelhar_no_orcamento` leva pra proposta: o cadastro como ficou, com a
    marca da reserva no lugar do nome (e o contato da reserva, quando vem)."""
    campos = {k: (cli.get(k) or "") for k in _CAMPOS_DO_CADASTRO}
    campos["nome"] = marca or campos["nome"]
    if telefone is not None:
        campos["telefone"] = telefone
    if email is not None:
        campos["email"] = email
    return campos


def salvar_cadastro_stand(pool, conta_id: int, codigo: str, dados: dict, *,
                          juntar: str = "sempre", vendedor_id=None) -> dict:
    """Salva o formulário "Dados do cliente" do stand.

    Cria o cliente na aba Clientes se ele ainda não existe (dedup por CNPJ/CPF
    ou WhatsApp, o de sempre do Zaq) e SOBRESCREVE com o que a pessoa digitou —
    é uma edição explícita, não um enriquecimento. Depois liga o stand e a
    proposta ao cliente e, se o contrato AINDA NÃO foi assinado, leva os dados
    pra ele (o assinado é documento congelado). O nome fantasia é a MARCA do
    stand e vai também pra prospecção da reserva (ver `cadastros_dos_stands`).
    Devolve {"ok", "cliente_id", "acao", "faltam", "congelado", "juntou"} ou
    {"ok": False, "erro"}.

    MESMA EMPRESA, DOIS STANDS (02/10/2026). A vendedora pôs no S97 (OCEAN BEACH)
    o CNPJ que já estava no i04 (EM ESSENCE): mesma dona, duas lojas. Antes isso
    batia na unicidade do CNPJ, sem saída. Agora o stand ENTRA no cadastro que já
    tem o documento (`juntou` diz qual e com que stands), respeitando o limite de
    stands por empresa do evento (2), e o cadastro provisório que ele tinha é
    arquivado. Regras de gravação, pra nenhum stand apagar o que é da empresa:
      - ENTRAR num cadastro que já existe só COMPLETA o que a empresa não tem
        (o formulário do provisório costuma vir vazio, e vazio não apaga);
      - num cadastro que serve a mais de uma reserva, o salvar troca o que veio
        preenchido e campo vazio não apaga; o nome do cadastro não muda (a marca
        vai pra prospecção) e as propostas NÃO assinadas das outras reservas
        recebem os dados da empresa;
      - trocar o documento de um stand de cadastro dividido (que já tinha
        documento) separa: ele ganha cadastro próprio, o outro fica como está;
      - quem também é cliente de OUTRA conta: nome, WhatsApp e e-mail da
        identidade nunca são gravados daqui (ficam na prospecção e na proposta).
    `juntar` diz quem pode entrar no cadastro de outra reserva:
        "sempre"       gestão (painel, ou dono/gestor no app)
        "do_vendedor"  a vendedora (de qualquer venda: a única regra é o limite)
        "nunca"        o link público do contrato — ninguém entra no cadastro de
                       outra loja (nem vê os dados dela) digitando um CPF/CNPJ"""
    from . import clientes as _cli
    from . import contrato as _ctr
    from . import validadoc
    stand = buscar(pool, conta_id, codigo)
    if not stand:
        return {"ok": False, "erro": "Estande não encontrado."}
    if stand["status"] == "livre":
        return {"ok": False, "erro": "Este estande ainda está livre — o cadastro nasce "
                                     "quando o comprovante chega."}
    g = lambda k: (dados.get(k) or "").strip()  # noqa: E731
    fantasia = g("fantasia")
    if not fantasia:
        return {"ok": False, "erro": "Preencha o nome fantasia."}
    doc = g("doc")
    tipo, digitos = validadoc.classificar(doc) if doc else (None, "")
    if doc and tipo not in ("pf", "pj"):
        return {"ok": False, "erro": "Documento deve ter 11 (CPF) ou 14 (CNPJ) dígitos."}
    if tipo == "pf" and not validadoc.valida_cpf(digitos):
        return {"ok": False, "erro": "CPF invalido"}
    if tipo == "pj" and not validadoc.valida_cnpj(digitos):
        return {"ok": False, "erro": "CNPJ invalido"}
    campos = {"nome": fantasia, "telefone": g("whats"), "email": g("email"),
              "endereco": g("end"), "cep": g("cep"), "cidade": g("cidade"),
              "uf": g("uf"), "obs": g("obs"),
              "razao_social": g("razao"), "representante": g("rep")}
    if tipo == "pf":
        campos["cpf"] = digitos
    elif tipo == "pj":
        campos["cnpj"] = digitos
    nome_doc = "CPF" if tipo == "pf" else "CNPJ"
    if not stand.get("prospeccao_id"):
        # STAND SEM RESERVA REGISTRADA (03/10/2026): o comprovante entrou pelo painel
        # sem o nome do lojista e o stand ficou sem prospecção — sem marca, sem
        # vendedora e sem proposta. Salvar o cadastro registra a reserva com o nome
        # fantasia (e a vendedora, quando é ela que salva)
        pid = _registrar_reserva_do_stand(
            pool, conta_id, stand, fantasia, g("whats"),
            vendedor_id if juntar == "do_vendedor" else None)
        if pid:
            stand["prospeccao_id"] = pid
    oid = stand.get("orcamento_id")
    cid = stand.get("cliente_id")
    atual = _cli.obter_cliente(pool, conta_id, cid) if cid else None
    if cid and not atual:
        cid = None                      # o cadastro do stand foi arquivado: começa de novo
    acao = "atualizado"
    juntou = None
    entrou = False                      # o stand ENTRA num cadastro que já existe
    with pool.connection() as c:        # quem o stand/reserva apontava antes (pra arquivar)
        antes = {r[0] for r in c.execute(
            "select distinct cliente_id from evento_stands where conta_id=%s "
            "and cliente_id is not null and (codigo=%s or (%s::bigint is not null "
            "and orcamento_id=%s::bigint and status <> 'livre'))",
            (conta_id, codigo, oid, oid)).fetchall()}

    try:
        # 1. pra qual cadastro o stand vai
        alvo, vend_alvo = None, None
        if digitos and digitos != ((atual or {}).get("documento") or ""):
            dono = _dono_do_documento(pool, conta_id, tipo, digitos)
            if dono and dono["pessoa_id"] != (atual or {}).get("pessoa_id"):
                if dono["cliente_id"]:
                    alvo, vend_alvo = int(dono["cliente_id"]), dono["vendedor_id"]
                elif juntar == "nunca":
                    # o link público: digitando um CPF/CNPJ ninguém puxa pra esta conta
                    # quem é cliente de outra (ou tem o cadastro daqui arquivado)
                    return {"ok": False, "erro": _pode_juntar("nunca", None, None, [], None)}
                else:
                    # a pessoa existe mas não tem cadastro ativo AQUI (é cliente de outra
                    # conta, ou o daqui foi arquivado): nasce o cadastro desta conta pra
                    # ela. É ENTRAR numa identidade que já existe: só completa o que falta,
                    # nunca troca o nome nem apaga contato
                    cid = _cli.puxar_ou_criar_cliente(pool, conta_id,
                                                      pessoa_id=int(dono["pessoa_id"]))
                    entrou, acao = True, "criado"
            elif not dono and (atual or {}).get("documento"):
                # documento NOVO num stand cujo cadastro já tinha outro: se o cadastro é
                # dividido com outra reserva, ou é a identidade de um cliente de OUTRA
                # conta, o stand sai e ganha cadastro próprio (o outro fica igual)
                outras_atual = _outras_reservas(pool, conta_id, cid, oid, codigo)
                fora_atual = bool(_clientes_de_outra_conta(pool, conta_id, {cid: atual}))
                if outras_atual or fora_atual:
                    cid = int(_cli.salvar_cliente(pool, conta_id, fantasia,
                                                  cpf=campos.get("cpf"),
                                                  cnpj=campos.get("cnpj"))["id"])
                    acao = "criado"
                    if outras_atual and not fora_atual:
                        _rebatizar_o_que_ficou(pool, conta_id, atual, stand, outras_atual)
        elif not cid and not digitos and g("whats"):
            achado = _cli.buscar_por_telefone(pool, conta_id, g("whats"))
            if achado:
                alvo, vend_alvo = int(achado["id"]), achado.get("vendedor_id")
                if vend_alvo is None:
                    with pool.connection() as c:
                        r = c.execute("select vendedor_id from clientes where id=%s and dono_id=%s",
                                      (alvo, conta_id)).fetchone()
                    vend_alvo = r[0] if r else None

        if alvo and alvo != cid:
            if not stand.get("prospeccao_id"):
                # sem a reserva registrada não há onde guardar a marca deste stand: ele
                # apareceria com o nome da outra loja
                return {"ok": False, "erro": "Este stand não tem a reserva registrada, então "
                        "não dá pra pôr ele no cadastro de outra loja — fale com a gestão."}
            outras = _outras_reservas(pool, conta_id, alvo, oid, codigo)
            cli_alvo = _cli.obter_cliente(pool, conta_id, alvo) or {}
            erro = (_pode_juntar(juntar, vendedor_id, vend_alvo, outras,
                                 cli_alvo.get("nome"))
                    or _cabe_na_empresa(pool, conta_id, outras, oid, codigo))
            if erro:
                return {"ok": False, "erro": erro}
            cid, entrou, acao = alvo, True, "juntado"
            juntou = {"cliente": cli_alvo.get("nome") or "",
                      "stands": [o["codigo"] for o in outras], "doc": nome_doc}

        if not cid:
            r = _cli.salvar_cliente(pool, conta_id, fantasia, telefone=g("whats") or None,
                                    cpf=campos.get("cpf"), cnpj=campos.get("cnpj"))
            cid, acao = int(r["id"]), r["acao"]

        # 2. o que grava no cadastro
        cli = _cli.obter_cliente(pool, conta_id, cid) or {}
        compartilhado = bool(_outras_reservas(pool, conta_id, cid, oid, codigo))
        de_fora = bool(_clientes_de_outra_conta(pool, conta_id, {cid: cli}))
        gravar = dict(campos)
        if entrou:
            # entrar só completa: o que a empresa já tem, fica
            gravar = {k: v for k, v in gravar.items()
                      if k in _CAMPOS_DO_CADASTRO and v and not (cli.get(k) or "").strip()}
        elif compartilhado:
            gravar = {k: v for k, v in gravar.items() if v}      # vazio não apaga
        if entrou or compartilhado:
            # o nome do cadastro não muda: a marca deste stand vai pra prospecção dele
            gravar.pop("nome", None)
        if de_fora:
            for k in ("nome", "telefone", "email", "cpf", "cnpj"):
                gravar.pop(k, None)
        if gravar and not entrou:
            _cli.atualizar_cliente(pool, conta_id, cid, **gravar)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    except Exception as e:  # noqa: BLE001 — ex.: CPF/CNPJ já é de OUTRO cliente
        if type(e).__name__ == "UniqueViolation":
            return {"ok": False, "erro": "Esse CPF/CNPJ já está cadastrado em outro "
                                         "cliente — abra o cadastro dele em Clientes."}
        raise

    # 3. liga a reserva inteira ao cadastro; a marca e o WhatsApp vão pra prospecção.
    # No cadastro dividido o formulário mostra o WhatsApp da EMPRESA: o mesmo número de
    # volta não é o contato desta loja e não troca o da reserva
    zap_reserva = g("whats")
    if (compartilhado and not entrou
            and _so_digitos(zap_reserva) == _so_digitos(cli.get("telefone"))):
        zap_reserva = ""
    with pool.connection() as c:
        if entrou:
            # trava o cadastro e confere o limite de novo: dois salvares ao mesmo tempo
            # no mesmo CNPJ não passam juntos do máximo por empresa. Conta a EMPRESA (a
            # pessoa): stands de um cadastro dela arquivado, ou de outro cadastro dela
            # nesta conta, também contam — "1 CNPJ só pode 2 stands"
            # pessoa ANTES do cadastro: a mesma ordem de `clientes.atualizar_cliente`
            # (update pessoas, depois clientes) — na ordem inversa as duas se travavam
            c.execute("select id from pessoas where id=(select pessoa_id from clientes "
                      "where id=%s and dono_id=%s) for update", (cid, conta_id))
            c.execute("select id from clientes where id=%s and dono_id=%s for update",
                      (cid, conta_id))
            n = c.execute(
                "select count(*) from evento_stands where conta_id=%s and status <> 'livre' "
                "and (cliente_id=%s or cliente_id in (select id from clientes where dono_id=%s "
                "and pessoa_id=(select pessoa_id from clientes where id=%s and dono_id=%s))) "
                "and codigo <> %s and (%s::bigint is null "
                "or orcamento_id is distinct from %s::bigint)",
                (conta_id, cid, conta_id, cid, conta_id, codigo, oid, oid)).fetchone()[0]
            desta = c.execute(
                "select count(*) from evento_stands where conta_id=%s and status <> 'livre' "
                "and (codigo=%s or (%s::bigint is not null and orcamento_id=%s::bigint))",
                (conta_id, codigo, oid, oid)).fetchone()[0]
            maximo = regras_de_pagamento(obter_config(pool, conta_id))["max_por_empresa"]
            if int(n) + int(desta or 1) > maximo:
                c.rollback()
                return {"ok": False, "erro": f"Essa empresa já tem {n} stand"
                        f"{'s' if n != 1 else ''} — o máximo é {maximo} por empresa. "
                        "Use outro CPF/CNPJ ou fale com a organização."}
        c.execute("update evento_stands set cliente_id=%s, atualizado_em=now() "
                  "where conta_id=%s and (codigo=%s or (%s::bigint is not null "
                  "and orcamento_id=%s::bigint and status <> 'livre'))",
                  (cid, conta_id, codigo, oid, oid))
        if oid:
            # o vínculo da proposta vai junto, na mesma transação: se cair depois daqui,
            # nada mais aponta pro cadastro antigo e ele ainda é arquivado no próximo salvar
            c.execute("update orcamentos set cliente_id=%s where id=%s and conta_id=%s",
                      (cid, oid, conta_id))
        if stand.get("prospeccao_id"):
            c.execute("update prospeccao set empresa=%s, "
                      "whatsapp=coalesce(nullif(%s, ''), whatsapp) "
                      "where id=%s and conta_id=%s",
                      (fantasia[:200], zap_reserva[:40],
                       stand["prospeccao_id"], conta_id))
        c.commit()
    if entrou and gravar:
        # só agora, com o stand já dentro do limite (a trava acima): um salvar recusado
        # não deixa na empresa o e-mail/CEP de outra loja
        try:
            _cli.atualizar_cliente(pool, conta_id, cid, **gravar)
        except Exception as e:  # noqa: BLE001 — completar é extra: a empresa fica como estava
            _log.warning("evento_stands: não completei o cadastro %s pelo %s: %s: %s",
                         cid, codigo, type(e).__name__, e)
    # o cadastro provisório que o stand tinha (só nome, sem vínculo) sai da lista
    for velho in antes - {cid}:
        _arquivar_se_sobrou(pool, conta_id, velho)

    if not oid and stand["status"] == "pre_reservado" and stand.get("comprovante_url"):
        # o comprovante chegou, mas a proposta não nasceu (veio sem o nome do lojista):
        # nasce agora, com o contrato, já no cadastro que acabou de ser salvo
        try:
            extra = garantir_orcamento_e_contrato(
                pool, conta_id, buscar(pool, conta_id, codigo) or stand,
                cliente_id=cid, cadastro_pronto=True)
            oid = (extra or {}).get("orcamento_id") or oid
        except Exception as e:  # noqa: BLE001 — o cadastro já foi salvo
            _log.warning("evento_stands: cadastro do %s/%s salvo, mas a proposta não "
                         "nasceu: %s: %s", conta_id, codigo, type(e).__name__, e)

    # 4. as propostas: a deste stand e as das outras reservas do mesmo cadastro
    cli = _cli.obter_cliente(pool, conta_id, cid) or {}
    tipo_c, dig_c = (validadoc.classificar(cli["documento"])
                     if cli.get("documento") else (None, ""))
    de_fora = bool(_clientes_de_outra_conta(pool, conta_id, {cid: cli}))

    def _assinado(o):
        try:
            return _ctr.assinado_do_orcamento(pool, conta_id, o)
        except Exception:  # noqa: BLE001 — base sem a 164: segue como não assinado
            return False

    congelado = False
    if oid:
        congelado = _assinado(oid)
        _espelhar_no_orcamento(
            pool, conta_id, oid, cid,
            _campos_da_proposta(cli, fantasia,
                                telefone=g("whats") if de_fora else None,
                                email=g("email") if de_fora else None),
            tipo_c, dig_c, congelado)
    outras = _outras_reservas(pool, conta_id, cid, oid, codigo)
    feitas = {oid}
    for o in outras:
        o2 = o.get("orcamento_id")
        if not o2 or o2 in feitas:
            continue
        feitas.add(o2)
        with pool.connection() as c:
            r = c.execute("select p.empresa, p.whatsapp, o.email from orcamentos o "
                          "left join prospeccao p on p.id = %s and p.conta_id = o.conta_id "
                          "where o.id=%s and o.conta_id=%s",
                          (o.get("prospeccao_id"), o2, conta_id)).fetchone()
        marca2, zap2, email2 = (r or (None, None, None))
        _espelhar_no_orcamento(
            pool, conta_id, o2, cid,
            _campos_da_proposta(cli, marca2 or "",
                                telefone=(zap2 or "") if de_fora else None,
                                email=(email2 or "") if de_fora else None),
            tipo_c, dig_c, _assinado(o2))

    cad = cadastros_dos_stands(pool, conta_id, [dict(stand, cliente_id=cid)])[codigo]
    return {"ok": True, "cliente_id": cid, "acao": acao,
            "faltam": cad["faltam"], "congelado": congelado, "juntou": juntou}


def _so_digitos(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _mesmo_nome(a, b) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def _marca_da_reserva(pool, conta_id: int, prospeccao_id) -> str:
    if not prospeccao_id:
        return ""
    with pool.connection() as c:
        r = c.execute("select empresa from prospeccao where id=%s and conta_id=%s",
                      (prospeccao_id, conta_id)).fetchone()
    return ((r[0] if r else "") or "").strip()


def _rebatizar_o_que_ficou(pool, conta_id: int, atual: dict, stand: dict,
                           outras: list[dict]) -> None:
    """Quem SAIU de um cadastro dividido foi o stand que dava nome a ele (o cadastro
    se chamava como a marca deste stand): o cadastro, que fica com o documento e com
    as outras reservas, passa a ter o nome da marca de uma delas — senão a aba
    Clientes mostra duas lojas com o mesmo nome, e o documento antigo na errada."""
    from . import clientes as _cli
    try:
        minha = _marca_da_reserva(pool, conta_id, stand.get("prospeccao_id"))
        if not minha or not _mesmo_nome(minha, atual.get("nome")):
            return
        for o in outras:
            outra = _marca_da_reserva(pool, conta_id, o.get("prospeccao_id"))
            if outra and not _mesmo_nome(outra, minha):
                _cli.atualizar_cliente(pool, conta_id, int(atual["id"]), nome=outra)
                return
    except Exception as e:  # noqa: BLE001 — é só o nome na lista: o resto já está certo
        _log.info("evento_stands: não rebatizei o cadastro %s: %s: %s",
                  atual.get("id"), type(e).__name__, e)


def nome_do_cadastro_mudou(pool, conta_id: int, cliente_id: int, antigo, novo) -> int:
    """A gestão corrigiu o NOME de um cliente na aba Clientes. A marca de cada stand
    mora na reserva (`prospeccao.empresa`, ver `cadastros_dos_stands`): a reserva
    cuja marca era o nome antigo passa a ter o novo — o stand, a vitrine e o próximo
    salvar do formulário mostram a correção, em vez de devolver o nome velho pro
    cadastro. A marca própria de um stand que entrou na mesma empresa (diferente do
    nome do cadastro) fica como está. Devolve quantas reservas mudaram."""
    antigo, novo = (antigo or "").strip(), (novo or "").strip()
    if not novo or _mesmo_nome(antigo, novo):
        return 0
    with pool.connection() as c:
        rows = c.execute(
            """select distinct p.id, p.empresa from evento_stands s
                 join prospeccao p on p.id = s.prospeccao_id and p.conta_id = s.conta_id
                where s.conta_id=%s and s.cliente_id=%s and s.status <> 'livre'""",
            (conta_id, cliente_id)).fetchall()
        ids = [pid for pid, emp in rows if not (emp or "").strip() or _mesmo_nome(emp, antigo)]
        if ids:
            c.execute("update prospeccao set empresa=%s where conta_id=%s and id = any(%s)",
                      (novo[:200], conta_id, ids))
        c.commit()
    return len(ids)


def _cabe_na_empresa(pool, conta_id: int, outras: list[dict], orcamento_id, codigo: str) -> str | None:
    """A regra do evento vale também pra quem entra num cadastro que já existe: a
    mesma empresa tem no máximo `max_por_empresa` stands (2, salvo config). Conta os
    stands que ela já tem em outras reservas mais os desta. None se cabe."""
    maximo = regras_de_pagamento(obter_config(pool, conta_id))["max_por_empresa"]
    with pool.connection() as c:
        desta = c.execute(
            "select count(*) from evento_stands where conta_id=%s and status <> 'livre' "
            "and (codigo=%s or (%s::bigint is not null and orcamento_id=%s::bigint))",
            (conta_id, codigo, orcamento_id, orcamento_id)).fetchone()[0]
    if len(outras) + int(desta or 1) <= maximo:
        return None
    ja = ", ".join(o["codigo"] for o in outras)
    return (f"Essa empresa já tem {len(outras)} stand{'s' if len(outras) != 1 else ''} ({ja}) — "
            f"o máximo é {maximo} por empresa. Use outro CPF/CNPJ ou fale com a organização.")


def _pode_juntar(juntar: str, vendedor_id, vendedor_do_cadastro, outras: list[dict],
                 nome_cadastro) -> str | None:
    """None se o stand pode entrar no cadastro que já existe; senão, a frase pra quem
    está salvando (ver `salvar_cadastro_stand`)."""
    # A ÚNICA regra de venda (o dono, 02/10/2026): "1 CNPJ só pode 2 stands" — o
    # limite é conferido em `_cabe_na_empresa` e de novo sob trava. Gestão e vendedora
    # (de qualquer venda) juntam; só o link PÚBLICO do contrato não — ali não é regra
    # de venda, é proteção: quem tem um link não vê os dados de outra empresa
    # digitando o CNPJ dela
    if juntar in ("sempre", "do_vendedor"):
        return None
    return ("Este CPF/CNPJ (ou WhatsApp) já está no cadastro de outra loja deste evento — "
            "fale com o seu vendedor pra juntar os stands na mesma empresa.")


def _espelhar_no_orcamento(pool, conta_id: int, orcamento_id: int, cliente_id: int,
                           campos: dict, tipo, digitos: str, congelado: bool) -> None:
    """Leva o cadastro pra proposta, que é o que o contrato lê. O NOME do
    contratante é `empresa` (razão social; na falta, o fantasia) e o
    representante legal vai em `socio` — a mesma regra de `_espelhar_cliente`
    (web/painel_servicos). Contrato ASSINADO não muda (só o vínculo)."""
    from . import validadoc
    with pool.connection() as c:
        try:
            from web.painel_servicos import _garantir_tabela
            _garantir_tabela(c)
        except Exception:  # noqa: BLE001 — colunas já existem em produção
            try:
                c.rollback()
            except Exception:  # noqa: BLE001
                pass
        c.execute("update orcamentos set cliente_id=%s where id=%s and conta_id=%s",
                  (cliente_id, orcamento_id, conta_id))
        if not congelado:
            doc_fmt = validadoc.formatar(digitos) if digitos else None
            c.execute(
                """update orcamentos
                      set empresa=%s, cliente=%s, socio=%s, whatsapp=%s, email=%s,
                          endereco=%s, cep=%s, cidade=%s, uf=%s,
                          cnpj=%s, cpf=%s
                    where id=%s and conta_id=%s""",
                (campos["razao_social"] or campos["nome"],
                 campos["representante"] or campos["nome"],
                 campos["representante"] or None, campos["telefone"] or None,
                 campos["email"] or None, campos["endereco"] or None,
                 campos["cep"] or None, campos["cidade"] or None,
                 (campos["uf"] or "")[:2].upper() or None,
                 doc_fmt if tipo == "pj" else None, doc_fmt if tipo == "pf" else None,
                 orcamento_id, conta_id))
        c.commit()


# ---------------------------------------------------------------------------
# O CLIENTE COMPLETA OS DADOS NO LINK DO CONTRATO, e a VENDEDORA CADASTRA O CLIENTE
# ANTES DA RESERVA (aprovados na maquete em 01/10/2026, só o app de estandes)
# ---------------------------------------------------------------------------

def stands_do_orcamento(pool, conta_id: int, orcamento_id) -> list[dict]:
    """Os stands ocupados de uma proposta — a reserva inteira (1 ou 2 stands)."""
    if not orcamento_id:
        return []
    with pool.connection() as c:
        rows = c.execute(
            f"select {_COLS} from evento_stands where conta_id=%s and orcamento_id=%s "
            "and status <> 'livre' order by codigo", (conta_id, orcamento_id)).fetchall()
    return [_fmt(r) for r in rows]


def cadastro_do_contrato(pool, conta_id: int, orcamento_id) -> dict | None:
    """O cadastro do lojista como o link do contrato o pede ("Complete os dados da
    sua empresa"): {codigos, cad, faltam, n_ok, n_total}. None quando a proposta não
    tem stand."""
    st = stands_do_orcamento(pool, conta_id, orcamento_id)
    if not st:
        return None
    cad = cadastros_dos_stands(pool, conta_id, st[:1])[st[0]["codigo"]]
    return {"codigos": [s["codigo"] for s in st], "cad": cad, "faltam": cad["faltam"],
            "n_ok": cad["n_ok"], "n_total": cad["n_total"]}


def salvar_cadastro_do_contrato(pool, conta_id: int, orcamento_id, dados: dict) -> dict:
    """O PRÓPRIO CLIENTE completa os dados no link do contrato, antes de assinar.

    Salva pelo mesmo `salvar_cadastro_stand` do app (cria/atualiza o cliente e leva
    os dados pro contrato ainda não assinado) no 1º stand da reserva e liga os
    outros ao mesmo cliente. O que o formulário não traz vem do cadastro como está
    — a observação interna da equipe nunca passa pela mão do cliente. Devolve o do
    `salvar_cadastro_stand` mais `completou` (o cadastro fechou os 7 dados AGORA),
    `prospeccao_id` e `codigos`."""
    st = stands_do_orcamento(pool, conta_id, orcamento_id)
    if not st:
        return {"ok": False, "erro": "Reserva não encontrada."}
    antes = cadastros_dos_stands(pool, conta_id, st[:1])[st[0]["codigo"]]
    chaves = ("fantasia", "whats", "razao", "doc", "rep", "email", "end", "cep",
              "cidade", "uf")
    final = {k: antes.get(k) or "" for k in chaves + ("obs",)}
    # nome fantasia e WhatsApp vazios no formulário não apagam o que o cadastro tem
    final.update({k: dados[k] for k in chaves
                  if k in dados and (dados[k] or k not in ("fantasia", "whats"))})
    # o link é público: ninguém entra no cadastro de outra loja digitando um CNPJ
    r = salvar_cadastro_stand(pool, conta_id, st[0]["codigo"], final, juntar="nunca")
    if not r.get("ok"):
        return r
    outros = [s["codigo"] for s in st[1:]]
    if outros:
        with pool.connection() as c:
            c.execute("update evento_stands set cliente_id=%s, atualizado_em=now() "
                      "where conta_id=%s and codigo = any(%s)",
                      (r["cliente_id"], conta_id, outros))
            c.commit()
    r["completou"] = bool(antes["faltam"]) and not r["faltam"]
    r["prospeccao_id"] = st[0].get("prospeccao_id")
    r["codigos"] = [s["codigo"] for s in st]
    return r


def cadastrar_cliente_do_vendedor(pool, conta_id: int, membro_id: int, dados: dict) -> dict:
    """"+ Novo cliente" do app de estandes: a vendedora cadastra o lojista ANTES da
    reserva — só nome fantasia e WhatsApp são obrigatórios — e ele fica na carteira
    dela (`clientes.vendedor_id`, migração 461) até reservar pelo link que ela manda
    (`?c=`). Mesmo dedup do Zaq (CPF/CNPJ ou WhatsApp): o lojista que já existe é
    completado, não duplicado; o que já é de outra vendedora, ou já tem stand, fica
    com quem está. Devolve {"ok", "cliente_id", "acao"} ou {"ok": False, "erro"}."""
    from . import clientes as _cli
    from . import validadoc
    g = lambda k: (dados.get(k) or "").strip()  # noqa: E731
    fantasia, whats = g("fantasia"), g("whats")
    if not fantasia or len("".join(ch for ch in whats if ch.isdigit())) < 10:
        return {"ok": False, "erro": "Preencha o nome fantasia e o WhatsApp (com DDD)."}
    doc = g("doc")
    tipo, digitos = validadoc.classificar(doc) if doc else (None, "")
    if doc and tipo not in ("pf", "pj"):
        return {"ok": False, "erro": "Documento deve ter 11 (CPF) ou 14 (CNPJ) dígitos."}
    try:
        r = _cli.salvar_cliente(
            pool, conta_id, fantasia, telefone=whats,
            cpf=digitos if tipo == "pf" else None, cnpj=digitos if tipo == "pj" else None,
            email=g("email") or None, endereco=g("end") or None, cep=g("cep") or None,
            cidade=g("cidade") or None, uf=(g("uf")[:2].upper() or None),
            razao_social=g("razao") or None, representante=g("rep") or None)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    except Exception as e:  # noqa: BLE001 — ex.: CPF/CNPJ já é de OUTRO cliente
        if type(e).__name__ == "UniqueViolation":
            return {"ok": False, "erro": "Esse CPF/CNPJ já está cadastrado em outro cliente."}
        raise
    cid = int(r["id"])
    with pool.connection() as c:
        dono = c.execute("select vendedor_id from clientes where id=%s and dono_id=%s",
                         (cid, conta_id)).fetchone()
        if dono and dono[0] and int(dono[0]) != int(membro_id):
            return {"ok": False, "erro": "Esse cliente já está na carteira de outra vendedora."}
        if c.execute("select 1 from evento_stands where conta_id=%s and cliente_id=%s "
                     "and status <> 'livre' limit 1", (conta_id, cid)).fetchone():
            return {"ok": False, "erro": "Esse cliente já tem stand reservado — "
                                         "veja a venda na aba Vendas."}
        c.execute("update clientes set vendedor_id=%s where id=%s and dono_id=%s",
                  (membro_id, cid, conta_id))
        c.commit()
    return {"ok": True, "cliente_id": cid, "acao": r["acao"]}


def clientes_do_vendedor_sem_stand(pool, conta_id: int, membro_id) -> list[dict]:
    """A carteira da vendedora que ainda não reservou: os clientes que ELA cadastrou
    ("+ Novo cliente") e que nenhum stand ocupado desta conta aponta. No formato do
    cadastro do stand (o que falta pro contrato). Base sem a 461: carteira vazia."""
    from . import clientes as _cli
    if not membro_id:
        return []
    try:
        with pool.connection() as c:
            ids = [int(r[0]) for r in c.execute(
                """select cl.id from clientes cl
                    where cl.dono_id=%s and cl.vendedor_id=%s
                      and not exists (select 1 from evento_stands s
                                       where s.conta_id = cl.dono_id and s.cliente_id = cl.id
                                         and s.status <> 'livre')
                    order by lower(cl.nome)""", (conta_id, membro_id)).fetchall()]
    except Exception as e:  # noqa: BLE001
        _log.info("evento_stands: sem a carteira da vendedora %s: %s: %s",
                  membro_id, type(e).__name__, e)
        return []
    clis = _cli.obter_clientes(pool, conta_id, ids)
    return [_cadastro_do_cliente(clis[cid]) for cid in ids if cid in clis]


def _orcamento_da_pagina(pool, conta_id: int, orcamento_id) -> dict | None:
    if not orcamento_id:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select to_jsonb(o)->>'canal', coalesce(o.setup_centavos,0),
                      o.sinal_centavos, o.sinal_pago_em, o.status
                 from orcamentos o where o.id=%s and o.conta_id=%s""",
            (orcamento_id, conta_id)).fetchone()
    if not r:
        return None
    return {"canal": r[0] or "", "total": int(r[1] or 0), "sinal_centavos": r[2],
            "sinal_pago_em": r[3], "status": r[4]}


def _gravar_sinal_no_orcamento(pool, conta_id: int, orcamento_id: int, sinal: int,
                               total: int, saldo_ate) -> None:
    """O gestor conferiu o comprovante: o sinal é o valor que CAIU. Refaz o plano
    (sinal + saldo) e carimba o recebimento — é o carimbo que faz o
    `fechar_orcamento` dar baixa no título do sinal, na data em que ele caiu."""
    import json as _json
    plano = plano_de_pagamento(total, sinal, saldo_ate, _ag.agora_brt().date())
    with pool.connection() as c:
        c.execute(
            """update orcamentos
                  set parcelas=%s::jsonb, sinal_centavos=%s,
                      sinal_pago_em=coalesce(sinal_pago_em, now())
                where id=%s and conta_id=%s and status <> 'fechado'""",
            (_json.dumps(plano), int(sinal), orcamento_id, conta_id))
        c.commit()


def confirmar_pagamento(pool, conta_id: int, codigo: str,
                        membro_id: int | None = None, *,
                        sinal_centavos: int | None = None) -> dict:
    """O dono OLHOU o comprovante e confirma: pre_reservado -> vendido.

    Não existe gateway automático nessa ponta (pedido explícito da Outlet
    Chic) — é sempre um clique humano vendo o arquivo primeiro.

    RESERVA DE 2 ESTANDES: confirma os dois de uma vez (um comprovante, um
    contrato). `sinal_centavos` é o valor que o gestor conferiu no comprovante:
    nunca abaixo do mínimo (R$ 1.500 por estande) e nunca acima do total. Ele
    refaz o plano (sinal + saldo) do orçamento e carimba o recebimento, e o
    `fechar_orcamento` dá a baixa no título do sinal e deixa o saldo em aberto,
    com o vencimento da data-limite.

    Se o estande já tem `orcamento_id` (o orçamento nasceu por fora, no fluxo
    de proposta normal), reaproveita `finance.vendas.fechar_orcamento` pra
    gerar os títulos a receber — é o MESMO motor que todo fechamento de
    orçamento de evento usa hoje (Prime Eventos, conta 34); não duplicamos
    lançamento financeiro aqui.

    `por_assinatura=True` ao chamar fechar_orcamento: naquele módulo a trava
    normal é "só fecha quem assinou o contrato", e não existe fluxo de
    contrato pro estande — a confirmação do dono vendo o comprovante É a
    evidência de venda aqui.

    Idempotente: chamar de novo num estande já 'vendido' não duplica nada,
    só confirma o que já era.
    """
    with pool.connection() as c:
        atual = c.execute(
            f"select {_COLS} from evento_stands where conta_id=%s and codigo=%s",
            (conta_id, codigo)).fetchone()
    if atual is None:
        return {"ok": False, "erro": "Estande não encontrado."}
    atual = _fmt(atual)
    if atual["status"] == "vendido":
        return {"ok": True, "stand": atual, "ja_confirmado": True}
    if atual["status"] != "pre_reservado":
        return {"ok": False, "erro": f"Estande está '{atual['status']}', "
                                     "não dá pra confirmar pagamento."}

    grupo = grupo_do_stand(pool, conta_id, atual)
    reg = regras_de_pagamento(obter_config(pool, conta_id))
    orc = _orcamento_da_pagina(pool, conta_id, atual["orcamento_id"])
    sinal_final = None
    if orc and orc["canal"] == "pagina_stands":
        minimo = reg["sinal_minimo_centavos"] * len(grupo)
        sinal_final = (int(sinal_centavos) if sinal_centavos is not None
                       else int(orc["sinal_centavos"] or minimo))
        if sinal_final < minimo:
            return {"ok": False, "erro": "O sinal mínimo é " + _reais(minimo)
                    + (" (R$ 1.500,00 por estande)" if len(grupo) > 1 else "")
                    + " — o valor conferido está abaixo."}
        if orc["total"] and sinal_final > orc["total"]:
            return {"ok": False, "erro": "O sinal não pode passar do total ("
                    + _reais(orc["total"]) + ")."}

    with pool.connection() as c:
        rows = c.execute(
            f"""update evento_stands set status='vendido', pre_reserva_ate=null,
                       atualizado_em=now()
                 where conta_id=%s and codigo = any(%s) and status='pre_reservado'
                 returning {_COLS}""",
            (conta_id, [x["codigo"] for x in grupo])).fetchall()
        c.commit()
    r = next((x for x in rows if x[2] == codigo), None)
    if r is None:
        # concorrência: alguém mexeu no estande entre o select e o update
        # (outro clique de confirmar, ou expirou no meio) — falha fechada.
        return {"ok": False, "erro": "Não confirmei (o estande mudou entre a "
                                     "leitura e a confirmação). Tente de novo."}
    stand = _fmt(r)
    financeiro = None
    if stand["orcamento_id"]:
        try:
            from . import vendas
            if sinal_final is not None:
                _gravar_sinal_no_orcamento(pool, conta_id, stand["orcamento_id"],
                                           sinal_final, orc["total"], reg["saldo_ate"])
            financeiro = vendas.fechar_orcamento(pool, conta_id, stand["orcamento_id"],
                                                 criado_por=membro_id, por_assinatura=True)
        except Exception as e:  # noqa: BLE001 — o estande JÁ VENDEU; o título é bônus
            _log.warning("evento_stands: confirmar_pagamento vendeu %s/%s mas o "
                        "lançamento financeiro falhou: %s: %s", conta_id, codigo,
                        type(e).__name__, e)
    _log.info("evento_stands: pagamento confirmado — conta %s, estande(s) %s", conta_id,
              ",".join(x["codigo"] for x in grupo))
    try:
        cods = " + ".join(x["codigo"] for x in grupo)
        aberto = (situacao_financeira(pool, conta_id, [stand["orcamento_id"]])
                  .get(stand["orcamento_id"], {}).get("aberto", 0)) if stand["orcamento_id"] else 0
        ate = reg["saldo_ate"].strftime("%d/%m") if reg.get("saldo_ate") else ""
        avisar_vendedor(pool, conta_id, stand["prospeccao_id"], "Sinal confirmado ✓",
                        f"{cods}: a gestão confirmou o sinal. "
                        + (f"Falta o saldo de {_reais(aberto)}" + (f" até {ate}." if ate else ".")
                           if aberto else "Venda garantida."),
                        url=f"/cockpit/stands?abrir={codigo}")
    except Exception as e:  # noqa: BLE001 — o aviso é bônus
        _log.info("evento_stands: aviso de sinal confirmado falhou: %s", e)
    return {"ok": True, "stand": stand, "financeiro": financeiro,
            "codigos": [x["codigo"] for x in grupo]}


def _reais(centavos: int) -> str:
    return "R$ " + f"{int(centavos) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def orcamentos_do_estande(pool, conta_id: int, orcamento_ids: list[int]) -> dict[int, dict]:
    """{id: {"total", "sinal_centavos"}} das propostas dos estandes, em lote."""
    ids = [int(x) for x in orcamento_ids if x]
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            "select id, coalesce(setup_centavos,0), sinal_centavos from orcamentos "
            "where conta_id=%s and id = any(%s)", (conta_id, ids)).fetchall()
    return {int(r[0]): {"total": int(r[1] or 0), "sinal_centavos": r[2]} for r in rows}


def situacao_financeira(pool, conta_id: int, orcamento_ids: list[int]) -> dict[int, dict]:
    """Quanto já entrou e quanto falta, por orçamento, lido dos TÍTULOS (a fonte da
    verdade do financeiro): {oid: {"pago", "aberto", "venc_aberto"}}. Orçamento sem
    título ainda (sinal não confirmado) fica de fora."""
    ids = [int(x) for x in orcamento_ids if x]
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            """select orcamento_id, status, sum(valor_centavos)::bigint,
                      min(vencimento) filter (where status='aberto')
                 from titulos
                where conta_id=%s and orcamento_id = any(%s) and tipo='receber'
                  and status in ('pago','aberto')
                group by orcamento_id, status""", (conta_id, ids)).fetchall()
    out: dict[int, dict] = {}
    for oid, st, soma, venc in rows:
        d = out.setdefault(int(oid), {"pago": 0, "aberto": 0, "venc_aberto": None})
        d["pago" if st == "pago" else "aberto"] = int(soma or 0)
        if st == "aberto":
            d["venc_aberto"] = venc
    return out


def registrar_pagamento_saldo(pool, conta_id: int, codigo: str, valor_centavos: int,
                              membro_id: int | None = None) -> dict:
    """O cliente pagou (parte do) saldo: dá baixa nos títulos em aberto da reserva,
    do vencimento mais antigo ao mais novo. Pode ser em mais de uma vez — o que
    sobrar de uma baixa parcial continua aberto, com o mesmo vencimento. Só depois
    de o sinal estar confirmado (é ele que cria os títulos)."""
    from . import empresa as _emp
    stand = buscar(pool, conta_id, codigo)
    if stand is None or not stand.get("orcamento_id"):
        return {"ok": False, "erro": "Estande sem proposta — não há saldo pra baixar."}
    if stand["status"] != "vendido":
        return {"ok": False, "erro": "Confirme o sinal antes de registrar o saldo."}
    valor = int(valor_centavos or 0)
    if valor <= 0:
        return {"ok": False, "erro": "Informe o valor recebido."}
    with pool.connection() as c:
        abertos = c.execute(
            """select id, valor_centavos from titulos
                where conta_id=%s and orcamento_id=%s and tipo='receber'
                  and status='aberto'
                order by vencimento, parcela_idx nulls last, id""",
            (conta_id, stand["orcamento_id"])).fetchall()
    total_aberto = sum(int(v) for _, v in abertos)
    if not abertos:
        return {"ok": False, "erro": "Não há saldo em aberto — já está quitado."}
    if valor > total_aberto:
        return {"ok": False, "erro": "O valor passa do saldo em aberto ("
                + _reais(total_aberto) + ")."}
    restante = valor
    for tid, vt in abertos:
        if restante <= 0:
            break
        vt = int(vt)
        if restante < vt:
            # baixa PARCIAL: o pedaço pago vira título quitado, o resto segue
            # aberto com o mesmo vencimento (título novo, sem parcela_idx)
            with pool.connection() as c:
                c.execute(
                    """insert into titulos
                         (conta_id, tipo, descricao, contraparte, valor_centavos,
                          vencimento, categoria, recorrente, criado_por,
                          orcamento_id, parcela_idx)
                       select conta_id, tipo, left(descricao || ' · restante', 200),
                              contraparte, %s, vencimento, categoria, recorrente,
                              criado_por, orcamento_id, null
                         from titulos where id=%s and conta_id=%s""",
                    (vt - restante, tid, conta_id))
                c.execute("update titulos set valor_centavos=%s where id=%s "
                          "and conta_id=%s and status='aberto'", (restante, tid, conta_id))
                c.commit()
            pago = restante
        else:
            pago = vt
        res = _emp.dar_baixa_titulo(pool, conta_id, tid, membro_id=membro_id)
        if not res.get("ok"):
            return {"ok": False, "erro": res.get("erro") or "Não consegui dar a baixa."}
        restante -= pago
    sit = situacao_financeira(pool, conta_id, [stand["orcamento_id"]]).get(
        stand["orcamento_id"], {})
    return {"ok": True, "aberto": sit.get("aberto", 0), "quitado": sit.get("aberto", 0) == 0}


# ---------------------------------------------------------------------------
# AVISOS AO VENDEDOR (push no app): reserva pelo link dele, sinal confirmado,
# reserva pra vencer e reserva vencida. Best-effort: nunca derrubam o fluxo.
# ---------------------------------------------------------------------------

def _vendedor_da_venda(pool, conta_id: int, prospeccao_id) -> tuple | None:
    if not prospeccao_id:
        return None
    with pool.connection() as c:
        r = c.execute("select vendedor_id, empresa from prospeccao where conta_id=%s and id=%s",
                      (conta_id, prospeccao_id)).fetchone()
    return (int(r[0]), r[1] or "") if r and r[0] else None


def avisar_vendedor(pool, conta_id: int, prospeccao_id, titulo: str, corpo: str,
                    url: str = "/cockpit/stands/vendas") -> int:
    """Push pro vendedor dono da venda (a prospecção do link dele). Venda sem
    vendedor não avisa ninguém. Devolve quantos aparelhos receberam."""
    try:
        v = _vendedor_da_venda(pool, conta_id, prospeccao_id)
        if not v:
            return 0
        from . import cockpit as _ck
        return _ck.enviar_push(pool, conta_id, v[0], titulo, corpo, url=url)
    except Exception as e:  # noqa: BLE001
        _log.info("evento_stands: push ao vendedor falhou: %s: %s", type(e).__name__, e)
        return 0


def avisar_reserva_nova(pool, conta_id: int, prospeccao_id, codigos: list[str],
                        nome: str, sinal_centavos: int | None) -> int:
    cods = " + ".join(codigos)
    # cliente cadastrado antes (ou que já comprou com tudo preenchido): a venda
    # nasce completa e o aviso já manda pro contrato
    completo = False
    try:
        st = buscar(pool, conta_id, codigos[0])
        if st and st["status"] != "livre":
            completo = not cadastros_dos_stands(pool, conta_id, [st])[st["codigo"]]["faltam"]
    except Exception:  # noqa: BLE001 — sem a conferência, o aviso de sempre
        completo = False
    return avisar_vendedor(
        pool, conta_id, prospeccao_id, "Nova reserva pelo seu link",
        f"{nome or 'Um cliente'} reservou {cods}"
        + (f" e mandou o sinal de {_reais(sinal_centavos)}" if sinal_centavos else "")
        + (". Cadastro completo: já pode mandar o contrato." if completo
           else ". Complete os dados do cliente pro contrato."),
        url=f"/cockpit/stands?abrir={codigos[0]}")


def avisar_cadastro_completo(pool, conta_id: int, prospeccao_id, codigos: list[str],
                             nome: str) -> int:
    """O cliente completou os dados no link do contrato: a vendedora fica sabendo
    que agora é só ele assinar."""
    alvo = "dos stands" if len(codigos) > 1 else "do stand"
    return avisar_vendedor(
        pool, conta_id, prospeccao_id, "Cadastro completo ✓",
        f"{nome or 'O cliente'} completou os dados do contrato {alvo} "
        f"{' + '.join(codigos)}. Agora é só assinar.",
        url=f"/cockpit/stands?abrir={codigos[0]}")


def avisar_reservas_vencendo(pool, agora, horas: int = 24) -> int:
    """Uma vez por reserva: faltando menos de `horas` pro prazo vencer sem a gestão
    confirmar o sinal, o vendedor é avisado (dá tempo de cobrar o comprovante)."""
    with pool.connection() as c:
        rows = c.execute(
            """update evento_stands set aviso_vence_em=now()
                where status='pre_reservado' and aviso_vence_em is null
                  and pre_reserva_ate is not null and pre_reserva_ate > %s
                  and pre_reserva_ate <= %s + make_interval(hours => %s)
                returning conta_id, codigo, prospeccao_id, pre_reserva_ate""",
            (agora, agora, int(horas))).fetchall()
        c.commit()
    por_venda: dict = {}
    for conta_id, codigo, pid, ate in rows:
        por_venda.setdefault((conta_id, pid), {"cods": [], "ate": ate})["cods"].append(codigo)
    n = 0
    for (conta_id, pid), v in por_venda.items():
        n += avisar_vendedor(
            pool, conta_id, pid, "Reserva perto de vencer",
            f"{' + '.join(sorted(v['cods']))}: a reserva vence {v['ate']:%d/%m às %H:%M} se o "
            "sinal não for confirmado. Confira o comprovante com o cliente.",
            url=f"/cockpit/stands?abrir={sorted(v['cods'])[0]}")
    return n


def avisar_expiradas(pool, expirados: list[dict]) -> int:
    """O prazo venceu e o stand voltou pro mapa: o vendedor fica sabendo."""
    por_venda: dict = {}
    for e in expirados or []:
        por_venda.setdefault((e["conta_id"], e.get("prospeccao_id")), []).append(e["codigo"])
    n = 0
    for (conta_id, pid), cods in por_venda.items():
        n += avisar_vendedor(
            pool, conta_id, pid, "Reserva vencida",
            f"{' + '.join(sorted(cods))} voltou pro mapa: o sinal não foi confirmado no prazo.",
            url="/cockpit/stands/vendas")
    return n


def liberar(pool, conta_id: int, codigo: str) -> bool:
    """Devolve o estande pra 'livre' — o interessado desistiu, o dono errou o
    clique, ou uma venda precisa ser desfeita. Limpa comprovante e prazo: o
    próximo interessado começa do zero, sem arquivo de outra negociação
    pendurado. NÃO mexe no orçamento (se algum dia existiu) — isso o dono
    resolve à parte, no próprio orçamento."""
    with pool.connection() as c:
        cur = c.execute(
            """update evento_stands
                  set status='livre', pre_reserva_ate=null, comprovante_url=null,
                      comprovante_em=null, grupo_id=null, aviso_vence_em=null,
                      atualizado_em=now()
                where conta_id=%s and status <> 'livre'
                  and (codigo=%s or (grupo_id is not null and grupo_id = (
                        select grupo_id from evento_stands where conta_id=%s and codigo=%s)))""",
            (conta_id, codigo, conta_id, codigo))
        c.commit()
        return cur.rowcount > 0


def expirar_pre_reservas(pool, agora: datetime) -> list[dict]:
    """Libera os estandes cujo prazo venceu sem confirmação — a rede de
    segurança do modo 'pagamento' (dono esqueceu de conferir) e o prazo de
    pagamento do modo 'pedido'. Clone de agenda.expirar_pre_reservas, por
    estande em vez de por data.

    MANTÉM o comprovante_url ao expirar (ao contrário de `liberar`): se o
    cliente pagou de verdade e o dono só demorou a conferir, o arquivo antigo
    continua rastreável no histórico até um novo comprovante — de outro
    interessado ou do mesmo — sobrescrever. Só o status e o prazo voltam.

    Devolve o que expirou, pra quem chama (o ticker de fundo) poder avisar."""
    with pool.connection() as c:
        rows = c.execute(
            """update evento_stands
                  set status='livre', pre_reserva_ate=null, grupo_id=null,
                      aviso_vence_em=null, atualizado_em=now()
                where status='pre_reservado' and pre_reserva_ate is not null
                  and pre_reserva_ate <= %s
                returning id, conta_id, codigo, pavilhao, zona, prospeccao_id""",
            (agora,)).fetchall()
        c.commit()
    expirados = [{"id": r[0], "conta_id": r[1], "codigo": r[2], "pavilhao": r[3],
                  "zona": r[4], "prospeccao_id": r[5]} for r in rows]
    if expirados:
        _log.info("evento_stands: %d estande(s) voltaram a livre (prazo vencido)",
                  len(expirados))
    return expirados
