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
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from psycopg.errors import UniqueViolation
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
        alter table orcamentos add column if not exists token         text;
        alter table orcamentos add column if not exists itens         jsonb;
        alter table orcamentos add column if not exists aprovada_em   timestamptz;
        alter table orcamentos add column if not exists aprovada_por  text;
        alter table orcamentos add column if not exists aprovada_doc  text;
        alter table orcamentos add column if not exists aprovada_ip   text;
        alter table orcamentos add column if not exists telefone      text;
        alter table orcamentos add column if not exists cidade        text;
        alter table orcamentos add column if not exists uf            text;
        alter table orcamentos add column if not exists site          text;
        alter table orcamentos add column if not exists cargo         text;
        alter table orcamentos add column if not exists socio         text;
        alter table orcamentos add column if not exists modo          text not null default 'recorrente';
        alter table orcamentos add column if not exists evento        jsonb;
        alter table orcamentos add column if not exists parcelas      jsonb;
        alter table orcamentos add column if not exists numero        int;
        alter table orcamentos add column if not exists endereco      text;
        alter table orcamentos add column if not exists cep           text;
        alter table orcamentos add column if not exists evento_agenda_id bigint;
        create index if not exists idx_orcamentos_status on orcamentos (status, criado_em desc);
        create index if not exists idx_orcamentos_conta on orcamentos (conta_id, status, criado_em desc);
        create unique index if not exists idx_orcamentos_token on orcamentos (token) where token is not null;
        create unique index if not exists uq_orcamentos_conta_numero
            on orcamentos (conta_id, numero) where numero is not null;
    """)
    # modo do orçamento: 'recorrente' (setup+mensal) ou 'evento' (data, qtd ×
    # valor unitário, parcelas). Espelha a migração 147.
    c.execute("alter table orcamentos drop constraint if exists orcamentos_modo_check")
    c.execute("""alter table orcamentos add constraint orcamentos_modo_check
        check (modo in ('recorrente','evento'))""")
    # novo estado 'aprovada' — relaxa o check de status (068 só tinha 5 estados).
    c.execute("alter table orcamentos drop constraint if exists orcamentos_status_check")
    c.execute("""alter table orcamentos add constraint orcamentos_status_check
        check (status in ('rascunho','enviado','negociando','aprovada','fechado','perdido'))""")
    c.commit()


def _saida(request: Request, conta) -> str:
    """Pra onde mandar quem chegou aqui mas a conta não vende serviço.

    Tem que ser um lugar que o PAPEL abre E que a CONTA tem. O dono passa em
    tudo; membro de equipe, não — e mandar um VENDEDOR pra /painel/empresa
    (que exige caps.financeiro) fazia o gate de web/app.py devolver ele pra
    /painel/servicos, que devolvia pra /painel/empresa: laço infinito, e o
    vendedor de empresa que só vende produto simplesmente não entrava no
    sistema. /trocar é a saída terminal — o gate libera pra todo mundo.
    """
    papel = request.session.get("papel", "dono")
    if papel == "dono":
        return "/painel/empresa" if conta[12] else "/painel"
    from contas import equipe as _equipe
    caps = _equipe.caps_do_papel(papel)
    if caps["vendas"] and conta[11]:        # [11] = tem_pj — o funil não pede serviço
        return "/painel/prospeccao"
    if caps["financeiro"] and conta[12]:    # [12] = acesso_pj
        return "/painel/empresa"
    return "/trocar"


def _conta_servico(request: Request):
    """Gate da aba: conta logada + acesso PJ + vende serviço. Devolve (conta, None)
    ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if not conta[12]:                       # [12] = acesso_pj
        return None, RedirectResponse(_saida(request, conta), status_code=303)
    if not conta[14]:                       # [14] = vende_servico
        return None, RedirectResponse(_saida(request, conta), status_code=303)
    from contas import equipe as _equipe
    if not _equipe.caps_do_papel(request.session.get("papel", "dono"))["vendas"]:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _ator(request: Request):
    """(membro_id, papel) do operador logado — pra carimbar/filtrar o funil."""
    return request.session.get("membro_id"), request.session.get("papel", "dono")


def _nicho(conta_id: int) -> str:
    """Slug do nicho da conta. É ele que decide o MODO do orçamento (evento ×
    recorrente) e o vocabulário da tela — nunca o que o navegador manda."""
    return emp.obter_dados_empresa(get_pool(), conta_id).get("nicho") or ""


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
    nicho = _nicho(conta[0])
    # eventos vende PACOTE/preço de evento avulso — sem setup+mensalidade estilo
    # SaaS. A tela some com "Setup" e chama o preço único de "Valor" (fica
    # gravado em setup_centavos por baixo, pra "Fechar contrato" não virar
    # cobrança recorrente errada de um evento pontual).
    servico_avulso = nicho == "eventos"
    return _render("servicos", request, empresa_nome=conta[2],
                   tem_pj=True, vende_servico=True, servico_avulso=servico_avulso)


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


@router.get("/painel/servicos/leads/buscar")
def painel_servicos_leads_buscar(request: Request, q: str = ""):
    """Busca clientes já cadastrados na Base (prospeccao) por nome/empresa/e-mail,
    pra preencher o card Cliente sem digitar tudo de novo. tipo é inferido (tem
    CNPJ -> pj, senão pf), igual o backfill que a 131_pessoa_cnpj.sql já fez pra
    pessoas. Vendedor só busca os próprios leads; dono/gestor busca todos."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    q = (q or "").strip()
    if len(q) < 2:
        return JSONResponse({"itens": []})
    membro_id, papel = _ator(request)
    termo = f"%{q}%"
    query = """select id, empresa, contato, cargo, cnpj, telefone, whatsapp, email,
                      cidade, uf, socio, segmento, site_url
                 from prospeccao
                where conta_id=%s and (empresa ilike %s or contato ilike %s or email ilike %s)"""
    params = [conta[0], termo, termo, termo]
    if papel == "vendedor" and membro_id:
        query += " and vendedor_id=%s"
        params.append(membro_id)
    query += " order by atualizado_em desc nulls last limit 8"
    with get_pool().connection() as c:
        rows = c.execute(query, tuple(params)).fetchall()
    itens = [{
        "id": r[0], "empresa": r[1] or "", "contato": r[2] or "", "cargo": r[3] or "",
        "cnpj": r[4] or "", "telefone": r[5] or "", "whatsapp": r[6] or "", "email": r[7] or "",
        "cidade": r[8] or "", "uf": r[9] or "", "socio": r[10] or "", "segmento": r[11] or "",
        "site": r[12] or "", "tipo": "pj" if (r[4] or "").strip() else "pf",
    } for r in rows]
    return JSONResponse({"itens": itens})


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


class ItemIn(BaseModel):
    nome: str = ""
    desc: str = ""
    setup: int = 0            # total da linha em REAIS (qtd × unitário)
    mensal: int = 0
    qtd: int = 1              # evento: quantidade contratada
    unitario: int = 0         # evento: valor unitário em REAIS


class EventoIn(BaseModel):
    """O bloco 'O evento' do orçamento (modo evento). Datas e horas ficam como
    texto do jeito que a empresa escreve ('2025-11-18', '19:00', '24:00') — quem
    transforma em compromisso é agenda.janela_evento, que sabe a regra da
    virada da meia-noite."""
    data: str = ""
    convidados: int | None = None
    inicio: str = ""
    fim: str = ""
    tipo: str = ""
    contratos: list[str] = []
    local: str = ""


class ParcelaIn(BaseModel):
    venc: str = ""
    valor_centavos: int = 0
    forma: str = ""
    obs: str = ""


class SalvarIn(BaseModel):
    id: int | None = None   # se vier, ATUALIZA a proposta (reaberta do funil)
    cliente: str = ""
    empresa: str = ""
    cnpj: str = ""
    segmento: str = ""
    whatsapp: str = ""
    email: str = ""
    telefone: str = ""
    cidade: str = ""
    uf: str = ""
    site: str = ""
    cargo: str = ""
    socio: str = ""
    endereco: str = ""
    cep: str = ""
    modulos: list[str] = []   # ids dos modulos escolhidos
    itens: list[ItemIn] = []  # snapshot das linhas (nome/setup/mensal) pra a proposta
    evento: EventoIn | None = None      # modo evento: data, convidados, horário...
    parcelas: list[ParcelaIn] = []      # modo evento: plano de pagamento
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
    # a descrição do item é o que o cliente lê ("o espaço inclui: ..."): num
    # orçamento de evento ela tem parágrafos inteiros, então o corte é largo.
    itens = [{"nome": (it.nome or "")[:120], "desc": (it.desc or "")[:2000],
              "setup": int(it.setup or 0), "mensal": int(it.mensal or 0),
              "qtd": max(1, int(it.qtd or 1)), "unitario": int(it.unitario or 0)}
             for it in (dados.itens or [])[:50]]
    itens_json = json.dumps(itens)
    # o MODO vem do nicho da conta, não do navegador: quem vende evento emite
    # orçamento de evento, e só. (mesma regra do servico_avulso da tela)
    modo = "evento" if _nicho(conta[0]) == "eventos" else "recorrente"
    evento_json = json.dumps(dados.evento.model_dump()) if (modo == "evento" and dados.evento) else None
    parcelas_json = json.dumps(
        [p.model_dump() for p in (dados.parcelas or [])[:60] if int(p.valor_centavos or 0) > 0]
    ) if modo == "evento" else None
    vals = (dados.cliente or None, dados.empresa or None,
            (dados.cnpj or "").strip() or None, dados.segmento or None,
            (dados.whatsapp or "").strip() or None, (dados.email or "").strip() or None,
            (dados.telefone or "").strip() or None, (dados.cidade or "").strip() or None,
            (dados.uf or "").strip()[:2].upper() or None, (dados.site or "").strip() or None,
            (dados.cargo or "").strip() or None, (dados.socio or "").strip() or None,
            (dados.endereco or "").strip() or None, (dados.cep or "").strip() or None,
            json.dumps(modulos), itens_json, (dados.escopo or "").strip() or None,
            (dados.canal or "").strip() or None, modo, evento_json, parcelas_json,
            int(dados.setup) * 100, int(dados.mensal) * 100,
            int(dados.primeiro_ano) * 100, int(dados.n_modulos))
    with get_pool().connection() as c:
        _garantir_tabela(c)
        oid = tok = None
        if dados.id:
            # atualiza a proposta reaberta (nunca mexe em uma já 'fechado')
            r = c.execute(
                """update orcamentos set cliente=%s, empresa=%s, cnpj=%s, segmento=%s,
                       whatsapp=%s, email=%s, telefone=%s, cidade=%s, uf=%s, site=%s,
                       cargo=%s, socio=%s, endereco=%s, cep=%s,
                       modulos=%s::jsonb, itens=%s::jsonb, escopo=%s, canal=%s,
                       modo=%s, evento=%s::jsonb, parcelas=%s::jsonb,
                       setup_centavos=%s, mensal_centavos=%s, primeiro_ano_centavos=%s,
                       n_modulos=%s, atualizado_em=now(),
                       token=coalesce(token, %s),
                       -- editar uma proposta JÁ assinada a reabre: volta pra 'enviado'
                       -- e limpa a assinatura (os termos mudaram → precisa re-aprovar).
                       status=case when status='aprovada' then 'enviado' else status end,
                       aprovada_por=case when status='aprovada' then null else aprovada_por end,
                       aprovada_em=case when status='aprovada' then null else aprovada_em end,
                       aprovada_doc=case when status='aprovada' then null else aprovada_doc end,
                       aprovada_ip=case when status='aprovada' then null else aprovada_ip end
                     where id=%s and conta_id=%s and status <> 'fechado'
                   returning id, token""",
                vals + (secrets.token_urlsafe(16), dados.id, conta[0])).fetchone()
            if r:
                oid, tok = r
        if oid is None and not dados.id:
            membro_id, _papel = _ator(request)
            criador = str(membro_id) if membro_id else "dono"
            # numero: sequencial POR CONTA, calculado no próprio INSERT. O índice
            # único (conta_id, numero) é quem garante a série — se dois salvarem
            # ao mesmo tempo, o perdedor tenta de novo e pega o próximo.
            sql_ins = """insert into orcamentos
                   (conta_id, cliente, empresa, cnpj, segmento, whatsapp, email,
                    telefone, cidade, uf, site, cargo, socio, endereco, cep,
                    modulos, itens, escopo, canal, modo, evento, parcelas,
                    setup_centavos, mensal_centavos,
                    primeiro_ano_centavos, n_modulos, criado_por, token, numero)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,
                           (select coalesce(max(numero),0)+1 from orcamentos where conta_id=%s))
                   returning id, token"""
            for _tentativa in range(3):
                try:
                    r = c.execute(sql_ins, (conta[0],) + vals
                                  + (criador, secrets.token_urlsafe(16), conta[0])).fetchone()
                    break
                except UniqueViolation:
                    c.rollback()   # outro salvou primeiro: o max+1 recalcula na próxima
            else:
                return JSONResponse({"erro": "não consegui numerar o orçamento; tente de novo"},
                                    status_code=409)
            oid, tok = r
        c.commit()
    if oid is None:
        return JSONResponse({"erro": "proposta não encontrada ou já fechada"}, status_code=400)
    return JSONResponse({"ok": True, "id": oid, "token": tok})


@router.get("/painel/servicos/lista")
def painel_servicos_lista(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    membro_id, papel = _ator(request)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        # auto-cura: toda proposta precisa de um token pro link/PDF (as antigas
        # foram salvas antes do token existir). Gera pra quem está sem, uma vez.
        c.execute(
            """update orcamentos set token = substr(md5(random()::text || id::text
                 || clock_timestamp()::text), 1, 22)
               where conta_id=%s and token is null""", (conta[0],))
        c.commit()
        # vendedor vê só as propostas dele; dono/gestor veem o funil inteiro.
        _cols = """select id, cliente, empresa, setup_centavos, mensal_centavos,
                          primeiro_ano_centavos, n_modulos, criado_em, status,
                          token, aprovada_por, aprovada_em, numero,
                          coalesce(modo,'recorrente')"""
        if papel == "vendedor" and membro_id:
            rows = c.execute(
                _cols + """ from orcamentos where conta_id=%s and criado_por=%s
                   order by criado_em desc limit 50""",
                (conta[0], str(membro_id))).fetchall()
        else:
            rows = c.execute(
                _cols + """ from orcamentos where conta_id=%s
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
        "token": r[9] or "",
        "aprovada_por": r[10] or "",
        "aprovada_em": r[11].strftime("%d/%m/%Y") if r[11] else "",
        "numero": r[12], "modo": r[13] or "recorrente",
        "inicial": (r[1] or r[2] or "?").strip()[:1].upper(),
    } for r in rows]
    return JSONResponse({"itens": itens})


@router.get("/painel/servicos/item/{orc_id}")
def painel_servicos_item(request: Request, orc_id: int):
    """Devolve um orçamento salvo (escopado por conta) pra reabrir no formulário."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    membro_id, papel = _ator(request)
    dono_filtro = " and criado_por=%s" if (papel == "vendedor" and membro_id) else ""
    args = (orc_id, conta[0]) + ((str(membro_id),) if dono_filtro else ())
    with get_pool().connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select id, cliente, empresa, cnpj, segmento, whatsapp, email,
                      modulos, escopo, status, setup_centavos, mensal_centavos,
                      primeiro_ano_centavos, n_modulos, itens,
                      telefone, cidade, uf, site, cargo, socio,
                      endereco, cep, evento, parcelas, numero,
                      coalesce(modo,'recorrente')
                 from orcamentos where id=%s and conta_id=%s""" + dono_filtro,
            args).fetchone()
    if not r:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    def _jsonb(v, vazio=None):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return vazio if vazio is not None else []
        if v is None:
            return vazio if vazio is not None else []
        return v
    return JSONResponse({
        "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
        "cnpj": r[3] or "", "segmento": r[4] or "", "whatsapp": r[5] or "",
        "email": r[6] or "", "modulos": [str(m) for m in (_jsonb(r[7]))],
        "escopo": r[8] or "", "status": r[9] or "rascunho",
        "setup": brl(r[10]), "mensal": brl(r[11]), "total": brl(r[12]),
        "n_modulos": r[13] or 0, "itens": _jsonb(r[14]),
        "telefone": r[15] or "", "cidade": r[16] or "", "uf": r[17] or "",
        "site": r[18] or "", "cargo": r[19] or "", "socio": r[20] or "",
        "endereco": r[21] or "", "cep": r[22] or "",
        "evento": _jsonb(r[23], {}), "parcelas": _jsonb(r[24]),
        "numero": r[25], "modo": r[26] or "recorrente",
    })


class FecharIn(BaseModel):
    id: int


@router.post("/painel/servicos/fechar")
def painel_servicos_fechar(request: Request, dados: FecharIn):
    """Fecha o orçamento: vira contrato e gera os títulos a receber no módulo
    Empresa — setup + mensalidade recorrente no modo recorrente, um título por
    parcela no modo evento. Idempotente e escopado por conta."""
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
<p class="mut" style="margin-top:0">{{ 'Monte a proposta, salve no funil e feche o contrato — ao fechar, vira título a receber no módulo Empresa.' if servico_avulso else 'Monte a proposta, salve no funil e feche o contrato — ao fechar, vira título a receber (setup + mensalidade) no módulo Empresa.' }}</p>
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
.oc-grid{display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:1rem; align-items:start}
.oc-grid > *{min-width:0}
@media(max-width:820px){.oc-grid{grid-template-columns:minmax(0,1fr)}}
.oc-field{display:flex; flex-direction:column; gap:.35rem; margin-bottom:.6rem}
.oc-field label{font-size:.82rem; color:var(--txt-mut)}
.oc-inp{padding:.55rem .7rem; border-radius:8px; background:var(--bg); color:var(--txt); border:1px solid var(--borda); font-size:.95rem; width:100%; box-sizing:border-box}
.oc-inp:focus{border-color:var(--verde); outline:none}
.oc-mod{display:grid; grid-template-columns:auto 1fr 84px 84px 84px auto; gap:.55rem; align-items:center; padding:.6rem 0; border-bottom:1px solid var(--borda)}
.oc-mod.avulso,.oc-head.avulso{grid-template-columns:auto 1fr 58px 90px 90px auto}
.pg-row{display:grid; grid-template-columns:140px 110px minmax(0,1fr) minmax(0,1fr) auto; gap:.5rem; align-items:center; padding:.45rem 0; border-bottom:1px solid var(--borda)}
.pg-row:last-child{border-bottom:0}
.pg-row input{padding:.4rem .5rem; font-size:.88rem}
.pg-row .oc-valor{text-align:right}
@media(max-width:640px){.pg-row{grid-template-columns:1fr 1fr; gap:.4rem}.pg-row .pg-obs{grid-column:1/-1}}
.pg-aviso{color:#e6b877}
.oc-mod.off{opacity:.5}
.oc-mod .oc-nome,.oc-browse-row .oc-nome{cursor:default; min-width:0}
.oc-desc-preview{white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%}
.oc-rowacts{display:flex; gap:.35rem; white-space:nowrap}
.oc-ic{background:var(--bg); border:1px solid var(--borda); color:var(--txt-mut); cursor:pointer; font-size:.85rem; width:30px; height:30px; padding:0; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; transition:border-color .15s,color .15s,background .15s}
.oc-ic:hover{color:var(--txt); border-color:var(--verde); background:var(--card)}
.oc-del:hover{color:#e0857a; border-color:#5c2a27}
.oc-svcform{background:var(--bg); border:1px solid var(--borda); border-radius:10px; padding:.8rem; margin-top:.7rem}
.oc-empty{border:1px dashed var(--borda); border-radius:12px; padding:1.4rem; text-align:center; margin-top:.6rem}
.oc-tog{width:42px; height:24px; border-radius:99px; border:none; cursor:pointer; position:relative; background:#2a3550; flex:none}
.oc-tog.on{background:var(--verde-claro)}
.oc-tog::after{content:""; position:absolute; top:3px; left:3px; width:18px; height:18px; border-radius:50%; background:#fff; transition:left .15s}
.oc-tog.on::after{left:21px}
.oc-num{display:flex; align-items:center; gap:3px; border:1px solid var(--borda); border-radius:7px; padding:.35rem .5rem; background:var(--bg)}
.oc-num span{font-size:.62rem; color:var(--txt-mut); text-transform:uppercase; letter-spacing:.03em; white-space:nowrap}
.oc-num input{width:100%; border:none; background:transparent; color:var(--txt); text-align:right; font-size:.86rem; font-variant-numeric:tabular-nums}
.oc-num input:focus{outline:none}
.oc-custo-col{display:none}
.sv-wrap.oc-margin .oc-custo-col{display:flex}
.oc-head{display:grid; grid-template-columns:auto 1fr 84px 84px 84px auto; gap:.55rem; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--txt-mut); padding-bottom:.4rem; border-bottom:1px solid var(--borda)}
.oc-pill{padding:.4rem .8rem; border-radius:99px; border:1px solid var(--borda); background:var(--bg); color:var(--txt); cursor:pointer; font-size:.85rem}
.oc-pill.on{border-color:var(--verde-claro); background:#10241d; color:var(--verde-claro)}
.tipo-badge{font-size:.62rem; font-weight:700; letter-spacing:.02em; border-radius:5px; padding:.05rem .35rem; flex-shrink:0}
.tipo-badge.pj{color:#6fb0e6; border:1px solid #2f4a63; background:#11212e}
.tipo-badge.pf{color:var(--amber, var(--ambar)); border:1px solid var(--ambar-borda); background:#2a2113}
.cli-drop-item{padding:.55rem .8rem; cursor:pointer; border-bottom:1px solid var(--borda)}
.cli-drop-item:last-child{border-bottom:0}
.cli-drop-item:hover{background:var(--bg)}
.cli-drop-item .top{display:flex; align-items:center; gap:.4rem}
.cli-drop-item .nome{font-size:.86rem; font-weight:600}
.cli-drop-item .sub{font-size:.76rem; color:var(--txt-mut); margin-top:.15rem}
.oc-contador{display:inline-flex; align-items:center; gap:.3rem; font-size:.74rem; color:var(--txt-mut); background:var(--bg); border:1px solid var(--borda); border-radius:999px; padding:.15rem .6rem}
.oc-contador b{color:var(--txt)}
.oc-buscaic{position:absolute; left:.8rem; top:50%; transform:translateY(-50%); color:var(--txt-mut); font-size:.85rem; pointer-events:none}
.oc-drop-item{display:flex; align-items:center; justify-content:space-between; gap:.6rem; padding:.55rem .8rem; cursor:pointer; font-size:.85rem; border-bottom:1px solid var(--borda)}
.oc-drop-item:last-child{border-bottom:0}
.oc-drop-item:hover{background:var(--bg)}
.oc-drop-item .nome{overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0}
.oc-drop-item .preco{flex-shrink:0; color:var(--verde-claro); font-variant-numeric:tabular-nums; font-size:.8rem}
.oc-drop-empty{padding:.9rem .8rem; font-size:.82rem; color:var(--txt-mut); text-align:center}
.oc-vertodos-link{font-size:.8rem; color:var(--verde-claro); cursor:pointer; text-decoration:none; display:inline-block; margin-top:.7rem}
.oc-vertodos-link:hover{text-decoration:underline}
.oc-catalogo-completo{display:none; margin-top:.6rem; border-top:1px dashed var(--borda); padding-top:.7rem}
.oc-catalogo-completo.open{display:block}
.oc-browse-row{display:grid; grid-template-columns:auto 1fr 90px auto; gap:.55rem; align-items:center; padding:.5rem 0; border-bottom:1px solid var(--borda)}
.oc-browse-row:last-child{border-bottom:0}
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
.oc-btn-g{background:var(--verde); color:var(--sobre-verde)}
.oc-btn-o{background:transparent; border:1px solid var(--borda); color:var(--txt)}
.oc-hist{display:flex; align-items:center; justify-content:space-between; gap:.6rem; padding:.7rem .8rem; border:1px solid var(--borda); border-radius:10px; margin-bottom:.5rem; flex-wrap:wrap}
.oc-av{width:32px; height:32px; border-radius:8px; background:#13251d; color:var(--verde-claro); display:flex; align-items:center; justify-content:center; font-weight:700; flex:none; margin-right:.7rem}
.oc-badge{font-size:.66rem; font-weight:700; padding:.12rem .5rem; border-radius:6px; letter-spacing:.03em; text-transform:uppercase}
.oc-badge.fechado{background:#10241d; color:var(--verde-claro)}
.oc-badge.aberto{background:#2a2212; color:#e0b25a}
.oc-fechar{background:var(--verde); color:var(--sobre-verde); border:0; border-radius:8px; padding:.4rem .8rem; font-weight:600; cursor:pointer; font-size:.8rem}
/* mobile: cada serviço vira 2 linhas (toggle+nome+ações em cima, valores embaixo).
   Fica no FIM do bloco pra vencer a cascata das regras base acima. */
@media(max-width:600px){
  .sv-wrap{padding-left:.6rem; padding-right:.6rem}
  .sv-wrap .oc-head{display:none!important}   /* o JS seta display:grid inline; !important vence no mobile */
  .sv-wrap .oc-mod{display:flex; flex-wrap:wrap; align-items:center; gap:.4rem .5rem; padding:.7rem 0}
  .sv-wrap .oc-mod .oc-tog{order:1}
  .sv-wrap .oc-mod .oc-nome{order:2; flex:1 1 60%; min-width:0}
  .sv-wrap .oc-mod .oc-rowacts{order:3; margin-left:auto}
  .sv-wrap .oc-mod .oc-num{order:4; flex:1 1 40%}
  .sv-wrap .oc-mod .oc-num input{text-align:left}
  .sv-wrap .oc-browse-row{display:flex; flex-wrap:wrap; align-items:center; gap:.4rem .5rem; padding:.7rem 0}
  .sv-wrap .oc-browse-row .oc-tog{order:1}
  .sv-wrap .oc-browse-row .oc-nome{order:2; flex:1 1 60%; min-width:0}
  .sv-wrap .oc-browse-row .oc-rowacts{order:3; margin-left:auto}
  .sv-wrap .oc-browse-row .oc-num{order:4; flex:1 1 40%}
  .sv-wrap .oc-browse-row .oc-num input{text-align:left}
}
</style>{% endraw %}

<div class="card"{% if servico_avulso %} style="display:none"{% endif %}>
  <h2 style="margin-top:0">Escopo automático · IA</h2>
  <p class="mut" style="margin-top:0">Cole o site ou a descrição do cliente. A IA escolhe os módulos e escreve o escopo da proposta.</p>
  <textarea id="oc-desc" class="oc-inp" rows="3" placeholder="Ex.: clínica com 3 unidades, muito WhatsApp, quer reduzir faltas e organizar leads..."></textarea>
  <div style="display:flex; align-items:center; gap:.8rem; margin-top:.6rem">
    <button id="oc-sugerir" class="oc-btn-g" style="border:none; border-radius:8px; padding:.55rem 1rem; cursor:pointer; font-weight:600; width:auto; margin:0">Sugerir escopo</button>
    <span id="oc-ia-msg" class="mut" style="font-size:.85rem"></span>
  </div>
  <div id="oc-escopo-out" class="mut" style="display:none; margin-top:.8rem; padding:.8rem; background:var(--bg); border:1px solid var(--borda); border-radius:8px; line-height:1.6"></div>
</div>

{% if servico_avulso %}
<div class="card">
  <h2 style="margin-top:0">O evento</h2>
  <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:.8rem">
    <div class="oc-field"><label>Data</label><input id="ev-data" class="oc-inp" type="date"></div>
    <div class="oc-field"><label>Convidados</label><input id="ev-conv" class="oc-inp" inputmode="numeric" placeholder="50"></div>
    <div class="oc-field"><label>Início</label><input id="ev-ini" class="oc-inp" placeholder="19:00"></div>
    <div class="oc-field"><label>Encerramento</label><input id="ev-fim" class="oc-inp" placeholder="24:00"></div>
  </div>
  <div class="oc-field"><label>Tipo de evento</label>
    <div style="display:flex; gap:.4rem; flex-wrap:wrap" id="ev-tipos">
      <button type="button" class="oc-pill ev-tipo">Aniversário</button>
      <button type="button" class="oc-pill ev-tipo">Casamento</button>
      <button type="button" class="oc-pill ev-tipo">Confraternização</button>
      <button type="button" class="oc-pill ev-tipo">Corporativo</button>
      <button type="button" class="oc-pill ev-tipo">Infantil</button>
      <button type="button" class="oc-pill ev-tipo">Suítes</button>
    </div>
  </div>
  <div class="oc-field"><label>Tipo de contrato</label>
    <div style="display:flex; gap:.4rem; flex-wrap:wrap" id="ev-contratos">
      <button type="button" class="oc-pill ev-ct" data-on="0">Locação de espaço</button>
      <button type="button" class="oc-pill ev-ct" data-on="0">Locação de móveis e utensílios</button>
      <button type="button" class="oc-pill ev-ct" data-on="0">Serviços terceirizados</button>
    </div>
  </div>
  <div class="oc-field" style="margin-bottom:.3rem"><label>Local</label><input id="ev-local" class="oc-inp" placeholder="Espaço 01 — Rua Deoclécio Brito, 3399"></div>
  <p class="mut" style="font-size:.78rem;margin:0">Festa que encerra às <b>24:00</b> termina 00:00 do dia seguinte — quando o cliente aprovar, o compromisso entra na agenda já com essa virada.</p>
</div>
{% endif %}

<div class="card">
  <h2 style="margin-top:0">Cliente</h2>

  {% if servico_avulso %}
  <div style="position:relative">
    <input id="cli-busca" class="oc-inp" placeholder="🔍 Buscar cliente já cadastrado na Base… (nome, empresa)" autocomplete="off">
    <div id="cli-drop" style="display:none; position:absolute; left:0; right:0; top:calc(100% + 6px); background:var(--card-2); border:1px solid var(--borda); border-radius:10px; max-height:280px; overflow-y:auto; z-index:5; box-shadow:0 12px 30px rgba(0,0,0,.4)"></div>
  </div>
  <a id="cli-novo-link" href="#" style="font-size:.78rem; color:var(--verde-claro); text-decoration:none; display:inline-block; margin-top:.5rem">✏️ ou cadastrar um cliente novo, sem vínculo com lead</a>

  <div id="cli-chip" style="display:none; align-items:center; gap:.8rem; padding:.7rem .9rem; border:1px solid var(--borda); border-radius:12px; background:var(--card-2); margin-top:.8rem">
    <div id="cli-chip-av" style="width:38px; height:38px; border-radius:10px; background:#10241d; border:1px solid #1c3a30; color:var(--verde-claro); display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1rem; flex-shrink:0">?</div>
    <div style="flex:1; min-width:0">
      <b id="cli-chip-nome"></b>
      <div class="mut" id="cli-chip-sub" style="font-size:.78rem; margin-top:.1rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis"></div>
    </div>
    <span id="cli-chip-tipo" class="tipo-badge"></span>
    <button type="button" class="oc-pill" id="cli-ver-dados" style="padding:.3rem .6rem; font-size:.78rem">Ver dados</button>
    <button type="button" class="oc-pill" id="cli-trocar" style="padding:.3rem .6rem; font-size:.78rem">Trocar</button>
  </div>
  {% endif %}

  <div id="cli-form-full"{% if servico_avulso %} style="display:none; margin-top:.8rem; border-top:1px dashed var(--borda); padding-top:.8rem"{% endif %}>
    {% if servico_avulso %}
    <div style="display:flex; gap:.4rem; margin-bottom:.8rem">
      <button type="button" class="oc-pill" id="btn-tipo-pj" data-tipo="pj">🏢 Pessoa Jurídica</button>
      <button type="button" class="oc-pill" id="btn-tipo-pf" data-tipo="pf">🧑 Pessoa Física</button>
    </div>
    {% endif %}
    <div class="oc-field" style="margin-bottom:.7rem">
      <label id="oc-cnpj-label">{{ 'CNPJ / CPF' if servico_avulso else 'CNPJ' }} <span style="color:var(--txt-mut);font-size:.78rem">— preenche empresa, segmento e contato automaticamente</span></label>
      <div style="display:flex; gap:.5rem; align-items:center">
        <input id="oc-cnpj" class="oc-inp" placeholder="00.000.000/0000-00" inputmode="numeric" style="flex:1">
        <button id="oc-cnpj-btn" type="button" style="background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.55rem 1.1rem;font-weight:600;cursor:pointer;white-space:nowrap">Buscar</button>
      </div>
      <span id="oc-cnpj-msg" style="font-size:.8rem;color:var(--txt-mut);display:block;margin-top:.25rem"></span>
    </div>
    <div style="display:grid; grid-template-columns:{{ '1fr 1fr 1fr' if servico_avulso else '1fr 1fr' }}; gap:.8rem">
      <div class="oc-field"><label id="oc-empresa-label">Empresa</label><input id="oc-empresa" class="oc-inp" placeholder="Nome da empresa"></div>
      <div class="oc-field"><label>Contato</label><input id="oc-contato" class="oc-inp" placeholder="Responsável"></div>
      <div class="oc-field" id="campo-oc-cargo"{% if servico_avulso %} style="display:none"{% endif %}><label>Cargo</label><input id="oc-cargo" class="oc-inp" placeholder="Cargo do contato"></div>
      <div class="oc-field" id="campo-oc-socio"{% if servico_avulso %} style="display:none"{% endif %}><label>Sócio</label><input id="oc-socio" class="oc-inp" placeholder="Sócio / dono"></div>
      <div class="oc-field"><label>WhatsApp</label><input id="oc-whats" class="oc-inp" placeholder="(86) 9 9999-9999"></div>
      <div class="oc-field"{% if servico_avulso %} style="display:none"{% endif %}><label>Telefone</label><input id="oc-tel" class="oc-inp" placeholder="(86) 3333-0000"></div>
      <div class="oc-field"><label>E-mail</label><input id="oc-email" class="oc-inp" placeholder="contato@empresa.com.br"></div>
      <div class="oc-field"{% if servico_avulso %} style="display:none"{% endif %}><label>Site</label><input id="oc-site" class="oc-inp" inputmode="url" placeholder="site.com.br"></div>
      <div class="oc-field"{% if not servico_avulso %} style="display:none"{% endif %}><label>Endereço</label><input id="oc-endereco" class="oc-inp" placeholder="Rua, nº, bairro"></div>
      <div class="oc-field"{% if not servico_avulso %} style="display:none"{% endif %}><label>CEP</label><input id="oc-cep" class="oc-inp" inputmode="numeric" placeholder="64000-000"></div>
      <div class="oc-field"><label>Cidade</label><input id="oc-cidade" class="oc-inp" placeholder="Teresina"></div>
      <div class="oc-field"><label>UF</label><input id="oc-uf" class="oc-inp" maxlength="2" placeholder="PI"></div>
      <div class="oc-field"{% if servico_avulso %} style="display:none"{% endif %}><label>Segmento</label><input id="oc-segmento" class="oc-inp" placeholder="Saúde, Varejo, Logística..."></div>
    </div>
  </div>
</div>

<div class="oc-grid">
  <div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem">
        <h2 style="margin:0">Meus serviços</h2>
        <div style="display:flex; gap:.5rem; flex-wrap:wrap; align-items:center">
          {% if servico_avulso %}<span class="oc-contador"><b id="oc-contador-n">0</b> de <span id="oc-contador-total">0</span> na proposta</span>{% endif %}
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
          <div class="oc-field" style="margin-bottom:0"><label>{{ 'Valor (R$)' if servico_avulso else 'Setup (R$)' }}</label><input id="svc-setup" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0{% if servico_avulso %};display:none{% endif %}"><label>Mensal (R$)</label><input id="svc-mensal" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Custo (R$)</label><input id="svc-custo" class="oc-inp" inputmode="numeric" value="0" style="text-align:right; max-width:120px"></div>
          <div style="flex:1; display:flex; gap:.4rem; justify-content:flex-end">
            <button id="svc-salvar" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">Salvar</button>
            <button id="svc-cancelar" class="oc-pill" type="button">Cancelar</button>
          </div>
        </div>
        <div id="svc-msg" class="mut" style="font-size:.8rem; margin-top:.4rem"></div>
      </div>

      {% if servico_avulso %}
      <div class="oc-buscabox" id="oc-buscabox" style="position:relative; margin-top:.8rem; display:none">
        <span class="oc-buscaic">🔍</span>
        <input id="oc-busca" class="oc-inp" placeholder="Buscar serviço pra adicionar… (ex.: drinks, dj, buffet)" autocomplete="off" style="padding-left:2.2rem">
        <div id="oc-drop" style="display:none; position:absolute; left:0; right:0; top:calc(100% + 6px); background:var(--card-2); border:1px solid var(--borda); border-radius:10px; max-height:280px; overflow-y:auto; z-index:5; box-shadow:0 12px 30px rgba(0,0,0,.4)"></div>
      </div>
      <div id="oc-sel-empty" class="oc-empty" style="display:none">
        <b>Nenhum serviço nesta proposta ainda</b>
        <p class="mut" style="margin:.3rem 0 0; font-size:.85rem">Busque acima e clique pra adicionar — só o que você escolher aparece aqui embaixo.</p>
      </div>
      {% endif %}
      <div class="oc-head{% if servico_avulso %} avulso{% endif %}" id="oc-head" style="margin-top:.8rem; display:none">
        <span></span><span>Serviço</span><span style="text-align:right">{{ 'Valor' if servico_avulso else 'Setup' }}</span>{% if not servico_avulso %}<span style="text-align:right">Mensal</span>{% endif %}<span style="text-align:right">{{ 'Custo' if servico_avulso else 'Custo/Margem' }}</span><span></span>
      </div>
      <div id="oc-mods"{% if servico_avulso %} style="display:none"{% endif %}></div>
      {% if servico_avulso %}
      <a id="oc-vertodos" href="#" class="oc-vertodos-link" style="display:none">📋 ver os <span id="oc-vertodos-n">0</span> serviços em ordem alfabética ›</a>
      <div id="oc-catalogo-completo" class="oc-catalogo-completo"></div>
      {% endif %}
      <div id="oc-mods-empty" class="oc-empty" style="display:none">
        <b>Você ainda não cadastrou seus serviços</b>
        <p class="mut" style="margin:.3rem 0 0">{{ 'Adicione o que a sua empresa vende — nome e valor. Isso vira o seu catálogo pra montar orçamentos.' if servico_avulso else 'Adicione o que a sua empresa vende — nome, setup e mensalidade. Isso vira o seu catálogo pra montar orçamentos.' }}</p>
        <div style="display:flex; gap:.5rem; justify-content:center; margin-top:.8rem; flex-wrap:wrap">
          <button id="oc-add2" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">+ Adicionar serviço</button>
          {% if not servico_avulso %}<button id="oc-import" class="oc-pill" type="button">Usar modelo de tecnologia</button>{% endif %}
        </div>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:.6rem">
        <button id="oc-todos" class="oc-pill" type="button" style="display:none">Marcar todos</button>
        <button id="oc-limpar" class="oc-pill" type="button" style="display:none">Limpar seleção</button>
      </div>
    </div>

    {% if servico_avulso %}
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem">
        <h2 style="margin:0">Plano de pagamento</h2>
        <div style="display:flex; gap:.5rem; flex-wrap:wrap">
          <button id="pg-gerar" class="oc-pill" type="button">Sinal + parcelas…</button>
          <button id="pg-add" class="oc-pill" type="button">+ Parcela</button>
        </div>
      </div>
      <p class="mut" style="margin:.3rem 0 .6rem; font-size:.85rem">Cada linha vira um título a receber, no vencimento, quando você fechar o contrato.</p>

      <div id="pg-gerador" style="display:none; background:var(--bg); border:1px solid var(--borda); border-radius:10px; padding:.7rem .8rem; margin-bottom:.7rem">
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:.6rem">
          <div class="oc-field" style="margin-bottom:0"><label>Sinal (R$)</label><input id="pg-entrada" class="oc-inp" placeholder="0,00"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Nº de parcelas</label><input id="pg-n" class="oc-inp" inputmode="numeric" value="12"></div>
          <div class="oc-field" style="margin-bottom:0"><label>1º vencimento</label><input id="pg-venc" class="oc-inp" type="date"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Forma</label><input id="pg-forma" class="oc-inp" placeholder="Cartão de crédito"></div>
        </div>
        <div style="display:flex; gap:.5rem; margin-top:.6rem">
          <button id="pg-gerar-ok" class="oc-btn-g" type="button" style="border:0;border-radius:8px;padding:.5rem 1rem;font-weight:600;cursor:pointer">Gerar</button>
          <button id="pg-gerar-cc" class="oc-pill" type="button">Cancelar</button>
        </div>
      </div>

      <div id="pg-linhas"></div>
      <div id="pg-vazio" class="oc-empty"><b>Sem parcelas ainda</b>
        <p class="mut" style="margin:.3rem 0 0; font-size:.85rem">Sem plano de pagamento, fechar o contrato gera um título só, com o total.</p></div>
      <div class="mut" id="pg-resumo" style="font-size:.82rem; margin-top:.6rem"></div>
    </div>
    {% endif %}

    {% if not servico_avulso %}
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
          <div class="oc-step"><button type="button" data-step="-1">-</button><span class="v" id="oc-integ-v">0</span><button type="button" data-step="1">+</button></div>
          <input type="hidden" id="oc-integ" value="0">
        </div>
        <div class="oc-field" style="margin-bottom:0"><label>Suporte 24h</label>
          <button id="oc-sup" class="oc-pill" data-on="0" type="button">Atendimento dedicado</button>
        </div>
      </div>
      <div class="oc-field" style="margin-top:.8rem"><label>Canais</label>
        <div style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button class="oc-canal oc-pill" data-on="0">WhatsApp</button>
          <button class="oc-canal oc-pill" data-on="0">Site</button>
          <button class="oc-canal oc-pill" data-on="0">Instagram</button>
          <button class="oc-canal oc-pill" data-on="0">Telegram</button>
          <button class="oc-canal oc-pill" data-on="0">E-mail</button>
          <button class="oc-canal oc-pill" data-on="0">Voz</button>
        </div>
      </div>
    </div>
    {% endif %}
  </div>

  <div class="oc-ledger">
    <div class="card" style="margin:0">
      <div class="mut" style="font-size:.78rem; letter-spacing:.1em; text-transform:uppercase; color:var(--verde-claro)">Resumo · ao vivo</div>
      <div class="oc-ll"><span class="mut">Investimento inicial</span><b id="oc-r-setup">R$ 0</b></div>
      {% if not servico_avulso %}<div class="oc-ll"><span class="mut">Mensalidade</span><b id="oc-r-mensal" style="color:var(--verde-claro)">R$ 0</b></div>{% endif %}
      <div class="oc-ll" id="oc-r-margem-l" style="display:none"><span class="mut">{{ 'Margem' if servico_avulso else 'Margem/mês' }}</span><b id="oc-r-margem" style="color:var(--verde-claro); font-size:.95rem">-</b></div>
      <div class="oc-total"><div class="mut" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--verde-claro)">{{ 'Total' if servico_avulso else 'Total 1º ano' }}</div><div class="v" id="oc-r-ano">R$ 0</div><div class="mut" id="oc-r-eco" style="display:none; font-size:.8rem; color:var(--verde-claro); margin-top:.3rem"></div></div>
      {% if servico_avulso %}
      <div class="oc-field" style="margin-top:.7rem; margin-bottom:0"><label class="mut" style="font-size:.76rem">Desconto (%)</label><input id="oc-desconto" class="oc-inp" inputmode="numeric" value="0" style="text-align:right"></div>
      {% else %}
      <button id="oc-anual" class="oc-pill" data-on="0" type="button" style="width:100%; margin-top:.7rem; text-align:left; display:flex; justify-content:space-between; align-items:center">Pagamento anual (-15%) <span id="oc-anual-mk">↻</span></button>
      {% endif %}
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

<script>window.SERVICO_AVULSO = {{ 'true' if servico_avulso else 'false' }};</script>
{% raw %}<script>
(function(){
  var SERVICO_AVULSO = window.SERVICO_AVULSO;
  var INFRA={compartilhada:{s:0,m:0},dedicada:{s:1500,m:800},onpremise:{s:6000,m:1500}};
  function fmt(n){return 'R$ '+Math.round(n||0).toLocaleString('pt-BR');}
  function rows(){return [].slice.call(document.querySelectorAll('.oc-mod'));}
  function num(el){return parseInt(((el&&el.value)||'0').replace(/\D/g,''),10)||0;}
  function seg(g){var b=document.querySelector('[data-grupo="'+g+'"].on'); return b?b.getAttribute('data-val'):'';}
  var WRAP=document.querySelector('.sv-wrap');

  // quantidade da linha (só existe no modo evento; sem o campo, é sempre 1)
  function qtd(r){return Math.max(1,num(r.querySelector('.oc-qtd')));}

  function calc(){
    var setup=0,mensal=0,modMensal=0,custo=0,mods=0;
    rows().forEach(function(r){
      if(r.getAttribute('data-on')==='1'){
        mods++;
        var q=qtd(r);
        setup+=num(r.querySelector('.oc-setup'))*q;
        var mm=num(r.querySelector('.oc-mensal'));
        mensal+=mm; modMensal+=mm;
        custo+=num(r.querySelector('.oc-custo'))*q;
      }
    });
    var inf=INFRA[seg('infra')]||INFRA.compartilhada;
    setup+=inf.s; mensal+=inf.m;
    if(seg('volume')==='alto') mensal+=600;
    var integ=num(document.getElementById('oc-integ'));
    setup+=integ*250; mensal+=integ*120;
    var canais=document.querySelectorAll('.oc-canal[data-on="1"]').length;
    setup+=canais*400;
    var ocSup=document.getElementById('oc-sup');
    if(ocSup&&ocSup.getAttribute('data-on')==='1') mensal+=1500;
    if(SERVICO_AVULSO){
      var desconto=Math.max(0,Math.min(100,num(document.getElementById('oc-desconto'))));
      var total=setup*(1-desconto/100);
      var margemAv=setup-custo, margemAvPct=setup>0?Math.round(margemAv/setup*100):0;
      return {setup:setup,mensal:0,mensalCheio:0,ano1:total,margem:margemAv,margemPct:margemAvPct,mods:mods,desconto:desconto,economia:setup-total};
    }
    var anual=document.getElementById('oc-anual').getAttribute('data-on')==='1';
    var mensalEf=anual?mensal*0.85:mensal;
    var ano1=setup+mensalEf*12;
    var margem=modMensal-custo, margemPct=modMensal>0?Math.round(margem/modMensal*100):0;
    return {setup:setup,mensal:mensalEf,mensalCheio:mensal,ano1:ano1,margem:margem,margemPct:margemPct,mods:mods,anual:anual};
  }

  function pinta(){
    var c=calc();
    document.getElementById('oc-r-setup').textContent=fmt(c.setup);
    var elMensal=document.getElementById('oc-r-mensal');
    if(elMensal)elMensal.textContent=fmt(c.mensal);
    document.getElementById('oc-r-ano').textContent=fmt(c.ano1);
    document.getElementById('oc-r-margem').textContent=fmt(c.margem)+' · '+c.margemPct+'%';
    var eco=document.getElementById('oc-r-eco');
    if(SERVICO_AVULSO){
      if(c.desconto>0){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.economia);}
      else eco.style.display='none';
      pintaParcelas();   // mudou item/desconto -> o plano de pagamento pode ter deixado de fechar
    }else if(c.anual){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.mensalCheio*12*0.15)+' no ano';}
    else eco.style.display='none';
  }

  // linhas de serviço são dinâmicas (catálogo por conta) — delegação:
  var MODS=document.getElementById('oc-mods');
  MODS.addEventListener('click',function(e){
    if(SERVICO_AVULSO){
      var rm=e.target.closest('.oc-tog')||e.target.closest('.oc-rm');
      if(!rm) return;
      var row=rm.closest('.oc-mod'); if(!row) return;
      delete SELECIONADOS[row.getAttribute('data-id')];
      renderCatalogoAvulso();
      return;
    }
    var tog=e.target.closest('.oc-tog'); if(!tog) return;
    var r=tog.closest('.oc-mod'); var on=r.getAttribute('data-on')==='1';
    r.setAttribute('data-on',on?'0':'1'); r.classList.toggle('off',on); tog.classList.toggle('on',!on); pinta();
  });
  MODS.addEventListener('input',function(e){
    if(e.target.classList.contains('oc-setup')||e.target.classList.contains('oc-mensal')
       ||e.target.classList.contains('oc-custo')||e.target.classList.contains('oc-qtd')) pinta();
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
  var ocDesconto=document.getElementById('oc-desconto');
  if(ocDesconto)ocDesconto.addEventListener('input',pinta);

  // ---- O evento (modo evento) ----
  function coletarEvento(){
    if(!SERVICO_AVULSO)return null;
    var tipo=document.querySelector('#ev-tipos .ev-tipo.on');
    var cts=[].slice.call(document.querySelectorAll('#ev-contratos .ev-ct[data-on="1"]'))
             .map(function(b){return b.textContent.trim();});
    return {data:document.getElementById('ev-data').value||'',
            convidados:num(document.getElementById('ev-conv'))||null,
            inicio:document.getElementById('ev-ini').value||'',
            fim:document.getElementById('ev-fim').value||'',
            tipo:tipo?tipo.textContent.trim():'',
            contratos:cts,
            local:document.getElementById('ev-local').value||''};
  }
  function aplicarEvento(ev){
    if(!SERVICO_AVULSO)return;
    ev=ev||{};
    setv('ev-data',ev.data); setv('ev-conv',ev.convidados?String(ev.convidados):'');
    setv('ev-ini',ev.inicio); setv('ev-fim',ev.fim); setv('ev-local',ev.local);
    document.querySelectorAll('#ev-tipos .ev-tipo').forEach(function(b){
      b.classList.toggle('on',b.textContent.trim()===(ev.tipo||''));
    });
    var cts=ev.contratos||[];
    document.querySelectorAll('#ev-contratos .ev-ct').forEach(function(b){
      var on=cts.indexOf(b.textContent.trim())>=0;
      b.setAttribute('data-on',on?'1':'0'); b.classList.toggle('on',on);
    });
  }
  document.querySelectorAll('#ev-tipos .ev-tipo').forEach(function(b){
    b.addEventListener('click',function(){       // tipo de evento: escolhe um
      var on=b.classList.contains('on');
      document.querySelectorAll('#ev-tipos .ev-tipo').forEach(function(x){x.classList.remove('on');});
      if(!on)b.classList.add('on');
    });
  });
  document.querySelectorAll('#ev-contratos .ev-ct').forEach(function(b){
    b.addEventListener('click',function(){       // tipo de contrato: quantos quiser
      var on=b.getAttribute('data-on')==='1';
      b.setAttribute('data-on',on?'0':'1'); b.classList.toggle('on',!on);
    });
  });

  // ---- Plano de pagamento (modo evento) ----
  // Aqui o dinheiro tem CENTAVOS (7.810,00 dividido em 12 não fecha em reais
  // redondos), então parcela é guardada em centavos — o resto da tela continua
  // em reais inteiros.
  function centavos(s){
    s=String(s==null?'':s).replace(/[^\d,.-]/g,'');
    if(!s)return 0;
    if(s.indexOf(',')>=0) s=s.replace(/\./g,'').replace(',','.');
    var v=parseFloat(s);
    return isNaN(v)?0:Math.round(v*100);
  }
  function fmtc(c){return (Math.round(c||0)/100).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});}
  function pgRows(){return [].slice.call(document.querySelectorAll('.pg-row'));}
  function pgInp(cls,ph,val,tipo){
    var i=document.createElement('input');
    i.className='oc-inp '+cls; if(tipo)i.type=tipo;
    if(ph)i.placeholder=ph;
    i.value=(val==null?'':val);
    return i;
  }
  function addParcela(p){
    var box=document.getElementById('pg-linhas'); if(!box)return;
    p=p||{};
    var d=document.createElement('div'); d.className='pg-row';
    d.appendChild(pgInp('pg-venc','',p.venc||'','date'));
    d.appendChild(pgInp('pg-valor oc-valor','0,00',p.valor_centavos?fmtc(p.valor_centavos):''));
    d.appendChild(pgInp('pg-forma','Pix, cartão…',p.forma||''));
    d.appendChild(pgInp('pg-obs','Observação (ex.: sinal)',p.obs||''));
    var b=document.createElement('button');
    b.className='oc-ic pg-rm'; b.type='button'; b.title='Remover parcela'; b.textContent='🗑';
    d.appendChild(b);
    box.appendChild(d);
    pintaParcelas();
  }
  function coletarParcelas(){
    return pgRows().map(function(r){
      return {venc:r.querySelector('.pg-venc').value||'',
              valor_centavos:centavos(r.querySelector('.pg-valor').value),
              forma:r.querySelector('.pg-forma').value||'',
              obs:r.querySelector('.pg-obs').value||''};
    }).filter(function(p){return p.valor_centavos>0;});
  }
  function pintaParcelas(){
    var el=document.getElementById('pg-resumo'); if(!el)return;
    var linhas=pgRows();
    document.getElementById('pg-vazio').style.display=linhas.length?'none':'block';
    if(!linhas.length){el.textContent=''; el.className='mut'; return;}
    var soma=coletarParcelas().reduce(function(a,p){return a+p.valor_centavos;},0);
    var total=Math.round(calc().ano1*100), dif=total-soma;
    el.className='mut'+(dif!==0?' pg-aviso':'');
    el.textContent = dif===0
      ? ('As parcelas somam R$ '+fmtc(soma)+' — bate com o total do orçamento.')
      : (dif>0 ? ('Faltam R$ '+fmtc(dif)+' pra fechar o total de R$ '+fmtc(total)+'.')
               : ('As parcelas passam R$ '+fmtc(-dif)+' do total de R$ '+fmtc(total)+'.'));
  }
  function mesMais(iso,n){
    var p=(iso||'').split('-'); if(p.length!==3)return iso||'';
    var ano=parseInt(p[0],10), mes=parseInt(p[1],10)-1+n, dia=parseInt(p[2],10);
    var d=new Date(ano,mes,1);
    d.setDate(Math.min(dia,new Date(d.getFullYear(),d.getMonth()+1,0).getDate()));
    return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);
  }
  function gerarParcelas(){
    var total=Math.round(calc().ano1*100);
    if(total<=0){alert('Monte os itens primeiro — o total está zerado.');return;}
    var entrada=centavos(document.getElementById('pg-entrada').value);
    var n=Math.max(1,Math.min(60,num(document.getElementById('pg-n'))));
    var venc=document.getElementById('pg-venc').value;
    var forma=document.getElementById('pg-forma').value||'';
    if(!venc){alert('Escolha o 1º vencimento.');return;}
    if(entrada>total){alert('O sinal é maior que o total do orçamento.');return;}
    document.getElementById('pg-linhas').innerHTML='';
    if(entrada>0)addParcela({venc:venc,valor_centavos:entrada,forma:'Pix',
                             obs:'Sinal — confirma a reserva da data'});
    var resto=total-entrada;
    if(resto>0){
      // a sobra da divisão vai na ÚLTIMA parcela: a soma sempre fecha com o total.
      var base=Math.floor(resto/n), sobra=resto-base*n;
      for(var i=0;i<n;i++){
        addParcela({venc:mesMais(venc,entrada>0?i+1:i),
                    valor_centavos:base+(i===n-1?sobra:0),
                    forma:forma, obs:(n>1?('Parcela '+(i+1)+'/'+n):'')});
      }
    }
    document.getElementById('pg-gerador').style.display='none';
    pintaParcelas();
  }
  var pgBox=document.getElementById('pg-linhas');
  if(pgBox){
    pgBox.addEventListener('input',pintaParcelas);
    pgBox.addEventListener('click',function(e){
      var rm=e.target.closest('.pg-rm'); if(!rm)return;
      var row=rm.closest('.pg-row'); if(row)row.remove();
      pintaParcelas();
    });
    document.getElementById('pg-add').addEventListener('click',function(){addParcela();});
    var pgGer=document.getElementById('pg-gerador');
    document.getElementById('pg-gerar').addEventListener('click',function(){
      pgGer.style.display=(pgGer.style.display==='none'?'block':'none');
    });
    document.getElementById('pg-gerar-cc').addEventListener('click',function(){pgGer.style.display='none';});
    document.getElementById('pg-gerar-ok').addEventListener('click',gerarParcelas);
  }

  // ---- catálogo de serviços por conta (Meus serviços) ----
  var CATALOGO=[];
  var SELECIONADOS={};   // eventos (servico_avulso): slugs marcados nesta proposta
  var VERTODOS_OPEN=false;
  function ec(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

  // eventos: catálogo ordenado A-Z + "busca pra adicionar" — a lista só mostra
  // o que já foi escolhido pra esta proposta; o resto fica atrás da busca ou
  // do link "ver todos", pra não repetir o card gigante de antes com 26 linhas
  // sempre visíveis.
  function buildRowAvulso(s){
    return '<button class="oc-tog on" type="button" title="Remover da proposta"></button>'
      +'<div class="oc-nome"><b>'+ec(s.nome)+'</b><div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div>'
      +'<div class="oc-num"><span>Qtd</span><input class="oc-qtd" inputmode="numeric" value="1"></div>'
      +'<div class="oc-num"><span>Vr. unit.</span><input class="oc-setup" inputmode="numeric" value="'+s.setup+'"></div>'
      +'<div class="oc-num"><span>Custo</span><input class="oc-custo" inputmode="numeric" value="'+s.custo+'"></div>'
      +'<div class="oc-rowacts"><button class="oc-ic oc-rm" type="button" title="Remover da proposta">🗑</button></div>';
  }
  function renderCatalogoAvulso(){
    var box=document.getElementById('oc-mods');
    var valores={};
    rows().forEach(function(r){valores[r.getAttribute('data-id')]={setup:r.querySelector('.oc-setup').value,custo:r.querySelector('.oc-custo').value,qtd:r.querySelector('.oc-qtd').value};});
    box.innerHTML='';
    var itens=CATALOGO.filter(function(s){return SELECIONADOS[s.slug];});
    itens.forEach(function(s){
      var r=document.createElement('div'); r.className='oc-mod avulso';
      r.setAttribute('data-id',s.slug); r.setAttribute('data-on','1');
      r.setAttribute('data-nome',s.nome); r.setAttribute('data-desc',s.descricao||''); r.setAttribute('data-cid',s.id);
      r.innerHTML=buildRowAvulso(s);
      box.appendChild(r);
      var v=valores[s.slug];
      if(v){ r.querySelector('.oc-setup').value=v.setup; r.querySelector('.oc-custo').value=v.custo;
             if(v.qtd) r.querySelector('.oc-qtd').value=v.qtd; }
    });
    var vazioTotal=CATALOGO.length===0;
    box.style.display=itens.length?'block':'none';
    document.getElementById('oc-sel-empty').style.display=(itens.length||vazioTotal)?'none':'block';
    document.getElementById('oc-mods-empty').style.display=vazioTotal?'block':'none';
    var busca=document.getElementById('oc-buscabox'); if(busca)busca.style.display=vazioTotal?'none':'block';
    var vt=document.getElementById('oc-vertodos');
    if(vt){
      vt.style.display=vazioTotal?'none':'inline-block';
      vt.innerHTML='📋 '+(VERTODOS_OPEN?'esconder a lista completa ‹':('ver os <span id="oc-vertodos-n">'+CATALOGO.length+'</span> serviços em ordem alfabética ›'));
    }
    document.getElementById('oc-contador-n').textContent=itens.length;
    document.getElementById('oc-contador-total').textContent=CATALOGO.length;
    renderCatalogoCompleto();
    pinta();
  }
  function renderCatalogoCompleto(){
    var box=document.getElementById('oc-catalogo-completo');
    box.classList.toggle('open',VERTODOS_OPEN);
    if(!VERTODOS_OPEN){box.innerHTML=''; return;}
    box.innerHTML=CATALOGO.map(function(s){
      var on=!!SELECIONADOS[s.slug];
      return '<div class="oc-browse-row" data-id="'+ec(s.slug)+'"><button class="oc-tog'+(on?' on':'')+'" type="button" title="'+(on?'Remover da proposta':'Adicionar à proposta')+'"></button>'
        +'<div class="oc-nome"><b>'+ec(s.nome)+'</b><div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div>'
        +'<div class="oc-num"><span>Valor</span><input value="'+s.setup+'" readonly></div>'
        +'<div class="oc-rowacts"><button class="oc-ic oc-edit" type="button" title="Editar serviço">✎</button><button class="oc-ic oc-del" type="button" title="Excluir serviço">🗑</button></div></div>';
    }).join('');
  }

  function renderCatalogo(preserva){
    var box=document.getElementById('oc-mods'), onset={};
    if(preserva){rows().forEach(function(r){onset[r.getAttribute('data-id')]=r.getAttribute('data-on');});}
    box.innerHTML='';
    CATALOGO.forEach(function(s){
      // orçamento começa LIMPO: nenhum serviço marcado. O vendedor marca (ou a IA
      // sugere). Ao re-renderizar (add/editar), preserva o que já estava marcado.
      var on = preserva ? (onset[s.slug]!==undefined?onset[s.slug]:'0') : '0';
      var r=document.createElement('div'); r.className='oc-mod'+(SERVICO_AVULSO?' avulso':'')+(on==='1'?'':' off');
      r.setAttribute('data-id',s.slug); r.setAttribute('data-on',on);
      r.setAttribute('data-nome',s.nome); r.setAttribute('data-desc',s.descricao||''); r.setAttribute('data-cid',s.id);
      var lblValor=SERVICO_AVULSO?'Valor':'Setup';
      r.innerHTML='<button class="oc-tog'+(on==='1'?' on':'')+'" type="button" title="Entra nesta proposta"></button>'
        +'<div class="oc-nome"><b>'+ec(s.nome)+'</b><div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div>'
        +'<div class="oc-num"><span>'+lblValor+'</span><input class="oc-setup" inputmode="numeric" value="'+s.setup+'"></div>'
        +(SERVICO_AVULSO?'':'<div class="oc-num"><span>Mensal</span><input class="oc-mensal" inputmode="numeric" value="'+s.mensal+'"></div>')
        +'<div class="oc-num'+(SERVICO_AVULSO?'':' oc-custo-col')+'"><span>Custo</span><input class="oc-custo" inputmode="numeric" value="'+s.custo+'"></div>'
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
      CATALOGO=d.itens||[];
      if(SERVICO_AVULSO){
        CATALOGO.sort(function(a,b){return (a.nome||'').localeCompare(b.nome||'','pt-BR');});
        Object.keys(SELECIONADOS).forEach(function(id){if(!CATALOGO.some(function(s){return s.slug===id;}))delete SELECIONADOS[id];});
        renderCatalogoAvulso();
      } else {
        renderCatalogo(preserva);
      }
    }).catch(function(){});
  }
  if(SERVICO_AVULSO){
    var ocBusca=document.getElementById('oc-busca'), ocDrop=document.getElementById('oc-drop');
    var renderDropBusca=function(){
      var q=(ocBusca.value||'').trim().toLowerCase();
      if(!q){ocDrop.style.display='none'; ocDrop.innerHTML=''; return;}
      var m=CATALOGO.filter(function(s){return !SELECIONADOS[s.slug] && (s.nome||'').toLowerCase().indexOf(q)>=0;});
      ocDrop.innerHTML = m.length
        ? m.slice(0,8).map(function(s){return '<div class="oc-drop-item" data-id="'+ec(s.slug)+'"><span class="nome">'+ec(s.nome)+'</span><span class="preco">'+fmt(s.setup)+'</span></div>';}).join('')
        : '<div class="oc-drop-empty">Nenhum serviço com esse nome.</div>';
      ocDrop.style.display='block';
    }
    ocBusca.addEventListener('input',renderDropBusca);
    ocBusca.addEventListener('focus',function(){if(ocBusca.value.trim())renderDropBusca();});
    document.addEventListener('click',function(e){if(!e.target.closest('#oc-buscabox'))ocDrop.style.display='none';});
    ocDrop.addEventListener('click',function(e){
      var it=e.target.closest('.oc-drop-item'); if(!it)return;
      SELECIONADOS[it.getAttribute('data-id')]=true;
      ocBusca.value=''; ocDrop.style.display='none'; ocDrop.innerHTML='';
      renderCatalogoAvulso();
    });

    document.getElementById('oc-vertodos').addEventListener('click',function(e){
      e.preventDefault();
      VERTODOS_OPEN=!VERTODOS_OPEN;
      renderCatalogoAvulso();
    });
    document.getElementById('oc-catalogo-completo').addEventListener('click',function(e){
      var tog=e.target.closest('.oc-tog');
      if(tog){
        var row=tog.closest('.oc-browse-row'); var id=row.getAttribute('data-id');
        if(SELECIONADOS[id])delete SELECIONADOS[id]; else SELECIONADOS[id]=true;
        renderCatalogoAvulso();
        return;
      }
      var ed=e.target.closest('.oc-edit'), dl=e.target.closest('.oc-del');
      if(ed){var row2=ed.closest('.oc-browse-row'); var s=CATALOGO.filter(function(x){return x.slug===row2.getAttribute('data-id');})[0]; if(s)abrirForm({id:s.id,nome:s.nome,descricao:s.descricao,setup:s.setup,mensal:s.mensal,custo:s.custo});}
      else if(dl){var row3=dl.closest('.oc-browse-row'); var s2=CATALOGO.filter(function(x){return x.slug===row3.getAttribute('data-id');})[0]; if(s2&&confirm('Excluir "'+s2.nome+'" do seu catálogo?')){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:s2.id})}).then(function(){carregarCatalogo(true);});}}
    });
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
  var ocImport=document.getElementById('oc-import');
  if(ocImport)ocImport.addEventListener('click',function(){
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
        set('oc-cidade',d.cidade); set('oc-uf',d.uf);
        var loc=[d.cidade,d.uf].filter(Boolean).join('/');
        msg.textContent='Preenchido pela Receita'+(loc?' — '+loc:'')+'. Confira e ajuste se precisar.';
        if(SERVICO_AVULSO)atualizarChip();
      })
      .catch(function(){msg.textContent='Erro de conexão ao consultar.';})
      .finally(function(){btn.disabled=false; btn.textContent=t0;});
  });

  // ---- Cliente: buscar da Base (só nicho eventos — servico_avulso) ----
  function aplicaTipoCliente(tipo){
    var btnPj=document.getElementById('btn-tipo-pj'), btnPf=document.getElementById('btn-tipo-pf');
    if(!btnPj||!btnPf)return;   // não é eventos, esse toggle nem existe
    var pj=tipo!=='pf';
    btnPj.classList.toggle('on',pj); btnPf.classList.toggle('on',!pj);
    document.getElementById('oc-cnpj').placeholder=pj?'00.000.000/0000-00':'000.000.000-00';
    document.getElementById('oc-cnpj-btn').style.display=pj?'inline-block':'none';
    document.getElementById('oc-empresa-label').textContent=pj?'Empresa':'Nome completo';
    document.getElementById('oc-empresa').placeholder=pj?'Nome da empresa':'Nome completo';
    // Cargo/Sócio/Telefone/Site/Segmento ficam ocultos sempre pra eventos (não é
    // sobre PJ×PF — esse segmento não precisa desses campos, ponto), então esse
    // toggle não mexe mais na visibilidade deles.
  }
  var btnTipoPj=document.getElementById('btn-tipo-pj'), btnTipoPf=document.getElementById('btn-tipo-pf');
  if(btnTipoPj)btnTipoPj.addEventListener('click',function(){aplicaTipoCliente('pj');});
  if(btnTipoPf)btnTipoPf.addEventListener('click',function(){aplicaTipoCliente('pf');});

  function atualizarChip(){
    var chip=document.getElementById('cli-chip');
    if(!chip)return;   // não é eventos, essa UI nem existe
    var nome=(document.getElementById('oc-empresa').value||'').trim();
    var busca=document.getElementById('cli-busca');
    if(!nome){
      chip.style.display='none';
      if(busca)busca.style.display='';
      var linkN=document.getElementById('cli-novo-link'); if(linkN)linkN.style.display='';
      return;
    }
    var cnpjDig=(document.getElementById('oc-cnpj').value||'').replace(/\D/g,'');
    var tipo=cnpjDig.length===14?'pj':'pf';
    aplicaTipoCliente(tipo);
    document.getElementById('cli-chip-av').textContent=nome.charAt(0).toUpperCase();
    document.getElementById('cli-chip-nome').textContent=nome;
    var partes=[document.getElementById('oc-whats').value,document.getElementById('oc-email').value,document.getElementById('oc-cidade').value].filter(Boolean);
    document.getElementById('cli-chip-sub').textContent=partes.join(' · ');
    var tb=document.getElementById('cli-chip-tipo'); tb.textContent=tipo.toUpperCase(); tb.className='tipo-badge '+tipo;
    chip.style.display='flex';
    document.getElementById('cli-form-full').style.display='none';
    if(busca)busca.style.display='none';
    var linkN2=document.getElementById('cli-novo-link'); if(linkN2)linkN2.style.display='none';
  }
  var cliBusca=document.getElementById('cli-busca'), cliDrop=document.getElementById('cli-drop');
  if(cliBusca){
    var cliTimer=null;
    function cliBadge(tipo){return '<span class="tipo-badge '+tipo+'">'+tipo.toUpperCase()+'</span>';}
    function cliRenderDrop(itens){
      if(!itens.length){cliDrop.innerHTML='<div class="cli-drop-item" style="cursor:default;color:var(--txt-mut);font-size:.82rem;text-align:center">Nenhum cliente com esse nome na Base.</div>';cliDrop.style.display='block';return;}
      cliDrop.innerHTML=itens.map(function(l,i){
        var sub=[l.email,l.cidade].filter(Boolean).join(' · ');
        return '<div class="cli-drop-item" data-i="'+i+'"><span class="top">'+cliBadge(l.tipo)+'<span class="nome">'+ec(l.empresa)+'</span></span><span class="sub">'+ec(sub)+'</span></div>';
      }).join('');
      cliDrop.style.display='block';
      cliDrop._itens=itens;
    }
    cliBusca.addEventListener('input',function(){
      var q=cliBusca.value.trim();
      clearTimeout(cliTimer);
      if(q.length<2){cliDrop.style.display='none'; cliDrop.innerHTML=''; return;}
      cliTimer=setTimeout(function(){
        fetch('/painel/servicos/leads/buscar?q='+encodeURIComponent(q))
          .then(function(r){return r.json();})
          .then(function(d){cliRenderDrop(d.itens||[]);})
          .catch(function(){});
      },250);
    });
    document.addEventListener('click',function(e){
      if(!e.target.closest('#cli-busca')&&!e.target.closest('#cli-drop'))cliDrop.style.display='none';
    });
    cliDrop.addEventListener('click',function(e){
      var it=e.target.closest('.cli-drop-item'); if(!it||!cliDrop._itens)return;
      var l=cliDrop._itens[parseInt(it.getAttribute('data-i'),10)]; if(!l)return;
      setv('oc-cnpj',l.cnpj); setv('oc-empresa',l.empresa); setv('oc-contato',l.contato);
      setv('oc-cargo',l.cargo); setv('oc-socio',l.socio); setv('oc-whats',l.whatsapp);
      setv('oc-tel',l.telefone); setv('oc-email',l.email); setv('oc-site',l.site);
      setv('oc-cidade',l.cidade); setv('oc-uf',l.uf); setv('oc-segmento',l.segmento);
      cliBusca.value=''; cliDrop.style.display='none'; cliDrop.innerHTML='';
      atualizarChip();
    });
  }
  var cliVerDados=document.getElementById('cli-ver-dados');
  if(cliVerDados)cliVerDados.addEventListener('click',function(){
    var f=document.getElementById('cli-form-full');
    f.style.display=(f.style.display==='none')?'block':'none';
  });
  var cliTrocar=document.getElementById('cli-trocar');
  if(cliTrocar)cliTrocar.addEventListener('click',function(){
    document.getElementById('cli-chip').style.display='none';
    document.getElementById('cli-form-full').style.display='none';
    if(cliBusca){cliBusca.style.display=''; cliBusca.focus();}
    var linkN=document.getElementById('cli-novo-link'); if(linkN)linkN.style.display='';
  });
  var cliNovoLink=document.getElementById('cli-novo-link');
  if(cliNovoLink)cliNovoLink.addEventListener('click',function(e){
    e.preventDefault();
    ['oc-empresa','oc-contato','oc-cnpj','oc-segmento','oc-whats','oc-email','oc-tel','oc-cidade','oc-uf','oc-site','oc-cargo','oc-socio'].forEach(function(id){setv(id,'');});
    aplicaTipoCliente('pj');
    document.getElementById('cli-form-full').style.display='block';
    document.getElementById('cli-chip').style.display='none';
    if(cliBusca)cliBusca.style.display='none';
    this.style.display='none';
  });

  function coletarBody(){
    var c=calc();
    var sel=rows().filter(function(r){return r.getAttribute('data-on')==='1';});
    // setup da linha = total (qtd × unitário) — é o que o funil soma; qtd e
    // unitário viajam junto pra o orçamento poder mostrar "10 × R$ 25,00".
    var itens=sel.map(function(r){
      var q=qtd(r), u=num(r.querySelector('.oc-setup'));
      return {nome:r.getAttribute('data-nome'),desc:r.getAttribute('data-desc')||'',
              setup:u*q,mensal:num(r.querySelector('.oc-mensal')),qtd:q,unitario:u};
    });
    var escEl=document.getElementById('oc-escopo-out');
    return {id:EDIT_ID,cliente:document.getElementById('oc-contato').value||'',empresa:document.getElementById('oc-empresa').value||'',cnpj:document.getElementById('oc-cnpj').value||'',segmento:document.getElementById('oc-segmento').value||'',whatsapp:document.getElementById('oc-whats').value||'',email:document.getElementById('oc-email').value||'',telefone:document.getElementById('oc-tel').value||'',cidade:document.getElementById('oc-cidade').value||'',uf:document.getElementById('oc-uf').value||'',site:document.getElementById('oc-site').value||'',cargo:document.getElementById('oc-cargo').value||'',socio:document.getElementById('oc-socio').value||'',endereco:(document.getElementById('oc-endereco')||{}).value||'',cep:(document.getElementById('oc-cep')||{}).value||'',modulos:sel.map(function(r){return r.getAttribute('data-id');}),itens:itens,evento:coletarEvento(),parcelas:(SERVICO_AVULSO?coletarParcelas():[]),escopo:(escEl.getAttribute('data-escopo')||''),setup:Math.round(c.setup),mensal:Math.round(c.mensal),primeiro_ano:Math.round(c.ano1),n_modulos:c.mods};
  }
  function salvarProposta(cb){
    fetch('/painel/servicos/salvar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(coletarBody())})
      .then(function(r){return r.json();}).then(function(d){if(d&&d.id){EDIT_ID=d.id;} if(cb)cb(d);})
      .catch(function(){if(cb)cb(null);});
  }
  document.getElementById('oc-salvar').addEventListener('click',function(){
    var btn=this; btn.textContent='Salvando...';
    salvarProposta(function(d){ btn.textContent=(d&&d.id)?'Salvo!':'Erro ao salvar'; carregarHist(); setTimeout(function(){btn.textContent='Salvar no funil';},1500); });
  });

  function esc(s){var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML;}
  function setv(id,v){var e=document.getElementById(id); if(e){e.value=v||'';}}
  function marcaMods(ids){
    if(SERVICO_AVULSO){
      SELECIONADOS={};
      (ids||[]).forEach(function(id){SELECIONADOS[id]=true;});
      renderCatalogoAvulso();
      return;
    }
    rows().forEach(function(r){
      var on=(ids||[]).indexOf(r.getAttribute('data-id'))>=0;
      r.setAttribute('data-on',on?'1':'0'); r.classList.toggle('off',!on);
      r.querySelector('.oc-tog').classList.toggle('on',on);
    });
  }
  var EDIT_ID=null;
  function novo(){
    EDIT_ID=null;
    ['oc-empresa','oc-contato','oc-cnpj','oc-segmento','oc-whats','oc-email','oc-tel','oc-cidade','oc-uf','oc-site','oc-cargo','oc-socio','oc-desc','oc-endereco','oc-cep'].forEach(function(id){setv(id,'');});
    aplicarEvento({});
    var pgb=document.getElementById('pg-linhas');
    if(pgb){pgb.innerHTML=''; pintaParcelas();}
    var out=document.getElementById('oc-escopo-out'); out.style.display='none'; out.removeAttribute('data-escopo'); out.textContent='';
    document.getElementById('oc-editando').style.display='none';
    marcaMods([]);   // proposta nova começa sem nenhum serviço marcado
    if(SERVICO_AVULSO)atualizarChip();
    pinta();
  }
  function abrir(id){
    fetch('/painel/servicos/item/'+id).then(function(r){return r.json();}).then(function(d){
      if(d.erro){alert('Não consegui abrir essa proposta.'); return;}
      EDIT_ID=d.id;
      setv('oc-empresa',d.empresa); setv('oc-contato',d.cliente); setv('oc-cnpj',d.cnpj);
      setv('oc-segmento',d.segmento); setv('oc-whats',d.whatsapp); setv('oc-email',d.email);
      setv('oc-tel',d.telefone); setv('oc-cidade',d.cidade); setv('oc-uf',d.uf);
      setv('oc-site',d.site); setv('oc-cargo',d.cargo); setv('oc-socio',d.socio);
      setv('oc-endereco',d.endereco); setv('oc-cep',d.cep);
      aplicarEvento(d.evento);
      if(SERVICO_AVULSO){
        var pgb=document.getElementById('pg-linhas');
        if(pgb){pgb.innerHTML=''; (d.parcelas||[]).forEach(addParcela); pintaParcelas();}
      }
      marcaMods(d.modulos);
      // restaura os valores EXATOS que estavam salvos (não recalcula pelo catálogo)
      (d.itens||[]).forEach(function(it){
        var r=rows().filter(function(x){return x.getAttribute('data-nome')===it.nome;})[0];
        if(r){ var s=r.querySelector('.oc-setup'), m=r.querySelector('.oc-mensal'), q=r.querySelector('.oc-qtd');
          // proposta antiga não tem qtd/unitário: o setup salvo era o próprio unitário.
          var unit=(it.unitario!=null&&it.unitario>0)?it.unitario:it.setup;
          if(s&&unit!=null) s.value=unit;
          if(q&&it.qtd) q.value=it.qtd;
          if(m&&it.mensal!=null) m.value=it.mensal; }
      });
      var out=document.getElementById('oc-escopo-out');
      if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
      else{out.style.display='none'; out.removeAttribute('data-escopo');}
      var bn=document.getElementById('oc-editando');
      bn.style.display='flex';
      var aviso=(d.status==='aprovada')?' · ⚠ editar vai pedir nova aprovação do cliente':'';
      bn.querySelector('.t').textContent='Editando proposta #'+d.id+' · '+d.status+aviso+' — salve pra atualizar o link do cliente.';
      if(SERVICO_AVULSO)atualizarChip();
      pinta();
      window.scrollTo({top:0,behavior:'smooth'});
    }).catch(function(){alert('Erro de conexão.');});
  }
  document.getElementById('oc-novo').addEventListener('click',novo);
  function fechar(id,btn){
    if(!confirm(SERVICO_AVULSO?'Fechar este contrato? Cada parcela do plano de pagamento vira um título a receber no módulo Empresa (sem plano, gera um título com o total).':'Fechar este contrato? Vai gerar título a receber (setup + mensalidade) no módulo Empresa.')){return;}
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
        var evento=it.modo==='evento';
        var sub=[(it.numero?('nº '+it.numero):''),esc(it.data),
                 it.mods+(evento?' itens':' módulos'),esc(evento?it.setup:it.total)]
                .filter(Boolean).join(' · ');
        left.innerHTML='<div class="oc-av">'+esc(it.inicial)+'</div>'
          +'<div><b>'+esc(it.cliente)+(it.empresa?' <span class="mut">· '+esc(it.empresa)+'</span>':'')+'</b>'
          +'<div class="mut" style="font-size:.78rem">'+sub+'</div></div>';
        left.addEventListener('click',function(){abrir(it.id);});
        el.appendChild(left);
        var right=document.createElement('div'); right.style.display='flex'; right.style.alignItems='center'; right.style.gap='.5rem'; right.style.flexWrap='wrap';
        var aprovada=it.status==='aprovada';
        var badge=document.createElement('span');
        badge.className='oc-badge '+(fechado?'fechado':(aprovada?'fechado':'aberto'));
        badge.textContent=fechado?'Fechado':(aprovada?('Aprovada'+(it.aprovada_por?' · '+esc(it.aprovada_por):'')):esc(it.status));
        right.appendChild(badge);
        if(!fechado){
          var be=document.createElement('button'); be.className='oc-ic'; be.title='Editar proposta'; be.textContent='✏️';
          be.addEventListener('click',function(){abrir(it.id);});
          right.appendChild(be);
        }
        if(it.token){
          var lk=window.location.origin+'/proposta/'+it.token;
          var bl=document.createElement('button'); bl.className='oc-ic'; bl.title='Copiar link do cliente'; bl.textContent='🔗';
          bl.addEventListener('click',function(){navigator.clipboard.writeText(lk); bl.textContent='✓'; setTimeout(function(){bl.textContent='🔗';},1200);});
          var bp=document.createElement('button'); bp.className='oc-ic'; bp.title='Abrir / baixar PDF'; bp.textContent='📄';
          bp.addEventListener('click',function(){window.open('/proposta/'+it.token,'_blank');});
          right.appendChild(bl); right.appendChild(bp);
        }
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

  // Gerar proposta = salva no funil e abre o LINK PÚBLICO (o mesmo que o cliente
  // recebe: vê, baixa PDF e aprova/assina). Abre a aba já, pra o popup não ser
  // bloqueado, e navega quando o token volta.
  document.getElementById('oc-gerar').addEventListener('click',function(){
    var emp=(document.getElementById('oc-empresa').value||'').trim();
    var sel=rows().filter(function(r){return r.getAttribute('data-on')==='1';});
    if(!emp && !sel.length){alert('Preencha a empresa ou marque ao menos um serviço.');return;}
    var w=window.open('about:blank','_blank');
    if(w){try{w.document.write('<p style="font-family:system-ui;color:#8A8475;padding:24px">Gerando proposta…</p>');}catch(e){}}
    var btn=this, t0=btn.textContent; btn.textContent='Gerando...';
    salvarProposta(function(d){
      btn.textContent=t0; carregarHist();
      if(!d||!d.token){ if(w)w.close(); alert('Não consegui gerar a proposta.'); return; }
      if(w){ w.location='/proposta/'+d.token; } else { window.location='/proposta/'+d.token; }
    });
  });

  carregarCatalogo(false).then(function(){
    var ab=new URLSearchParams(location.search).get('abrir');
    if(ab && /^[0-9]+$/.test(ab)){ abrir(ab); history.replaceState({},'','/painel/servicos'); }
  });
  carregarHist();
})();
</script>{% endraw %}
{% endblock %}"""

# Registra o template no env do portal (reusa base/nav/gate do painel).
_env.loader.mapping["servicos"] = _SERVICOS_TPL
