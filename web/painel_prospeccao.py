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

from fastapi import APIRouter, Request, Form, UploadFile, File
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
                      p.site_url
                 from prospeccao p
                 left join membros m on m.id = p.vendedor_id
                where p.id=%s and p.conta_id=%s""", (alvo_id, conta_id)).fetchone()
    if not r:
        return None
    cols = ["id", "empresa", "cnpj", "segmento", "cidade", "uf", "contato", "cargo",
            "telefone", "whatsapp", "email", "status", "temperatura", "valor",
            "origem", "obs", "instagram", "socio", "regime_tributario", "porte",
            "ultimo_contato_em", "proximo_contato_em", "vendedor_id", "vendedor_nome",
            "orcamento_id", "tem_site", "maps_url", "receita", "site_url"]
    d = dict(zip(cols, r))
    d["zap_link"] = _zap_link(d["whatsapp"] or d["telefone"])
    d["tel_link"] = "tel:" + _so_digitos(d["telefone"]) if d["telefone"] else ""
    d["site_dominio"] = _dominio(d.get("site_url"))
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
                       p.telefone, p.whatsapp, p.vendedor_id, m.nome
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
                "vendedor_id": r[11], "vendedor": r[12]}
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
                  esconder_redes: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    res = fontes.buscar_places(segmento, cidade)
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
    busca = {"segmento": segmento, "cidade": cidade, "esconder": esconder,
             "ok": res.get("ok"), "erro": res.get("erro"), "n_redes": n_redes}
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


def _canais_status() -> dict:
    """Quais canais estão conectados (por presença de credencial no ambiente)."""
    twilio = bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"))
    return {
        "email": bool(remetente_configurado()),
        "whatsapp": twilio and bool(os.environ.get("TWILIO_WHATSAPP_FROM")),
        "messenger": twilio and bool(os.environ.get("TWILIO_MESSENGER_FROM")),
        "instagram": bool(os.environ.get("META_PAGE_TOKEN") and os.environ.get("IG_ACCOUNT_ID")),
    }


@router.get("/painel/prospeccao/comunicacao", response_class=HTMLResponse)
def prospeccao_comunicacao(request: Request, aba: str = "conversas", canal: str = "", vendedor: str = ""):
    """Hub omnichannel: Conversas · E-mails · Agente · Canais (lê de conversas/mensagens)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    aba = aba if aba in ("conversas", "emails", "agente", "canais") else "conversas"
    pool = get_pool()
    where = ["cv.conta_id=%s"]
    params: list = [ctx["conta_id"]]
    filtro_vend = ""
    if not ctx["gerencia"]:
        where.append("p.vendedor_id=%s")
        params.append(ctx["membro_id"])
    else:
        filtro_vend = (vendedor or "").strip()
        if filtro_vend.isdigit():
            where.append("p.vendedor_id=%s")
            params.append(int(filtro_vend))
    if canal in CANAIS_TODOS:
        where.append("cv.canal=%s")
        params.append(canal)
    wsql = " and ".join(where)
    convs, emails = [], []
    with pool.connection() as c:
        rows = c.execute(f"""
            select cv.id, cv.canal, cv.status, cv.ultima_msg_em, cv.prospeccao_id,
                   coalesce(p.empresa, cv.contato_ref, '—'), p.cidade, p.uf,
                   lm.texto, lm.autor, lm.membro_id, mm.nome, cnt.n
              from conversas cv
              left join prospeccao p on p.id = cv.prospeccao_id
              left join lateral (select texto, autor, membro_id from mensagens
                                  where conversa_id=cv.id order by criado_em desc limit 1) lm on true
              left join membros mm on mm.id = lm.membro_id
              join lateral (select count(*) n from mensagens where conversa_id=cv.id) cnt on true
             where {wsql}
             order by cv.ultima_msg_em desc limit 100""", tuple(params)).fetchall()
        for r in rows:
            if r[9] == "bot":
                quem = "🤖 Agente"
            elif r[9] == "lead":
                quem = r[5]
            elif r[10] and r[10] == ctx["membro_id"]:
                quem = "Você"
            else:
                quem = r[11] or "—"
            convs.append({"id": r[0], "canal": r[1], "canal_rot": CANAL_ROT.get(r[1], r[1]),
                          "status": r[2], "quando": r[3], "empresa": r[5],
                          "cidade": r[6], "uf": r[7], "preview": _preview(r[8]),
                          "quem": quem, "n": r[12]})
        if aba == "emails":
            erows = c.execute(f"""
                select msg.criado_em, coalesce(p.empresa, cv.contato_ref, '—'),
                       msg.membro_id, mm.nome, msg.texto
                  from mensagens msg
                  join conversas cv on cv.id = msg.conversa_id
                  left join prospeccao p on p.id = cv.prospeccao_id
                  left join membros mm on mm.id = msg.membro_id
                 where cv.conta_id=%s and msg.canal='email' and msg.direcao='out'
                   {'and p.vendedor_id=%s' if not ctx['gerencia'] else ''}
                 order by msg.criado_em desc limit 100""",
                (ctx["conta_id"], ctx["membro_id"]) if not ctx["gerencia"] else (ctx["conta_id"],)).fetchall()
            for e in erows:
                cab, _, corpo = (e[4] or "").partition("\n\n")
                emails.append({"quando": e[0], "empresa": e[1],
                               "quem": "Você" if e[2] and e[2] == ctx["membro_id"] else (e[3] or "—"),
                               "cabecalho": cab.strip(), "preview": " ".join(corpo.split())[:80]})
    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render("prospeccao_comunicacao", request, titulo="Comunicação",
                   secao_ativa="prospeccao", aba=aba, convs=convs, emails=emails, canal=canal,
                   canais=_canais_status(), canal_rot=CANAL_ROT,
                   gerencia=ctx["gerencia"], vendedores=vends, filtro_vend=filtro_vend,
                   remetente=remetente_configurado(), tem_ia=_tem_ia())


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
                      p.cidade, p.uf, p.whatsapp, p.telefone, p.email, p.status, m.nome, p.vendedor_id
                 from conversas cv
                 left join prospeccao p on p.id = cv.prospeccao_id
                 left join membros m on m.id = p.vendedor_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if not ctx["gerencia"] and cv[12] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        rows = c.execute(
            """select msg.canal, msg.direcao, msg.autor, msg.criado_em, msg.texto, msg.membro_id, mm.nome
                 from mensagens msg left join membros mm on mm.id = msg.membro_id
                where msg.conversa_id=%s order by msg.criado_em asc""", (conversa_id,)).fetchall()
    msgs = []
    for (cn, direcao, autor, quando, texto, mid, nome) in rows:
        cab, _, corpo = (texto or "").partition("\n\n")
        if autor == "bot":
            quem = "🤖 Agente"
        elif autor == "lead":
            quem = cv[3] or "Lead"
        else:
            quem = "Você" if mid and mid == ctx["membro_id"] else (nome or "—")
        msgs.append({"canal": cn, "direcao": direcao, "autor": autor,
                     "quando": quando.strftime("%d/%m %H:%M") if quando else "",
                     "quem": quem, "cabecalho": cab.strip(), "corpo": (corpo or cab).strip()})
    from finance import whatsapp_twilio as _wa
    pode_wa = cv[0] == "whatsapp" and _wa.configurado() and bool(cv[7] or cv[8])
    lead = {"id": cv[2], "empresa": cv[3], "canal": cv[0], "canal_rot": CANAL_ROT.get(cv[0], cv[0]),
            "segmento": cv[4], "cidade": cv[5], "uf": cv[6],
            "whatsapp": cv[7] or cv[8], "email": cv[9], "vendedor": cv[11],
            "status_rot": STATUS_ROT.get(cv[10], cv[10] or "")}
    return JSONResponse({"ok": True, "lead": lead, "msgs": msgs,
                         "conversa_id": conversa_id, "pode_responder": pode_wa})


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
            """select cv.canal, cv.prospeccao_id, cv.contato_ref, p.whatsapp, p.telefone, p.vendedor_id
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, ctx["conta_id"])).fetchone()
        if not cv:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        if not ctx["gerencia"] and cv[5] != ctx["membro_id"]:
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
        canal = cv[0]
        if canal != "whatsapp":
            return JSONResponse({"ok": False, "erro": "canal_sem_resposta"})
        from finance import whatsapp_twilio as wa
        numero = cv[3] or cv[4] or cv[2]
        res = wa.enviar_texto(numero, texto)
        if not res.get("ok"):
            erros = {"nao_configurado": "WhatsApp não conectado (falta credencial no Render).",
                     "numero_invalido": "Número do lead inválido."}
            return JSONResponse({"ok": False, "erro": erros.get(res.get("erro"), "Não consegui enviar (janela de 24h fechada? use template).")})
        _registrar_msg(c, ctx["conta_id"], cv[1], "whatsapp", "out", "humano",
                       texto, ctx["membro_id"], res.get("sid"))
        c.commit()
    return JSONResponse({"ok": True})


@router.post("/webhooks/twilio")
async def webhook_twilio(request: Request):
    """Recebe mensagens do WhatsApp (Twilio). Valida a assinatura, acha/cria a
    conversa pelo telefone→lead e grava a mensagem (entrada). Abre a janela de 24h."""
    from finance import whatsapp_twilio as wa
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    assinatura = request.headers.get("X-Twilio-Signature", "")
    url = wa.url_webhook() or str(request.url)
    if not wa.validar_assinatura(url, params, assinatura):
        return Response(status_code=403)
    conta_id = wa.conta_dona()
    if not conta_id:
        return Response("<Response></Response>", media_type="application/xml")
    corpo = params.get("Body", "")
    remetente = _so_digitos(params.get("From", ""))
    sid = params.get("MessageSid") or params.get("SmsMessageSid")
    alvo8 = remetente[-8:] if len(remetente) >= 8 else remetente
    pool = get_pool()
    with pool.connection() as c:
        lead = c.execute(
            r"""select id from prospeccao
                 where conta_id=%s and right(regexp_replace(coalesce(whatsapp, telefone, ''), '\D', '', 'g'), 8) = %s
                 order by atualizado_em desc limit 1""", (conta_id, alvo8)).fetchone()
        lead_id = lead[0] if lead else None
        if lead_id:
            conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='whatsapp'",
                             (conta_id, lead_id)).fetchone()
        else:
            conv = c.execute("select id from conversas where conta_id=%s and contato_ref=%s and canal='whatsapp'",
                             (conta_id, remetente)).fetchone()
        if conv:
            conv_id = conv[0]
        else:
            conv_id = c.execute(
                """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status)
                   values (%s,%s,'whatsapp',%s,'aberta') returning id""",
                (conta_id, lead_id, remetente)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                     values (%s,'whatsapp','in','lead',%s,%s)""", (conv_id, corpo[:8000], sid))
        c.execute("""update conversas set ultima_msg_em=now(), status='aberta',
                       janela_expira_em=now()+interval '24 hours' where id=%s""", (conv_id,))
        c.commit()
    return Response("<Response></Response>", media_type="application/xml")


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


def _registrar_msg(c, conta_id, alvo_id, canal, direcao, autor, texto, membro_id=None, provider_sid=None):
    """Grava uma mensagem na conversa (cria a conversa se preciso) e atualiza o topo."""
    conv = _conversa_id(c, conta_id, alvo_id, canal)
    c.execute(
        """insert into mensagens (conversa_id, canal, direcao, autor, membro_id, texto, provider_sid)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (conv, canal, direcao, autor, membro_id, (texto or "")[:8000], provider_sid))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv,))
    return conv


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
    <div style="display:flex;gap:.5rem;flex-wrap:wrap">
      <a class="pbtn ghost" href="/painel/prospeccao/comunicacao" style="display:inline-flex;align-items:center">📨 Comunicação</a>
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
             onclick="if(!window._kbMoved)location.href='/painel/prospeccao/{{ c.id }}'">
          <div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" title="{{ c.temperatura }}" style="background:{{ temp_cor[c.temperatura] }}"></span><span class="emp">{{ c.empresa }}</span></div>
          {% if c.segmento or c.cidade %}<div class="sub">{% if c.segmento %}{{ c.segmento }}{% endif %}{% if c.cidade %} · {{ c.cidade }}{% if c.uf %}/{{ c.uf }}{% endif %}{% endif %}</div>{% endif %}
          <div class="ft">{% if c.valor %}<span style="font-size:.76rem;color:var(--verde-claro)">{{ brl(c.valor) }}</span>{% else %}<span></span>{% endif %}{% if c.proximo %}<span class="mut" style="font-size:.72rem">📅 {{ c.proximo.strftime('%d/%m') }}</span>{% endif %}</div>
          {% if gerencia and c.vendedor %}<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 {{ c.vendedor }}</div>{% endif %}
        </div>
        {% else %}<div class="kbempty">vazio</div>{% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<script>
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
function cardGo(id){if(!window._kbMoved)location.href='/painel/prospeccao/'+id;}
function updCounts(){var tot=0;document.querySelectorAll('.kbcol').forEach(function(col){var n=col.querySelectorAll('.kbcard').length;tot+=n;var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;var tc=document.querySelector('.kbtab[data-tab="'+col.getAttribute('data-status')+'"] .c');if(tc)tc.textContent=n;});var tn=document.getElementById('kb-total-n');if(tn)tn.textContent=tot;}
function addCard(l){var col=document.querySelector('.kbcol[data-status="novo"]');if(!col)return;var drop=col.querySelector('.kbdrop');var e=drop.querySelector('.kbempty');if(e)e.remove();
  var cor=TEMPCOR[l.temperatura]||'#5b9bd5';
  var sub=(l.segmento||l.cidade)?('<div class="sub">'+(l.segmento?jsEsc(l.segmento):'')+(l.cidade?(' · '+jsEsc(l.cidade)+(l.uf?('/'+jsEsc(l.uf)):'')):'')+'</div>'):'';
  var ft='<div class="ft">'+(l.valor?('<span style="font-size:.76rem;color:var(--verde-claro)">'+jsBrl(l.valor)+'</span>'):'<span></span>')+'<span></span></div>';
  var vd=l.vendedor?('<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 '+jsEsc(l.vendedor)+'</div>'):'';
  var html='<div class="kbcard" draggable="true" data-id="'+l.id+'" ondragstart="kbDrag(event,'+l.id+')" ondragend="kbEnd(event)" onclick="cardGo('+l.id+')"><div style="display:flex;align-items:center;gap:.4rem"><span class="tdot" style="background:'+cor+'"></span><span class="emp">'+jsEsc(l.empresa)+'</span></div>'+sub+ft+vd+'</div>';
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
    </div>
    {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
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
            <button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" onclick="prospToggle('edit-dados')">editar</button>
          </div>
        </div>
        <div id="cnpj-cands" style="margin:.2rem 0"></div>
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
@media(max-width:900px){.cx-grid{grid-template-columns:1fr}.cx-ctx{order:3}.cx-cc{grid-template-columns:1fr}}
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

  {% if aba=='conversas' %}
  <form class="cx-filtros" method="get" action="/painel/prospeccao/comunicacao">
    <input type="hidden" name="aba" value="conversas">
    <select class="fld" name="canal" style="width:auto" onchange="this.form.submit()">
      <option value="" {% if not canal %}selected{% endif %}>Todos os canais</option>
      <option value="email" {% if canal=='email' %}selected{% endif %}>✉️ E-mail</option>
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
    <div class="cx-list">
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
  <div class="cx-tbl">
    <table>
      <thead><tr><th style="width:88px">Data</th><th>Lead</th><th style="width:140px">Quem</th><th>Assunto / prévia</th></tr></thead>
      <tbody>
        {% for e in emails %}
        <tr><td class="mut" style="white-space:nowrap"><b style="color:var(--txt);display:block">{{ e.quando.strftime('%d/%m') if e.quando else '' }}</b>{{ e.quando.strftime('%H:%M') if e.quando else '' }}</td>
          <td><b>{{ e.empresa }}</b></td><td>{{ e.quem }}</td>
          <td><b style="font-size:.85rem">{{ e.cabecalho }}</b><div class="mut" style="font-size:.8rem">{{ e.preview }}</div></td></tr>
        {% else %}<tr><td colspan="4" class="mut" style="text-align:center;padding:2rem">Nenhum e-mail enviado ainda.</td></tr>{% endfor %}
      </tbody>
    </table>
  </div>

  {% elif aba=='agente' %}
  <div class="cx-card" style="margin-top:.8rem;text-align:center;padding:2rem">
    <div style="font-size:2rem">🤖</div>
    <h3 style="margin:.4rem 0">Agente IA</h3>
    <p class="mut" style="max-width:440px;margin:.2rem auto">Config (liga/desliga, confiança, autonomia, handoff) e treino (base de conhecimento) chegam na próxima entrega. A estrutura já está pronta aqui embaixo.</p>
  </div>

  {% else %}
  <p class="mut" style="margin:.8rem 0 0">Todos os canais num lugar só. As credenciais ficam no ambiente (Render); aqui você vê o status e conecta cada um quando o acesso libera.</p>
  <div class="cx-cc">
    <div class="cx-card">
      <h3>✉️ E-mail <span class="cx-stat {{ 'st-on' if canais.email else 'st-off' }}">● {{ 'Conectado' if canais.email else 'A configurar' }}</span></h3>
      <div class="cx-kv"><span>Remetente</span><b>{{ remetente or '—' }}</b></div>
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">SMTP (Google Workspace). Prospecção fria ✓</div>
    </div>
    <div class="cx-card">
      <h3>💬 WhatsApp <span class="cx-stat {{ 'st-on' if canais.whatsapp else 'st-off' }}">● {{ 'Conectado' if canais.whatsapp else 'Aguardando número' }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Twilio. Adquira o número, ponha no Render e conecta:</div>
      <div class="cx-env"><b>TWILIO_ACCOUNT_SID</b>=•••••<br><b>TWILIO_AUTH_TOKEN</b>=•••••<br><b>TWILIO_WHATSAPP_FROM</b>=whatsapp:+55••••<br><b>WHATSAPP_CONTA_ID</b>={{ conta[0] if conta else '•••' }}</div>
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">Webhook (no painel Twilio): <code>/webhooks/twilio</code> · fria ✓ (com template)</div>
    </div>
    <div class="cx-card">
      <h3>🔵 Messenger <span class="cx-stat {{ 'st-on' if canais.messenger else 'st-off' }}">● {{ 'Conectado' if canais.messenger else 'A conectar' }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Twilio (mesmo webhook). Precisa da Página do Facebook + revisão do app na Meta.</div>
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">📥 Canal de <b>resposta</b> (janela 24h) — não é abordagem fria.</div>
    </div>
    <div class="cx-card">
      <h3>📸 Instagram <span class="cx-stat {{ 'st-on' if canais.instagram else 'st-off' }}">● {{ 'Conectado' if canais.instagram else 'A conectar' }}</span></h3>
      <div class="mut" style="font-size:.8rem">Via Meta direto (a Twilio não cobre IG). Conta IG Profissional ligada à Página + App Review.</div>
      <div class="cx-env"><b>META_APP_ID</b>=•••••<br><b>META_PAGE_TOKEN</b>=•••••<br><b>IG_ACCOUNT_ID</b>=•••••</div>
      <div class="mut" style="margin-top:.4rem;font-size:.8rem">Webhook: <code>/webhooks/meta</code> · 📥 <b>resposta</b>.</div>
    </div>
  </div>
  {% endif %}
</div>
<script>
function cxEsc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function cxOpen(el,id){
  document.querySelectorAll('.cx-conv').forEach(function(x){x.classList.remove('on');});
  if(el)el.classList.add('on');
  var th=document.getElementById('cx-thread'),cx=document.getElementById('cx-ctx');
  th.innerHTML='<div class="cx-empty">Carregando…</div>';
  fetch('/painel/prospeccao/comunicacao/thread/'+id).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){th.innerHTML='<div class="cx-empty">Não consegui abrir.</div>';return;}
    var L=d.lead;
    var msgs='';
    d.msgs.forEach(function(m){
      var cls=(m.direcao==='in')?'cx-m cin':'cx-m';
      var cab=m.cabecalho?('<div class="cab">'+cxEsc(m.cabecalho)+'</div>'):'';
      var corpo=cxEsc(m.corpo||m.cabecalho).replace(/\\n/g,'<br>');
      msgs+='<div class="'+cls+'">'+cab+corpo+'<span class="meta">'+cxEsc(m.quem)+' · '+cxEsc(m.quando)+'</span></div>';
    });
    if(!d.msgs.length)msgs='<div class="cx-empty">Sem mensagens.</div>';
    var rodape;
    if(d.pode_responder){
      rodape='<div class="cx-comp"><textarea id="cx-reply" rows="2" placeholder="Escreva uma resposta…"></textarea>'
        +'<button class="pbtn" onclick="cxResponder('+d.conversa_id+')">Enviar</button></div>';
    }else{
      rodape='<div class="cx-stub">Responder por aqui<span class="lbl2">em breve</span> — disponível quando o canal estiver conectado (aba <b>Canais</b>). O <b>1º contato</b> sai pela ficha.</div>';
    }
    th.innerHTML=''
      +'<div class="cx-th"><div><b>'+cxEsc(L.empresa)+'</b><small>'+cxEsc(L.canal_rot||'')+(L.cidade?(' · '+cxEsc(L.cidade)+(L.uf?'/'+cxEsc(L.uf):'')):'')+(L.status_rot?(' · '+cxEsc(L.status_rot)):'')+'</small></div>'
      +'<span style="flex:1"></span>'+(L.id?('<a class="pbtn ghost" style="padding:.35rem .7rem;font-size:.78rem" href="/painel/prospeccao/'+L.id+'">Abrir ficha</a>'):'')+'</div>'
      +'<div class="cx-msgs">'+msgs+'</div>'+rodape;
    var kv=function(k,v){return v?('<div class="cx-kv"><span>'+k+'</span><b>'+cxEsc(v)+'</b></div>'):'';};
    cx.innerHTML=''
      +'<div class="cx-sec"><h4>Lead</h4>'+kv('Empresa',L.empresa)+kv('Segmento',L.segmento)+kv('Cidade',(L.cidade||'')+(L.uf?'/'+L.uf:''))+kv('WhatsApp',L.whatsapp)+kv('E-mail',L.email)+kv('Responsável',L.vendedor)+kv('Status',L.status_rot)+'</div>'
      +'<div class="cx-sec"><h4>🤖 Agente IA <span class="cx-stub" style="border:0;background:none;padding:0"><span class="lbl2">Fase 4</span></span></h4><div class="mut" style="font-size:.8rem;line-height:1.5">Atendimento automático com handoff pro vendedor chega numa próxima fase.</div></div>'
      +'<div class="cx-sec"><a class="pbtn" style="width:100%;text-align:center" href="/painel/prospeccao/'+L.id+'">Abrir ficha do lead</a></div>';
  }).catch(function(){th.innerHTML='<div class="cx-empty">Falha de rede.</div>';});
}
function cxResponder(convId){
  var ta=document.getElementById('cx-reply');if(!ta)return;var t=ta.value.trim();if(!t){return;}
  var btn=event&&event.target;if(btn){btn.disabled=true;btn.textContent='Enviando…';}
  var fd=new FormData();fd.append('conversa_id',convId);fd.append('texto',t);
  fetch('/painel/prospeccao/comunicacao/responder',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd})
    .then(function(r){return r.json();}).then(function(d){
      if(btn){btn.disabled=false;btn.textContent='Enviar';}
      if(!d.ok){alert(d.erro||'Não consegui enviar.');return;}
      var el=document.getElementById('cxc-'+convId);
      cxOpen(el,convId);
    }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Enviar';}alert('Falha de rede.');});
}
</script>
{% endblock %}"""

_env.loader.mapping["prospeccao"] = _KANBAN_TPL
_env.loader.mapping["prospeccao_captar"] = _CAPTAR_TPL
_env.loader.mapping["prospeccao_ficha"] = _FICHA_TPL
_env.loader.mapping["prospeccao_comunicacao"] = _COMUNICACAO_TPL
