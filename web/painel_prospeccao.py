"""Aba "Prospecção" do painel — pipeline de outbound do vendedor.

Ferramenta de prospecção ativa: cada vendedor trabalha uma lista de empresas-alvo
num kanban (novo → contatado → qualificado → proposta → ganho/perdido), abre a
ficha de cada alvo pra ver os dados enriquecidos, registrar contatos e agendar o
próximo. Dono e gestor veem a carteira inteira, filtram por vendedor e atribuem
alvos. Lead quente vira orçamento na Etapa 4 (por ora o botão fica preparado).

Reusa o motor do portal: _render/_env (base, gate, nav) + conta_logada + o login
por membro (contas.equipe). Escopo multi-tenant sagrado: toda query filtra por
conta[0]; o vendedor só enxerga os alvos atribuídos a ele.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from db.conexao import get_pool
from contas import equipe as eq
from web.portal import _render, _env, conta_logada

router = APIRouter()

# ---------------------------------------------------------------- domínio (rótulos)
STATUS = [
    ("novo", "Novo"),
    ("contatado", "Contatado"),
    ("qualificado", "Qualificado"),
    ("proposta", "Proposta"),
    ("ganho", "Ganho"),
    ("perdido", "Perdido"),
]
STATUS_OK = {s for s, _ in STATUS}
STATUS_ROT = dict(STATUS)
TEMPERATURAS = [("frio", "Frio"), ("morno", "Morno"), ("quente", "Quente")]
TEMP_OK = {t for t, _ in TEMPERATURAS}
TEMP_COR = {"frio": "#5b9bd5", "morno": "#e0a33e", "quente": "#e0574f"}
# pílula de temperatura (fundo/texto) — mesmo visual dos mockups.
TEMP_PILL = {
    "frio":  ("#14273a", "#7bb8e6"),
    "morno": ("#2e2713", "#e0b25a"),
    "quente": ("#3a1a1a", "#f0917f"),
}
TIPOS = [
    ("ligacao", "Ligação"), ("whatsapp", "WhatsApp"), ("email", "E-mail"),
    ("reuniao", "Reunião"), ("visita", "Visita"), ("nota", "Nota"),
]
TIPO_OK = {t for t, _ in TIPOS}
TIPO_ROT = dict(TIPOS)
RESULTADOS = [
    ("", "—"),
    ("sem_resposta", "Sem resposta"), ("retornar", "Retornar"),
    ("interessado", "Interessado"), ("sem_interesse", "Sem interesse"),
    ("agendado", "Agendado"), ("fechado", "Fechado"),
]
RESULTADO_OK = {r for r, _ in RESULTADOS if r}
RESULTADO_ROT = dict(RESULTADOS)
# cor da bolinha na timeline: verde = avançou, âmbar = travou, cinza = neutro.
_RES_VERDE = {"interessado", "agendado", "fechado"}
_RES_AMBAR = {"sem_resposta", "sem_interesse", "retornar"}


def _agora():
    return datetime.now(timezone.utc)


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _zap_link(numero: str) -> str:
    """Monta o link wa.me a partir de um telefone/whatsapp brasileiro."""
    d = _so_digitos(numero)
    if not d:
        return ""
    if not d.startswith("55"):
        d = "55" + d
    return "https://wa.me/" + d


# ---------------------------------------------------------------- acesso / escopo
def _acesso(request: Request):
    """Quem pode usar a Prospecção: qualquer papel com a capacidade 'vendas'
    (dono, gestor, vendedor). Devolve (ctx, None) ou (None, redirect).

    ctx = {conta, conta_id, papel, membro_id, gerencia}
      - gerencia (dono/gestor): vê a carteira inteira e atribui alvos.
      - vendedor: só enxerga e mexe nos alvos atribuídos a ele.
    """
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if not eq.caps_do_papel(papel).get("vendas"):
        return None, RedirectResponse("/painel", status_code=303)
    ctx = {
        "conta": conta,
        "conta_id": conta[0],
        "papel": papel,
        "membro_id": request.session.get("membro_id"),
        "gerencia": papel in ("dono", "gestor"),
    }
    return ctx, None


def _vendedores(pool, conta_id: int) -> list[dict]:
    """Membros que podem receber alvos: vendedores e gestores ativos da conta."""
    with pool.connection() as c:
        rows = c.execute(
            """select id, coalesce(nullif(nome,''), email), papel
                 from membros
                where conta_id=%s and ativo and papel in ('vendedor','gestor')
                order by nome""",
            (conta_id,),
        ).fetchall()
    return [{"id": r[0], "nome": r[1], "papel": r[2]} for r in rows]


def _carrega_alvo(pool, conta_id: int, alvo_id: int):
    """Ficha de um alvo (dict) ou None. Sempre com escopo por conta."""
    with pool.connection() as c:
        r = c.execute(
            """select p.id, p.empresa, p.cnpj, p.segmento, p.cidade, p.uf,
                      p.contato, p.cargo, p.telefone, p.whatsapp, p.email,
                      p.status, p.temperatura, p.valor_estimado_centavos, p.origem,
                      p.obs, p.instagram, p.socio, p.regime_tributario, p.porte,
                      p.ultimo_contato_em, p.proximo_contato_em, p.vendedor_id,
                      m.nome, p.orcamento_id, p.tem_site
                 from prospeccao p
                 left join membros m on m.id = p.vendedor_id
                where p.id=%s and p.conta_id=%s""",
            (alvo_id, conta_id),
        ).fetchone()
    if not r:
        return None
    cols = ["id", "empresa", "cnpj", "segmento", "cidade", "uf", "contato", "cargo",
            "telefone", "whatsapp", "email", "status", "temperatura", "valor",
            "origem", "obs", "instagram", "socio", "regime_tributario", "porte",
            "ultimo_contato_em", "proximo_contato_em", "vendedor_id", "vendedor_nome",
            "orcamento_id", "tem_site"]
    d = dict(zip(cols, r))
    d["zap_link"] = _zap_link(d["whatsapp"] or d["telefone"])
    d["tel_link"] = "tel:" + _so_digitos(d["telefone"]) if d["telefone"] else ""
    return d


def _pode_ver(alvo: dict, ctx: dict) -> bool:
    """Vendedor só acessa alvo atribuído a ele; gerência vê tudo."""
    if ctx["gerencia"]:
        return True
    return alvo["vendedor_id"] is not None and alvo["vendedor_id"] == ctx["membro_id"]


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
            tuple(params),
        ).fetchall()

    colunas = {s: [] for s, _ in STATUS}
    total_valor = 0
    for r in rows:
        card = {
            "id": r[0], "empresa": r[1], "segmento": r[2], "cidade": r[3], "uf": r[4],
            "status": r[5], "temperatura": r[6], "valor": r[7], "proximo": r[8],
            "telefone": r[9], "whatsapp": r[10], "vendedor_id": r[11], "vendedor": r[12],
        }
        colunas.get(r[5], colunas["novo"]).append(card)
        if r[5] != "perdido":
            total_valor += int(r[7] or 0)

    vends = _vendedores(pool, conta_id) if ctx["gerencia"] else []
    return _render(
        "prospeccao", request,
        titulo="Prospecção", secao_ativa="prospeccao",
        status=STATUS, colunas=colunas, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
        temperaturas_all=TEMPERATURAS,
        gerencia=ctx["gerencia"], vendedores=vends, filtro_vend=filtro_vend,
        total_valor=total_valor, total_alvos=len(rows),
        aviso=request.session.pop("prosp_aviso", None),
    )


# ================================================================ ADD MANUAL
@router.post("/painel/prospeccao/novo")
def prospeccao_novo(request: Request, empresa: str = Form(...), segmento: str = Form(""),
                    cidade: str = Form(""), uf: str = Form(""), contato: str = Form(""),
                    telefone: str = Form(""), whatsapp: str = Form(""), email: str = Form(""),
                    cnpj: str = Form(""), temperatura: str = Form("frio"),
                    valor: str = Form(""), origem: str = Form("manual"),
                    vendedor_id: str = Form(""), obs: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    empresa = (empresa or "").strip()
    if not empresa:
        request.session["prosp_aviso"] = "Informe ao menos o nome da empresa."
        return RedirectResponse("/painel/prospeccao", status_code=303)
    temperatura = temperatura if temperatura in TEMP_OK else "frio"

    if ctx["gerencia"]:
        vend = int(vendedor_id) if (vendedor_id or "").isdigit() else None
    else:
        vend = ctx["membro_id"]

    with get_pool().connection() as c:
        c.execute(
            """insert into prospeccao
                 (conta_id, vendedor_id, empresa, segmento, cidade, uf, contato,
                  telefone, whatsapp, email, cnpj, temperatura,
                  valor_estimado_centavos, origem, obs, criado_por)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ctx["conta_id"], vend, empresa, segmento.strip() or None,
             cidade.strip() or None, (uf or "").strip()[:2].upper() or None,
             contato.strip() or None, telefone.strip() or None, whatsapp.strip() or None,
             email.strip().lower() or None, cnpj.strip() or None, temperatura,
             _reais_para_centavos(valor), (origem or "manual").strip() or None,
             obs.strip() or None, ctx["membro_id"]),
        )
        c.commit()
    request.session["prosp_aviso"] = f"“{empresa}” entrou na prospecção."
    return RedirectResponse("/painel/prospeccao", status_code=303)


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
                where a.prospeccao_id=%s
                order by a.criado_em desc""",
            (alvo_id,),
        ).fetchall()
    timeline = []
    for (t, rr, d, ag, cr, nome) in ativs:
        cor = "#3ee0a6" if rr in _RES_VERDE else "#e0a33e" if rr in _RES_AMBAR else "#7a7a7a"
        timeline.append({
            "tipo_rot": TIPO_ROT.get(t, t), "resultado_rot": RESULTADO_ROT.get(rr or "", ""),
            "descricao": d, "agendado_para": ag, "criado_em": cr, "quem": nome, "cor": cor,
        })

    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render(
        "prospeccao_ficha", request,
        titulo=alvo["empresa"], secao_ativa="prospeccao",
        a=alvo, timeline=timeline, status=STATUS, temperaturas=TEMPERATURAS,
        tipos=TIPOS, resultados=RESULTADOS, temp_cor=TEMP_COR, temp_pill=TEMP_PILL,
        gerencia=ctx["gerencia"], vendedores=vends,
        aviso=request.session.pop("prosp_aviso", None),
    )


# ---------------------------------------------------------------- editar dados do alvo
@router.post("/painel/prospeccao/{alvo_id}/editar")
def prospeccao_editar(request: Request, alvo_id: int, contato: str = Form(""),
                      cargo: str = Form(""), telefone: str = Form(""), whatsapp: str = Form(""),
                      email: str = Form(""), cnpj: str = Form(""), segmento: str = Form(""),
                      cidade: str = Form(""), uf: str = Form(""), valor: str = Form(""),
                      socio: str = Form(""), regime_tributario: str = Form(""),
                      porte: str = Form(""), instagram: str = Form(""),
                      tem_site: str = Form(""), obs: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    alvo = _carrega_alvo(pool, ctx["conta_id"], alvo_id)
    if not alvo or not _pode_ver(alvo, ctx):
        return RedirectResponse("/painel/prospeccao", status_code=303)
    site = True if tem_site == "1" else False if tem_site == "0" else None
    with pool.connection() as c:
        c.execute(
            """update prospeccao set
                   contato=%s, cargo=%s, telefone=%s, whatsapp=%s, email=%s, cnpj=%s,
                   segmento=%s, cidade=%s, uf=%s, valor_estimado_centavos=%s,
                   socio=%s, regime_tributario=%s, porte=%s, instagram=%s, tem_site=%s,
                   obs=%s, atualizado_em=now()
                 where id=%s and conta_id=%s""",
            (contato.strip() or None, cargo.strip() or None, telefone.strip() or None,
             whatsapp.strip() or None, email.strip().lower() or None, cnpj.strip() or None,
             segmento.strip() or None, cidade.strip() or None,
             (uf or "").strip()[:2].upper() or None, _reais_para_centavos(valor),
             socio.strip() or None, regime_tributario.strip() or None, porte.strip() or None,
             instagram.strip() or None, site, obs.strip() or None, alvo_id, ctx["conta_id"]),
        )
        c.commit()
    request.session["prosp_aviso"] = "Dados atualizados."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- registrar contato
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
            """insert into prospeccao_atividades
                 (prospeccao_id, membro_id, tipo, resultado, descricao, agendado_para)
               values (%s,%s,%s,%s,%s,%s)""",
            (alvo_id, ctx["membro_id"], tipo, res, (descricao or "").strip(), prox),
        )
        novo_status = "contatado" if alvo["status"] == "novo" else alvo["status"]
        c.execute(
            """update prospeccao
                  set ultimo_contato_em = now(),
                      proximo_contato_em = coalesce(%s, proximo_contato_em),
                      status = %s,
                      atualizado_em = now()
                where id=%s and conta_id=%s""",
            (prox, novo_status, alvo_id, ctx["conta_id"]),
        )
        c.commit()
    request.session["prosp_aviso"] = "Contato registrado."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- editar temperatura
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
        c.execute(
            "update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
            (temperatura, alvo_id, ctx["conta_id"]),
        )
        c.commit()
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- atribuir vendedor (gerência)
@router.post("/painel/prospeccao/{alvo_id}/atribuir")
def prospeccao_atribuir(request: Request, alvo_id: int, vendedor_id: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if not ctx["gerencia"]:
        return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)
    vend = int(vendedor_id) if (vendedor_id or "").isdigit() else None
    with get_pool().connection() as c:
        if vend is not None:
            ok = c.execute("select 1 from membros where id=%s and conta_id=%s",
                           (vend, ctx["conta_id"])).fetchone()
            if not ok:
                vend = None
        c.execute(
            "update prospeccao set vendedor_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
            (vend, alvo_id, ctx["conta_id"]),
        )
        c.commit()
    request.session["prosp_aviso"] = "Alvo atribuído." if vend else "Alvo sem responsável."
    return RedirectResponse(f"/painel/prospeccao/{alvo_id}", status_code=303)


# ---------------------------------------------------------------- mover status (kanban drag)
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
        c.execute(
            "update prospeccao set status=%s, atualizado_em=now() where id=%s and conta_id=%s",
            (status, alvo_id, ctx["conta_id"]),
        )
        c.commit()
    return JSONResponse({"ok": True, "status": status})


# ---------------------------------------------------------------- helpers locais
def _reais_para_centavos(v: str) -> int:
    """'1.234,56' | '1234.56' | '1234' -> centavos. Tolerante a lixo -> 0."""
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
    """'YYYY-MM-DD' (input date) ou 'YYYY-MM-DDTHH:MM' -> datetime tz-aware, ou None."""
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
.pw{max-width:1180px;margin:0 auto}
.pw h2.tt{margin:0;font-size:1.35rem}
.pbtn{width:auto;margin:0;padding:.5rem .9rem;border-radius:9px;font-size:.86rem;font-weight:600;
  background:var(--verde);color:#fff;border:0;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem}
.pbtn:hover{background:var(--verde-hover)}
.pbtn.ghost{background:transparent;color:var(--txt-mut);border:1px solid var(--borda)}
.pbtn.ghost:hover{color:var(--txt);border-color:var(--verde)}
.pbtn[disabled]{opacity:.45;cursor:not-allowed}
.tpill{display:inline-flex;align-items:center;padding:.12rem .55rem;border-radius:999px;font-size:.72rem;font-weight:600;line-height:1.4}
.spill{display:inline-flex;align-items:center;padding:.14rem .6rem;border-radius:999px;font-size:.74rem;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt)}
.tdot{width:11px;height:11px;border-radius:50%;flex-shrink:0;display:inline-block}
/* ---- kanban ---- */
.kbwrap{overflow-x:auto;padding-bottom:.6rem;margin-top:1rem}
.kbrow{display:flex;gap:.75rem;min-width:min-content}
.kbcol{flex:0 0 250px;background:var(--bg);border:1px solid var(--borda);border-radius:14px;
  padding:.7rem;min-height:460px;display:flex;flex-direction:column}
.kbcol.dragover{border-color:var(--verde);box-shadow:0 0 0 1px var(--verde) inset}
.kbcol h4{margin:0 0 .55rem;font-size:.85rem;display:flex;align-items:center;justify-content:space-between}
.kbcnt{background:var(--card-2);border:1px solid var(--borda);border-radius:999px;padding:.02rem .5rem;font-size:.72rem;color:var(--txt-mut)}
.kbdrop{flex:1;display:flex;flex-direction:column;gap:.5rem}
.kbempty{border:1px dashed var(--borda);border-radius:10px;color:var(--txt-mut);font-size:.76rem;
  text-align:center;padding:1.1rem .5rem;opacity:.6}
.kbcard{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.6rem .65rem;cursor:pointer;transition:border-color .15s,transform .1s}
.kbcard:hover{border-color:var(--verde)}
.kbcard:active{transform:scale(.98)}
.kbcard .emp{font-size:.88rem;font-weight:600;line-height:1.2}
.kbcard .sub{color:var(--txt-mut);font-size:.74rem;margin-top:.22rem}
.kbcard .ft{display:flex;align-items:center;justify-content:space-between;gap:.3rem;margin-top:.42rem;flex-wrap:wrap}
/* ---- ficha ---- */
.fgrid{display:grid;grid-template-columns:1.05fr 1fr;gap:1rem;margin-top:1rem;align-items:start}
@media(max-width:820px){.fgrid{grid-template-columns:1fr}}
.fsec{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:1rem 1.1rem}
.fsec+.fsec{margin-top:1rem}
.fsec .sh{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem}
.fsec .sh b{font-size:.9rem}
.drow{display:grid;grid-template-columns:1.4rem 96px 1fr;gap:.55rem;align-items:baseline;padding:.5rem 0;border-top:1px solid var(--borda);font-size:.86rem}
.drow:first-of-type{border-top:0}
.drow .ic{color:var(--txt-mut);text-align:center}
.drow .lb{color:var(--txt-mut);font-size:.78rem}
.badge{display:inline-block;padding:.02rem .4rem;border-radius:6px;font-size:.68rem;font-weight:600;
  background:#123a26;color:#5fe0a5;border:1px solid #1d5c3c;margin-left:.35rem}
.tl{position:relative;padding:.15rem 0 .8rem 1rem;border-left:2px solid var(--borda);margin-left:.25rem}
.tl:last-child{padding-bottom:.1rem}
.tl .dt{position:absolute;left:-7px;top:.28rem;width:12px;height:12px;border-radius:50%;border:2px solid var(--card)}
.rcpills{display:flex;flex-wrap:wrap;gap:.4rem;margin:.2rem 0 .6rem}
.rcpill{width:auto;margin:0;padding:.35rem .75rem;border-radius:999px;font-size:.8rem;cursor:pointer;
  background:transparent;border:1px solid var(--borda);color:var(--txt-mut)}
.rcpill.on{background:var(--verde);border-color:var(--verde);color:#fff;font-weight:600}
.fld{width:100%;padding:.55rem .7rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-family:inherit;font-size:.9rem}
.chipin{display:inline-flex;align-items:center;gap:.4rem;border:1px solid var(--borda);border-radius:999px;
  padding:.3rem .7rem;color:var(--txt-mut);font-size:.8rem;background:var(--bg)}
.chipin input{border:0;background:transparent;color:var(--txt);padding:0;width:auto;font-size:.82rem}
.egrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem;margin-top:.5rem}
.egrid .full{grid-column:1/-1}
.lbl{display:block;color:var(--txt-mut);font-size:.72rem;margin-bottom:.15rem}
</style>"""

_KANBAN_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="pw">
  <div style="display:flex;align-items:flex-start;gap:.8rem;flex-wrap:wrap">
    <div style="flex:1;min-width:180px">
      <h2 class="tt">Prospecção</h2>
      <div class="mut" style="font-size:.82rem;margin-top:.15rem">
        {{ total_alvos }} alvo(s){% if total_valor %} · pipeline {{ brl(total_valor) }}{% endif %} · arraste os cards pra mover de etapa.
      </div>
    </div>
    <button type="button" class="pbtn" onclick="prospToggle('novo-alvo')">+ Novo alvo</button>
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

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

  <!-- novo alvo (oculto) -->
  <div id="novo-alvo" class="fsec" style="display:none;margin-top:1rem;max-width:760px">
    <div class="sh"><b>Novo alvo</b>
      <button type="button" class="pbtn ghost" onclick="prospToggle('novo-alvo')">Fechar</button></div>
    <form method="post" action="/painel/prospeccao/novo" class="egrid">
      <div class="full"><label class="lbl">Empresa *</label><input class="fld" name="empresa" required placeholder="Nome da empresa"></div>
      <div><label class="lbl">Segmento</label><input class="fld" name="segmento" placeholder="Ex: pet shop"></div>
      <div><label class="lbl">Cidade</label><input class="fld" name="cidade"></div>
      <div><label class="lbl">UF</label><input class="fld" name="uf" maxlength="2" style="text-transform:uppercase"></div>
      <div><label class="lbl">Contato</label><input class="fld" name="contato" placeholder="Pessoa"></div>
      <div><label class="lbl">Telefone</label><input class="fld" name="telefone"></div>
      <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp"></div>
      <div><label class="lbl">E-mail</label><input class="fld" name="email" type="email"></div>
      <div><label class="lbl">CNPJ</label><input class="fld" name="cnpj"></div>
      <div><label class="lbl">Valor estimado (R$)</label><input class="fld" name="valor" inputmode="decimal" placeholder="0,00"></div>
      <div><label class="lbl">Temperatura</label>
        <select class="fld" name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
      {% if gerencia %}
      <div><label class="lbl">Vendedor</label>
        <select class="fld" name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>
      {% endif %}
      <div class="full" style="display:flex;gap:.5rem;margin-top:.3rem">
        <button class="pbtn" style="margin:0">Adicionar</button>
      </div>
    </form>
  </div>

  <!-- kanban -->
  <div class="kbwrap">
    <div class="kbrow">
      {% for s, rot in status %}
      <div class="kbcol" data-status="{{ s }}" ondragover="kbOver(event)" ondragleave="kbLeave(event)" ondrop="kbDrop(event,'{{ s }}')">
        <h4><span>{{ rot }}</span><span class="kbcnt">{{ colunas[s]|length }}</span></h4>
        <div class="kbdrop">
          {% for c in colunas[s] %}
          <div class="kbcard" draggable="true" data-id="{{ c.id }}" ondragstart="kbDrag(event,{{ c.id }})" ondragend="kbEnd(event)"
               onclick="if(!window._kbMoved)location.href='/painel/prospeccao/{{ c.id }}'">
            <div style="display:flex;align-items:center;gap:.4rem">
              <span class="tdot" title="{{ c.temperatura }}" style="background:{{ temp_cor[c.temperatura] }}"></span>
              <span class="emp">{{ c.empresa }}</span>
            </div>
            {% if c.segmento or c.cidade %}<div class="sub">{% if c.segmento %}{{ c.segmento }}{% endif %}{% if c.cidade %} · {{ c.cidade }}{% if c.uf %}/{{ c.uf }}{% endif %}{% endif %}</div>{% endif %}
            <div class="ft">
              {% if c.valor %}<span style="font-size:.76rem;color:var(--verde-claro)">{{ brl(c.valor) }}</span>{% else %}<span></span>{% endif %}
              {% if c.proximo %}<span class="mut" style="font-size:.72rem">📅 {{ c.proximo.strftime('%d/%m') }}</span>{% endif %}
            </div>
            {% if gerencia and c.vendedor %}<div class="mut" style="font-size:.72rem;margin-top:.28rem">👤 {{ c.vendedor }}</div>{% endif %}
          </div>
          {% else %}
          <div class="kbempty">vazio</div>
          {% endfor %}
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
</div>

<script>
function prospToggle(id){var e=document.getElementById(id);e.style.display=(e.style.display==='none')?'block':'none';}
window._kbMoved=false;
function kbDrag(ev,id){ev.dataTransfer.setData('text/plain',id);ev.dataTransfer.effectAllowed='move';window._kbDragEl=ev.currentTarget;setTimeout(function(){ev.currentTarget.style.opacity='.35';},0);}
function kbEnd(ev){ev.currentTarget.style.opacity='';setTimeout(function(){window._kbMoved=false;},60);}
function kbOver(ev){ev.preventDefault();ev.currentTarget.classList.add('dragover');}
function kbLeave(ev){ev.currentTarget.classList.remove('dragover');}
function kbDrop(ev,status){
  ev.preventDefault();ev.currentTarget.classList.remove('dragover');
  var id=ev.dataTransfer.getData('text/plain');var card=window._kbDragEl;
  if(!id||!card)return;window._kbMoved=true;
  var drop=ev.currentTarget.querySelector('.kbdrop');
  var emp=drop.querySelector('.kbempty');if(emp)emp.remove();
  drop.appendChild(card);
  var body=new URLSearchParams();body.append('status',status);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();})
    .then(function(d){if(!d.ok){location.reload();return;}
      document.querySelectorAll('.kbcol').forEach(function(col){
        var n=col.querySelectorAll('.kbcard').length;var chip=col.querySelector('.kbcnt');if(chip)chip.textContent=n;
        var dp=col.querySelector('.kbdrop');if(n===0&&!dp.querySelector('.kbempty')){var e=document.createElement('div');e.className='kbempty';e.textContent='vazio';dp.appendChild(e);}
      });
    }).catch(function(){location.reload();});
}
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
          {% set tp = temp_pill[a.temperatura] %}
          <span class="tpill" style="background:{{ tp[0] }};color:{{ tp[1] }}">{{ a.temperatura }}</span>
        </div>
        <div class="mut" style="font-size:.82rem;margin-top:.25rem">
          {% if a.segmento %}{{ a.segmento }}{% endif %}{% if a.cidade %}{% if a.segmento %} · {% endif %}{{ a.cidade }}{% if a.uf %}/{{ a.uf }}{% endif %}{% endif %}
          {% if a.vendedor_nome %} · 👤 {{ a.vendedor_nome }}{% endif %}
        </div>
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
      <span style="flex:1"></span>
      <button type="button" class="pbtn" disabled title="Chega na Etapa 4 (conversão)">📄 Gerar orçamento</button>
    </div>
    {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  </div>

  <div class="fgrid">
    <!-- esquerda: dados -->
    <div>
      <div class="fsec">
        <div class="sh"><b>Dados</b>
          <button type="button" class="pbtn ghost" style="padding:.3rem .7rem;font-size:.78rem" onclick="prospToggle('edit-dados')">editar</button></div>

        {% if a.contato %}<div class="drow"><span class="ic">👤</span><span class="lb">Contato</span><span>{{ a.contato }}{% if a.cargo %} · {{ a.cargo }}{% endif %}</span></div>{% endif %}
        {% if a.cnpj %}<div class="drow"><span class="ic">🏢</span><span class="lb">CNPJ</span><span>{{ a.cnpj }}</span></div>{% endif %}
        {% if a.socio %}<div class="drow"><span class="ic">🧑‍💼</span><span class="lb">Sócio</span><span>{{ a.socio }}</span></div>{% endif %}
        {% if a.regime_tributario or a.porte %}<div class="drow"><span class="ic">📑</span><span class="lb">Regime</span><span>{{ a.regime_tributario or '—' }}{% if a.porte %} · porte {{ a.porte }}{% endif %}</span></div>{% endif %}
        {% if a.telefone %}<div class="drow"><span class="ic">📞</span><span class="lb">Telefone</span><span>{{ a.telefone }}</span></div>{% endif %}
        {% if a.whatsapp %}<div class="drow"><span class="ic">💬</span><span class="lb">WhatsApp</span><span>{{ a.whatsapp }}<span class="badge">Business?</span></span></div>{% endif %}
        {% if a.email %}<div class="drow"><span class="ic">✉️</span><span class="lb">E-mail</span><span>{{ a.email }}</span></div>{% endif %}
        {% if a.instagram %}<div class="drow"><span class="ic">📷</span><span class="lb">Instagram</span><span>{{ a.instagram }}</span></div>{% endif %}
        {% if a.tem_site is not none %}<div class="drow"><span class="ic">🌐</span><span class="lb">Site</span><span>{% if a.tem_site %}tem site{% else %}<span style="color:#e0574f">não tem</span>{% endif %}</span></div>{% endif %}
        {% if a.valor %}<div class="drow"><span class="ic">💰</span><span class="lb">Valor est.</span><span style="color:var(--verde-claro)">{{ brl(a.valor) }}</span></div>{% endif %}
        {% if a.proximo_contato_em %}<div class="drow"><span class="ic">📅</span><span class="lb">Próximo</span><span style="color:var(--verde-claro)">{{ a.proximo_contato_em.strftime('%d/%m/%Y') }}</span></div>{% endif %}
        {% if a.obs %}<div class="drow"><span class="ic">📝</span><span class="lb">Obs</span><span>{{ a.obs }}</span></div>{% endif %}
        {% if not (a.contato or a.cnpj or a.socio or a.telefone or a.whatsapp or a.email or a.instagram or a.valor) %}
          <div class="mut" style="font-size:.82rem">Sem dados ainda. Clique em <b>editar</b> pra preencher (a enriquecimento automática — CNPJ/WhatsApp/Instagram — chega na Etapa 3).</div>
        {% endif %}

        <!-- form editar (oculto) -->
        <form id="edit-dados" method="post" action="/painel/prospeccao/{{ a.id }}/editar" style="display:none;margin-top:.8rem;border-top:1px solid var(--borda);padding-top:.8rem">
          <div class="egrid">
            <div><label class="lbl">Contato</label><input class="fld" name="contato" value="{{ a.contato or '' }}"></div>
            <div><label class="lbl">Cargo</label><input class="fld" name="cargo" value="{{ a.cargo or '' }}"></div>
            <div><label class="lbl">Telefone</label><input class="fld" name="telefone" value="{{ a.telefone or '' }}"></div>
            <div><label class="lbl">WhatsApp</label><input class="fld" name="whatsapp" value="{{ a.whatsapp or '' }}"></div>
            <div><label class="lbl">E-mail</label><input class="fld" name="email" value="{{ a.email or '' }}"></div>
            <div><label class="lbl">CNPJ</label><input class="fld" name="cnpj" value="{{ a.cnpj or '' }}"></div>
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
            <div class="full"><label class="lbl">Observações</label><input class="fld" name="obs" value="{{ a.obs or '' }}"></div>
          </div>
          <div style="display:flex;gap:.5rem;margin-top:.7rem">
            <button class="pbtn" style="margin:0">Salvar dados</button>
            <button type="button" class="pbtn ghost" onclick="prospToggle('edit-dados')">Cancelar</button>
          </div>
        </form>
      </div>

      <div class="fsec">
        <div class="sh"><b>Temperatura</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/temperatura" style="margin:0">
          <div class="rcpills">
            {% for t,rot in temperaturas %}
            <button type="submit" name="temperatura" value="{{ t }}" class="rcpill {% if t==a.temperatura %}on{% endif %}"
              style="{% if t==a.temperatura %}background:{{ temp_cor[t] }};border-color:{{ temp_cor[t] }}{% endif %}">{{ rot }}</button>
            {% endfor %}
          </div>
        </form>
        {% if gerencia %}
        <form method="post" action="/painel/prospeccao/{{ a.id }}/atribuir" style="margin-top:.6rem">
          <label class="lbl">Vendedor responsável</label>
          <select class="fld" name="vendedor_id" onchange="this.form.submit()">
            <option value="">— sem responsável —</option>
            {% for v in vendedores %}<option value="{{ v.id }}" {% if v.id==a.vendedor_id %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
          </select>
        </form>
        {% endif %}
      </div>
    </div>

    <!-- direita: registrar contato + histórico -->
    <div>
      <div class="fsec">
        <div class="sh"><b>Registrar contato</b></div>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/contato" style="margin:0">
          <div class="rcpills" id="rc-pills">
            {% for t,rot in tipos %}
            <button type="button" class="rcpill {% if loop.first %}on{% endif %}" data-tipo="{{ t }}" onclick="rcPick(this)">{{ rot }}</button>
            {% endfor %}
          </div>
          <input type="hidden" name="tipo" id="rc-tipo" value="ligacao">
          <textarea class="fld" name="descricao" rows="2" placeholder="O que rolou nesse contato?"></textarea>
          <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-top:.6rem">
            <select class="fld" name="resultado" style="width:auto">
              {% for r,rot in resultados %}<option value="{{ r }}">{{ rot if r else 'Resultado…' }}</option>{% endfor %}
            </select>
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
        <div class="tl">
          <span class="dt" style="background:{{ ev.cor }}"></span>
          <div style="font-size:.86rem"><b>{{ ev.tipo_rot }}</b>{% if ev.resultado_rot %} — <span class="mut">{{ ev.resultado_rot }}</span>{% endif %}</div>
          {% if ev.descricao %}<div style="font-size:.83rem;margin-top:.12rem">{{ ev.descricao }}</div>{% endif %}
          <div class="mut" style="font-size:.72rem;margin-top:.2rem">
            {{ ev.criado_em.strftime('%d/%m/%Y %H:%M') if ev.criado_em else '' }}{% if ev.quem %} · {{ ev.quem }}{% endif %}{% if ev.agendado_para %} · próximo {{ ev.agendado_para.strftime('%d/%m') }}{% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>
</div>

<script>
function prospToggle(id){var e=document.getElementById(id);e.style.display=(e.style.display==='none')?'block':'none';}
function rcPick(el){var box=document.getElementById('rc-pills');box.querySelectorAll('.rcpill').forEach(function(b){b.classList.remove('on');});el.classList.add('on');document.getElementById('rc-tipo').value=el.getAttribute('data-tipo');}
</script>
{% endblock %}"""

_env.loader.mapping["prospeccao"] = _KANBAN_TPL
_env.loader.mapping["prospeccao_ficha"] = _FICHA_TPL
