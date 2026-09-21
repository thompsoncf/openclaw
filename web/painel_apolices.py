"""A tela Renovações: /painel/renovacoes — a carteira de apólices da corretora.

É a tela do segundo relógio. Quem vende festa tem a data da festa; quem vende
seguro tem o fim da vigência — e até esta entrega o fim da vigência não era
guardado em lugar nenhum, o que deixou `fu_festa_dias` nulo no perfil de Raio-X
da corretora (migração 242).

SÓ APARECE PRA CORRETORA (regra 6): o perfil do nicho tem que ser `seguros`. Uma
tela de apólice numa conta de festa seria exatamente o erro que a regra 6 nasceu
pra impedir.

QUEM VÊ O QUÊ, e é a decisão do dono de 17/09/2026 ("alerta pro corretor"):
o dono e o gestor veem a carteira inteira; o corretor vê a fila dele. O ALERTA —
o push de 60/30/15, em `finance/lembretes._renovacoes` — vai pro corretor da
apólice. A tela é onde se olha; o push é o que chega sem pedir.

O CADASTRO COMEÇA POR AUTO (mesma decisão). O formulário pergunta placa, modelo,
ano e classe de bônus porque é isso que a corretora tem hoje; a tabela aguenta
vida e residencial pelo `bem` jsonb desde o primeiro dia, e o que falta pra eles
é formulário, não banco.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from psycopg.errors import UniqueViolation as _UniqueViolation

from db.conexao import get_pool
from finance import apolice_pdf as apdf
from finance import apolices as ap
from finance import comprovantes as _cofre
from finance import funil_perda as _fp
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_apolices")

# a tela é lida no Brasil, como o resto do Cockpit e do Raio-X
_TZ = ZoneInfo("America/Sao_Paulo")

_PAPEIS_OK = ("dono", "gestor", "vendedor")


def _acesso(request: Request):
    """Devolve (conta, gerencia, redirect). `gerencia` é quem vê a carteira inteira."""
    conta = conta_logada(request)
    if conta is None:
        return None, False, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in _PAPEIS_OK:
        return None, False, RedirectResponse("/painel", status_code=303)
    # Regra 6: a tela segue o nicho. `seguros` e mais nenhum.
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "seguros":
        return None, False, RedirectResponse("/painel", status_code=303)
    return conta, papel in ("dono", "gestor"), None


def _brl(centavos) -> str:
    v = (int(centavos or 0)) / 100
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cent(txt: str) -> int:
    """'4.088,57' / '4088.57' / '' → centavos. Vazio é 0, não erro: a corretora
    cadastra a apólice antes de saber o prêmio mais vezes do que se imagina."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return 0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return int(round(float(t) * 100))
    except ValueError:
        return 0


def _data(txt: str):
    t = (txt or "").strip()
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def _int(txt: str):
    t = (txt or "").strip()
    try:
        return int(t) if t else None
    except ValueError:
        return None


def _membro_logado(request: Request) -> int | None:
    return request.session.get("membro_id")


def _corretores(pool, conta_id: int) -> list[tuple]:
    with pool.connection() as c:
        return c.execute(
            "select id, coalesce(nullif(nome,''), email, 'sem nome') from membros "
            " where conta_id=%s and coalesce(ativo,true) "
            "   and papel in ('dono','gestor','vendedor') order by 2", (conta_id,)).fetchall()


def _clientes(pool, conta_id: int) -> list[tuple]:
    with pool.connection() as c:
        return c.execute(
            "select id, nome from clientes where dono_id=%s and ativo "
            " order by nome limit 500", (conta_id,)).fetchall()


#: As três abas. Existem porque a tela fazia TRÊS TRABALHOS de frequências
#: diferentes na mesma rolagem — ver a fila é diário, cadastrar é eventual,
#: configurar percentual é uma vez na vida — e dava o mesmo peso aos três. Com uma
#: apólice cadastrada, a página mostrava três contadores zerados, 120 palavras de
#: lei e um formulário de 21 campos ANTES da única linha que interessava.
ABAS = (("fila", "Renovações"), ("carteira", "Carteira"), ("percentuais", "Percentuais"))


def _contexto(request: Request, conta, gerencia: bool, *, aba: str = "fila",
              busca: str = "") -> dict:
    """Tudo que a tela precisa, num lugar só — o GET e a conferência do PDF
    renderizam o MESMO template com o MESMO contexto; a conferência só acrescenta
    `conferir` e `form`. Duas montagens de contexto seriam duas telas que divergem."""
    pool = get_pool()
    conta_id = conta[0]
    hoje = date.today()
    if aba not in dict(ABAS):
        aba = "fila"
    # o vendedor não configura o que a conta inteira usa, nem vê a carteira toda
    if not gerencia and aba == "percentuais":
        aba = "fila"
    # O corretor vê a fila dele; o dono e o gestor veem a carteira inteira. Mesmo
    # desenho da Fila do vendedor — e é o que o dono pediu.
    so_meu = None if gerencia else _membro_logado(request)
    fila = ap.a_vencer(pool, conta_id, dias=ap.HORIZONTE, hoje=hoje, corretor_id=so_meu)
    carteira = ap.listar(pool, conta_id, hoje=hoje, busca=busca) if gerencia else []
    # os três degraus, pra faixa do topo. A lista vai até 90 dias e o alerta só
    # sai em 60 — a diferença entre as duas coisas é o ponto do desenho.
    em_alerta = [a for a in fila if a["degrau"] is not None]
    vencidas = [a for a in fila if (a["dias"] or 0) < 0]
    # o que vem DEPOIS do horizonte: é o que transforma "nenhuma apólice vencendo"
    # de mentira útil em informação. Só custa uma consulta quando a fila está vazia.
    proxima = ap.proxima(pool, conta_id, hoje=hoje, corretor_id=so_meu) if not fila else None
    return dict(titulo="Renovações", secao_ativa="renovacoes", gerencia=gerencia,
                aba=aba, abas=ABAS, busca=busca, proxima=proxima,
                fila=fila, carteira=carteira, em_alerta=em_alerta, vencidas=vencidas,
                n_carteira=ap.total_da_carteira(pool, conta_id) if gerencia else 0,
                comissoes=ap.comissoes(pool, conta_id),
                corretores=_corretores(pool, conta_id),
                clientes=_clientes(pool, conta_id),
                ramos=ap.RAMOS, situacoes=ap.SITUACOES, degraus=ap.DEGRAUS,
                horizonte=ap.HORIZONTE, hoje=hoje, brl=_brl,
                # o import só aparece se o cofre estiver ligado — melhor não ter botão
                # do que botão que engole a apólice do cliente (mesma regra do midia_cofre)
                cofre_ok=_cofre.configurado(),
                # QUANTOS ESTÃO ESPERANDO CONFERÊNCIA. Sem este número a tela de
                # Renovações não diz nada sobre o que o leitor já leu: a lista
                # mora DENTRO da janela "+ nova apólice", que é um fluxo de
                # CRIAR. Em 21/09/2026 o dono recebeu no WhatsApp "está esperando
                # você conferir em Renovações", abriu a tela e não viu nada — a
                # mensagem apontava pra um lugar que não mostrava.
                esperando=ap.esperando_conferencia(pool, conta_id) if gerencia else 0,
                # a janela do segurado (web/janela_lead.kbAbrirSegurado) precisa da
                # lista de motivos de perda DA CONTA e dos chips de decisão
                motivos=_motivos(pool, conta_id),
                decisoes=[{"c": c_, "r": dict(ap.SITUACOES)[c_]} for c_ in ap.DECISOES],
                conferir=None, form=apdf.para_formulario(apdf.Leitura()),
                erro=(request.query_params.get("erro") or "").strip())


@router.get("/painel/renovacoes", response_class=HTMLResponse)
def painel_renovacoes(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    q = request.query_params
    ctx = _contexto(request, conta, gerencia, aba=(q.get("aba") or "fila").strip(),
                    busca=(q.get("busca") or "").strip())
    return _render("renovacoes", request, **ctx)


def _motivos(pool, conta_id: int) -> list[dict]:
    """A lista de motivos de perda da conta, semeada pelo perfil `seguros` se for a
    primeira vez — a MESMA do funil (`funil_perda.motivos`). Uma segunda lista seria
    uma segunda verdade sobre a mesma perda."""
    try:
        with pool.connection() as c:
            with c.transaction():
                return [{"chave": m["chave"], "rotulo": m["rotulo"],
                         "exige_descricao": bool(m["exige_descricao"])}
                        for m in _fp.motivos(c, conta_id, "seguros")]
    except Exception as e:  # noqa: BLE001
        _log.warning("motivos de perda da conta %s falharam: %s: %s", conta_id, type(e).__name__, e)
        return []


def _fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _pct_txt(pct) -> str:
    """Decimal('20.00') -> '20%'; Decimal('12.50') -> '12,5%'. O Decimal vazava com
    duas casas na janela ("20.00%") — foi a única olhada antes de publicar que pegou."""
    from decimal import Decimal
    d = Decimal(str(pct)).normalize()
    txt = format(d, "f").replace(".", ",")
    return txt + "%"


def _fone_txt(tel) -> str:
    """'86994801456' -> '(86) 99480-1456'; com 55 na frente, tira. O que não tiver
    10 ou 11 dígitos volta como veio — melhor torto do que sumido."""
    d = "".join(ch for ch in str(tel or "") if ch.isdigit())
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return str(tel or "")


def _apolice_para_janela(a: dict) -> dict:
    """O que a janela mostra de cada apólice — formatado aqui, não no JS."""
    bem = a.get("bem") or {}
    bem_txt = " · ".join(x for x in (bem.get("modelo"), bem.get("ano"), bem.get("placa"),
                                     "zero km" if str(bem.get("zero_km", "")).lower() in ("sim", "true") else "")
                         if x)
    vi, vf, dias = a.get("vigencia_inicio"), a.get("vigencia_fim"), a.get("dias")
    pct = None
    if vi and vf and vf > vi:
        pct = round(100 * max(0, min((vf - vi).days, (date.today() - vi).days)) / (vf - vi).days)
    if dias is None:
        regua = ""
    elif dias > ap.DEGRAUS[0]:
        regua = f"entra em {_fmt_data(vf - __import__('datetime').timedelta(days=ap.DEGRAUS[0]))} · avisos 60/30/15"
    elif dias >= 0:
        regua = "em andamento · avisos 60/30/15"
    else:
        regua = "encerrada"
    return {
        "id": a["id"], "seguradora": a["seguradora"], "ramo_txt": a["ramo_txt"],
        "situacao": a["situacao"], "situacao_txt": a["situacao_txt"], "viva": a["situacao"] in ap.VIVAS,
        "numero_txt": (f"apólice {a['numero_apolice']}" if a.get("numero_apolice")
                       else (f"proposta {a['numero_proposta']}" if a.get("numero_proposta") else "")),
        "inicio": _fmt_data(vi), "vence": _fmt_data(vf), "dias": dias, "pct_vigencia": pct, "regua_txt": regua,
        "premio_fmt": _brl(a["premio_centavos"]),
        "iof_fmt": _brl(a["iof_centavos"]) if a.get("iof_centavos") else "",
        "parcelas_txt": (f"{a['parcelas']}×" + (f" · dia {a['dia_vencimento']}" if a.get("dia_vencimento") else "")) if a.get("parcelas") else "",
        "comissao_fmt": (_brl(a["comissao_estimada"]) + ("" if a.get("comissao_fechada") else
                         (f" ({_pct_txt(a['comissao_pct'])}, estimada)" if a.get("comissao_pct") is not None else " (estimada)")))
                        if a.get("comissao_estimada") is not None else "",
        "bem_txt": bem_txt, "chassi": bem.get("chassi") or "", "classe_bonus": a.get("classe_bonus") or "",
        "tem_pdf": bool(a.get("tem_pdf")),
        "perda_motivo_txt": (ap.rotulo_motivo_perda(a["perda_motivo"]) if a.get("perda_motivo") else ""),
    }


@router.get("/painel/renovacoes/cliente/{cliente_id}/resumo")
def resumo_do_cliente(request: Request, cliente_id: int):
    """O JSON da JANELA do segurado — `kbAbrirSegurado` em web/janela_lead.py.

    Mora sob /painel/renovacoes de propósito: é o prefixo que o gate já libera pro
    corretor (papel vendedor). /painel/clientes é do dono; o corretor que clica no
    nome na fila dele levaria 303 e a janela diria "não consegui abrir".
    """
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "acesso"}, status_code=403)
    d = ap.ficha_do_cliente(get_pool(), conta[0], cliente_id)
    if d is None:
        return JSONResponse({"ok": False, "erro": "nao_encontrado"}, status_code=404)
    tel = "".join(ch for ch in (d["telefone"] or "") if ch.isdigit())
    end_ = " · ".join(x for x in (d["endereco"], f"{d['cidade']}/{d['uf']}" if d.get("cidade") else None,
                                  d.get("cep")) if x)
    prox = d["proxima"]
    return JSONResponse({
        "ok": True, "id": d["id"], "nome": d["nome"], "tipo": d["tipo"], "documento": d["documento"],
        "desde": _fmt_data(d["desde"]), "telefone": _fone_txt(d["telefone"]), "email": d["email"],
        "endereco_fmt": end_,
        "zap_link": (f"https://wa.me/{tel if tel.startswith('55') else '55' + tel}" if len(tel) >= 10 else ""),
        "n_vivas": d["n_vivas"], "premio_ano_fmt": _brl(d["premio_ano_centavos"]),
        "proxima": ({"vence": _fmt_data(prox["vigencia_fim"]), "dias": prox["dias"]} if prox else None),
        "apolices": [_apolice_para_janela(a) for a in d["apolices"]],
        "conversa": [{"de": m["de"], "texto": m["texto"],
                      "quando": m["quando"].strftime("%d/%m %H:%M") if m["quando"] else ""} for m in d["conversa"]],
        "conversa_id": d["conversa_id"],
        "linha_do_tempo": [{"quando": _fmt_data(q), "texto": t} for (q, t) in d["linha_do_tempo"]],
    })


# ---------------------------------------------------------------- o import

def _ler_e_guardar(conta_id: int, conteudo: bytes, nome: str) -> dict:
    """Lê o PDF e o guarda no cofre. Roda na THREADPOOL — é síncrono e fala com a
    rede (Storage) e com o pymupdf; no event loop travaria os dois workers.

    Guarda ANTES de a pessoa confirmar, de propósito: os bytes não têm onde ficar
    entre a leitura e o "Cadastrar" (60 KB não cabem em cookie, e uma tabela de
    pendentes seria estado novo pra um caso raro). Quem desiste deixa um objeto
    órfão de dezenas de KB num bucket que já guarda comprovantes — custo aceito e
    dito aqui.
    """
    L = apdf.ler(conteudo)
    caminho = f"apolice/{conta_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}.pdf"
    _cofre.subir_em(caminho, conteudo, "application/pdf")
    return {"leitura": L, "caminho": caminho, "nome": (nome or "apolice.pdf")[:120],
            "bytes": len(conteudo)}


@router.post("/painel/renovacoes/importar", response_class=HTMLResponse)
async def importar_pdf(request: Request, arquivo: UploadFile = File(...),
                       # no fio o campo chama `json`; o nome em Python é outro porque
                       # `json` colide com `BaseModel.json` na assinatura do FastAPI
                       quer_json_txt: str = Form("", alias="json")):
    """Recebe a apólice em PDF e devolve a CONFERÊNCIA — nunca grava direto.

    `async def` que só lê o corpo; leitura do PDF, Storage e banco vão pra
    `run_in_threadpool`. É a segunda forma certa do docstring de
    tests/test_event_loop_nao_trava.py — a mesma dos webhooks do wa-qr. Este
    handler não pode entrar na lista PENDENTES.

    O que volta é o MESMO formulário do cadastro manual, pré-preenchido, com as
    checagens em cima. Salvar passa pelo mesmo `salvar_apolice`. Um leitor que
    grava sozinho transforma erro de leitura em dado errado no banco — e vigência
    lida errada é alerta que não dispara, o pior defeito possível nesta tela.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    # A JANELA PEDE JSON; a página antiga (sem JavaScript) continua esperando um 303
    # com o recado na URL. Devolver o 303 pra quem pediu JSON é pior que um erro: o
    # `fetch` segue o redirecionamento, recebe HTML e a janela diz "não respondeu".
    quer_json = bool((quer_json_txt or "").strip())

    def _falhou(msg: str):
        if quer_json:
            return JSONResponse({"ok": False, "erro": msg})
        return RedirectResponse(f"/painel/renovacoes?erro={msg}", status_code=303)

    if not gerencia:
        return _falhou("só o dono e o gestor cadastram apólice")
    from starlette.concurrency import run_in_threadpool
    if not await run_in_threadpool(_cofre.configurado):
        return _falhou("o cofre de documentos não está configurado nesta instalação")
    conteudo = await arquivo.read()
    nome = arquivo.filename or ""
    try:
        r = await run_in_threadpool(_ler_e_guardar, conta[0], conteudo, nome)
    except ValueError as e:
        return _falhou(str(e))
    except Exception as e:  # noqa: BLE001
        _log.warning("import de apólice falhou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return _falhou("não consegui ler este PDF")
    conferir = _conferir_de(r)
    if quer_json:
        # sem `_contexto`: a janela já está montada na tela, e são seis consultas a
        # menos entre soltar o PDF e a conferência aparecer
        return _leitura_em_json(r["leitura"], conferir)
    ctx = await run_in_threadpool(_contexto, request, conta, gerencia)
    ctx["conferir"] = conferir
    ctx["form"] = apdf.para_formulario(r["leitura"])
    return _render("renovacoes", request, **ctx)


def _conferir_de(r: dict, *, origem: dict | None = None) -> dict:
    """O que a conferência mostra, venha o PDF de onde vier.

    `origem` é a marca de quem entrou pelo WhatsApp: fica guardada dentro de
    `apolices.pdf_lido` e é o que tira a mensagem da lista depois de cadastrada.
    """
    L = r["leitura"]
    lido = apdf.resumo_para_guardar(L)
    if origem:
        lido["origem"] = origem
    return {
        "seguradora": L.seguradora, "reconhecida": L.reconhecida, "paginas": L.paginas,
        "checagens": L.checagens, "avisos": L.avisos, "nao_achou": L.nao_achou,
        "n_campos": len(L.campos), "ok": L.ok(), "pdf_nome": r["nome"],
        "pdf_caminho": r["caminho"], "pdf_bytes": r["bytes"], "pdf_lido": lido,
    }


def _conferencia_em_json(form: dict, conferir: dict) -> JSONResponse:
    """A resposta que a janela espera. UMA só pros três caminhos que chegam nela:
    o PDF solto, o PDF do WhatsApp lido na hora, e o que o leitor automático já
    tinha lido. Três montagens da mesma resposta divergiriam no primeiro conserto."""
    return JSONResponse({
        "ok": True,
        "form": form,
        "conf_html": _env.get_template("renovacoes_conf").render(conferir=conferir),
        "pdf": {"caminho": conferir["pdf_caminho"], "nome": conferir["pdf_nome"],
                "bytes": conferir["pdf_bytes"], "lido": conferir["pdf_lido"]},
    })


def _leitura_em_json(L, conferir: dict) -> JSONResponse:
    """O caminho de quem acabou de ler o PDF."""
    return _conferencia_em_json(apdf.para_formulario(L), conferir)


@router.get("/painel/renovacoes/whatsapp")
def pdfs_do_whatsapp(request: Request):
    """Os PDFs que já chegaram no número vinculado, pra janela listar.

    SÓ LISTA. Quem decide que aquilo é uma apólice é a pessoa — na medição de
    18/09 a maioria dos PDFs da conta era boleto, extrato e petição. Ler tudo e
    cadastrar sozinho encheria a carteira de lixo, e vigência errada é alerta que
    não dispara.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "sessão expirada"}, status_code=401)
    if not gerencia:
        return JSONResponse({"ok": False, "erro": "só o dono e o gestor cadastram apólice"})
    pool = get_pool()
    from finance import apolice_leitor as _apl
    itens = [
        {"fonte": "msg", "id": i["mensagem_id"], "de": i["de"], "nome": i["nome"],
         "quando": i["quando"], "kb": round(i["bytes"] / 1024) if i["bytes"] else 0,
         "parece": i["parece_apolice"], "ja": i["ja_cadastrada"],
         "lida": i["lida"], "resumo": _apl_resumo(i), "erro": i["erro_leitura"]}
        for i in ap.pdfs_do_whatsapp(pool, conta[0])]
    # AS OUTRAS PORTAS (305 e a do assistente): o que o corretor mandou pelo
    # Telegram ou pro número do assistente entra na MESMA lista. São três entradas
    # pro mesmo lugar, e separar em três telas faria a pessoa procurar em três
    # cantos o documento que ela acabou de mandar. O rótulo diz por onde veio.
    itens += [
        {"fonte": "lida", "id": i["lida_id"], "de": i["de"] + " · " + i["porta"],
         "nome": i["nome"], "quando": i["quando"],
         "kb": round(i["bytes"] / 1024) if i["bytes"] else 0,
         "parece": True, "ja": False, "lida": True,
         "resumo": _apl_resumo(i), "erro": i["erro_leitura"]}
        for i in _apl.sem_mensagem(pool, conta[0])]
    itens.sort(key=lambda i: i["quando"] or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)
    for i in itens:
        i["quando"] = _quando_txt(i["quando"])
    # a mesma consulta dá os dois números de que a tela precisa pro estado vazio:
    # quantos remetentes existem e quantos estão liberados
    quem = ap.quem_mandou_pdf(pool, conta[0])
    return JSONResponse({
        "ok": True,
        # Telegram e assistente contam como porta aberta: com documento vindo de
        # lá, a lista existe mesmo sem ninguém liberado no WhatsApp
        "remetentes": len(quem) + sum(1 for i in itens if i["fonte"] == "lida"),
        "liberados": sum(1 for q in quem if q["liberado"]),
        "itens": itens})


@router.get("/painel/renovacoes/remetentes")
def remetentes_do_whatsapp(request: Request):
    """Quem pode mandar apólice, e quem anda mandando PDF pro número da empresa.

    As duas listas juntas porque a tela é uma só: liberar é reconhecer um nome
    que já está ali, não digitar telefone.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "sessão expirada"}, status_code=401)
    if not gerencia:
        return JSONResponse({"ok": False, "erro": "só o dono e o gestor mexem nisto"})
    pool = get_pool()
    return JSONResponse({"ok": True, "tipos": [{"c": c_, "r": r_} for c_, r_ in ap.TIPOS_REMETENTE],
                         "liberados": [{"ref": f["contato_ref"], "nome": f["rotulo"],
                                        "tipo": f["tipo"], "tipo_txt": f["tipo_txt"]}
                                       for f in ap.remetentes(pool, conta[0])],
                         "mandaram": [{"ref": q["contato_ref"], "nome": q["nome"],
                                       "quantos": q["quantos"], "ultimo": _quando_txt(q["ultimo"]),
                                       "liberado": q["liberado"]}
                                      for q in ap.quem_mandou_pdf(pool, conta[0])]})


@router.post("/painel/renovacoes/remetentes")
def mudar_remetente(request: Request, acao: str = Form(""), ref: str = Form(""),
                    nome: str = Form(""), tipo: str = Form("corretor")):
    """Libera ou tira um número da lista de quem pode mandar apólice.

    Tirar não apaga apólice nenhuma — só para de sugerir os PDFs daquele número.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "sessão expirada"}, status_code=401)
    if not gerencia:
        return JSONResponse({"ok": False, "erro": "só o dono e o gestor mexem nisto"})
    pool = get_pool()
    try:
        if acao == "tirar":
            ap.tirar_remetente(pool, conta[0], ref)
        else:
            ap.liberar_remetente(pool, conta[0], ref, rotulo=nome, tipo=tipo,
                                 membro_id=_membro_logado(request))
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)})
    except Exception as e:  # noqa: BLE001
        _log.warning("remetente não mudou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return JSONResponse({"ok": False, "erro": "não deu pra salvar"})
    return JSONResponse({"ok": True})


def _apl_resumo(i: dict) -> str:
    """A linha que a janela mostra embaixo do nome quando o leitor já passou."""
    from finance import apolice_leitor as _apl
    return _apl.resumo(i) if i.get("lida") else ""


def _checagens_de(lido) -> list:
    """As checagens voltam do banco como lista de dicionários; o painel espera
    trios. Uma conversão só, aqui, pra a conferência guardada desenhar igual à
    conferência recém-lida."""
    saida = []
    for ch in (lido or {}).get("checagens") or []:
        if isinstance(ch, dict):
            saida.append((ch.get("nome", ""), bool(ch.get("ok")), ch.get("detalhe", "")))
        elif isinstance(ch, (list, tuple)) and len(ch) == 3:
            saida.append(tuple(ch))
    return saida


def _quando_txt(quando) -> str:
    """Data curta, no fuso de quem olha a tela."""
    if not quando:
        return ""
    try:
        return quando.astimezone(_TZ).strftime("%d/%m")
    except Exception:  # noqa: BLE001
        return quando.strftime("%d/%m")


@router.post("/painel/renovacoes/lida/{lida_id}")
def abrir_pre_cadastro(request: Request, lida_id: int):
    """A conferência de um pré-cadastro, pelo id dele.

    É o caminho do que chegou pelo Telegram, que não tem mensagem pra procurar.
    Nada é baixado: a leitura já está guardada."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "sessão expirada"}, status_code=401)
    if not gerencia:
        return JSONResponse({"ok": False, "erro": "só o dono e o gestor cadastram apólice"})
    from finance import apolice_leitor as _apl
    ja = _apl.por_id(get_pool(), conta[0], lida_id)
    if not ja:
        return JSONResponse({"ok": False, "erro": "não achei este documento"})
    if ja["erro"]:
        return JSONResponse({"ok": False, "erro": ja["erro"]})
    return _conferencia_em_json(ja["form"] or {}, _conferir_guardado(ja, {"lida": lida_id}))


def _conferir_guardado(ja: dict, origem: dict) -> dict:
    """A conferência montada a partir do que o leitor já guardou. Mesmo formato do
    `_conferir_de`, que monta a partir de uma leitura recém-feita."""
    lido = ja["lido"] or {}
    return {
        "seguradora": ja["seguradora"] or "", "reconhecida": ja["reconhecida"],
        "paginas": lido.get("paginas") or 0,
        "checagens": _checagens_de(lido),
        "avisos": lido.get("avisos") or [], "nao_achou": lido.get("nao_achou") or [],
        "n_campos": len(lido.get("campos") or {}), "ok": True,
        "pdf_nome": ja["pdf_nome"] or "apolice.pdf", "pdf_caminho": ja["pdf_caminho"],
        "pdf_bytes": ja["pdf_bytes"] or 0,
        "pdf_lido": dict(lido, origem=origem),
    }


@router.post("/painel/renovacoes/whatsapp/{mensagem_id}")
def ler_pdf_do_whatsapp(request: Request, mensagem_id: int):
    """Busca UM PDF no CDN do WhatsApp e cai na mesma conferência do PDF solto.

    `def` e não `async def`: busca na rede e escreve no cofre, e o FastAPI roda
    handler síncrono na threadpool (`tests/test_event_loop_nao_trava.py`).

    Nada é gravado como apólice aqui — o caminho de salvar continua sendo um só.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "sessão expirada"}, status_code=401)
    if not gerencia:
        return JSONResponse({"ok": False, "erro": "só o dono e o gestor cadastram apólice"})
    if not _cofre.configurado():
        return JSONResponse({"ok": False, "erro": "o cofre de documentos não está "
                                                  "configurado nesta instalação"})
    # SE O LEITOR AUTOMÁTICO JÁ PASSOU (migração 304), a conferência sai daqui:
    # nada é baixado de novo e o toque é instantâneo. É o caso normal desde que o
    # leitor entrou; o caminho de baixar na hora fica pro documento antigo, pro que
    # chegou antes do leitor existir e pro reprocesso.
    from finance import apolice_leitor as _apl
    ja = _apl.uma(get_pool(), conta[0], mensagem_id)
    if ja and not ja["erro"] and ja["pdf_caminho"]:
        return _conferencia_em_json(
            ja["form"] or {}, _conferir_guardado(ja, {"whatsapp_msg": mensagem_id}))
    if ja and ja["erro"]:
        return JSONResponse({"ok": False, "erro": ja["erro"]})
    achado = ap.ref_do_pdf(get_pool(), conta[0], mensagem_id)
    if not achado:
        return JSONResponse({"ok": False, "erro": "não achei este documento"})
    from finance import wa_midia as _wm
    try:
        conteudo = b"".join(_wm.buscar(achado["ref"], achado["tipo"]))
    except _wm.Expirou:
        # o recado exato importa: o arquivo não sumiu por erro nosso, e a saída é
        # pedir de novo — não adianta tentar outra vez
        return JSONResponse({"ok": False, "erro": "o WhatsApp já apagou este arquivo. "
                                                  "Peça de novo a quem mandou."})
    except Exception as e:  # noqa: BLE001
        _log.warning("pdf do whatsapp %s falhou (conta %s): %s: %s",
                     mensagem_id, conta[0], type(e).__name__, e)
        return JSONResponse({"ok": False, "erro": "não consegui baixar este arquivo agora"})
    try:
        r = _ler_e_guardar(conta[0], conteudo, achado["nome"])
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)})
    except Exception as e:  # noqa: BLE001
        _log.warning("leitura do pdf do whatsapp falhou (conta %s): %s: %s",
                     conta[0], type(e).__name__, e)
        return JSONResponse({"ok": False, "erro": "não consegui ler este PDF"})
    return _leitura_em_json(r["leitura"],
                            _conferir_de(r, origem={"whatsapp_msg": mensagem_id}))


@router.get("/painel/renovacoes/apolice/{apolice_id}/pdf")
def ver_pdf(request: Request, apolice_id: int):
    """Entrega o PDF guardado. É ESTA ROTA que faz o bucket poder ser privado: o
    `conta_id` no WHERE impede uma corretora de ler a apólice de outra trocando o
    número na URL. Sem cache — apólice tem CPF, endereço e placa."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    a = ap.uma(get_pool(), conta[0], apolice_id)
    if not a or not a.get("pdf_caminho"):
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    try:
        conteudo, tipo = _cofre.ler(a["pdf_caminho"])
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=502)
    nome = (a.get("pdf_nome") or "apolice.pdf").replace('"', "")
    return Response(conteudo, media_type=tipo or "application/pdf", headers={
        "Content-Disposition": f'inline; filename="{nome}"', "Cache-Control": "no-store"})


@router.post("/painel/renovacoes/apolice")
def salvar_apolice(request: Request,
                   apolice_id: str = Form(""), cliente_id: str = Form(""),
                   corretor_id: str = Form(""), seguradora: str = Form(""),
                   ramo: str = Form("auto"), numero_proposta: str = Form(""),
                   numero_apolice: str = Form(""), vigencia_inicio: str = Form(""),
                   vigencia_fim: str = Form(""), situacao: str = Form("proposta"),
                   premio: str = Form(""), iof: str = Form(""), franquia: str = Form(""),
                   comissao_pct: str = Form(""), comissao: str = Form(""),
                   classe_bonus: str = Form(""), parcelas: str = Form(""),
                   dia_vencimento: str = Form(""),
                   placa: str = Form(""), modelo: str = Form(""), ano: str = Form(""),
                   chassi: str = Form(""), obs: str = Form(""),
                   # o segurado, quando veio do PDF (ou digitado): acha/cria o cliente
                   nome: str = Form(""), cpf: str = Form(""), telefone: str = Form(""),
                   email: str = Form(""), endereco: str = Form(""),
                   # o PDF guardado pela conferência
                   pdf_caminho: str = Form(""), pdf_nome: str = Form(""),
                   pdf_bytes: str = Form(""), pdf_lido: str = Form(""),
                   # a janela manda `json=1` e espera a LINHA da tabela de volta
                   quer_json_txt: str = Form("", alias="json")):
    """Cadastra ou edita uma apólice.

    `def` e não `async def`: o handler escreve no banco de forma síncrona, e
    handler async fazendo isso trava o event loop do processo inteiro
    (`tests/test_event_loop_nao_trava.py` cobra isso de todo handler novo).

    O CAMPO DE AUTO vira `bem` jsonb — placa, modelo, ano, chassi. É o único
    formulário por ramo que existe hoje (decisão do dono: "começa por auto"), e a
    coluna já aguenta os outros sem migração nova.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    quer_json = bool((quer_json_txt or "").strip())

    def _falhou(msg: str, aba: str = ""):
        if quer_json:
            return JSONResponse({"ok": False, "erro": msg})
        destino = f"/painel/renovacoes?aba={aba}&erro={msg}" if aba else f"/painel/renovacoes?erro={msg}"
        return RedirectResponse(destino, status_code=303)

    pool = get_pool()
    # O CLIENTE PELO CPF. Se a pessoa escolheu um cliente no seletor, ele manda; se
    # não escolheu e o CPF veio (do PDF ou digitado), acha ou cria pelo MESMO caminho
    # do cadastro de clientes (`puxar_ou_criar_cliente`): dedup por documento,
    # identidade em `pessoas`, relação em `clientes`. Foi exatamente o que se fez à
    # mão pra Maria de Fátima em 18/09 — agora sem mão.
    cliente = _int(cliente_id)
    cpf_d = "".join(ch for ch in (cpf or "") if ch.isdigit())
    if cliente is None and cpf_d and (nome or "").strip():
        try:
            from finance import clientes as _cl
            cliente = _cl.puxar_ou_criar_cliente(
                pool, conta[0], cpf=cpf_d, nome=nome.strip(), celular=telefone or None,
                email=email or None, endereco=endereco or None,
                obs="Segurado — cadastrado pela apólice" if pdf_caminho else None)
        except ValueError as e:
            return _falhou(f"cliente: {e}")
    bem = {k: v.strip() for k, v in (("placa", placa), ("modelo", modelo),
                                     ("ano", ano), ("chassi", chassi)) if v.strip()}
    pct = (comissao_pct or "").strip().replace(",", ".")
    dados = {
        "cliente_id": cliente, "corretor_id": _int(corretor_id),
        "seguradora": seguradora, "ramo": ramo,
        "numero_proposta": (numero_proposta or "").strip() or None,
        "numero_apolice": (numero_apolice or "").strip() or None,
        "vigencia_inicio": _data(vigencia_inicio), "vigencia_fim": _data(vigencia_fim),
        "situacao": situacao,
        "premio_centavos": _cent(premio), "iof_centavos": _cent(iof),
        "franquia_centavos": _cent(franquia),
        "comissao_pct": (pct or None),
        # comissão VAZIA é None, não zero: zero diria "esta apólice não paga
        # comissão" e apagaria o percentual padrão da seguradora em silêncio.
        "comissao_centavos": (_cent(comissao) if (comissao or "").strip() else None),
        "classe_bonus": (classe_bonus or "").strip() or None,
        "parcelas": _int(parcelas), "dia_vencimento": _int(dia_vencimento),
        "bem": bem, "condutor": {}, "coberturas": [],
        "renovacao_de": None, "obs": (obs or "").strip() or None,
    }
    if (pdf_caminho or "").strip():
        import json as _json
        try:
            lido = _json.loads(pdf_lido) if (pdf_lido or "").strip() else None
        except ValueError:
            lido = None
        dados.update({"pdf_caminho": pdf_caminho.strip(), "pdf_nome": (pdf_nome or "").strip() or None,
                      "pdf_bytes": _int(pdf_bytes), "pdf_lido": lido,
                      "pdf_lido_em": datetime.now(timezone.utc)})
    try:
        aid = ap.salvar(pool, conta[0], dados, _int(apolice_id))
    except ValueError as e:
        return _falhou(str(e))
    except _UniqueViolation:
        # o índice da 278 (nº da apólice) ou o da 286 (nº da proposta): é a mesma
        # apólice sendo cadastrada de novo — o caso comum é reimportar o mesmo PDF.
        # "não deu pra salvar" esconderia o motivo; este diz o que fazer.
        return _falhou("esta apólice já está cadastrada — procure pelo número na Carteira",
                       aba="carteira")
    except Exception as e:  # noqa: BLE001
        _log.warning("apólice não salvou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return _falhou("não deu pra salvar")
    if quer_json:
        # A LINHA VOLTA PRONTA, do mesmo template que a tabela usa. O JavaScript só
        # a encaixa na ordem certa — nada de recarregar a página pra ver o que
        # acabou de ser cadastrado.
        linha = ap.uma(pool, conta[0], aid)
        resumo = ""
        if linha:
            resumo = (f"{linha['cliente']} · {linha['seguradora']} {linha['ramo_txt']} · "
                      f"vence {_fmt_data(linha['vigencia_fim'])}")
        return JSONResponse({
            "ok": True, "id": aid, "resumo": resumo,
            "linha_html": (_env.get_template("renovacoes_linha").render(a=linha, brl=_brl)
                           if linha else ""),
            "n_carteira": ap.total_da_carteira(pool, conta[0]),
        })
    return RedirectResponse("/painel/renovacoes", status_code=303)


@router.post("/painel/renovacoes/apolice/{apolice_id}/situacao")
def mudar_situacao(request: Request, apolice_id: int, situacao: str = Form(""),
                   status: str = Form(""), motivo: str = Form(""),
                   perda_descricao: str = Form(""),
                   # no fio o campo chama `json` (é o que a folha do funil manda); o nome
                   # Python é outro porque `json` sombreia `BaseModel.json` no pydantic
                   quer_json_txt: str = Form("", alias="json")):
    """Move a apólice de estado — pelo seletor da fila ou pelo chip da janela.

    'perdida' EXIGE MOTIVO, da lista da conta (`funil_perda.motivos`, perfil
    seguros). É o que faz o estado valer alguma coisa no Raio-X. A janela manda
    `status`/`motivo`/`perda_descricao` no formato da folha "Por que perdeu?" do
    funil (`kbPerguntarMotivo`) e `json=1`; o seletor da fila manda `situacao` e
    espera redirect. Os dois caminhos passam pelo mesmo `ap.perder`.

    "Renovada" só fecha esta apólice. A nova chega como documento (decisão do
    dono, 18/09: quem emite é a seguradora; nada é fabricado).
    """
    conta, _g, redir = _acesso(request)
    quer_json = (quer_json_txt or "").strip() == "1"
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "acesso"}, status_code=403) if quer_json else redir
    sit = ((situacao or status) or "").strip().lower()
    if sit not in dict(ap.SITUACOES):
        return JSONResponse({"ok": False, "erro": "situacao"}, status_code=400) if quer_json \
            else RedirectResponse("/painel/renovacoes", status_code=303)
    pool = get_pool()
    if sit == "perdida":
        try:
            ap.perder(pool, conta[0], apolice_id, motivo=motivo, descricao=perda_descricao,
                      motivos_validos=_motivos(pool, conta[0]))
        except ValueError as e:
            erro = str(e)
            if quer_json:
                return JSONResponse({"ok": False, "erro": erro}, status_code=400)
            return RedirectResponse(f"/painel/renovacoes?erro=perdida precisa de motivo ({erro})", status_code=303)
    else:
        with pool.connection() as c:
            with c.transaction():
                c.execute("update apolices set situacao=%s, atualizado_em=now() "
                          " where id=%s and conta_id=%s", (sit, apolice_id, conta[0]))
    if quer_json:
        return JSONResponse({"ok": True, "situacao": sit})
    return RedirectResponse("/painel/renovacoes", status_code=303)


@router.post("/painel/renovacoes/comissao")
def salvar_comissao(request: Request, seguradora: str = Form(""),
                    ramo: str = Form(""), pct: str = Form("")):
    """O percentual padrão da seguradora. Só dono e gestor: é número da casa, não
    do corretor — e é ele que alimenta o "comissão proposta × fechada" do Raio-X."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return RedirectResponse("/painel/renovacoes", status_code=303)
    if not ap.salvar_comissao(get_pool(), conta[0], seguradora, ramo, pct):
        return RedirectResponse("/painel/renovacoes?aba=percentuais&erro=percentual inválido",
                                status_code=303)
    return RedirectResponse("/painel/renovacoes?aba=percentuais", status_code=303)


@router.post("/painel/renovacoes/comissao/{cid}/apagar")
def apagar_comissao(request: Request, cid: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if gerencia:
        ap.apagar_comissao(get_pool(), conta[0], cid)
    return RedirectResponse("/painel/renovacoes?aba=percentuais", status_code=303)


_TPL_LINHA = r"""{# UMA linha da carteira. Vive sozinha porque o cadastro devolve ela
   pronta em JSON e o JavaScript a insere na tabela sem recarregar — duas
   marcações pra mesma linha divergiriam no primeiro conserto. #}
<tr data-id="{{ a.id }}" data-vence="{{ a.vigencia_fim.isoformat() }}"
    {% if a.cliente_id %}class="rn-linha" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this,'cliente')"{% endif %}>
  <td class="rn-cli">{% if a.cliente_id %}<button type="button" class="rn-abre" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this.closest('tr'),'cliente')">{{ a.cliente }}</button>
      {% else %}{{ a.cliente }} <span class="rn-pill" title="apólice sem cliente ligado">sem cliente</span>{% endif %}
      {% if a.bem.placa %} <span class="rn-pill">{{ a.bem.placa }}</span>{% endif %}
      {% if a.tem_pdf %} <a class="rn-pdf" href="/painel/renovacoes/apolice/{{ a.id }}/pdf" target="_blank">PDF</a>{% endif %}</td>
  <td>{{ a.seguradora }}</td>
  <td>{% if a.cliente_id %}<button type="button" class="rn-abre fraco" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this.closest('tr'),'apolice')">{{ a.ramo_txt }}</button>{% else %}{{ a.ramo_txt }}{% endif %}</td>
  <td>{{ a.vigencia_fim.strftime('%d/%m/%Y') }}{% if a.dias is not none and a.dias >= 0 %}
      <span class="rn-pill">{{ a.dias }}d</span>{% endif %}</td>
  <td>{{ a.situacao_txt }}</td>
  <td>{{ brl(a.premio_centavos) }}</td>
  <td>{% if a.comissao_estimada is not none %}{{ brl(a.comissao_estimada) }}{% else %}—{% endif %}</td>
</tr>
"""


_TPL_CONF = r"""{# O que o leitor achou no PDF. Template próprio porque o mesmo painel
   é renderizado de dois jeitos: na página (quando o POST veio sem JavaScript) e
   dentro da janela, devolvido em JSON pela importação. #}
<div class="rn-conf">
  <div class="cab">
    <h3>O que eu li de <b>{{ conferir.pdf_nome }}</b></h3>
    {% if conferir.reconhecida %}<span class="rn-tag d60">{{ conferir.seguradora }} · {{ conferir.n_campos }} campos</span>
    {% else %}<span class="rn-tag d15">layout não reconhecido</span>{% endif %}
    <span class="rn-pill">{{ conferir.paginas }} pág.</span>
  </div>
  {% if conferir.checagens %}
  <div class="chk">
    {% for nome, ok, det in conferir.checagens %}
    <span class="{% if ok %}ok{% else %}no{% endif %}">{% if ok %}✓{% else %}✗{% endif %}</span>
    <span>{{ nome }} <span style="color:var(--txt-mut)">· {{ det }}</span></span>
    {% endfor %}
  </div>
  {% endif %}
  {% for av in conferir.avisos %}<div class="rn-aviso ambar" style="margin:.5rem 0 0">{{ av }}</div>{% endfor %}
  {% if conferir.nao_achou %}
  <div class="falt">Não achei no papel: {{ conferir.nao_achou|join(', ') }} — preencha o que souber.</div>
  {% endif %}
  <div class="falt">As coberturas não são lidas, de propósito: ficam no PDF anexado, que é a parte
    que erra calado quando se tenta extrair. <b>Nada é salvo sem você conferir abaixo.</b></div>
</div>
"""


_TPL = r"""{% extends "base" %}{% block conteudo %}
{# CSS e JS da janela/balão são strings CRUAS (Markup) — o embrulho é da tela, como no
   funil e no Follow-up. Soltas, viram um muro de texto no topo da Carteira (visto
   em 18/09, na única olhada antes de publicar). #}
<style>{{ balao_css }}{{ janela_css }}</style>
<style>
/* A TELA TEM UMA LARGURA SÓ. O miolo da base centraliza cada bloco na largura
   dele: o título e as abas saíam estreitos e centrados, e a tabela (que tem
   min-width) vazava mais larga que tudo em cima dela. Este embrulho dá a mesma
   medida pra tudo, alinhado à esquerda, como uma tela de trabalho. */
.rn-pag{width:100%;max-width:1040px;margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.rn-topo{display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap;margin-bottom:.2rem}
/* mesma medida do Raio-X e do Follow-up: h1 de 1.5rem e uma linha de leitura embaixo */
.rn-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.rn-topo .sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.rn-abas{display:flex;gap:.15rem;border-bottom:1px solid var(--borda);margin:1.1rem 0 1.1rem;overflow-x:auto}
.rn-abas a{padding:.45rem .8rem;font-size:.85rem;color:var(--txt-mut);text-decoration:none;
  border-bottom:2px solid transparent;white-space:nowrap}
.rn-abas a.on{color:var(--verde-claro);border-bottom-color:var(--verde);font-weight:600}
.rn-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.5rem;margin-bottom:1rem}
.rn-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.rn-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.rn-cx .v{font-size:1.35rem;font-weight:700;line-height:1.15}
.rn-cx .n{font-size:.72rem;color:var(--txt-mut)}
.rn-cx.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.rn-cx.alerta .v{color:#F0DCA6}
.rn-cx.venc{background:var(--neon-fundo);border-color:var(--neon-borda)}
.rn-lista{display:flex;flex-direction:column;gap:.45rem;margin-bottom:1rem}
.rn-card{background:var(--card);border:1px solid var(--borda);border-left:3px solid var(--borda);
  border-radius:11px;padding:.6rem .8rem}
.rn-card.d15{border-left-color:#E06C6C}
.rn-card.d30{border-left-color:var(--amar)}
.rn-card.d60{border-left-color:#8FC9E6}
.rn-card.passou{border-left-color:#E06C6C;background:var(--ambar-fundo)}
.rn-card .cab{display:flex;gap:.45rem;align-items:baseline;flex-wrap:wrap}
.rn-card .quem{font-weight:700;font-size:.95rem}
.rn-card .meta{font-size:.79rem;color:var(--txt-mut);margin-top:.1rem}
.rn-card .dinheiro{font-size:.79rem;color:var(--txt);opacity:.85;margin-top:.15rem}
.rn-acoes{display:flex;gap:.35rem;align-items:center;flex-wrap:wrap;margin-top:.5rem}
.rn-acoes form{display:inline-flex;gap:.3rem;align-items:center;margin:0}
/* `width:auto;margin:0` explícitos: o CSS global do app tem button{width:100%;
   margin-top:1.4rem} pra botão de formulário, e o "Buscar" virava uma barra verde
   de largura total embaixo da busca. Mesma armadilha que a janela do lead documenta. */
.rn-bt{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;width:auto;margin:0;
  padding:.4rem .85rem;font-size:.8rem;font-weight:700;cursor:pointer;white-space:nowrap}
.rn-bt.fraco{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);font-weight:500}
.rn-tag{font-size:.67rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
  padding:.1rem .42rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.rn-tag.d15{background:rgba(224,108,108,.15);border-color:#E06C6C;color:#E9A0A0}
.rn-tag.d30{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#F0DCA6}
.rn-tag.d60{background:var(--azul-fundo);border-color:var(--azul-borda);color:#8FC9E6}
.rn-pill{font-size:.72rem;color:var(--txt-mut);background:var(--card-2);border:1px solid var(--borda);
  border-radius:999px;padding:.08rem .45rem}
.rn-bloco{background:var(--card);border:1px solid var(--borda);border-radius:13px;padding:.85rem 1rem;margin-bottom:1rem}
.rn-bloco h3{margin:0 0 .2rem;font-size:.95rem}
.rn-bloco .sub{font-size:.79rem;color:var(--txt-mut);margin-bottom:.8rem;line-height:1.55}
.rn-grupo{border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.6rem}
.rn-grupo > .t{font-size:.67rem;text-transform:uppercase;letter-spacing:.08em;color:var(--txt-mut);
  font-weight:700;margin-bottom:.45rem}
.rn-grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem}
.rn-campo{display:flex;flex-direction:column;gap:.18rem}
.rn-campo label{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}
.rn-campo.chave label{color:var(--verde-claro)}
.rn-campo input,.rn-campo select{background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;padding:.38rem .5rem;color:var(--txt);font-size:.85rem;width:100%}
.rn-campo.chave input{border-color:var(--neon-borda)}
.rn-busca{display:flex;gap:.5rem;margin-bottom:.8rem;align-items:center}
.rn-busca input{flex:1;min-width:0;background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;padding:.42rem .6rem;color:var(--txt);font-size:.85rem}
.rn-rol{overflow-x:auto;border:1px solid var(--borda);border-radius:11px}
.rn-tab{border-collapse:collapse;width:100%;min-width:640px;font-size:.84rem;font-variant-numeric:tabular-nums}
.rn-tab th{text-align:right;padding:.5rem .6rem;font-size:.66rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--txt-mut);font-weight:500;background:var(--card-2);
  border-bottom:1px solid var(--borda);white-space:nowrap}
.rn-tab th:first-child,.rn-tab td:first-child{text-align:left}
.rn-tab td{text-align:right;padding:.45rem .6rem;border-bottom:1px solid var(--borda);white-space:nowrap}
.rn-tab tr:last-child td{border-bottom:0}
.rn-aviso{border-radius:10px;padding:.7rem .85rem;font-size:.84rem;line-height:1.55;margin-bottom:.9rem}
.rn-aviso.azul{background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6}
.rn-aviso.ambar{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:#F0DCA6}
.rn-vazio{background:var(--card);border:1px solid var(--borda);border-radius:13px;padding:1.1rem 1rem;margin-bottom:1rem}
.rn-vazio .t{font-weight:600;font-size:.95rem;margin-bottom:.15rem}
.rn-vazio .s{font-size:.82rem;color:var(--txt-mut);line-height:1.6}
/* o nome abre a janela. Sem sublinhado pontilhado (lia como link quebrado): negrito,
   mãozinha, e a linha inteira acende ao passar — é a linha que é clicável. */
.rn-abre{background:none;border:0;padding:0;margin:0;width:auto;font:inherit;color:var(--txt);font-weight:600;cursor:pointer;text-align:left}
.rn-abre:hover,.rn-abre:focus-visible{color:var(--verde-claro);outline:none}
.rn-abre.fraco{font-weight:400;color:var(--txt-mut)}
.rn-abre.fraco:hover{color:var(--txt)}
.rn-tab tr.rn-linha{cursor:pointer}
.rn-tab tr.rn-linha:hover td{background:rgba(37,211,102,.05)}
.rn-tab td.rn-cli{white-space:normal;min-width:180px}
details.rn-det{margin-bottom:1rem}
details.rn-det > summary{cursor:pointer;font-size:.8rem;color:var(--txt-mut);list-style:none;
  display:inline-flex;gap:.3rem;align-items:center;padding:.3rem .65rem;border:1px solid var(--borda);
  border-radius:999px;background:var(--card)}
details.rn-det > summary::-webkit-details-marker{display:none}
details.rn-det[open] > summary{margin-bottom:.6rem}
/* SOLTAR O ARQUIVO AQUI é o caminho principal (o dono: "importar a apólice em PDF
   e jogar aqui"). O input nativo fica escondido mas alcançável pelo teclado — quem
   navega sem mouse chega nele pelo <label>, e sem JavaScript o botão de baixo
   ainda envia o formulário do jeito antigo. */
.rn-drop{position:relative;border:1.5px dashed var(--neon-borda);background:var(--neon-fundo);
  border-radius:12px;padding:1.5rem 1rem;margin-bottom:.8rem;text-align:center}
.rn-drop.sobre{border-color:var(--verde);background:rgba(37,211,102,.12)}
.rn-drop .t{font-weight:600;font-size:.95rem;color:var(--verde-claro)}
.rn-drop .s{font-size:.78rem;color:var(--txt-mut);line-height:1.55;margin:.3rem auto .8rem;max-width:44ch}
.rn-drop input[type=file]{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.rn-drop input[type=file]:focus-visible + label{outline:2px solid var(--verde);outline-offset:2px}
.rn-drop label{display:inline-block;cursor:pointer}
.rn-drop .semjs{display:block;margin:.6rem auto 0;font-size:.74rem}
.rn-conf{background:var(--card);border:1px solid var(--neon-borda);border-radius:13px;padding:.85rem 1rem;margin-bottom:.8rem}
.rn-conf .cab{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap;margin-bottom:.5rem}
.rn-conf h3{margin:0;font-size:.95rem}
.rn-conf .chk{display:grid;grid-template-columns:auto 1fr;gap:.2rem .6rem;font-size:.82rem}
/* `.ok` colide com um `.ok` GLOBAL do portal (caixa verde de status, portal.py:214).
   Sem zerar a casca aqui, cada ✓ da conferência vira um quadradinho com borda e a
   lista fica com o dobro da altura — foi assim que ela nasceu em 18/09. */
.rn-conf .ok,.rn-conf .no{background:none;border:0;border-radius:0;padding:0;font-weight:700;line-height:1.5}
.rn-conf .ok{color:var(--verde-claro)}
.rn-conf .no{color:#E98A80}
.rn-conf .falt{font-size:.78rem;color:var(--txt-mut);margin-top:.5rem}
.rn-pdf{font-size:.68rem;font-weight:700;letter-spacing:.05em;padding:.08rem .4rem;border-radius:5px;
  background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6;text-decoration:none}
/* o chip que abre a janela — mesma casca do <summary> dos outros blocos */
.rn-chip{background:var(--card);border:1px solid var(--borda);color:var(--txt-mut);border-radius:999px;
  width:auto;margin:0 0 1rem;padding:.3rem .65rem;font-size:.8rem;cursor:pointer}
.rn-chip:hover{color:var(--txt);border-color:var(--neon-borda)}

/* ── A JANELA DO CADASTRO ──────────────────────────────────────────────────
   Antes o cadastro era um bloco dobrável DENTRO da aba "Renovações", e o botão
   do topo era uma âncora pra ele: nas abas Carteira e Percentuais o destino não
   existia e o botão não fazia NADA (o dono viu em 18/09). Agora a janela mora
   fora das abas, abre em cima da tela e não troca de página — subir o PDF,
   conferir e cadastrar acontecem todos aqui, por fetch. */
.rn-jan{position:fixed;inset:0;z-index:90;display:flex;align-items:center;justify-content:center;padding:1rem}
.rn-jan[hidden]{display:none}
.rn-jan [hidden]{display:none !important}
.rn-jan .fundo{position:absolute;inset:0;background:rgba(0,0,0,.6)}
.rn-jan .cx{position:relative;background:var(--card);border:1px solid var(--borda);border-radius:14px;
  width:100%;max-width:720px;max-height:min(86vh,760px);display:flex;flex-direction:column;
  box-shadow:0 18px 50px rgba(0,0,0,.5);overflow:hidden}
.rn-jan .cab{display:flex;align-items:center;gap:.6rem;flex:none;
  padding:.75rem 1rem;border-bottom:1px solid var(--borda);background:var(--card-2)}
.rn-jan .cab h3{margin:0;font-size:1rem}
.rn-jan .cab .passo{font-size:.72rem;color:var(--txt-mut);flex:1;min-width:0}
.rn-jan .cab .x{background:none;border:0;color:var(--txt-mut);font-size:1rem;cursor:pointer;
  width:auto;margin:0;padding:.2rem .35rem;line-height:1}
.rn-jan .cab .x:hover{color:var(--txt)}
.rn-jan .corpo{padding:.9rem 1rem 1.1rem;overflow-y:auto;flex:1}
.rn-jan .corpo .sub{font-size:.79rem;color:var(--txt-mut);line-height:1.55;margin-bottom:.7rem}
/* a segunda porta: o que já chegou no WhatsApp vinculado */
/* A faixa do que está esperando conferência. `width:auto` e `margin` explícitos
   vencem o `button{width:100%;margin-top:1.4rem}` global — a mesma armadilha que
   já esticou o ✕ de fechar. */
.rn-espera{display:flex;align-items:center;gap:.6rem;width:100%;margin:0 0 .9rem;
  text-align:left;padding:.7rem .85rem;border-radius:10px;cursor:pointer;
  background:var(--neon-fundo);border:1px solid var(--verde);color:var(--txt);
  font:inherit;font-size:.86rem}
.rn-espera:hover{border-color:var(--verde-claro,#46f58a)}
.rn-espera .ic{flex:none;font-size:1.05rem;line-height:1}
.rn-espera .tx{flex:1;min-width:0}
.rn-espera .vai{flex:none;font-size:.78rem;font-weight:600;color:var(--verde-claro,#46f58a);
  white-space:nowrap}
@media (max-width:420px){.rn-espera{flex-wrap:wrap}.rn-espera .vai{margin-left:auto}}

.rn-wpp{margin-top:1rem}
.rn-wpp .cab{display:flex;align-items:baseline;gap:.4rem;flex-wrap:wrap;margin-bottom:.45rem}
.rn-wpp .cab .t{font-size:.82rem;font-weight:600}
.rn-wpp .cab .s{font-size:.74rem;color:var(--txt-mut)}
.rn-wpp .lista{display:flex;flex-direction:column;gap:.3rem;max-height:216px;overflow-y:auto}
.rn-wpp .item{display:flex;gap:.5rem;align-items:center;text-align:left;width:auto;margin:0;
  background:var(--bg-2);border:1px solid var(--borda);border-radius:9px;padding:.45rem .6rem;
  cursor:pointer;color:var(--txt);font:inherit}
.rn-wpp .item:hover:not([disabled]){border-color:var(--neon-borda);background:var(--neon-fundo)}
.rn-wpp .item[disabled]{opacity:.45;cursor:default}
.rn-wpp .item .nome{flex:1;min-width:0;font-size:.82rem;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.rn-wpp .item .quem{font-size:.72rem;color:var(--txt-mut);white-space:nowrap}
.rn-wpp .vazio{font-size:.79rem;color:var(--txt-mut);line-height:1.55}
.rn-wpp .pe{margin-top:.5rem;font-size:.74rem;padding:.24rem .6rem}
/* quem pode mandar: liberar é reconhecer um nome que já está ali */
.rn-fonte{display:flex;gap:.5rem;align-items:center;background:var(--bg-2);
  border:1px solid var(--borda);border-radius:9px;padding:.45rem .6rem;margin-bottom:.3rem}
.rn-fonte .nome{flex:1;min-width:0;font-size:.83rem;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.rn-fonte .meta{font-size:.72rem;color:var(--txt-mut);white-space:nowrap}
.rn-fonte .bts{display:flex;gap:.25rem;flex:none}
.rn-fonte .mini{width:auto;margin:0;padding:.2rem .5rem;font-size:.72rem;border-radius:7px;
  cursor:pointer;background:transparent;border:1px solid var(--borda);color:var(--txt-mut)}
.rn-fonte .mini:hover{color:var(--txt);border-color:var(--neon-borda)}
.rn-secao{font-size:.67rem;text-transform:uppercase;letter-spacing:.08em;color:var(--txt-mut);
  font-weight:700;margin:1rem 0 .4rem}
.rn-secao:first-child{margin-top:0}
.rn-lendo{display:flex;align-items:center;gap:.6rem;font-size:.87rem;color:var(--txt-mut);padding:1.6rem .2rem}
.rn-lendo .bola{width:14px;height:14px;border-radius:50%;flex:none;
  border:2px solid var(--neon-borda);border-top-color:var(--verde);animation:rn-gira .7s linear infinite}
@keyframes rn-gira{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.rn-lendo .bola{animation:none}}
.rn-ok{text-align:center;padding:1.3rem .4rem}
.rn-ok .marca{font-size:1.6rem;color:var(--verde-claro);line-height:1}
.rn-ok .t{font-weight:700;font-size:1rem;margin:.35rem 0 .15rem}
.rn-ok .s{font-size:.84rem;color:var(--txt-mut);line-height:1.6}
.rn-ok .bts{display:flex;gap:.45rem;justify-content:center;margin-top:1rem;flex-wrap:wrap}
.rn-erro{background:rgba(224,108,108,.12);border:1px solid #5A2B2B;color:#E9A0A0;
  border-radius:10px;padding:.55rem .75rem;font-size:.82rem;margin-bottom:.8rem}
/* a linha recém-cadastrada, pra pessoa achar onde ela caiu na tabela */
.rn-tab tr.novata td{background:var(--neon-fundo)}
@media (max-width:640px){
  .rn-jan{padding:0;align-items:flex-end}
  .rn-jan .cx{max-width:none;max-height:92vh;border-radius:14px 14px 0 0}
}
</style>

<div class="rn-pag">
<div class="rn-topo">
  <div><h2>Renovações</h2>
    <div class="sub">{% if aba == 'carteira' %}tudo que já foi cadastrado{% elif aba == 'percentuais' %}a comissão de cada seguradora{% else %}o que vence nos próximos {{ horizonte }} dias{% endif %}</div></div>
  {% if gerencia %}
  <button type="button" class="rn-bt" onclick="rnAbrir(event)">+ Nova apólice</button>
  {% endif %}
</div>

<nav class="rn-abas">
  {% for chave, rot in abas %}
    {% if chave != 'percentuais' or gerencia %}
    <a href="/painel/renovacoes?aba={{ chave }}" class="{% if aba == chave %}on{% endif %}">{{ rot }}{% if chave == 'carteira' %}<span id="rn-conta-carteira">{% if n_carteira %} ({{ n_carteira }}){% endif %}</span>{% endif %}</a>
    {% endif %}
  {% endfor %}
</nav>

{% if erro %}<div class="rn-aviso ambar"><b>Não salvou:</b> {{ erro }}</div>{% endif %}

{# O QUE O LEITOR JÁ LEU E NINGUÉM CONFERIU. Fica no topo das TRÊS abas, porque
   é a única coisa desta tela que tem alguém esperando do outro lado — o
   documento já chegou, já foi lido, e só falta um clique. Some sozinha quando
   não há nada, como as faixas de contagem. #}
{% if esperando %}
<button type="button" class="rn-espera" onclick="rnAbrir(event)">
  <span class="ic">📄</span>
  <span class="tx"><b>{{ esperando }}</b> apólice{{ 's' if esperando > 1 }}
    lida{{ 's' if esperando > 1 }} esperando você conferir</span>
  <span class="vai">conferir →</span>
</button>
{% endif %}

{# ─────────────────────────── ABA: a fila ─────────────────────────── #}
{% if aba == 'fila' %}

  {# os contadores só existem quando há o que contar. Três caixas dizendo 0 na
     faixa mais nobre da página é o que a tela fazia antes. #}
  {% if fila %}
  <div class="rn-faixas">
    {% if em_alerta %}<div class="rn-cx alerta"><span class="r">Na régua</span>
      <span class="v">{{ em_alerta|length }}</span><span class="n">60 dias ou menos</span></div>{% endif %}
    {% set na_fila = fila|length - em_alerta|length - vencidas|length %}
    {% if na_fila > 0 %}<div class="rn-cx"><span class="r">Na fila</span>
      <span class="v">{{ na_fila }}</span><span class="n">entre 60 e {{ horizonte }} dias</span></div>{% endif %}
    {% if vencidas %}<div class="rn-cx venc"><span class="r">Já venceu</span>
      <span class="v">{{ vencidas|length }}</span><span class="n">e ninguém marcou</span></div>{% endif %}
  </div>

  <div class="rn-lista">
    {% for a in fila %}
    {% set passou = (a.dias is not none and a.dias < 0) %}
    <div class="rn-card {% if passou %}passou{% elif a.degrau %}d{{ a.degrau }}{% endif %}" id="a{{ a.id }}">
      <div class="cab">
        {% if a.cliente_id %}<button type="button" class="quem rn-abre" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this.closest('.rn-card'),'cliente')">{{ a.cliente }}</button>
        {% else %}<span class="quem">{{ a.cliente }}</span>{% endif %}
        <span class="rn-pill">{{ a.seguradora }}</span>
        <span class="rn-pill">{{ a.ramo_txt|lower }}</span>
        {% if a.tem_pdf %}<a class="rn-pdf" href="/painel/renovacoes/apolice/{{ a.id }}/pdf" target="_blank">PDF</a>{% endif %}
        {% if passou %}<span class="rn-tag d15">venceu</span>
        {% elif a.degrau %}<span class="rn-tag d{{ a.degrau }}">faltam {{ a.dias }} dias</span>{% endif %}
      </div>
      <div class="meta">
        {%- if a.bem.modelo %}{{ a.bem.modelo }}{% if a.bem.placa %} · {{ a.bem.placa }}{% endif %} · {% endif -%}
        vence {{ a.vigencia_fim.strftime('%d/%m/%Y') }}
        {%- if a.dias == 0 %} · <b>é hoje</b>{% elif a.dias == 1 %} · <b>é amanhã</b>
        {%- elif a.dias < 0 %} · <b>há {{ -a.dias }} dias</b>{% endif %}
        {%- if a.classe_bonus %} · classe de bônus {{ a.classe_bonus }}{% endif %}
      </div>
      <div class="dinheiro">
        Prêmio {{ brl(a.premio_centavos) }}
        {%- if a.comissao_estimada is not none %} · comissão {{ brl(a.comissao_estimada) }}
          {%- if not a.comissao_fechada %} (estimada{% if a.comissao_pct %}, {{ a.comissao_pct }}%{% endif %}){% endif %}
        {%- else %} · <a href="/painel/renovacoes?aba=percentuais" style="color:var(--amar)">cadastre o percentual da {{ a.seguradora }}</a>{% endif %}
      </div>
      <div class="rn-acoes">
        <form method="post" action="/painel/renovacoes/apolice/{{ a.id }}/situacao">
          <select name="situacao" onchange="this.form.submit()"
                  style="background:var(--bg);border:1px solid var(--borda);border-radius:8px;padding:.28rem .45rem;color:var(--txt);font-size:.78rem">
            {% for chave, rot in situacoes %}{% if chave != 'perdida' or chave == a.situacao %}
            <option value="{{ chave }}" {% if chave == a.situacao %}selected{% endif %}>{{ rot }}</option>
            {% endif %}{% endfor %}
          </select>
          <noscript><button class="rn-bt fraco">ok</button></noscript>
        </form>
        {% if a.cliente_id and a.situacao in ('proposta','vigente','vencida') %}
        <button type="button" class="rn-bt fraco" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this.closest('.rn-card'),'apolice')">perdi…</button>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  </div>

  {% else %}
  {# O VAZIO INFORMA. "Nenhuma apólice vencendo" é verdade e é inútil: a corretora
     TEM carteira, e a tela dizendo que não há nada convida a cadastrar duplicado. #}
  <div class="rn-vazio">
    <div class="t">Nada vence nos próximos {{ horizonte }} dias.</div>
    {% if proxima %}
    <div class="s">A próxima é <b>{{ proxima.cliente }}</b> — {{ proxima.seguradora }}
      {{ proxima.ramo_txt|lower }}{% if proxima.bem.modelo %}, {{ proxima.bem.modelo }}{% endif %},
      que vence em <b>{{ proxima.vigencia_fim.strftime('%d/%m/%Y') }}</b>
      (faltam {{ proxima.dias }} dias).<br>
      Entra nesta lista em <b>{{ proxima.entra_em.strftime('%d/%m/%Y') }}</b>, e o primeiro
      aviso no celular sai no mesmo dia.</div>
    {% elif n_carteira %}
    <div class="s">A carteira tem {{ n_carteira }} apólice{{ 's' if n_carteira > 1 }}, e nenhuma
      delas está perto de vencer. Veja tudo na aba <a href="/painel/renovacoes?aba=carteira">Carteira</a>.</div>
    {% else %}
    <div class="s">A carteira está vazia. Cadastre a primeira apólice abaixo — o aviso
      começa a valer no mesmo dia.</div>
    {% endif %}
  </div>
  {% endif %}

  <details class="rn-det">
    <summary>por que 60, 30 e 15?</summary>
    <div class="rn-aviso azul" style="margin:0">
      O aviso no celular sai três vezes por apólice — faltando 60, 30 e 15 dias — e vai
      pro corretor responsável. A lista vai até {{ horizonte }} dias porque "o que vem por
      aí" é consulta, não interrupção: entre 90 e 60 dias a apólice aparece e não avisa ninguém.
      <br><br>
      Ninguém é obrigado a avisar o cliente do vencimento — nem a corretora, nem a
      seguradora. Quem tem prazo é a seguradora que <b>não</b> quer renovar, e são 30 dias
      (Lei 15.040/2024). É por isso que avisar antes retém: quem liga faltando dois dias
      não está fazendo consultoria, está emitindo boleto.
    </div>
  </details>

  {% if gerencia %}
  <button type="button" class="rn-chip" onclick="rnAbrir(event)">+ cadastrar uma apólice</button>
  {% endif %}

{# ────────────────────────── ABA: a carteira ────────────────────────── #}
{% elif aba == 'carteira' %}
  <form class="rn-busca" method="get" action="/painel/renovacoes">
    <input type="hidden" name="aba" value="carteira">
    <input name="busca" value="{{ busca }}" placeholder="nome, seguradora, placa, modelo ou número…">
    <button class="rn-bt">Buscar</button>
    {% if busca %}<a class="rn-bt fraco" style="text-decoration:none;padding:.35rem .8rem"
       href="/painel/renovacoes?aba=carteira">limpar</a>{% endif %}
  </form>
  {% if carteira %}
  <div class="rn-rol"><table class="rn-tab">
    <thead><tr><th>Cliente</th><th>Seguradora</th><th>Ramo</th><th>Vence</th><th>Situação</th>
        <th>Prêmio</th><th>Comissão</th></tr></thead>
    {# `tbody` com id porque a linha nova entra AQUI depois do cadastro, sem
       recarregar a página — e é o mesmo template que o servidor devolve. #}
    <tbody id="rn-corpo">
    {% for a in carteira %}{% include "renovacoes_linha" %}{% endfor %}
    </tbody>
  </table></div>
  {% else %}
  <div class="rn-vazio"><div class="t">
    {% if busca %}Nada encontrado para “{{ busca }}”.{% else %}A carteira está vazia.{% endif %}</div>
    <div class="s">{% if busca %}A busca olha nome do cliente, seguradora, placa, modelo e número da
      apólice.{% else %}<button type="button" class="rn-abre" style="color:var(--verde-claro)"
      onclick="rnAbrir(event)">Cadastre a primeira</button> — leva um PDF ou o que você souber.{% endif %}</div>
  </div>
  {% endif %}

{# ───────────────────────── ABA: os percentuais ───────────────────────── #}
{% else %}
  <div class="rn-bloco">
    <h3>Comissão por seguradora</h3>
    <div class="sub">A comissão não vem escrita na apólice — é papel do cliente, e o cliente
      não vê quanto o corretor ganha. Cadastre o percentual uma vez e toda apólice daquela
      seguradora passa a mostrar a comissão estimada. Deixe o ramo em “todos” pra valer no
      geral, e cadastre por ramo só onde o percentual foge.
      <br>O percentual incide sobre o <b>prêmio líquido</b>, sem o IOF — o IOF é imposto
      repassado ao governo e não entra em comissão.</div>
    {% if comissoes %}
    <div class="rn-rol" style="margin-bottom:.8rem"><table class="rn-tab" style="min-width:420px">
      <tr><th>Seguradora</th><th>Ramo</th><th>%</th><th></th></tr>
      {% for cm in comissoes %}
      <tr><td>{{ cm.seguradora }}</td><td>{{ cm.ramo_txt }}</td><td>{{ cm.pct }}%</td>
        <td><form method="post" action="/painel/renovacoes/comissao/{{ cm.id }}/apagar">
          <button class="rn-bt fraco" style="padding:.18rem .5rem">apagar</button></form></td></tr>
      {% endfor %}
    </table></div>
    {% endif %}
    <form method="post" action="/painel/renovacoes/comissao">
      <div class="rn-grade">
        <div class="rn-campo"><label>Seguradora</label><input name="seguradora" required placeholder="Allianz"></div>
        <div class="rn-campo"><label>Ramo</label>
          <select name="ramo"><option value="">todos os ramos</option>
            {% for chave, rot in ramos %}<option value="{{ chave }}">{{ rot }}</option>{% endfor %}</select></div>
        <div class="rn-campo"><label>Percentual</label><input name="pct" required placeholder="20"></div>
      </div>
      <button class="rn-bt" style="margin-top:.6rem">Salvar percentual</button>
    </form>
  </div>
{% endif %}
</div>{# .rn-pag #}

{% if gerencia %}
{# A JANELA DO CADASTRO fica FORA das abas de propósito: o botão "+ Nova apólice"
   existe nas três, e enquanto o cadastro morava dentro da aba "Renovações" o
   botão apontava pra um destino que as outras duas não tinham — clicar não fazia nada.
   Aqui dentro nada navega: o PDF sobe por fetch, a leitura volta em JSON e o
   cadastro devolve a linha pronta pra tabela. Sem trocar de página, sem refresh. #}
<div class="rn-jan" id="rn-jan" {% if not conferir %}hidden{% endif %}>
  <div class="fundo" onclick="rnFechar()"></div>
  <div class="cx" role="dialog" aria-modal="true" aria-labelledby="rn-jan-tit">
    <div class="cab">
      <h3 id="rn-jan-tit">Nova apólice</h3>
      <span class="passo" id="rn-passo"></span>
      <button type="button" class="x" onclick="rnFechar()" aria-label="Fechar">✕</button>
    </div>
    <div class="corpo">
      <div class="rn-erro" id="rn-erro" hidden></div>

      {# 1 · o documento #}
      <div id="rn-p-pdf" {% if conferir %}hidden{% endif %}>
        {% if cofre_ok %}
        <form class="rn-drop" id="rn-drop" method="post" action="/painel/renovacoes/importar"
              enctype="multipart/form-data" onsubmit="return rnLerPdf(this)">
          <div class="t">Solte a apólice em PDF aqui</div>
          <div class="s">Eu leio os campos e você confere antes de salvar. Allianz é reconhecida;
            as outras seguradoras entram à medida que os PDFs chegarem.</div>
          <input type="file" id="rn-arq" name="arquivo" accept="application/pdf" required
                 onchange="rnLerPdf(this.form)">
          <label class="rn-bt" for="rn-arq">escolher o arquivo</label>
          <button class="rn-bt fraco semjs">Ler o PDF</button>
        </form>
        {# O que JÁ chegou no número vinculado. A lista é montada por JavaScript e o
           bloco fica escondido enquanto não houver nada — bloco vazio dizendo
           "nenhum documento" é ruído no caminho principal. #}
        <div class="rn-wpp" id="rn-wpp" hidden>
          <div class="cab"><span class="t">…ou um que já chegou no WhatsApp ou no Telegram</span>
            <span class="s" id="rn-wpp-sub"></span></div>
          <div class="lista" id="rn-wpp-lista"></div>
          <div class="vazio" id="rn-wpp-vazio" hidden></div>
          <button type="button" class="rn-bt fraco pe" onclick="rnFontes()">quem pode mandar</button>
        </div>
        {% endif %}
        <button type="button" class="rn-bt fraco" onclick="rnMao()">não tenho o PDF — digitar à mão</button>
      </div>

      {# 2 · lendo (o servidor trabalha; a janela continua aqui) #}
      <div class="rn-lendo" id="rn-p-lendo" hidden><span class="bola"></span>
        <span>Lendo a apólice… são alguns segundos. Pode deixar a janela aberta.</span></div>

      {# 3 · conferir e cadastrar #}
      <div id="rn-p-form" {% if not conferir %}hidden{% endif %}>
        <div id="rn-conf">{% if conferir %}{% include "renovacoes_conf" %}{% endif %}</div>
        <div class="sub">Auto por enquanto — é o que a carteira tem hoje. Os outros ramos já
          gravam; o que falta pra eles é o formulário, não o cadastro.</div>
        <form method="post" action="/painel/renovacoes/apolice" id="rn-form" onsubmit="return rnSalvar(this)">
          <input type="hidden" name="pdf_caminho" value="{{ conferir.pdf_caminho if conferir else '' }}">
          <input type="hidden" name="pdf_nome" value="{{ conferir.pdf_nome if conferir else '' }}">
          <input type="hidden" name="pdf_bytes" value="{{ conferir.pdf_bytes if conferir else '' }}">
          <input type="hidden" name="pdf_lido" value='{% if conferir %}{{ conferir.pdf_lido|tojson|forceescape }}{% endif %}'>
        <div class="rn-grupo"><div class="t">Quem</div><div class="rn-grade">
          <div class="rn-campo"><label>Cliente já na carteira</label>
            <select name="cliente_id"><option value="">— achar pelo CPF abaixo, ou nenhum —</option>
              {% for cid, nm in clientes %}<option value="{{ cid }}">{{ nm }}</option>{% endfor %}
            </select></div>
          <div class="rn-campo"><label>Corretor (recebe o aviso)</label>
            <select name="corretor_id"><option value="">— dono e gestores —</option>
              {% for mid, nm in corretores %}<option value="{{ mid }}">{{ nm }}</option>{% endfor %}
            </select></div>
          <div class="rn-campo"><label>Segurado</label><input name="nome" value="{{ form.nome }}" placeholder="nome completo"></div>
          <div class="rn-campo"><label>CPF</label><input name="cpf" value="{{ form.cpf }}" placeholder="acha ou cria o cliente"></div>
          <div class="rn-campo"><label>Telefone</label><input name="telefone" value="{{ form.telefone }}"></div>
          <div class="rn-campo"><label>E-mail</label><input name="email" value="{{ form.email }}"></div>
          <div class="rn-campo" style="grid-column:1/-1"><label>Endereço</label><input name="endereco" value="{{ form.endereco }}"></div>
        </div></div>

        <div class="rn-grupo"><div class="t">O que, e até quando</div><div class="rn-grade">
          <div class="rn-campo chave"><label>Seguradora *</label><input name="seguradora" required value="{{ form.seguradora }}" placeholder="Allianz"></div>
          <div class="rn-campo"><label>Ramo</label>
            <select name="ramo">{% for chave, rot in ramos %}<option value="{{ chave }}" {% if chave == form.ramo %}selected{% endif %}>{{ rot }}</option>{% endfor %}</select></div>
          <div class="rn-campo"><label>Situação</label>
            <select name="situacao">{% for chave, rot in situacoes %}<option value="{{ chave }}" {% if chave == form.situacao %}selected{% endif %}>{{ rot }}</option>{% endfor %}</select></div>
          <div class="rn-campo"><label>Início da vigência</label><input type="date" name="vigencia_inicio" value="{{ form.vigencia_inicio }}"></div>
          <div class="rn-campo chave"><label>Fim da vigência *</label><input type="date" name="vigencia_fim" required value="{{ form.vigencia_fim }}"></div>
          <div class="rn-campo"><label>Nº da proposta</label><input name="numero_proposta" value="{{ form.numero_proposta }}"></div>
          <div class="rn-campo"><label>Nº da apólice</label><input name="numero_apolice" value="{{ form.numero_apolice }}"></div>
        </div></div>

        <div class="rn-grupo"><div class="t">Quanto</div><div class="rn-grade">
          <div class="rn-campo"><label>Prêmio líquido</label><input name="premio" value="{{ form.premio }}" placeholder="3.807,57"></div>
          <div class="rn-campo"><label>IOF + juros</label><input name="iof" value="{{ form.iof }}" placeholder="281,00"></div>
          <div class="rn-campo"><label>Franquia</label><input name="franquia" value="{{ form.franquia }}" placeholder="4.088,88"></div>
          <div class="rn-campo"><label>Comissão %</label><input name="comissao_pct" placeholder="vem da seguradora"></div>
          <div class="rn-campo"><label>Comissão R$ (se souber)</label><input name="comissao"></div>
          <div class="rn-campo"><label>Parcelas</label><input name="parcelas" value="{{ form.parcelas }}" placeholder="4"></div>
          <div class="rn-campo"><label>Dia do vencimento</label><input name="dia_vencimento" value="{{ form.dia_vencimento }}" placeholder="5"></div>
        </div></div>

        <div class="rn-grupo"><div class="t">O bem segurado</div><div class="rn-grade">
          <div class="rn-campo"><label>Placa</label><input name="placa" value="{{ form.placa }}"></div>
          <div class="rn-campo"><label>Marca / modelo</label><input name="modelo" value="{{ form.modelo }}" placeholder="GEELY EX2 MAX"></div>
          <div class="rn-campo"><label>Ano</label><input name="ano" value="{{ form.ano }}" placeholder="2026"></div>
          <div class="rn-campo"><label>Chassi</label><input name="chassi" value="{{ form.chassi }}"></div>
          <div class="rn-campo"><label>Classe de bônus</label><input name="classe_bonus" value="{{ form.classe_bonus }}" placeholder="00"></div>
        </div></div>
          <div class="rn-acoes" style="margin-top:.8rem">
            <button class="rn-bt rn-salvar">{% if conferir %}Está certo — cadastrar{% else %}Cadastrar apólice{% endif %}</button>
            <button type="button" class="rn-bt fraco" onclick="rnFechar()">cancelar</button>
          </div>
        </form>
      </div>

      {# 3b · quem pode mandar #}
      <div id="rn-p-fontes" hidden>
        <div class="sub">Só entra na lista o PDF que vier destes números. O nome é o que o
          WhatsApp mostra e pode mudar; o que vale é o número. Tirar alguém daqui não
          apaga apólice nenhuma.</div>
        <div id="rn-fontes-corpo"></div>
        <div class="rn-acoes" style="margin-top:.9rem">
          <button type="button" class="rn-bt fraco" onclick="rnVoltar()">voltar</button>
        </div>
      </div>

      {# 4 · cadastrada #}
      <div class="rn-ok" id="rn-p-ok" hidden>
        <div class="marca">✓</div>
        <div class="t">Cadastrada</div>
        <div class="s" id="rn-ok-txt"></div>
        <div class="bts">
          <button type="button" class="rn-bt" onclick="rnOutra()">cadastrar outra</button>
          <button type="button" class="rn-bt fraco" onclick="rnFechar()">fechar</button>
        </div>
      </div>
    </div>
  </div>
</div>
{% endif %}
<script>
/* A JANELA DO CADASTRO — três regras que o dono deu em 18/09: não trocar de
   página, não recarregar, e a tela responder na hora enquanto o servidor
   trabalha. Por isso nada aqui navega: o PDF sobe por fetch e a leitura volta em
   JSON; o cadastro devolve a LINHA já renderizada pelo mesmo template da tabela,
   e ela entra na carteira no lugar certo (a tabela é ordenada por vencimento).

   Sem JavaScript os dois formulários continuam funcionando pelo caminho antigo —
   `method="post"` e o servidor responde a página inteira. É por isso que o
   painel de conferência é template, e não HTML montado aqui: uma marcação só. */
(function(){
  var PASSOS = ['pdf', 'lendo', 'form', 'ok', 'fontes'];
  function jan(){ return document.getElementById('rn-jan'); }
  function el(id){ return document.getElementById(id); }

  function passo(qual, rotulo){
    PASSOS.forEach(function(nome){
      var d = el('rn-p-' + nome);
      if(d) d.hidden = (nome !== qual);
    });
    var r = el('rn-passo');
    if(r) r.textContent = rotulo || '';
  }

  function erro(txt){
    var e = el('rn-erro');
    if(!e) return;
    e.textContent = txt || '';
    e.hidden = !txt;
  }

  window.rnAbrir = function(ev){
    if(ev){ ev.preventDefault(); ev.stopPropagation(); }
    var j = jan();
    if(!j) return false;
    j.hidden = false;
    erro('');
    // reabrir no meio de uma conferência não joga fora o que já foi lido
    var f = el('rn-p-form');
    if(!f || f.hidden) passo('pdf', '1 de 2 · o documento');
    var arq = j.querySelector('input[type=file]');
    if(arq){ try { arq.focus(); } catch(_e){} }
    wppCarregar();
    return false;
  };

  window.rnFechar = function(){
    var j = jan();
    if(j) j.hidden = true;
  };

  window.rnMao = function(){
    erro('');
    passo('form', 'à mão');
  };

  document.addEventListener('keydown', function(e){
    if(e.key !== 'Escape') return;
    var j = jan();
    if(j && !j.hidden) window.rnFechar();
  });

  function preencher(d){
    var form = el('rn-form');
    if(!form) return;
    var c = el('rn-conf');
    if(c) c.innerHTML = d.conf_html || '';
    var campos = d.form || {};
    Object.keys(campos).forEach(function(k){
      var campo = form.querySelector('[name="' + k + '"]');
      // só escreve o que o leitor achou: campo vazio não apaga o padrão do select
      if(campo && campos[k]) campo.value = campos[k];
    });
    var pdf = d.pdf || {};
    ['caminho', 'nome', 'bytes'].forEach(function(k){
      var campo = form.querySelector('[name="pdf_' + k + '"]');
      if(campo) campo.value = pdf[k] || '';
    });
    var lido = form.querySelector('[name="pdf_lido"]');
    if(lido) lido.value = pdf.lido ? JSON.stringify(pdf.lido) : '';
    var bt = form.querySelector('.rn-salvar');
    if(bt) bt.textContent = 'Está certo — cadastrar';
  }

  window.rnLerPdf = function(form){
    var arq = form.querySelector('input[type=file]');
    var f = arq && arq.files && arq.files[0];
    if(!f) return false;
    window.rnSubir(f);
    return false;
  };

  window.rnSubir = function(f){
    erro('');
    passo('lendo', 'lendo o documento');
    var fd = new FormData();
    fd.append('arquivo', f);
    fd.append('json', '1');
    zapFetch('/painel/renovacoes/importar', { method: 'POST', body: fd, credentials: 'same-origin' }).then(function(d){if(!d){passo('pdf', '1 de 2 · o documento');
        erro('a leitura não respondeu. Tente de novo.');return;}
        if(!d || !d.ok){
          passo('pdf', '1 de 2 · o documento');
          erro((d && d.erro) || 'não consegui ler este PDF.');
          return;
        }
        preencher(d);
        passo('form', '2 de 2 · confira e cadastre');
      });
  };

  function entrar(d){
    var corpo = el('rn-corpo');
    if(corpo && d.linha_html){
      var caixa = document.createElement('tbody');
      caixa.innerHTML = d.linha_html;
      var nova = caixa.querySelector('tr');
      if(nova){
        nova.className = nova.className + ' novata';
        // a tabela desce por vencimento: a linha entra no lugar dela, não no topo
        var venc = nova.getAttribute('data-vence') || '';
        var alvo = null;
        Array.prototype.forEach.call(corpo.querySelectorAll('tr[data-vence]'), function(tr){
          if(alvo === null && (tr.getAttribute('data-vence') || '') < venc) alvo = tr;
        });
        if(alvo) corpo.insertBefore(nova, alvo); else corpo.appendChild(nova);
      }
    }
    var conta = el('rn-conta-carteira');
    if(conta && d.n_carteira) conta.textContent = ' (' + d.n_carteira + ')';
    var t = el('rn-ok-txt');
    if(t) t.textContent = d.resumo || '';
    wppLida = false;          // o documento usado sai da lista na próxima abertura
    passo('ok', '');
  }

  window.rnSalvar = function(form){
    erro('');
    var bt = form.querySelector('.rn-salvar');
    var rotulo = bt ? bt.textContent : '';
    if(bt){ bt.disabled = true; bt.textContent = 'cadastrando…'; }
    function volta(){ if(bt){ bt.disabled = false; bt.textContent = rotulo; } }
    var fd = new FormData(form);
    fd.append('json', '1');
    zapFetch('/painel/renovacoes/apolice', { method: 'POST', body: fd, credentials: 'same-origin' }).then(function(d){if(!d){volta();
        // o pedido pode ter chegado: mandar tentar de novo criaria a apólice duas vezes
        erro('não consegui confirmar o cadastro. Procure pelo número na Carteira antes de repetir.');return;}
        volta();
        if(!d || !d.ok){ erro((d && d.erro) || 'não consegui cadastrar.'); return; }
        entrar(d);
      });
    return false;
  };

  // com JavaScript o arquivo sobe assim que é escolhido; o botão de enviar só
  // existe pra quem está sem JavaScript, e some aqui
  var semjs = document.querySelector('.rn-drop .semjs');
  if(semjs) semjs.hidden = true;

  var alvo = document.getElementById('rn-drop');
  if(alvo){
    ['dragenter', 'dragover'].forEach(function(nome){
      alvo.addEventListener(nome, function(e){
        e.preventDefault(); e.stopPropagation();
        alvo.className = 'rn-drop sobre';
      });
    });
    ['dragleave', 'drop'].forEach(function(nome){
      alvo.addEventListener(nome, function(e){
        e.preventDefault(); e.stopPropagation();
        alvo.className = 'rn-drop';
      });
    });
    alvo.addEventListener('drop', function(e){
      var arqs = e.dataTransfer && e.dataTransfer.files;
      if(!arqs || !arqs.length) return;
      var campo = document.getElementById('rn-arq');
      if(campo){
        try { campo.files = arqs; } catch(_e){}
      }
      // o input pode recusar a atribuição em navegador antigo: o arquivo do
      // evento é o que sobe de qualquer jeito
      window.rnSubir(arqs[0]);
    });
  }

  // ── os PDFs que já chegaram no WhatsApp ──────────────────────────────────
  var wppLida = false;

  function wppCarregar(){
    var caixa = el('rn-wpp');
    if(!caixa || wppLida) return;
    wppLida = true;
    zapFetch('/painel/renovacoes/whatsapp', { credentials: 'same-origin' }).then(function(d){if(!d){/* sem a lista a janela continua inteira */return;}
        // ninguém nunca mandou PDF pra este número: o bloco continua escondido,
        // porque o caminho principal é soltar o arquivo
        if(!d || !d.ok || !d.remetentes) return;
        var lista = el('rn-wpp-lista');
        var vazio = el('rn-wpp-vazio');
        if(!lista || !vazio) return;
        caixa.hidden = false;
        lista.innerHTML = '';
        // alguém já mandou, mas ninguém foi liberado ainda
        if(!d.itens.length){
          vazio.hidden = false;
          vazio.textContent = d.liberados
            ? 'Nada novo de quem você liberou nos últimos 90 dias.'
            : ('Ninguém liberado ainda. ' + d.remetentes
               + (d.remetentes === 1 ? ' número mandou PDF' : ' números mandaram PDF')
               + ' pra este WhatsApp nos últimos 90 dias.');
          var sub0 = el('rn-wpp-sub');
          if(sub0) sub0.textContent = '';
          return;
        }
        vazio.hidden = true;
        d.itens.forEach(function(it){
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'item';
          // o LEITOR AUTOMÁTICO já passou: a linha diz seguradora, segurado e
          // vencimento, e o nome do arquivo vira o título do hover. Sem leitura,
          // o nome do arquivo continua sendo a melhor informação que existe.
          var nome = document.createElement('span');
          nome.className = 'nome';
          if(it.resumo && !it.erro){
            nome.textContent = it.resumo;
            b.title = it.nome;
          } else {
            nome.textContent = (it.parece && !it.ja ? '📄 ' : '') + it.nome;
          }
          var quem = document.createElement('span');
          quem.className = 'quem';
          if(it.ja) quem.textContent = 'já cadastrada';
          else if(it.erro) quem.textContent = it.erro;
          else quem.textContent = it.de + ' · ' + it.quando;
          b.appendChild(nome);
          b.appendChild(quem);
          // documento já cadastrado, ou que o WhatsApp apagou antes de eu ler:
          // clicar não levaria a lugar nenhum
          if(it.ja || it.erro){ b.disabled = true; }
          else { b.onclick = function(){ window.rnDoWhats(it.fonte, it.id); }; }
          lista.appendChild(b);
        });
        var sub = el('rn-wpp-sub');
        if(sub) sub.textContent = d.itens.length + (d.itens.length === 1 ? ' documento' : ' documentos') + ' de quem você liberou';
      });
  }

  window.rnDoWhats = function(fonte, id){
    erro('');
    passo('lendo', 'abrindo o documento');
    // duas portas, dois endereços: a do WhatsApp ainda pode precisar baixar o
    // arquivo; a do pré-cadastro (Telegram, ou WhatsApp já lido) só devolve
    var url = (fonte === 'lida' ? '/painel/renovacoes/lida/' : '/painel/renovacoes/whatsapp/') + id;
    zapFetch(url, { method: 'POST', credentials: 'same-origin' }).then(function(d){if(!d){passo('pdf', '1 de 2 · o documento');
        erro('a busca não respondeu. Tente de novo.');return;}
        if(!d || !d.ok){
          passo('pdf', '1 de 2 · o documento');
          erro((d && d.erro) || 'não consegui ler este PDF.');
          return;
        }
        preencher(d);
        passo('form', '2 de 2 · confira e cadastre');
      });
  };

  // ── quem pode mandar ─────────────────────────────────────────────────────
  var voltarPara = 'pdf';

  function linhaFonte(nome, meta, bts){
    var li = document.createElement('div');
    li.className = 'rn-fonte';
    var n = document.createElement('span');
    n.className = 'nome';
    n.textContent = nome;
    var m = document.createElement('span');
    m.className = 'meta';
    m.textContent = meta;
    var caixa = document.createElement('span');
    caixa.className = 'bts';
    bts.forEach(function(b){
      var bt = document.createElement('button');
      bt.type = 'button';
      bt.className = 'mini';
      bt.textContent = b.rotulo;
      bt.onclick = b.quando;
      caixa.appendChild(bt);
    });
    li.appendChild(n);
    li.appendChild(m);
    li.appendChild(caixa);
    return li;
  }

  function fontesDesenhar(d){
    var corpo = el('rn-fontes-corpo');
    if(!corpo) return;
    corpo.innerHTML = '';
    var t1 = document.createElement('div');
    t1.className = 'rn-secao';
    t1.textContent = 'Podem mandar';
    corpo.appendChild(t1);
    if(!d.liberados.length){
      var nada = document.createElement('div');
      nada.className = 'rn-wpp';
      nada.innerHTML = '<div class="vazio">Ninguém ainda. Libere abaixo quem manda apólice.</div>';
      corpo.appendChild(nada);
    }
    d.liberados.forEach(function(f){
      corpo.appendChild(linhaFonte(f.nome, f.tipo_txt, [
        { rotulo: 'tirar', quando: function(){ fontesMudar({ acao: 'tirar', ref: f.ref }); } }]));
    });
    var novos = d.mandaram.filter(function(q){ return !q.liberado; });
    if(novos.length){
      var t2 = document.createElement('div');
      t2.className = 'rn-secao';
      t2.textContent = 'Mandaram PDF nos últimos 90 dias';
      corpo.appendChild(t2);
      novos.forEach(function(q){
        var quantos = q.quantos + (q.quantos === 1 ? ' documento' : ' documentos');
        corpo.appendChild(linhaFonte(q.nome, quantos + ' · ' + q.ultimo,
          d.tipos.map(function(t){
            return { rotulo: t.r.toLowerCase(), quando: function(){
              fontesMudar({ acao: 'liberar', ref: q.ref, nome: q.nome, tipo: t.c }); } };
          })));
      });
    }
  }

  function fontesMudar(dados){
    var fd = new FormData();
    Object.keys(dados).forEach(function(k){ fd.append(k, dados[k]); });
    zapFetch('/painel/renovacoes/remetentes', { method: 'POST', body: fd, credentials: 'same-origin' }).then(function(d){if(!d){erro('não consegui salvar agora.');return;}
        if(!d || !d.ok){ erro((d && d.erro) || 'não deu pra salvar.'); return; }
        wppLida = false;          // a lista de PDFs muda junto
        window.rnFontes(true);
      });
  }

  window.rnFontes = function(recarregando){
    erro('');
    if(!recarregando){
      var atual = PASSOS.filter(function(nome){
        var d = el('rn-p-' + nome);
        return d && !d.hidden;
      })[0];
      if(atual && atual !== 'fontes') voltarPara = atual;
    }
    passo('fontes', 'quem pode mandar');
    zapFetch('/painel/renovacoes/remetentes', { credentials: 'same-origin' }).then(function(d){if(!d){erro('não consegui carregar a lista.');return;}
        if(!d || !d.ok){ erro((d && d.erro) || 'não consegui carregar a lista.'); return; }
        fontesDesenhar(d);
      });
  };

  window.rnVoltar = function(){
    erro('');
    wppCarregar();
    passo(voltarPara, voltarPara === 'pdf' ? '1 de 2 · o documento' : '');
  };

  window.rnOutra = function(){
    var form = el('rn-form');
    if(form){
      form.reset();
      ['pdf_caminho', 'pdf_nome', 'pdf_bytes', 'pdf_lido'].forEach(function(n){
        var campo = form.querySelector('[name="' + n + '"]');
        if(campo) campo.value = '';
      });
      var bt = form.querySelector('.rn-salvar');
      if(bt) bt.textContent = 'Cadastrar apólice';
    }
    var c = el('rn-conf');
    if(c) c.innerHTML = '';
    var arq = document.querySelector('#rn-p-pdf input[type=file]');
    if(arq) arq.value = '';
    erro('');
    wppCarregar();
    passo('pdf', '1 de 2 · o documento');
  };
})();
</script>
<script>var _KB_MOTIVOS = {{ motivos|tojson }}; var _KB_DECISOES = {{ decisoes|tojson }};</script>
<script>{{ balao_js }}</script>
<script>{{ janela_js }}</script>
{% endblock %}"""

_env.loader.mapping["renovacoes"] = _TPL
_env.loader.mapping["renovacoes_linha"] = _TPL_LINHA
_env.loader.mapping["renovacoes_conf"] = _TPL_CONF
