"""Aba "Prospecção" do painel — pipeline de outbound do vendedor.

Kanban por status, ficha do alvo (dados enriquecidos + histórico + registrar
contato), captação de leads (Manual / CSV / Google Maps) e enriquecimento de
CNPJ. Dono e gestor veem a carteira inteira, filtram por vendedor e atribuem;
o vendedor só enxerga os alvos dele.

Reusa o motor do portal: _render/_env (base, gate, nav) + conta_logada + o login
por membro (contas.equipe). Escopo multi-tenant sagrado: toda query filtra por
conta[0]. As fontes externas (Places, BrasilAPI) vivem em finance.prospeccao_fontes.
"""
import base64
import csv as _csv
import io
import json
import os
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response

from db.conexao import get_pool
from contas import equipe as eq
from finance import prospeccao_fontes as fontes
from finance import servicos_catalogo as scat
from finance.email_sender import enviar_email, remetente_configurado
from web.portal import _render, _env, conta_logada

router = APIRouter()

# ---------------------------------------------------------------- domínio (rótulos)
STATUS = [
    ("novo", "Novo"), ("contatado", "Contatado"), ("qualificado", "Qualificado"),
    ("proposta", "Proposta"), ("ganho", "Ganho"), ("perdido", "Perdido"),
]
STATUS_OK = {s for s, _ in STATUS}
STATUS_ROT = dict(STATUS)
TEMPERATURAS = [("frio", "Frio"), ("morno", "Morno"), ("quente", "Quente")]
TEMP_OK = {t for t, _ in TEMPERATURAS}
TEMP_COR = {"frio": "#5b9bd5", "morno": "#e0a33e", "quente": "#e0574f"}
TEMP_PILL = {"frio": ("#14273a", "#7bb8e6"), "morno": ("#2e2713", "#e0b25a"),
             "quente": ("#3a1a1a", "#f0917f")}
TIPOS = [("ligacao", "Ligação"), ("whatsapp", "WhatsApp"), ("email", "E-mail"),
         ("reuniao", "Reunião"), ("visita", "Visita"), ("nota", "Nota")]
TIPO_OK = {t for t, _ in TIPOS}
TIPO_ROT = dict(TIPOS)
RESULTADOS = [("", "—"), ("sem_resposta", "Sem resposta"), ("retornar", "Retornar"),
              ("interessado", "Interessado"), ("sem_interesse", "Sem interesse"),
              ("agendado", "Agendado"), ("fechado", "Fechado")]
RESULTADO_OK = {r for r, _ in RESULTADOS if r}
RESULTADO_ROT = dict(RESULTADOS)
_RES_VERDE = {"interessado", "agendado", "fechado"}
_RES_AMBAR = {"sem_resposta", "sem_interesse", "retornar"}


def _agora():
    return datetime.now(timezone.utc)


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _zap_link(numero: str) -> str:
    d = _so_digitos(numero)
    if not d:
        return ""
    if not d.startswith("55"):
        d = "55" + d
    return "https://wa.me/" + d


def _zap_link_texto(numero: str, texto: str) -> str:
    """wa.me com a mensagem já preenchida (o vendedor confere e envia em 1 clique)."""
    base = _zap_link(numero)
    if not base:
        return ""
    return base + "?text=" + quote(texto or "")


def _tem_ia() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _tem_credify() -> bool:
    from finance import credify as cf
    return cf.tem_credenciais()


def _ein_remetente(pool, conta_id):
    """E-mail que a empresa usa pra enviar (caixa própria ou global)."""
    from finance import email_inbound as _ein
    return _ein.remetente_conta(pool, conta_id)


def _membro_contato(pool, conta_id: int, vid):
    """nome + e-mail do responsável (pra assinar a mensagem e virar Reply-To)."""
    if not vid:
        return None, None
    with pool.connection() as c:
        r = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                      "where id=%s and conta_id=%s", (vid, conta_id)).fetchone()
    return (r[0], r[1]) if r else (None, None)


# ---------------------------------------------------------------- acesso / escopo
def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if not eq.caps_do_papel(papel).get("vendas"):
        return None, RedirectResponse("/painel", status_code=303)
    if not conta[11]:  # prospecção é ferramenta de EMPRESA (módulo PJ) — não de conta PF
        return None, RedirectResponse("/painel", status_code=303)
    ctx = {"conta": conta, "conta_id": conta[0], "papel": papel,
           "membro_id": request.session.get("membro_id"),
           "gerencia": papel in ("dono", "gestor"),  # vê a carteira toda + filtra
           "pode_atribuir": papel == "dono"}          # só o dono atribui/reatribui
    return ctx, None


def _vendedores(pool, conta_id: int) -> list[dict]:
    """Quem pode receber alvos: o dono (aparece pelo nome) + vendedores/gestores.
    O dono vem primeiro e rotulado, pra ele poder ficar com leads no próprio nome."""
    with pool.connection() as c:
        rows = c.execute(
            """select id, coalesce(nullif(nome,''), email), papel
                 from membros
                where conta_id=%s and ativo and papel in ('dono','vendedor','gestor')
                order by (papel<>'dono'), nome""", (conta_id,)).fetchall()
    out = []
    for r in rows:
        nome = r[1] + " (você)" if r[2] == "dono" else r[1]
        out.append({"id": r[0], "nome": nome, "papel": r[2]})
    return out


def _carrega_alvo(pool, conta_id: int, alvo_id: int):
    with pool.connection() as c:
        r = c.execute(
            """select p.id, p.empresa, p.cnpj, p.segmento, p.cidade, p.uf,
                      p.contato, p.cargo, p.telefone, p.whatsapp, p.email,
                      p.status, p.temperatura, p.valor_estimado_centavos, p.origem,
                      p.obs, p.instagram, p.socio, p.regime_tributario, p.porte,
                      p.ultimo_contato_em, p.proximo_contato_em, p.vendedor_id,
                      m.nome, p.orcamento_id, p.tem_site, p.maps_url, p.receita,
                      p.site_url, p.decisor_nome, p.decisor_cargo, p.decisor_telefone,
                      p.decisor_whatsapp, p.decisor_em, p.decisor_telefones
                 from prospeccao p
                 left join membros m on m.id = p.vendedor_id
                where p.id=%s and p.conta_id=%s""", (alvo_id, conta_id)).fetchone()
    if not r:
        return None
    cols = ["id", "empresa", "cnpj", "segmento", "cidade", "uf", "contato", "cargo",
            "telefone", "whatsapp", "email", "status", "temperatura", "valor",
            "origem", "obs", "instagram", "socio", "regime_tributario", "porte",
            "ultimo_contato_em", "proximo_contato_em", "vendedor_id", "vendedor_nome",
            "orcamento_id", "tem_site", "maps_url", "receita", "site_url",
            "decisor_nome", "decisor_cargo", "decisor_telefone", "decisor_whatsapp", "decisor_em",
            "decisor_telefones"]
    d = dict(zip(cols, r))
    d["zap_link"] = _zap_link(d["whatsapp"] or d["telefone"])
    d["tel_link"] = "tel:" + _so_digitos(d["telefone"]) if d["telefone"] else ""
    d["site_dominio"] = _dominio(d.get("site_url"))
    d["decisor_zap"] = _zap_link(d["decisor_telefone"]) if d.get("decisor_whatsapp") else ""
    d["decisor_tel_link"] = "tel:" + _so_digitos(d["decisor_telefone"]) if d.get("decisor_telefone") else ""
    # enriquece cada telefone do decisor com links (ligar / WhatsApp) pro template
    _TIPO_TEL = {"COMERCIAL": "Comercial", "RESIDENCIAL": "Residencial", "RECADO": "Recado",
                 "CELULAR": "Celular"}
    tels = d.get("decisor_telefones") if isinstance(d.get("decisor_telefones"), list) else []
    for t in tels:
        fmt = t.get("formatado") or ""
        t["tel_link"] = "tel:" + _so_digitos(fmt) if fmt else ""
        t["zap_link"] = _zap_link(fmt) if t.get("whatsapp") and fmt else ""
        t["tipo_rot"] = _TIPO_TEL.get((t.get("tipo") or "").upper(), (t.get("tipo") or "").title())
    d["decisor_telefones"] = tels
    return d


def _dominio(url: str | None) -> str:
    """Só o domínio, pra mostrar o link curto na ficha (sem http:// nem /caminho)."""
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("://", 1)[-1]
    u = u.split("/", 1)[0]
    return u[4:] if u.startswith("www.") else u


def _pode_ver(alvo: dict, ctx: dict) -> bool:
    if ctx["gerencia"]:
        return True
    return alvo["vendedor_id"] is not None and alvo["vendedor_id"] == ctx["membro_id"]


def _vendedor_destino(ctx: dict, vendedor_id: str, pool, conta_id: int):
    """Pra quem vai o alvo captado: só o dono escolhe (validando a conta);
    vendedor/gestor sempre pra si mesmo."""
    if not ctx["pode_atribuir"]:
        return ctx["membro_id"]
    if not (vendedor_id or "").isdigit():
        return None
    vid = int(vendedor_id)
    with pool.connection() as c:
        ok = c.execute("select 1 from membros where id=%s and conta_id=%s",
                       (vid, conta_id)).fetchone()
    return vid if ok else None


def _eh_ajax(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def _nome_vendedor(pool, conta_id: int, vid):
    if not vid:
        return None
    with pool.connection() as c:
        r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s and conta_id=%s",
                      (vid, conta_id)).fetchone()
    return r[0] if r else None


def _lead_card(id_, empresa, segmento, cidade, uf, temperatura, valor, vendedor):
    """Payload mínimo pra o JS montar um card no kanban (sem recarregar)."""
    return {"id": id_, "empresa": empresa, "segmento": segmento or "", "cidade": cidade or "",
            "uf": uf or "", "temperatura": temperatura, "valor": int(valor or 0), "vendedor": vendedor}


_RECEITA_KEYS = ["fonte", "razao_social", "nome_fantasia", "situacao", "abertura",
                 "capital_social", "natureza", "endereco", "inscricao_estadual",
                 "atividade_principal", "atividades_secundarias"]


def _receita_extras(d: dict) -> dict:
    """Só os campos ricos da Receita que valem guardar/mostrar (o resto já vai
    pras colunas)."""
    return {k: d.get(k) for k in _RECEITA_KEYS if d.get(k)}


# ================================================================ KANBAN
@router.get("/painel/prospeccao", response_class=HTMLResponse)
def prospeccao_kanban(request: Request, vendedor: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    conta_id = ctx["conta_id"]
    where = ["p.conta_id = %s"]
    params: list = [conta_id]
    filtro_vend = ""
    if not ctx["gerencia"]:
        where.append("p.vendedor_id = %s")
        params.append(ctx["membro_id"])
    else:
        filtro_vend = (vendedor or "").strip()
        if filtro_vend == "nao":
            where.append("p.vendedor_id is null")
        elif filtro_vend.isdigit():
            where.append("p.vendedor_id = %s")
            params.append(int(filtro_vend))
    with pool.connection() as c:
        rows = c.execute(
            f"""select p.id, p.empresa, p.segmento, p.cidade, p.uf, p.status,
                       p.temperatura, p.valor_estimado_centavos, p.proximo_contato_em,
                       p.telefone, p.whatsapp, p.vendedor_id, m.nome,
                       p.email, p.instagram, p.enriquecido_em
                  from prospeccao p
                  left join membros m on m.id = p.vendedor_id
                 where {' and '.join(where)}
                 order by p.proximo_contato_em asc nulls last, p.atualizado_em desc""",
            tuple(params)).fetchall()
    colunas = {s: [] for s, _ in STATUS}
    total_valor = 0
    for r in rows:
        card = {"id": r[0], "empresa": r[1], "segmento": r[2], "cidade": r[3],
                "uf": r[4], "status": r[5], "temperatura": r[6], "valor": r[7],
                "proximo": r[8], "telefone": r[9], "whatsapp": r[10],
                "vendedor_id": r[11], "vendedor": r[12],
                "tem_email": bool(r[13]), "tem_whatsapp": bool(r[10]),
                "tem_instagram": bool(r[14]), "enriquecido": bool(r[15])}
        colunas.get(r[5], colunas["novo"]).append(card)
        if r[5] != "perdido":
            total_valor += int(r[7] or 0)
    vends = _vendedores(pool, conta_id) if ctx["gerencia"] else []
    return _render("prospeccao", request, titulo="Prospecção", secao_ativa="prospeccao",
                   status=STATUS, colunas=colunas, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
                   temperaturas_all=TEMPERATURAS, gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"],
                   vendedores=vends, filtro_vend=filtro_vend, total_valor=total_valor,
                   total_alvos=len(rows), tem_places=fontes.tem_chave_places(),
                   aviso=request.session.pop("prosp_aviso", None))


# ================================================================ ADD MANUAL
@router.post("/painel/prospeccao/novo")
def prospeccao_novo(request: Request, empresa: str = Form(...), segmento: str = Form(""),
                    cidade: str = Form(""), uf: str = Form(""), contato: str = Form(""),
                    telefone: str = Form(""), whatsapp: str = Form(""), email: str = Form(""),
                    cnpj: str = Form(""), temperatura: str = Form("frio"),
                    valor: str = Form(""), origem: str = Form("manual"),
                    vendedor_id: str = Form(""), obs: str = Form(""),
                    socio: str = Form(""), regime_tributario: str = Form(""),
                    porte: str = Form(""), cargo: str = Form(""), instagram: str = Form(""),
                    site_url: str = Form(""), receita: str = Form(""), voltar: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ajax = _eh_ajax(request)
    empresa = (empresa or "").strip()
    destino = voltar if voltar in ("/painel/prospeccao", "/painel/prospeccao/captar") else "/painel/prospeccao"
    if not empresa:
        if ajax:
            return JSONResponse({"ok": False, "erro": "Informe ao menos o nome da empresa."}, status_code=400)
        request.session["prosp_aviso"] = "Informe ao menos o nome da empresa."
        return RedirectResponse(destino, status_code=303)
    temperatura = temperatura if temperatura in TEMP_OK else "frio"
    site_link = (site_url or "").strip()
    if site_link and "://" not in site_link:
        site_link = "https://" + site_link
    tem_site = True if site_link else None   # tem link → tem site; senão desconhecido
    # pacote da Receita que o "Buscar Receita" capturou (hidden), só as chaves ricas
    receita_json = None
    if (receita or "").strip():
        try:
            receita_json = json.dumps(_receita_extras(json.loads(receita)))
        except Exception:  # noqa: BLE001
            receita_json = None
    pool = get_pool()
    vend = _vendedor_destino(ctx, vendedor_id, pool, ctx["conta_id"])
    with pool.connection() as c:
        row = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, segmento, cidade,
                 uf, contato, cargo, telefone, whatsapp, email, cnpj, temperatura,
                 valor_estimado_centavos, origem, obs, socio, regime_tributario, porte,
                 instagram, site_url, tem_site, receita, criado_por)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) returning id""",
            (ctx["conta_id"], vend, empresa, segmento.strip() or None, cidade.strip() or None,
             (uf or "").strip()[:2].upper() or None, contato.strip() or None, cargo.strip() or None,
             telefone.strip() or None, whatsapp.strip() or None, email.strip().lower() or None,
             cnpj.strip() or None, temperatura, _reais_para_centavos(valor),
             (origem or "manual").strip() or None, obs.strip() or None,
             socio.strip() or None, regime_tributario.strip() or None, porte.strip() or None,
             instagram.strip() or None, site_link or None, tem_site, receita_json,
             ctx["membro_id"])).fetchone()
        c.commit()
    if ajax:
        lead = _lead_card(row[0], empresa, segmento.strip(), cidade.strip(),
                          (uf or "").strip()[:2].upper(), temperatura, _reais_para_centavos(valor),
                          _nome_vendedor(pool, ctx["conta_id"], vend) if ctx["gerencia"] else None)
        return JSONResponse({"ok": True, "lead": lead})
    request.session["prosp_aviso"] = f"“{empresa}” entrou na prospecção."
    return RedirectResponse(destino, status_code=303)


# ================================================================ CAPTAÇÃO
def _render_captar(request, ctx, aba="manual", resultados=None, busca=None):
    pool = get_pool()
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_captar", request, titulo="Captar leads",
                   secao_ativa="prospeccao", aba=aba, gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"],
                   vendedores=vends, temperaturas_all=TEMPERATURAS,
                   tem_places=fontes.tem_chave_places(), resultados=resultados,
                   busca=busca or {}, temp_cor=TEMP_COR,
                   aviso=request.session.pop("prosp_aviso", None))


@router.get("/painel/prospeccao/cnpj")
def cnpj_lookup(request: Request, cnpj: str = ""):
    """Consulta 1 CNPJ na Receita (BrasilAPI) e devolve os dados pro autofill."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    res = fontes.enriquecer_cnpj(cnpj)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "erro": res.get("erro")})
    return JSONResponse({"ok": True, "dados": res["dados"]})


@router.get("/painel/prospeccao/captar", response_class=HTMLResponse)
def captar_pagina(request: Request, aba: str = "manual"):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    aba = aba if aba in ("manual", "csv", "google") else "manual"
    return _render_captar(request, ctx, aba=aba)


@router.post("/painel/prospeccao/captar/csv", response_class=HTMLResponse)
async def captar_csv(request: Request, arquivo: UploadFile = File(...),
                     vendedor_id: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    vend = _vendedor_destino(ctx, vendedor_id, pool, ctx["conta_id"])
    raw = await arquivo.read()
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1", errors="replace")
    # detecta separador (vírgula ou ponto-e-vírgula)
    amostra = texto[:2000]
    sep = ";" if amostra.count(";") > amostra.count(",") else ","
    leitor = _csv.DictReader(io.StringIO(texto), delimiter=sep)
    mapa = {"empresa": ["empresa", "nome", "razao_social", "razão social", "fantasia"],
            "telefone": ["telefone", "fone", "tel"], "whatsapp": ["whatsapp", "zap", "celular"],
            "cidade": ["cidade", "municipio", "município"], "uf": ["uf", "estado"],
            "segmento": ["segmento", "ramo", "categoria"], "contato": ["contato", "responsavel", "responsável"],
            "email": ["email", "e-mail"], "cnpj": ["cnpj"]}
    inseridos, pulados = 0, 0
    with pool.connection() as c:
        for linha in leitor:
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in linha.items() if k}
            def pega(campo):
                for h in mapa[campo]:
                    if norm.get(h):
                        return norm[h]
                return ""
            empresa = pega("empresa")
            if not empresa:
                # sem cabeçalho reconhecível: usa a 1ª coluna como empresa
                empresa = next((v for v in norm.values() if v), "")
            if not empresa:
                pulados += 1
                continue
            c.execute(
                """insert into prospeccao (conta_id, vendedor_id, empresa, segmento,
                     cidade, uf, contato, telefone, whatsapp, email, cnpj, temperatura,
                     origem, criado_por)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'frio','csv',%s)""",
                (ctx["conta_id"], vend, empresa[:250], pega("segmento") or None,
                 pega("cidade") or None, pega("uf")[:2].upper() or None,
                 pega("contato") or None, pega("telefone") or None, pega("whatsapp") or None,
                 (pega("email").lower() or None), pega("cnpj") or None, ctx["membro_id"]))
            inseridos += 1
        c.commit()
    msg = f"{inseridos} lead(s) importado(s) do CSV." + (f" {pulados} linha(s) sem nome ignorada(s)." if pulados else "")
    if _eh_ajax(request):
        return JSONResponse({"ok": True, "inseridos": inseridos, "pulados": pulados, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse("/painel/prospeccao", status_code=303)


@router.post("/painel/prospeccao/captar/buscar", response_class=HTMLResponse)
def captar_buscar(request: Request, segmento: str = Form(...), cidade: str = Form(""),
                  bairro: str = Form(""), rua: str = Form(""),
                  esconder_redes: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    res = fontes.buscar_places(segmento, cidade, bairro=bairro, rua=rua)
    itens = res.get("itens", [])
    esconder = esconder_redes == "1"
    if esconder:
        itens = [i for i in itens if not i["rede"]]
    for i in itens:
        i["pack"] = _pack({"e": i["empresa"], "t": i["telefone"], "c": i.get("cidade") or cidade.strip(),
                           "u": i.get("uf") or "", "sg": i.get("segmento") or "",
                           "p": i["place_id"], "s": 1 if i["tem_site"] else 0,
                           "tp": i["temperatura"], "en": i["endereco"],
                           "r": i.get("rating"), "n": i.get("avaliacoes"),
                           "m": i.get("maps_uri") or "", "st": i.get("status") or "",
                           "w": i.get("site") or ""})
    n_redes = sum(1 for x in res.get("itens", []) if x["rede"]) if esconder else 0
    busca = {"segmento": segmento, "cidade": cidade, "bairro": bairro, "rua": rua,
             "esconder": esconder, "ok": res.get("ok"), "erro": res.get("erro"), "n_redes": n_redes}
    if _eh_ajax(request):
        enxuto = [{"empresa": i["empresa"], "telefone": i["telefone"], "rating": i.get("rating"),
                   "tem_site": i["tem_site"], "endereco": i["endereco"],
                   "segmento": i.get("segmento") or "", "cidade": i.get("cidade") or "",
                   "uf": i.get("uf") or "", "aberto": i.get("aberto", True),
                   "temperatura": i["temperatura"], "pack": i["pack"]} for i in itens]
        return JSONResponse({"ok": res.get("ok"), "erro": res.get("erro"),
                             "itens": enxuto, "n_redes": n_redes})
    return _render_captar(request, ctx, aba="google", resultados=itens, busca=busca)


@router.post("/painel/prospeccao/captar/importar")
async def captar_importar(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    form = await request.form()
    escolhidos = form.getlist("itens")
    vend = _vendedor_destino(ctx, form.get("vendedor_id", ""), pool, ctx["conta_id"])
    nome_vend = _nome_vendedor(pool, ctx["conta_id"], vend) if ctx["gerencia"] else None
    inseridos, dup, leads = 0, 0, []
    with pool.connection() as c:
        for token in escolhidos:
            d = _unpack(token)
            if not d or not d.get("e"):
                continue
            pid = d.get("p") or None
            if pid and c.execute("select 1 from prospeccao where conta_id=%s and place_id=%s",
                                 (ctx["conta_id"], pid)).fetchone():
                dup += 1
                continue
            partes = []
            if d.get("en"):
                partes.append(d["en"])
            if d.get("r"):
                partes.append(f"nota {d['r']} ({d.get('n', 0)} aval.)")
            if d.get("st") and d["st"] != "OPERATIONAL":
                partes.append("status: " + d["st"])
            obs = " · ".join(partes)   # link do mapa vai na coluna própria, não na obs
            temp = d.get("tp") if d.get("tp") in TEMP_OK else "frio"
            row = c.execute(
                """insert into prospeccao (conta_id, vendedor_id, empresa, segmento, cidade,
                     uf, telefone, temperatura, tem_site, place_id, origem, obs, maps_url, site_url, criado_por)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'google_places',%s,%s,%s,%s) returning id""",
                (ctx["conta_id"], vend, d["e"][:250], d.get("sg") or None, d.get("c") or None,
                 (d.get("u") or "")[:2].upper() or None, d.get("t") or None, temp,
                 bool(d.get("s")), pid, obs or None, d.get("m") or None, d.get("w") or None,
                 ctx["membro_id"])).fetchone()
            leads.append(_lead_card(row[0], d["e"][:250], d.get("sg") or "", d.get("c") or "",
                                    (d.get("u") or "")[:2].upper(), temp, 0, nome_vend))
            inseridos += 1
        c.commit()
    msg = f"{inseridos} lead(s) adicionado(s) do Google." + (f" {dup} já existia(m)." if dup else "")
    if _eh_ajax(request):
        return JSONResponse({"ok": True, "inseridos": inseridos, "dup": dup, "leads": leads, "msg": msg})
    request.session["prosp_aviso"] = msg
    return RedirectResponse("/painel/prospeccao", status_code=303)


# ================================================================ COMUNICAÇÃO (inbox)
_CANAIS_COMM = ("email", "whatsapp")


def _preview(descricao: str) -> str:
    """Prévia curta da mensagem: pega o corpo (depois do cabeçalho) e a 1ª linha."""
    corpo = (descricao or "").split("\n\n", 1)
    txt = corpo[1] if len(corpo) > 1 else corpo[0]
    return " ".join(txt.split())[:90]


CANAL_ROT = {"email": "✉️ E-mail", "whatsapp": "💬 WhatsApp",
             "messenger": "🔵 Messenger", "instagram": "📸 Instagram"}
CANAIS_TODOS = ("email", "whatsapp", "messenger", "instagram")
_QR_ROT = {"conectado": "✅ Conectado! Já pode captar leads por aqui.",
           "aguardando_qr": "📱 Escaneie o QR no WhatsApp do celular (Aparelhos conectados › Conectar aparelho).",
           "reconectando": "🔄 Reconectando…",
           "desconectado": "Desconectado. Clique em Gerar QR."}


def _canal_ident(c, conta_id, canal):
    """Identificador (número/página) do canal daquela empresa, se configurado."""
    r = c.execute("select identificador from canais_config where conta_id=%s and canal=%s and ativo",
                  (conta_id, canal)).fetchone()
    return r[0] if r else None


def _conta_por_ident(c, canal, ident_digitos):
    """Roteia o inbound: acha a empresa dona do número que recebeu (últimos 11 díg)."""
    r = c.execute(
        r"""select conta_id from canais_config
             where canal=%s and ativo
               and right(regexp_replace(identificador,'\D','','g'), 11) = right(%s, 11)
             limit 1""", (canal, ident_digitos)).fetchone()
    return r[0] if r else None


def _canais_status(pool, conta_id: int) -> dict:
    """Status de cada canal: credencial global + número/página da empresa (banco)."""
    twilio = bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"))
    meta_app = bool(os.environ.get("META_APP_SECRET") and os.environ.get("META_VERIFY_TOKEN"))
    nums = {}
    email_senha = None
    tokens = {}
    provs = {}
    wa_phone = {}
    with pool.connection() as c:
        for (canal, ident, tok, prov, waid) in c.execute(
                """select canal, identificador, token, coalesce(provedor,'twilio'), wa_phone_id
                     from canais_config where conta_id=%s and ativo""",
                (conta_id,)).fetchall():
            nums[canal] = ident
            tokens[canal] = tok
            provs[canal] = prov
            wa_phone[canal] = waid
        er = c.execute(
            "select imap_senha from canais_config where conta_id=%s and canal='email'",
            (conta_id,)).fetchone()
        email_senha = er[0] if er else None
    email_ident = nums.get("email")
    # RECEBER precisa de endereço + senha (própria no banco, ou a do env quando é a mesma caixa do SMTP)
    env_ok = bool(email_ident) and (email_ident.strip().lower()
             == (os.environ.get("SMTP_USER") or "").strip().lower()) and bool(os.environ.get("SMTP_SENHA"))
    email_rx = bool(email_ident) and (bool(email_senha) or env_ok)
    # Messenger/Instagram: precisa do app (env) + Página/IG id + Page Token (por conta)
    messenger_ok = meta_app and bool(nums.get("messenger")) and bool(tokens.get("messenger"))
    instagram_ok = meta_app and bool(nums.get("instagram")) and bool(tokens.get("instagram"))
    # WhatsApp: por provedor — Cloud API (número próprio) precisa de phone_id + token +
    # app da Meta; Twilio precisa das credenciais globais + número da empresa.
    wa_prov = provs.get("whatsapp") or "twilio"
    if wa_prov == "cloud":
        whatsapp_ok = meta_app and bool(wa_phone.get("whatsapp")) and bool(tokens.get("whatsapp"))
    elif wa_prov == "qr":
        from finance import whatsapp_qr as _wq
        whatsapp_ok = _wq.configurado()   # serviço ligado; a sessão em si aparece na aba QR
    else:
        whatsapp_ok = twilio and bool(nums.get("whatsapp"))
    from finance import email_inbound as _ein
    rem_conta = _ein.remetente_conta(pool, conta_id)     # caixa da EMPRESA (ou global)
    return {
        "email": bool(rem_conta),                         # ENVIAR (caixa da empresa/global)
        "email_remetente": rem_conta or "",               # o e-mail que vai no From
        "email_rx": email_rx,                             # RECEBER (caixa da conta)
        "email_ident": email_ident or "",
        "whatsapp": whatsapp_ok,
        "wa_provedor": wa_prov,
        "wa_phone_set": bool(wa_phone.get("whatsapp")),
        "messenger": messenger_ok,
        "instagram": instagram_ok,
        "twilio": twilio, "meta": meta_app, "numeros": nums,
        "tokens_set": {k: bool(v) for k, v in tokens.items()},
    }


_AGENTE_PADRAO = {"ativo": False, "limiar_confianca": 80, "horario": "comercial",
                  "tom": "informal", "max_trocas": 20, "escalar_para": "dono_lead",
                  "pode_responder": True, "pode_qualificar": True, "pode_agendar": True,
                  "pode_orcamento": True, "orcamento_proativo": False}


def _agente_config(c, conta_id: int) -> dict:
    """Config do agente da empresa (defaults se ainda não salvou)."""
    r = c.execute(
        """select ativo, limiar_confianca, horario, tom, max_trocas, escalar_para,
                  pode_responder, pode_qualificar, pode_agendar, pode_orcamento, orcamento_proativo
             from agente_config where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(_AGENTE_PADRAO)
    ks = ["ativo", "limiar_confianca", "horario", "tom", "max_trocas", "escalar_para",
          "pode_responder", "pode_qualificar", "pode_agendar", "pode_orcamento", "orcamento_proativo"]
    return dict(zip(ks, r))


def _agente_conhecimento(c, conta_id: int) -> dict:
    """Base de conhecimento: {instrucoes: texto, faqs: [{id, pergunta, resposta}]}."""
    rows = c.execute(
        "select id, tipo, pergunta, resposta from agente_conhecimento where conta_id=%s order by ordem, id",
        (conta_id,)).fetchall()
    instr, faqs = "", []
    for (id_, tipo, perg, resp) in rows:
        if tipo == "instrucoes":
            instr = resp or ""
        else:
            faqs.append({"id": id_, "pergunta": perg or "", "resposta": resp or ""})
    return {"instrucoes": instr, "faqs": faqs}


def _conversas_list(c, conta_id, gerencia, membro_id, canal="", vend="", escopo=""):
    """Lista de conversas (topo por última msg) — usada na página e no polling.
    escopo: 'email' = só e-mail (aba E-mails); 'msg' = só mensageiros (aba Conversas)."""
    where = ["cv.conta_id=%s"]
    params = [conta_id]
    if not gerencia:
        where.append("p.vendedor_id=%s")
        params.append(membro_id)
    elif (vend or "").isdigit():   # filtro por vendedor (só dono/gestor)
        where.append("p.vendedor_id=%s")
        params.append(int(vend))
    if escopo == "email":
        where.append("cv.canal='email'")
    elif canal in CANAIS_TODOS and canal != "email":
        where.append("cv.canal=%s")
        params.append(canal)
    elif escopo == "msg":
        where.append("cv.canal <> 'email'")
    rows = c.execute(f"""
        select cv.id, cv.canal, cv.status, cv.ultima_msg_em, cv.prospeccao_id,
               coalesce(p.empresa, cv.contato_ref, '—'), p.cidade, p.uf,
               lm.texto, lm.autor, lm.membro_id, mm.nome, cnt.n, lm.id
          from conversas cv
          left join prospeccao p on p.id = cv.prospeccao_id
          left join lateral (select id, texto, autor, membro_id from mensagens
                              where conversa_id=cv.id order by criado_em desc limit 1) lm on true
          left join membros mm on mm.id = lm.membro_id
          join lateral (select count(*) n from mensagens where conversa_id=cv.id) cnt on true
         where {' and '.join(where)}
         order by cv.ultima_msg_em desc limit 100""", tuple(params)).fetchall()
    out = []
    for r in rows:
        if r[9] == "bot":
            quem = "🤖 Agente"
        elif r[9] == "lead":
            quem = r[5]
        elif r[10] and r[10] == membro_id:
            quem = "Você"
        else:
            quem = r[11] or "—"
        out.append({"id": r[0], "canal": r[1], "canal_rot": CANAL_ROT.get(r[1], r[1]),
                    "status": r[2], "quando": r[3], "empresa": r[5], "cidade": r[6],
                    "uf": r[7], "preview": _preview(r[8]), "quem": quem, "n": r[12],
                    "ult_autor": r[9], "ult_msg_id": r[13] or 0})
    return out


@router.get("/painel/prospeccao/comunicacao/lista")
def prospeccao_comunicacao_lista(request: Request, canal: str = "", vendedor: str = "", escopo: str = ""):
    """Lista de conversas em JSON (pro polling em tempo real)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False}, status_code=401)
    escopo = escopo if escopo in ("email", "msg") else "msg"
    with get_pool().connection() as c:
        convs = _conversas_list(c, ctx["conta_id"], ctx["gerencia"], ctx["membro_id"],
                                canal=canal, vend=vendedor, escopo=escopo)
    for cv in convs:
        cv["quando"] = cv["quando"].strftime("%d/%m %H:%M") if cv["quando"] else ""
    return JSONResponse({"ok": True, "convs": convs})


@router.get("/painel/prospeccao/comunicacao", response_class=HTMLResponse)
def prospeccao_comunicacao(request: Request, aba: str = "conversas", canal: str = "", vendedor: str = ""):
    """Hub omnichannel: Conversas · E-mails · Agente · Canais (lê de conversas/mensagens)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    aba = aba if aba in ("conversas", "emails", "agente", "canais") else "conversas"
    pool = get_pool()
    filtro_vend = (vendedor or "").strip() if ctx["gerencia"] else ""
    escopo = "email" if aba == "emails" else "msg"
    convs = []
    with pool.connection() as c:
        convs = _conversas_list(c, ctx["conta_id"], ctx["gerencia"], ctx["membro_id"],
                                canal=canal, vend=filtro_vend, escopo=escopo)
        ag_cfg, ag_conhec = None, None
        if aba == "agente":
            ag_cfg = _agente_config(c, ctx["conta_id"])
            ag_conhec = _agente_conhecimento(c, ctx["conta_id"])
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_comunicacao", request, titulo="Comunicação",
                   secao_ativa="prospeccao", aba=aba, convs=convs, escopo=escopo, canal=canal,
                   canais=_canais_status(pool, ctx["conta_id"]), canal_rot=CANAL_ROT,
                   gerencia=ctx["gerencia"], vendedores=vends, filtro_vend=filtro_vend,
                   remetente=_ein_remetente(pool, ctx["conta_id"]), tem_ia=_tem_ia(),
                   ag_cfg=ag_cfg, ag_conhec=ag_conhec,
                   aviso=request.session.pop("prosp_aviso", None))


@router.get("/painel/prospeccao/comunicacao/thread/{conversa_id}")
def prospeccao_comunicacao_thread(request: Request, conversa_id: int):
    """Thread de uma conversa: mensagens in/out (read-only nesta fase)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            """select cv.canal, cv.status, cv.prospeccao_id, p.empresa, p.segmento,
                      p.cidade, p.uf, p.whatsapp, p.telefone, p.email, p.status, m.nome,
                      p.vendedor_id, cv.contato_ref, cv.agente_ativo
                 from conversas cv
                 left join prospeccao p on p.id = cv.prospeccao_id
                 left join membros m on m.id = p.vendedor_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        # conversa sem lead (prospeccao_id null) não tem vendedor — só gerência ou
        # quem já é responsável vê; vendedor comum não acessa conversa órfã.
        if not ctx["gerencia"] and cv[2] is not None and cv[12] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        if not ctx["gerencia"] and cv[2] is None:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        rows = c.execute(
            """select msg.canal, msg.direcao, msg.autor, msg.criado_em, msg.texto, msg.membro_id, mm.nome
                 from mensagens msg left join membros mm on mm.id = msg.membro_id
                where msg.conversa_id=%s order by msg.criado_em asc""", (conversa_id,)).fetchall()
    msgs = []
    for (cn, direcao, autor, quando, texto, mid, nome) in rows:
        # só e-mail separa assunto (cabeçalho) do corpo; os outros canais são texto puro
        if cn == "email" and "\n\n" in (texto or ""):
            cab, _, corpo = (texto or "").partition("\n\n")
        else:
            cab, corpo = "", (texto or "")
        if autor == "bot":
            quem = "🤖 Agente"
        elif autor == "lead":
            quem = cv[3] or "Lead"
        else:
            quem = "Você" if mid and mid == ctx["membro_id"] else (nome or "—")
        msgs.append({"canal": cn, "direcao": direcao, "autor": autor,
                     "quando": quando.strftime("%d/%m %H:%M") if quando else "",
                     "quem": quem, "cabecalho": cab.strip(), "corpo": corpo.strip()})
    destino = cv[7] or cv[8] or cv[13]     # telefone do lead OU contato_ref (conversa sem lead)
    pode_wa = False
    if cv[0] == "whatsapp" and bool(destino):
        from finance import whatsapp_out
        with pool.connection() as _c:
            pode_wa = whatsapp_out.configurado_conta(_c, ctx["conta_id"])
    elif cv[0] == "email":
        _dest_mail = (cv[9] or cv[13] or "")            # e-mail do lead OU do contato órfão
        pode_wa = bool(remetente_configurado()) and ("@" in _dest_mail)
    elif cv[0] in ("messenger", "instagram"):
        with pool.connection() as _c:
            _t = _c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                            (ctx["conta_id"], cv[0])).fetchone()
        pode_wa = bool(_t and _t[0]) and bool(cv[13])   # token da Página + PSID/IGSID (contato_ref)
    lead = {"id": cv[2], "empresa": cv[3] or cv[13] or "—", "canal": cv[0], "canal_rot": CANAL_ROT.get(cv[0], cv[0]),
            "segmento": cv[4], "cidade": cv[5], "uf": cv[6],
            "whatsapp": destino, "email": cv[9], "vendedor": cv[11],
            "status_rot": STATUS_ROT.get(cv[10], cv[10] or "")}
    return JSONResponse({"ok": True, "lead": lead, "msgs": msgs,
                         "conversa_id": conversa_id, "pode_responder": pode_wa,
                         "agente_ativo": bool(cv[14])})


@router.post("/painel/prospeccao/comunicacao/responder")
def comunicacao_responder(request: Request, conversa_id: int = Form(...), texto: str = Form(...)):
    """Responde numa conversa (Fase B: WhatsApp via Twilio, dentro da janela 24h)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    texto = (texto or "").strip()
    if not texto:
        return JSONResponse({"ok": False, "erro": "vazio"})
    pool = get_pool()
    with pool.connection() as c:
        cv = c.execute(
            """select cv.canal, cv.prospeccao_id, cv.contato_ref, p.whatsapp, p.telefone,
                      p.vendedor_id, p.email
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if not ctx["gerencia"] and cv[5] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        canal = cv[0]
        if canal == "email":
            destino = (cv[6] or "").strip() or (cv[2] or "").strip()   # e-mail do lead OU contato órfão
            if "@" not in destino:
                return JSONResponse({"ok": False, "erro": "Sem e-mail de destino nesta conversa."})
            if not _ein_remetente(pool, ctx["conta_id"]):
                return JSONResponse({"ok": False, "erro": "E-mail não configurado (configure a caixa da empresa na aba Canais)."})
            ult = c.execute("""select texto from mensagens where conversa_id=%s and canal='email'
                                order by criado_em desc limit 1""", (conversa_id,)).fetchone()
            base = (ult[0] or "").split("\n\n", 1)[0].strip() if ult else ""
            base = re.sub(r"(?i)^\s*(re|fwd?):\s*", "", base).strip()
            assunto = ("Re: " + base) if base else (f"Contato · {ctx['conta'][2] or ''}").strip()
            _nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
            html = ("<div style=\"font-family:system-ui,Arial,sans-serif;font-size:15px;"
                    "line-height:1.6;color:#222\">"
                    + "".join(f"<p style=\"margin:0 0 12px\">{_html_escape(par)}</p>"
                              for par in texto.split("\n\n")) + "</div>")
            from finance import email_inbound as _ein
            ok = _ein.enviar_conta(pool, ctx["conta_id"], destino, assunto, html, texto_alt=texto,
                                   reply_to=email_rem or None, from_nome=(ctx["conta"][2] or None))
            if not ok:
                return JSONResponse({"ok": False, "erro": "Não consegui enviar o e-mail (confira a caixa da empresa na aba Canais)."})
            _add_msg(c, conversa_id, "email", "out", "humano", f"{assunto}\n\n{texto}", ctx["membro_id"])
            c.commit()
            return JSONResponse({"ok": True})
        if canal in ("messenger", "instagram"):
            from finance import meta_msg
            r = c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                          (ctx["conta_id"], canal)).fetchone()
            res = meta_msg.enviar(r[0] if r else None, (cv[2] or "").strip(), texto, canal)
            if not res.get("ok"):
                return JSONResponse({"ok": False, "erro": "Não consegui enviar (fora da janela de 24h, ou falta conectar a Página/token na aba Canais)."})
            _add_msg(c, conversa_id, canal, "out", "humano", texto, ctx["membro_id"], res.get("sid"))
            c.commit()
            return JSONResponse({"ok": True})
        if canal != "whatsapp":
            return JSONResponse({"ok": False, "erro": "canal_sem_resposta"})
        from finance import whatsapp_out
        numero = cv[3] or cv[4] or cv[2]
        res = whatsapp_out.enviar(c, ctx["conta_id"], numero, texto)
        if not res.get("ok"):
            erros = {"nao_configurado": "WhatsApp não conectado (falta credencial Twilio no Render).",
                     "sem_numero_empresa": "Configure o WhatsApp desta empresa na aba Canais.",
                     "numero_invalido": "Número do lead inválido.",
                     "qr_indisponivel": "A conexão por QR ainda não está ligada — use Twilio ou Cloud API."}
            return JSONResponse({"ok": False, "erro": erros.get(res.get("erro"), "Não consegui enviar (janela de 24h fechada? use template).")})
        _add_msg(c, conversa_id, "whatsapp", "out", "humano",
                 texto, ctx["membro_id"], res.get("sid"))
        # vendedor respondeu = assumiu a conversa: pausa o bot (não fala por cima).
        # Volta ao automático clicando "devolver ao agente".
        c.execute("update conversas set status='pendente', agente_ativo=false where id=%s",
                  (conversa_id,))
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/comunicacao/agente-conversa")
def comunicacao_agente_conversa(request: Request, conversa_id: int = Form(...), ativar: str = Form("")):
    """Assumir (desliga o bot nessa conversa) ou devolver ao agente (liga)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    on = ativar == "1"
    # devolver ao agente → 'aberta' (o webhook pode reativar); assumir → 'pendente'
    # (sticky: o webhook respeita e não reativa sozinho).
    novo_status = "aberta" if on else "pendente"
    with get_pool().connection() as c:
        r = c.execute(
            "update conversas set agente_ativo=%s, status=%s where id=%s and conta_id=%s returning id",
            (on, novo_status, conversa_id, ctx["conta_id"])).fetchone()
        c.commit()
    if not r:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
    return JSONResponse({"ok": True, "agente_ativo": on})


@router.post("/painel/prospeccao/comunicacao/email-sync")
def comunicacao_email_sync(request: Request):
    """Puxa os e-mails recebidos da caixa (IMAP) pra dentro do inbox, sob demanda."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import email_inbound as ein
    pool = get_pool()
    try:
        n = ein.sincronizar(pool, ctx["conta_id"])
    except Exception:  # noqa: BLE001
        n = 0
    if n:
        return JSONResponse({"ok": True, "novos": n})
    # nada novo: roda o diagnóstico pra explicar o porquê (IMAP off? login? spam?)
    try:
        diag = ein.diagnostico(pool, ctx["conta_id"])
    except Exception:  # noqa: BLE001
        diag = {"msg": "Nenhum e-mail novo."}
    return JSONResponse({"ok": True, "novos": 0, "detalhe": diag.get("msg") or "Nenhum e-mail novo."})


@router.post("/painel/prospeccao/comunicacao/email-config")
def comunicacao_email_config(request: Request, endereco: str = Form(...),
                             senha: str = Form(""), host: str = Form("")):
    """Salva a caixa de e-mail (endereço + senha de app) da conta pra RECEBER. Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = "/painel/prospeccao/comunicacao?aba=canais"
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura os canais."
        return RedirectResponse(destino, status_code=303)
    endereco = (endereco or "").strip()
    if "@" not in endereco:
        request.session["prosp_aviso"] = "Informe um e-mail válido."
        return RedirectResponse(destino, status_code=303)
    from finance import email_inbound as ein
    try:
        ein.salvar_config(get_pool(), ctx["conta_id"], endereco, senha, host)
        request.session["prosp_aviso"] = "Caixa de e-mail salva ✓ — clique em “Testar conexão”."
    except Exception:  # noqa: BLE001
        request.session["prosp_aviso"] = "Não consegui salvar a caixa de e-mail."
    return RedirectResponse(destino, status_code=303)


@router.post("/painel/prospeccao/comunicacao/email-testar")
def comunicacao_email_testar(request: Request):
    """Testa a conexão IMAP da caixa da conta e devolve o diagnóstico (pro botão)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import email_inbound as ein
    try:
        diag = ein.diagnostico(get_pool(), ctx["conta_id"])
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha no teste."})
    return JSONResponse({"ok": bool(diag.get("ok")), "msg": diag.get("msg") or ""})


@router.post("/painel/prospeccao/comunicacao/whatsapp-testar")
def comunicacao_whatsapp_testar(request: Request, numero: str = Form("")):
    """Manda um WhatsApp de teste pelo Cloud API da conta (confirma phone_id+token)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "Só o dono/gestor testa os canais."}, status_code=403)
    numero = (numero or "").strip()
    if not numero:
        return JSONResponse({"ok": False, "msg": "Informe um número (com DDD) pra receber o teste."})
    with get_pool().connection() as c:
        r = c.execute("""select coalesce(provedor,'twilio'), wa_phone_id, token
                           from canais_config where conta_id=%s and canal='whatsapp' and ativo""",
                      (ctx["conta_id"],)).fetchone()
    if not r or r[0] != "cloud":
        return JSONResponse({"ok": False, "msg": "Salve primeiro o WhatsApp no modo Cloud API (Phone Number ID + token)."})
    if not (r[1] and r[2]):
        return JSONResponse({"ok": False, "msg": "Falta o Phone Number ID ou o token — salve os dois e tente de novo."})
    from finance import whatsapp_cloud as wac
    res = wac.enviar_texto(r[1], r[2], numero,
                           "✅ Teste do ZAQ — se você recebeu esta mensagem, seu WhatsApp "
                           "(Cloud API) está conectado e pronto pra captar leads. 🎉")
    if res.get("ok"):
        return JSONResponse({"ok": True, "msg": "Enviado! Confira o WhatsApp desse número. 📲"})
    det = str(res.get("erro") or "")[:180]
    return JSONResponse({"ok": False, "msg": "Não enviou. A Meta respondeu: " + (det or "erro desconhecido")
                         + " · (token válido? número do destino verificado no painel da Meta?)"})


@router.post("/painel/prospeccao/comunicacao/whatsapp-qr-iniciar")
def comunicacao_whatsapp_qr_iniciar(request: Request):
    """Coloca a empresa no modo QR e pede o QR ao serviço Node pra exibir/escanear."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import whatsapp_qr as wq
    if not wq.configurado():
        return JSONResponse({"ok": False, "msg": "O serviço de QR ainda não está ligado (falta WA_QR_SERVICE_URL no ambiente)."})
    with get_pool().connection() as c:
        c.execute(
            """insert into canais_config (conta_id, canal, identificador, provedor, ativo)
               values (%s,'whatsapp',%s,'qr',true)
               on conflict (conta_id, canal)
               do update set provedor='qr', identificador=excluded.identificador,
                             ativo=true, atualizado_em=now()""",
            (ctx["conta_id"], "qr:" + str(ctx["conta_id"])))
        c.commit()
    r = wq.iniciar(ctx["conta_id"])
    return JSONResponse({"ok": bool(r.get("ok", True) and not r.get("erro")),
                         "status": r.get("status"), "qr": r.get("qr"),
                         "msg": _QR_ROT.get(r.get("status"), "") if r.get("status") else (r.get("erro") or "")})


@router.get("/painel/prospeccao/comunicacao/whatsapp-qr-status")
def comunicacao_whatsapp_qr_status(request: Request):
    """Consulta o status da sessão QR (pro polling do painel enquanto escaneia)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import whatsapp_qr as wq
    r = wq.status(ctx["conta_id"])
    st = r.get("status") or "desconectado"
    return JSONResponse({"ok": True, "status": st, "qr": r.get("qr"), "msg": _QR_ROT.get(st, "")})


@router.post("/painel/prospeccao/comunicacao/whatsapp-qr-sair")
def comunicacao_whatsapp_qr_sair(request: Request):
    """Desconecta a sessão QR e volta a empresa pro Twilio (padrão)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import whatsapp_qr as wq
    wq.sair(ctx["conta_id"])
    with get_pool().connection() as c:
        c.execute("delete from canais_config where conta_id=%s and canal='whatsapp' and coalesce(provedor,'twilio')='qr'",
                  (ctx["conta_id"],))
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/comunicacao/virar-lead")
def comunicacao_virar_lead(request: Request, conversa_id: int = Form(...)):
    """Promove uma conversa órfã (e-mail/WhatsApp de remetente novo, sem lead) a um
    lead da prospecção — só quando VOCÊ decide que vale."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    with get_pool().connection() as c:
        cv = c.execute(
            "select canal, prospeccao_id, contato_ref from conversas where id=%s and conta_id=%s",
            (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if cv[1]:
            return JSONResponse({"ok": True, "lead_id": cv[1]})     # já é lead
        ref = (cv[2] or "").strip()
        eh_email = "@" in ref
        vend = None if ctx["gerencia"] else ctx["membro_id"]
        lead_id = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, email, whatsapp,
                 origem, temperatura, status)
               values (%s,%s,%s,%s,%s,%s,'morno','novo') returning id""",
            (ctx["conta_id"], vend, ref or "Contato",
             ref if eh_email else None, None if eh_email else ("+" + ref if ref else None),
             "email_inbound" if eh_email else "whatsapp_inbound")).fetchone()[0]
        c.execute("update conversas set prospeccao_id=%s where id=%s", (lead_id, conversa_id))
        c.commit()
    return JSONResponse({"ok": True, "lead_id": lead_id})


@router.post("/painel/prospeccao/comunicacao/canal-numero")
def comunicacao_canal_numero(request: Request, canal: str = Form(...), numero: str = Form(""),
                             token: str = Form(""), provedor: str = Form(""),
                             wa_phone_id: str = Form("")):
    """Vincula (ou limpa) o identificador de um canal à empresa (nº WhatsApp Twilio,
    WhatsApp Cloud API do número próprio, ou Página/IG id + Page Token). Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = "/painel/prospeccao/comunicacao?aba=canais"
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura os canais."
        return RedirectResponse(destino, status_code=303)
    if canal not in ("whatsapp", "messenger", "instagram"):
        return RedirectResponse(destino, status_code=303)
    from finance import whatsapp_twilio as wa
    token = (token or "").strip()
    provedor = (provedor or "").strip()
    wa_phone_id = (wa_phone_id or "").strip()
    pool = get_pool()
    try:
        with pool.connection() as c:
            if canal == "whatsapp" and provedor == "cloud":
                # WhatsApp Cloud API (número próprio na Meta): precisa do Phone Number ID.
                if not wa_phone_id:
                    request.session["prosp_aviso"] = "Informe o Phone Number ID da Cloud API."
                    return RedirectResponse(destino, status_code=303)
                ident = wa.normalizar_from(numero) or ("wa:" + wa_phone_id)
                c.execute(
                    """insert into canais_config (conta_id, canal, identificador, provedor, wa_phone_id, ativo)
                       values (%s,'whatsapp',%s,'cloud',%s,true)
                       on conflict (conta_id, canal)
                       do update set identificador=excluded.identificador, provedor='cloud',
                                     wa_phone_id=excluded.wa_phone_id, ativo=true, atualizado_em=now()""",
                    (ctx["conta_id"], ident, wa_phone_id))
                if token:      # access token — vazio mantém o atual
                    c.execute("update canais_config set token=%s where conta_id=%s and canal='whatsapp'",
                              (token, ctx["conta_id"]))
                msg = "WhatsApp (Cloud API) conectado ✓ — aponte o webhook do número pra /webhooks/meta."
                c.commit()
                request.session["prosp_aviso"] = msg
                return RedirectResponse(destino, status_code=303)
            ident = wa.normalizar_from(numero) if canal == "whatsapp" else (numero or "").strip()
            if ident:
                extra = ", provedor='twilio', wa_phone_id=null" if canal == "whatsapp" else ""
                c.execute(
                    f"""insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                       values (%s,%s,%s,'twilio',true)
                       on conflict (conta_id, canal)
                       do update set identificador=excluded.identificador, ativo=true,
                                     atualizado_em=now(){extra}""",
                    (ctx["conta_id"], canal, ident))
                if token:      # Page Access Token (Meta) — vazio mantém o atual
                    c.execute("update canais_config set token=%s where conta_id=%s and canal=%s",
                              (token, ctx["conta_id"], canal))
                msg = "Canal vinculado a esta empresa ✓"
            else:
                c.execute("delete from canais_config where conta_id=%s and canal=%s", (ctx["conta_id"], canal))
                msg = "Canal removido."
            c.commit()
    except Exception:  # noqa: BLE001 — provável colisão do índice (canal, identificador)
        msg = "Esse número já está vinculado a outra empresa."
    request.session["prosp_aviso"] = msg
    return RedirectResponse(destino, status_code=303)


_AG_DESTINO = "/painel/prospeccao/comunicacao?aba=agente"


@router.post("/painel/prospeccao/comunicacao/agente-config")
async def comunicacao_agente_config(request: Request):
    """Salva a config do Agente (por empresa). Só dono/gestor."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor configura o agente."
        return RedirectResponse(_AG_DESTINO, status_code=303)
    f = await request.form()
    def _b(k):
        return bool(f.get(k))
    def _i(k, pad, lo, hi):
        try:
            return max(lo, min(hi, int(f.get(k) or pad)))
        except (ValueError, TypeError):
            return pad
    horario = f.get("horario") if f.get("horario") in ("comercial", "24h") else "comercial"
    tom = f.get("tom") if f.get("tom") in ("informal", "formal") else "informal"
    escalar = f.get("escalar_para") if f.get("escalar_para") in ("dono_lead", "plantao") else "dono_lead"
    vals = (_b("ativo"), _i("limiar_confianca", 80, 50, 95), horario, tom,
            _i("max_trocas", 20, 1, 100), escalar, _b("pode_responder"), _b("pode_qualificar"),
            _b("pode_agendar"), _b("pode_orcamento"), _b("orcamento_proativo"))
    with get_pool().connection() as c:
        c.execute(
            """insert into agente_config (conta_id, ativo, limiar_confianca, horario, tom,
                 max_trocas, escalar_para, pode_responder, pode_qualificar, pode_agendar,
                 pode_orcamento, orcamento_proativo)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (conta_id) do update set
                 ativo=excluded.ativo, limiar_confianca=excluded.limiar_confianca,
                 horario=excluded.horario, tom=excluded.tom, max_trocas=excluded.max_trocas,
                 escalar_para=excluded.escalar_para, pode_responder=excluded.pode_responder,
                 pode_qualificar=excluded.pode_qualificar, pode_agendar=excluded.pode_agendar,
                 pode_orcamento=excluded.pode_orcamento, orcamento_proativo=excluded.orcamento_proativo,
                 atualizado_em=now()""",
            (ctx["conta_id"], *vals))
        c.commit()
    request.session["prosp_aviso"] = "Agente atualizado ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/agente-instrucoes")
def comunicacao_agente_instrucoes(request: Request, texto: str = Form("")):
    """Salva as instruções gerais do agente (uma linha tipo='instrucoes' por conta)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(_AG_DESTINO, status_code=303)
    with get_pool().connection() as c:
        c.execute("delete from agente_conhecimento where conta_id=%s and tipo='instrucoes'", (ctx["conta_id"],))
        if (texto or "").strip():
            c.execute("""insert into agente_conhecimento (conta_id, tipo, resposta, ordem)
                         values (%s,'instrucoes',%s,0)""", (ctx["conta_id"], texto.strip()[:4000]))
        c.commit()
    request.session["prosp_aviso"] = "Instruções salvas ✓"
    return RedirectResponse(_AG_DESTINO, status_code=303)


@router.post("/painel/prospeccao/comunicacao/agente-faq")
def comunicacao_agente_faq(request: Request, pergunta: str = Form(""), resposta: str = Form(""),
                           excluir: str = Form("")):
    """Adiciona ou exclui uma pergunta/resposta da base do agente."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(_AG_DESTINO, status_code=303)
    with get_pool().connection() as c:
        if excluir.isdigit():
            c.execute("delete from agente_conhecimento where id=%s and conta_id=%s and tipo='faq'",
                      (int(excluir), ctx["conta_id"]))
            msg = "Pergunta removida."
        elif (pergunta or "").strip() and (resposta or "").strip():
            c.execute("""insert into agente_conhecimento (conta_id, tipo, pergunta, resposta, ordem)
                         values (%s,'faq',%s,%s,
                           coalesce((select max(ordem)+1 from agente_conhecimento where conta_id=%s and tipo='faq'),1))""",
                      (ctx["conta_id"], pergunta.strip()[:300], resposta.strip()[:2000], ctx["conta_id"]))
            msg = "Pergunta adicionada ✓"
        else:
            msg = "Preencha pergunta e resposta."
        c.commit()
    request.session["prosp_aviso"] = msg
    return RedirectResponse(_AG_DESTINO, status_code=303)


def _wa_inbound_conversa(c, conta_id, remetente, corpo, sid, nome_perfil, agente_on):
    """WhatsApp de ENTRADA (Twilio OU Cloud API): resolve lead+conversa pelo telefone,
    grava a mensagem (dedup por provider_sid) e reabre a janela/reativa o agente.
    Devolve conv_id. Um humano que 'assumiu' (status='pendente') não é reativado."""
    remetente = _so_digitos(remetente)
    alvo8 = remetente[-8:] if len(remetente) >= 8 else remetente
    lead = c.execute(
        r"""select id from prospeccao
             where conta_id=%s and right(regexp_replace(coalesce(whatsapp, telefone, ''), '\D', '', 'g'), 8) = %s
             order by atualizado_em desc limit 1""", (conta_id, alvo8)).fetchone()
    lead_id = lead[0] if lead else None
    if not lead_id:
        # contato NOVO (landing/WhatsApp) → vira lead QUENTE, não atribuído (o dono distribui).
        nome = (nome_perfil or "").strip() or "Contato WhatsApp"
        lead_id = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp,
                 origem, temperatura, status)
               values (%s, null, %s, %s, 'whatsapp_inbound', 'quente', 'novo') returning id""",
            (conta_id, nome[:250], "+" + remetente)).fetchone()[0]
    # acha a conversa do lead OU uma órfã por contato_ref (e vincula ela ao lead)
    conv = c.execute(
        """select id, prospeccao_id from conversas
            where conta_id=%s and canal='whatsapp'
              and (prospeccao_id=%s or (prospeccao_id is null and contato_ref=%s))
            order by ultima_msg_em desc limit 1""",
        (conta_id, lead_id, remetente)).fetchone()
    if conv:
        conv_id = conv[0]
        if conv[1] is None:
            c.execute("update conversas set prospeccao_id=%s where id=%s", (lead_id, conv_id))
    else:
        conv_id = c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, agente_ativo)
               values (%s,%s,'whatsapp',%s,'aberta',%s) returning id""",
            (conta_id, lead_id, remetente, agente_on)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                 values (%s,'whatsapp','in','lead',%s,%s)
                 on conflict (provider_sid) where provider_sid is not null do nothing""",
              (conv_id, (corpo or "")[:8000], sid))
    # reabre a janela de 24h e REATIVA o agente — a menos que um humano tenha assumido
    # (status='pendente'). O CASE lê o status ANTIGO da linha, então 'pendente' fica preservado.
    c.execute(
        """update conversas set ultima_msg_em=now(),
             janela_expira_em=now()+interval '24 hours',
             status = case when status='pendente' then 'pendente' else 'aberta' end,
             agente_ativo = case when status='pendente' then agente_ativo else %s end
           where id=%s""", (agente_on, conv_id))
    return conv_id


@router.post("/webhooks/twilio")
async def webhook_twilio(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens do WhatsApp (Twilio). Valida a assinatura, acha/cria a
    conversa pelo telefone→lead e grava a mensagem (entrada). Abre a janela de 24h."""
    from finance import whatsapp_twilio as wa
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    assinatura = request.headers.get("X-Twilio-Signature", "")
    # a Twilio assina com a URL PÚBLICA exata que ela chamou. Atrás do proxy do
    # Render, request.url vem com host/esquema internos — então reconstruímos a
    # partir dos cabeçalhos encaminhados (Host público + https).
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto", "https")
        url = f"{proto}://{host}{request.url.path}"
    else:
        url = wa.url_webhook() or str(request.url)
    if not wa.validar_assinatura(url, params, assinatura):
        import logging
        logging.getLogger("prospeccao").info(
            "webhook_twilio: assinatura inválida · url=%s · From=%s · To=%s",
            url, params.get("From", ""), params.get("To", ""))
        return Response(status_code=403)
    corpo = params.get("Body", "")
    remetente = _so_digitos(params.get("From", ""))
    destino = _so_digitos(params.get("To", ""))       # o número da empresa que recebeu
    sid = params.get("MessageSid") or params.get("SmsMessageSid")
    pool = get_pool()
    with pool.connection() as c:
        conta_id = _conta_por_ident(c, "whatsapp", destino)
        if not conta_id:
            return Response("<Response></Response>", media_type="application/xml")
        # --- RSVP de convite de reunião (botões do template) ---
        # O convite sai pelo número da empresa, então a resposta do convidado volta
        # PRA CÁ. Intercepta ANTES do inbox de prospecção, senão o "Confirmar"
        # viraria uma conversa de lead e o status do convite não mudaria.
        from finance import convites as _cv, whatsapp_out as _wout
        _txt = corpo or params.get("ButtonText") or params.get("ButtonPayload") or ""
        _st = _cv.rsvp_por_texto(_txt)
        if _st:
            _pend = _cv.pendentes_por_numero(pool, remetente)
            if _pend:
                _conv = _cv.responder(pool, _pend[0]["token"], _st)
                if _conv:
                    if _conv.get("mudou"):                             # só avisa se mudou
                        _cv.pos_resposta(pool, _conv)
                    _wout.enviar(c, conta_id, remetente, _cv.confirmacao_texto(_conv))
                    c.commit()
                    return Response("<Response></Response>", media_type="application/xml")
        # o agente assume conversa nova quando o master está ligado (o vendedor pode
        # "assumir" depois, o que desliga o bot só naquela conversa).
        master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                           (conta_id,)).fetchone()
        agente_on = bool(master and master[0])
        conv_id = _wa_inbound_conversa(c, conta_id, remetente, corpo, sid,
                                       params.get("ProfileName"), agente_on)
        c.commit()
    # deixa o agente atender em background (não segura a resposta pro Twilio)
    from finance import agente as _ag
    background_tasks.add_task(_ag.atender, get_pool(), conta_id, conv_id)
    return Response("<Response></Response>", media_type="application/xml")


def _descad_page(titulo: str, corpo: str) -> str:
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{titulo}</title><style>body{{margin:0;background:#0b0b0c;color:#e9eae6;"
        "font-family:system-ui,Arial,sans-serif;display:grid;place-items:center;min-height:100vh}"
        ".c{max-width:440px;background:#141416;border:1px solid #26262b;border-radius:14px;padding:1.6rem;margin:1rem}"
        "h1{font-size:1.2rem;margin:0 0 .6rem}p{color:#b9beb6;line-height:1.5}"
        ".b{background:#e0574f;border:0;color:#fff;border-radius:9px;padding:.6rem 1rem;font-size:.95rem;cursor:pointer;font-family:inherit}"
        "</style></head><body><div class='c'><h1>" + titulo + "</h1>" + corpo + "</div></body></html>")


@router.get("/descadastrar", response_class=HTMLResponse)
def descadastrar_form(request: Request, t: str = ""):
    from finance.campanhas_motor import descad_verify
    conta_id, email = descad_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Esse link de descadastro não é válido ou expirou.</p>"),
                            status_code=400)
    import html as _h
    return HTMLResponse(_descad_page(
        "Descadastrar",
        f"<p>Confirmar que <b>{_h.escape(email)}</b> não quer mais receber estes e-mails?</p>"
        f"<form method='post' action='/descadastrar'><input type='hidden' name='t' value='{_h.escape(t)}'>"
        "<button class='b'>Sim, quero descadastrar</button></form>"))


@router.post("/descadastrar", response_class=HTMLResponse)
def descadastrar_do(request: Request, t: str = Form("")):
    from finance.campanhas_motor import descad_verify, registrar_descadastro
    conta_id, email = descad_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Link inválido.</p>"), status_code=400)
    try:
        registrar_descadastro(get_pool(), conta_id, email, t)
    except Exception:  # noqa: BLE001
        pass
    import html as _h
    return HTMLResponse(_descad_page("Pronto ✓",
        f"<p>Feito! <b>{_h.escape(email)}</b> não vai mais receber nossos e-mails. Obrigado! 🙏</p>"))


@router.get("/tenho-interesse", response_class=HTMLResponse)
def tenho_interesse(request: Request, t: str = ""):
    """CTA da campanha: o lead clicou 'Tenho interesse'. Para a sequência, esquenta o
    lead, manda o material na hora e o agente IA assume. Página pública (sem login)."""
    from finance.campanhas_motor import interesse_verify, registrar_interesse
    conta_id, pid, camp_id = interesse_verify(t)
    if not conta_id:
        return HTMLResponse(_descad_page("Link inválido", "<p>Esse link não é válido ou expirou.</p>"),
                            status_code=400)
    try:
        registrar_interesse(get_pool(), conta_id, pid, camp_id)
    except Exception:  # noqa: BLE001
        pass
    return HTMLResponse(_descad_page("Recebemos! 🎉",
        "<p>Obrigado pelo interesse! Acabamos de te enviar um material por e-mail e "
        "já vamos falar com você. 😊</p>"))


def _conta_por_meta(c, plataforma, ident):
    """Roteia o inbound da Meta: acha a empresa dona da Página/IG que recebeu."""
    r = c.execute(
        "select conta_id from canais_config where canal=%s and ativo and identificador=%s limit 1",
        (plataforma, str(ident))).fetchone()
    return r[0] if r else None


def _conversa_meta(c, conta_id, plataforma, sender):
    """Acha/cria a conversa (órfã, sem lead) daquele remetente Messenger/Instagram."""
    conv = c.execute(
        """select id from conversas where conta_id=%s and canal=%s and prospeccao_id is null
            and contato_ref=%s order by ultima_msg_em desc limit 1""",
        (conta_id, plataforma, str(sender))).fetchone()
    if conv:
        return conv[0]
    return c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, ultima_msg_em)
           values (%s,null,%s,%s,'aberta',now()) returning id""",
        (conta_id, plataforma, str(sender))).fetchone()[0]


@router.get("/webhooks/meta")
def webhook_meta_verify(request: Request):
    """Verificação do webhook da Meta (GET com hub.challenge)."""
    from finance import meta_msg
    q = request.query_params
    ch = meta_msg.verificar_challenge(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    if ch is None:
        return Response(status_code=403)
    return Response(ch, media_type="text/plain")


@router.post("/webhooks/meta")
async def webhook_meta(request: Request, background_tasks: BackgroundTasks):
    """Recebe mensagens de Messenger + Instagram (Meta). Valida a assinatura, roteia
    pela Página/IG que recebeu, grava como conversa órfã (você decide se vira lead) e
    dispara o agente."""
    from finance import meta_msg
    body = await request.body()
    if not meta_msg.validar_assinatura(body, request.headers.get("x-hub-signature-256", "")):
        return Response(status_code=403)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    eventos = meta_msg.parse_eventos(payload)
    pool = get_pool()
    disparar = []
    with pool.connection() as c:
        for ev in eventos:
            if ev["plataforma"] == "whatsapp":
                # WhatsApp Cloud API (número próprio): roteia pelo phone_number_id
                r = c.execute("""select conta_id from canais_config
                                  where canal='whatsapp' and ativo and wa_phone_id=%s limit 1""",
                              (ev["conta_ident"],)).fetchone()
                conta_id = r[0] if r else None
                if not conta_id:
                    continue
                m = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                              (conta_id,)).fetchone()
                agente_on = bool(m and m[0])
                conv_id = _wa_inbound_conversa(c, conta_id, ev["sender"], ev["texto"],
                                               ev.get("sid"), ev.get("nome"), agente_on)
                if agente_on:
                    disparar.append((conta_id, conv_id))
                continue
            conta_id = _conta_por_meta(c, ev["plataforma"], ev["conta_ident"])
            if not conta_id:
                continue
            conv_id = _conversa_meta(c, conta_id, ev["plataforma"], ev["sender"])
            _add_msg(c, conv_id, ev["plataforma"], "in", "lead", ev["texto"])
            master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                               (conta_id,)).fetchone()
            if master and master[0]:
                c.execute("update conversas set agente_ativo=true where id=%s and status<>'pendente'",
                          (conv_id,))
                disparar.append((conta_id, conv_id))
        c.commit()
    from finance import agente as _ag
    for (cid, cvid) in disparar:
        background_tasks.add_task(_ag.atender, get_pool(), cid, cvid)
    return Response("ok", media_type="text/plain")


@router.post("/webhooks/wa-qr")
async def webhook_wa_qr(request: Request, background_tasks: BackgroundTasks):
    """Entrada do WhatsApp por QR (serviço Node services/wa-qr). Autentica pelo
    segredo compartilhado, roteia pela conta_id que o serviço já resolveu e trata
    igual aos outros canais (lead + agente)."""
    segredo = os.environ.get("WA_QR_SHARED_SECRET") or ""
    if not segredo or request.headers.get("x-wa-secret") != segredo:
        return Response(status_code=403)
    try:
        payload = json.loads((await request.body()).decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return Response("ok", media_type="text/plain")
    try:
        conta_id = int(payload.get("conta_id") or 0)
    except (TypeError, ValueError):
        conta_id = 0
    sender = str(payload.get("sender") or "").strip()
    texto = (payload.get("texto") or "").strip()
    if not conta_id or not sender or not texto:
        return Response("ok", media_type="text/plain")
    pool = get_pool()
    with pool.connection() as c:
        # confere que a empresa está mesmo no modo QR (evita conta_id forjado tocar outra via)
        dono = c.execute("""select 1 from canais_config
                              where conta_id=%s and canal='whatsapp' and ativo and coalesce(provedor,'twilio')='qr'""",
                         (conta_id,)).fetchone()
        if not dono:
            return Response("ok", media_type="text/plain")
        m = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                      (conta_id,)).fetchone()
        agente_on = bool(m and m[0])
        conv_id = _wa_inbound_conversa(c, conta_id, sender, texto,
                                       payload.get("id") or None, payload.get("nome"), agente_on)
        c.commit()
    if agente_on:
        from finance import agente as _ag
        background_tasks.add_task(_ag.atender, get_pool(), conta_id, conv_id)
    return Response("ok", media_type="text/plain")


# ================================================================ CAMPANHAS (Fase 2)
# (definido ANTES de /{alvo_id} pra "campanhas" não cair na rota da ficha)
_PASSOS_PADRAO = [
    (0, 0, "Uma ideia pra {empresa}", "", True),                 # D0: o agente escreve
    (1, 3, "Só reforçando — vale 2 min?",
     "Oi, {empresa}!\n\nSemana passada te mandei uma ideia rápida pra atender melhor sem "
     "aumentar a equipe. Vale 2 minutinhos essa semana pra eu te mostrar?", False),
    (2, 7, "Fecho por aqui 👋",
     "Oi, {empresa}!\n\nÉ a última vez que passo por aqui 😊 Se fizer sentido, me chama no "
     "WhatsApp quando quiser — fica à vontade.", False),
]
_STATUS_ROT_CP = {"rascunho": "✎ Rascunho", "ativa": "● Ativa",
                  "pausada": "❚❚ Pausada", "concluida": "✓ Concluída"}
_ALVO_ROT = {"fila": "Na fila", "enviado": "Em sequência", "respondeu": "Respondeu ✓",
             "descadastrou": "Descadastrou", "erro": "Erro", "concluido": "Concluído"}


def _campanha_publico_where(conta_id, camp_id, seg, cidade, temp):
    """WHERE dos leads elegíveis: e-mail válido, fora da campanha e não descadastrados."""
    where = ["p.conta_id=%s", "p.email_ok",
             "p.id not in (select prospeccao_id from campanha_alvos where campanha_id=%s)",
             "lower(coalesce(p.email,'')) not in (select lower(email) from descadastros where conta_id=%s)"]
    params = [conta_id, camp_id, conta_id]
    if (seg or "").strip():
        where.append("p.segmento ilike %s"); params.append("%" + seg.strip() + "%")
    if (cidade or "").strip():
        where.append("p.cidade ilike %s"); params.append("%" + cidade.strip() + "%")
    if temp in ("frio", "morno", "quente"):
        where.append("p.temperatura=%s"); params.append(temp)
    return " and ".join(where), params


@router.get("/painel/prospeccao/campanhas", response_class=HTMLResponse)
def prospeccao_campanhas(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        request.session["prosp_aviso"] = "Só o dono/gestor gerencia campanhas."
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with get_pool().connection() as c:
        rows = c.execute(
            """select cp.id, cp.nome, cp.status, cp.limite_dia,
                 (select count(*) from campanha_alvos a where a.campanha_id=cp.id),
                 (select count(*) from campanha_alvos a where a.campanha_id=cp.id and a.status='enviado'),
                 (select count(*) from campanha_alvos a where a.campanha_id=cp.id and a.status='respondeu')
               from campanhas cp where cp.conta_id=%s order by cp.criado_em desc""",
            (ctx["conta_id"],)).fetchall()
        eleg = c.execute("select count(*) from prospeccao where conta_id=%s and email_ok",
                         (ctx["conta_id"],)).fetchone()[0]
    camps = [{"id": r[0], "nome": r[1], "status": r[2], "limite": r[3],
              "n": r[4], "env": r[5], "resp": r[6], "status_rot": _STATUS_ROT_CP.get(r[2], r[2])}
             for r in rows]
    return _render("prospeccao_campanhas", request, titulo="Campanhas", secao_ativa="prospeccao",
                   camps=camps, elegiveis=eleg, aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/campanhas/nova")
def prospeccao_campanha_nova(request: Request, nome: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    nome = (nome or "").strip() or "Nova campanha"
    with get_pool().connection() as c:
        cid = c.execute("insert into campanhas (conta_id, nome, criado_por) values (%s,%s,%s) returning id",
                        (ctx["conta_id"], nome[:120], ctx["membro_id"])).fetchone()[0]
        for (ordem, dias, assunto, corpo, ia) in _PASSOS_PADRAO:
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,%s,%s,%s,%s,%s)""", (cid, ordem, dias, assunto, corpo, ia))
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/campanhas/{cid}", status_code=303)


@router.get("/painel/prospeccao/campanhas/{camp_id}", response_class=HTMLResponse)
def prospeccao_campanha_det(request: Request, camp_id: int, seg: str = "", cidade: str = "", temp: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        cp = c.execute("select id, nome, status, limite_dia, coalesce(enviados_hoje,0), dia_contagem, coalesce(material,'') from campanhas where id=%s and conta_id=%s",
                       (camp_id, ctx["conta_id"])).fetchone()
        if not cp:
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        passos = c.execute("""select ordem, dias_apos, assunto, corpo, usar_ia
                                from campanha_passos where campanha_id=%s order by ordem""",
                           (camp_id,)).fetchall()
        st = dict(c.execute("select status, count(*) from campanha_alvos where campanha_id=%s group by status",
                            (camp_id,)).fetchall())
        na_camp = c.execute("select count(*) from campanha_alvos where campanha_id=%s", (camp_id,)).fetchone()[0]
        enviados = c.execute("select count(*) from campanha_alvos where campanha_id=%s and ultima_msg_em is not null",
                             (camp_id,)).fetchone()[0]
        leads = c.execute(
            """select p.empresa, p.email, a.status, a.passo_atual,
                      to_char(a.proximo_envio_em - interval '3 hours','DD/MM HH24:MI'),
                      to_char(a.ultima_msg_em - interval '3 hours','DD/MM HH24:MI')
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.ultima_msg_em desc nulls last, a.id desc limit 200""",
            (camp_id,)).fetchall()
        wsql, wparams = _campanha_publico_where(ctx["conta_id"], camp_id, seg, cidade, temp)
        eleg = c.execute(f"select count(*) from prospeccao p where {wsql}", tuple(wparams)).fetchone()[0]
        sample = c.execute(
            """select p.empresa, p.segmento, p.cidade, p.uf, p.whatsapp, p.email
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.id limit 1""", (camp_id,)).fetchone()
        if not sample:
            sample = c.execute(
                """select empresa, segmento, cidade, uf, whatsapp, email from prospeccao
                    where conta_id=%s and email_ok order by atualizado_em desc limit 1""",
                (ctx["conta_id"],)).fetchone()
    resp = st.get("respondeu", 0)
    from datetime import date as _date
    hoje = cp[4] if cp[5] == _date.today() else 0
    camp = {"id": cp[0], "nome": cp[1], "status": cp[2], "limite": cp[3],
            "status_rot": _STATUS_ROT_CP.get(cp[2], cp[2]), "material": cp[6]}
    metr = {"total": na_camp, "fila": st.get("fila", 0), "enviados": enviados, "responderam": resp,
            "descadastros": st.get("descadastrou", 0), "erros": st.get("erro", 0),
            "concluidos": st.get("concluido", 0), "hoje": hoje,
            "taxa": (round(100 * resp / enviados) if enviados else 0)}
    passos_l = [{"dias": p[1], "assunto": p[2], "corpo": p[3], "ia": p[4]} for p in passos]
    from finance.campanhas_motor import _fmt as _cfmt
    cadencia = " · ".join("D" + str(p["dias"]) for p in passos_l) or "—"
    previa = None
    if passos_l and sample:
        _ld = {"empresa": sample[0], "segmento": sample[1], "cidade": sample[2], "uf": sample[3],
               "whatsapp": sample[4], "email": sample[5]}
        p0 = passos_l[0]
        previa = {"ia": bool(p0["ia"]), "empresa": _ld["empresa"], "email": _ld["email"],
                  "whatsapp": _ld["whatsapp"],
                  "assunto": _cfmt(p0["assunto"] or "Uma ideia pra {empresa}", _ld),
                  "corpo": ("O agente escreve um e-mail único pra este lead — clique em “Gerar prévia com IA” "
                            "pra ver um exemplo." if p0["ia"] else _cfmt(p0["corpo"] or "", _ld))}
    leads_l = [{"empresa": r[0], "email": r[1], "status": r[2], "passo": r[3],
                "prox": r[4], "ult": r[5], "rot": _ALVO_ROT.get(r[2], r[2])} for r in leads]
    return _render("prospeccao_campanha", request, titulo=camp["nome"], secao_ativa="prospeccao",
                   camp=camp, passos=passos_l, elegiveis=eleg, na_camp=na_camp, st=st, metr=metr,
                   leads=leads_l, previa=previa, cadencia=cadencia, remetente=remetente_configurado(),
                   seg=seg, cidade=cidade, temp=temp, aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/campanhas/{camp_id}/publico")
def prospeccao_campanha_publico(request: Request, camp_id: int, seg: str = Form(""),
                                cidade: str = Form(""), temp: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        if not c.execute("select 1 from campanhas where id=%s and conta_id=%s",
                         (camp_id, ctx["conta_id"])).fetchone():
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        wsql, wparams = _campanha_publico_where(ctx["conta_id"], camp_id, seg, cidade, temp)
        n = c.execute(
            f"""insert into campanha_alvos (campanha_id, prospeccao_id)
                select %s, p.id from prospeccao p where {wsql} on conflict do nothing""",
            (camp_id, *wparams)).rowcount
        c.commit()
    request.session["prosp_aviso"] = f"{n} lead(s) adicionado(s) à campanha ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


def _campanha_dona(c, conta_id, camp_id):
    return c.execute("select 1 from campanhas where id=%s and conta_id=%s", (camp_id, conta_id)).fetchone()


@router.post("/painel/prospeccao/campanhas/{camp_id}/config")
def prospeccao_campanha_config(request: Request, camp_id: int, nome: str = Form(""),
                               limite_dia: str = Form("40"), material: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    try:
        lim = max(1, min(500, int(limite_dia)))
    except (ValueError, TypeError):
        lim = 40
    with get_pool().connection() as c:
        c.execute("""update campanhas set nome=coalesce(nullif(%s,''),nome), limite_dia=%s,
                       material=%s, atualizado_em=now() where id=%s and conta_id=%s""",
                  ((nome or "").strip()[:120], lim, (material or "").strip()[:2000],
                   camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Configuração salva ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/status")
def prospeccao_campanha_status(request: Request, camp_id: int, status: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"] or status not in ("rascunho", "ativa", "pausada", "concluida"):
        return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)
    with get_pool().connection() as c:
        c.execute("update campanhas set status=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (status, camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = {"ativa": "Campanha ativada ✓ (o disparo entra na próxima etapa)",
                                      "pausada": "Campanha pausada.",
                                      "rascunho": "Voltou pra rascunho."}.get(status, "Status atualizado.")
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/sequencia")
def prospeccao_campanha_sequencia(request: Request, camp_id: int,
                                  dias: list[str] = Form([]), assunto: list[str] = Form([]),
                                  corpo: list[str] = Form([]), usar_ia: list[str] = Form([])):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
        c.execute("delete from campanha_passos where campanha_id=%s", (camp_id,))
        ordem = 0
        for i in range(len(dias)):
            try:
                d = max(0, min(120, int(dias[i])))
            except (ValueError, TypeError):
                d = 0
            a = (assunto[i] if i < len(assunto) else "").strip()[:300]
            co = (corpo[i] if i < len(corpo) else "").strip()[:8000]
            ia = (usar_ia[i] if i < len(usar_ia) else "0") == "1"
            if not a and not co and not ia:
                continue                      # passo vazio → ignora
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,%s,%s,%s,%s,%s)""", (camp_id, ordem, d, a, co, ia))
            ordem += 1
        if ordem == 0:                         # garante ao menos 1 passo
            c.execute("""insert into campanha_passos (campanha_id, ordem, dias_apos, assunto, corpo, usar_ia)
                         values (%s,0,0,%s,'',true)""", (camp_id, "Uma ideia pra {empresa}"))
        c.commit()
    request.session["prosp_aviso"] = "Sequência salva ✓"
    return RedirectResponse(f"/painel/prospeccao/campanhas/{camp_id}", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/excluir")
def prospeccao_campanha_excluir(request: Request, camp_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)
    with get_pool().connection() as c:
        c.execute("delete from campanhas where id=%s and conta_id=%s", (camp_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Campanha excluída."
    return RedirectResponse("/painel/prospeccao/campanhas", status_code=303)


@router.post("/painel/prospeccao/campanhas/{camp_id}/previa-ia")
def prospeccao_campanha_previa_ia(request: Request, camp_id: int):
    """Gera uma prévia real do e-mail que o agente escreveria (1 lead de exemplo)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not ctx["gerencia"]:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    pool = get_pool()
    with pool.connection() as c:
        if not _campanha_dona(c, ctx["conta_id"], camp_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        s = c.execute(
            """select p.empresa, p.segmento, p.cidade, p.uf, p.whatsapp, p.email
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s order by a.id limit 1""", (camp_id,)).fetchone()
        if not s:
            s = c.execute("""select empresa,segmento,cidade,uf,whatsapp,email from prospeccao
                              where conta_id=%s and email_ok order by atualizado_em desc limit 1""",
                          (ctx["conta_id"],)).fetchone()
    if not s:
        return JSONResponse({"ok": False, "erro": "Adicione leads (com e-mail) primeiro."})
    lead = {"empresa": s[0], "segmento": s[1], "cidade": s[2], "uf": s[3], "whatsapp": s[4], "email": s[5]}
    try:
        from finance.campanhas_motor import _email_ia
        m = _email_ia(pool, ctx["conta_id"], lead)
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Não consegui gerar a prévia."})
    return JSONResponse({"ok": True, "empresa": lead["empresa"], "assunto": m["assunto"], "corpo": m["corpo"]})


# ================================================================ FICHA DO ALVO
@router.get("/painel/prospeccao/{alvo_id}", response_class=HTMLResponse)
def prospeccao_ficha(request: Request, alvo_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with pool.connection() as c:
        ativs = c.execute(
            """select a.tipo, a.resultado, a.descricao, a.agendado_para, a.criado_em, m.nome
                 from prospeccao_atividades a
                 left join membros m on m.id = a.membro_id
                where a.prospeccao_id=%s order by a.criado_em desc""", (alvo_id,)).fetchall()
    timeline = []
    for (t, rr, d, ag, cr, nome) in ativs:
        cor = "#3ee0a6" if rr in _RES_VERDE else "#e0a33e" if rr in _RES_AMBAR else "#7a7a7a"
        timeline.append({"tipo_rot": TIPO_ROT.get(t, t), "resultado_rot": RESULTADO_ROT.get(rr or "", ""),
                         "descricao": d, "agendado_para": ag, "criado_em": cr, "quem": nome, "cor": cor})
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_ficha", request, titulo=alvo["empresa"], secao_ativa="prospeccao",
                   a=alvo, timeline=timeline, status=STATUS, temperaturas=TEMPERATURAS,
                   tipos=TIPOS, resultados=RESULTADOS, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
                   gerencia=ctx["gerencia"], pode_atribuir=ctx["pode_atribuir"], vendedores=vends,
                   tem_cnpja=fontes.tem_chave_cnpja(), tem_ia=_tem_ia(),
                   tem_credify=_tem_credify(),
                   embed=request.query_params.get("embed") == "1",
                   aviso=request.session.pop("prosp_aviso", None))


@router.post("/painel/prospeccao/{alvo_id}/editar")
def prospeccao_editar(request: Request, alvo_id: int, contato: str = Form(""),
                      cargo: str = Form(""), telefone: str = Form(""), whatsapp: str = Form(""),
                      email: str = Form(""), cnpj: str = Form(""), segmento: str = Form(""),
                      cidade: str = Form(""), uf: str = Form(""), valor: str = Form(""),
                      socio: str = Form(""), regime_tributario: str = Form(""),
                      porte: str = Form(""), instagram: str = Form(""),
                      tem_site: str = Form(""), site_url: str = Form(""), obs: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    site_link = (site_url or "").strip()
    if site_link and "://" not in site_link:
        site_link = "https://" + site_link
    site_link = site_link or None
    # se preencheu o link, o lead tem site (mesmo que não tenha marcado o rádio)
    site = True if (tem_site == "1" or site_link) else False if tem_site == "0" else None
    with pool.connection() as c:
        c.execute(
            """update prospeccao set contato=%s, cargo=%s, telefone=%s, whatsapp=%s,
                   email=%s, cnpj=%s, segmento=%s, cidade=%s, uf=%s,
                   valor_estimado_centavos=%s, socio=%s, regime_tributario=%s, porte=%s,
                   instagram=%s, tem_site=%s, site_url=%s, obs=%s, atualizado_em=now()
                 where id=%s and conta_id=%s""",
            (contato.strip() or None, cargo.strip() or None, telefone.strip() or None,
             whatsapp.strip() or None, email.strip().lower() or None, cnpj.strip() or None,
             segmento.strip() or None, cidade.strip() or None, (uf or "").strip()[:2].upper() or None,
             _reais_para_centavos(valor), socio.strip() or None, regime_tributario.strip() or None,
             porte.strip() or None, instagram.strip() or None, site, site_link, obs.strip() or None,
             alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Dados atualizados."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/enriquecer")
def prospeccao_enriquecer(request: Request, alvo_id: int):
    """Puxa sócio/regime/porte do CNPJ na BrasilAPI (grátis). Só preenche campos vazios."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if not alvo["cnpj"]:
        request.session["prosp_aviso"] = "Sem CNPJ pra consultar. Preencha o CNPJ e tente de novo."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = fontes.enriquecer_cnpj(alvo["cnpj"])
    if not res.get("ok"):
        erros = {"cnpj_invalido": "CNPJ inválido.", "rede": "Não consegui falar com a Receita agora.",
                 "http_404": "CNPJ não encontrado na base."}
        request.session["prosp_aviso"] = erros.get(res.get("erro"), "Não consegui enriquecer agora.")
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    d = res["dados"]
    # só preenche o que estiver vazio (não sobrescreve o que o vendedor já pôs);
    # o pacote rico (situação, abertura, capital, endereço, IE…) vai no jsonb receita.
    with pool.connection() as c:
        c.execute(
            """update prospeccao set
                   socio=coalesce(socio,%s), regime_tributario=coalesce(regime_tributario,%s),
                   porte=coalesce(porte,%s), telefone=coalesce(telefone,%s),
                   email=coalesce(email,%s), segmento=coalesce(segmento,%s),
                   cidade=coalesce(cidade,%s), uf=coalesce(uf,%s),
                   receita=%s::jsonb, atualizado_em=now()
                 where id=%s and conta_id=%s""",
            (d.get("socio"), d.get("regime_tributario"), d.get("porte"), d.get("telefone"),
             d.get("email"), d.get("segmento"), d.get("cidade"), d.get("uf"),
             json.dumps(_receita_extras(d)), alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Dados da Receita preenchidos ✓"
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/buscar-cnpj")
def prospeccao_buscar_cnpj(request: Request, alvo_id: int):
    """Acha CNPJs pelo nome+cidade do lead (CNPJá). Devolve candidatos pra escolher."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    res = fontes.buscar_cnpj_por_nome(alvo["empresa"], alvo["cidade"] or "", alvo["uf"] or "")
    from urllib.parse import quote
    termo = " ".join(x for x in (alvo["empresa"], alvo["cidade"], "cnpj") if x)
    res["web"] = "https://www.google.com/search?q=" + quote(termo)
    return JSONResponse(res)


@router.post("/painel/prospeccao/{alvo_id}/aplicar-cnpj")
def prospeccao_aplicar_cnpj(request: Request, alvo_id: int, cnpj: str = Form(...)):
    """Grava o CNPJ escolhido e já enriquece (colunas + jsonb receita)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    limpo = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(limpo) != 14:
        request.session["prosp_aviso"] = "CNPJ inválido."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = fontes.enriquecer_cnpj(limpo)
    with pool.connection() as c:
        if res.get("ok"):
            d = res["dados"]
            # identidade (sócio/regime/porte/segmento) + receita: SOBRESCREVE — assim
            # "trocar CNPJ" corrige de fato. Contato/local: coalesce (Google costuma
            # ser o certo do lead).
            c.execute(
                """update prospeccao set cnpj=%s,
                       socio=%s, regime_tributario=%s, porte=%s, segmento=%s,
                       telefone=coalesce(telefone,%s), email=coalesce(email,%s),
                       cidade=coalesce(cidade,%s), uf=coalesce(uf,%s),
                       receita=%s::jsonb, atualizado_em=now()
                     where id=%s and conta_id=%s""",
                (cnpj.strip(), d.get("socio"), d.get("regime_tributario"), d.get("porte"),
                 d.get("segmento"), d.get("telefone"), d.get("email"), d.get("cidade"),
                 d.get("uf"), json.dumps(_receita_extras(d)), alvo_id, ctx["conta_id"]))
            request.session["prosp_aviso"] = "CNPJ vinculado e dados da Receita preenchidos ✓"
        else:
            c.execute("update prospeccao set cnpj=%s, atualizado_em=now() where id=%s and conta_id=%s",
                      (cnpj.strip(), alvo_id, ctx["conta_id"]))
            request.session["prosp_aviso"] = "CNPJ salvo (não consegui enriquecer agora — use ↻)."
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/limpar-cnpj")
def prospeccao_limpar_cnpj(request: Request, alvo_id: int):
    """Desfaz um CNPJ escolhido errado: zera cnpj + receita + identidade (sócio/
    regime/porte). Contato/telefone/cidade/uf ficam (dados do lead)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with pool.connection() as c:
        c.execute(
            """update prospeccao set cnpj=null, receita=null, socio=null,
                   regime_tributario=null, porte=null, atualizado_em=now()
                 where id=%s and conta_id=%s""", (alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "CNPJ removido. Busque de novo se quiser."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/contato")
def prospeccao_contato(request: Request, alvo_id: int, tipo: str = Form(...),
                       resultado: str = Form(""), descricao: str = Form(""),
                       proximo: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if tipo not in TIPO_OK:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    res = resultado if resultado in RESULTADO_OK else None
    prox = _data_para_ts(proximo)
    with pool.connection() as c:
        c.execute(
            """insert into prospeccao_atividades (prospeccao_id, membro_id, tipo,
                 resultado, descricao, agendado_para) values (%s,%s,%s,%s,%s,%s)""",
            (alvo_id, ctx["membro_id"], tipo, res, (descricao or "").strip(), prox))
        novo_status = "contatado" if alvo["status"] == "novo" else alvo["status"]
        c.execute(
            """update prospeccao set ultimo_contato_em=now(),
                   proximo_contato_em=coalesce(%s, proximo_contato_em), status=%s,
                   atualizado_em=now() where id=%s and conta_id=%s""",
            (prox, novo_status, alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Contato registrado."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/temperatura")
def prospeccao_temperatura(request: Request, alvo_id: int, temperatura: str = Form(...)):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if temperatura not in TEMP_OK:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    with pool.connection() as c:
        c.execute("update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (temperatura, alvo_id, ctx["conta_id"]))
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/atribuir")
def prospeccao_atribuir(request: Request, alvo_id: int, vendedor_id: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["pode_atribuir"]:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    vend = _vendedor_destino(ctx, vendedor_id, get_pool(), ctx["conta_id"])
    with get_pool().connection() as c:
        c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (vend, alvo_id, ctx["conta_id"]))
        c.commit()
    request.session["prosp_aviso"] = "Alvo atribuído." if vend else "Alvo sem responsável."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- gerar orçamento (Etapa 4)
@router.post("/painel/prospeccao/{alvo_id}/orcamento")
def prospeccao_orcamento(request: Request, alvo_id: int):
    """Converte o lead num orçamento (módulo Serviços) e leva o vendedor pra editar.
    Reaproveita a tabela orcamentos: cliente/empresa são texto, não precisa cadastrar
    cliente antes. Grava o vínculo em prospeccao.orcamento_id e avança pra 'proposta'."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    if not ctx["conta"][14]:  # vende_servico — orçamento é do módulo Serviços
        request.session["prosp_aviso"] = "Orçamento é do módulo Serviços — ative Serviços na empresa pra usar."
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    oid = alvo.get("orcamento_id")
    if not oid:
        from web.painel_servicos import _garantir_tabela
        criador = str(ctx["membro_id"]) if ctx["membro_id"] else "dono"
        with pool.connection() as c:
            _garantir_tabela(c)
            row = c.execute(
                """insert into orcamentos (conta_id, cliente, empresa, cnpj, segmento,
                     whatsapp, email, telefone, cidade, uf, site, cargo, socio,
                     criado_por, token, status)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'rascunho') returning id""",
                (ctx["conta_id"], alvo["contato"], alvo["empresa"], alvo["cnpj"], alvo["segmento"],
                 alvo["whatsapp"] or alvo["telefone"], alvo["email"], alvo["telefone"],
                 alvo["cidade"], alvo["uf"], alvo["site_url"], alvo["cargo"], alvo["socio"],
                 criador, secrets.token_urlsafe(16))).fetchone()
            oid = row[0]
            novo_status = alvo["status"] if alvo["status"] in ("ganho", "perdido") else "proposta"
            c.execute("update prospeccao set orcamento_id=%s, status=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (oid, novo_status, alvo_id, ctx["conta_id"]))
            c.commit()
    return RedirectResponse(f"/painel/servicos?abrir={oid}", status_code=303)


@router.post("/painel/prospeccao/{alvo_id}/status")
async def prospeccao_status(request: Request, alvo_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    form = await request.form()
    status = (form.get("status") or request.query_params.get("status") or "").strip()
    if status not in STATUS_OK:
        return JSONResponse({"ok": False, "erro": "status"}, status_code=400)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    with pool.connection() as c:
        c.execute("update prospeccao set status=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (status, alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "status": status})


# ================================================================ 1º CONTATO (IA)
def _ctx_servicos(pool, conta_id: int) -> str:
    """Catálogo de serviços da empresa, pra a IA saber o que oferecer."""
    try:
        itens = scat.listar(pool, conta_id)
    except Exception:  # noqa: BLE001
        itens = []
    return "\n".join(f"- {s.get('nome','')}: {s.get('descricao','') or ''}".strip()
                     for s in (itens or [])[:20])


def _draft_ia(pool, conta, alvo: dict, canal: str, remetente: str) -> dict:
    """Gera o rascunho da 1ª abordagem via Claude. canal: 'email' | 'whatsapp'.
    Retorna {'assunto','corpo'} (email) ou {'texto'} (whatsapp), ou {'erro'}."""
    from core.brain import Brain
    empresa_nome = (conta[2] or "nossa empresa").strip()
    catalogo = _ctx_servicos(pool, conta[0])
    site = alvo.get("site_url") or ""
    lead = "\n".join(x for x in [
        f"Empresa: {alvo.get('empresa') or ''}",
        f"Contato: {alvo.get('contato') or ''}" if alvo.get("contato") else "",
        f"Segmento: {alvo.get('segmento') or ''}" if alvo.get("segmento") else "",
        f"Cidade: {alvo.get('cidade') or ''}{('/' + alvo['uf']) if alvo.get('uf') else ''}" if alvo.get("cidade") else "",
        f"Site: {site}" if site else "",
    ] if x)
    if canal == "whatsapp":
        formato = '{"texto":"..."}'
        instr = ("uma mensagem de WhatsApp de PRIMEIRO contato: bem curta (3 a 5 linhas), "
                 "informal e humana, 1 pergunta/CTA leve pra puxar conversa, sem links longos, "
                 "pode usar no máximo 1 emoji.")
    else:
        formato = '{"assunto":"...","corpo":"..."}'
        instr = ("um e-mail de PRIMEIRO contato: assunto curto e o corpo com 5 a 8 linhas, "
                 "tom consultivo (não spam), conecte com o segmento/realidade do lead, 1 CTA "
                 "(sugerir uma conversa rápida). Assine como " + remetente + " (" + empresa_nome + ").")
    system = ("Você é um SDR de pré-vendas experiente. Escreve abordagens de prospecção "
              "que soam humanas e relevantes, em português do Brasil. NUNCA invente dados "
              "(preço, números, promessas). Responda SEMPRE só com JSON válido, sem markdown.")
    prompt = (f"MINHA EMPRESA: {empresa_nome}\n"
              f"SERVIÇOS QUE OFEREÇO:\n{catalogo or '(catálogo não informado)'}\n\n"
              f"LEAD QUE VOU ABORDAR:\n{lead}\n\n"
              f"Tarefa: escreva {instr}\n"
              f"Responda APENAS com JSON: {formato}")
    try:
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": prompt}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        txt = re.sub(r"^```json|^```|```$", "", txt).strip()
        return json.loads(txt)
    except Exception as e:  # noqa: BLE001
        return {"erro": str(e)[:120]}


def _conversa_id(c, conta_id, alvo_id, canal):
    """Acha (ou cria) a conversa daquele lead+canal e devolve o id — base do inbox omnichannel."""
    r = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal=%s",
                  (conta_id, alvo_id, canal)).fetchone()
    if r:
        return r[0]
    r = c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
           values (%s,%s,%s,'aberta',now()) returning id""",
        (conta_id, alvo_id, canal)).fetchone()
    return r[0]


def _add_msg(c, conversa_id, canal, direcao, autor, texto, membro_id=None, provider_sid=None):
    """Grava uma mensagem numa conversa JÁ conhecida (não re-deriva por lead) e
    atualiza o topo. Use quando já se tem o conversa_id (ex: responder no inbox)."""
    c.execute(
        """insert into mensagens (conversa_id, canal, direcao, autor, membro_id, texto, provider_sid)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (conversa_id, canal, direcao, autor, membro_id, (texto or "")[:8000], provider_sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))
    return conversa_id


def _registrar_msg(c, conta_id, alvo_id, canal, direcao, autor, texto, membro_id=None, provider_sid=None):
    """Grava uma mensagem na conversa DO LEAD (cria a conversa se preciso). Pra
    conversa sem lead, use _add_msg com o conversa_id."""
    conv = _conversa_id(c, conta_id, alvo_id, canal)
    return _add_msg(c, conv, canal, direcao, autor, texto, membro_id, provider_sid)


def _reg_atividade(c, alvo_id, conta_id, membro_id, tipo, descricao, status_atual):
    """Registra a abordagem na timeline, no inbox (conversas/mensagens) e avança 'novo'→'contatado'."""
    c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo,
                   resultado, descricao) values (%s,%s,%s,%s,%s)""",
              (alvo_id, membro_id, tipo, None, (descricao or "")[:4000]))
    if tipo in ("email", "whatsapp"):
        _registrar_msg(c, conta_id, alvo_id, tipo, "out", "humano", descricao, membro_id)
    novo = "contatado" if status_atual == "novo" else status_atual
    c.execute("""update prospeccao set ultimo_contato_em=now(), status=%s,
                   atualizado_em=now() where id=%s and conta_id=%s""",
              (novo, alvo_id, conta_id))


@router.post("/painel/prospeccao/{alvo_id}/mensagem-ia")
def prospeccao_mensagem_ia(request: Request, alvo_id: int, canal: str = Form("email")):
    """Gera o rascunho da 1ª abordagem (e-mail ou WhatsApp) via IA — não envia."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if not _tem_ia():
        return JSONResponse({"ok": False, "erro": "sem_ia"})
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    canal = "whatsapp" if canal == "whatsapp" else "email"
    nome_rem, _email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    d = _draft_ia(pool, ctx["conta"], alvo, canal, nome_rem or "Equipe")
    if d.get("erro"):
        return JSONResponse({"ok": False, "erro": d["erro"]})
    if canal == "whatsapp":
        texto = (d.get("texto") or "").strip()
        return JSONResponse({"ok": True, "canal": "whatsapp", "texto": texto,
                             "link": _zap_link_texto(alvo["whatsapp"] or alvo["telefone"], texto)})
    return JSONResponse({"ok": True, "canal": "email",
                         "assunto": (d.get("assunto") or "").strip(),
                         "corpo": (d.get("corpo") or "").strip()})


@router.post("/painel/prospeccao/{alvo_id}/enviar-email")
def prospeccao_enviar_email(request: Request, alvo_id: int, assunto: str = Form(...),
                            corpo: str = Form(...)):
    """Envia o e-mail (revisado pelo vendedor) pro lead e registra na timeline."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    destino = (alvo.get("email") or "").strip()
    if not destino:
        return JSONResponse({"ok": False, "erro": "sem_email"})
    assunto = (assunto or "").strip() or f"Contato · {ctx['conta'][2] or ''}".strip()
    corpo = (corpo or "").strip()
    if not corpo:
        return JSONResponse({"ok": False, "erro": "sem_corpo"})
    nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    html = "<div style=\"font-family:system-ui,Arial,sans-serif;font-size:15px;line-height:1.6;color:#222\">" \
           + "".join(f"<p style=\"margin:0 0 12px\">{_html_escape(par)}</p>"
                     for par in corpo.split("\n\n")) + "</div>"
    ok = enviar_email(destino, assunto, html, texto_alt=corpo,
                      reply_to=email_rem or None, from_nome=(ctx["conta"][2] or None))
    if not ok:
        return JSONResponse({"ok": False, "erro": "envio_falhou"})
    remetente = remetente_configurado() or ""
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "email",
                       f"De {remetente} · Para {destino} · {assunto}\n\n{corpo}", alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/excluir")
def prospeccao_excluir(request: Request, alvo_id: int):
    """Exclui um lead. A conversa/e-mail dele vira órfã (FK SET NULL) e continua no
    inbox — nada de e-mail perdido; só sai do funil."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    with pool.connection() as c:
        c.execute("delete from prospeccao where id=%s and conta_id=%s", (alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- enriquecimento de canais

def _enriquecer_lead(pool, conta_id, alvo_id) -> dict:
    """Raspa o site do lead → e-mail/Instagram/WhatsApp; preenche só o que está vazio
    (não sobrescreve o que o usuário já tem) e marca email_ok/enriquecido_em."""
    from finance import enriquecimento as enr
    with pool.connection() as c:
        r = c.execute(
            "select site_url, telefone, email, whatsapp, instagram from prospeccao where id=%s and conta_id=%s",
            (alvo_id, conta_id)).fetchone()
        if not r:
            return {"ok": False}
        site, tel, email_at, wa_at, ig_at = r
        f = enr.enriquecer(site or "", tel or "", email_at or "")
        novo_email = email_at or f["email"]
        novo_ig = ig_at or f["instagram"]
        novo_wa = wa_at or f["whatsapp"]
        c.execute(
            """update prospeccao set email=%s, instagram=%s, whatsapp=%s, email_ok=%s,
                 enriquecido_em=now(), atualizado_em=now() where id=%s and conta_id=%s""",
            (novo_email, novo_ig, novo_wa, f["email_ok"], alvo_id, conta_id))
        c.commit()
    return {"ok": True, "email": novo_email, "email_ok": bool(f["email_ok"]),
            "instagram": novo_ig, "whatsapp": novo_wa}


def _enriquecer_lote_bg(pool, conta_id, ids):
    for aid in ids:
        try:
            _enriquecer_lead(pool, conta_id, aid)
        except Exception:  # noqa: BLE001
            pass


@router.post("/painel/prospeccao/{alvo_id}/enriquecer-canais")
def prospeccao_enriquecer_canais(request: Request, alvo_id: int):
    """Verifica os canais de UM lead (raspa o site, valida e-mail)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    try:
        return JSONResponse(_enriquecer_lead(pool, ctx["conta_id"], alvo_id))
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao verificar canais."})


@router.post("/painel/prospeccao/{alvo_id}/decisor-credify")
def prospeccao_decisor_credify(request: Request, alvo_id: int):
    """Descobre o DECISOR (sócio-administrador) do lead via Credify, pelo CNPJ:
    nome + cargo (+ telefone/WhatsApp se a conta tiver a consulta liberada). Salva
    em colunas dedicadas do lead. Consulta PAGA + dado de pessoa (LGPD) — ação
    deliberada por lead, só dono/gestor ou o dono do lead."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    from finance import credify as cf
    if not cf.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "Credify não configurada (CREDIFY_CLIENT_ID/SECRET no Render)."})
    if not (alvo.get("cnpj") and len(_so_digitos(alvo["cnpj"])) == 14):
        return JSONResponse({"ok": False, "erro": "Preencha o CNPJ do lead primeiro."})
    try:
        r = cf.decisor_com_telefone(alvo["cnpj"])
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao consultar a Credify."})
    nome = r.get("decisor_nome")
    if not nome:
        if r.get("permissao"):
            msg = "A consulta de Quadro Societário não está liberada na sua conta Credify."
        elif r.get("motivo"):
            msg = "Credify: " + str(r["motivo"]) + "."
        else:
            msg = "Não achei o quadro societário desse CNPJ na Credify."
        return JSONResponse({"ok": False, "erro": msg})
    tels = r.get("telefones") or []
    with pool.connection() as c:
        c.execute(
            """update prospeccao set decisor_nome=%s, decisor_cargo=%s, decisor_telefone=%s,
                 decisor_whatsapp=%s, decisor_telefones=%s::jsonb, decisor_em=now(), atualizado_em=now()
               where id=%s and conta_id=%s""",
            (nome, r.get("decisor_qualificacao"), r.get("decisor_telefone"),
             bool(r.get("decisor_whatsapp")), json.dumps(tels), alvo_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True, "nome": nome, "cargo": r.get("decisor_qualificacao"),
                         "telefone": r.get("decisor_telefone"),
                         "whatsapp": bool(r.get("decisor_whatsapp")),
                         "n_telefones": len(tels),
                         "sem_telefone": not tels})


@router.post("/painel/prospeccao/identificar-numero")
def prospeccao_identificar_numero(request: Request, numero: str = Form("")):
    """Telefone reverso (Credify 'PF Telefone'): número -> em que NOME está cadastrado.
    Consulta paga + dado de pessoa (LGPD) — ação deliberada. Não persiste o CPF."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import credify as cf
    if not cf.tem_credenciais():
        return JSONResponse({"ok": False, "erro": "Credify não configurada no ambiente."})
    dig = _so_digitos(numero)
    if len(dig) < 10:
        return JSONResponse({"ok": False, "erro": "Informe DDD + número (ex.: 86981885930)."})
    try:
        r = cf.titular_por_telefone("", dig)
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": "Falha ao consultar a Credify."})
    if not r.get("ok"):
        e = (r.get("erro") or "").lower()
        if "permiss" in e:
            msg = "A consulta de telefone reverso (PF Telefone) ainda não está liberada na sua conta Credify."
        elif "idconsulta" in e:
            msg = "Falta o IdConsulta da PF Telefone (CREDIFY_ID_TELREV) — avise o suporte."
        elif "não encontrado" in e or "sem_titular" in e:
            msg = "Nenhum titular encontrado pra esse número na base."
        else:
            msg = "Credify: " + (r.get("erro") or "não consegui")
        return JSONResponse({"ok": False, "erro": msg})
    def _mask(c):
        c = _so_digitos(c or "")
        return f"***.{c[3:6]}.***-{c[9:]}" if len(c) == 11 else ""
    def _tipo_ok(tp):   # a Credify manda códigos tipo "0"; só mostra rótulo de verdade
        tp = (tp or "").strip()
        return tp if (len(tp) > 1 and not tp.isdigit()) else ""
    tit, vistos = [], set()
    for t in (r.get("titulares") or []):
        nome = (t.get("nome") or "").strip()
        chave = (nome.upper(), _so_digitos(t.get("cpf")))
        if not nome or chave in vistos:      # dedup: mesmo titular repetido
            continue
        vistos.add(chave)
        tit.append({"nome": nome, "cidade": t.get("cidade"), "uf": t.get("uf"),
                    "tipo": _tipo_ok(t.get("tipo")), "cpf_mask": _mask(t.get("cpf")),
                    "endereco": t.get("endereco")})
    return JSONResponse({"ok": True, "titulares": tit})


@router.post("/painel/prospeccao/enriquecer-lote")
def prospeccao_enriquecer_lote(request: Request, background_tasks: BackgroundTasks):
    """Verifica canais de vários leads (que têm site e ainda não foram verificados),
    em background. Devolve quantos entraram na fila."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    where = ["conta_id=%s", "coalesce(site_url,'')<>''", "enriquecido_em is null"]
    params = [ctx["conta_id"]]
    if not ctx["gerencia"]:
        where.append("vendedor_id=%s")
        params.append(ctx["membro_id"])
    with get_pool().connection() as c:
        ids = [r[0] for r in c.execute(
            f"select id from prospeccao where {' and '.join(where)} order by criado_em desc limit 80",
            tuple(params)).fetchall()]
    if ids:
        import threading
        threading.Thread(target=_enriquecer_lote_bg, args=(get_pool(), ctx["conta_id"], ids),
                         daemon=True).start()
    return JSONResponse({"ok": True, "n": len(ids)})


@router.post("/painel/prospeccao/{alvo_id}/convidar-zaq")
def prospeccao_convidar_zaq(request: Request, alvo_id: int):
    """Manda pro lead um e-mail convidando a CRIAR conta no Zaq (link pro /cadastro
    pré-preenchido). Reusa o cadastro público — sem token/senha novos."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    destino = (alvo.get("email") or "").strip()
    if not destino:
        return JSONResponse({"ok": False, "erro": "Lead sem e-mail — cadastre o e-mail antes."})
    if not remetente_configurado():
        return JSONResponse({"ok": False, "erro": "E-mail não configurado no ambiente."})
    from urllib.parse import urlencode
    from finance.email_sender import _app_url, enviar_convite_zaq
    q = urlencode({k: v for k, v in {
        "nome": alvo.get("empresa") or "",
        "email": destino,
        "whatsapp": alvo.get("whatsapp") or alvo.get("telefone") or "",
    }.items() if v})
    link = _app_url() + "/cadastro" + (("?" + q) if q else "")
    _nome_rem, email_rem = _membro_contato(pool, ctx["conta_id"], ctx["membro_id"])
    ok = enviar_convite_zaq(destino, alvo.get("empresa"), link,
                            from_nome=(ctx["conta"][2] or None), reply_to=email_rem or None)
    if not ok:
        return JSONResponse({"ok": False, "erro": "Não consegui enviar o convite (confira o SMTP)."})
    remetente = remetente_configurado() or ""
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "email",
                       f"De {remetente} · Para {destino} · Convite pro Zaq\n\n"
                       f"Convite pra criar conta no Zaq: {link}", alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/painel/prospeccao/{alvo_id}/registrar-whatsapp")
def prospeccao_registrar_whatsapp(request: Request, alvo_id: int, texto: str = Form("")):
    """Registra na timeline que o WhatsApp de 1º contato foi disparado (o envio é
    no app do vendedor via wa.me — aqui só marcamos o histórico)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    with pool.connection() as c:
        _reg_atividade(c, alvo_id, ctx["conta_id"], ctx["membro_id"], "whatsapp",
                       "WhatsApp de 1º contato" + (f"\n\n{texto.strip()}" if texto.strip() else ""),
                       alvo["status"])
        c.commit()
    return JSONResponse({"ok": True})


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


# ---------------------------------------------------------------- helpers locais
def _pack(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def _unpack(s: str):
    try:
        return json.loads(base64.urlsafe_b64decode((s or "").encode()).decode())
    except Exception:  # noqa: BLE001
        return None


def _reais_para_centavos(v: str) -> int:
    s = (v or "").strip()
    if not s:
        return 0
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except (ValueError, TypeError):
        return 0


def _data_para_ts(v: str):
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ================================================================ CSS + TEMPLATES
_CSS = """<style>
.pw{max-width:1240px;margin:0 auto;padding:1.2rem 1rem 2.5rem}
.pw h2.tt{margin:0;font-size:1.35rem}
.pbtn{width:auto;margin:0;padding:.5rem .9rem;border-radius:9px;font-size:.86rem;font-weight:600;
  background:var(--verde);color:#fff;border:0;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;text-decoration:none}
.pbtn:hover{background:var(--verde-hover)}
.pbtn.ghost{background:transparent;color:var(--txt-mut);border:1px solid var(--borda)}
.pbtn.ghost:hover{color:var(--txt);border-color:var(--verde)}
.pbtn[disabled]{opacity:.45;cursor:not-allowed}
.tpill{display:inline-flex;align-items:center;padding:.12rem .55rem;border-radius:999px;font-size:.72rem;font-weight:600;line-height:1.4}
.spill{display:inline-flex;align-items:center;padding:.14rem .6rem;border-radius:999px;font-size:.74rem;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt)}
.tdot{width:11px;height:11px;border-radius:50%;flex-shrink:0;display:inline-block}
.fld{width:100%;padding:.55rem .7rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-family:inherit;font-size:.9rem}
.lbl{display:block;color:var(--txt-mut);font-size:.72rem;margin-bottom:.15rem}
.egrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem}
.egrid .full{grid-column:1/-1}
/* ---- kanban: mobile-first (abas), vira grid no desktop ---- */
.kbtabs{display:flex;gap:.3rem;overflow-x:auto;margin-top:.9rem;padding-bottom:.35rem;-webkit-overflow-scrolling:touch}
.kbtab{width:auto;margin:0;white-space:nowrap;padding:.4rem .7rem;border-radius:999px;font-size:.8rem;cursor:pointer;
  background:transparent;border:1px solid var(--borda);color:var(--txt-mut);display:inline-flex;align-items:center;gap:.35rem}
.kbtab.on{background:var(--verde);border-color:var(--verde);color:#fff;font-weight:600}
.kbtab .c{background:rgba(0,0,0,.25);border-radius:999px;padding:0 .4rem;font-size:.72rem}
.kbrow{display:block;margin-top:.5rem}
.kbcol{display:none;flex-direction:column;background:var(--bg);border:1px solid var(--borda);border-radius:14px;padding:.7rem;min-height:220px}
.kbcol.show{display:flex}
.kbcol.dragover{border-color:var(--verde);box-shadow:0 0 0 1px var(--verde) inset}
.kbcol h4{margin:0 0 .55rem;font-size:.85rem;display:flex;align-items:center;justify-content:space-between}
.kbcnt{background:var(--card-2);border:1px solid var(--borda);border-radius:999px;padding:.02rem .5rem;font-size:.72rem;color:var(--txt-mut)}
.kbdrop{flex:1;display:flex;flex-direction:column;gap:.5rem}
.kbempty{border:1px dashed var(--borda);border-radius:10px;color:var(--txt-mut);font-size:.76rem;text-align:center;padding:1.1rem .5rem;opacity:.55}
.kbcard{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.6rem .65rem;cursor:pointer;transition:border-color .15s,transform .1s}
.kbcard:hover{border-color:var(--verde)}
.kbcard:active{transform:scale(.98)}
.kbcard .emp{font-size:.88rem;font-weight:600;line-height:1.2}
.kbcard .sub{color:var(--txt-mut);font-size:.74rem;margin-top:.22rem}
.kbcard .ft{display:flex;align-items:center;justify-content:space-between;gap:.3rem;margin-top:.42rem;flex-wrap:wrap}
@media(min-width:900px){
  .kbtabs{display:none}
  .kbrow{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.55rem}
  .kbcol{display:flex !important;min-height:180px}
}
/* ---- ficha ---- */
.fgrid{display:grid;grid-template-columns:1.05fr 1fr;gap:1rem;margin-top:1rem;align-items:start}
@media(max-width:820px){.fgrid{grid-template-columns:1fr}}
.fsec{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:1rem 1.1rem}
.fsec+.fsec{margin-top:1rem}
.fsec .sh{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem;gap:.5rem}
.fsec .sh b{font-size:.9rem}
.drow{display:grid;grid-template-columns:1.4rem 96px 1fr;gap:.55rem;align-items:baseline;padding:.5rem 0;border-top:1px solid var(--borda);font-size:.86rem}
.drow:first-of-type{border-top:0}
.drow .ic{color:var(--txt-mut);text-align:center}
.drow .lb{color:var(--txt-mut);font-size:.78rem}
.drow>span:last-child{min-width:0;overflow-wrap:anywhere}
.badge{display:inline-block;padding:.02rem .4rem;border-radius:6px;font-size:.68rem;font-weight:600;background:#123a26;color:#5fe0a5;border:1px solid #1d5c3c;margin-left:.35rem}
.tl{position:relative;padding:.15rem 0 .8rem 1rem;border-left:2px solid var(--borda);margin-left:.25rem}
.tl:last-child{padding-bottom:.1rem}
.tl .dt{position:absolute;left:-7px;top:.28rem;width:12px;height:12px;border-radius:50%;border:2px solid var(--card)}
.rcpills{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 .6rem}
.rcpill{width:auto;margin:0;padding:.35rem .75rem;border-radius:999px;font-size:.8rem;cursor:pointer;background:transparent;border:1px solid var(--borda);color:var(--txt-mut)}
.rcpill.on{background:var(--verde);border-color:var(--verde);color:#fff;font-weight:600}
.chipin{display:inline-flex;align-items:center;gap:.4rem;border:1px solid var(--borda);border-radius:999px;padding:.3rem .7rem;color:var(--txt-mut);font-size:.8rem;background:var(--bg)}
.chipin input{border:0;background:transparent;color:var(--txt);padding:0;width:auto;font-size:.82rem}
/* ---- captação ---- */
.cabas{display:flex;gap:.3rem;background:var(--bg);border:1px solid var(--borda);border-radius:11px;padding:4px;margin:.2rem 0 1rem}
.caba{width:auto;margin:0;flex:1;text-align:center;padding:.5rem .6rem;border-radius:8px;font-size:.85rem;cursor:pointer;background:transparent;border:0;color:var(--txt-mut);text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:.35rem}
.caba.on{background:var(--verde);color:#fff;font-weight:600}
.rlist{border:1px solid var(--borda);border-radius:12px;overflow:hidden;margin-top:.5rem}
.rrow{display:flex;align-items:center;gap:.7rem;padding:.6rem .8rem;border-top:1px solid var(--borda)}
.rrow:first-child{border-top:0}
.rrow input[type=checkbox]{width:auto;margin:0;flex-shrink:0;width:18px;height:18px;accent-color:var(--verde)}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0;position:absolute}
.tgl{position:absolute;inset:0;background:#333;border-radius:999px;transition:.2s;cursor:pointer}
.tgl:before{content:'';position:absolute;left:3px;top:3px;width:18px;height:18px;background:#fff;border-radius:50%;transition:.2s}
.toggle input:checked+.tgl{background:var(--verde)}
.toggle input:checked+.tgl:before{transform:translateX(20px)}
</style>"""

_KANBAN_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw">
  <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
    <div style="flex:1;min-width:170px">
      <h2 class="tt">Prospecção</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">{% if conta %}<b style="color:var(--verde-claro)">🏢 {{ conta[2] }}</b> · {% endif %}<span id="kb-total-n">{{ total_alvos }}</span> alvo(s){% if total_valor %} · pipeline {{ brl(total_valor) }}{% endif %}{% if n_contextos and n_contextos > 1 %} · <a href="/trocar" style="color:var(--verde-claro)">trocar empresa ⇄</a>{% endif %}</div>
    </div>
    <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
      <a class="pbtn ghost" href="/painel/prospeccao/comunicacao" style="display:inline-flex;align-items:center">📨 Comunicação</a>
      {% if gerencia %}<a class="pbtn ghost" href="/painel/prospeccao/campanhas" style="display:inline-flex;align-items:center">📣 Campanhas</a>{% endif %}
      <button type="button" class="pbtn ghost" id="enrq-btn" onclick="enrqLote()" title="Raspa o site dos leads e descobre e-mail, Instagram e WhatsApp">🔎 Verificar canais</button>
      <span class="mut" id="enrq-msg" style="font-size:.8rem"></span>
      <button type="button" class="pbtn" onclick="capToggle()">🎯 Captar leads</button>
    </div>
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <!-- painel de captação inline (abre pra baixo, sem sair da página) -->
  <div id="captar" class="fsec" style="display:none;margin-top:1rem">
    <div class="cabas">
      <button type="button" class="caba on" data-tab="manual" onclick="capTab('manual')">✏️ Manual</button>
      <button type="button" class="caba" data-tab="csv" onclick="capTab('csv')">📄 CSV</button>
      <button type="button" class="caba" data-tab="google" onclick="capTab('google')">📍 Google Maps</button>
    </div>

    <div class="captab" data-tab="manual">
      <form id="cap-manual" action="/painel/prospeccao/novo" method="post" onsubmit="return capManual(event)">
        <input type="hidden" name="voltar" value="/painel/prospeccao">
        <input type="hidden" name="receita">
        <div style="display:flex;gap:.5rem;align-items:end;background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.7rem;margin-bottom:.8rem;flex-wrap:wrap">
          <div style="flex:1;min-width:200px"><label class="lbl">🔎 CNPJ — puxa tudo da Receita</label><input class="fld" name="cnpj" inputmode="numeric" placeholder="digite o CNPJ (só números) e clique buscar"></div>
          <button type="button" class="pbtn" onclick="capCnpj()" style="white-space:nowrap">↓ Buscar Receita</button>
        </div>
        <div class="egrid">
          <div class="full"><label class="lbl">Empresa *</label><input class="fld" name="empresa" required placeholder="Nome da empresa"></div>
          <div><label class="lbl">Contato</label><input class="fld" name="contato"></div>
          <div><label class="lbl">Cargo</label><input class="fld" name="cargo" placeholder="Cargo do contato"></div>
          <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
          <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
          <div><label class="lbl">E-mail</label><input class="fld" name="email" inputmode="email"></div>
          <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
          <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
          <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
          <div><label class="lbl">Sócio</label><input class="fld" name="socio"></div>
          <div><label class="lbl">Regime</label><input class="fld" name="regime_tributario"></div>
          <div><label class="lbl">Porte</label><input class="fld" name="porte"></div>
          <div><label class="lbl">Instagram</label><input class="fld" name="instagram" placeholder="@perfil"></div>
          <div><label class="lbl">Site (link)</label><input class="fld" name="site_url" inputmode="url" placeholder="https://…"></div>
          <div><label class="lbl">Valor (R$)</label><input class="fld" name="valor" inputmode="decimal" placeholder="0,00"></div>
          <div><label class="lbl">Temperatura</label><select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
          {% if pode_atribuir %}<div><label class="lbl">Vendedor</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
          <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs"></div>
          <div class="full"><button class="pbtn" style="margin:.3rem 0 0">Adicionar</button></div>
        </div>
      </form>
    </div>

    <div class="captab" data-tab="csv" style="display:none">
      <form id="cap-csv" action="/painel/prospeccao/captar/csv" method="post" enctype="multipart/form-data" onsubmit="return capCsv(event)">
        <label class="lbl">Arquivo CSV</label>
        <input class="fld" type="file" name="arquivo" accept=".csv,text/csv" required>
        <div class="mut" style="font-size:.8rem;margin-top:.5rem">1ª linha = cabeçalho. Colunas: <b>empresa</b>, telefone, whatsapp, cidade, uf, segmento, contato, email, cnpj. Separador , ou ;.</div>
        {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
        <button class="pbtn" style="margin-top:.8rem">Importar CSV</button>
      </form>
    </div>

    <div class="captab" data-tab="google" style="display:none">
      {% if not tem_places %}
      <div class="mut" style="font-size:.84rem;line-height:1.6">📍 Pra buscar no Google Maps falta a chave. No Render (openclaw-web → Environment) adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_PLACES_API_KEY</code> (Places API New, billing ativo).</div>
      {% else %}
      <form id="cap-google" action="/painel/prospeccao/captar/buscar" method="post" onsubmit="return capBuscar(event)">
        <div class="egrid">
          <div><label class="lbl">Segmento</label><input class="fld" name="segmento" required placeholder="Ex: pet shop"></div>
          <div><label class="lbl">Cidade</label><input class="fld" name="cidade" placeholder="Ex: Teresina - PI"></div>
        </div>
        <div class="lbl" style="margin-top:.7rem;color:var(--txt-mut)">📍 Refinar por região <span style="font-weight:400">— opcional, pra buscar numa área específica</span></div>
        <div class="egrid" style="margin-top:.25rem">
          <div><label class="lbl">Bairro</label><input class="fld" name="bairro" placeholder="Ex: Jardim Renascença"></div>
          <div><label class="lbl">Rua</label><input class="fld" name="rua" placeholder="Ex: Av. Nossa Sra. de Fátima"></div>
        </div>
        <div class="mut" style="font-size:.76rem;margin-top:.3rem">Bairro filtra a vizinhança toda. Rua afunila bastante (poucos resultados) — use pra mira fina.</div>
        <label class="rrow" style="border:1px solid var(--borda);border-radius:10px;margin-top:.6rem;cursor:pointer">
          <span class="toggle"><input type="checkbox" name="esconder_redes" value="1" checked><span class="tgl"></span></span>
          <span style="font-size:.88rem">Esconder redes grandes (Petz, Drogasil…)</span>
        </label>
        {% if pode_atribuir %}<div style="max-width:280px;margin-top:.6rem"><label class="lbl">Atribuir a</label><select class="fld" id="cap-g-vend" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}
        <button class="pbtn" style="margin-top:.8rem" id="cap-g-btn">Buscar</button>
      </form>
      <div id="cap-res" style="margin-top:.9rem"></div>
      {% endif %}
    </div>
  </div>

  {% if gerencia %}
  <form method="get" action="/painel/prospeccao" style="margin-top:.8rem;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
    <span class="mut" style="font-size:.8rem">Vendedor:</span>
    <select name="vendedor" onchange="this.form.submit()" style="width:auto;padding:.4rem .6rem">
      <option value="" {% if not filtro_vend %}selected{% endif %}>Todos</option>
      <option value="nao" {% if filtro_vend=='nao' %}selected{% endif %}>Sem responsável</option>
      {% for v in vendedores %}<option value="{{ v.id }}" {% if filtro_vend==(v.id|string) %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
    </select>
  </form>
  {% endif %}

  <!-- abas de status (só mobile) -->
  <div class="kbtabs" id="kbtabs">
    {% for s, rot in status %}<button type="button" class="kbtab" data-tab="{{ s }}" onclick="kbTab('{{ s }}')">{{ rot }} <span class="c">{{ colunas[s]|length }}</span></button>{% endfor %}
  </div>

  <div class="kbrow" id="kbrow">
    {% for s, rot in status %}
    <div class="kbcol" data-status="{{ s }}" ondragover="kbOver(event)" ondragleave="kbLeave(event)" ondrop="kbDrop(event,'{{ s }}')">
      <h4><span>{{ rot }}</span><span class="kbcnt">{{ colunas[s]|length }}</span></h4>
      <div class="kbdrop">
        {% for c in colunas[s] %}
        <div class="kbcard" draggable="true" data-id="{{ c.id }}" ondragstart="kbDrag(event,{{ c.id }})" ondragend="kbEnd(event)"
             onclick="if(!window._kbMoved)kbAbrir({{ c.id }})">
          <div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" title="{{ c.temperatura }}" style="background:{{ temp_cor[c.temperatura] }}"></span><span class="emp">{{ c.empresa }}</span><span style="flex:1"></span><button type="button" class="kbx" title="Excluir lead" onclick="kbExcluir(event,{{ c.id }})">✕</button></div>
          {% if c.segmento or c.cidade %}<div class="sub">{% if c.segmento %}{{ c.segmento }}{% endif %}{% if c.cidade %} · {{ c.cidade }}{% if c.uf %}/{{ c.uf }}{% endif %}{% endif %}</div>{% endif %}
          {% if c.tem_whatsapp or c.tem_email or c.tem_instagram or c.enriquecido %}<div class="kbch">{% if c.tem_whatsapp %}<span title="WhatsApp">💬</span>{% endif %}{% if c.tem_email %}<span title="E-mail">✉️</span>{% endif %}{% if c.tem_instagram %}<span title="Instagram">📸</span>{% endif %}{% if c.enriquecido and not (c.tem_whatsapp or c.tem_email or c.tem_instagram) %}<span class="mut" title="Verificado, sem canal encontrado">— sem canal</span>{% endif %}</div>{% endif %}
          <div class="ft">{% if c.valor %}<span style="font-size:.76rem;color:var(--verde-claro)">{{ brl(c.valor) }}</span>{% else %}<span></span>{% endif %}{% if c.proximo %}<span class="mut" style="font-size:.72rem">📅 {{ c.proximo.strftime('%d/%m') }}</span>{% endif %}</div>
          {% if gerencia and c.vendedor %}<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 {{ c.vendedor }}</div>{% endif %}
        </div>
        {% else %}<div class="kbempty">vazio</div>{% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<style>
.kbch{display:flex;gap:.35rem;margin-top:.3rem;font-size:.82rem;align-items:center}
.kbch .mut{font-size:.7rem}
.kbx{background:none;border:0;color:#6b6b6b;cursor:pointer;font-size:.82rem;line-height:1;padding:.1rem .25rem;border-radius:6px;opacity:.55}
.kbx:hover{opacity:1;color:#e0574f;background:rgba(224,87,79,.12)}
#kb-drawer{position:fixed;inset:0;z-index:80;display:none}
#kb-drawer.on{display:block}
#kb-drawer .bd{position:absolute;inset:0;background:rgba(0,0,0,.55)}
#kb-drawer .pnl{position:absolute;top:0;right:0;bottom:0;width:min(760px,96vw);background:var(--bg);border-left:1px solid var(--borda);box-shadow:-12px 0 40px rgba(0,0,0,.45);transform:translateX(100%);transition:transform .22s ease;display:flex;flex-direction:column}
#kb-drawer.on .pnl{transform:translateX(0)}
#kb-drawer .dh{display:flex;align-items:center;gap:.6rem;padding:.55rem .8rem;border-bottom:1px solid var(--borda);background:#0b0b0c}
#kb-drawer .dh b{font-size:.9rem}
#kb-drawer .dh .x{margin-left:auto;background:none;border:1px solid var(--borda);color:var(--txt);border-radius:8px;padding:.3rem .6rem;cursor:pointer}
#kb-drawer iframe{flex:1;border:0;width:100%;background:var(--bg)}
</style>
<div id="kb-drawer">
  <div class="bd" onclick="kbFechar()"></div>
  <div class="pnl">
    <div class="dh"><b id="kb-dtit">Lead</b>
      <a class="x" id="kb-dfull" href="#" title="Abrir em página cheia">⤢</a>
      <button class="x" onclick="kbFechar()">✕ Fechar</button></div>
    <iframe id="kb-dframe" title="Ficha do lead"></iframe>
  </div>
</div>

<script>
function kbAbrir(id){var dr=document.getElementById('kb-drawer');
  document.getElementById('kb-dframe').src='/painel/prospeccao/'+id+'?embed=1';
  document.getElementById('kb-dfull').href='/painel/prospeccao/'+id;
  var card=document.querySelector('.kbcard[data-id="'+id+'"]');
  var nm=card?(card.querySelector('.emp')||{}).textContent:'';document.getElementById('kb-dtit').textContent=nm||'Lead';
  dr.classList.add('on');document.body.style.overflow='hidden';}
function kbFechar(){var dr=document.getElementById('kb-drawer');dr.classList.remove('on');document.body.style.overflow='';
  setTimeout(function(){document.getElementById('kb-dframe').src='about:blank';},250);}
document.addEventListener('keydown',function(e){if(e.key==='Escape')kbFechar();});
function kbTab(s){document.querySelectorAll('.kbcol').forEach(function(c){c.classList.toggle('show',c.getAttribute('data-status')===s);});
  document.querySelectorAll('.kbtab').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===s);});}
(function(){var cols=document.querySelectorAll('#kbrow .kbcol');var alvo='novo';
  for(var i=0;i<cols.length;i++){if(cols[i].querySelectorAll('.kbcard').length){alvo=cols[i].getAttribute('data-status');break;}}
  kbTab(alvo);})();
window._kbMoved=false;
function kbDrag(ev,id){ev.dataTransfer.setData('text/plain',id);ev.dataTransfer.effectAllowed='move';window._kbDragEl=ev.currentTarget;setTimeout(function(){ev.currentTarget.style.opacity='.35';},0);}
function kbEnd(ev){ev.currentTarget.style.opacity='';setTimeout(function(){window._kbMoved=false;},60);}
function kbOver(ev){ev.preventDefault();ev.currentTarget.classList.add('dragover');}
function kbLeave(ev){ev.currentTarget.classList.remove('dragover');}
function kbDrop(ev,status){ev.preventDefault();ev.currentTarget.classList.remove('dragover');
  var id=ev.dataTransfer.getData('text/plain');var card=window._kbDragEl;if(!id||!card)return;window._kbMoved=true;
  var drop=ev.currentTarget.querySelector('.kbdrop');var emp=drop.querySelector('.kbempty');if(emp)emp.remove();drop.appendChild(card);
  var body=new URLSearchParams();body.append('status',status);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){location.reload();return;}
      document.querySelectorAll('.kbcol').forEach(function(col){var n=col.querySelectorAll('.kbcard').length;
        var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;
        var tabc=document.querySelector('.kbtab[data-tab="'+col.getAttribute('data-status')+'"] .c');if(tabc)tabc.textContent=n;
        var dp=col.querySelector('.kbdrop');if(n===0&&!dp.querySelector('.kbempty')){var e=document.createElement('div');e.className='kbempty';e.textContent='vazio';dp.appendChild(e);}});
    }).catch(function(){location.reload();});}

// ---- captação inline (sem reload) ----
var TEMPCOR={frio:'#5b9bd5',morno:'#e0a33e',quente:'#e0574f'};
function jsEsc(s){return (s||'').replace(/[&<>"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function jsBrl(c){c=c||0;var s=(c/100).toFixed(2).split('.');var i=s[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g,'.');return 'R$ '+i+','+s[1];}
function cardGo(id){if(!window._kbMoved)kbAbrir(id);}
function enrqLote(){var b=document.getElementById('enrq-btn'),m=document.getElementById('enrq-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Verificando…';m.textContent='';
  fetch('/painel/prospeccao/enriquecer-lote',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
    if(!d.ok){m.textContent=d.erro||'Não consegui.';return;}
    m.textContent=d.n?('Verificando '+d.n+' lead(s) em 2º plano — recarregue em ~1 min pra ver os canais.'):'Nada pra verificar (leads sem site ou já verificados).';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';});}
function kbExcluir(ev,id){ev.stopPropagation();ev.preventDefault();
  if(!confirm('Excluir este lead? A conversa/e-mail dele continua no inbox — só sai do funil.'))return;
  fetch('/painel/prospeccao/'+id+'/excluir',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui excluir.');return;}
      var card=document.querySelector('.kbcard[data-id="'+id+'"]');if(card&&card.parentNode)card.parentNode.removeChild(card);
      updCounts();}).catch(function(){alert('Falha de rede.');});}
function updCounts(){var tot=0;document.querySelectorAll('.kbcol').forEach(function(col){var n=col.querySelectorAll('.kbcard').length;tot+=n;var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;var tc=document.querySelector('.kbtab[data-tab="'+col.getAttribute('data-status')+'"] .c');if(tc)tc.textContent=n;});var tn=document.getElementById('kb-total-n');if(tn)tn.textContent=tot;}
function addCard(l){var col=document.querySelector('.kbcol[data-status="novo"]');if(!col)return;var drop=col.querySelector('.kbdrop');var e=drop.querySelector('.kbempty');if(e)e.remove();
  var cor=TEMPCOR[l.temperatura]||'#5b9bd5';
  var sub=(l.segmento||l.cidade)?('<div class="sub">'+(l.segmento?jsEsc(l.segmento):'')+(l.cidade?(' · '+jsEsc(l.cidade)+(l.uf?('/'+jsEsc(l.uf)):'')):'')+'</div>'):'';
  var ft='<div class="ft">'+(l.valor?('<span style="font-size:.76rem;color:var(--verde-claro)">'+jsBrl(l.valor)+'</span>'):'<span></span>')+'<span></span></div>';
  var vd=l.vendedor?('<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 '+jsEsc(l.vendedor)+'</div>'):'';
  var html='<div class="kbcard" draggable="true" data-id="'+l.id+'" ondragstart="kbDrag(event,'+l.id+')" ondragend="kbEnd(event)" onclick="cardGo('+l.id+')"><div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" style="background:'+cor+'"></span><span class="emp">'+jsEsc(l.empresa)+'</span><span style="flex:1"></span><button type="button" class="kbx" title="Excluir lead" onclick="kbExcluir(event,'+l.id+')">✕</button></div>'+sub+ft+vd+'</div>';
  drop.insertAdjacentHTML('afterbegin',html);updCounts();}
function capToggle(){var e=document.getElementById('captar');var vis=e.style.display!=='none';e.style.display=vis?'none':'block';if(!vis){var i=e.querySelector('.captab[data-tab=manual] input[name=empresa]');if(i)i.focus();e.scrollIntoView({behavior:'smooth',block:'nearest'});}}
function capTab(t){document.querySelectorAll('#captar .caba').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-tab')===t);});document.querySelectorAll('#captar .captab').forEach(function(d){d.style.display=(d.getAttribute('data-tab')===t)?'block':'none';});}
function capFetch(url,fd){return fetch(url,{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();});}
function capManual(ev){ev.preventDefault();var f=ev.target;capFetch('/painel/prospeccao/novo',new FormData(f)).then(function(d){if(!d.ok){capToast(d.erro||'Erro');return;}addCard(d.lead);f.reset();capToast('Lead adicionado');}).catch(function(){capToast('Falha de rede');});return false;}
function capCnpj(){var f=document.getElementById('cap-manual');var cnpj=f.querySelector('[name=cnpj]').value.replace(/\\D/g,'');if(cnpj.length!==14){capToast('CNPJ precisa ter 14 dígitos');return;}
  capToast('Consultando Receita…');
  fetch('/painel/prospeccao/cnpj?cnpj='+cnpj,{headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){capToast('CNPJ não encontrado ('+(d.erro||'')+')');return;}var x=d.dados;
    function put(n,v,forca){var el=f.querySelector('[name='+n+']');if(el&&v&&(forca||!el.value))el.value=v;}
    put('empresa',x.razao_social,false);put('segmento',x.segmento,true);put('cidade',x.cidade,true);put('uf',x.uf,true);
    put('telefone',x.telefone,true);put('email',x.email,true);put('socio',x.socio,true);put('regime_tributario',x.regime_tributario,true);put('porte',x.porte,true);
    var rc=f.querySelector('[name=receita]');if(rc){try{rc.value=JSON.stringify(x);}catch(e){}}
    capToast('Dados da Receita preenchidos ✓');
  }).catch(function(){capToast('Falha de rede');});}
function capCsv(ev){ev.preventDefault();capFetch('/painel/prospeccao/captar/csv',new FormData(ev.target)).then(function(d){if(!d.ok){capToast('Erro no CSV');return;}capToast(d.msg||'Importado');setTimeout(function(){location.reload();},800);}).catch(function(){capToast('Falha de rede');});return false;}
function capBuscar(ev){ev.preventDefault();var f=ev.target;var btn=document.getElementById('cap-g-btn');if(btn){btn.disabled=true;btn.textContent='Buscando…';}
  capFetch('/painel/prospeccao/captar/buscar',new FormData(f)).then(function(d){if(btn){btn.disabled=false;btn.textContent='Buscar';}var box=document.getElementById('cap-res');
    if(!d.ok){box.innerHTML='<div class="mut" style="color:#e0a33e">Não consegui buscar ('+(d.erro||'?')+'). Confira a chave/billing e tente de novo.</div>';return;}
    if(!d.itens.length){box.innerHTML='<div class="mut">Nada encontrado'+(d.n_redes?(' ('+d.n_redes+' rede(s) oculta(s))'):'')+'. Tente outro termo/cidade.</div>';return;}
    var TP={quente:'#f0917f',morno:'#e0b25a',frio:'#7bb8e6'};
    var h='<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem"><div class="mut" style="font-size:.82rem">'+d.itens.length+' encontrado(s)'+(d.n_redes?(' · '+d.n_redes+' oculta(s)'):'')+'</div><label class="mut" style="font-size:.8rem;cursor:pointer"><input type="checkbox" onclick="capAll(this)" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> marcar todos</label></div><div class="rlist" id="cap-list">';
    d.itens.forEach(function(it){var loc=(it.cidade?(' · '+jsEsc(it.cidade)+(it.uf?('/'+jsEsc(it.uf)):'')):'');h+='<label class="rrow" style="cursor:pointer"><input type="checkbox" name="itens" value="'+it.pack+'"><span style="flex:1"><span style="display:flex;align-items:center;gap:.4rem"><span class="tdot" style="background:'+(TEMPCOR[it.temperatura]||'#5b9bd5')+'"></span><b style="font-size:.88rem">'+jsEsc(it.empresa)+'</b>'+(it.aberto===false?' <span style=\\'color:#e0574f;font-size:.7rem\\'>(fechado)</span>':'')+'</span><span class="mut" style="font-size:.76rem">'+(it.segmento?(jsEsc(it.segmento)+' · '):'')+(it.telefone?jsEsc(it.telefone):'')+(it.rating?(' · nota '+it.rating):'')+(it.tem_site?'':' · <span style=\\'color:#e0574f\\'>sem site</span>')+loc+'</span></span><span class="tpill" style="background:transparent;border:1px solid '+(TP[it.temperatura]||'#7bb8e6')+';color:'+(TP[it.temperatura]||'#7bb8e6')+'">'+it.temperatura+'</span></label>';});
    h+='</div><div style="margin-top:.8rem"><button type="button" class="pbtn" onclick="capImport()">Adicionar selecionados</button></div>';box.innerHTML=h;
  }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Buscar';}capToast('Falha de rede');});return false;}
function capAll(el){document.querySelectorAll('#cap-list input[name=itens]').forEach(function(c){c.checked=el.checked;});}
function capImport(){var packs=[];document.querySelectorAll('#cap-list input[name=itens]:checked').forEach(function(c){packs.push(c.value);});if(!packs.length){capToast('Marque ao menos um');return;}
  var fd=new FormData();packs.forEach(function(p){fd.append('itens',p);});var vs=document.getElementById('cap-g-vend');if(vs)fd.append('vendedor_id',vs.value);
  capFetch('/painel/prospeccao/captar/importar',fd).then(function(d){if(!d.ok){capToast('Erro ao importar');return;}(d.leads||[]).forEach(addCard);capToast(d.msg||'Adicionados');document.getElementById('cap-res').innerHTML='';var gf=document.getElementById('cap-google');if(gf)gf.reset();}).catch(function(){capToast('Falha de rede');});}
function capToast(msg){var t=document.getElementById('cap-toast');if(!t){t=document.createElement('div');t.id='cap-toast';t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s';document.body.appendChild(t);}t.textContent=msg;t.style.opacity='1';clearTimeout(window._captoastT);window._captoastT=setTimeout(function(){t.style.opacity='0';},2600);}
</script>
{% endblock %}"""

_CAPTAR_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw" style="max-width:760px">
  <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
    <a href="/painel/prospeccao" class="mut" style="text-decoration:none;font-size:.85rem">‹ Prospecção</a>
    <span style="flex:1"></span>
  </div>
  <h2 class="tt" style="margin-top:.3rem">Captar leads</h2>
  {% if aviso %}<div class="ok" style="margin-top:.6rem">{{ aviso }}</div>{% endif %}

  <div class="cabas">
    <a class="caba {% if aba=='manual' %}on{% endif %}" href="/painel/prospeccao/captar?aba=manual">✏️ Manual</a>
    <a class="caba {% if aba=='csv' %}on{% endif %}" href="/painel/prospeccao/captar?aba=csv">📄 CSV</a>
    <a class="caba {% if aba=='google' %}on{% endif %}" href="/painel/prospeccao/captar?aba=google">📍 Google Maps</a>
  </div>

  {% macro vendsel() %}{% if pode_atribuir %}<div><label class="lbl">Atribuir a</label>
    <select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>{% endif %}{% endmacro %}

  {% if aba=='manual' %}
  <div class="fsec">
    <form method="post" action="/painel/prospeccao/novo" class="egrid">
      <input type="hidden" name="voltar" value="/painel/prospeccao/captar">
      <div class="full"><label class="lbl">Empresa *</label><input class="fld" name="empresa" required placeholder="Nome da empresa"></div>
      <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
      <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
      <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
      <div><label class="lbl">Contato</label><input class="fld" name="contato"></div>
      <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
      <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
      <div><label class="lbl">CNPJ</label><input class="fld" name="cnpj"></div>
      <div><label class="lbl">Temperatura</label><select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
      {{ vendsel() }}
      <div class="full"><button class="pbtn" style="margin:.3rem 0 0">Adicionar lead</button></div>
    </form>
  </div>

  {% elif aba=='csv' %}
  <div class="fsec">
    <form method="post" action="/painel/prospeccao/captar/csv" enctype="multipart/form-data">
      <label class="lbl">Arquivo CSV</label>
      <input class="fld" type="file" name="arquivo" accept=".csv,text/csv" required>
      <div class="mut" style="font-size:.8rem;margin-top:.5rem">Use a 1ª linha como cabeçalho. Colunas reconhecidas:
        <b>empresa</b> (ou nome), telefone, whatsapp, cidade, uf, segmento, contato, email, cnpj. Separador vírgula ou ponto-e-vírgula.</div>
      {% if pode_atribuir %}<div style="margin-top:.6rem;max-width:280px">{{ vendsel() }}</div>{% endif %}
      <button class="pbtn" style="margin-top:.8rem">Importar CSV</button>
    </form>
  </div>

  {% else %}
  <div class="fsec">
    {% if not tem_places %}
    <div style="padding:.4rem 0">
      <b style="font-size:.9rem">📍 Buscar no Google Maps</b>
      <div class="mut" style="font-size:.84rem;margin-top:.5rem;line-height:1.6">
        Pra puxar comércio real (nome, telefone, nota, se tem site) falta configurar a chave da API.
        No Render, em <b>openclaw-web → Environment</b>, adicione <code style="background:var(--bg);padding:.1rem .35rem;border-radius:5px;border:1px solid var(--borda)">GOOGLE_PLACES_API_KEY</code>
        (Places API New, billing ativo no Google Cloud). Assim que salvar e o serviço reiniciar, a busca funciona aqui.
      </div>
    </div>
    {% else %}
    <form method="post" action="/painel/prospeccao/captar/buscar">
      <div class="egrid">
        <div><label class="lbl">Segmento</label><input class="fld" name="segmento" required placeholder="Ex: pet shop" value="{{ busca.segmento or '' }}"></div>
        <div><label class="lbl">Cidade</label><input class="fld" name="cidade" placeholder="Ex: Teresina - PI" value="{{ busca.cidade or '' }}"></div>
      </div>
      <div class="lbl" style="margin-top:.7rem;color:var(--txt-mut)">📍 Refinar por região <span style="font-weight:400">— opcional, pra buscar numa área específica</span></div>
      <div class="egrid" style="margin-top:.25rem">
        <div><label class="lbl">Bairro</label><input class="fld" name="bairro" placeholder="Ex: Jardim Renascença" value="{{ busca.bairro or '' }}"></div>
        <div><label class="lbl">Rua</label><input class="fld" name="rua" placeholder="Ex: Av. Nossa Sra. de Fátima" value="{{ busca.rua or '' }}"></div>
      </div>
      <div class="mut" style="font-size:.76rem;margin-top:.3rem">Bairro filtra a vizinhança toda. Rua afunila bastante (poucos resultados) — use pra mira fina.</div>
      <label class="rrow" style="border:1px solid var(--borda);border-radius:10px;margin-top:.6rem;cursor:pointer">
        <span class="toggle"><input type="checkbox" name="esconder_redes" value="1" {% if busca.esconder %}checked{% endif %}><span class="tgl"></span></span>
        <span style="font-size:.88rem">Esconder redes grandes (Petz, Drogasil…)</span>
      </label>
      <button class="pbtn" style="margin-top:.8rem">Buscar</button>
    </form>

    {% if resultados is not none %}
      {% if not busca.ok %}
        <div class="mut" style="margin-top:.9rem;color:#e0a33e">Não consegui buscar agora ({{ busca.erro }}). Confira a chave/billing e tente de novo.</div>
      {% elif not resultados %}
        <div class="mut" style="margin-top:.9rem">Nada encontrado{% if busca.esconder and busca.n_redes %} (escondi {{ busca.n_redes }} rede(s) grande(s)){% endif %}. Tente outro termo/cidade.</div>
      {% else %}
      <form method="post" action="/painel/prospeccao/captar/importar" style="margin-top:.9rem">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
          <div class="mut" style="font-size:.82rem">{{ resultados|length }} encontrado(s){% if busca.esconder and busca.n_redes %} · {{ busca.n_redes }} rede(s) oculta(s){% endif %}</div>
          <label class="mut" style="font-size:.8rem;cursor:pointer"><input type="checkbox" onclick="captAll(this)" style="width:auto;vertical-align:middle;accent-color:var(--verde)"> marcar todos</label>
        </div>
        <div class="rlist">
          {% for it in resultados %}
          <label class="rrow" style="cursor:pointer">
            <input type="checkbox" name="itens" value="{{ it.pack }}">
            <span style="flex:1">
              <span style="display:flex;align-items:center;gap:.4rem"><span class="tdot" style="background:{{ temp_cor[it.temperatura] }}"></span><b style="font-size:.88rem">{{ it.empresa }}</b></span>
              <span class="mut" style="font-size:.76rem">{% if it.segmento %}{{ it.segmento }} · {% endif %}{% if it.telefone %}{{ it.telefone }}{% endif %}{% if it.rating %} · nota {{ it.rating }}{% endif %}{% if not it.tem_site %} · <span style="color:#e0574f">sem site</span>{% endif %}{% if it.cidade %} · {{ it.cidade }}{% if it.uf %}/{{ it.uf }}{% endif %}{% endif %}</span>
            </span>
            {% set tp = {'quente':'#f0917f','morno':'#e0b25a','frio':'#7bb8e6'} %}
            <span class="tpill" style="background:transparent;border:1px solid {{ tp[it.temperatura] }};color:{{ tp[it.temperatura] }}">{{ it.temperatura }}</span>
          </label>
          {% endfor %}
        </div>
        <div style="display:flex;align-items:end;gap:.6rem;flex-wrap:wrap;margin-top:.8rem">
          {% if pode_atribuir %}<div style="min-width:200px">{{ vendsel() }}</div>{% endif %}
          <button class="pbtn">Adicionar selecionados</button>
        </div>
      </form>
      {% endif %}
    {% endif %}
    {% endif %}
  </div>
  {% endif %}
</div>
<script>
function captAll(el){document.querySelectorAll('input[name=itens]').forEach(function(c){c.checked=el.checked;});}
</script>
{% endblock %}"""

_FICHA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw" style="max-width:920px">
  <a href="/painel/prospeccao" class="mut" style="text-decoration:none;font-size:.85rem">‹ Prospecção</a>

  <div class="fsec" style="margin-top:.5rem">
    <div style="display:flex;align-items:flex-start;gap:.6rem;flex-wrap:wrap">
      <div style="flex:1;min-width:200px">
        <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
          <span class="tdot" style="width:13px;height:13px;background:{{ temp_cor[a.temperatura] }}"></span>
          <h2 class="tt">{{ a.empresa }}</h2>
          {% set tp = temp_pill[a.temperatura] %}<span class="tpill" style="background:{{ tp[0] }};color:{{ tp[1] }}">{{ a.temperatura }}</span>
        </div>
        <div class="mut" style="font-size:.82rem;margin-top:.25rem">{% if a.segmento %}{{ a.segmento }}{% endif %}{% if a.cidade %}{% if a.segmento %} · {% endif %}{{ a.cidade }}{% if a.uf %}/{{ a.uf }}{% endif %}{% endif %}{% if a.vendedor_nome %} · 👤 {{ a.vendedor_nome }}{% endif %}</div>
      </div>
      <form method="post" action="/painel/prospeccao/{{ a.id }}/status" style="margin:0">
        <select name="status" onchange="this.form.submit()" class="spill" style="width:auto;padding:.25rem .6rem;border-radius:999px">
          {% for s,rot in status %}<option value="{{ s }}" {% if s==a.status %}selected{% endif %}>{{ rot }}</option>{% endfor %}
        </select>
      </form>
    </div>
    <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.8rem">
      {% if a.tel_link %}<a class="pbtn ghost" href="{{ a.tel_link }}">📞 Ligar</a>{% endif %}
      {% if a.zap_link %}<a class="pbtn ghost" href="{{ a.zap_link }}" target="_blank" rel="noopener">💬 WhatsApp</a>{% endif %}
      {% if a.maps_url %}<a class="pbtn ghost" href="{{ a.maps_url }}" target="_blank" rel="noopener">🗺️ Mapa</a>{% endif %}
      {% if a.site_url %}<a class="pbtn ghost" href="{{ a.site_url }}" target="_blank" rel="noopener">🌐 Site</a>{% endif %}
      <span style="flex:1"></span>
      {% if not vende_servico %}<button type="button" class="pbtn" disabled title="Disponível pra empresas que vendem serviço">📄 Gerar orçamento</button>
      {% elif a.orcamento_id %}<a class="pbtn" href="/painel/servicos?abrir={{ a.orcamento_id }}">📄 Ver orçamento</a>
      {% else %}<form method="post" action="/painel/prospeccao/{{ a.id }}/orcamento" style="margin:0"><button class="pbtn">📄 Gerar orçamento</button></form>{% endif %}
      {% if a.site_url %}<button type="button" class="pbtn ghost" id="enrqf-btn" onclick="enrqLead({{ a.id }})" title="Raspa o site e descobre e-mail, Instagram e WhatsApp">🔎 Verificar canais</button>{% endif %}
      {% if a.email %}<button type="button" class="pbtn ghost" id="cvz-btn" onclick="convidarZaq({{ a.id }})" title="Manda um e-mail com link pro cliente criar a conta no Zaq">🎟️ Convidar pro Zaq</button>{% endif %}
    </div>
    <div class="mut" id="enrqf-msg" style="font-size:.82rem;margin-top:.5rem"></div>
    {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
    <script>
    function convidarZaq(id){var b=document.getElementById('cvz-btn');if(b){b.disabled=true;b.textContent='Enviando…';}
      fetch('/painel/prospeccao/'+id+'/convidar-zaq',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
        if(!d.ok){if(b){b.disabled=false;b.textContent='🎟️ Convidar pro Zaq';}alert(d.erro||'Não consegui enviar.');return;}
        if(b){b.textContent='✓ Convite enviado';}}).catch(function(){if(b){b.disabled=false;b.textContent='🎟️ Convidar pro Zaq';}alert('Falha de rede.');});}
    function identificarNumero(){var b=document.getElementById('idn-btn'),m=document.getElementById('idn-msg'),n=document.getElementById('idn-num');
      var num=(n&&n.value||'').trim();if(!num){if(m){m.textContent='Digite o número com DDD.';m.style.color='#e0a33e';}return;}
      if(b){b.disabled=true;var t=b.textContent;b.textContent='Consultando…';}if(m){m.textContent='Consultando o titular na Credify…';m.style.color='';}
      var fd=new FormData();fd.append('numero',num);
      fetch('/painel/prospeccao/identificar-numero',{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
        if(b){b.disabled=false;b.textContent=t;}
        if(!d.ok){if(m){m.textContent=d.erro||'Não consegui.';m.style.color='#e0a33e';}return;}
        if(!d.titulares||!d.titulares.length){if(m){m.textContent='Nenhum titular encontrado.';m.style.color='#e0a33e';}return;}
        var h=d.titulares.map(function(t){var l='<b style=\\'color:var(--verde-claro)\\'>'+(t.nome||'—')+'</b>';
          if(t.cidade||t.uf)l+=' <span class=\\'mut\\'>· '+(t.cidade||'')+(t.uf?('/'+t.uf):'')+'</span>';
          if(t.tipo)l+=' <span class=\\'mut\\' style=\\'font-size:.72rem\\'>('+t.tipo+')</span>';return l;}).join('<br>');
        if(m){m.innerHTML=h;m.style.color='';}}).catch(function(){if(b){b.disabled=false;b.textContent=t;}if(m){m.textContent='Falha de rede.';m.style.color='#e0a33e';}});}
    function buscarDecisor(id){var b=document.getElementById('dec-btn'),m=document.getElementById('dec-msg');if(b){b.disabled=true;var t=b.textContent;b.textContent='Consultando…';}if(m){m.textContent='Consultando o quadro societário na Credify…';m.style.color='';}
      fetch('/painel/prospeccao/'+id+'/decisor-credify',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent=t;}
        if(!d.ok){if(m){m.textContent=d.erro||'Não consegui.';m.style.color='#e0a33e';}return;}
        var msg='Decisor: '+d.nome+(d.cargo?(' ('+d.cargo+')'):'');
        msg+=d.n_telefones?(' · '+d.n_telefones+' telefone(s)'):' · telefone não liberado na sua conta Credify';
        if(m){m.textContent=msg+' — recarregando…';m.style.color='var(--verde-claro)';}
        setTimeout(function(){location.reload();},1200);}).catch(function(){if(b){b.disabled=false;b.textContent=t;}if(m){m.textContent='Falha de rede.';m.style.color='#e0a33e';}});}
    function enrqLead(id){var b=document.getElementById('enrqf-btn'),m=document.getElementById('enrqf-msg');if(b){b.disabled=true;b.textContent='Verificando…';}if(m)m.textContent='Raspando o site…';
      fetch('/painel/prospeccao/'+id+'/enriquecer-canais',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent='🔎 Verificar canais';}
        if(!d.ok){if(m)m.textContent=d.erro||'Não consegui.';return;}
        var p=[];if(d.whatsapp)p.push('💬 '+d.whatsapp);if(d.email)p.push('✉️ '+d.email+(d.email_ok?' ✓':' (não validou)'));if(d.instagram)p.push('📸 '+d.instagram);
        if(m)m.textContent=p.length?('Achei: '+p.join('  ·  ')+' — recarregando…'):'Não achei canais no site.';
        if(p.length)setTimeout(function(){location.reload();},1400);}).catch(function(){if(b){b.disabled=false;b.textContent='🔎 Verificar canais';}if(m)m.textContent='Falha de rede.';});}
    </script>
  </div>

  <div class="fgrid">
    <div>
      {% if tem_ia and (a.email or a.whatsapp or a.telefone) %}
      <div class="fsec">
        <div class="sh"><b>✨ Primeiro contato</b><span class="mut" style="font-size:.74rem;font-weight:400">IA escreve · você revisa e envia</span></div>
        <div style="display:flex;gap:.5rem;flex-wrap:wrap">
          {% if a.email %}<button type="button" class="pbtn ghost" id="ia-btn-email" onclick="iaMsg('email')">✉️ E-mail com IA</button>{% endif %}
          {% if a.whatsapp or a.telefone %}<button type="button" class="pbtn ghost" id="ia-btn-wpp" onclick="iaMsg('whatsapp')">💬 WhatsApp com IA</button>{% endif %}
        </div>
        <div id="ia-box" style="display:none;margin-top:.7rem"></div>
      </div>
      {% endif %}
      <div class="fsec">
        <div class="sh"><b>Dados</b>
          <div style="display:flex;gap:.4rem">
            {% set _end_lead = (a.receita.endereco if a.receita else None) or a.obs or ((a.cidade or '') ~ ('/' ~ a.uf if a.uf else '')) %}
            {% if a.cnpj %}<form method="post" action="/painel/prospeccao/{{ a.id }}/enriquecer" style="margin:0"><button class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" title="Puxar dados da Receita (CNPJá/BrasilAPI)">↻ atualizar</button></form>
              {% if tem_cnpja %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" data-endereco="{{ _end_lead }}" onclick="acharCnpj({{ a.id }},this)" title="Buscar outro CNPJ (trocar)">🔎 trocar</button>{% endif %}
              <form method="post" action="/painel/prospeccao/{{ a.id }}/limpar-cnpj" style="margin:0" onsubmit="return confirm('Remover o CNPJ e os dados da Receita deste lead?')"><button class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" title="Remover o CNPJ (escolhido errado)">🗑 limpar</button></form>
            {% elif tem_cnpja %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" data-endereco="{{ _end_lead }}" onclick="acharCnpj({{ a.id }},this)" title="Achar o CNPJ por nome+cidade (CNPJá)">🔎 achar CNPJ</button>
            {% else %}<a class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" target="_blank" rel="noopener" title="Achar o CNPJ na web (nome + cidade)" href="https://www.google.com/search?q={{ (a.empresa ~ ' ' ~ (a.cidade or '') ~ ' cnpj')|urlencode }}">🔎 achar CNPJ</a>{% endif %}
            {% if a.cnpj and tem_credify %}<button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" id="dec-btn" onclick="buscarDecisor({{ a.id }})" title="Descobre o sócio-administrador (decisor) pelo CNPJ via Credify — consulta paga">🕵️ {% if a.decisor_nome %}Atualizar decisor{% else %}Buscar decisor{% endif %}</button>{% endif %}
            <button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" onclick="prospToggle('edit-dados')">editar</button>
          </div>
        </div>
        <div class="mut" id="dec-msg" style="font-size:.8rem;margin:.1rem 0"></div>
        <div id="cnpj-cands" style="margin:.2rem 0"></div>
        {% if a.decisor_nome %}
        <div class="drow" style="align-items:flex-start">
          <span class="ic">🕵️</span><span class="lb">Decisor</span>
          <span style="flex:1"><b>{{ a.decisor_nome }}</b>{% if a.decisor_cargo %} · <span class="mut">{{ a.decisor_cargo }}</span>{% endif %}<span class="badge" style="margin-left:.3rem">Credify</span>
            {% if a.decisor_telefones %}
              {% for t in a.decisor_telefones %}
              <div style="margin-top:.3rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
                <span style="color:var(--verde-claro)">{{ t.formatado }}</span>
                {% if t.tipo_rot %}<span class="mut" style="font-size:.74rem">{{ t.tipo_rot }}</span>{% endif %}
                {% if t.whatsapp %}<span class="badge" style="background:#10241a;border-color:#1e4a34;color:#3ddc84">WhatsApp</span>{% endif %}
                {% if t.tel_link %}<a href="{{ t.tel_link }}" style="color:var(--txt-mut);font-size:.78rem">ligar</a>{% endif %}
                {% if t.zap_link %}<a href="{{ t.zap_link }}" target="_blank" rel="noopener" style="color:#3ddc84;font-size:.78rem">abrir WhatsApp</a>{% endif %}
              </div>
              {% endfor %}
            {% elif a.decisor_telefone %}<br><span style="color:var(--verde-claro)">{{ a.decisor_telefone }}</span>
              {% if a.decisor_tel_link %} · <a href="{{ a.decisor_tel_link }}" style="color:var(--verde-claro)">ligar</a>{% endif %}
              {% if a.decisor_zap %} · <a href="{{ a.decisor_zap }}" target="_blank" rel="noopener" style="color:#3ddc84">WhatsApp</a>{% endif %}
            {% else %}<br><span class="mut" style="font-size:.78rem">telefone indisponível (consulta de telefone não liberada na Credify)</span>{% endif %}
          </span>
        </div>
        {% endif %}
        {% if tem_credify %}
        <div class="drow" style="align-items:flex-start">
          <span class="ic">🔎</span><span class="lb">Identificar nº</span>
          <span style="flex:1">
            <div style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center">
              <input class="fld" id="idn-num" inputmode="tel" placeholder="DDD+número (86981885930)" style="flex:1;min-width:150px;max-width:240px">
              <button type="button" class="pbtn ghost" id="idn-btn" style="padding:.35rem .7rem;font-size:.78rem" onclick="identificarNumero()">Consultar titular</button>
            </div>
            <div class="mut" id="idn-msg" style="font-size:.78rem;margin-top:.3rem">Descobre em que nome um telefone está cadastrado (Credify · consulta paga).</div>
          </span>
        </div>
        {% endif %}
        {% if a.contato %}<div class="drow"><span class="ic">👤</span><span class="lb">Contato</span><span>{{ a.contato }}{% if a.cargo %} · {{ a.cargo }}{% endif %}</span></div>{% endif %}
        {% if a.cnpj %}<div class="drow"><span class="ic">🏢</span><span class="lb">CNPJ</span><span>{{ a.cnpj }}</span></div>{% endif %}
        {% if a.socio %}<div class="drow"><span class="ic">🧑‍💼</span><span class="lb">Sócio</span><span>{{ a.socio }}</span></div>{% endif %}
        {% if a.regime_tributario or a.porte %}<div class="drow"><span class="ic">📑</span><span class="lb">Regime</span><span>{{ a.regime_tributario or '—' }}{% if a.porte %} · porte {{ a.porte }}{% endif %}</span></div>{% endif %}
        {% if a.telefone %}<div class="drow"><span class="ic">📞</span><span class="lb">Telefone</span><span>{{ a.telefone }}</span></div>{% endif %}
        {% if a.whatsapp %}<div class="drow"><span class="ic">💬</span><span class="lb">WhatsApp</span><span>{{ a.whatsapp }}<span class="badge">Business?</span></span></div>{% endif %}
        {% if a.email %}<div class="drow"><span class="ic">✉️</span><span class="lb">E-mail</span><span>{{ a.email }}</span></div>{% endif %}
        {% if a.instagram %}<div class="drow"><span class="ic">📷</span><span class="lb">Instagram</span><span>{{ a.instagram }}</span></div>{% endif %}
        {% if a.site_url %}<div class="drow"><span class="ic">🌐</span><span class="lb">Site</span><span><a href="{{ a.site_url }}" target="_blank" rel="noopener" style="color:var(--verde-claro)">{{ a.site_dominio or a.site_url }}</a> · <span class="mut" style="font-size:.78rem">ver página</span></span></div>{% elif a.tem_site is not none %}<div class="drow"><span class="ic">🌐</span><span class="lb">Site</span><span>{% if a.tem_site %}tem site{% else %}<span style="color:#e0574f">não tem</span>{% endif %}</span></div>{% endif %}
        {% if a.valor %}<div class="drow"><span class="ic">💰</span><span class="lb">Valor est.</span><span style="color:var(--verde-claro)">{{ brl(a.valor) }}</span></div>{% endif %}
        {% if a.proximo_contato_em %}<div class="drow"><span class="ic">📅</span><span class="lb">Próximo</span><span style="color:var(--verde-claro)">{{ a.proximo_contato_em.strftime('%d/%m/%Y') }}</span></div>{% endif %}
        {% if a.obs %}<div class="drow"><span class="ic">📝</span><span class="lb">Obs</span><span>{{ a.obs }}</span></div>{% endif %}
        {% if a.receita %}
        <div style="margin-top:.6rem;border-top:1px solid var(--borda);padding-top:.5rem">
          <div class="lb" style="text-transform:uppercase;letter-spacing:.03em;margin-bottom:.2rem">🧾 Receita Federal{% if a.receita.fonte %} · <span style="opacity:.7">{{ a.receita.fonte }}</span>{% endif %}</div>
          {% if a.receita.nome_fantasia %}<div class="drow"><span class="ic">🏷️</span><span class="lb">Fantasia</span><span>{{ a.receita.nome_fantasia }}</span></div>{% endif %}
          {% if a.receita.situacao %}<div class="drow"><span class="ic">📌</span><span class="lb">Situação</span><span>{{ a.receita.situacao }}</span></div>{% endif %}
          {% if a.receita.abertura %}<div class="drow"><span class="ic">📆</span><span class="lb">Abertura</span><span>{{ a.receita.abertura }}</span></div>{% endif %}
          {% if a.receita.capital_social %}<div class="drow"><span class="ic">🏦</span><span class="lb">Capital</span><span>{{ a.receita.capital_social }}</span></div>{% endif %}
          {% if a.receita.natureza %}<div class="drow"><span class="ic">⚖️</span><span class="lb">Natureza</span><span>{{ a.receita.natureza }}</span></div>{% endif %}
          {% if a.receita.inscricao_estadual %}<div class="drow"><span class="ic">🧾</span><span class="lb">Insc. Est.</span><span>{{ a.receita.inscricao_estadual }}</span></div>{% endif %}
          {% if a.receita.endereco %}<div class="drow"><span class="ic">📍</span><span class="lb">Endereço</span><span>{{ a.receita.endereco }}</span></div>{% endif %}
          {% if a.receita.atividades_secundarias %}<div class="drow"><span class="ic">🔧</span><span class="lb">Outras ativ.</span><span>{{ a.receita.atividades_secundarias|join(' · ') }}</span></div>{% endif %}
        </div>
        {% endif %}
        {% if not (a.contato or a.cnpj or a.socio or a.telefone or a.whatsapp or a.email or a.instagram or a.valor) %}
          <div class="mut" style="font-size:.82rem">Sem dados ainda. Clique em <b>editar</b> pra preencher — ou preencha o CNPJ e use <b>↻ atualizar</b> pra puxar da Receita.</div>{% endif %}

        <form id="edit-dados" method="post" action="/painel/prospeccao/{{ a.id }}/editar" style="display:none;margin-top:.8rem;border-top:1px solid var(--borda);padding-top:.8rem">
          <div class="egrid">
            <div class="full"><label class="lbl">CNPJ <span class="mut" style="font-weight:400">— cole aqui e puxe tudo da Receita</span></label><div style="display:flex;gap:.3rem"><input class="fld" name="cnpj" inputmode="numeric" placeholder="00.000.000/0000-00" value="{{ a.cnpj or '' }}"><button type="button" class="pbtn ghost" style="padding:.5rem .6rem;white-space:nowrap" onclick="fichaCnpj()" title="Preencher tudo pela Receita">↓ Receita</button></div></div>
            <div><label class="lbl">Contato</label><input class="fld" name="contato" value="{{ a.contato or '' }}"></div>
            <div><label class="lbl">Cargo</label><input class="fld" name="cargo" value="{{ a.cargo or '' }}"></div>
            <div><label class="lbl">Telefone</label><input class="fld" name="telefone" value="{{ a.telefone or '' }}"></div>
            <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp" value="{{ a.whatsapp or '' }}"></div>
            <div><label class="lbl">E-mail</label><input class="fld" name="email" value="{{ a.email or '' }}"></div>
            <div><label class="lbl">Segmento</label><input class="fld" name="segmento" value="{{ a.segmento or '' }}"></div>
            <div><label class="lbl">Cidade</label><input class="fld" name="cidade" value="{{ a.cidade or '' }}"></div>
            <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" value="{{ a.uf or '' }}"></div>
            <div><label class="lbl">Sócio</label><input class="fld" name="socio" value="{{ a.socio or '' }}"></div>
            <div><label class="lbl">Regime</label><input class="fld" name="regime_tributario" value="{{ a.regime_tributario or '' }}"></div>
            <div><label class="lbl">Porte</label><input class="fld" name="porte" value="{{ a.porte or '' }}"></div>
            <div><label class="lbl">Instagram</label><input class="fld" name="instagram" value="{{ a.instagram or '' }}"></div>
            <div><label class="lbl">Valor est. (R$)</label><input class="fld" name="valor" inputmode="decimal" value="{{ (a.valor/100)|n2 if a.valor else '' }}"></div>
            <div><label class="lbl">Tem site?</label><select class="fld" name="tem_site">
              <option value="" {% if a.tem_site is none %}selected{% endif %}>—</option>
              <option value="1" {% if a.tem_site is true %}selected{% endif %}>Tem</option>
              <option value="0" {% if a.tem_site is false %}selected{% endif %}>Não tem</option></select></div>
            <div class="full"><label class="lbl">Site (link)</label><input class="fld" name="site_url" inputmode="url" placeholder="https://…" value="{{ a.site_url or '' }}"></div>
            <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs" value="{{ a.obs or '' }}"></div>
          </div>
          <div style="display:flex;gap:.5rem;margin-top:.7rem"><button class="pbtn" style="margin:0">Salvar dados</button><button type="button" class="pbtn ghost" onclick="prospToggle('edit-dados')">Cancelar</button></div>
        </form>
      </div>

      <div class="fsec">
        <div class="sh"><b>Temperatura</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/temperatura" style="margin:0">
          <div class="rcpills">{% for t,rot in temperaturas %}<button type="submit" name="temperatura" value="{{ t }}" class="rcpill {% if t==a.temperatura %}on{% endif %}" style="{% if t==a.temperatura %}background:{{ temp_cor[t] }};border-color:{{ temp_cor[t] }}{% endif %}">{{ rot }}</button>{% endfor %}</div>
        </form>
        {% if pode_atribuir %}
        <form method="post" action="/painel/prospeccao/{{ a.id }}/atribuir" style="margin-top:.6rem">
          <label class="lbl">Vendedor responsável</label>
          <select class="fld" name="vendedor_id" onchange="this.form.submit()"><option value="">— sem responsável —</option>{% for v in vendedores %}<option value="{{ v.id }}" {% if v.id==a.vendedor_id %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}</select>
        </form>{% endif %}
      </div>
    </div>

    <div>
      <div class="fsec">
        <div class="sh"><b>Registrar contato</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/contato" style="margin:0">
          <div class="rcpills" id="rc-pills">{% for t,rot in tipos %}<button type="button" class="rcpill {% if loop.first %}on{% endif %}" data-tipo="{{ t }}" onclick="rcPick(this)">{{ rot }}</button>{% endfor %}</div>
          <input type="hidden" name="tipo" id="rc-tipo" value="ligacao">
          <textarea class="fld" name="descricao" rows="2" placeholder="O que rolou nesse contato?"></textarea>
          <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.6rem">
            <select class="fld" name="resultado" style="width:auto">{% for r,rot in resultados %}<option value="{{ r }}">{{ rot if r else 'Resultado…' }}</option>{% endfor %}</select>
            <label class="chipin">📅 Próximo <input type="date" name="proximo"></label>
            <span style="flex:1"></span>
            <button class="pbtn" style="margin:0">✓ Salvar contato</button>
          </div>
        </form>
      </div>

      <div class="fsec">
        <div class="sh"><b>Histórico</b></div>
        {% if not timeline %}<p class="mut" style="margin:.2rem 0 0">Nenhum contato registrado ainda.</p>{% endif %}
        {% for ev in timeline %}
        <div class="tl"><span class="dt" style="background:{{ ev.cor }}"></span>
          <div style="font-size:.86rem"><b>{{ ev.tipo_rot }}</b>{% if ev.resultado_rot %} — <span class="mut">{{ ev.resultado_rot }}</span>{% endif %}</div>
          {% if ev.descricao %}<div style="font-size:.83rem;margin-top:.12rem">{{ ev.descricao }}</div>{% endif %}
          <div class="mut" style="font-size:.72rem;margin-top:.2rem">{{ ev.criado_em.strftime('%d/%m/%Y %H:%M') if ev.criado_em else '' }}{% if ev.quem %} · {{ ev.quem }}{% endif %}{% if ev.agendado_para %} · próximo {{ ev.agendado_para.strftime('%d/%m') }}{% endif %}</div>
        </div>{% endfor %}
      </div>
    </div>
  </div>
</div>
<script>
function prospToggle(id){var e=document.getElementById(id);e.style.display=(e.style.display==='none')?'block':'none';}
function rcPick(el){var box=document.getElementById('rc-pills');box.querySelectorAll('.rcpill').forEach(function(b){b.classList.remove('on');});el.classList.add('on');document.getElementById('rc-tipo').value=el.getAttribute('data-tipo');}
function fToast(msg){var t=document.getElementById('f-toast');if(!t){t=document.createElement('div');t.id='f-toast';t.style.cssText='position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--card);border:1px solid var(--verde);color:var(--verde-claro);padding:.6rem 1rem;border-radius:10px;z-index:200;font-size:.85rem;box-shadow:0 6px 20px rgba(0,0,0,.4);transition:opacity .4s';document.body.appendChild(t);}t.textContent=msg;t.style.opacity='1';clearTimeout(window._ft);window._ft=setTimeout(function(){t.style.opacity='0';},2600);}
function cnpjManualBox(id){
  // caixa pra colar o CNPJ achado por fora (Google) e aplicar direto — mesma
  // rota do "usar" (grava + puxa a Receita; o backend valida os 14 dígitos).
  // Sem precisar abrir "editar".
  return '<form method="post" action="/painel/prospeccao/'+id+'/aplicar-cnpj" '
    +'style="display:flex;gap:.3rem;align-items:center;margin-top:.5rem;border-top:1px dashed var(--borda);padding-top:.5rem">'
    +'<span class="mut" style="font-size:.76rem;white-space:nowrap">Já tem o CNPJ?</span>'
    +'<input class="fld" name="cnpj" inputmode="numeric" placeholder="cole aqui" style="flex:1;padding:.4rem .55rem">'
    +'<button class="pbtn" style="padding:.35rem .8rem;font-size:.78rem;margin:0">usar</button></form>';
}
function acharCnpj(id,btn){var box=document.getElementById('cnpj-cands');var endLead=(btn&&btn.getAttribute('data-endereco'))||'';box.innerHTML='<div class="mut" style="font-size:.8rem">Procurando CNPJ…</div>';
  fetch('/painel/prospeccao/'+id+'/buscar-cnpj',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    var webBtn=d.web?('<a class="pbtn" style="padding:.35rem .8rem;font-size:.8rem;margin-top:.4rem;display:inline-flex" target="_blank" rel="noopener" href="'+d.web+'">🔎 buscar na web</a>'):'';
    var manual=cnpjManualBox(id);
    if(!d.ok){box.innerHTML='<div class="mut" style="font-size:.8rem;color:#e0a33e">Não achei ('+(d.erro||'?')+').</div>'+webBtn+manual;return;}
    if(!d.itens||!d.itens.length){box.innerHTML='<div class="mut" style="font-size:.8rem">Nenhum CNPJ nessa cidade pra esse nome.</div>'+webBtn+manual;return;}
    var h='';
    if(endLead){h+='<div class="mut" style="font-size:.76rem;margin:.1rem 0 .3rem">📍 Endereço do lead: <b>'+jsEsc(endLead)+'</b> — escolha o que bate:</div>';}
    else{h+='<div class="lb" style="margin:.2rem 0">Escolha a empresa certa (confira o endereço):</div>';}
    h+='<div class="rlist">';
    d.itens.forEach(function(it){
      var loc=(it.cidade?(jsEsc(it.cidade)+(it.uf?('/'+it.uf):'')):'');
      h+='<form method="post" action="/painel/prospeccao/'+id+'/aplicar-cnpj" class="rrow" style="cursor:pointer;gap:.5rem"><input type="hidden" name="cnpj" value="'+it.cnpj+'">'
        +'<span style="flex:1"><b style="font-size:.85rem">'+jsEsc(it.razao_social||it.nome_fantasia||it.cnpj)+'</b>'
        +'<span class="mut" style="font-size:.74rem"> · '+it.cnpj+(it.situacao?(' · '+jsEsc(it.situacao)):'')+'</span>'
        +(it.endereco?('<span class="mut" style="display:block;font-size:.74rem">📍 '+jsEsc(it.endereco)+(loc?(' · '+loc):'')+'</span>'):(loc?('<span class="mut" style="display:block;font-size:.74rem">📍 '+loc+'</span>'):''))
        +'</span><button class="pbtn" style="padding:.3rem .7rem;font-size:.78rem;margin:0">usar</button></form>';
    });
    h+='</div>';
    if(d.web){h+='<div style="margin-top:.4rem"><a class="mut" style="font-size:.76rem" target="_blank" rel="noopener" href="'+d.web+'">nenhuma bate? buscar na web →</a></div>';}
    h+=manual;
    box.innerHTML=h;
  }).catch(function(){box.innerHTML='<div class="mut" style="font-size:.8rem;color:#e0a33e">Falha de rede.</div>'+cnpjManualBox(id);});}
function jsEsc(s){return (s||'').replace(/[&<>"]/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function fichaCnpj(){var f=document.getElementById('edit-dados');var cnpj=f.querySelector('[name=cnpj]').value.replace(/\\D/g,'');if(cnpj.length!==14){fToast('CNPJ precisa ter 14 dígitos');return;}
  fToast('Consultando Receita…');
  fetch('/painel/prospeccao/cnpj?cnpj='+cnpj,{headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){fToast('CNPJ não encontrado ('+(d.erro||'')+')');return;}var x=d.dados;
    function put(n,v){var el=f.querySelector('[name='+n+']');if(el&&v)el.value=v;}
    var e=f.querySelector('[name=empresa]');
    put('segmento',x.segmento);put('cidade',x.cidade);put('uf',x.uf);put('telefone',x.telefone);
    put('email',x.email);put('socio',x.socio);put('regime_tributario',x.regime_tributario);put('porte',x.porte);
    fToast('Preenchido pela Receita ✓ confira e salve');
  }).catch(function(){fToast('Falha de rede');});}
function iaMsg(canal){var box=document.getElementById('ia-box');var eb=document.getElementById('ia-btn-email'),wb=document.getElementById('ia-btn-wpp');
  if(eb)eb.disabled=true;if(wb)wb.disabled=true;box.style.display='block';box.innerHTML='<div class="mut" style="font-size:.82rem">✨ Gerando com IA…</div>';
  var fd=new FormData();fd.append('canal',canal);
  fetch('/painel/prospeccao/{{ a.id }}/mensagem-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(eb)eb.disabled=false;if(wb)wb.disabled=false;
    if(!d.ok){box.innerHTML='<div class="mut" style="color:#e0a33e;font-size:.82rem">'+(d.erro==='sem_ia'?'IA não configurada (falta a chave da IA).':'Não consegui gerar ('+(d.erro||'?')+').')+'</div>';return;}
    if(d.canal==='whatsapp'){
      box.innerHTML='<label class="lbl">Mensagem de WhatsApp</label><textarea class="fld" id="ia-texto" rows="5"></textarea>'
        +'<div style="display:flex;gap:.5rem;margin-top:.5rem;flex-wrap:wrap"><button type="button" class="pbtn" onclick="iaWhats()">💬 Abrir no WhatsApp</button>'
        +'<button type="button" class="pbtn ghost" onclick="iaMsg(&quot;whatsapp&quot;)">↻ gerar outra</button></div>'
        +'<div class="mut" style="font-size:.74rem;margin-top:.35rem">Abre seu WhatsApp com o texto pronto e registra no histórico.</div>';
      document.getElementById('ia-texto').value=d.texto||'';box.setAttribute('data-link',d.link||'');
    }else{
      box.innerHTML='<label class="lbl">Assunto</label><input class="fld" id="ia-assunto">'
        +'<label class="lbl" style="margin-top:.5rem">Mensagem</label><textarea class="fld" id="ia-corpo" rows="8"></textarea>'
        +'<div style="display:flex;gap:.5rem;margin-top:.5rem;flex-wrap:wrap"><button type="button" class="pbtn" onclick="iaSendEmail()">✉️ Enviar e-mail</button>'
        +'<button type="button" class="pbtn ghost" onclick="iaMsg(&quot;email&quot;)">↻ gerar outro</button></div>'
        +'<div class="mut" style="font-size:.74rem;margin-top:.35rem">Envia pra {{ a.email }} · resposta volta pro seu e-mail · registra no histórico.</div>';
      document.getElementById('ia-assunto').value=d.assunto||'';document.getElementById('ia-corpo').value=d.corpo||'';
    }
  }).catch(function(){if(eb)eb.disabled=false;if(wb)wb.disabled=false;box.innerHTML='<div class="mut" style="color:#e0a33e">Falha de rede.</div>';});}
function iaSendEmail(){var a=document.getElementById('ia-assunto').value,c=document.getElementById('ia-corpo').value;if(!c.trim()){fToast('Escreva a mensagem');return;}
  fToast('Enviando…');var fd=new FormData();fd.append('assunto',a);fd.append('corpo',c);
  fetch('/painel/prospeccao/{{ a.id }}/enviar-email',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){fToast(d.erro==='envio_falhou'?'Não consegui enviar (confira a config de e-mail).':d.erro==='sem_email'?'Lead sem e-mail.':'Erro ao enviar');return;}
    fToast('E-mail enviado ✓');setTimeout(function(){location.reload();},900);}).catch(function(){fToast('Falha de rede');});}
function iaWhats(){var box=document.getElementById('ia-box');var base=(box.getAttribute('data-link')||'').split('?text=')[0];var t=document.getElementById('ia-texto').value;
  if(base)window.open(base+'?text='+encodeURIComponent(t),'_blank');
  var fd=new FormData();fd.append('texto',t);
  fetch('/painel/prospeccao/{{ a.id }}/registrar-whatsapp',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){return r.json();}).then(function(){fToast('Registrado no histórico ✓');setTimeout(function(){location.reload();},900);}).catch(function(){});}
</script>
{% endblock %}"""

_COMUNICACAO_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<style>
.cx-wrap{max-width:1180px;margin:0 auto;padding:0 .3rem}
.cx-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.cx-head code{background:var(--card);border:1px solid var(--borda);border-radius:6px;padding:.12rem .45rem;color:var(--verde-claro);font-size:.82rem}
.cx-filtros{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
.cx-grid{display:grid;grid-template-columns:300px 1fr 280px;gap:.7rem;align-items:start}
.cx-list{border:1px solid var(--borda);border-radius:12px;background:var(--card);overflow:hidden;max-height:72vh;overflow-y:auto}
.cx-conv{display:flex;gap:.6rem;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--borda);padding:.7rem .75rem;cursor:pointer;color:var(--txt)}
.cx-conv:hover{background:#141416}
.cx-conv.on{background:#12271f}
.cx-conv .av{width:36px;height:36px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.78rem;background:#1d3a30;color:var(--verde-claro)}
.cx-conv .mid{flex:1;min-width:0}
.cx-conv .nm{display:flex;justify-content:space-between;gap:.4rem;align-items:baseline}
.cx-conv .nm b{font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cx-conv .nm .t{color:var(--txt-mut);font-size:.7rem;white-space:nowrap}
.cx-conv .pre{color:var(--txt-mut);font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:.15rem}
.cx-cn{font-size:.66rem;padding:.05rem .4rem;border-radius:999px;border:1px solid;margin-top:.25rem;display:inline-block}
.cn-mail{color:var(--azul,#5b9bd5);border-color:#2f4a63;background:#14212e}
.cn-wpp{color:#3ddc84;border-color:#1e4a34;background:#10241a}
.cx-thread,.cx-ctx{border:1px solid var(--borda);border-radius:12px;background:var(--card);min-height:40vh}
.cx-thread{display:flex;flex-direction:column;max-height:72vh}
.cx-empty{padding:2.4rem 1rem;text-align:center;color:var(--txt-mut);font-size:.9rem}
.cx-th{display:flex;align-items:center;gap:.6rem;padding:.7rem .85rem;border-bottom:1px solid var(--borda)}
.cx-th b{font-size:.92rem}
.cx-th small{display:block;color:var(--txt-mut);font-size:.75rem}
.cx-msgs{flex:1;overflow-y:auto;padding:.9rem;display:flex;flex-direction:column;gap:.6rem}
.cx-day{align-self:center;color:#6c6c68;font-size:.72rem}
.cx-m{max-width:82%;align-self:flex-end;background:#123028;border:1px solid #1d5741;border-radius:12px;border-bottom-right-radius:4px;padding:.5rem .7rem;font-size:.86rem;line-height:1.45}
.cx-m .cab{color:var(--txt-mut);font-size:.72rem;margin-bottom:.25rem}
.cx-m .meta{display:block;color:var(--txt-mut);font-size:.68rem;margin-top:.3rem;text-align:right}
.cx-comp{border-top:1px solid var(--borda);padding:.6rem .7rem;display:flex;gap:.5rem;align-items:flex-end;background:#101011}
.cx-comp textarea{flex:1;resize:none;background:var(--bg);border:1px solid var(--borda);color:var(--txt);border-radius:9px;padding:.5rem .6rem;font:inherit;font-size:.86rem}
.cx-stub{border-top:1px solid var(--borda);padding:.7rem .85rem;background:#101011;color:var(--txt-mut);font-size:.8rem}
.cx-stub .lbl2{display:inline-block;font-size:.62rem;padding:.05rem .4rem;border-radius:999px;background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.3rem}
.cx-ctx{padding:.85rem}
.cx-sec{margin-bottom:.9rem}
.cx-sec h4{margin:0 0 .4rem;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut)}
.cx-kv{display:flex;justify-content:space-between;gap:.5rem;font-size:.82rem;padding:.18rem 0}
.cx-kv span{color:var(--txt-mut)}
.cn-msg{color:#4a9cff;border-color:#274a73;background:#0f1e30}
.cn-ig{color:#f083b0;border-color:#5c2946;background:#2a1420}
.cx-tabs{display:flex;gap:.2rem;border-bottom:1px solid var(--borda);margin:.7rem 0 0;overflow-x:auto}
.cx-tab{padding:.6rem .85rem;font-size:.88rem;color:var(--txt-mut);border-bottom:2px solid transparent;white-space:nowrap;text-decoration:none}
.cx-tab:hover{color:var(--txt)}
.cx-tab.on{color:var(--verde-claro);border-bottom-color:var(--verde)}
.cx-m.cin{align-self:flex-start;background:var(--card-2);border-color:var(--borda)}
.cx-m.cbot{background:#1c1428;border-color:#4a3163}
.cx-m .who{display:block;font-size:.64rem;color:#c9a3e0;margin-bottom:.15rem}
.cx-msgs{scroll-behavior:smooth}
.cx-undot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--verde);margin-left:.3rem;vertical-align:middle}
.cx-conv .av{transition:none}
.cx-tbl{margin-top:.8rem;border:1px solid var(--borda);border-radius:12px;overflow:hidden;background:var(--card)}
.cx-tbl table{width:100%;border-collapse:collapse;font-size:.86rem}
.cx-tbl th{text-align:left;font-weight:600;color:var(--txt-mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;padding:.6rem .8rem;background:var(--card-2);border-bottom:1px solid var(--borda)}
.cx-tbl td{padding:.6rem .8rem;border-top:1px solid var(--borda);vertical-align:top}
.cx-tbl tr:hover td{background:#191b1a}
.cx-cc{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:.8rem}
.cx-card{border:1px solid var(--borda);border-radius:14px;background:var(--card);padding:1rem}
.cx-card h3{margin:0 0 .4rem;font-size:.95rem}
.cx-stat{display:inline-flex;align-items:center;gap:.35rem;font-size:.76rem;padding:.15rem .5rem;border-radius:999px;border:1px solid;margin-left:.4rem}
.st-on{color:var(--verde-claro);border-color:#1d5741;background:#0f231b}
.st-off{color:#e0a33e;border-color:#5a4520;background:#241d10}
.cx-env{font-family:ui-monospace,Menlo,monospace;font-size:.76rem;background:var(--bg);border:1px solid var(--borda);border-radius:8px;padding:.5rem .6rem;color:var(--txt-mut);margin-top:.5rem}
.cx-env b{color:var(--verde-claro)}
.waseg{display:grid;grid-template-columns:repeat(3,1fr);gap:.25rem;margin:.3rem 0 .75rem;background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.25rem}
.waseg label{font-size:.75rem;border-radius:7px;padding:.42rem .25rem;cursor:pointer;color:var(--txt-mut);text-align:center;line-height:1.15;display:flex;align-items:center;justify-content:center;transition:background .12s,color .12s}
.waseg label:hover{color:var(--txt)}
.waseg label.on{color:#fff;background:var(--verde);font-weight:600}
.waseg label input{display:none}
.waprov{display:none}
.waprov .lbl{margin-top:.5rem}
.sw{position:relative;display:inline-block;width:42px;height:24px;flex-shrink:0}
.sw input{opacity:0;width:0;height:0}
.sw span{position:absolute;inset:0;background:#333;border-radius:999px;cursor:pointer;transition:.15s}
.sw span::before{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;background:#eee;border-radius:50%;transition:.15s}
.sw input:checked+span{background:var(--verde)}
.sw input:checked+span::before{transform:translateX(18px);background:#04140d}
.agrow{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.55rem 0;border-top:1px solid var(--borda)}
.agrow:first-of-type{border-top:0}
.agrow .lab b{font-size:.88rem}.agrow .lab div{color:var(--txt-mut);font-size:.76rem;margin-top:.1rem}
.aggrid{display:grid;grid-template-columns:1fr 1fr;gap:.7rem;margin-top:.3rem}
.agfield label{display:block;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut);margin-bottom:.25rem}
.agfaq{border:1px solid var(--borda);border-radius:10px;padding:.55rem .65rem;margin-bottom:.45rem;background:var(--bg);display:flex;gap:.6rem;align-items:flex-start}
.agfaq .q b{font-size:.85rem}.agfaq .q p{margin:.15rem 0 0;color:var(--txt-mut);font-size:.8rem}
.tag-new{font-size:.6rem;padding:.05rem .4rem;border-radius:999px;background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.35rem}
@media(max-width:900px){.cx-grid{grid-template-columns:1fr}.cx-ctx{order:3}.cx-cc{grid-template-columns:1fr}.aggrid{grid-template-columns:1fr}}
</style>
<div class="cx-wrap">
  <div class="cx-head">
    <div>
      <h2 class="tt">📨 Comunicação</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">Enviando e-mails como {% if remetente %}<code>{{ remetente }}</code> · respostas voltam pro e-mail de quem enviou{% else %}<span style="color:#e0574f">(e-mail ainda não configurado)</span>{% endif %}</div>
    </div>
    <a class="pbtn ghost" href="/painel/prospeccao" style="display:inline-flex;align-items:center">‹ Prospecção</a>
  </div>

  <div class="cx-tabs">
    {% for k,rot in [('conversas','💬 Conversas'),('emails','✉️ E-mails'),('agente','🤖 Agente IA'),('canais','⚙️ Canais')] %}
    <a class="cx-tab {% if aba==k %}on{% endif %}" href="/painel/prospeccao/comunicacao?aba={{ k }}">{{ rot }}</a>{% endfor %}
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  {% if aba=='conversas' %}
  <form class="cx-filtros" method="get" action="/painel/prospeccao/comunicacao">
    <input type="hidden" name="aba" value="conversas">
    <select class="fld" name="canal" style="width:auto" onchange="this.form.submit()">
      <option value="" {% if not canal %}selected{% endif %}>Todos os mensageiros</option>
      <option value="whatsapp" {% if canal=='whatsapp' %}selected{% endif %}>💬 WhatsApp</option>
      <option value="messenger" {% if canal=='messenger' %}selected{% endif %}>🔵 Messenger</option>
      <option value="instagram" {% if canal=='instagram' %}selected{% endif %}>📸 Instagram</option>
    </select>
    {% if gerencia %}<select class="fld" name="vendedor" style="width:auto" onchange="this.form.submit()">
      <option value="">Todos os vendedores</option>
      {% for v in vendedores %}<option value="{{ v.id }}" {% if filtro_vend==(v.id|string) %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
    </select>{% endif %}
    <span class="mut" style="align-self:center;font-size:.8rem">{{ convs|length }} conversa(s)</span>
  </form>

  <div class="cx-grid">
    <div class="cx-list" id="cx-list">
      {% for c in convs %}
      <button type="button" class="cx-conv" id="cxc-{{ c.id }}" onclick="cxOpen(this,{{ c.id }})">
        <span class="av">{{ (c.empresa[:2]|upper) if c.empresa else '?' }}</span>
        <span class="mid">
          <span class="nm"><b>{{ c.empresa }}</b><span class="t">{{ c.quando.strftime('%d/%m') if c.quando else '' }}</span></span>
          <span class="pre">{{ c.quem }}: {{ c.preview }}</span>
          {% set cnc = {'whatsapp':'cn-wpp','email':'cn-mail','messenger':'cn-msg','instagram':'cn-ig'} %}
          <span class="cx-cn {{ cnc.get(c.canal,'cn-mail') }}">{{ c.canal_rot }}{% if c.n > 1 %} · {{ c.n }}{% endif %}</span>
        </span>
      </button>
      {% else %}
      <div class="cx-empty">Nenhuma comunicação ainda.<br><span style="font-size:.82rem">Envie um e-mail ou WhatsApp de 1º contato pela ficha de um lead — aparece aqui.</span></div>
      {% endfor %}
    </div>

    <div class="cx-thread" id="cx-thread">
      <div class="cx-empty">← Escolha uma conversa pra ver as mensagens.</div>
    </div>

    <div class="cx-ctx" id="cx-ctx">
      <div class="cx-empty" style="padding:1.4rem 1rem">Selecione uma conversa.</div>
    </div>
  </div>

  {% elif aba=='emails' %}
  <div style="display:flex;align-items:center;gap:.6rem;margin:.2rem 0 .7rem;flex-wrap:wrap">
    <button class="pbtn ghost" id="esync-btn" type="button" onclick="emailSync()">🔄 Sincronizar recebidos</button>
    <span class="mut" id="esync-msg" style="font-size:.82rem"></span>
    <span style="flex:1"></span>
    <span class="mut" style="font-size:.8rem">{{ convs|length }} conversa(s) de e-mail</span>
  </div>
  <div class="cx-grid">
    <div class="cx-list" id="cx-list">
      {% for c in convs %}
      <button type="button" class="cx-conv" id="cxc-{{ c.id }}" onclick="cxOpen(this,{{ c.id }})">
        <span class="av">{{ (c.empresa[:2]|upper) if c.empresa else '?' }}</span>
        <span class="mid">
          <span class="nm"><b>{{ c.empresa }}</b><span class="t">{{ c.quando.strftime('%d/%m') if c.quando else '' }}</span></span>
          <span class="pre">{{ c.quem }}: {{ c.preview }}</span>
          <span class="cx-cn cn-mail">✉️ E-mail{% if c.n > 1 %} · {{ c.n }}{% endif %}</span>
        </span>
      </button>
      {% else %}
      <div class="cx-empty">Nenhum e-mail ainda.<br><span style="font-size:.82rem">Os recebidos aparecem aqui após “Sincronizar recebidos”. Configure a caixa na aba <b>Canais</b>.</span></div>
      {% endfor %}
    </div>
    <div class="cx-thread" id="cx-thread"><div class="cx-empty">← Escolha um e-mail pra ler e responder.</div></div>
    <div class="cx-ctx" id="cx-ctx"><div class="cx-empty" style="padding:1.4rem 1rem">Selecione um e-mail.</div></div>
  </div>
  <script>
  function emailSync(){var b=document.getElementById('esync-btn'),m=document.getElementById('esync-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Sincronizando…';m.textContent='';
    fetch('/painel/prospeccao/comunicacao/email-sync',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
      if(!d.ok){m.textContent=d.erro||'Não consegui.';return;}
      if(d.novos){m.textContent='+'+d.novos+' novo(s) — recarregando…';setTimeout(function(){location.reload();},900);}else{m.textContent=d.detalhe||'Nenhum e-mail novo.';}}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';});}
  </script>

  {% elif aba=='agente' %}
  {% if not gerencia %}
  <div class="cx-card" style="margin-top:.8rem"><p class="mut" style="margin:0">Só o dono/gestor configura o Agente.</p></div>
  {% else %}
  {% if not tem_ia %}<div class="cx-card" style="margin-top:.8rem;border-color:#5a4520"><p class="mut" style="margin:0;color:#e0a33e">⚠️ A IA ainda não está configurada no ambiente (falta a chave). O agente só responde depois disso — mas você já pode deixar tudo configurado aqui.</p></div>{% endif %}
  <form method="post" action="/painel/prospeccao/comunicacao/agente-config" class="cx-cc" style="align-items:start">
    <div>
      <div class="cx-card">
        <div style="display:flex;align-items:center;gap:.7rem">
          <div style="font-size:1.6rem">🤖</div>
          <div style="flex:1"><b style="font-size:1rem">Agente de Atendimento</b><div class="mut" style="font-size:.8rem">Responde os leads, qualifica e te passa quando precisa.</div></div>
          <label class="sw"><input type="checkbox" name="ativo" {% if ag_cfg.ativo %}checked{% endif %}><span></span></label>
        </div>
      </div>
      <div class="cx-card">
        <h3>⚙️ Comportamento</h3>
        <div class="agfield" style="margin:.5rem 0"><label>Confiança mínima pra responder sozinho: <b style="color:#c9a3e0" id="lim-v">{{ ag_cfg.limiar_confianca }}%</b></label>
          <input type="range" name="limiar_confianca" min="50" max="95" value="{{ ag_cfg.limiar_confianca }}" style="width:100%;accent-color:#7b4fb0" oninput="document.getElementById('lim-v').textContent=this.value+'%'"></div>
        <div class="aggrid">
          <div class="agfield"><label>Responder em</label><select class="fld" name="horario"><option value="comercial" {% if ag_cfg.horario=='comercial' %}selected{% endif %}>Horário comercial</option><option value="24h" {% if ag_cfg.horario=='24h' %}selected{% endif %}>24 horas</option></select></div>
          <div class="agfield"><label>Tom</label><select class="fld" name="tom"><option value="informal" {% if ag_cfg.tom=='informal' %}selected{% endif %}>Informal</option><option value="formal" {% if ag_cfg.tom=='formal' %}selected{% endif %}>Formal</option></select></div>
          <div class="agfield"><label>Máx. respostas do bot antes de te chamar</label><input class="fld" name="max_trocas" inputmode="numeric" value="{{ ag_cfg.max_trocas }}"><small class="mut" style="font-size:.72rem">12 ou mais = praticamente sempre ativo (não passa pro humano por quantidade)</small></div>
          <div class="agfield"><label>Escalar para</label><select class="fld" name="escalar_para"><option value="dono_lead" {% if ag_cfg.escalar_para=='dono_lead' %}selected{% endif %}>Dono do lead</option><option value="plantao" {% if ag_cfg.escalar_para=='plantao' %}selected{% endif %}>Vendedor de plantão</option></select></div>
        </div>
      </div>
      <div class="cx-card">
        <h3>✅ O que ele faz sozinho</h3>
        <div class="agrow"><div class="lab"><b>Responder dúvidas frequentes</b><div>Usa a base de conhecimento ao lado</div></div><label class="sw"><input type="checkbox" name="pode_responder" {% if ag_cfg.pode_responder %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Qualificar o lead</b><div>Mede interesse e ajusta a temperatura</div></div><label class="sw"><input type="checkbox" name="pode_qualificar" {% if ag_cfg.pode_qualificar %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Agendar follow-up</b></div><label class="sw"><input type="checkbox" name="pode_agendar" {% if ag_cfg.pode_agendar %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Gerar orçamento prévio quando o cliente pedir<span class="tag-new">novo</span></b><div>Monta rascunho com serviços + preço e manda o link</div></div><label class="sw"><input type="checkbox" name="pode_orcamento" {% if ag_cfg.pode_orcamento %}checked{% endif %}><span></span></label></div>
        <div class="agrow"><div class="lab"><b>Oferecer orçamento proativamente</b><div>Sem o cliente pedir</div></div><label class="sw"><input type="checkbox" name="orcamento_proativo" {% if ag_cfg.orcamento_proativo %}checked{% endif %}><span></span></label></div>
      </div>
      <div style="display:flex;justify-content:flex-end"><button class="pbtn">Salvar configuração</button></div>
    </div>
    <div>
      <div class="cx-card">
        <h3>🙋 Quando ele te passa (handoff)</h3>
        <div class="mut" style="font-size:.84rem;line-height:1.9">✓ O cliente pede pra falar com uma pessoa<br>✓ Sentimento negativo / reclamação<br>✓ Confiança abaixo do limiar<br>✓ Negociação de preço / fechamento<br>✓ Assunto fora do escopo dos serviços</div>
      </div>
    </div>
  </form>

  <div class="cx-card">
    <h3>🧠 Treinar o agente</h3>
    <p class="mut" style="font-size:.82rem;margin:.1rem 0 .6rem">Quanto melhor a base, melhor ele responde. Já funciona antes mesmo do WhatsApp.</p>
    <form method="post" action="/painel/prospeccao/comunicacao/agente-instrucoes">
      <div class="agfield"><label>Sobre a empresa / instruções gerais</label>
        <textarea class="fld" name="texto" rows="3" placeholder="Ex: Somos a X, ajudamos comércios de Teresina a... Nunca prometa prazo/desconto sem confirmar com o vendedor.">{{ ag_conhec.instrucoes }}</textarea></div>
      <div style="display:flex;justify-content:flex-end;margin-top:.4rem"><button class="pbtn ghost">Salvar instruções</button></div>
    </form>
    <div style="border-top:1px solid var(--borda);margin:.8rem 0;padding-top:.7rem">
      <label class="agfield" style="display:block;margin-bottom:.4rem"><span style="font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut)">Perguntas & respostas</span></label>
      {% for q in ag_conhec.faqs %}
      <div class="agfaq"><div class="q" style="flex:1"><b>{{ q.pergunta }}</b><p>{{ q.resposta }}</p></div>
        <form method="post" action="/painel/prospeccao/comunicacao/agente-faq" style="margin:0"><input type="hidden" name="excluir" value="{{ q.id }}"><button class="pbtn ghost" style="padding:.3rem .55rem;font-size:.76rem" title="Remover">🗑</button></form></div>
      {% else %}<p class="mut" style="font-size:.82rem">Nenhuma pergunta cadastrada ainda.</p>{% endfor %}
      <form method="post" action="/painel/prospeccao/comunicacao/agente-faq" style="margin-top:.5rem">
        <div class="aggrid">
          <div class="agfield"><label>Pergunta</label><input class="fld" name="pergunta" placeholder="Quanto custa?"></div>
          <div class="agfield"><label>Resposta</label><input class="fld" name="resposta" placeholder="A partir de R$149/mês. Posso montar um orçamento?"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-top:.4rem"><button class="pbtn">+ Adicionar pergunta</button></div>
      </form>
    </div>
  </div>
  {% endif %}

  {% else %}
  <p class="mut" style="margin:.8rem 0 0">Todos os canais num lugar só. As credenciais ficam no ambiente (Render); aqui você vê o status e conecta cada um quando o acesso libera.</p>
  <div class="cx-cc">
    <div class="cx-card">
      <h3>✉️ E-mail <span class="cx-stat {{ 'st-on' if canais.email else 'st-off' }}">● {{ 'Enviando' if canais.email else 'A configurar' }}</span></h3>
      <div class="cx-kv"><span>Enviar (De:)</span><b>{{ remetente or '—' }}</b></div>
      <div class="cx-kv"><span>Receber (IMAP)</span><b>{% if canais.email_rx %}<span style="color:var(--verde-claro)">✓ {{ canais.email_ident }}</span>{% else %}—{% endif %}</b></div>
      <div class="mut" style="font-size:.72rem">A empresa <b>envia e recebe</b> pelo próprio e-mail (a mesma caixa abaixo). Só cai no e-mail global se você não configurar nenhuma.</div>
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/email-config" style="margin-top:.6rem">
        <label class="lbl">Caixa pra RECEBER (endereço + senha de app)</label>
        <input class="fld" name="endereco" type="email" placeholder="voce@empresa.com" value="{{ canais.email_ident }}" style="margin-bottom:.35rem">
        <input class="fld" name="senha" type="password" placeholder="senha de app (deixe vazio p/ manter)" autocomplete="new-password" style="margin-bottom:.35rem">
        <input class="fld" name="host" placeholder="imap.gmail.com (padrão)" style="margin-bottom:.4rem">
        <div style="display:flex;gap:.4rem">
          <button class="pbtn" style="white-space:nowrap">Salvar</button>
          <button type="button" class="pbtn ghost" id="etest-btn" onclick="emailTestar()" style="white-space:nowrap">Testar conexão</button>
        </div>
        <div class="mut" id="etest-msg" style="font-size:.78rem;margin-top:.4rem"></div>
      </form>
      <div class="mut" style="margin-top:.4rem;font-size:.76rem">Gmail/Workspace: gere uma <b>senha de app</b> em myaccount.google.com/apppasswords e ligue o IMAP. A senha é guardada só pra esta empresa.</div>
      <script>
      function emailTestar(){var b=document.getElementById('etest-btn'),m=document.getElementById('etest-msg');if(!b)return;b.disabled=true;var t=b.textContent;b.textContent='Testando…';m.textContent='';m.style.color='';
        fetch('/painel/prospeccao/comunicacao/email-testar',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){b.disabled=false;b.textContent=t;
          m.textContent=d.msg||d.erro||'—';m.style.color=d.ok?'var(--verde-claro)':'#e0a33e';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';m.style.color='#e0a33e';});}
      </script>
      {% else %}<div class="mut" style="margin-top:.4rem;font-size:.8rem">SMTP (Google Workspace). Prospecção fria ✓</div>{% endif %}
    </div>
    <div class="cx-card">
      <h3>💬 WhatsApp <span class="cx-stat {{ 'st-on' if canais.whatsapp else 'st-off' }}">● {{ 'Conectado' if canais.whatsapp else 'A configurar' }}</span></h3>
      {% if gerencia %}
      <div class="mut" style="font-size:.8rem;margin-bottom:.1rem">Como este cliente conecta o WhatsApp:</div>
      <div class="waseg">
        <label class="{{ 'on' if canais.wa_provedor not in ['cloud','qr'] else '' }}"><input type="radio" name="wa_prov_sel" value="twilio" {{ 'checked' if canais.wa_provedor not in ['cloud','qr'] else '' }} onchange="waProv('twilio')">Twilio</label>
        <label class="{{ 'on' if canais.wa_provedor=='cloud' else '' }}"><input type="radio" name="wa_prov_sel" value="cloud" {{ 'checked' if canais.wa_provedor=='cloud' else '' }} onchange="waProv('cloud')">Número próprio</label>
        <label class="{{ 'on' if canais.wa_provedor=='qr' else '' }}"><input type="radio" name="wa_prov_sel" value="qr" {{ 'checked' if canais.wa_provedor=='qr' else '' }} onchange="waProv('qr')">QR Code</label>
      </div>

      <div id="wa-twilio" class="waprov">
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">Via Twilio (BSP)</div>
        <div class="mut" style="font-size:.78rem">Credenciais globais no Render:</div>
        <div class="cx-env"><b>TWILIO_ACCOUNT_SID</b>=•••••<br><b>TWILIO_AUTH_TOKEN</b>=•••••</div>
        <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.5rem">
          <input type="hidden" name="canal" value="whatsapp"><input type="hidden" name="provedor" value="twilio">
          <label class="lbl">Número desta empresa</label>
          <div style="display:flex;gap:.4rem">
            <input class="fld" name="numero" inputmode="tel" placeholder="+17602847678" value="{{ canais.numeros.get('whatsapp','') if canais.wa_provedor!='cloud' else '' }}">
            <button class="pbtn" style="white-space:nowrap">Salvar</button>
          </div>
        </form>
        <div class="mut" style="margin-top:.4rem;font-size:.78rem">No painel Twilio, aponte o webhook pra <code>/webhooks/twilio</code>.</div>
      </div>

      <div id="wa-cloud" class="waprov">
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">Cloud API oficial da Meta</div>
        <div class="mut" style="font-size:.78rem">Número <b>próprio</b> do cliente, direto na Meta — sem Twilio, grátis e sem risco de ban. Usa o mesmo app da Meta (env) + webhook <code>/webhooks/meta</code>.</div>
        <div style="font-size:.72rem;color:var(--txt-mut);background:#181a17;border:1px solid var(--borda);border-radius:8px;padding:.45rem .6rem;margin:.5rem 0">⚠️ Ao registrar o número na Cloud API ele <b>sai do app do celular</b> e passa a ser operado pelo sistema. Requer conta Meta Business + verificar o número.</div>
        <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.2rem">
          <input type="hidden" name="canal" value="whatsapp"><input type="hidden" name="provedor" value="cloud">
          <label class="lbl">Número (opcional, só p/ exibir)</label>
          <input class="fld" name="numero" inputmode="tel" placeholder="+5586999999999" value="{{ canais.numeros.get('whatsapp','') if canais.wa_provedor=='cloud' else '' }}" style="margin-bottom:.35rem">
          <label class="lbl">Phone Number ID (Cloud API)</label>
          <input class="fld" name="wa_phone_id" placeholder="ex.: 123456789012345" style="margin-bottom:.35rem">
          <label class="lbl">Access Token</label>
          <input class="fld" name="token" type="password" placeholder="token da Cloud API {% if canais.tokens_set.get('whatsapp') and canais.wa_provedor=='cloud' %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin-bottom:.45rem">
          <button class="pbtn">Conectar número próprio</button>
        </form>
        {% if canais.wa_provedor=='cloud' and canais.wa_phone_set %}
        <div style="border:1px solid var(--borda);border-radius:9px;padding:.55rem .6rem;margin-top:.6rem;background:var(--bg)">
          <div class="lbl" style="margin:0 0 .35rem">Testar envio</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center">
            <input class="fld" id="watest-num" inputmode="tel" placeholder="Seu nº (+55...)" style="flex:1;min-width:150px">
            <button type="button" class="pbtn ghost" id="watest-btn" onclick="waTestar()" style="white-space:nowrap">📤 Enviar teste</button>
          </div>
          <div class="mut" id="watest-msg" style="font-size:.78rem;margin-top:.35rem"></div>
        </div>
        <script>
        function waTestar(){var b=document.getElementById('watest-btn'),m=document.getElementById('watest-msg'),n=document.getElementById('watest-num');if(!b)return;
          var num=(n&&n.value||'').trim();b.disabled=true;var t=b.textContent;b.textContent='Enviando…';m.textContent='';m.style.color='';
          var fd=new FormData();fd.append('numero',num);
          fetch('/painel/prospeccao/comunicacao/whatsapp-testar',{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
            b.disabled=false;b.textContent=t;m.textContent=d.msg||d.erro||'—';m.style.color=d.ok?'var(--verde-claro)':'#e0a33e';}).catch(function(){b.disabled=false;b.textContent=t;m.textContent='Falha de rede.';m.style.color='#e0a33e';});}
        </script>
        {% endif %}
        <div class="mut" style="margin-top:.4rem;font-size:.78rem">Pega o <b>Phone Number ID</b> e o <b>token</b> em developers.facebook.com → seu app → WhatsApp → API Setup. Assine <code>messages</code>.</div>
      </div>

      <div id="wa-qr" class="waprov">
        <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">QR Code (tipo WhatsApp Web)</div>
        <div style="font-size:.78rem;color:#e0a33e;background:#2a2113;border:1px solid #5a4520;border-radius:8px;padding:.55rem .7rem">
          Usa o número <b>como está</b>, sem migrar nada. Mas: <b>viola os termos</b> do WhatsApp (risco de banimento) e depende de um serviço à parte sempre-ligado.
        </div>
        <div style="display:flex;gap:.4rem;margin-top:.6rem;flex-wrap:wrap">
          <button type="button" class="pbtn" id="qr-btn" onclick="qrIniciar()">📱 Gerar QR</button>
          <button type="button" class="pbtn ghost" id="qr-sair" onclick="qrSair()" style="display:none">Desconectar</button>
        </div>
        <div id="qr-box" style="margin-top:.6rem;text-align:center;display:none">
          <img id="qr-img" alt="QR do WhatsApp" style="width:220px;max-width:100%;border-radius:10px;background:#fff;padding:.4rem">
        </div>
        <div class="mut" id="qr-msg" style="font-size:.78rem;margin-top:.45rem"></div>
        <script>
        var _qrTimer=null;
        function qrShow(d){var box=document.getElementById('qr-box'),img=document.getElementById('qr-img'),
            msg=document.getElementById('qr-msg'),sair=document.getElementById('qr-sair'),btn=document.getElementById('qr-btn');
          if(d.qr){img.src=d.qr;box.style.display='block';}else{box.style.display='none';}
          if(msg)msg.textContent=d.msg||'';
          var conectado=d.status==='conectado';
          if(sair)sair.style.display=(d.status&&d.status!=='desconectado')?'inline-flex':'none';
          if(btn)btn.textContent=conectado?'Reconectar':'📱 Gerar QR';
          if(msg)msg.style.color=conectado?'var(--verde-claro)':'';
          if(d.status==='conectado'||d.status==='desconectado'){if(_qrTimer){clearInterval(_qrTimer);_qrTimer=null;}}}
        function qrPoll(){fetch('/painel/prospeccao/comunicacao/whatsapp-qr-status').then(function(r){return r.json();}).then(qrShow).catch(function(){});}
        function qrIniciar(){var btn=document.getElementById('qr-btn'),msg=document.getElementById('qr-msg');
          btn.disabled=true;var t=btn.textContent;btn.textContent='Gerando…';if(msg)msg.textContent='';
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-iniciar',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(d){
            btn.disabled=false;btn.textContent=t;qrShow(d);
            if(!d.ok&&msg){msg.textContent=d.msg||d.erro||'Falha.';msg.style.color='#e0a33e';return;}
            if(_qrTimer)clearInterval(_qrTimer);_qrTimer=setInterval(qrPoll,3000);
          }).catch(function(){btn.disabled=false;btn.textContent=t;if(msg){msg.textContent='Falha de rede.';msg.style.color='#e0a33e';}});}
        function qrSair(){if(!confirm('Desconectar o WhatsApp por QR desta empresa?'))return;
          fetch('/painel/prospeccao/comunicacao/whatsapp-qr-sair',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(r){return r.json();}).then(function(){
            if(_qrTimer){clearInterval(_qrTimer);_qrTimer=null;}qrShow({status:'desconectado',msg:'Desconectado.'});}).catch(function(){});}
        {% if canais.wa_provedor=='qr' %}qrPoll();{% endif %}
        </script>
      </div>
      <script>
      function waProv(v){['twilio','cloud','qr'].forEach(function(k){var e=document.getElementById('wa-'+k);if(e)e.style.display=(k===v)?'block':'none';});
        document.querySelectorAll('.waseg label').forEach(function(l){var r=l.querySelector('input');l.classList.toggle('on',!!r&&r.value===v);});}
      waProv('{{ canais.wa_provedor if canais.wa_provedor in ['twilio','cloud','qr'] else 'twilio' }}');
      </script>
      {% else %}
      <div class="mut" style="margin-top:.4rem">Provedor: <b>{{ 'Cloud API (número próprio)' if canais.wa_provedor=='cloud' else 'Twilio' }}</b> · Número: <b>{{ canais.numeros.get('whatsapp','—') }}</b></div>
      {% endif %}
    </div>
    <div class="cx-card">
      <h3>🔵 Messenger <span class="cx-stat {{ 'st-on' if canais.messenger else 'st-off' }}">● {{ 'Conectado' if canais.messenger else ('Falta Página/token' if canais.meta else 'Falta app (env)') }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Meta direto. App da Meta (env global) + Página do Facebook. 📥 responde na janela de 24h.</div>
      <div class="cx-env"><b>META_APP_SECRET</b>=•••••<br><b>META_VERIFY_TOKEN</b>=•••••</div>
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.6rem">
        <input type="hidden" name="canal" value="messenger">
        <label class="lbl">Page ID (Facebook) + Page Access Token</label>
        <input class="fld" name="numero" placeholder="Page ID" value="{{ canais.numeros.get('messenger','') }}" style="margin-bottom:.35rem">
        <input class="fld" name="token" type="password" placeholder="Page Access Token {% if canais.tokens_set.get('messenger') %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin-bottom:.4rem">
        <button class="pbtn">Salvar</button>
      </form>{% endif %}
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">No app da Meta, aponte o webhook pra <code>/webhooks/meta</code> (verify token = META_VERIFY_TOKEN) e assine <code>messages</code>.</div>
    </div>
    <div class="cx-card">
      <h3>📸 Instagram <span class="cx-stat {{ 'st-on' if canais.instagram else ('Falta conta/token' if canais.meta else 'Falta app (env)') }}">● {{ 'Conectado' if canais.instagram else ('Falta conta/token' if canais.meta else 'Falta app (env)') }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Meta direto (mesmo webhook). Conta IG Profissional ligada à Página. 📥 responde na janela de 24h.</div>
      {% if gerencia %}
      <form method="post" action="/painel/prospeccao/comunicacao/canal-numero" style="margin-top:.6rem">
        <input type="hidden" name="canal" value="instagram">
        <label class="lbl">IG Account ID + Page Access Token</label>
        <input class="fld" name="numero" placeholder="IG Account ID" value="{{ canais.numeros.get('instagram','') }}" style="margin-bottom:.35rem">
        <input class="fld" name="token" type="password" placeholder="Page Access Token {% if canais.tokens_set.get('instagram') %}(salvo — vazio mantém){% endif %}" autocomplete="new-password" style="margin-bottom:.4rem">
        <button class="pbtn">Salvar</button>
      </form>{% endif %}
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">Webhook: <code>/webhooks/meta</code> · assine <code>messages</code> no produto Instagram do app.</div>
    </div>
  </div>
  {% endif %}
</div>
<script>
var _cxConv=null,_cxSig='',_cxAg=null,_cxTimer=null,_cxSeen={},_cxList={};
var _cxEscopo='{{ escopo }}';   // 'email' (aba E-mails) ou 'msg' (aba Conversas)
function cxEsc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function cxMsgsHtml(d){
  if(!d.msgs.length)return '<div class="cx-empty">Sem mensagens.</div>';
  var h='';
  d.msgs.forEach(function(m){
    var cls=(m.direcao==='in')?'cx-m cin':((m.autor==='bot')?'cx-m cbot':'cx-m');
    var cab=m.cabecalho?('<div class="cab">'+cxEsc(m.cabecalho)+'</div>'):'';
    var corpo=cxEsc(m.corpo||m.cabecalho).replace(/\\n/g,'<br>');
    h+='<div class="'+cls+'">'+cab+corpo+'<span class="meta">'+cxEsc(m.quem)+' · '+cxEsc(m.quando)+'</span></div>';
  });
  return h;
}
function cxSig(d){return d.msgs.length+'|'+(d.msgs.length?(d.msgs[d.msgs.length-1].corpo||''):'')+'|'+(d.agente_ativo?1:0)+'|'+(d.pode_responder?1:0);}
function cxScroll(force){var b=document.getElementById('cx-msgs');if(!b)return;
  if(force||b.scrollHeight-b.scrollTop-b.clientHeight<80)b.scrollTop=b.scrollHeight;}
function cxOpen(el,id){
  _cxConv=id;if(el&&_cxList[id])_cxSeen[id]=_cxList[id].ult_msg_id||0;
  var th=document.getElementById('cx-thread'),cx=document.getElementById('cx-ctx');
  document.querySelectorAll('.cx-conv').forEach(function(x){x.classList.remove('on');});
  var lb=document.getElementById('cxc-'+id);if(lb){lb.classList.add('on');var dt=lb.querySelector('.cx-undot');if(dt)dt.remove();}
  th.innerHTML='<div class="cx-empty">Carregando…</div>';
  fetch('/painel/prospeccao/comunicacao/thread/'+id).then(function(r){return r.json();}).then(function(d){
    if(_cxConv!==id)return;
    if(!d.ok){th.innerHTML='<div class="cx-empty">Não consegui abrir.</div>';return;}
    var L=d.lead;_cxSig=cxSig(d);_cxAg=d.agente_ativo?1:0;
    var rodape=d.pode_responder
      ?'<div class="cx-comp"><textarea id="cx-reply" rows="2" placeholder="Escreva uma resposta…" onkeydown="if(event.key===\\'Enter\\'&&!event.shiftKey){event.preventDefault();cxResponder('+d.conversa_id+');}"></textarea><button class="pbtn" id="cx-send" onclick="cxResponder('+d.conversa_id+')">Enviar</button></div>'
      :'<div class="cx-stub">Responder por aqui<span class="lbl2">em breve</span> — disponível quando o canal estiver conectado (aba <b>Canais</b>).</div>';
    var agBtn=(d.agente_ativo
      ?'<button class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem;border-color:#4a3163;color:#c9a3e0" onclick="cxAgente('+d.conversa_id+',0)" title="Assumir você (desliga o agente)">🙋 Assumir</button>'
      :'<button class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem" onclick="cxAgente('+d.conversa_id+',1)" title="Devolver ao agente">🤖 Ativar agente</button>');
    th.innerHTML=''
      +'<div class="cx-th"><div><b>'+cxEsc(L.empresa)+'</b><small>'+cxEsc(L.canal_rot||'')+(L.cidade?(' · '+cxEsc(L.cidade)+(L.uf?'/'+cxEsc(L.uf):'')):'')+(L.status_rot?(' · '+cxEsc(L.status_rot)):'')+(d.agente_ativo?' · <span style=\\'color:#c9a3e0\\'>🤖 no automático</span>':'')+'</small></div>'
      +'<span style="flex:1"></span>'+agBtn+(L.id?(' <a class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem" href="/painel/prospeccao/'+L.id+'">Abrir ficha</a>'):(' <button class="pbtn" style="padding:.35rem .7rem;font-size:.78rem" onclick="cxVirarLead('+d.conversa_id+')" title="Criar um lead a partir deste contato">➕ Levar para o lead</button>'))+'</div>'
      +'<div class="cx-msgs" id="cx-msgs">'+cxMsgsHtml(d)+'</div>'+rodape;
    cxScroll(true);
    var kv=function(k,v){return v?('<div class="cx-kv"><span>'+k+'</span><b>'+cxEsc(v)+'</b></div>'):'';};
    cx.innerHTML=''
      +'<div class="cx-sec"><h4>Lead</h4>'+kv('Empresa',L.empresa)+kv('Segmento',L.segmento)+kv('Cidade',(L.cidade||'')+(L.uf?'/'+L.uf:''))+kv('WhatsApp',L.whatsapp)+kv('E-mail',L.email)+kv('Responsável',L.vendedor)+kv('Status',L.status_rot)+'</div>'
      +(L.id?('<div class="cx-sec"><a class="pbtn" style="width:100%;text-align:center" href="/painel/prospeccao/'+L.id+'">Abrir ficha do lead</a></div>'):('<div class="cx-sec"><button class="pbtn" style="width:100%" onclick="cxVirarLead('+d.conversa_id+')">➕ Levar para o lead</button><div class="mut" style="font-size:.74rem;margin-top:.4rem">Este contato ainda não é um lead. Crie o lead quando fizer sentido.</div></div>'));
  }).catch(function(){th.innerHTML='<div class="cx-empty">Falha de rede.</div>';});
}
function cxPollThread(){
  if(!_cxConv)return;var id=_cxConv;
  fetch('/painel/prospeccao/comunicacao/thread/'+id).then(function(r){return r.json();}).then(function(d){
    if(!d.ok||_cxConv!==id)return;
    if((d.agente_ativo?1:0)!==_cxAg||cxSig(d).split('|')[3]!==_cxSig.split('|')[3]){cxOpen(document.getElementById('cxc-'+id),id);return;}
    var sig=cxSig(d);if(sig===_cxSig)return;_cxSig=sig;
    var b=document.getElementById('cx-msgs');
    if(b){
      // se o usuário está acompanhando o rodapé, seguimos a conversa; se ele subiu
      // pra ler o histórico, preservamos a posição (innerHTML zera o scroll senão).
      var perto=(b.scrollHeight-b.scrollTop-b.clientHeight)<120;var st=b.scrollTop;
      b.innerHTML=cxMsgsHtml(d);
      b.scrollTop=perto?b.scrollHeight:st;
    }
  }).catch(function(){});
}
function cxParams(){var q=new URLSearchParams(location.search);return 'canal='+(q.get('canal')||'')+'&vendedor='+(q.get('vendedor')||'')+'&escopo='+encodeURIComponent(_cxEscopo||'msg');}
function cxListItem(c){
  var cnc={whatsapp:'cn-wpp',email:'cn-mail',messenger:'cn-msg',instagram:'cn-ig'}[c.canal]||'cn-mail';
  var unread=(c.id!==_cxConv)&&(c.ult_autor==='lead'||c.ult_autor==='bot')&&((c.ult_msg_id||0)>(_cxSeen[c.id]||0));
  var av=(c.empresa||'?').substring(0,2).toUpperCase();
  return '<button type="button" class="cx-conv'+(c.id===_cxConv?' on':'')+'" id="cxc-'+c.id+'" onclick="cxOpen(this,'+c.id+')">'
    +'<span class="av">'+cxEsc(av)+'</span><span class="mid">'
    +'<span class="nm"><b>'+cxEsc(c.empresa)+'</b><span class="t">'+cxEsc(c.quando||'')+(unread?' <span class="cx-undot"></span>':'')+'</span></span>'
    +'<span class="pre">'+cxEsc(c.quem)+': '+cxEsc(c.preview)+'</span>'
    +'<span class="cx-cn '+cnc+'">'+cxEsc(c.canal_rot)+(c.n>1?(' · '+c.n):'')+'</span></span></button>';
}
function cxPollList(){
  var box=document.getElementById('cx-list');if(!box)return;
  fetch('/painel/prospeccao/comunicacao/lista?'+cxParams()).then(function(r){return r.json();}).then(function(d){
    if(!d.ok)return;var h='';_cxList={};
    d.convs.forEach(function(c){_cxList[c.id]=c;h+=cxListItem(c);});
    box.innerHTML=h||'<div class="cx-empty">Nenhuma conversa ainda.</div>';
  }).catch(function(){});
}
function cxAgente(convId,on){
  var fd=new FormData();fd.append('conversa_id',convId);fd.append('ativar',on?'1':'0');
  fetch('/painel/prospeccao/comunicacao/agente-conversa',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui.');return;}cxOpen(document.getElementById('cxc-'+convId),convId);}).catch(function(){alert('Falha de rede.');});
}
function cxVirarLead(convId){var fd=new FormData();fd.append('conversa_id',convId);
  fetch('/painel/prospeccao/comunicacao/virar-lead',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui.');return;}
      location.href='/painel/prospeccao/'+d.lead_id;}).catch(function(){alert('Falha de rede.');});}
function cxResponder(convId){
  var ta=document.getElementById('cx-reply');if(!ta)return;var t=ta.value.trim();if(!t)return;
  var b=document.getElementById('cx-msgs');
  if(b){if(b.querySelector('.cx-empty'))b.innerHTML='';b.insertAdjacentHTML('beforeend','<div class="cx-m" style="opacity:.6"><span class="who">Você</span>'+cxEsc(t).replace(/\\n/g,'<br>')+'<span class="meta">enviando…</span></div>');cxScroll(true);}
  ta.value='';var sd=document.getElementById('cx-send');if(sd){sd.disabled=true;sd.textContent='…';}
  var fd=new FormData();fd.append('conversa_id',convId);fd.append('texto',t);
  fetch('/painel/prospeccao/comunicacao/responder',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(sd){sd.disabled=false;sd.textContent='Enviar';}
      if(!d.ok){alert(d.erro||'Não consegui enviar.');ta.value=t;}
      _cxSig='';cxPollThread();cxPollList();
    }).catch(function(){if(sd){sd.disabled=false;sd.textContent='Enviar';}alert('Falha de rede.');ta.value=t;});
}
function cxTick(){cxPollList();cxPollThread();}
(function(){var box=document.getElementById('cx-list');if(box){document.querySelectorAll('#cx-list .cx-conv').forEach(function(b){var id=parseInt(b.id.replace('cxc-',''));_cxList[id]={id:id,ult_msg_id:0};});
  _cxTimer=setInterval(cxTick,4000);
  // o navegador congela o timer quando a aba fica em segundo plano — ao voltar o
  // foco/visibilidade, atualiza na hora pra conversa subir e mostrar as msgs novas.
  document.addEventListener('visibilitychange',function(){if(!document.hidden)cxTick();});
  window.addEventListener('focus',cxTick);}})();
</script>
{% endblock %}"""

_CPILL_CSS = """<style>.cpill{font-size:.68rem;font-weight:700;padding:.14rem .5rem;border-radius:999px;border:1px solid var(--borda);white-space:nowrap}
.cpill.ativa{color:#3ddc84;border-color:#1e4a34;background:#10241a}
.cpill.pausada{color:#e0a33e;border-color:#5a4520;background:#2a2113}
.cpill.rascunho,.cpill.concluida{color:#8a938a}
.cpill.ia{color:#c9a3e0;border-color:#4a3163;background:#1c1327}
.cpstep{display:flex;gap:.7rem;align-items:center;padding:.55rem 0;border-top:1px solid var(--borda)}
.cpstep:first-of-type{border-top:0}
.cpday{width:54px;text-align:center;flex-shrink:0}.cpday b{font-size:1.05rem}
/* pipeline */
.cppipe{display:flex;gap:.5rem;background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:.6rem;margin:1rem 0;overflow-x:auto}
.cppstep{flex:1;min-width:140px;border:1px solid var(--borda);border-radius:9px;padding:.55rem .65rem;position:relative;background:var(--bg)}
.cppstep h5{margin:.2rem 0 .05rem;font-size:.82rem}.cppstep p{margin:0;font-size:.72rem;color:var(--mut)}
.cppstep.on{border-color:#1e4a34;background:#10241a}
.cppstep .arw{position:absolute;right:-.6rem;top:50%;transform:translateY(-50%);color:var(--mut);z-index:2}
/* barra de progresso do card */
.cpbar{height:6px;border-radius:999px;background:#1e1f22;margin-top:.5rem;overflow:hidden;display:flex}
.cpbar i{display:block;height:100%}.cpbar .e{background:#5b9bd5}.cpbar .r{background:#3ddc84}
/* botão excluir no card da lista */
.cpx{width:32px;height:32px;padding:0;border-radius:8px;background:transparent;border:1px solid var(--borda);color:var(--mut);cursor:pointer;font-size:.9rem;flex-shrink:0;line-height:1}
.cpx:hover{color:#e0574f;border-color:#5c2a27;background:#231414}
/* explicação do fluxo */
.cphelp{border:1px solid var(--borda);border-radius:11px;background:var(--card);padding:.8rem .95rem;margin-top:.5rem}
.cphelp ol{margin:.4rem 0 0;padding-left:1.15rem;font-size:.82rem;color:var(--mut);line-height:1.7}
.cphelp ol b{color:var(--txt)}
/* resumo 3 colunas */
.cpsum{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.7rem;margin-top:1rem}
@media(max-width:760px){.cpsum{grid-template-columns:1fr}}
.cpsum .box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.75rem .85rem}
.cpsum .box h5{margin:0 0 .4rem;font-size:.82rem}
.cpkv{display:flex;justify-content:space-between;gap:.5rem;font-size:.82rem;padding:.15rem 0}
.cpkv span{color:var(--mut)}
/* prévia do e-mail */
.mailp{background:#0e0f11;border:1px solid var(--borda);border-radius:11px;overflow:hidden;margin-top:.7rem}
.mailp .h{padding:.6rem .85rem;border-bottom:1px solid var(--borda);font-size:.78rem;color:var(--mut)}
.mailp .h b{color:var(--txt)}
.mailp .b{padding:.9rem 1rem;font-size:.9rem;line-height:1.6;white-space:pre-wrap}
.mailp .wa{display:inline-block;margin-top:.5rem;color:#3ddc84;border:1px solid #1e4a34;background:#10241a;border-radius:8px;padding:.3rem .65rem;font-size:.82rem;text-decoration:none}
.mailp .f{padding:.55rem .85rem;border-top:1px solid var(--borda);font-size:.68rem;color:var(--mut)}</style>"""

_CAMPANHAS_TPL = """{% extends "base" %}{% block conteudo %}""" + _CPILL_CSS + """
<div style="max-width:1000px;margin:0 auto">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap">
    <div><h2 class="tt">📣 Campanhas de E-mail</h2>
      <div class="mut" style="font-size:.85rem">Prospecção fria automatizada · <b style="color:var(--verde-claro)">{{ elegiveis }}</b> lead(s) com e-mail válido prontos pra abordar</div></div>
    <div style="display:flex;gap:.5rem"><a class="pbtn ghost" href="/painel/prospeccao">‹ Prospecção</a><a class="pbtn ghost" href="/painel/prospeccao/comunicacao">📨 Comunicação</a></div>
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div class="cppipe">
    <div class="cppstep"><div>🎯</div><h5>Captar</h5><p>Maps + CNPJ</p><span class="arw">›</span></div>
    <div class="cppstep"><div>🔎</div><h5>Verificar canais</h5><p>site → e-mail ✓</p><span class="arw">›</span></div>
    <div class="cppstep on"><div>✉️</div><h5>Sequência</h5><p>D0 · follow-ups</p><span class="arw">›</span></div>
    <div class="cppstep"><div>💬</div><h5>Resposta → inbox</h5><p>para a sequência</p><span class="arw">›</span></div>
    <div class="cppstep"><div>🤝</div><h5>Converte</h5><p>agente assume</p></div>
  </div>

  <div class="cphelp">
    <b style="font-size:.9rem">Como um lead entra numa campanha</b>
    <ol>
      <li>Na <b>Prospecção</b>, rode <b>🔎 Verificar canais</b> — descobre o e-mail do lead pelo site/CNPJ.</li>
      <li>Só entra quem tem <b>e-mail válido</b> e não descadastrou (os {{ elegiveis }} acima).</li>
      <li>Abra a campanha → aba <b>👥 Público</b> → filtre por segmento/cidade e clique <b>Adicionar à campanha</b>.</li>
      <li>Ative: o motor dispara a sequência sozinho (D0 + follow-ups) e <b>para</b> em quem responde — a conversa cai no inbox pro agente assumir.</li>
    </ol>
  </div>
  {% if elegiveis == 0 %}<div class="mut" style="margin-top:.5rem;font-size:.85rem;border:1px solid var(--borda);border-radius:10px;padding:.7rem .9rem">Nenhum lead com e-mail válido ainda. Rode o <b>🔎 Verificar canais</b> na Prospecção pra descobrir os e-mails primeiro.</div>{% endif %}

  <div class="card" style="padding:.9rem 1rem;margin:1rem 0">
    <form method="post" action="/painel/prospeccao/campanhas/nova" style="margin:0">
      <label class="lbl">Nome da campanha <span style="color:var(--mut);font-weight:400">— opcional, dá pra renomear depois</span></label>
      <input class="fld" name="nome" placeholder="ex.: Salões · Teresina" maxlength="120" style="margin-bottom:.6rem">
      <button class="pbtn" style="width:100%;justify-content:center">＋ Criar campanha</button>
    </form>
  </div>

  <div style="display:flex;flex-direction:column;gap:.6rem">
    {% for c in camps %}
    <div class="card" style="padding:.9rem 1rem">
      <div style="display:flex;align-items:center;gap:.6rem">
        <a href="/painel/prospeccao/campanhas/{{ c.id }}" style="flex:1;text-decoration:none;color:inherit;font-weight:700">{{ c.nome }}</a>
        <span class="cpill {{ c.status }}">{{ c.status_rot }}</span>
        <form method="post" action="/painel/prospeccao/campanhas/{{ c.id }}/excluir" style="margin:0" onsubmit="return confirm('Excluir “{{ c.nome }}”? Os leads voltam pro funil.')">
          <button class="cpx" title="Excluir campanha">🗑</button>
        </form>
      </div>
      <a href="/painel/prospeccao/campanhas/{{ c.id }}" style="display:block;text-decoration:none;color:inherit">
        <div class="mut" style="font-size:.8rem;margin-top:.35rem"><b style="color:var(--txt)">{{ c.n }}</b> leads · {{ c.env }} enviados · <span style="color:var(--verde-claro)">{{ c.resp }} respostas</span> · limite {{ c.limite }}/dia</div>
        {% if c.n %}<div class="cpbar"><i class="e" style="width:{{ (100*c.env/c.n)|round|int }}%"></i><i class="r" style="width:{{ (100*c.resp/c.n)|round|int }}%"></i></div>{% endif %}
      </a>
    </div>
    {% else %}<div class="mut" style="text-align:center;padding:2rem">Nenhuma campanha ainda — crie a primeira acima.</div>{% endfor %}
  </div>
</div>
{% endblock %}"""

_CAMPANHA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CPILL_CSS + """
<style>.passo{border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.55rem;background:var(--bg)}
.passo .prow{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.passo .prow label{font-size:.8rem;color:var(--mut);display:flex;align-items:center;gap:.3rem}
.passo textarea{width:100%;resize:vertical}
.cpstats{display:grid;grid-template-columns:repeat(6,1fr);gap:.6rem}
@media(max-width:720px){.cpstats{grid-template-columns:repeat(3,1fr)}}
.cpstat{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.7rem .8rem}
.cpstat .n{font-size:1.45rem;font-weight:750;letter-spacing:-.02em}
.cpstat .l{font-size:.72rem;color:var(--mut);margin-top:.1rem}
.cpstat.g .n{color:#3ddc84}.cpstat.r .n{color:#e0574f}
.apill{font-size:.68rem;font-weight:600;padding:.1rem .45rem;border-radius:999px;border:1px solid var(--borda);white-space:nowrap}
.apill.respondeu,.apill.concluido{color:#3ddc84;border-color:#1e4a34;background:#10241a}
.apill.enviado{color:#5b9bd5;border-color:#2f4a63;background:#14212e}
.apill.descadastrou,.apill.erro{color:#e0a33e;border-color:#5a4520;background:#2a2113}
.apill.fila{color:#8a938a}</style>
<div style="max-width:1000px;margin:0 auto">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap">
    <div><h2 class="tt">{{ camp.nome }} <span class="cpill {{ camp.status }}">{{ camp.status_rot }}</span></h2>
      <div class="mut" style="font-size:.85rem"><b style="color:var(--txt)">{{ na_camp }}</b> lead(s) na campanha · limite {{ camp.limite }}/dia</div></div>
    <div style="display:flex;gap:.4rem;flex-wrap:wrap">
      {% if camp.status != 'ativa' %}<form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/status" style="margin:0"><input type="hidden" name="status" value="ativa"><button class="pbtn">▶ Ativar</button></form>
      {% else %}<form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/status" style="margin:0"><input type="hidden" name="status" value="pausada"><button class="pbtn ghost">❚❚ Pausar</button></form>{% endif %}
      <a class="pbtn ghost" href="/painel/prospeccao/campanhas">‹ Campanhas</a>
    </div>
  </div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div class="cpsum">
    <div class="box"><h5>👥 Público</h5>
      <div class="cpkv"><span>Na campanha</span><b>{{ na_camp }}</b></div>
      <div class="cpkv"><span>Elegíveis (filtro)</span><b>{{ elegiveis }}</b></div></div>
    <div class="box"><h5>🗓️ Cadência</h5>
      <div class="cpkv"><span>Passos</span><b>{{ passos|length }}</b></div>
      <div class="cpkv"><span>Sequência</span><b>{{ cadencia }}</b></div>
      <div class="cpkv"><span>Para ao responder</span><b style="color:var(--verde-claro)">Sim</b></div></div>
    <div class="box"><h5>🛡️ Limites & LGPD</h5>
      <div class="cpkv"><span>Envios/dia</span><b>{{ camp.limite }}</b></div>
      <div class="cpkv"><span>Enviados hoje</span><b>{{ metr.hoje }}</b></div>
      <div class="cpkv"><span>Descadastro</span><b style="color:var(--verde-claro)">Ativo</b></div></div>
  </div>

  <!-- CONFIG -->
  <form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/config" class="card" style="padding:.9rem 1rem;margin-top:1rem">
    <div style="display:flex;gap:.6rem;align-items:flex-end;flex-wrap:wrap">
      <div><label class="lbl">Nome</label><input class="fld" name="nome" value="{{ camp.nome }}" maxlength="120" style="min-width:240px"></div>
      <div><label class="lbl">Envios/dia</label><input class="fld" name="limite_dia" value="{{ camp.limite }}" inputmode="numeric" style="width:100px"></div>
      <span style="flex:1"></span>
      <button class="pbtn ghost" formaction="/painel/prospeccao/campanhas/{{ camp.id }}/excluir" formmethod="post" style="color:#e0574f;border-color:#5c2a27" onclick="return confirm('Excluir a campanha? Os leads voltam pro funil.')">🗑 Excluir</button>
    </div>
    <div style="margin-top:.7rem">
      <label class="lbl">📎 Material <span style="color:var(--mut);font-weight:400">— enviado quando o lead clica “✅ Tenho interesse” no e-mail</span></label>
      <textarea class="fld" name="material" rows="2" placeholder="Link da apresentação, PDF, vídeo… (ex.: https://... ). O agente manda isso na hora e assume a conversa.">{{ camp.material or '' }}</textarea>
    </div>
    <div style="margin-top:.6rem"><button class="pbtn">Salvar</button></div>
  </form>

  <!-- PÚBLICO -->
  <div class="card" style="padding:1rem;margin-top:1rem">
    <h3 style="margin:0 0 .4rem;font-size:1rem">👥 Público — adicionar leads</h3>
    <div class="mut" style="font-size:.82rem;margin-bottom:.6rem">Só entram leads com <b>e-mail válido</b> (verificados na Fase 1) e que não descadastraram.</div>
    <form method="get" action="/painel/prospeccao/campanhas/{{ camp.id }}" style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
      <input class="fld" name="seg" value="{{ seg }}" placeholder="Segmento (ex.: salão)" style="width:auto">
      <input class="fld" name="cidade" value="{{ cidade }}" placeholder="Cidade" style="width:auto">
      <select class="fld" name="temp" style="width:auto" onchange="this.form.submit()">
        <option value="">Qualquer temperatura</option>
        <option value="frio" {% if temp=='frio' %}selected{% endif %}>Frio</option>
        <option value="morno" {% if temp=='morno' %}selected{% endif %}>Morno</option>
        <option value="quente" {% if temp=='quente' %}selected{% endif %}>Quente</option>
      </select>
      <button class="pbtn ghost">Filtrar</button>
    </form>
    <div style="display:flex;align-items:center;gap:.9rem;margin-top:.8rem;flex-wrap:wrap">
      <div><span style="font-size:1.7rem;font-weight:750">{{ elegiveis }}</span> <span class="mut">elegíveis com esse filtro</span></div>
      <form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/publico" style="margin:0">
        <input type="hidden" name="seg" value="{{ seg }}"><input type="hidden" name="cidade" value="{{ cidade }}"><input type="hidden" name="temp" value="{{ temp }}">
        <button class="pbtn" {% if not elegiveis %}disabled{% endif %}>＋ Adicionar {{ elegiveis }} à campanha</button>
      </form>
    </div>
  </div>

  <!-- SEQUÊNCIA (editor) -->
  <form method="post" action="/painel/prospeccao/campanhas/{{ camp.id }}/sequencia" class="card" style="padding:1rem;margin-top:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.5rem">
      <h3 style="margin:0;font-size:1rem">🗓️ Sequência</h3>
      <div style="display:flex;gap:.4rem"><button type="button" class="pbtn ghost" onclick="addPasso()">＋ Passo</button><button class="pbtn">Salvar sequência</button></div>
    </div>
    <div class="mut" style="font-size:.8rem;margin-bottom:.7rem"><b>D+</b> = dias após o 1º e-mail (0 = primeiro). <b>IA escreve</b> = o agente monta o e-mail único por lead. <b>Template</b> = usa o texto abaixo (variáveis <code>{empresa}</code>, <code>{cidade}</code>, <code>{segmento}</code>).</div>
    <div id="passos">
      {% for p in passos %}
      <div class="passo">
        <div class="prow">
          <label>D+<input class="fld" name="dias" value="{{ p.dias }}" inputmode="numeric" style="width:64px"></label>
          <select class="fld" name="usar_ia" style="width:auto"><option value="1" {% if p.ia %}selected{% endif %}>🤖 IA escreve</option><option value="0" {% if not p.ia %}selected{% endif %}>Template</option></select>
          <span style="flex:1"></span>
          <button type="button" class="pbtn ghost" onclick="remPasso(this)" title="Remover passo">✕</button>
        </div>
        <input class="fld" name="assunto" value="{{ p.assunto }}" placeholder="Assunto do e-mail" style="margin-top:.45rem">
        <textarea class="fld" name="corpo" rows="3" placeholder="Texto do e-mail (Template). Na opção IA, deixe vazio ou use como orientação." style="margin-top:.45rem">{{ p.corpo }}</textarea>
      </div>
      {% endfor %}
    </div>
  </form>

  <!-- PRÉVIA -->
  {% if previa %}
  <div class="card" style="padding:1rem;margin-top:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
      <h3 style="margin:0;font-size:1rem">✉️ Prévia <span class="mut" style="font-size:.75rem;font-weight:400">· 1º passo · exemplo com {{ previa.empresa }}</span></h3>
      {% if previa.ia %}<button type="button" class="pbtn ghost" id="pia-btn" onclick="previaIA({{ camp.id }})">🤖 Gerar prévia com IA</button>{% endif %}
    </div>
    <div class="mailp">
      <div class="h">De <b>{{ remetente or 'sua empresa' }}</b> · Para {{ previa.email or '—' }}</div>
      <div class="b"><b id="pv-assunto">{{ previa.assunto }}</b>
        <div style="height:.5rem"></div><span id="pv-corpo">{{ previa.corpo }}</span>
        <div style="margin:16px 0 2px"><span style="display:inline-block;padding:11px 22px;background:#16a34a;color:#fff;font-weight:bold;border-radius:8px">✅ Tenho interesse</span></div>
        <div class="mut" style="font-size:.72rem">Ao clicar, o lead recebe o material{% if camp.material %} (você já configurou){% else %} — <b>configure o Material</b> lá em cima{% endif %} e o agente assume.</div>
        {% if previa.whatsapp %}<div><a class="wa" href="#">💬 Falar no WhatsApp</a></div>{% endif %}
      </div>
      <div class="f">Sua empresa · Se não quiser mais receber, descadastrar (link automático em cada envio).</div>
    </div>
  </div>
  {% endif %}

  <!-- MÉTRICAS -->
  <h3 style="margin:1.3rem 0 .5rem;font-size:1rem">📊 Desempenho</h3>
  <div class="cpstats">
    <div class="cpstat"><div class="n">{{ metr.enviados }}</div><div class="l">Enviados</div></div>
    <div class="cpstat g"><div class="n">{{ metr.responderam }}</div><div class="l">Responderam</div></div>
    <div class="cpstat g"><div class="n">{{ metr.taxa }}%</div><div class="l">Taxa de resposta</div></div>
    <div class="cpstat"><div class="n">{{ metr.fila }}</div><div class="l">Na fila</div></div>
    <div class="cpstat r"><div class="n">{{ metr.descadastros }}</div><div class="l">Descadastros</div></div>
    <div class="cpstat"><div class="n">{{ metr.hoje }}<span style="font-size:.9rem;color:var(--mut)">/{{ camp.limite }}</span></div><div class="l">Hoje</div></div>
  </div>
  <div class="mut" style="font-size:.8rem;margin-top:.6rem">{% if camp.status=='ativa' %}✅ <b style="color:var(--verde-claro)">Ativa</b> — o motor dispara automaticamente (até {{ camp.limite }}/dia) e para em quem responde ou descadastra.{% else %}Clique <b>▶ Ativar</b> pra o motor começar a disparar.{% endif %}{% if metr.erros %} · <span style="color:#e0574f">{{ metr.erros }} erro(s) de envio</span>{% endif %}</div>

  <!-- LEADS -->
  <div class="card" style="padding:0;margin-top:1rem;overflow:hidden">
    <div style="padding:.7rem 1rem;border-bottom:1px solid var(--borda)"><b style="font-size:.92rem">Leads da campanha</b> <span class="mut" style="font-size:.8rem">({{ na_camp }})</span></div>
    <div style="max-height:420px;overflow:auto">
    <table style="width:100%;border-collapse:collapse;font-size:.84rem">
      <thead><tr style="text-align:left;color:var(--mut)"><th style="padding:.5rem 1rem;font-weight:500">Empresa</th><th style="padding:.5rem;font-weight:500">Situação</th><th style="padding:.5rem;font-weight:500">Passo</th><th style="padding:.5rem 1rem;font-weight:500">Próximo/último</th></tr></thead>
      <tbody>
        {% for l in leads %}
        <tr style="border-top:1px solid var(--borda)">
          <td style="padding:.5rem 1rem"><b>{{ l.empresa }}</b><div class="mut" style="font-size:.76rem">{{ l.email }}</div></td>
          <td style="padding:.5rem"><span class="apill {{ l.status }}">{{ l.rot }}</span></td>
          <td style="padding:.5rem" class="mut">D{{ l.passo }}</td>
          <td class="mut" style="padding:.5rem 1rem;white-space:nowrap">{% if l.status in ('fila','enviado') and l.prox %}⏳ {{ l.prox }}{% elif l.ult %}✓ {{ l.ult }}{% else %}—{% endif %}</td>
        </tr>
        {% else %}<tr><td colspan="4" class="mut" style="text-align:center;padding:1.6rem">Nenhum lead ainda — adicione o público acima.</td></tr>{% endfor %}
      </tbody>
    </table>
    </div>
  </div>
</div>
<script>
function novoPasso(){return '<div class="passo"><div class="prow"><label>D+<input class="fld" name="dias" value="7" inputmode="numeric" style="width:64px"></label>'
  +'<select class="fld" name="usar_ia" style="width:auto"><option value="0" selected>Template</option><option value="1">\\ud83e\\udd16 IA escreve</option></select>'
  +'<span style="flex:1"></span><button type="button" class="pbtn ghost" onclick="remPasso(this)" title="Remover passo">\\u2715</button></div>'
  +'<input class="fld" name="assunto" placeholder="Assunto do e-mail" style="margin-top:.45rem">'
  +'<textarea class="fld" name="corpo" rows="3" placeholder="Texto do e-mail (use {empresa}, {cidade})" style="margin-top:.45rem"></textarea></div>';}
function addPasso(){document.getElementById('passos').insertAdjacentHTML('beforeend',novoPasso());}
function remPasso(b){var ps=document.querySelectorAll('#passos .passo');if(ps.length<=1){alert('Deixe ao menos 1 passo.');return;}b.closest('.passo').remove();}
function previaIA(id){var b=document.getElementById('pia-btn');if(b){b.disabled=true;b.textContent='Gerando…';}
  fetch('/painel/prospeccao/campanhas/'+id+'/previa-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()}).then(function(r){return r.json();}).then(function(d){if(b){b.disabled=false;b.textContent='🤖 Gerar prévia com IA';}
    if(!d.ok){alert(d.erro||'Não consegui.');return;}
    document.getElementById('pv-assunto').textContent=d.assunto;document.getElementById('pv-corpo').textContent=d.corpo;}).catch(function(){if(b){b.disabled=false;b.textContent='🤖 Gerar prévia com IA';}alert('Falha de rede.');});}
</script>
{% endblock %}"""

_env.loader.mapping["prospeccao"] = _KANBAN_TPL
_env.loader.mapping["prospeccao_captar"] = _CAPTAR_TPL
_env.loader.mapping["prospeccao_ficha"] = _FICHA_TPL
_env.loader.mapping["prospeccao_comunicacao"] = _COMUNICACAO_TPL
_env.loader.mapping["prospeccao_campanhas"] = _CAMPANHAS_TPL
_env.loader.mapping["prospeccao_campanha"] = _CAMPANHA_TPL
