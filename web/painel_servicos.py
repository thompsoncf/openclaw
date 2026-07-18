"""Aba "Serviços" do painel (PJ) — Vendas de Serviços DENTRO da página da empresa.

Antes essa tela morava no /admin (área do operador Zaq). Estava no lugar errado:
o orçamento é feito PELA empresa, na página dela, escopado por conta_id. Aqui ela
vive no /painel/servicos — reusa o gate/nav/base do portal (conta logada + módulo
PJ + vende_servico) e escopa TUDO por conta[0].

Fluxo: captação (CNPJ autofill via Receita/BrasilAPI) → monta a proposta (módulos,
plano, parâmetros) → salva no funil → "Fechar contrato" chama
finance.vendas.fechar_orcamento, que vira TÍTULOS A RECEBER no módulo Empresa
(setup único + mensalidade recorrente). Sem PDV novo pra serviço: a receita entra
pelo livro-caixa de sempre.

O catálogo de serviços é POR CONTA (finance.servicos_catalogo) — cada empresa
monta o que vende. Empresa nova começa vazia; a Aladdin usa o modelo de
tecnologia. A IA de escopo e a validação de módulos usam o catálogo da conta.
"""
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from core.brain import Brain
from db.conexao import get_pool
from finance.cnpj_info import consultar_cnpj
from finance import empresa as emp, vendas, servicos_catalogo as scat
from web.portal import _render, _env, conta_logada, brl

router = APIRouter()


def _garantir_tabela(c):
    """Cria/atualiza a tabela orcamentos em runtime (deploy nao roda migracao
    sozinho; add-if-not-exists e' idempotente e barato). Espelha as migracoes
    068/069/070."""
    c.execute("""
        create table if not exists orcamentos (
            id                     bigserial primary key,
            cliente                text,
            empresa                text,
            segmento               text,
            setup_centavos         bigint default 0,
            mensal_centavos        bigint default 0,
            primeiro_ano_centavos  bigint default 0,
            n_modulos              int default 0,
            criado_em              timestamptz default now()
        )""")
    c.execute("""
        alter table orcamentos add column if not exists cnpj          text;
        alter table orcamentos add column if not exists whatsapp      text;
        alter table orcamentos add column if not exists email         text;
        alter table orcamentos add column if not exists modulos       jsonb;
        alter table orcamentos add column if not exists escopo        text;
        alter table orcamentos add column if not exists status        text not null default 'rascunho';
        alter table orcamentos add column if not exists criado_por    text;
        alter table orcamentos add column if not exists canal         text;
        alter table orcamentos add column if not exists follow_up_em  date;
        alter table orcamentos add column if not exists atualizado_em timestamptz not null default now();
        alter table orcamentos add column if not exists conta_id      bigint references contas(id) on delete restrict;
        create index if not exists idx_orcamentos_status on orcamentos (status, criado_em desc);
        create index if not exists idx_orcamentos_conta on orcamentos (conta_id, status, criado_em desc);
    """)
    c.commit()


def _conta_servico(request: Request):
    """Gate da aba: conta logada + acesso PJ + vende serviço. Devolve (conta, None)
    ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if not conta[12]:                       # [12] = acesso_pj
        return None, RedirectResponse("/painel", status_code=303)
    if not conta[14]:                       # [14] = vende_servico
        return None, RedirectResponse("/painel/empresa", status_code=303)
    return conta, None


# ---------------------------------------------------------------- rotas
@router.get("/painel/servicos", response_class=HTMLResponse)
def painel_servicos(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return redir
    pool = get_pool()
    with pool.connection() as c:
        _garantir_tabela(c)
    scat.garantir_tabela(pool)
    return _render("servicos", request, empresa_nome=conta[2],
                   tem_pj=True, vende_servico=True)


# ---------------------------------------------------------------- catálogo (por conta)
@router.get("/painel/servicos/catalogo")
def painel_servicos_catalogo(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    scat.garantir_tabela(pool)
    itens = [{
        "id": s["id"], "slug": s["slug"], "nome": s["nome"],
        "descricao": s["descricao"],
        "setup": round(s["setup_centavos"] / 100),
        "mensal": round(s["mensal_centavos"] / 100),
        "custo": round(s["custo_centavos"] / 100),
    } for s in scat.listar(pool, conta[0])]
    return JSONResponse({"itens": itens})


class ServicoIn(BaseModel):
    id: int | None = None
    nome: str = ""
    descricao: str = ""
    setup: int = 0     # REAIS
    mensal: int = 0    # REAIS
    custo: int = 0     # REAIS


@router.post("/painel/servicos/catalogo/salvar")
def painel_servicos_catalogo_salvar(request: Request, dados: ServicoIn):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = scat.salvar(get_pool(), conta[0], id=dados.id, nome=dados.nome,
                    descricao=dados.descricao,
                    setup_centavos=int(dados.setup or 0) * 100,
                    mensal_centavos=int(dados.mensal or 0) * 100,
                    custo_centavos=int(dados.custo or 0) * 100)
    if not r.get("ok"):
        return JSONResponse({"erro": r.get("erro", "falha ao salvar")}, status_code=400)
    return JSONResponse(r)


class ServicoDelIn(BaseModel):
    id: int


@router.post("/painel/servicos/catalogo/excluir")
def painel_servicos_catalogo_excluir(request: Request, dados: ServicoDelIn):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = scat.excluir(get_pool(), conta[0], int(dados.id))
    if not r.get("ok"):
        return JSONResponse({"erro": "serviço não encontrado"}, status_code=404)
    return JSONResponse(r)


@router.post("/painel/servicos/catalogo/importar-modelo")
def painel_servicos_catalogo_importar(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    return JSONResponse(scat.importar_modelo(get_pool(), conta[0]))


@router.get("/painel/servicos/cnpj")
def painel_servicos_cnpj(request: Request, cnpj: str = ""):
    """Consulta o CNPJ na BrasilAPI/Receita e devolve os campos pra preencher a
    ficha. Tolerante a falha (consultar_cnpj retorna None em qualquer erro)."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    dados = consultar_cnpj(cnpj)
    if not dados:
        return JSONResponse(
            {"erro": "CNPJ nao encontrado (confira os digitos) ou consulta indisponivel"},
            status_code=404)
    return JSONResponse({
        "empresa": dados.get("nome"),
        "segmento": dados.get("ramo") or dados.get("cnae"),
        "whatsapp": dados.get("telefone"),
        "email": dados.get("email"),
        "cidade": dados.get("cidade"),
        "uf": dados.get("uf"),
    })


class SugerirIn(BaseModel):
    descricao: str = ""


@router.post("/painel/servicos/sugerir")
def painel_servicos_sugerir(request: Request, dados: SugerirIn):
    """Le a descricao/site do cliente e devolve {modules, segmento, escopo} via Claude."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    desc = (dados.descricao or "").strip()
    if not desc:
        return JSONResponse({"erro": "descricao vazia"}, status_code=400)

    itens = scat.listar(get_pool(), conta[0])
    if not itens:
        return JSONResponse(
            {"erro": "cadastre seus serviços primeiro pra a IA poder escolher"},
            status_code=400)
    slugs_validos = {s["slug"] for s in itens}
    catalogo = "\n".join(f"{s['slug']}: {s['nome']} - {s['descricao']}" for s in itens)
    system = (
        "Voce e' consultor de pre-vendas. A empresa vende os servicos listados "
        "abaixo (catalogo dela). Responde SEMPRE em portugues do Brasil e SO' com "
        "JSON valido."
    )
    prompt = (
        f"SERVICOS DISPONIVEIS (use os slugs exatos):\n{catalogo}\n\n"
        f"DESCRICAO DO CLIENTE:\n\"\"\"{desc}\"\"\"\n\n"
        "Tarefa: escolha os servicos mais adequados, identifique o segmento e "
        "escreva um escopo (2 a 4 frases, tom comercial, focado em resultado).\n"
        "Responda APENAS com JSON, sem markdown:\n"
        '{"modules":["slug","slug"],"segmento":"...","escopo":"..."}'
    )
    try:
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": prompt}])
        txt = "".join(
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        ).strip()
        txt = re.sub(r"^```json|^```|```$", "", txt).strip()
        data = json.loads(txt)
        mods = [i for i in data.get("modules", []) if i in slugs_validos]
        return JSONResponse({
            "modules": mods,
            "segmento": (data.get("segmento") or "").strip(),
            "escopo": (data.get("escopo") or "").strip(),
        })
    except Exception:
        return JSONResponse({"erro": "falha ao gerar"}, status_code=500)


class SalvarIn(BaseModel):
    id: int | None = None   # se vier, ATUALIZA a proposta (reaberta do funil)
    cliente: str = ""
    empresa: str = ""
    cnpj: str = ""
    segmento: str = ""
    whatsapp: str = ""
    email: str = ""
    modulos: list[str] = []   # ids dos modulos escolhidos
    escopo: str = ""
    canal: str = ""
    setup: int = 0            # em REAIS
    mensal: int = 0           # em REAIS
    primeiro_ano: int = 0     # em REAIS
    n_modulos: int = 0


@router.post("/painel/servicos/salvar")
def painel_servicos_salvar(request: Request, dados: SalvarIn):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    validos = scat.slugs_validos(get_pool(), conta[0])
    modulos = [i for i in (dados.modulos or []) if i in validos]
    vals = (dados.cliente or None, dados.empresa or None,
            (dados.cnpj or "").strip() or None, dados.segmento or None,
            (dados.whatsapp or "").strip() or None, (dados.email or "").strip() or None,
            json.dumps(modulos), (dados.escopo or "").strip() or None,
            (dados.canal or "").strip() or None,
            int(dados.setup) * 100, int(dados.mensal) * 100,
            int(dados.primeiro_ano) * 100, int(dados.n_modulos))
    with get_pool().connection() as c:
        _garantir_tabela(c)
        oid = None
        if dados.id:
            # atualiza a proposta reaberta (nunca mexe em uma já 'fechado')
            r = c.execute(
                """update orcamentos set cliente=%s, empresa=%s, cnpj=%s, segmento=%s,
                       whatsapp=%s, email=%s, modulos=%s::jsonb, escopo=%s, canal=%s,
                       setup_centavos=%s, mensal_centavos=%s, primeiro_ano_centavos=%s,
                       n_modulos=%s, atualizado_em=now()
                     where id=%s and conta_id=%s and status <> 'fechado'
                   returning id""",
                vals + (dados.id, conta[0])).fetchone()
            oid = r[0] if r else None
        if oid is None and not dados.id:
            oid = c.execute(
                """insert into orcamentos
                   (conta_id, cliente, empresa, cnpj, segmento, whatsapp, email,
                    modulos, escopo, canal, setup_centavos, mensal_centavos,
                    primeiro_ano_centavos, n_modulos)
                   values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s) returning id""",
                (conta[0],) + vals).fetchone()[0]
        c.commit()
    if oid is None:
        return JSONResponse({"erro": "proposta não encontrada ou já fechada"}, status_code=400)
    return JSONResponse({"ok": True, "id": oid})


@router.get("/painel/servicos/lista")
def painel_servicos_lista(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        rows = c.execute(
            """select id, cliente, empresa, setup_centavos, mensal_centavos,
                      primeiro_ano_centavos, n_modulos, criado_em, status
               from orcamentos where conta_id=%s
               order by criado_em desc limit 50""", (conta[0],)).fetchall()
    itens = [{
        "id": r[0],
        "cliente": r[1] or "-",
        "empresa": r[2] or "",
        "setup": brl(r[3]),
        "mensal": brl(r[4]),
        "total": brl(r[5]),
        "mods": r[6],
        "data": r[7].strftime("%d/%m") if r[7] else "",
        "status": r[8] or "rascunho",
        "inicial": (r[1] or r[2] or "?").strip()[:1].upper(),
    } for r in rows]
    return JSONResponse({"itens": itens})


@router.get("/painel/servicos/item/{orc_id}")
def painel_servicos_item(request: Request, orc_id: int):
    """Devolve um orçamento salvo (escopado por conta) pra reabrir no formulário."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select id, cliente, empresa, cnpj, segmento, whatsapp, email,
                      modulos, escopo, status, setup_centavos, mensal_centavos,
                      primeiro_ano_centavos, n_modulos
                 from orcamentos where id=%s and conta_id=%s""",
            (orc_id, conta[0])).fetchone()
    if not r:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    mods = r[7]
    if isinstance(mods, str):
        try:
            mods = json.loads(mods)
        except Exception:
            mods = []
    return JSONResponse({
        "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
        "cnpj": r[3] or "", "segmento": r[4] or "", "whatsapp": r[5] or "",
        "email": r[6] or "", "modulos": [str(m) for m in (mods or [])],
        "escopo": r[8] or "", "status": r[9] or "rascunho",
        "setup": brl(r[10]), "mensal": brl(r[11]), "total": brl(r[12]),
        "n_modulos": r[13] or 0,
    })


class FecharIn(BaseModel):
    id: int


@router.post("/painel/servicos/fechar")
def painel_servicos_fechar(request: Request, dados: FecharIn):
    """Fecha o orçamento: vira contrato e gera os títulos a receber (setup +
    mensalidade recorrente) no módulo Empresa. Idempotente e escopado por conta."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = vendas.fechar_orcamento(get_pool(), conta[0], int(dados.id))
    if not r.get("ok"):
        return JSONResponse({"erro": r.get("erro", "falha ao fechar")}, status_code=400)
    return JSONResponse(r)


# ---------------------------------------------------------------- template
_SERVICOS_TPL = r"""{% extends "base" %}{% block conteudo %}
<div class="sv-wrap">
<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.4rem">
  <h1 style="margin:.2rem 0">Vendas de Serviços</h1>
  <span class="mut" style="font-size:.85rem">{{ empresa_nome }}</span>
</div>
<p class="mut" style="margin-top:0">Monte a proposta, salve no funil e feche o contrato — ao fechar, vira título a receber (setup + mensalidade) no módulo Empresa.</p>
<div id="oc-editando" style="display:none;align-items:center;justify-content:space-between;gap:.6rem;background:#10241d;border:1px solid #1c3a30;border-radius:10px;padding:.5rem .8rem;margin-bottom:.8rem">
  <span class="t" style="font-size:.85rem;color:var(--verde-claro)"></span>
  <button id="oc-novo" type="button" class="oc-pill">Nova proposta</button>
</div>

{% raw %}<style>
.sv-wrap{width:100%;max-width:960px;padding:0 1rem 2rem;box-sizing:border-box}
.sv-wrap .card{max-width:none;margin:0 0 1rem}
/* o base do painel força button{width:100%;margin-top:1.4rem} — reseta aqui e
   reaplica largura cheia só onde faz sentido (os CTAs do resumo). */
.sv-wrap button{width:auto;margin-top:0}
.sv-wrap .oc-tog{width:42px;padding:0}
.sv-wrap .oc-step button{width:34px;padding:0}
.sv-wrap .oc-num input{padding:.35rem 0}
.sv-wrap .oc-btn{width:100%;margin-top:.6rem}
.sv-wrap #oc-anual{width:100%;margin-top:.7rem}
.sv-wrap #oc-cnpj-btn,.sv-wrap #oc-sugerir{margin-top:0}
.sv-wrap h1{font-size:1.5rem}
.sv-wrap .card h2{font-size:1.05rem}
.oc-grid{display:grid; grid-template-columns:1fr 320px; gap:1rem; align-items:start}
@media(max-width:820px){.oc-grid{grid-template-columns:1fr}}
.oc-field{display:flex; flex-direction:column; gap:.35rem; margin-bottom:.6rem}
.oc-field label{font-size:.82rem; color:var(--txt-mut)}
.oc-inp{padding:.55rem .7rem; border-radius:8px; background:var(--bg); color:var(--txt); border:1px solid var(--borda); font-size:.95rem; width:100%; box-sizing:border-box}
.oc-inp:focus{border-color:var(--verde); outline:none}
.oc-mod{display:grid; grid-template-columns:auto 1fr 84px 84px 84px auto; gap:.55rem; align-items:center; padding:.6rem 0; border-bottom:1px solid var(--borda)}
.oc-mod.off{opacity:.5}
.oc-mod .oc-nome{cursor:default}
.oc-rowacts{display:flex; gap:.1rem; white-space:nowrap}
.oc-ic{background:none; border:0; color:var(--txt-mut); cursor:pointer; font-size:.95rem; padding:.1rem .25rem; border-radius:6px}
.oc-ic:hover{color:var(--txt); background:var(--bg)}
.oc-svcform{background:var(--bg); border:1px solid var(--borda); border-radius:10px; padding:.8rem; margin-top:.7rem}
.oc-empty{border:1px dashed var(--borda); border-radius:12px; padding:1.4rem; text-align:center; margin-top:.6rem}
.oc-tog{width:42px; height:24px; border-radius:99px; border:none; cursor:pointer; position:relative; background:#2a3550; flex:none}
.oc-tog.on{background:var(--verde-claro)}
.oc-tog::after{content:""; position:absolute; top:3px; left:3px; width:18px; height:18px; border-radius:50%; background:#fff; transition:left .15s}
.oc-tog.on::after{left:21px}
.oc-num{display:flex; align-items:center; gap:3px; border:1px solid var(--borda); border-radius:7px; padding:.35rem .5rem; background:var(--bg)}
.oc-num span{font-size:.72rem; color:var(--txt-mut)}
.oc-num input{width:100%; border:none; background:transparent; color:var(--txt); text-align:right; font-size:.86rem; font-variant-numeric:tabular-nums}
.oc-num input:focus{outline:none}
.oc-custo-col{display:none}
.sv-wrap.oc-margin .oc-custo-col{display:flex}
.oc-head{display:grid; grid-template-columns:auto 1fr 84px 84px 84px auto; gap:.55rem; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--txt-mut); padding-bottom:.4rem; border-bottom:1px solid var(--borda)}
.oc-pill{padding:.4rem .8rem; border-radius:99px; border:1px solid var(--borda); background:var(--bg); color:var(--txt); cursor:pointer; font-size:.85rem}
.oc-pill.on{border-color:var(--verde-claro); background:#10241d; color:var(--verde-claro)}
.oc-seg button{padding:.45rem .7rem; border:1px solid var(--borda); background:var(--bg); color:var(--txt); cursor:pointer; font-size:.85rem; border-radius:7px}
.oc-seg button.on{border-color:var(--verde-claro); background:#10241d; color:var(--verde-claro)}
.oc-step{display:inline-flex; align-items:center; gap:0}
.oc-step button{width:34px; height:34px; border:1px solid var(--borda); background:var(--bg); color:var(--txt); cursor:pointer; font-size:1.1rem}
.oc-step .v{min-width:38px; text-align:center; font-variant-numeric:tabular-nums}
.oc-ledger{position:sticky; top:1rem}
.oc-ll{display:flex; justify-content:space-between; align-items:baseline; padding:.6rem 0; border-bottom:1px solid var(--borda)}
.oc-ll b{font-size:1.1rem; font-variant-numeric:tabular-nums}
.oc-total{margin-top:.8rem; padding:1rem; border-radius:12px; background:#10241d; border:1px solid #1c3a30}
.oc-total .v{font-size:1.7rem; font-weight:700; color:var(--verde-claro); font-variant-numeric:tabular-nums}
.oc-btn{display:block; width:100%; padding:.75rem; border-radius:10px; border:none; cursor:pointer; font-weight:600; font-size:.95rem; margin-top:.6rem}
.oc-btn-g{background:var(--verde); color:#04140d}
.oc-btn-o{background:transparent; border:1px solid var(--borda); color:var(--txt)}
.oc-hist{display:flex; align-items:center; justify-content:space-between; gap:.6rem; padding:.7rem .8rem; border:1px solid var(--borda); border-radius:10px; margin-bottom:.5rem; flex-wrap:wrap}
.oc-av{width:32px; height:32px; border-radius:8px; background:#13251d; color:var(--verde-claro); display:flex; align-items:center; justify-content:center; font-weight:700; flex:none; margin-right:.7rem}
.oc-badge{font-size:.66rem; font-weight:700; padding:.12rem .5rem; border-radius:6px; letter-spacing:.03em; text-transform:uppercase}
.oc-badge.fechado{background:#10241d; color:var(--verde-claro)}
.oc-badge.aberto{background:#2a2212; color:#e0b25a}
.oc-fechar{background:var(--verde); color:#04140d; border:0; border-radius:8px; padding:.4rem .8rem; font-weight:600; cursor:pointer; font-size:.8rem}
</style>{% endraw %}

<div class="card">
  <h2 style="margin-top:0">Escopo automático · IA</h2>
  <p class="mut" style="margin-top:0">Cole o site ou a descrição do cliente. A IA escolhe os módulos e escreve o escopo da proposta.</p>
  <textarea id="oc-desc" class="oc-inp" rows="3" placeholder="Ex.: clínica com 3 unidades, muito WhatsApp, quer reduzir faltas e organizar leads..."></textarea>
  <div style="display:flex; align-items:center; gap:.8rem; margin-top:.6rem">
    <button id="oc-sugerir" class="oc-btn-g" style="border:none; border-radius:8px; padding:.55rem 1rem; cursor:pointer; font-weight:600; width:auto; margin:0">Sugerir escopo</button>
    <span id="oc-ia-msg" class="mut" style="font-size:.85rem"></span>
  </div>
  <div id="oc-escopo-out" class="mut" style="display:none; margin-top:.8rem; padding:.8rem; background:var(--bg); border:1px solid var(--borda); border-radius:8px; line-height:1.6"></div>
</div>

<div class="card">
  <h2 style="margin-top:0">Cliente</h2>
  <div class="oc-field" style="margin-bottom:.7rem">
    <label>CNPJ <span style="color:var(--txt-mut);font-size:.78rem">— preenche empresa, segmento e contato automaticamente</span></label>
    <div style="display:flex; gap:.5rem; align-items:center">
      <input id="oc-cnpj" class="oc-inp" placeholder="00.000.000/0000-00" inputmode="numeric" style="flex:1">
      <button id="oc-cnpj-btn" type="button" style="background:var(--verde);color:#04140d;border:0;border-radius:8px;padding:.55rem 1.1rem;font-weight:600;cursor:pointer;white-space:nowrap">Buscar</button>
    </div>
    <span id="oc-cnpj-msg" style="font-size:.8rem;color:var(--txt-mut);display:block;margin-top:.25rem"></span>
  </div>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:.8rem">
    <div class="oc-field"><label>Empresa</label><input id="oc-empresa" class="oc-inp" placeholder="Nome da empresa"></div>
    <div class="oc-field"><label>Contato</label><input id="oc-contato" class="oc-inp" placeholder="Responsável"></div>
    <div class="oc-field"><label>WhatsApp</label><input id="oc-whats" class="oc-inp" placeholder="(86) 9 9999-9999"></div>
    <div class="oc-field"><label>E-mail</label><input id="oc-email" class="oc-inp" placeholder="contato@empresa.com.br"></div>
    <div class="oc-field"><label>Segmento</label><input id="oc-segmento" class="oc-inp" placeholder="Saúde, Varejo, Logística..."></div>
  </div>
</div>

<div class="oc-grid">
  <div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem">
        <h2 style="margin:0">Meus serviços</h2>
        <div style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button id="oc-add" class="oc-pill" type="button">+ Adicionar serviço</button>
          <button id="oc-margin" class="oc-pill" type="button">Modo margem</button>
        </div>
      </div>

      <!-- formulário de add/editar serviço do catálogo -->
      <div id="oc-svc-form" class="oc-svcform" style="display:none">
        <input type="hidden" id="svc-id">
        <div style="display:grid; grid-template-columns:2fr 3fr; gap:.6rem">
          <div class="oc-field" style="margin-bottom:.4rem"><label>Nome do serviço</label><input id="svc-nome" class="oc-inp" placeholder="Ex.: Consultoria de SEO"></div>
          <div class="oc-field" style="margin-bottom:.4rem"><label>Descrição</label><input id="svc-desc" class="oc-inp" placeholder="O que está incluso"></div>
        </div>
        <div style="display:flex; gap:.6rem; flex-wrap:wrap; align-items:flex-end">
          <div class="oc-field" style="margin-bottom:0"><label>Setup (R$)</label><input id="svc-setup" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Mensal (R$)</label><input id="svc-mensal" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Custo (R$)</label><input id="svc-custo" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div style="flex:1; display:flex; gap:.4rem; justify-content:flex-end">
            <button id="svc-salvar" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">Salvar</button>
            <button id="svc-cancelar" class="oc-pill" type="button">Cancelar</button>
          </div>
        </div>
        <div id="svc-msg" class="mut" style="font-size:.8rem; margin-top:.4rem"></div>
      </div>

      <div class="oc-head" id="oc-head" style="margin-top:.8rem; display:none">
        <span></span><span>Serviço</span><span style="text-align:right">Setup</span><span style="text-align:right">Mensal</span><span style="text-align:right">Custo/Margem</span><span></span>
      </div>
      <div id="oc-mods"></div>
      <div id="oc-mods-empty" class="oc-empty" style="display:none">
        <b>Você ainda não cadastrou seus serviços</b>
        <p class="mut" style="margin:.3rem 0 0">Adicione o que a sua empresa vende — nome, setup e mensalidade. Isso vira o seu catálogo pra montar orçamentos.</p>
        <div style="display:flex; gap:.5rem; justify-content:center; margin-top:.8rem; flex-wrap:wrap">
          <button id="oc-add2" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">+ Adicionar serviço</button>
          <button id="oc-import" class="oc-pill" type="button">Usar modelo de tecnologia</button>
        </div>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:.6rem">
        <button id="oc-todos" class="oc-pill" type="button" style="display:none">Marcar todos</button>
        <button id="oc-limpar" class="oc-pill" type="button" style="display:none">Limpar seleção</button>
      </div>
    </div>

    <div class="card">
      <h2 style="margin-top:0">Parâmetros</h2>
      <div class="oc-field"><label>Infraestrutura</label>
        <div class="oc-seg" style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button data-grupo="infra" data-val="compartilhada" class="on">Compartilhada</button>
          <button data-grupo="infra" data-val="dedicada">Dedicada</button>
          <button data-grupo="infra" data-val="onpremise">On-premise</button>
        </div>
      </div>
      <div class="oc-field"><label>Volume mensal</label>
        <div class="oc-seg" style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button data-grupo="volume" data-val="baixo">Baixo</button>
          <button data-grupo="volume" data-val="medio" class="on">Médio</button>
          <button data-grupo="volume" data-val="alto">Alto</button>
        </div>
      </div>
      <div style="display:flex; gap:1.5rem; flex-wrap:wrap; align-items:flex-end">
        <div class="oc-field" style="margin-bottom:0"><label>Integrações externas</label>
          <div class="oc-step"><button type="button" data-step="-1">-</button><span class="v" id="oc-integ-v">3</span><button type="button" data-step="1">+</button></div>
          <input type="hidden" id="oc-integ" value="3">
        </div>
        <div class="oc-field" style="margin-bottom:0"><label>Suporte 24h</label>
          <button id="oc-sup" class="oc-pill" data-on="0" type="button">Atendimento dedicado</button>
        </div>
      </div>
      <div class="oc-field" style="margin-top:.8rem"><label>Canais</label>
        <div style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button class="oc-canal oc-pill on" data-on="1">WhatsApp</button>
          <button class="oc-canal oc-pill on" data-on="1">Site</button>
          <button class="oc-canal oc-pill on" data-on="1">Instagram</button>
          <button class="oc-canal oc-pill" data-on="0">Telegram</button>
          <button class="oc-canal oc-pill" data-on="0">E-mail</button>
          <button class="oc-canal oc-pill" data-on="0">Voz</button>
        </div>
      </div>
    </div>
  </div>

  <div class="oc-ledger">
    <div class="card" style="margin:0">
      <div class="mut" style="font-size:.78rem; letter-spacing:.1em; text-transform:uppercase; color:var(--verde-claro)">Resumo · ao vivo</div>
      <div class="oc-ll"><span class="mut">Investimento inicial</span><b id="oc-r-setup">R$ 0</b></div>
      <div class="oc-ll"><span class="mut">Mensalidade</span><b id="oc-r-mensal" style="color:var(--verde-claro)">R$ 0</b></div>
      <div class="oc-ll" id="oc-r-margem-l" style="display:none"><span class="mut">Margem/mês</span><b id="oc-r-margem" style="color:var(--verde-claro); font-size:.95rem">-</b></div>
      <div class="oc-total"><div class="mut" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--verde-claro)">Total 1º ano</div><div class="v" id="oc-r-ano">R$ 0</div><div class="mut" id="oc-r-eco" style="display:none; font-size:.8rem; color:var(--verde-claro); margin-top:.3rem"></div></div>
      <button id="oc-anual" class="oc-pill" data-on="0" type="button" style="width:100%; margin-top:.7rem; text-align:left; display:flex; justify-content:space-between; align-items:center">Pagamento anual (-15%) <span id="oc-anual-mk">↻</span></button>
      <button id="oc-gerar" class="oc-btn oc-btn-g">Gerar proposta</button>
      <button id="oc-salvar" class="oc-btn oc-btn-o">Salvar no funil</button>
    </div>
  </div>
</div>

<div class="card">
  <h2 style="margin-top:0">Funil</h2>
  <div id="oc-hist-box"><p class="mut">Carregando...</p></div>
</div>
</div>

{% raw %}<script>
(function(){
  var INFRA={compartilhada:{s:0,m:0},dedicada:{s:1500,m:800},onpremise:{s:6000,m:1500}};
  function fmt(n){return 'R$ '+Math.round(n||0).toLocaleString('pt-BR');}
  function rows(){return [].slice.call(document.querySelectorAll('.oc-mod'));}
  function num(el){return parseInt(((el&&el.value)||'0').replace(/\D/g,''),10)||0;}
  function seg(g){var b=document.querySelector('[data-grupo="'+g+'"].on'); return b?b.getAttribute('data-val'):'';}
  var WRAP=document.querySelector('.sv-wrap');

  function calc(){
    var setup=0,mensal=0,modMensal=0,custo=0,mods=0;
    rows().forEach(function(r){
      if(r.getAttribute('data-on')==='1'){
        mods++;
        setup+=num(r.querySelector('.oc-setup'));
        var mm=num(r.querySelector('.oc-mensal'));
        mensal+=mm; modMensal+=mm;
        custo+=num(r.querySelector('.oc-custo'));
      }
    });
    var inf=INFRA[seg('infra')]||INFRA.compartilhada;
    setup+=inf.s; mensal+=inf.m;
    if(seg('volume')==='alto') mensal+=600;
    var integ=num(document.getElementById('oc-integ'));
    setup+=integ*250; mensal+=integ*120;
    var canais=document.querySelectorAll('.oc-canal[data-on="1"]').length;
    setup+=canais*400;
    if(document.getElementById('oc-sup').getAttribute('data-on')==='1') mensal+=1500;
    var anual=document.getElementById('oc-anual').getAttribute('data-on')==='1';
    var mensalEf=anual?mensal*0.85:mensal;
    var ano1=setup+mensalEf*12;
    var margem=modMensal-custo, margemPct=modMensal>0?Math.round(margem/modMensal*100):0;
    return {setup:setup,mensal:mensalEf,mensalCheio:mensal,ano1:ano1,margem:margem,margemPct:margemPct,mods:mods,anual:anual};
  }

  function pinta(){
    var c=calc();
    document.getElementById('oc-r-setup').textContent=fmt(c.setup);
    document.getElementById('oc-r-mensal').textContent=fmt(c.mensal);
    document.getElementById('oc-r-ano').textContent=fmt(c.ano1);
    document.getElementById('oc-r-margem').textContent=fmt(c.margem)+' · '+c.margemPct+'%';
    var eco=document.getElementById('oc-r-eco');
    if(c.anual){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.mensalCheio*12*0.15)+' no ano';}
    else eco.style.display='none';
  }

  // linhas de serviço são dinâmicas (catálogo por conta) — delegação:
  var MODS=document.getElementById('oc-mods');
  MODS.addEventListener('click',function(e){
    var tog=e.target.closest('.oc-tog'); if(!tog) return;
    var r=tog.closest('.oc-mod'); var on=r.getAttribute('data-on')==='1';
    r.setAttribute('data-on',on?'0':'1'); r.classList.toggle('off',on); tog.classList.toggle('on',!on); pinta();
  });
  MODS.addEventListener('input',function(e){
    if(e.target.classList.contains('oc-setup')||e.target.classList.contains('oc-mensal')||e.target.classList.contains('oc-custo')) pinta();
  });

  document.getElementById('oc-margin').addEventListener('click',function(){
    WRAP.classList.toggle('oc-margin');
    this.classList.toggle('on');
    document.getElementById('oc-r-margem-l').style.display=WRAP.classList.contains('oc-margin')?'flex':'none';
    pinta();
  });

  document.querySelectorAll('[data-grupo]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('[data-grupo="'+b.getAttribute('data-grupo')+'"]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); pinta();
    });
  });
  document.querySelectorAll('[data-step]').forEach(function(b){
    b.addEventListener('click',function(){
      var h=document.getElementById('oc-integ');
      var v=Math.max(0,Math.min(20,(parseInt(h.value,10)||0)+parseInt(b.getAttribute('data-step'),10)));
      h.value=v; document.getElementById('oc-integ-v').textContent=v; pinta();
    });
  });
  document.querySelectorAll('.oc-canal,#oc-sup,#oc-anual').forEach(function(b){
    b.addEventListener('click',function(){
      var on=b.getAttribute('data-on')==='1';
      b.setAttribute('data-on',on?'0':'1');
      b.classList.toggle('on',!on);
      var mk=document.getElementById('oc-anual-mk'); if(b.id==='oc-anual'&&mk) mk.textContent=on?'↻':'✓';
      pinta();
    });
  });

  // ---- catálogo de serviços por conta (Meus serviços) ----
  var CATALOGO=[];
  function ec(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
  function renderCatalogo(preserva){
    var box=document.getElementById('oc-mods'), onset={};
    if(preserva){rows().forEach(function(r){onset[r.getAttribute('data-id')]=r.getAttribute('data-on');});}
    box.innerHTML='';
    CATALOGO.forEach(function(s){
      var on = preserva ? (onset[s.slug]!==undefined?onset[s.slug]:'1') : '1';
      var r=document.createElement('div'); r.className='oc-mod'+(on==='1'?'':' off');
      r.setAttribute('data-id',s.slug); r.setAttribute('data-on',on);
      r.setAttribute('data-nome',s.nome); r.setAttribute('data-desc',s.descricao||''); r.setAttribute('data-cid',s.id);
      r.innerHTML='<button class="oc-tog'+(on==='1'?' on':'')+'" type="button" title="Entra nesta proposta"></button>'
        +'<div class="oc-nome"><b>'+ec(s.nome)+'</b><div class="mut" style="font-size:.78rem">'+ec(s.descricao||'')+'</div></div>'
        +'<div class="oc-num"><span>R$</span><input class="oc-setup" inputmode="numeric" value="'+s.setup+'"></div>'
        +'<div class="oc-num"><span>R$</span><input class="oc-mensal" inputmode="numeric" value="'+s.mensal+'"></div>'
        +'<div class="oc-num oc-custo-col"><span>R$</span><input class="oc-custo" inputmode="numeric" value="'+s.custo+'"></div>'
        +'<div class="oc-rowacts"><button class="oc-ic oc-edit" type="button" title="Editar serviço">✎</button><button class="oc-ic oc-del" type="button" title="Excluir serviço">🗑</button></div>';
      box.appendChild(r);
    });
    var vazio=CATALOGO.length===0;
    document.getElementById('oc-mods-empty').style.display=vazio?'block':'none';
    document.getElementById('oc-head').style.display=vazio?'none':'grid';
    document.getElementById('oc-todos').style.display=vazio?'none':'inline-block';
    document.getElementById('oc-limpar').style.display=vazio?'none':'inline-block';
    pinta();
  }
  function carregarCatalogo(preserva){
    return fetch('/painel/servicos/catalogo').then(function(r){return r.json();}).then(function(d){
      CATALOGO=d.itens||[]; renderCatalogo(preserva);
    }).catch(function(){});
  }
  // editar / excluir (delegação)
  document.getElementById('oc-mods').addEventListener('click',function(e){
    var ed=e.target.closest('.oc-edit'), dl=e.target.closest('.oc-del');
    if(ed){var r=ed.closest('.oc-mod'); abrirForm({id:r.getAttribute('data-cid'),nome:r.getAttribute('data-nome'),descricao:r.getAttribute('data-desc'),setup:num(r.querySelector('.oc-setup')),mensal:num(r.querySelector('.oc-mensal')),custo:num(r.querySelector('.oc-custo'))});}
    else if(dl){var r2=dl.closest('.oc-mod'); if(confirm('Excluir "'+r2.getAttribute('data-nome')+'" do seu catálogo?')){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:parseInt(r2.getAttribute('data-cid'),10)})}).then(function(){carregarCatalogo(true);});}}
  });
  // form de add/editar serviço do catálogo
  function abrirForm(s){
    s=s||{};
    document.getElementById('svc-id').value=s.id||'';
    document.getElementById('svc-nome').value=s.nome||'';
    document.getElementById('svc-desc').value=s.descricao||'';
    document.getElementById('svc-setup').value=s.setup||0;
    document.getElementById('svc-mensal').value=s.mensal||0;
    document.getElementById('svc-custo').value=s.custo||0;
    document.getElementById('svc-msg').textContent='';
    document.getElementById('oc-svc-form').style.display='block';
    document.getElementById('svc-nome').focus();
  }
  function fecharForm(){document.getElementById('oc-svc-form').style.display='none';}
  document.getElementById('oc-add').addEventListener('click',function(){abrirForm();});
  document.getElementById('oc-add2').addEventListener('click',function(){abrirForm();});
  document.getElementById('svc-cancelar').addEventListener('click',fecharForm);
  document.getElementById('svc-salvar').addEventListener('click',function(){
    var nome=document.getElementById('svc-nome').value.trim();
    if(!nome){document.getElementById('svc-msg').textContent='Informe o nome do serviço.';return;}
    var idv=document.getElementById('svc-id').value;
    var body={id:idv?parseInt(idv,10):null,nome:nome,descricao:document.getElementById('svc-desc').value||'',setup:num(document.getElementById('svc-setup')),mensal:num(document.getElementById('svc-mensal')),custo:num(document.getElementById('svc-custo'))};
    var b=this; b.disabled=true;
    fetch('/painel/servicos/catalogo/salvar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json().then(function(d){return{ok:r.ok,d:d};});})
      .then(function(res){b.disabled=false; if(!res.ok){document.getElementById('svc-msg').textContent=(res.d&&res.d.erro)||'Não consegui salvar.';return;} fecharForm(); carregarCatalogo(true);})
      .catch(function(){b.disabled=false; document.getElementById('svc-msg').textContent='Erro de conexão.';});
  });
  document.getElementById('oc-import').addEventListener('click',function(){
    var b=this; b.disabled=true; b.textContent='Importando...';
    fetch('/painel/servicos/catalogo/importar-modelo',{method:'POST'}).then(function(){return carregarCatalogo(false);}).finally(function(){b.disabled=false; b.textContent='Usar modelo de tecnologia';});
  });
  document.getElementById('oc-todos').addEventListener('click',function(){rows().forEach(function(r){r.setAttribute('data-on','1');r.classList.remove('off');r.querySelector('.oc-tog').classList.add('on');});pinta();});
  document.getElementById('oc-limpar').addEventListener('click',function(){rows().forEach(function(r){r.setAttribute('data-on','0');r.classList.add('off');r.querySelector('.oc-tog').classList.remove('on');});pinta();});

  document.getElementById('oc-sugerir').addEventListener('click',function(){
    var desc=document.getElementById('oc-desc').value.trim();
    if(!desc){return;}
    var btn=this, msg=document.getElementById('oc-ia-msg');
    btn.disabled=true; var t0=btn.textContent; btn.textContent='Analisando...'; msg.textContent='';
    fetch('/painel/servicos/sugerir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({descricao:desc})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.erro){msg.textContent='Não consegui gerar agora. Tente de novo.'; return;}
        var ids=d.modules||[];
        if(ids.length){rows().forEach(function(r){var on=ids.indexOf(r.getAttribute('data-id'))>=0; r.setAttribute('data-on',on?'1':'0'); r.classList.toggle('off',!on); r.querySelector('.oc-tog').classList.toggle('on',on);});}
        if(d.segmento){document.getElementById('oc-segmento').value=d.segmento;}
        var out=document.getElementById('oc-escopo-out');
        if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
        pinta();
      })
      .catch(function(){msg.textContent='Erro de conexão.';})
      .finally(function(){btn.disabled=false; btn.textContent=t0;});
  });

  document.getElementById('oc-cnpj-btn').addEventListener('click',function(){
    var dig=(document.getElementById('oc-cnpj').value||'').replace(/\D/g,'');
    var msg=document.getElementById('oc-cnpj-msg');
    if(dig.length!==14){msg.textContent='Digite os 14 dígitos do CNPJ.'; return;}
    var btn=this, t0=btn.textContent; btn.disabled=true; btn.textContent='Buscando...'; msg.textContent='';
    fetch('/painel/servicos/cnpj?cnpj='+encodeURIComponent(dig))
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){msg.textContent=(res.d&&res.d.erro)||'Não encontrei esse CNPJ.'; return;}
        var d=res.d;
        function set(id,v){if(v){document.getElementById(id).value=v;}}
        set('oc-empresa',d.empresa); set('oc-segmento',d.segmento);
        set('oc-whats',d.whatsapp); set('oc-email',d.email);
        var loc=[d.cidade,d.uf].filter(Boolean).join('/');
        msg.textContent='Preenchido pela Receita'+(loc?' — '+loc:'')+'. Confira e ajuste se precisar.';
      })
      .catch(function(){msg.textContent='Erro de conexão ao consultar.';})
      .finally(function(){btn.disabled=false; btn.textContent=t0;});
  });

  document.getElementById('oc-salvar').addEventListener('click',function(){
    var c=calc();
    var modIds=rows().filter(function(r){return r.getAttribute('data-on')==='1';}).map(function(r){return r.getAttribute('data-id');});
    var escEl=document.getElementById('oc-escopo-out');
    var body={id:EDIT_ID,cliente:document.getElementById('oc-contato').value||'',empresa:document.getElementById('oc-empresa').value||'',cnpj:document.getElementById('oc-cnpj').value||'',segmento:document.getElementById('oc-segmento').value||'',whatsapp:document.getElementById('oc-whats').value||'',email:document.getElementById('oc-email').value||'',modulos:modIds,escopo:(escEl.getAttribute('data-escopo')||''),setup:Math.round(c.setup),mensal:Math.round(c.mensal),primeiro_ano:Math.round(c.ano1),n_modulos:c.mods};
    var btn=this; btn.textContent='Salvando...';
    fetch('/painel/servicos/salvar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json();}).then(function(d){if(d&&d.id){EDIT_ID=d.id;} btn.textContent='Salvo!'; carregarHist(); setTimeout(function(){btn.textContent='Salvar no funil';},1500);})
      .catch(function(){btn.textContent='Erro ao salvar';});
  });

  function esc(s){var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML;}
  function setv(id,v){var e=document.getElementById(id); if(e){e.value=v||'';}}
  function marcaMods(ids){
    rows().forEach(function(r){
      var on=(ids||[]).indexOf(r.getAttribute('data-id'))>=0;
      r.setAttribute('data-on',on?'1':'0'); r.classList.toggle('off',!on);
      r.querySelector('.oc-tog').classList.toggle('on',on);
    });
  }
  var EDIT_ID=null;
  function novo(){
    EDIT_ID=null;
    ['oc-empresa','oc-contato','oc-cnpj','oc-segmento','oc-whats','oc-email','oc-desc'].forEach(function(id){setv(id,'');});
    var out=document.getElementById('oc-escopo-out'); out.style.display='none'; out.removeAttribute('data-escopo'); out.textContent='';
    document.getElementById('oc-editando').style.display='none';
    pinta();
  }
  function abrir(id){
    fetch('/painel/servicos/item/'+id).then(function(r){return r.json();}).then(function(d){
      if(d.erro){alert('Não consegui abrir essa proposta.'); return;}
      EDIT_ID=d.id;
      setv('oc-empresa',d.empresa); setv('oc-contato',d.cliente); setv('oc-cnpj',d.cnpj);
      setv('oc-segmento',d.segmento); setv('oc-whats',d.whatsapp); setv('oc-email',d.email);
      marcaMods(d.modulos);
      var out=document.getElementById('oc-escopo-out');
      if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
      else{out.style.display='none'; out.removeAttribute('data-escopo');}
      var bn=document.getElementById('oc-editando');
      bn.style.display='flex';
      bn.querySelector('.t').textContent='Proposta #'+d.id+' · '+d.status+' · '+d.total+' (valores por módulo recalculados pelo catálogo)';
      pinta();
      window.scrollTo({top:0,behavior:'smooth'});
    }).catch(function(){alert('Erro de conexão.');});
  }
  document.getElementById('oc-novo').addEventListener('click',novo);
  function fechar(id,btn){
    if(!confirm('Fechar este contrato? Vai gerar título a receber (setup + mensalidade) no módulo Empresa.')){return;}
    btn.disabled=true; btn.textContent='Fechando...';
    fetch('/painel/servicos/fechar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui fechar.'); btn.disabled=false; btn.textContent='Fechar contrato'; return;}
        carregarHist();
      })
      .catch(function(){alert('Erro de conexão.'); btn.disabled=false; btn.textContent='Fechar contrato';});
  }
  function carregarHist(){
    fetch('/painel/servicos/lista').then(function(r){return r.json();}).then(function(d){
      var box=document.getElementById('oc-hist-box');
      if(!d.itens||!d.itens.length){box.innerHTML='<p class="mut">Nenhuma proposta no funil ainda.</p>'; return;}
      box.innerHTML='';
      d.itens.forEach(function(it){
        var el=document.createElement('div'); el.className='oc-hist';
        var fechado=it.status==='fechado';
        var left=document.createElement('div'); left.className='oc-hist-open';
        left.style.cssText='display:flex;align-items:center;min-width:0;flex:1;cursor:pointer';
        left.title='Abrir proposta';
        left.innerHTML='<div class="oc-av">'+esc(it.inicial)+'</div>'
          +'<div><b>'+esc(it.cliente)+(it.empresa?' <span class="mut">· '+esc(it.empresa)+'</span>':'')+'</b>'
          +'<div class="mut" style="font-size:.78rem">'+esc(it.data)+' · '+it.mods+' módulos · '+esc(it.total)+'</div></div>';
        left.addEventListener('click',function(){abrir(it.id);});
        el.appendChild(left);
        var right=document.createElement('div'); right.style.display='flex'; right.style.alignItems='center'; right.style.gap='.6rem';
        var badge=document.createElement('span'); badge.className='oc-badge '+(fechado?'fechado':'aberto'); badge.textContent=fechado?'Fechado':esc(it.status);
        right.appendChild(badge);
        if(!fechado){
          var b=document.createElement('button'); b.className='oc-fechar'; b.textContent='Fechar contrato';
          b.addEventListener('click',function(){fechar(it.id,b);});
          right.appendChild(b);
        }
        el.appendChild(right);
        box.appendChild(el);
      });
    }).catch(function(){document.getElementById('oc-hist-box').innerHTML='<p class="mut">Erro ao carregar.</p>';});
  }

  document.getElementById('oc-gerar').addEventListener('click',function(){
    var c=calc();
    var empresa=document.getElementById('oc-empresa').value||'Cliente';
    var contato=document.getElementById('oc-contato').value||'';
    var whats=document.getElementById('oc-whats').value||'';
    var segmento=document.getElementById('oc-segmento').value||'';
    var escopo=(document.getElementById('oc-escopo-out').getAttribute('data-escopo'))||('Propomos à '+empresa+' a implantação de uma operação de IA sob medida, integrando os módulos abaixo, com foco em automatizar o atendimento, qualificar oportunidades e dar visibilidade dos resultados.');
    var linhas=rows().filter(function(r){return r.getAttribute('data-on')==='1';}).map(function(r){
      return {nome:r.getAttribute('data-nome'),desc:r.getAttribute('data-desc'),setup:num(r.querySelector('.oc-setup')),mensal:num(r.querySelector('.oc-mensal'))};
    });
    var hoje=new Date();
    var meses=['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
    var dataStr=hoje.getDate()+' de '+meses[hoje.getMonth()]+' de '+hoje.getFullYear();
    var docNum='PR-'+hoje.getFullYear()+(''+(hoje.getMonth()+1)).padStart(2,'0')+'-'+(''+(Math.floor(hoje.getTime()/1000)%1000)).padStart(3,'0');
    var trs=linhas.map(function(l){return '<tr><td><b>'+l.nome+'</b><small>'+l.desc+'</small></td><td class=r>'+fmt(l.setup)+'</td><td class=r>'+fmt(l.mensal)+'</td></tr>';}).join('');
    var html='<!doctype html><html lang=pt-br><head><meta charset=utf-8><title>Proposta '+empresa+'</title>'
      +'<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,-apple-system,sans-serif;color:#14213D;background:#EEE9DD;padding:30px}'
      +'.pg{max-width:794px;margin:0 auto;background:#fff;border-radius:4px;overflow:hidden;box-shadow:0 20px 50px -25px rgba(20,33,61,.4)}'
      +'.hd{background:#14213D;color:#F4F1EA;padding:36px 48px;display:flex;justify-content:space-between;align-items:flex-start}'
      +'.hd .lg{font-size:26px;font-weight:600}.hd .lg span{color:#E0B458}'
      +'.hd .sub{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#9FA8BC;margin-top:5px}'
      +'.hd .mt{text-align:right;font-size:11px;color:#9FA8BC}.hd .mt b{display:block;color:#E0B458;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}'
      +'.bd{padding:34px 48px 44px}.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:24px 0 10px}'
      +'.cli{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:14px 16px;font-size:14px}'
      +'p.es{font-size:13.5px;line-height:1.7;color:#3A4254}'
      +'table{width:100%;border-collapse:collapse;margin-top:4px}th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#9FA8BC;background:#14213D;padding:10px 14px;text-align:left}th.r,td.r{text-align:right;font-family:monospace}'
      +'td{padding:12px 14px;border-bottom:1px solid #F0EBE0;font-size:13.5px}td small{display:block;color:#A8A192;font-size:11.5px;margin-top:2px}'
      +'.tot{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:24px}.bx{border:1px solid #ECE7DC;border-radius:12px;padding:18px}.bx .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}.bx .v{font-family:monospace;font-size:23px;font-weight:600;margin-top:6px}'
      +'.fin{margin-top:12px;background:#14213D;border-radius:12px;padding:20px 22px;display:flex;justify-content:space-between;align-items:center}.fin .l{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#E0B458;font-weight:600}.fin .v{font-family:monospace;font-size:30px;font-weight:600;color:#FBFAF7}'
      +'.ft{margin-top:28px;font-size:11.5px;color:#8A8475;line-height:1.6}@media print{body{background:#fff;padding:0}.pg{box-shadow:none;border-radius:0}@page{size:A4;margin:0}}'
      +'</style></head><body><div class=pg>'
      +'<div class=hd><div><div class=lg>'+esc(empresa)+' <span>·</span></div><div class=sub>Proposta comercial</div></div>'
      +'<div class=mt><b>Proposta comercial</b>'+docNum+'<br>'+dataStr+'</div></div>'
      +'<div class=bd><div class=eb>Preparada para</div><div class=cli><b>'+esc(empresa)+'</b>'+(contato?' · '+esc(contato):'')+(whats?' · '+esc(whats):'')+(segmento?'<br><span style="color:#A8A192">Segmento: '+esc(segmento)+'</span>':'')+'</div>'
      +'<div class=eb>Escopo da solução</div><p class=es>'+esc(escopo)+'</p>'
      +'<div class=eb>Módulos contratados</div><table><tr><th>Módulo</th><th class=r>Setup</th><th class=r>Mensal</th></tr>'+trs+'</table>'
      +'<div class=tot><div class=bx><div class=l>Investimento inicial</div><div class=v>'+fmt(c.setup)+'</div></div><div class=bx><div class=l>Mensalidade</div><div class=v>'+fmt(c.mensal)+'</div></div></div>'
      +'<div class=fin><div><div class=l>Total estimado · 1º ano</div></div><div class=v>'+fmt(c.ano1)+'</div></div>'
      +'<div class=ft>Proposta válida por 15 dias. Valores em reais (BRL). Implantação média de 2 a 6 semanas conforme escopo.</div>'
      +'</div></div><script>window.onload=function(){setTimeout(function(){window.print();},400);}<\/script></body></html>';
    var w=window.open('','_blank');
    if(w){w.document.write(html); w.document.close();}
  });

  carregarCatalogo(false); carregarHist();
})();
</script>{% endraw %}
{% endblock %}"""

# Registra o template no env do portal (reusa base/nav/gate do painel).
_env.loader.mapping["servicos"] = _SERVICOS_TPL
