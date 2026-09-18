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
async def importar_pdf(request: Request, arquivo: UploadFile = File(...)):
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
    if not gerencia:
        return RedirectResponse("/painel/renovacoes", status_code=303)
    from starlette.concurrency import run_in_threadpool
    if not await run_in_threadpool(_cofre.configurado):
        return RedirectResponse("/painel/renovacoes?erro=o cofre de documentos não está "
                                "configurado nesta instalação", status_code=303)
    conteudo = await arquivo.read()
    nome = arquivo.filename or ""
    try:
        r = await run_in_threadpool(_ler_e_guardar, conta[0], conteudo, nome)
    except ValueError as e:
        return RedirectResponse(f"/painel/renovacoes?erro={e}", status_code=303)
    except Exception as e:  # noqa: BLE001
        _log.warning("import de apólice falhou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return RedirectResponse("/painel/renovacoes?erro=não consegui ler este PDF",
                                status_code=303)
    L = r["leitura"]
    ctx = await run_in_threadpool(_contexto, request, conta, gerencia)
    ctx["conferir"] = {
        "seguradora": L.seguradora, "reconhecida": L.reconhecida, "paginas": L.paginas,
        "checagens": L.checagens, "avisos": L.avisos, "nao_achou": L.nao_achou,
        "n_campos": len(L.campos), "ok": L.ok(), "pdf_nome": r["nome"],
        "pdf_caminho": r["caminho"], "pdf_bytes": r["bytes"],
        "pdf_lido": apdf.resumo_para_guardar(L),
    }
    ctx["form"] = apdf.para_formulario(L)
    return _render("renovacoes", request, **ctx)


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
                   pdf_bytes: str = Form(""), pdf_lido: str = Form("")):
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
            return RedirectResponse(f"/painel/renovacoes?erro=cliente: {e}", status_code=303)
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
        ap.salvar(pool, conta[0], dados, _int(apolice_id))
    except ValueError as e:
        return RedirectResponse(f"/painel/renovacoes?erro={e}", status_code=303)
    except _UniqueViolation:
        # o índice da 278 (nº da apólice) ou o da 286 (nº da proposta): é a mesma
        # apólice sendo cadastrada de novo — o caso comum é reimportar o mesmo PDF.
        # "não deu pra salvar" esconderia o motivo; este diz o que fazer.
        return RedirectResponse("/painel/renovacoes?aba=carteira&erro=esta apólice já está "
                                "cadastrada — procure pelo número na Carteira", status_code=303)
    except Exception as e:  # noqa: BLE001
        _log.warning("apólice não salvou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return RedirectResponse("/painel/renovacoes?erro=não deu pra salvar", status_code=303)
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


_TPL = r"""{% extends "base" %}{% block conteudo %}
{# CSS e JS da janela/balão são strings CRUAS (Markup) — o embrulho é da tela, como no
   funil e no Follow-up. Soltas, viram um muro de texto no topo da Carteira (visto
   em 18/09, na única olhada antes de publicar). #}
<style>{{ balao_css }}{{ janela_css }}</style>
<style>
.rn-topo{display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap;margin-bottom:.2rem}
.rn-topo h2{margin:0;font-size:1.25rem}
.rn-abas{display:flex;gap:.15rem;border-bottom:1px solid var(--borda);margin:.7rem 0 1rem;overflow-x:auto}
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
.rn-bt{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:.35rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
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
.rn-busca{display:flex;gap:.4rem;margin-bottom:.8rem;flex-wrap:wrap}
.rn-busca input{flex:1;min-width:180px;background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;padding:.42rem .6rem;color:var(--txt);font-size:.85rem}
.rn-rol{overflow-x:auto;border:1px solid var(--borda);border-radius:11px}
.rn-tab{border-collapse:collapse;width:100%;min-width:620px;font-size:.84rem}
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
.rn-drop{border:1.5px dashed var(--neon-borda);background:var(--neon-fundo);border-radius:10px;
  padding:.8rem .9rem;margin-bottom:.8rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.rn-drop .t{font-weight:600;font-size:.9rem;color:var(--verde-claro)}
.rn-drop .s{font-size:.76rem;color:var(--txt-mut)}
.rn-drop input[type=file]{font-size:.8rem;color:var(--txt-mut);max-width:100%}
.rn-conf{background:var(--card);border:1px solid var(--neon-borda);border-radius:13px;padding:.85rem 1rem;margin-bottom:.8rem}
.rn-conf .cab{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap;margin-bottom:.5rem}
.rn-conf h3{margin:0;font-size:.95rem}
.rn-conf .chk{display:grid;grid-template-columns:auto 1fr;gap:.2rem .6rem;font-size:.82rem}
.rn-conf .ok{color:var(--verde-claro)}.rn-conf .no{color:#E98A80}
.rn-conf .falt{font-size:.78rem;color:var(--txt-mut);margin-top:.5rem}
.rn-pdf{font-size:.68rem;font-weight:700;letter-spacing:.05em;padding:.08rem .4rem;border-radius:5px;
  background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6;text-decoration:none}
</style>

<div class="rn-topo">
  <h2>Renovações</h2>
  {% if gerencia %}
  {# âncora não abre <details>; o onclick abre e a âncora leva até lá #}
  <a class="rn-bt" href="#nova" style="text-decoration:none"
     onclick="var d=document.getElementById('nova');if(d)d.open=true">+ Nova apólice</a>
  {% endif %}
</div>

<nav class="rn-abas">
  {% for chave, rot in abas %}
    {% if chave != 'percentuais' or gerencia %}
    <a href="/painel/renovacoes?aba={{ chave }}" class="{% if aba == chave %}on{% endif %}">{{ rot }}{% if chave == 'carteira' and n_carteira %} ({{ n_carteira }}){% endif %}</a>
    {% endif %}
  {% endfor %}
</nav>

{% if erro %}<div class="rn-aviso ambar"><b>Não salvou:</b> {{ erro }}</div>{% endif %}

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
  <details class="rn-det" id="nova" {% if conferir or not n_carteira %}open{% endif %}>
    <summary>+ cadastrar uma apólice</summary>

    {% if cofre_ok and not conferir %}
    {# O CAMINHO PRINCIPAL É O PDF (decisão do dono, 18/09). Digitar continua
       existindo — é o caminho quando não há PDF ou o layout não é reconhecido. #}
    <form class="rn-drop" method="post" action="/painel/renovacoes/importar" enctype="multipart/form-data">
      <div style="flex:1;min-width:200px"><div class="t">Tem a apólice em PDF? Comece por ela.</div>
        <div class="s">Eu leio os campos e você confere antes de salvar. Allianz reconhecida;
          outras seguradoras entram à medida que os PDFs chegarem.</div></div>
      <input type="file" name="arquivo" accept="application/pdf" required>
      <button class="rn-bt">Ler o PDF</button>
    </form>
    {% endif %}

    {% if conferir %}
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
    {% endif %}

    <div class="rn-bloco" style="margin:0">
      <h3>{% if conferir %}Confira e cadastre{% else %}Cadastrar apólice{% endif %}</h3>
      <div class="sub">Auto por enquanto — é o que a carteira tem hoje. Os outros ramos já
        gravam; o que falta pra eles é o formulário, não o cadastro.</div>
      <form method="post" action="/painel/renovacoes/apolice">
        {% if conferir %}
        <input type="hidden" name="pdf_caminho" value="{{ conferir.pdf_caminho }}">
        <input type="hidden" name="pdf_nome" value="{{ conferir.pdf_nome }}">
        <input type="hidden" name="pdf_bytes" value="{{ conferir.pdf_bytes }}">
        <input type="hidden" name="pdf_lido" value='{{ conferir.pdf_lido|tojson|forceescape }}'>
        {% endif %}
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

        <button class="rn-bt">{% if conferir %}Está certo — cadastrar{% else %}Cadastrar apólice{% endif %}</button>
        {% if conferir %}<a class="rn-bt fraco" href="/painel/renovacoes" style="text-decoration:none;margin-left:.4rem">descartar</a>{% endif %}
      </form>
    </div>
  </details>
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
    <tr><th>Cliente</th><th>Seguradora</th><th>Ramo</th><th>Vence</th><th>Situação</th>
        <th>Prêmio</th><th>Comissão</th></tr>
    {% for a in carteira %}
    <tr {% if a.cliente_id %}class="rn-linha" onclick="kbAbrirSegurado(event,{{ a.cliente_id }},this,'cliente')"{% endif %}>
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
    {% endfor %}
  </table></div>
  {% else %}
  <div class="rn-vazio"><div class="t">
    {% if busca %}Nada encontrado para “{{ busca }}”.{% else %}A carteira está vazia.{% endif %}</div>
    <div class="s">{% if busca %}A busca olha nome do cliente, seguradora, placa, modelo e número da
      apólice.{% else %}Cadastre a primeira em <a href="/painel/renovacoes#nova">Renovações</a>.{% endif %}</div>
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
<script>var _KB_MOTIVOS = {{ motivos|tojson }}; var _KB_DECISOES = {{ decisoes|tojson }};</script>
<script>{{ balao_js }}</script>
<script>{{ janela_js }}</script>
{% endblock %}"""

_env.loader.mapping["renovacoes"] = _TPL
