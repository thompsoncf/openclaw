"""Aba "Prospecção" do painel — pipeline de outbound do vendedor.

Ferramenta de prospecção ativa: cada vendedor trabalha uma lista de empresas-alvo
num kanban (novo → contatado → qualificado → proposta → ganho/perdido), abre a
ficha de cada alvo pra registrar contatos (ligação/whats/e-mail/…) e agendar o
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
TEMPERATURAS = [("frio", "Frio"), ("morno", "Morno"), ("quente", "Quente")]
TEMP_OK = {t for t, _ in TEMPERATURAS}
TEMP_COR = {"frio": "#5b9bd5", "morno": "#e0a33e", "quente": "#e0574f"}
TIPOS = [
    ("ligacao", "Ligação"), ("whatsapp", "WhatsApp"), ("email", "E-mail"),
    ("reuniao", "Reunião"), ("visita", "Visita"), ("nota", "Nota"),
]
TIPO_OK = {t for t, _ in TIPOS}
RESULTADOS = [
    ("", "—"),
    ("sem_resposta", "Sem resposta"), ("retornar", "Retornar"),
    ("interessado", "Interessado"), ("sem_interesse", "Sem interesse"),
    ("agendado", "Agendado"), ("fechado", "Fechado"),
]
RESULTADO_OK = {r for r, _ in RESULTADOS if r}


def _agora():
    return datetime.now(timezone.utc)


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
    return dict(zip(cols, r))


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
        # vendedor: só a própria carteira
        where.append("p.vendedor_id = %s")
        params.append(ctx["membro_id"])
    else:
        # gerência: filtro opcional por vendedor (?vendedor=<id> ou 'nao' = sem dono)
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
        if r[5] not in ("perdido",):
            total_valor += int(r[7] or 0)

    vends = _vendedores(pool, conta_id) if ctx["gerencia"] else []
    return _render(
        "prospeccao", request,
        titulo="Prospecção", secao_ativa="prospeccao",
        status=STATUS, colunas=colunas, temp_cor=TEMP_COR,
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

    # vendedor: alvo já nasce na carteira dele. gerência: escolhe (ou deixa livre).
    if ctx["gerencia"]:
        vend = int(vendedor_id) if (vendedor_id or "").isdigit() else None
    else:
        vend = ctx["membro_id"]

    with pool_conn() as c:
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
    timeline = [{"tipo": t, "tipo_rot": dict(TIPOS).get(t, t), "resultado": rr,
                 "resultado_rot": dict(RESULTADOS).get(rr or "", ""), "descricao": d,
                 "agendado_para": ag, "criado_em": cr, "quem": nome}
                for (t, rr, d, ag, cr, nome) in ativs]

    vends = _vendedores(pool, ctx["conta_id"]) if ctx["gerencia"] else []
    return _render(
        "prospeccao_ficha", request,
        titulo=alvo["empresa"], secao_ativa="prospeccao",
        a=alvo, timeline=timeline, status=STATUS, temperaturas=TEMPERATURAS,
        tipos=TIPOS, resultados=RESULTADOS, temp_cor=TEMP_COR,
        gerencia=ctx["gerencia"], vendedores=vends,
        aviso=request.session.pop("prosp_aviso", None),
    )


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
        # o alvo "esquenta": último contato = agora, próximo = agendado, e sai do 'novo'
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
    with pool_conn() as c:
        # o vendedor destino tem que ser da mesma conta (defesa multi-tenant)
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
    # aceita form-encoded (drag JS) ou fallback via query
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
def pool_conn():
    return get_pool().connection()


def _reais_para_centavos(v: str) -> int:
    """'1.234,56' | '1234.56' | '1234' -> centavos. Tolerante a lixo -> 0."""
    s = (v or "").strip()
    if not s:
        return 0
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:                      # formato BR: ponto = milhar, vírgula = decimal
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


# ================================================================ TEMPLATES
_KANBAN_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card larga" style="max-width:none">
  <div style="display:flex;align-items:center;gap:.8rem;flex-wrap:wrap">
    <div style="flex:1;min-width:180px">
      <h2 style="margin:0">Prospecção</h2>
      <div class="mut" style="font-size:.82rem">
        {{ total_alvos }} alvo(s){% if total_valor %} · pipeline {{ brl(total_valor) }}{% endif %} ·
        arraste os cards pra mover de etapa.
      </div>
    </div>
    <button type="button" class="aba on" style="width:auto" onclick="prospToggle('novo-alvo')">+ Novo alvo</button>
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

  <!-- formulário de novo alvo (oculto) -->
  <div id="novo-alvo" style="display:none;margin-top:1rem;padding:1rem;border:1px solid var(--borda);border-radius:12px;background:var(--bg)">
    <form method="post" action="/painel/prospeccao/novo" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;align-items:end">
      <div style="grid-column:1/-1"><label class="mut" style="font-size:.72rem">Empresa *</label><input name="empresa" required placeholder="Nome da empresa"></div>
      <div><label class="mut" style="font-size:.72rem">Segmento</label><input name="segmento" placeholder="Ex: pet shop"></div>
      <div><label class="mut" style="font-size:.72rem">Cidade</label><input name="cidade"></div>
      <div><label class="mut" style="font-size:.72rem">UF</label><input name="uf" maxlength="2" style="text-transform:uppercase"></div>
      <div><label class="mut" style="font-size:.72rem">Contato</label><input name="contato" placeholder="Pessoa"></div>
      <div><label class="mut" style="font-size:.72rem">Telefone</label><input name="telefone"></div>
      <div><label class="mut" style="font-size:.72rem">WhatsApp</label><input name="whatsapp"></div>
      <div><label class="mut" style="font-size:.72rem">E-mail</label><input name="email" type="email"></div>
      <div><label class="mut" style="font-size:.72rem">CNPJ</label><input name="cnpj"></div>
      <div><label class="mut" style="font-size:.72rem">Valor estimado (R$)</label><input name="valor" inputmode="decimal" placeholder="0,00"></div>
      <div><label class="mut" style="font-size:.72rem">Temperatura</label>
        <select name="temperatura">{% for v,l in temperaturas_all %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
      {% if gerencia %}
      <div><label class="mut" style="font-size:.72rem">Vendedor</label>
        <select name="vendedor_id"><option value="">— livre —</option>{% for v in vendedores %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}</select></div>
      {% endif %}
      <div style="grid-column:1/-1;display:flex;gap:.5rem">
        <button style="width:auto;margin:0">Adicionar</button>
        <button type="button" class="aba" style="width:auto;margin:0" onclick="prospToggle('novo-alvo')">Cancelar</button>
      </div>
    </form>
  </div>

  <!-- kanban -->
  <div class="kb-scroll" style="margin-top:1rem;overflow-x:auto;padding-bottom:.5rem">
    <div style="display:flex;gap:.7rem;min-width:min-content">
      {% for s, rot in status %}
      <div class="kb-col" data-status="{{ s }}"
           style="flex:0 0 240px;background:var(--bg);border:1px solid var(--borda);border-radius:12px;padding:.6rem"
           ondragover="kbOver(event)" ondragleave="kbLeave(event)" ondrop="kbDrop(event,'{{ s }}')">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem">
          <b style="font-size:.85rem">{{ rot }}</b>
          <span class="chip" style="padding:.05rem .45rem">{{ colunas[s]|length }}</span>
        </div>
        <div class="kb-drop" style="min-height:40px;display:flex;flex-direction:column;gap:.5rem">
          {% for c in colunas[s] %}
          <div class="kb-card" draggable="true" data-id="{{ c.id }}" ondragstart="kbDrag(event,{{ c.id }})" ondragend="kbEnd(event)"
               onclick="if(!window._kbMoved)location.href='/painel/prospeccao/{{ c.id }}'"
               style="background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.55rem .6rem;cursor:pointer">
            <div style="display:flex;align-items:center;gap:.4rem">
              <span title="{{ c.temperatura }}" style="width:11px;height:11px;border-radius:50%;flex-shrink:0;background:{{ temp_cor[c.temperatura] }}"></span>
              <b style="font-size:.86rem;line-height:1.15">{{ c.empresa }}</b>
            </div>
            <div class="mut" style="font-size:.74rem;margin-top:.25rem">
              {% if c.segmento %}{{ c.segmento }}{% endif %}{% if c.cidade %} · {{ c.cidade }}{% if c.uf %}/{{ c.uf }}{% endif %}{% endif %}
            </div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-top:.35rem;gap:.3rem;flex-wrap:wrap">
              {% if c.valor %}<span style="font-size:.74rem;color:var(--verde-claro)">{{ brl(c.valor) }}</span>{% else %}<span></span>{% endif %}
              {% if c.proximo %}<span class="mut" style="font-size:.7rem">📅 {{ c.proximo.strftime('%d/%m') }}</span>{% endif %}
            </div>
            {% if gerencia and c.vendedor %}<div class="mut" style="font-size:.7rem;margin-top:.25rem">👤 {{ c.vendedor }}</div>{% endif %}
          </div>
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
function kbEnd(ev){ev.currentTarget.style.opacity='';setTimeout(function(){window._kbMoved=false;},50);}
function kbOver(ev){ev.preventDefault();ev.currentTarget.style.borderColor='var(--verde)';}
function kbLeave(ev){ev.currentTarget.style.borderColor='var(--borda)';}
function kbDrop(ev,status){
  ev.preventDefault();ev.currentTarget.style.borderColor='var(--borda)';
  var id=ev.dataTransfer.getData('text/plain');var card=window._kbDragEl;
  if(!id||!card)return;
  window._kbMoved=true;
  var drop=ev.currentTarget.querySelector('.kb-drop');drop.appendChild(card);
  var body=new URLSearchParams();body.append('status',status);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();})
    .then(function(d){if(!d.ok){location.reload();}else{
      // atualiza contadores
      document.querySelectorAll('.kb-col').forEach(function(col){
        var n=col.querySelectorAll('.kb-card').length;var chip=col.querySelector('.chip');if(chip)chip.textContent=n;
      });
    }})
    .catch(function(){location.reload();});
}
</script>
{% endblock %}"""

_FICHA_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
    <a href="/painel/prospeccao" class="mut" style="text-decoration:none;font-size:.85rem">‹ Prospecção</a>
  </div>
  <div style="display:flex;align-items:center;gap:.5rem;margin-top:.3rem;flex-wrap:wrap">
    <span title="{{ a.temperatura }}" style="width:14px;height:14px;border-radius:50%;background:{{ temp_cor[a.temperatura] }}"></span>
    <h2 style="margin:0">{{ a.empresa }}</h2>
    <span class="tag">{{ dict(status)[a.status] if a.status in dict(status) else a.status }}</span>
  </div>
  <div class="mut" style="font-size:.82rem;margin-top:.2rem">
    {% if a.segmento %}{{ a.segmento }}{% endif %}{% if a.cidade %} · {{ a.cidade }}{% if a.uf %}/{{ a.uf }}{% endif %}{% endif %}
    {% if a.vendedor_nome %} · 👤 {{ a.vendedor_nome }}{% endif %}
    {% if a.valor %} · <span style="color:var(--verde-claro)">{{ brl(a.valor) }}</span>{% endif %}
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;margin-top:1rem">
    <!-- coluna esquerda: dados + controles -->
    <div>
      <div class="metric">
        <b style="font-size:.85rem">Dados</b>
        <div class="mut" style="font-size:.82rem;margin-top:.5rem;line-height:1.7">
          {% if a.contato %}<div>👤 {{ a.contato }}{% if a.cargo %} · {{ a.cargo }}{% endif %}</div>{% endif %}
          {% if a.telefone %}<div>📞 {{ a.telefone }}</div>{% endif %}
          {% if a.whatsapp %}<div>💬 {{ a.whatsapp }}</div>{% endif %}
          {% if a.email %}<div>✉️ {{ a.email }}</div>{% endif %}
          {% if a.cnpj %}<div>🏢 {{ a.cnpj }}</div>{% endif %}
          {% if a.socio %}<div>Sócio: {{ a.socio }}</div>{% endif %}
          {% if a.regime_tributario %}<div>Regime: {{ a.regime_tributario }}</div>{% endif %}
          {% if a.porte %}<div>Porte: {{ a.porte }}</div>{% endif %}
          {% if a.instagram %}<div>📷 {{ a.instagram }}</div>{% endif %}
          {% if a.origem %}<div class="mut">Origem: {{ a.origem }}</div>{% endif %}
          {% if a.ultimo_contato_em %}<div class="mut">Último contato: {{ a.ultimo_contato_em.strftime('%d/%m/%Y') }}</div>{% endif %}
          {% if a.proximo_contato_em %}<div style="color:var(--verde-claro)">Próximo: {{ a.proximo_contato_em.strftime('%d/%m/%Y') }}</div>{% endif %}
          {% if a.obs %}<div style="margin-top:.4rem">{{ a.obs }}</div>{% endif %}
        </div>
      </div>

      <div class="metric" style="margin-top:.7rem">
        <b style="font-size:.85rem">Etapa & temperatura</b>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/status" style="margin-top:.5rem">
          <select name="status" onchange="this.form.submit()">
            {% for s,rot in status %}<option value="{{ s }}" {% if s==a.status %}selected{% endif %}>{{ rot }}</option>{% endfor %}
          </select>
        </form>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/temperatura" style="margin-top:.5rem">
          <select name="temperatura" onchange="this.form.submit()">
            {% for t,rot in temperaturas %}<option value="{{ t }}" {% if t==a.temperatura %}selected{% endif %}>{{ rot }}</option>{% endfor %}
          </select>
        </form>
        {% if gerencia %}
        <form method="post" action="/painel/prospeccao/{{ a.id }}/atribuir" style="margin-top:.5rem">
          <label class="mut" style="font-size:.72rem">Vendedor</label>
          <select name="vendedor_id" onchange="this.form.submit()">
            <option value="">— sem responsável —</option>
            {% for v in vendedores %}<option value="{{ v.id }}" {% if v.id==a.vendedor_id %}selected{% endif %}>{{ v.nome }}</option>{% endfor %}
          </select>
        </form>
        {% endif %}
      </div>

      <div class="metric" style="margin-top:.7rem">
        <b style="font-size:.85rem">Conversão</b>
        <div class="mut" style="font-size:.8rem;margin-top:.4rem">Virar orçamento/cliente chega na próxima etapa do módulo.</div>
        <button type="button" disabled style="margin-top:.6rem;opacity:.5;cursor:not-allowed">Gerar orçamento (em breve)</button>
      </div>
    </div>

    <!-- coluna direita: registrar contato + timeline -->
    <div>
      <div class="metric">
        <b style="font-size:.85rem">Registrar contato</b>
        <form method="post" action="/painel/prospeccao/{{ a.id }}/contato" style="margin-top:.5rem">
          <div style="display:flex;gap:.5rem;flex-wrap:wrap">
            <div style="flex:1;min-width:120px"><label class="mut" style="font-size:.72rem">Tipo</label>
              <select name="tipo">{% for t,rot in tipos %}<option value="{{ t }}">{{ rot }}</option>{% endfor %}</select></div>
            <div style="flex:1;min-width:120px"><label class="mut" style="font-size:.72rem">Resultado</label>
              <select name="resultado">{% for r,rot in resultados %}<option value="{{ r }}">{{ rot }}</option>{% endfor %}</select></div>
          </div>
          <label class="mut" style="font-size:.72rem;margin-top:.4rem;display:block">O que rolou</label>
          <textarea name="descricao" rows="2" placeholder="Anotações do contato" style="width:100%;padding:.6rem .8rem;border-radius:8px;border:1px solid #333;background:var(--bg);color:var(--txt);font-family:inherit"></textarea>
          <label class="mut" style="font-size:.72rem;margin-top:.4rem;display:block">Próximo contato (agendar)</label>
          <input type="date" name="proximo">
          <button style="margin-top:.7rem">Salvar contato</button>
        </form>
      </div>

      <div class="metric" style="margin-top:.7rem">
        <b style="font-size:.85rem">Histórico</b>
        {% if not timeline %}<p class="mut" style="margin-top:.4rem">Nenhum contato registrado ainda.</p>{% endif %}
        <div style="margin-top:.5rem">
          {% for ev in timeline %}
          <div style="border-left:2px solid var(--borda);padding:.1rem 0 .8rem .8rem;position:relative">
            <span style="position:absolute;left:-6px;top:.2rem;width:10px;height:10px;border-radius:50%;background:var(--verde)"></span>
            <div style="font-size:.85rem"><b>{{ ev.tipo_rot }}</b>{% if ev.resultado_rot %} · <span class="mut">{{ ev.resultado_rot }}</span>{% endif %}</div>
            {% if ev.descricao %}<div style="font-size:.82rem;margin-top:.15rem">{{ ev.descricao }}</div>{% endif %}
            <div class="mut" style="font-size:.72rem;margin-top:.2rem">
              {{ ev.criado_em.strftime('%d/%m/%Y %H:%M') if ev.criado_em else '' }}{% if ev.quem %} · {{ ev.quem }}{% endif %}
              {% if ev.agendado_para %} · próximo {{ ev.agendado_para.strftime('%d/%m') }}{% endif %}
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %}"""

# `dict` e a lista completa de temperaturas ficam disponíveis no template.
_env.globals.setdefault("dict", dict)
_env.loader.mapping["prospeccao"] = _KANBAN_TPL
_env.loader.mapping["prospeccao_ficha"] = _FICHA_TPL
