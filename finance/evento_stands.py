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
        "orcamento_id, ordem, criado_em, atualizado_em, cliente_id")


def _fmt(row) -> dict:
    return {"id": row[0], "conta_id": row[1], "codigo": row[2], "pavilhao": row[3],
            "zona": row[4], "tamanho": row[5], "preco_centavos": row[6], "status": row[7],
            "pre_reserva_ate": row[8], "comprovante_url": row[9], "comprovante_em": row[10],
            "prospeccao_id": row[11], "orcamento_id": row[12], "ordem": row[13],
            "criado_em": row[14], "atualizado_em": row[15],
            "cliente_id": row[16] if len(row) > 16 else None}


_CFG_COLS = ("conta_id, slug, bloqueia_em, pre_reserva_dias, whatsapp_numero, pix_chave, "
            "pix_titular, edicao_label, evento_inicio, evento_fim, evento_local")


def _fmt_cfg(row) -> dict:
    return {"conta_id": row[0], "slug": row[1], "bloqueia_em": row[2],
            "pre_reserva_dias": row[3], "whatsapp_numero": row[4], "pix_chave": row[5],
            "pix_titular": row[6], "edicao_label": row[7], "evento_inicio": row[8],
            "evento_fim": row[9], "evento_local": row[10]}


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


# Mesmos tipos aceitos de finance/comprovantes.py (o comprovante do estande é
# prova de pagamento, não foto de obra — não reaproveita finance/obra_fotos.py,
# que aceita HEIC e não aceita PDF).
_EXT_COMPROVANTE = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg",
                    "image/png": "png", "image/webp": "webp"}


def subir_e_registrar_comprovante(pool, conta_id: int, codigo: str, conteudo: bytes,
                                  content_type: str, *, prospeccao_id: int | None = None,
                                  subir=None) -> dict:
    """Sobe o arquivo pro bucket PRIVADO dos comprovantes e, se subiu, chama
    `registrar_comprovante`. É o CANO ÚNICO dos dois canais que recebem arquivo
    direto do cliente — a página pública (web/loja_stands.py) e a ferramenta do
    agente no WhatsApp (finance/tools_pj.py) — pra não duplicar validação de
    tipo/tamanho nem o formato do caminho no bucket em dois lugares que um dia
    discordariam.

    `subir` é injetável (testes) — por padrão `finance.comprovantes.subir_em`,
    o MESMO bucket privado que o comprovante de sinal de orçamento já usa (só
    muda o prefixo do caminho: 'stands/' em vez de 'comprovantes/').

    Devolve {"ok": False, "erro": "..."} tanto pra upload inválido (arquivo
    vazio, tipo não aceito) quanto pra estande indisponível — quem chama não
    precisa saber em qual das duas etapas falhou pra mostrar a mensagem."""
    from . import comprovantes as _comprov
    import time
    import uuid
    try:
        ct = _comprov.validar(conteudo, content_type)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    ext = _EXT_COMPROVANTE.get(ct, "bin")
    caminho = f"stands/{conta_id}/{codigo}-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}"
    subir = subir or _comprov.subir_em
    try:
        subir(caminho, conteudo, ct)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    r = registrar_comprovante(pool, conta_id, codigo, caminho, prospeccao_id=prospeccao_id)
    if r.get("ok"):
        # o sinal "pagou" (comprovante na mão): nasce a proposta + o contrato
        # pra assinatura — best-effort, o estande já está travado de qualquer
        # jeito e o contrato é consequência, não condição.
        try:
            extra = garantir_orcamento_e_contrato(pool, conta_id, r["stand"])
            if extra:
                r.update(extra)
                r["stand"]["orcamento_id"] = extra["orcamento_id"]
        except Exception as e:  # noqa: BLE001
            _log.warning("evento_stands: comprovante ok mas proposta/contrato do "
                         "%s/%s falhou: %s: %s", conta_id, codigo, type(e).__name__, e)
    return r


def garantir_orcamento_e_contrato(pool, conta_id: int, stand: dict) -> dict | None:
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
    preco = int(stand.get("preco_centavos") or 0)
    # O OBJETO do contrato: vira o nome do item da proposta, e é dele que o
    # contrato tira "Espaço locado" e o campo {objeto.descricao} das cláusulas.
    # mesmo shape de item do cockpit/painel: nome + setup em REAIS (total da linha)
    itens = [{"nome": descricao_objeto(stand), "setup": preco / 100, "mensal": 0}]
    # o EVENTO completo pro quadro "Objeto": tipo (marca + edição), período de
    # vários dias e local (mesmo jsonb da proposta de evento do painel). `data`
    # segue ISO (a data de início) porque é o que o resto do sistema lê.
    cfg = obter_config(pool, conta_id) or {}
    evento = _json.dumps(_evento_da_config(pool, conta_id, cfg)) if cfg.get("evento_inicio") else None
    modo = vendas.modo_do_orcamento(pool, conta_id)
    token = _secrets.token_urlsafe(16)
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
                  criado_por, canal, token, modo, numero)
               values (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,0,%s,
                       'aprovada',%s,now(),
                       'pagina_stands','pagina_stands',%s,%s,
                       {vendas.NUMERO_SQL})
               returning id""",
            (conta_id, nome[:200], nome[:200], (zap or None),
             _json.dumps(itens), evento, preco, preco, nome[:120], token, modo,
             conta_id)).fetchone())
        if not r:
            return None
        oid = int(r[0])
        c.execute("update evento_stands set orcamento_id=%s, atualizado_em=now() "
                  "where conta_id=%s and codigo=%s",
                  (oid, conta_id, stand["codigo"]))
        c.execute("update prospeccao set orcamento_id=%s where id=%s and conta_id=%s",
                  (oid, pid, conta_id))
        c.commit()
    # QUEM RESERVA JÁ VIRA CADASTRO (aprovado na maquete, 30/09/2026): nome
    # fantasia + WhatsApp entram em `clientes` agora, sem esperar o gestor. O
    # Zaq junta por WhatsApp — o mesmo lojista comprando um segundo stand não
    # vira dois clientes. O gestor completa o resto em "Dados do cliente".
    _garantir_cliente_do_stand(pool, conta_id, stand["codigo"], oid, nome, zap)
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
    nome = ""
    try:
        from . import empresa as _emp
        d = _emp.obter_dados_empresa(pool, conta_id) or {}
        nome = (d.get("nome_fantasia") or "").strip()
    except Exception:  # noqa: BLE001 — sem o nome fantasia, cai no slug
        nome = ""
    marca = nome or (cfg.get("slug") or "").replace("-", " ").title()
    tipo = " — ".join(x for x in (marca, cfg.get("edicao_label")) if x)
    return {"tipo": tipo, "data": cfg["evento_inicio"].isoformat(),
            "periodo": _periodo(cfg["evento_inicio"], cfg.get("evento_fim")),
            "local": cfg.get("evento_local") or ""}


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


def cadastros_dos_stands(pool, conta_id: int, stands: list[dict]) -> dict[str, dict]:
    """{codigo: cadastro} dos stands que NÃO estão livres — lido de `clientes`
    (o cadastro de verdade) e, enquanto o cliente não existir, da prospecção
    (só nome e WhatsApp). Em lote: a lista de estandes mostra tudo de uma vez."""
    from . import clientes as _cli
    ocup = [s for s in stands if s["status"] != "livre"]
    clientes = _cli.obter_clientes(pool, conta_id,
                                   [s.get("cliente_id") for s in ocup])
    pids = [s["prospeccao_id"] for s in ocup
            if s.get("prospeccao_id") and s.get("cliente_id") not in clientes]
    prosp = {}
    if pids:
        with pool.connection() as c:
            prosp = {r[0]: r for r in c.execute(
                "select id, empresa, whatsapp from prospeccao "
                "where conta_id=%s and id = any(%s)", (conta_id, pids)).fetchall()}
    out = {}
    for s in ocup:
        c = clientes.get(s.get("cliente_id"))
        if c:
            out[s["codigo"]] = _montar_cadastro({
                "fantasia": c.get("nome"), "whats": c.get("telefone"),
                "razao": c.get("razao_social"), "doc": c.get("documento_fmt") or "",
                "rep": c.get("representante"), "email": c.get("email"),
                "end": c.get("endereco"), "cep": c.get("cep"),
                "cidade": c.get("cidade"), "uf": c.get("uf"), "obs": c.get("obs")},
                cliente_id=c["id"])
        else:
            p = prosp.get(s.get("prospeccao_id"))
            out[s["codigo"]] = _montar_cadastro(
                {"fantasia": p[1] if p else "", "whats": p[2] if p else ""})
    return out


def salvar_cadastro_stand(pool, conta_id: int, codigo: str, dados: dict) -> dict:
    """Salva o formulário "Dados do cliente" do stand.

    Cria o cliente na aba Clientes se ele ainda não existe (dedup por CNPJ/CPF
    ou WhatsApp, o de sempre do Zaq) e SOBRESCREVE com o que a pessoa digitou —
    é uma edição explícita, não um enriquecimento. Depois liga o stand e a
    proposta ao cliente e, se o contrato AINDA NÃO foi assinado, leva os dados
    pra ele (o assinado é documento congelado). Devolve
    {"ok", "cliente_id", "acao", "faltam", "congelado"} ou {"ok": False, "erro"}."""
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
    campos = {"nome": fantasia, "telefone": g("whats"), "email": g("email"),
              "endereco": g("end"), "cep": g("cep"), "cidade": g("cidade"),
              "uf": g("uf"), "obs": g("obs"),
              "razao_social": g("razao"), "representante": g("rep")}
    if tipo == "pf":
        campos["cpf"] = digitos
    elif tipo == "pj":
        campos["cnpj"] = digitos
    cid = stand.get("cliente_id")
    acao = "atualizado"
    try:
        if not cid:
            r = _cli.salvar_cliente(pool, conta_id, fantasia, telefone=g("whats") or None,
                                    cpf=campos.get("cpf"), cnpj=campos.get("cnpj"))
            cid, acao = int(r["id"]), r["acao"]
        _cli.atualizar_cliente(pool, conta_id, cid, **campos)
    except ValueError as e:
        return {"ok": False, "erro": str(e)}
    except Exception as e:  # noqa: BLE001 — ex.: CPF/CNPJ já é de OUTRO cliente
        if type(e).__name__ == "UniqueViolation":
            return {"ok": False, "erro": "Esse CPF/CNPJ já está cadastrado em outro "
                                         "cliente — abra o cadastro dele em Clientes."}
        raise

    oid = stand.get("orcamento_id")
    with pool.connection() as c:
        c.execute("update evento_stands set cliente_id=%s, atualizado_em=now() "
                  "where conta_id=%s and codigo=%s", (cid, conta_id, codigo))
        if stand.get("prospeccao_id"):
            c.execute("update prospeccao set empresa=%s, whatsapp=%s "
                      "where id=%s and conta_id=%s",
                      (fantasia[:200], g("whats")[:40] or None,
                       stand["prospeccao_id"], conta_id))
        c.commit()

    congelado = False
    if oid:
        try:
            congelado = _ctr.assinado_do_orcamento(pool, conta_id, oid)
        except Exception:  # noqa: BLE001 — base sem a 164: segue como não assinado
            congelado = False
        _espelhar_no_orcamento(pool, conta_id, oid, cid, campos, tipo, digitos,
                               congelado)
    cad = cadastros_dos_stands(pool, conta_id, [dict(stand, cliente_id=cid)])[codigo]
    return {"ok": True, "cliente_id": cid, "acao": acao,
            "faltam": cad["faltam"], "congelado": congelado}


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


def confirmar_pagamento(pool, conta_id: int, codigo: str,
                        membro_id: int | None = None) -> dict:
    """O dono OLHOU o comprovante e confirma: pre_reservado -> vendido.

    Não existe gateway automático nessa ponta (pedido explícito da Outlet
    Chic) — é sempre um clique humano vendo o arquivo primeiro.

    Se o estande já tem `orcamento_id` (o orçamento nasceu por fora, no fluxo
    de proposta normal), reaproveita `finance.vendas.fechar_orcamento` pra
    gerar os títulos a receber — é o MESMO motor que todo fechamento de
    orçamento de evento usa hoje (Prime Eventos, conta 34); não duplicamos
    lançamento financeiro aqui.

    `por_assinatura=True` ao chamar fechar_orcamento: naquele módulo a trava
    normal é "só fecha quem assinou o contrato", e não existe fluxo de
    contrato pro estande — a confirmação do dono vendo o comprovante É a
    evidência de venda aqui. Suposição de produto (não técnica): se um dia a
    Outlet Chic quiser assinatura de contrato pro estande, revisar este ponto.

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
        r = c.execute(
            f"""update evento_stands set status='vendido', pre_reserva_ate=null,
                       atualizado_em=now()
                 where conta_id=%s and codigo=%s and status='pre_reservado'
                 returning {_COLS}""",
            (conta_id, codigo)).fetchone()
        c.commit()
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
            financeiro = vendas.fechar_orcamento(pool, conta_id, stand["orcamento_id"],
                                                 criado_por=membro_id, por_assinatura=True)
        except Exception as e:  # noqa: BLE001 — o estande JÁ VENDEU; o título é bônus
            _log.warning("evento_stands: confirmar_pagamento vendeu %s/%s mas o "
                        "lançamento financeiro falhou: %s: %s", conta_id, codigo,
                        type(e).__name__, e)
    _log.info("evento_stands: pagamento confirmado — conta %s, estande %s", conta_id, codigo)
    return {"ok": True, "stand": stand, "financeiro": financeiro}


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
                      comprovante_em=null, atualizado_em=now()
                where conta_id=%s and codigo=%s and status <> 'livre'""",
            (conta_id, codigo))
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
                  set status='livre', pre_reserva_ate=null, atualizado_em=now()
                where status='pre_reservado' and pre_reserva_ate is not null
                  and pre_reserva_ate <= %s
                returning id, conta_id, codigo, pavilhao, zona""",
            (agora,)).fetchall()
        c.commit()
    expirados = [{"id": r[0], "conta_id": r[1], "codigo": r[2], "pavilhao": r[3],
                  "zona": r[4]} for r in rows]
    if expirados:
        _log.info("evento_stands: %d estande(s) voltaram a livre (prazo vencido)",
                  len(expirados))
    return expirados
