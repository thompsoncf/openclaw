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

NO NICHO DE EVENTOS o último passo é outro: o botão "Fechar contrato" não aparece,
porque quem abre o financeiro é a ASSINATURA do contrato pelo cliente. O botão
gerava contas a receber sem olhar contrato nenhum — e o nome convidava a apertar
justamente quando o contrato ainda estava sem assinatura.

O catálogo de serviços é POR CONTA (finance.servicos_catalogo) — cada empresa
monta o que vende. Empresa nova começa vazia; a Aladdin usa o modelo de
tecnologia. A IA de escopo e a validação de módulos usam o catálogo da conta.
"""
import json
import logging
import re
import secrets

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from psycopg.errors import UniqueViolation
from pydantic import BaseModel

from core.brain import Brain
from core import esquema_runtime
from db.conexao import get_pool
from finance.cnpj_info import consultar_cnpj
from finance import (agenda as ag, comprovantes as comprov, contrato as ctr,
                     desconto as dsc, empresa as emp, icones_servico as ics,
                     proposta_email as pmail, vendas, servicos_catalogo as scat)
from web.portal import _render, _env, conta_logada, brl

router = APIRouter()


def _garantir_tabela(c):
    """Cria/atualiza a tabela orcamentos em runtime — UMA VEZ POR PROCESSO.

    Espelha as migracoes 068/069/070/147/160/178/179, que o preDeployCommand do
    Render ja' roda no deploy. O comentario antigo dizia "deploy nao roda migracao
    sozinho; add-if-not-exists e' idempotente e barato": a primeira parte deixou
    de valer quando o preDeploy entrou, e a segunda nunca foi verdade. Sao 57
    comandos DDL, dois deles DROP/ADD CONSTRAINT, e ALTER TABLE pega ACCESS
    EXCLUSIVE — que enfileira ate' SELECT. Rodando isso a cada requisicao, em 12
    rotas, o painel inteiro ficou lento pra todo mundo em 22/08/2026.

    Ver core/esquema_runtime: a marca so' e' gravada se o DDL passar, e a chave
    inclui o banco, entao processo que fala com dois bancos garante os dois."""
    esquema_runtime.garantir(esquema_runtime.chave(c, "orcamentos"),
                             lambda: _criar_orcamentos(c))


def _criar_orcamentos(c):
    """O DDL em si. Chamado uma vez por processo, via _garantir_tabela."""
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
        alter table orcamentos add column if not exists cpf           text;
        alter table orcamentos add column if not exists whatsapp      text;
        alter table orcamentos add column if not exists email         text;
        alter table orcamentos add column if not exists modulos       jsonb;
        alter table orcamentos add column if not exists escopo        text;
        alter table orcamentos add column if not exists status        text not null default 'rascunho';
        alter table orcamentos add column if not exists criado_por    text;
        alter table orcamentos add column if not exists canal         text;
        alter table orcamentos add column if not exists desconto_tipo text not null default 'pct';
        alter table orcamentos add column if not exists desconto_pct  numeric(5,2) not null default 0;
        alter table orcamentos add column if not exists desconto_centavos bigint not null default 0;
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
        alter table orcamentos add column if not exists cliente_id    bigint;
        -- o sinal (migração 161): `sinal_centavos` é o valor congelado quando a
        -- data foi pré-reservada, `sinal_pago_em` é o carimbo de quem confirmou.
        -- Faltavam aqui — este mesmo arquivo já os LIA (ver `salvar` e a listagem
        -- do funil), então banco criado só por este guard quebrava na leitura.
        alter table orcamentos add column if not exists sinal_centavos int;
        alter table orcamentos add column if not exists sinal_pago_em timestamptz;
        -- contrato de locação (migração 160): o documento congelado no momento
        -- da assinatura, e quem assinou. Fica no orçamento, e não numa tabela
        -- própria, porque é 1-para-1 com ele e nasce e morre junto.
        alter table orcamentos add column if not exists contrato_texto        jsonb;
        alter table orcamentos add column if not exists contrato_assinado_em  timestamptz;
        alter table orcamentos add column if not exists contrato_assinado_por text;
        alter table orcamentos add column if not exists contrato_assinado_doc text;
        alter table orcamentos add column if not exists contrato_assinado_ip  text;
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
    # registro dos envios da proposta por e-mail (migração 178). Mesmo motivo das
    # linhas acima: o deploy não roda migração sozinho, e sem a tabela o histórico
    # some em silêncio — o botão manda e a tela diz "nunca enviado".
    c.execute("""
        create table if not exists orcamento_envios (
            id            bigserial primary key,
            conta_id      bigint      not null,
            orcamento_id  bigint      not null,
            canal         text        not null default 'email',
            destino       text        not null default '',
            remetente     text        not null default '',
            ok            boolean     not null default true,
            erro          text        not null default '',
            por           text        not null default '',
            criado_em     timestamptz not null default now());
        create index if not exists idx_orc_envios_orcamento
            on orcamento_envios (orcamento_id, criado_em desc);
    """)
    # comprovante de pagamento por PARCELA (migração 179). Guarda o CAMINHO no
    # bucket privado, nunca uma URL — ver finance/comprovantes.
    c.execute("""
        create table if not exists orcamento_comprovantes (
            id            bigserial primary key,
            conta_id      bigint      not null,
            orcamento_id  bigint      not null,
            parcela_idx   int         not null,
            caminho       text        not null,
            nome          text        not null default '',
            tipo          text        not null default '',
            bytes         bigint      not null default 0,
            por           text        not null default '',
            criado_em     timestamptz not null default now());
        create unique index if not exists uq_orc_comprovante
            on orcamento_comprovantes (orcamento_id, parcela_idx);
        create index if not exists idx_orc_comprovante_conta
            on orcamento_comprovantes (conta_id, orcamento_id);
    """)
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


def _dif_plano(parcelas, total_centavos, setup_centavos) -> int:
    """Soma das parcelas menos o total do orçamento, em centavos. 0 = fecha.

    Positivo quer dizer que o plano cobra MAIS do que o documento declara — foi
    exatamente o que aconteceu no primeiro orçamento de evento real: R$ 9.405,00 na
    folha e R$ 12.105,00 em títulos a receber, porque as parcelas tinham sido
    geradas antes de os itens mudarem e ninguém regerou."""
    itens = parcelas
    if isinstance(itens, str):
        try:
            itens = json.loads(itens)
        except ValueError:
            return 0
    if not isinstance(itens, list) or not itens:
        return 0
    soma = 0
    for p in itens:
        if isinstance(p, dict):
            try:
                v = int(p.get("valor_centavos") or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                soma += v
    total = int(total_centavos or 0) or int(setup_centavos or 0)
    return soma - total if (soma and total) else 0


def _espelhar_cliente(pool, conta_id: int, dados) -> int | None:
    """Salva o cliente do orçamento na base de Clientes da empresa.

    O vendedor puxa o lead, gera o orçamento e o cliente fica só dentro do
    orçamento (texto solto) — pra corrigir um telefone errado não tinha onde ir.
    Aqui ele passa a existir na aba Clientes desde o momento em que o orçamento
    é salvo. `clientes.criar_cliente` resolve a identidade (CPF/CNPJ fundem,
    celular só sugere) e reusa a relação, então salvar o mesmo orçamento dez
    vezes não cria dez clientes.

    Devolve o cliente_id, ou None quando não deu (sem nome, documento inválido).
    """
    from finance import clientes as cli
    nome = (dados.empresa or dados.cliente or "").strip()
    if not nome:
        return None
    doc = "".join(ch for ch in (dados.cnpj or "") if ch.isdigit())
    # cidade/uf são recentes no payload: getattr pra um orçamento antigo (ou uma
    # porta que ainda não manda os campos) não derrubar o espelhamento inteiro.
    comuns = {"telefone": (dados.whatsapp or dados.telefone or "").strip() or None,
              "email": (dados.email or "").strip() or None,
              "cidade": (getattr(dados, "cidade", "") or "").strip() or None,
              "uf": (getattr(dados, "uf", "") or "").strip() or None,
              "endereco": (getattr(dados, "endereco", "") or "").strip() or None,
              "cep": (getattr(dados, "cep", "") or "").strip() or None}
    try:
        return cli.criar_cliente(pool, conta_id, nome, **comuns,
                                 cpf=doc if len(doc) == 11 else None,
                                 cnpj=doc if len(doc) == 14 else None)
    except ValueError:
        # documento com dígito verificador inválido: o orçamento vale do mesmo
        # jeito, o cliente entra sem documento.
        return cli.criar_cliente(pool, conta_id, nome, **comuns)


def _com_retry_numero(c, executar, tentativas: int = 3):
    """Roda um insert/update que calcula `numero = max+1` da conta.

    O índice único (conta_id, numero) é quem garante a série: se dois salvarem
    ao mesmo tempo, o perdedor leva UniqueViolation, a transação volta e ele
    tenta de novo — na segunda vez o max já é o do vencedor.
    """
    for _ in range(tentativas):
        try:
            return executar()
        except UniqueViolation:
            c.rollback()
    return None


def _nicho(conta_id: int) -> str:
    """Slug do nicho da conta. É ele que decide o MODO do orçamento (evento ×
    recorrente) e o vocabulário da tela — nunca o que o navegador manda."""
    return emp.obter_dados_empresa(get_pool(), conta_id).get("nicho") or ""


def _local_padrao(dados: dict) -> str:
    """Endereço do estabelecimento, pronto pro campo "Local" do evento.

    A festa costuma ser no salão da própria empresa — deixar o campo vazio é
    obrigar o vendedor a digitar o mesmo endereço em todo orçamento. Vem
    preenchido; evento fora, ele troca."""
    rua = (dados.get("endereco") or "").strip()
    bairro = (dados.get("bairro") or "").strip()
    cidade = (dados.get("cidade") or "").strip()
    uf = (dados.get("uf") or "").strip().upper()
    cep = "".join(ch for ch in (dados.get("cep") or "") if ch.isdigit())
    if not rua:
        return ""
    partes = [rua]
    if bairro:
        partes.append(bairro)
    if cidade:
        partes.append(f"{cidade}/{uf}" if uf else cidade)
    if len(cep) == 8:
        partes.append(f"CEP {cep[:5]}-{cep[5:]}")
    return " · ".join(partes)


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
    dados_emp = emp.obter_dados_empresa(pool, conta[0])
    nicho = dados_emp.get("nicho") or ""
    # eventos vende PACOTE/preço de evento avulso — sem setup+mensalidade estilo
    # SaaS. A tela some com "Setup" e chama o preço único de "Valor" (fica
    # gravado em setup_centavos por baixo, pra "Fechar contrato" não virar
    # cobrança recorrente errada de um evento pontual).
    servico_avulso = nicho == "eventos"
    # O CONTRATO É DO DONO. Ele define o que a empresa se compromete a cumprir
    # com o cliente — prazo, multa, sinal — e isso não é decisão de quem vende.
    # `gerir` é a capacidade que já separa o titular do resto (contas/equipe.py:26:
    # só o dono tem); usar ela evita criar uma segunda régua de permissão que
    # amanhã diverge da primeira.
    from contas import equipe as _equipe
    pode_contrato = servico_avulso and _equipe.caps_do_papel(
        request.session.get("papel", "dono"))["gerir"]
    # A tela abre no tipo que ESTA empresa mais cadastra, em vez de sempre em PJ.
    # Ver clientes.tipo_predominante: na Prime, 23 de 23 clientes são PF.
    from finance import clientes as _cli
    tipo_padrao = _cli.tipo_predominante(pool, conta[0]) if servico_avulso else "pj"
    return _render("servicos", request, empresa_nome=conta[2],
                   tem_pj=True, vende_servico=True, servico_avulso=servico_avulso,
                   pode_contrato=pode_contrato, tipo_padrao=tipo_padrao,
                   tipos_evento=scat.TIPOS_EVENTO, tipos_contrato=scat.TIPOS_CONTRATO,
                   local_padrao=_local_padrao(dados_emp) if servico_avulso else "",
                   icones_paleta=ics.paleta())


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
        "categoria": s["categoria"], "foto_url": s["foto_url"],
        # `icone` é o que o vendedor fixou (pode ser vazio); `icone_svg` é o que
        # a tela desenha — já resolvido pelo nome/categoria quando não fixaram.
        "icone": s["icone"],
        "icone_svg": ics.svg(ics.escolher(s["nome"], s["categoria"], s["icone"]), px=20),
    } for s in scat.listar(pool, conta[0])]
    return JSONResponse({"itens": itens, "categorias": scat.CATEGORIAS_EVENTOS})


class ServicoIn(BaseModel):
    id: int | None = None
    nome: str = ""
    descricao: str = ""
    setup: int = 0     # REAIS
    mensal: int = 0    # REAIS
    custo: int = 0     # REAIS
    categoria: str = ""    # agrupa no orçamento de evento (subtotal por categoria)
    foto_url: str = ""     # legado: catálogo antigo que subiu foto
    icone: str = ""        # chave do ícone; vazio = deduzido do nome/categoria


@router.post("/painel/servicos/catalogo/salvar")
def painel_servicos_catalogo_salvar(request: Request, dados: ServicoIn):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = scat.salvar(get_pool(), conta[0], id=dados.id, nome=dados.nome,
                    descricao=dados.descricao,
                    setup_centavos=int(dados.setup or 0) * 100,
                    mensal_centavos=int(dados.mensal or 0) * 100,
                    custo_centavos=int(dados.custo or 0) * 100,
                    categoria=dados.categoria, foto_url=dados.foto_url,
                    icone=dados.icone)
    if not r.get("ok"):
        return JSONResponse({"erro": r.get("erro", "falha ao salvar")}, status_code=400)
    return JSONResponse(r)


class ServicoDelIn(BaseModel):
    id: int


@router.get("/painel/servicos/catalogo/icone-sugerido")
def painel_servicos_icone_sugerido(request: Request, nome: str = "", categoria: str = ""):
    """O ícone que o serviço teria sem ninguém escolher nada.

    A regra (nome -> categoria -> 'outros') mora no Python e é a MESMA que a
    folha usa; a tela pergunta em vez de reimplementar, senão painel e papel
    divergem no dia em que alguém mexer numa das listas."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    return JSONResponse({"chave": ics.escolher(nome, categoria)})


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
    categoria: str = ""       # evento: agrupa e soma por categoria na folha
    icone: str = ""           # evento: selo do item na folha (vazio = deduzido)
    # DESCONTO DA LINHA. Fica aqui, no snapshot do item, e não em coluna: `itens`
    # já é o retrato da linha no momento da proposta, e o desconto dela é parte do
    # mesmo retrato. Lista separada obrigaria a casar duas por índice — a armadilha
    # que a 162 tirou dos títulos.
    # `desc_val` e não `desc`: `desc` já é a DESCRIÇÃO do item, logo acima.
    desc_tipo: str = "pct"    # 'pct' | 'valor'
    desc_val: int = 0         # % ou REAIS, conforme desc_tipo


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
    desconto: int = 0         # % aplicado ao total (o que a tela já calculava)


class ParcelaIn(BaseModel):
    venc: str = ""
    valor_centavos: int = 0
    forma: str = ""
    obs: str = ""


class SalvarIn(BaseModel):
    id: int | None = None   # se vier, ATUALIZA a proposta (reaberta do funil)
    # DE QUAL LEAD É ESTA PROPOSTA. Vem preenchido quando o cliente foi escolhido na
    # busca da Base; null quando é cliente novo, sem vínculo. É este vínculo que faz
    # o card andar sozinho: o gatilho `orcamento_enviado` procura o orçamento por
    # `prospeccao.orcamento_id`, e proposta solta não tem card pra mover.
    lead_id: int | None = None
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
    setup: int = 0            # em REAIS, BRUTO (antes de qualquer desconto)
    mensal: int = 0           # em REAIS, bruto
    # o líquido NÃO vem mais da tela: o servidor recalcula com finance.desconto.
    # Continua no modelo porque a tela ainda o manda, e ignorá-lo em silêncio é
    # melhor que quebrar o payload de uma aba aberta durante o deploy.
    primeiro_ano: int = 0     # IGNORADO — derivado no servidor
    n_modulos: int = 0
    desconto_tipo: str = "pct"    # 'pct' | 'valor' — desconto do TOTAL
    desconto_pct: float = 0       # 0–100
    desconto_valor: int = 0       # em REAIS


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
              "qtd": max(1, int(it.qtd or 1)), "unitario": int(it.unitario or 0),
              "categoria": (it.categoria or "")[:60], "icone": (it.icone or "")[:30],
              "desc_tipo": "valor" if (it.desc_tipo or "") == "valor" else "pct",
              "desc_val": max(0, int(it.desc_val or 0))}
             for it in (dados.itens or [])[:50]]
    itens_json = json.dumps(itens)
    # o MODO vem do nicho da conta, não do navegador: quem vende evento emite
    # orçamento de evento, e só. (mesma regra do servico_avulso da tela)
    modo = vendas.modo_do_orcamento(get_pool(), conta[0])
    evento_dic = dados.evento.model_dump() if (modo == "evento" and dados.evento) else None
    evento_json = json.dumps(evento_dic) if evento_dic is not None else None
    parcelas_json = json.dumps(
        [p.model_dump() for p in (dados.parcelas or [])[:60] if int(p.valor_centavos or 0) > 0]
    ) if modo == "evento" else None
    # O TOTAL LÍQUIDO É DERIVADO AQUI, não recebido. O bruto continua vindo da
    # tela (ela conhece infraestrutura, canais e integrações, que não são linha),
    # mas quanto o desconto tira passa a ser conta do servidor — senão bastaria
    # editar o JSON no navegador pra fechar um orçamento por qualquer valor.
    #
    # `extra_*` é o que o modo recorrente soma FORA das linhas: recebe o desconto
    # do total (está no subtotal) e não recebe desconto por item (não é item).
    bruto_setup = max(0, int(dados.setup or 0)) * 100
    bruto_mensal = max(0, int(dados.mensal or 0)) * 100
    itens_setup = sum(max(0, int(i["setup"] or 0)) for i in itens) * 100
    itens_mensal = sum(max(0, int(i["mensal"] or 0)) for i in itens) * 100
    tot = dsc.totais(
        itens,
        tipo="valor" if (dados.desconto_tipo or "") == "valor" else "pct",
        pct=max(0.0, float(dados.desconto_pct or 0)),
        valor=max(0, int(dados.desconto_valor or 0)) * 100,
        extra_setup=max(0, bruto_setup - itens_setup),
        extra_mensal=max(0, bruto_mensal - itens_mensal),
    )
    # O documento chega num campo só (a tela tem um input) e é roteado por TAMANHO:
    # 11 dígitos é CPF, 14 é CNPJ — a mesma régua que `criar_cliente` já usa pra
    # gravar em `pessoas`. Até 29/08/2026 tudo caía na coluna `cnpj`, e por isso os
    # 12 orçamentos com documento da Prime guardavam CPF num campo chamado cnpj.
    # Documento com tamanho estranho continua indo pra `cnpj`, como antes: melhor
    # guardar no lugar antigo do que descartar o que o vendedor digitou.
    _doc = "".join(ch for ch in (dados.cnpj or "") if ch.isdigit())
    _cpf_val = (dados.cnpj or "").strip() if len(_doc) == 11 else None
    _cnpj_val = None if len(_doc) == 11 else ((dados.cnpj or "").strip() or None)
    vals = (dados.cliente or None, dados.empresa or None,
            _cpf_val, _cnpj_val, dados.segmento or None,
            (dados.whatsapp or "").strip() or None, (dados.email or "").strip() or None,
            (dados.telefone or "").strip() or None, (dados.cidade or "").strip() or None,
            (dados.uf or "").strip()[:2].upper() or None, (dados.site or "").strip() or None,
            (dados.cargo or "").strip() or None, (dados.socio or "").strip() or None,
            (dados.endereco or "").strip() or None, (dados.cep or "").strip() or None,
            json.dumps(modulos), itens_json, (dados.escopo or "").strip() or None,
            (dados.canal or "").strip() or None, modo, evento_json, parcelas_json,
            bruto_setup, bruto_mensal,
            tot["total"], int(dados.n_modulos),
            "valor" if (dados.desconto_tipo or "") == "valor" else "pct",
            max(0.0, min(100.0, float(dados.desconto_pct or 0))),
            max(0, int(dados.desconto_valor or 0)) * 100)
    pool = get_pool()
    reabriu = None
    # CONTRATO ASSINADO NÃO SE EDITA POR BAIXO. Documento congelado, com aceite e
    # IP do cliente — mudar os itens e valores do orçamento de origem faria o
    # assinado dizer uma coisa e o sistema outra, que é o mesmo tipo de divergência
    # que o contrato no sistema nasceu pra matar. Mesma regra do `fechado`, e pelo
    # mesmo motivo. Precisou mudar? É aditivo (contrato novo).
    if dados.id:
        try:
            if ctr.assinado_do_orcamento(pool, conta[0], int(dados.id)):
                return JSONResponse(
                    {"erro": "esta proposta tem contrato assinado e não pode ser editada — "
                             "faça um aditivo"}, status_code=409)
        except Exception:  # noqa: BLE001 — base sem a 164: segue o fluxo de antes
            logging.getLogger("servicos.salvar").info(
                "não deu pra checar contrato assinado do orçamento %s", dados.id)
    with pool.connection() as c:
        _garantir_tabela(c)
        oid = tok = None
        if dados.id:
            # ESTADO ANTES da edição: o UPDATE abaixo reverte 'aprovada' -> 'enviado',
            # e depois já não dá pra saber que houve reabertura. É esse fato que
            # decide o que fazer com a data que o cliente tinha reservado.
            antes = c.execute(
                """select coalesce(status,''), evento_agenda_id, sinal_pago_em
                     from orcamentos where id=%s and conta_id=%s""",
                (int(dados.id), conta[0])).fetchone()
            if antes and antes[0] == "aprovada" and antes[1]:
                reabriu = {"evento_agenda_id": antes[1], "sinal_pago_em": antes[2]}
            # atualiza a proposta reaberta (nunca mexe em uma já 'fechado')
            r = _com_retry_numero(c, lambda: c.execute(
                """update orcamentos set cliente=%s, empresa=%s, cpf=%s, cnpj=%s, segmento=%s,
                       whatsapp=%s, email=%s, telefone=%s, cidade=%s, uf=%s, site=%s,
                       cargo=%s, socio=%s, endereco=%s, cep=%s,
                       modulos=%s::jsonb, itens=%s::jsonb, escopo=%s, canal=%s,
                       modo=%s, evento=%s::jsonb, parcelas=%s::jsonb,
                       setup_centavos=%s, mensal_centavos=%s, primeiro_ano_centavos=%s,
                       n_modulos=%s,
                       desconto_tipo=%s, desconto_pct=%s, desconto_centavos=%s,
                       atualizado_em=now(),
                       token=coalesce(token, %s),
                       -- proposta criada fora do painel (cockpit, prospecção,
                       -- agente) entra sem número: ganha o dela agora.
                       numero=coalesce(numero,
                           (select coalesce(max(numero),0)+1 from orcamentos o2
                             where o2.conta_id=%s)),
                       -- editar uma proposta JÁ assinada a reabre: volta pra 'enviado'
                       -- e limpa a assinatura (os termos mudaram → precisa re-aprovar).
                       status=case when status='aprovada' then 'enviado' else status end,
                       aprovada_por=case when status='aprovada' then null else aprovada_por end,
                       aprovada_em=case when status='aprovada' then null else aprovada_em end,
                       aprovada_doc=case when status='aprovada' then null else aprovada_doc end,
                       aprovada_ip=case when status='aprovada' then null else aprovada_ip end
                     where id=%s and conta_id=%s and status <> 'fechado'
                   returning id, token""",
                vals + (secrets.token_urlsafe(16), conta[0], dados.id, conta[0])).fetchone())
            if r:
                oid, tok = r
        if oid is None and not dados.id:
            membro_id, _papel = _ator(request)
            criador = str(membro_id) if membro_id else "dono"
            # numero: sequencial POR CONTA, calculado no próprio INSERT. O índice
            # único (conta_id, numero) é quem garante a série — se dois salvarem
            # ao mesmo tempo, o perdedor tenta de novo e pega o próximo.
            sql_ins = """insert into orcamentos
                   (conta_id, cliente, empresa, cpf, cnpj, segmento, whatsapp, email,
                    telefone, cidade, uf, site, cargo, socio, endereco, cep,
                    modulos, itens, escopo, canal, modo, evento, parcelas,
                    setup_centavos, mensal_centavos,
                    primeiro_ano_centavos, n_modulos,
                    desconto_tipo, desconto_pct, desconto_centavos,
                    criado_por, token, numero)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,
                           (select coalesce(max(numero),0)+1 from orcamentos where conta_id=%s))
                   returning id, token"""
            r = _com_retry_numero(c, lambda: c.execute(
                sql_ins,
                (conta[0],) + vals + (criador, secrets.token_urlsafe(16), conta[0])).fetchone())
            if r is None:
                return JSONResponse({"erro": "não consegui numerar o orçamento; tente de novo"},
                                    status_code=409)
            oid, tok = r
        c.commit()
    if oid is None:
        return JSONResponse({"erro": "proposta não encontrada ou já fechada"}, status_code=400)
    # o cliente do orçamento entra na base de Clientes. Falhar aqui não pode
    # derrubar o orçamento, que é o que o vendedor está tentando salvar.
    cliente_id = None
    if modo == "evento":
        try:
            cliente_id = _espelhar_cliente(get_pool(), conta[0], dados)
            # o VÍNCULO é o que faz a folha reler o cadastro depois: sem ele, o
            # texto copiado aqui congelaria pra sempre e corrigir na aba
            # Clientes não mudaria nada.
            if cliente_id:
                with get_pool().connection() as c:
                    c.execute("update orcamentos set cliente_id=%s "
                              "where id=%s and conta_id=%s",
                              (cliente_id, oid, conta[0]))
                    c.commit()
        except Exception:  # noqa: BLE001
            cliente_id = None
    # CAMINHO DE VOLTA da reabertura: a assinatura foi desfeita, então a data na
    # agenda precisa acompanhar. Fora da transação de propósito — o que não pode se
    # perder é a edição; se a agenda falhar, a proposta editada continua salva e o
    # dono resolve a data pela própria Agenda.
    # O VÍNCULO COM O LEAD. Sem ele a proposta é um documento solto: o gatilho
    # `orcamento_enviado` procura o orçamento por `prospeccao.orcamento_id`, e o que
    # não está ligado a lead nenhum não tem card pra mover — por mais que o envio
    # esteja registrado. Medido na conta 34 em 19/08: 4 propostas, ZERO ligadas, e o
    # único card que chegou em Proposta foi arrastado na mão.
    #
    # `orcamento_id is null` no WHERE é a trava: amarrar aqui não pode roubar um
    # lead que já tem outra proposta. Quem já está servido continua como está, e o
    # caso vira conversa, não sobrescrita silenciosa.
    #
    # Fora da transação e tolerante pelo mesmo motivo do espelho de cliente acima: o
    # que não pode se perder é a proposta, que é o que a pessoa está tentando salvar.
    if oid and dados.lead_id:
        try:
            from finance import proposta_lead as _pl
            with pool.connection() as c:
                _pl.ligar(c, conta[0], int(dados.lead_id), oid, _ator(request)[0])
                c.commit()
        except Exception:  # noqa: BLE001 — o vínculo é organização, não o orçamento
            logging.getLogger("servicos.salvar").warning(
                "não deu pra ligar o lead %s ao orçamento %s", dados.lead_id, oid,
                exc_info=True)
    resp = {"ok": True, "id": oid, "token": tok, "cliente_id": cliente_id}
    if reabriu and oid:
        try:
            resp["reaberta"] = vendas.reabrir_proposta(
                pool, conta[0], oid, reabriu["evento_agenda_id"],
                reabriu["sinal_pago_em"], evento_dic)
        except Exception:  # noqa: BLE001
            logging.getLogger("servicos.salvar").exception(
                "reabrir_proposta falhou pro orçamento %s", oid)
    return JSONResponse(resp)


@router.get("/painel/servicos/lista")
def painel_servicos_lista(request: Request):
    # importado AQUI dentro, e não no topo: `painel_prospeccao` importa deste
    # módulo (`_garantir_tabela`), então um import de topo fecharia o ciclo.
    # A regra do número mora lá porque é lá que ela nasceu — copiar seria criar
    # uma segunda versão pra divergir depois.
    from web.painel_prospeccao import _zap_link
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
        # o prazo da pré-reserva vem da agenda, não do orçamento: quem manda na
        # data é o compromisso. Se ele já virou firme (ou foi cancelado), a
        # subconsulta devolve null e o botão "Sinal recebido" some sozinho.
        _cols = """select id, cliente, empresa, setup_centavos, mensal_centavos,
                          primeiro_ano_centavos, n_modulos, criado_em, status,
                          token, aprovada_por, aprovada_em, numero,
                          coalesce(modo,'recorrente'), sinal_centavos, sinal_pago_em,
                          parcelas,
                          (select ct.token from contratos ct
                            where ct.orcamento_id = orcamentos.id and ct.substitui_id is null
                            order by ct.id desc limit 1),
                          (select ct.numero from contratos ct
                            where ct.orcamento_id = orcamentos.id and ct.substitui_id is null
                            order by ct.id desc limit 1),
                          (select ct.assinado_em is not null from contratos ct
                            where ct.orcamento_id = orcamentos.id and ct.substitui_id is null
                            order by ct.id desc limit 1),
                          (select e.pre_reserva_ate from eventos_agenda e
                            where e.id = orcamentos.evento_agenda_id
                              and e.status = 'pre_reservado'),
                          evento,
                          (select e.status from eventos_agenda e
                            where e.id = orcamentos.evento_agenda_id),
                          -- o último ENVIO da proposta por e-mail. Sem isto, "será
                          -- que já mandei pra Carla?" só se responde abrindo o
                          -- Gmail — e na dúvida se manda duas vezes.
                          -- o APELIDO não é enfeite: sem ele a saída fica com duas
                          -- colunas chamadas `criado_em` (esta e a do orçamento) e o
                          -- `order by criado_em` de baixo vira ambíguo — a lista
                          -- inteira do funil devolvia 500.
                          (select ev.criado_em from orcamento_envios ev
                            where ev.orcamento_id = orcamentos.id and ev.ok
                            order by ev.criado_em desc limit 1) as enviado_em,
                          -- O NOME DO CADASTRO. `cliente` e `empresa` são dois
                          -- campos livres pra mesma coisa e divergiram: em 25/08,
                          -- de 26 orçamentos, 19 apareciam como "−" e 2 como
                          -- TELEFONE. Este é o único que estava certo nas 26.
                          (select cl.nome from clientes cl
                            where cl.id = orcamentos.cliente_id) as cadastro_nome,
                          coalesce(whatsapp, telefone, '') as zap,
                          -- desde quando o contrato está na mão do cliente
                          -- esperando assinatura — não desde quando foi CRIADO.
                          -- O APELIDO aqui é pela mesma razão do `enviado_em`
                          -- acima: sem ele esta coluna também se chama `enviado_em`
                          -- e derruba o `order by` de baixo com ORDER BY ambíguo.
                          (select ct.enviado_em from contratos ct
                            where ct.orcamento_id = orcamentos.id and ct.substitui_id is null
                            order by ct.id desc limit 1) as contrato_enviado_em"""
        if papel == "vendedor" and membro_id:
            rows = c.execute(
                _cols + """ from orcamentos where conta_id=%s and criado_por=%s
                   order by criado_em desc limit 50""",
                (conta[0], str(membro_id))).fetchall()
        else:
            rows = c.execute(
                _cols + """ from orcamentos where conta_id=%s
                   order by criado_em desc limit 50""", (conta[0],)).fetchall()
    # OS PAGAMENTOS DE TODAS AS LINHAS, em dois SELECTs. Por linha seriam cem
    # consultas numa lista de cinquenta — o mesmo N+1 que já custou caro na Agenda.
    ids = [r[0] for r in rows]
    pagos_por_orc, comp_por_orc = {}, {}
    if ids:
        with get_pool().connection() as c:
            try:
                for oid, idx in c.execute(
                        """select orcamento_id, parcela_idx from titulos
                            where conta_id=%s and orcamento_id = any(%s)
                              and parcela_idx is not null and status='pago'""",
                        (conta[0], ids)).fetchall():
                    pagos_por_orc.setdefault(oid, []).append(idx)
            except Exception:  # noqa: BLE001 — conta sem o módulo financeiro
                pagos_por_orc = {}
            try:
                for oid, idx in c.execute(
                        """select orcamento_id, parcela_idx from orcamento_comprovantes
                            where conta_id=%s and orcamento_id = any(%s)""",
                        (conta[0], ids)).fetchall():
                    comp_por_orc.setdefault(oid, []).append(idx)
            except Exception:  # noqa: BLE001
                comp_por_orc = {}

    itens = [{
        "id": r[0],
        # `cliente`/`empresa` continuam indo crus porque o editor e os diálogos de
        # confirmação ainda os usam. Quem manda na LINHA é o titulo/sub de baixo.
        "cliente": r[1] or "-",
        "empresa": r[2] or "",
        "setup": brl(r[3]),
        "mensal": brl(r[4]),
        "total": brl(r[5]),
        "mods": r[6],
        # COM ANO E COM RÓTULO. Era um "12/08" solto no meio de nº, itens e valor:
        # ninguém lia aquilo como "quando isto foi gerado", e proposta tem validade
        # na cabeça de quem vende. Sem o ano, um orçamento do ano passado passava
        # por deste mês.
        "data": r[7].strftime("%d/%m/%Y") if r[7] else "",
        "status": r[8] or "rascunho",
        "token": r[9] or "",
        "aprovada_por": r[10] or "",
        "aprovada_em": r[11].strftime("%d/%m/%Y") if r[11] else "",
        "numero": r[12], "modo": r[13] or "recorrente",
        "inicial": (r[24] or r[2] or r[1] or "?").strip()[:1].upper(),
        "sinal": brl(r[14]) if r[14] else "",
        "sinal_pago": bool(r[15]),
        # o quanto o plano de pagamento DIVERGE do total, em centavos (0 = fecha).
        # Vem do servidor porque o botão "Fechar contrato" age numa linha do funil,
        # não no orçamento aberto no editor — e é ele que vira título a receber.
        "plano_difere": _dif_plano(r[16], r[5], r[3]),
        # o CONTRATO é documento próprio, com link próprio. Aparece na linha do
        # funil assim que nasce (sinal confirmado) — é daqui que o dono manda o
        # link pro cliente, do mesmo jeito que já manda o da proposta.
        "contrato_token": r[17] or "",
        "contrato_numero": r[18],
        "contrato_assinado": bool(r[19]),
        # só vem preenchido enquanto a data está SEGURADA esperando o sinal
        "pre_reserva_ate": r[20].astimezone(ag.BRT).strftime("%d/%m %H:%M") if r[20] else "",
        # O ESTADO DA DATA, resolvido no servidor. A linha do funil mostrava só a
        # pré-reserva correndo — "data firme", "nunca entrou" e "liberada" tinham a
        # mesma cara, e duas delas são problema. Ver vendas.estado_da_data.
        #
        # CHAMA-SE `data_estado`, NÃO `data`. Nasceu como "data" e colidiu com a
        # chave logo acima — dicionário Python fica com a ÚLTIMA, então a data de
        # geração sumiu da linha do funil no mesmo commit em que o selo apareceu,
        # sem quebrar teste nenhum. São duas coisas diferentes e agora têm dois
        # nomes diferentes.
        "data_estado": vendas.estado_da_data(
            status=r[8], modo=r[13] or "recorrente", evento=r[21],
            evento_status=r[22], pre_reserva_ate=r[20]),
        "enviado_em": r[23].astimezone(ag.BRT).strftime("%d/%m %H:%M") if r[23] else "",
        # os três abaixo são MATÉRIA-PRIMA do servidor: viram titulo/sub e a faixa
        # do contrato logo depois, e os que começam com _ saem do dict antes do JSON.
        "_cadastro": r[24] or "",
        # o LINK, não o número: `_zap_link` sabe que 86981885930 sem DDI vira
        # China no wa.me e que celular antigo de 10 dígitos precisa do 9. Reescrever
        # isso aqui em JS (`replace(/\D/g,'')`) dava um atalho que abre no vazio.
        "zap_link": _zap_link(r[25] or ""),
        "_contrato_enviado_em": r[26],
        "evento": r[21] if isinstance(r[21], dict) else None,
        # quanto já entrou, e quanto disso está sem comprovante
        "pgto": vendas.resumo_pagamentos(
            r[16], r[15], pagos_por_orc.get(r[0], ()), comp_por_orc.get(r[0], ())),
    } for r in rows]
    # UMA vez por requisição, não por linha: o nicho é da conta, e `tem_contrato`
    # abre o banco pra descobrir. Dentro do laço seriam cinquenta consultas iguais
    # — o mesmo N+1 que já custou caro na Agenda.
    from finance import contrato as _ctr
    try:
        # `exige_assinatura` é a porta que já existe pra esta pergunta — e é a MESMA
        # que decide o modo do orçamento. Uma regra nova e paralela poderia divergir,
        # e aí a linha ofereceria "Fechar negócio" numa conta que precisa de papel.
        _nicho_tem_contrato = _ctr.exige_assinatura(get_pool(), conta[0])
    except Exception:  # noqa: BLE001
        # na dúvida, o comportamento de antes: assume que o contrato existe e não
        # oferece "Fechar negócio" sozinho. Errar pra menos aqui só deixa a linha
        # como estava; errar pra mais oferece fechar negócio onde falta papel.
        _nicho_tem_contrato = True
    # O QUE A LINHA DIZ — selos de pendência, a ação principal e o resumo do que
    # já foi. Montado aqui, depois de `itens`, porque lê o que acabou de ser
    # calculado (o estado da data, os pagamentos) em vez de recalcular.
    for it in itens:
        it["painel"] = vendas.linha_do_funil(
            status=it["status"], data_estado=it["data_estado"],
            sinal=it["sinal"], sinal_pago=it["sinal_pago"], pagamentos=it["pgto"],
            enviado_em=it["enviado_em"], contrato_numero=it["contrato_numero"],
            contrato_assinado=it["contrato_assinado"],
            plano_difere=it["plano_difere"], aprovada_por=it["aprovada_por"],
            nunca_enviada=not it["enviado_em"],
            contrato_enviado_em=it["_contrato_enviado_em"], tem_contrato=_nicho_tem_contrato)
        # o NOME, resolvido no servidor pela mesma função pura que os testes cobrem.
        # A tela não decide mais quem é o cliente desta linha.
        it.update(vendas.titulo_do_funil(
            cadastro=it["_cadastro"], empresa=it["empresa"], cliente=it["cliente"],
            modo=it["modo"], evento=it.get("evento"), numero=it["numero"]))
        for _k in ("_cadastro", "_contrato_enviado_em"):
            it.pop(_k, None)
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
                      coalesce(modo,'recorrente'),
                      desconto_tipo, desconto_pct, desconto_centavos, criado_em
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
        # o desconto volta pro editor: reabrir a proposta pra trocar uma vírgula
        # não pode zerar em silêncio o que foi negociado.
        "desconto_tipo": r[27] or "pct",
        "desconto_pct": float(r[28] or 0),
        "desconto_valor": round(int(r[29] or 0) / 100),
        # QUANDO ESTA PROPOSTA FOI GERADA. A folha do cliente sempre disse
        # ("Emitido em"); quem vende, não — nem no funil nem aqui no editor. E é
        # quem vende que precisa saber se aquilo ainda está de pé.
        "gerado_em": r[30].strftime("%d/%m/%Y") if r[30] else "",
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


class SinalIn(BaseModel):
    id: int


@router.post("/painel/servicos/sinal-recebido")
def painel_servicos_sinal_recebido(request: Request, dados: SinalIn):
    """O sinal caiu: a data segurada vira compromisso firme e o título daquela
    parcela recebe baixa, na data em que o sinal caiu.

    A regra inteira mora em finance.vendas.confirmar_sinal — a Agenda tem um botão
    igual a este na caixa do dia, e duas cópias da regra viraria dois
    comportamentos. Aqui fica só o gate da conta e a resposta pro navegador.
    """
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    with pool.connection() as c:
        _garantir_tabela(c)
    r = vendas.confirmar_sinal(pool, conta[0], int(dados.id))
    if not r.get("ok"):
        return JSONResponse({"erro": r.get("erro", "falha ao confirmar")}, status_code=404)
    return JSONResponse(r)


# ============================================== MANDAR A PROPOSTA POR E-MAIL
#
# O funil sabia gerar o link e abrir o PDF; mandar era por fora, na mão. E o
# vendedor não tinha como saber se já tinha mandado — na dúvida, mandava de novo.
#
# Por qual caixa o e-mail sai é decisão de fundo e mora em finance/proposta_email:
# a proposta é mensagem PRA CLIENTE, e ele vai apertar Responder.


def _dados_do_envio(pool, conta_id: int, orc_id: int) -> dict | None:
    """O orçamento, do jeito que a tela de envio precisa. None se não é da conta."""
    with pool.connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select coalesce(cliente,''), coalesce(empresa,''), coalesce(email,''),
                      numero, coalesce(modo,'recorrente'), token, evento,
                      coalesce(primeiro_ano_centavos, setup_centavos, 0), criado_em
                 from orcamentos where id=%s and conta_id=%s""",
            (orc_id, conta_id)).fetchone()
        if not r:
            return None
        token = r[5]
        if not token:
            # proposta antiga, de antes de o token existir. A listagem do funil já
            # preenche na passagem; gerar aqui também é o mesmo UPDATE, e evita que
            # o botão dependa de a pessoa ter carregado a lista antes.
            token = c.execute(
                """update orcamentos set token = substr(md5(random()::text || id::text
                     || clock_timestamp()::text), 1, 22)
                   where id=%s and conta_id=%s returning token""",
                (orc_id, conta_id)).fetchone()[0]
            c.commit()
    return {"cliente": r[0], "empresa_cli": r[1], "email": r[2], "numero": r[3],
            "modo": r[4], "token": token, "evento": r[6] or {}, "total": r[7],
            "criado_em": r[8]}


def _resumo_do_envio(d: dict) -> str:
    """A linha discreta embaixo do botão do e-mail: do que se trata, sem abrir.

    A DATA EM QUE FOI GERADO entra aqui de propósito. Proposta tem validade na
    cabeça de quem recebe, e um cliente que acha o e-mail duas semanas depois
    precisa saber se aquilo ainda é de hoje — sem ter que perguntar."""
    ev = d.get("evento") or {}
    partes = []
    if ev.get("tipo"):
        partes.append(str(ev["tipo"]))
    if ev.get("data"):
        partes.append(ctr.data_br(ev["data"]))
    if d.get("total"):
        partes.append(brl(d["total"]))
    if d.get("criado_em"):
        partes.append(f"gerado em {d['criado_em'].strftime('%d/%m/%Y')}")
    return " · ".join(partes)


@router.get("/painel/servicos/email/{orc_id}")
def painel_servicos_email(request: Request, orc_id: int, alvo: str = "proposta"):
    """O que a tela de envio abre preenchido — e por qual caixa vai sair.

    O remetente é resolvido AQUI, antes de mandar, porque o mesmo botão se
    comporta diferente em duas empresas: a que tem caixa configurada e a que não
    tem. Sem dizer, o vendedor descobriria pelo cliente reclamando que respondeu e
    ninguém viu."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    d = _dados_do_envio(pool, conta[0], int(orc_id))
    if not d:
        return JSONResponse({"erro": "orçamento não encontrado"}, status_code=404)
    dados_emp = emp.obter_dados_empresa(pool, conta[0]) or {}
    nome_emp = (dados_emp.get("nome_fantasia") or dados_emp.get("razao_social")
                or conta[2] or "")
    rem = pmail.remetente(pool, conta[0], dados_emp.get("email_empresa") or "")
    envios = pmail.historico(pool, conta[0], int(orc_id))
    _base = f"{request.base_url.scheme}://{request.base_url.netloc}"
    _quem = d["cliente"] or d["empresa_cli"]
    _assunto = pmail.assunto_padrao(d["numero"], nome_emp, d["modo"])
    _msg = pmail.texto_padrao(_quem, d["modo"])
    _link = f"{_base}/proposta/{d['token']}"
    # MESMO CAMINHO, outro documento. O contrato tinha link e não tinha envio: o
    # selo mandava "mande o link pro cliente" e o link ficava escondido no menu de
    # três pontos. Aqui ele reusa a tela de envio, o remetente resolvido e o
    # registro em `orcamento_envios` — nada de fluxo paralelo pra manter.
    if (alvo or "") == "contrato":
        from finance import contrato as _ctr
        _ct = _ctr.por_orcamento(pool, conta[0], int(orc_id))
        if not _ct or not _ct.get("token"):
            return JSONResponse({"erro": "esta proposta ainda não tem contrato"},
                                status_code=404)
        _link = f"{_base}/contrato/{_ct['token']}"
        _assunto = f"Contrato nº {_ct.get('numero') or ''} — {nome_emp}".strip()
        _msg = (f"Olá{(', ' + _quem) if _quem else ''}! Segue o contrato para "
                "leitura e assinatura. É só abrir o link e assinar por lá — "
                "qualquer dúvida, me chame.")
    return JSONResponse({
        "para": d["email"],
        "cliente": d["cliente"] or d["empresa_cli"],
        "assunto": _assunto, "mensagem": _msg, "link": _link,
        "resumo": _resumo_do_envio(d),
        "empresa": nome_emp,
        "remetente": rem,
        "envios": [{"quando": e["quando"].strftime("%d/%m %H:%M"), "ok": e["ok"],
                    "destino": e["destino"]} for e in envios],
    })


class EnviarEmailIn(BaseModel):
    id: int
    para: str = ""
    assunto: str = ""
    mensagem: str = ""
    alvo: str = "proposta"


@router.post("/painel/servicos/proposta/{orc_id}/link-copiado")
def painel_servicos_link_copiado(request: Request, orc_id: int):
    """Anota que alguém pegou o link desta proposta pra mandar pro cliente.

    POR QUE ISSO É UM ENVIO. O funil precisa saber que a proposta saiu, e no
    desktop o caminho mais usado não é o e-mail: é copiar o link e colar onde o
    cliente estiver. Sem esta anotação, quem manda assim fica com o card parado em
    "Novo" pra sempre — e o dono arrasta na mão, que é justamente o que a régua
    existe pra evitar.

    E POR QUE ELE SE CHAMA "LINK COPIADO", E NÃO "ENVIADO". No e-mail e na conversa
    do WhatsApp o Zaq entregou; aqui ele só sabe que o link saiu da tela. A tela
    escreve a diferença, em vez de prometer uma certeza que não tem.

    Best-effort: devolve ok mesmo quando não deu pra anotar. Um erro aqui não pode
    fazer a tela dizer que o link não foi copiado — ele foi, o navegador já copiou.
    """
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "nao autorizado"}, status_code=403)
    try:
        from finance import proposta_email as _pe
        _pe.registrar(get_pool(), conta[0], int(orc_id), destino="", remetente_usado="",
                      ok=True, canal="link", por=str(_ator(request)[0] or ""))
    except Exception:  # noqa: BLE001 — anotação não é o trabalho da tela
        logging.getLogger("servicos.envio").warning(
            "proposta %s: não registrei o link copiado", orc_id, exc_info=True)
    return JSONResponse({"ok": True})


@router.post("/painel/servicos/enviar-email")
def painel_servicos_enviar_email(request: Request, dados: EnviarEmailIn):
    """Manda, registra o que aconteceu e devolve o link como plano B.

    QUEM PODE: o mesmo gate da aba (dono, gestor e vendedor). É o vendedor que
    fala com o cliente e a proposta é dele — travar isso mandaria ele pedir pro
    dono apertar um botão.

    O E-MAIL DIGITADO FICA SALVO no orçamento quando ele não tinha nenhum. Sem
    isso, a mesma pessoa redigitaria o endereço a cada envio, e o orçamento
    seguiria sem o dado que o contrato depois vai precisar.

    `alvo="contrato"` é o "Mandar pra assinar": manda o LINK DO CONTRATO, não o
    da proposta. Antes deste campo existir, a tela de pré-visualização (rota
    GET, `?alvo=contrato`) já mostrava o assunto/mensagem certos — mas o envio
    de verdade ignorava isso e mandava sempre o link da proposta com o botão
    "Ver a proposta". O cliente recebia um e-mail com jeito de contrato e um
    link que não assinava nada; o contrato nunca ficava assinado_em preenchido,
    e o botão "Mandar pra assinar" continuava aparecendo pra sempre — não por
    falta de lembrar que já mandou, mas porque na prática nunca chegou link de
    assinatura nenhum (relato de produção, conta Prime Eventos/Bianca, 28/08)."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    d = _dados_do_envio(pool, conta[0], int(dados.id))
    if not d:
        # o 404 vem ANTES da checagem do e-mail: orçamento de outra conta não pode
        # se denunciar respondendo "confira o e-mail" em vez de "não existe".
        return JSONResponse({"erro": "orçamento não encontrado"}, status_code=404)
    # sem `para` no corpo vale o que está gravado no orçamento — é o caso de quem
    # abriu a tela e apertou Enviar sem tocar em nada.
    para = (dados.para or "").strip() or d["email"]
    if "@" not in para or "." not in para.split("@")[-1]:
        return JSONResponse({"erro": "Confira o e-mail do cliente."}, status_code=400)
    dados_emp = emp.obter_dados_empresa(pool, conta[0]) or {}
    nome_emp = (dados_emp.get("nome_fantasia") or dados_emp.get("razao_social")
                or conta[2] or "")
    quem = d["cliente"] or d["empresa_cli"]
    _base = f"{request.base_url.scheme}://{request.base_url.netloc}"
    doc_rotulo = None
    # MESMO RAMO da rota GET (linha ~1215) — tem que dar o mesmo link/assunto/
    # mensagem que a pré-visualização já mostrou, senão o que sai no e-mail
    # trai o que a tela prometeu.
    if (dados.alvo or "") == "contrato":
        from finance import contrato as _ctr
        _ct = _ctr.por_orcamento(pool, conta[0], int(dados.id))
        if not _ct or not _ct.get("token"):
            return JSONResponse({"erro": "esta proposta ainda não tem contrato"},
                                status_code=404)
        link = f"{_base}/contrato/{_ct['token']}"
        assunto_padrao_ = f"Contrato nº {_ct.get('numero') or ''} — {nome_emp}".strip()
        mensagem_padrao_ = (f"Olá{(', ' + quem) if quem else ''}! Segue o contrato para "
                           "leitura e assinatura. É só abrir o link e assinar por lá — "
                           "qualquer dúvida, me chame.")
        doc_rotulo = "contrato"
    else:
        link = f"{_base}/proposta/{d['token']}"
        assunto_padrao_ = pmail.assunto_padrao(d["numero"], nome_emp, d["modo"])
        mensagem_padrao_ = pmail.texto_padrao(quem, d["modo"])
    assunto = (dados.assunto or "").strip() or assunto_padrao_
    mensagem = (dados.mensagem or "").strip() or mensagem_padrao_
    html, texto = pmail.montar(
        mensagem=mensagem, link=link, numero=d["numero"], empresa=nome_emp,
        telefone=dados_emp.get("telefone") or "",
        email_empresa=dados_emp.get("email_empresa") or "",
        resumo=_resumo_do_envio(d), modo=d["modo"], doc_rotulo=doc_rotulo)
    r = pmail.enviar(pool, conta[0], destino=para, assunto=assunto, html=html,
                     texto=texto, empresa=nome_emp,
                     reply_to=dados_emp.get("email_empresa") or "")
    membro_id, _papel = _ator(request)
    pmail.registrar(pool, conta[0], int(dados.id), destino=para,
                    remetente_usado=r["remetente"], ok=r["ok"], erro=r["erro"],
                    por=str(membro_id or "dono"))
    if not d["email"]:
        with pool.connection() as c:
            c.execute("update orcamentos set email=%s where id=%s and conta_id=%s "
                      "and coalesce(email,'')=''", (para, int(dados.id), conta[0]))
            c.commit()
    if not r["ok"]:
        # o link vai junto do erro: o vendedor tem um cliente esperando, e mandar
        # pelo WhatsApp resolve o dia dele enquanto a caixa se conserta.
        return JSONResponse({"erro": r["erro"], "link": link}, status_code=502)
    if doc_rotulo == "contrato":
        # É AQUI que a linha do funil aprende que o contrato saiu de casa: sem
        # isto o card ficava com o botão "Mandar pra assinar" pra sempre, porque
        # nada distinguia "pronto" de "já na mão do cliente" (relato de produção,
        # conta Prime Eventos/Bianca, 28/08).
        with pool.connection() as c:
            c.execute("update contratos set enviado_em=now() where id=%s",
                     (_ct["id"],))
            c.commit()
    return JSONResponse({"ok": True, "remetente": r["remetente"], "para": para})


# ==================================== COMPROVANTE DE PAGAMENTO (sinal e parcelas)
#
# O comprovante é da PARCELA, não do orçamento — um orçamento tem o sinal e mais N.
# A chave é `parcela_idx`, a mesma que os títulos usam.
#
# QUEM FAZ O QUÊ, e é de propósito que sejam gates diferentes:
#   ver a lista e o arquivo   dono, gestor e VENDEDOR — é ele que cobra o cliente,
#                             e cobrar sem saber o que já entrou é ligar no escuro
#   anexar                    só dono e gestor. Papel de dinheiro é do financeiro.
#
# `vendas` + `financeiro` dá exatamente dono e gestor: o papel `financeiro` puro
# não passa no gate da aba, e o vendedor não tem `financeiro`.


def _conta_financeiro(request: Request):
    """Gate de quem MEXE em dinheiro dentro da aba: dono e gestor."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return None, JSONResponse({"erro": "nao autorizado"}, status_code=403)
    from contas import equipe as _equipe
    if not _equipe.caps_do_papel(request.session.get("papel", "dono"))["financeiro"]:
        return None, JSONResponse(
            {"erro": "só o dono e o gestor anexam comprovante"}, status_code=403)
    return conta, None


@router.get("/painel/servicos/pagamentos/{orc_id}")
def painel_servicos_pagamentos(request: Request, orc_id: int):
    """O sinal e as parcelas do orçamento, com o que já foi pago e o que tem
    comprovante. Aberto pra quem vende — inclusive o vendedor."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    with pool.connection() as c:
        _garantir_tabela(c)
    d = vendas.pagamentos_do_orcamento(pool, conta[0], int(orc_id))
    if not d:
        return JSONResponse({"erro": "orçamento não encontrado"}, status_code=404)
    from contas import equipe as _equipe
    pode = _equipe.caps_do_papel(request.session.get("papel", "dono"))["financeiro"]
    anexos = comprov.por_orcamento(pool, conta[0], int(orc_id))
    linhas = []
    for p in d["parcelas"]:
        a = anexos.get(p["idx"])
        linhas.append({
            "idx": p["idx"], "rotulo": p["rotulo"], "valor": brl(p["valor_centavos"]),
            "venc": ctr.data_br(p["venc"]) if p["venc"] else "",
            "forma": p["forma"], "pago": p["pago"],
            "pago_em": p["pago_em"].strftime("%d/%m/%Y") if p["pago_em"] else "",
            "vence_hoje": p["vence_hoje"],
            "comprovante_id": (a or {}).get("id"),
            "comprovante_nome": (a or {}).get("nome") or "",
        })
    return JSONResponse({
        "parcelas": linhas, "total": brl(d["total"]), "recebido": brl(d["recebido"]),
        "falta": brl(d["falta"]),
        # a tela só oferece o botão quando ele tem pra onde mandar o arquivo —
        # botão que engole comprovante é pior que botão nenhum
        "pode_anexar": bool(pode and comprov.configurado()),
        "sem_storage": not comprov.configurado(),
    })


@router.post("/painel/servicos/comprovante")
async def painel_servicos_comprovante_subir(
        request: Request, orcamento_id: int = Form(...), parcela_idx: int = Form(...),
        arquivo: UploadFile = File(...)):
    """Anexa (ou substitui) o comprovante de uma parcela."""
    conta, redir = _conta_financeiro(request)
    if redir is not None:
        return redir
    pool = get_pool()
    d = vendas.pagamentos_do_orcamento(pool, conta[0], int(orcamento_id))
    if not d:
        return JSONResponse({"erro": "orçamento não encontrado"}, status_code=404)
    if not any(p["idx"] == int(parcela_idx) for p in d["parcelas"]):
        # parcela inventada na URL não pode criar linha órfã: o comprovante ficaria
        # invisível na tela e ninguém saberia que subiu.
        return JSONResponse({"erro": "essa parcela não existe no plano"}, status_code=400)
    conteudo = await arquivo.read()
    try:
        caminho = comprov.subir(conteudo, arquivo.content_type or "",
                                conta_id=conta[0], orcamento_id=int(orcamento_id),
                                parcela_idx=int(parcela_idx))
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    membro_id, _papel = _ator(request)
    r = comprov.registrar(pool, conta[0], int(orcamento_id), int(parcela_idx),
                          caminho=caminho, nome=arquivo.filename or "",
                          tipo=arquivo.content_type or "", bytes_=len(conteudo),
                          por=str(membro_id or "dono"))
    return JSONResponse({"ok": True, "id": r["id"], "trocou": r["trocou"]})


@router.get("/painel/servicos/comprovante/{comprovante_id}")
def painel_servicos_comprovante_ver(request: Request, comprovante_id: int):
    """Entrega o arquivo. É ESTA ROTA que faz o bucket poder ser privado.

    O `conta_id` no WHERE é o que impede uma empresa de ler o comprovante de outra
    trocando o número na URL. E vai sem cache: documento de dinheiro não fica
    guardado no navegador de quem usou o painel num computador emprestado."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    d = comprov.obter(get_pool(), conta[0], int(comprovante_id))
    if not d:
        return JSONResponse({"erro": "não encontrado"}, status_code=404)
    try:
        conteudo, tipo = comprov.ler(d["caminho"])
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=502)
    nome = (d["nome"] or "comprovante").replace('"', "")
    return Response(conteudo, media_type=d["tipo"] or tipo, headers={
        "Content-Disposition": f'inline; filename="{nome}"',
        "Cache-Control": "no-store",
    })


class MarcarDataIn(BaseModel):
    id: int


@router.post("/painel/servicos/marcar-data")
def painel_servicos_marcar_data(request: Request, dados: MarcarDataIn):
    """Põe na agenda a data de um orçamento aprovado que ficou de fora.

    É o conserto dos dois estados ruins da linha do funil:
      • FORA DA AGENDA — a aprovação nunca virou compromisso (orçamento sem hora
        de início, erro engolido, processo reiniciado antes da tarefa rodar);
      • DATA LIBERADA — o prazo do sinal venceu e o compromisso foi cancelado; o
        cliente reapareceu e a empresa quer segurar de novo.

    Usa a MESMA função da aprovação (proposta._reservar_na_agenda) de propósito.
    Uma segunda rotina de "criar o compromisso" seria uma segunda regra: prazo
    diferente, título diferente, conflito não avisado. Ela já é idempotente —
    clicar duas vezes não cria dois compromissos.

    O compromisso CANCELADO não é ressuscitado: fica como histórico e o orçamento
    solta o vínculo pra ganhar um novo. Reviver o antigo apagaria o registro de
    que a data chegou a vencer.
    """
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    with pool.connection() as c:
        # o token é a chave de leitura do orçamento (a mesma que o cliente usa).
        # Propostas antigas nasceram sem ele; a listagem do funil preenche na
        # passagem, mas depender disso deixaria esta rota quebrada pra quem chegar
        # por outro caminho. Gerar aqui é o mesmo UPDATE, e é idempotente.
        c.execute(
            """update orcamentos set token = substr(md5(random()::text || id::text
                 || clock_timestamp()::text), 1, 22)
               where id=%s and conta_id=%s and token is null""", (int(dados.id), conta[0]))
        c.commit()
        r = c.execute(
            """select o.token, coalesce(o.status,''), coalesce(o.modo,'recorrente'),
                      (select e.status from eventos_agenda e where e.id=o.evento_agenda_id)
                 from orcamentos o where o.id=%s and o.conta_id=%s""",
            (int(dados.id), conta[0])).fetchone()
        if not r:
            return JSONResponse({"erro": "orçamento não encontrado"}, status_code=404)
        token, status, modo, ev_status = r
        if modo != "evento" or status not in ("aprovada", "fechado"):
            return JSONResponse(
                {"erro": "só orçamento de evento já aprovado reserva data"}, status_code=400)
        if ev_status in ("ativo", "pre_reservado"):
            return JSONResponse({"erro": "essa data já está na agenda"}, status_code=409)
        if ev_status == "cancelado":
            c.execute("update orcamentos set evento_agenda_id=null, sinal_centavos=null "
                      "where id=%s and conta_id=%s", (int(dados.id), conta[0]))
            c.commit()

    from web import proposta as prop
    d = prop._carregar(token, pool=pool)
    if not d:
        return JSONResponse({"erro": "não consegui ler o orçamento"}, status_code=404)
    novo_id = prop._reservar_na_agenda(d, pool=pool)
    if not novo_id:
        # o motivo mais comum tem conserto, e dizer "falhou" mandaria o dono
        # procurar no escuro justamente o campo que a linha do funil já apontou.
        falta_hora = not ((d.get("evento") or {}).get("inicio") or "").strip()
        return JSONResponse(
            {"erro": ("Falta a hora de início do evento — preencha em “O evento” e "
                      "marque de novo." if falta_hora else
                      "Não consegui marcar. Confira a data e a hora do evento.")},
            status_code=400)
    return JSONResponse({"ok": True, "evento_id": novo_id})


def _conta_evento(request: Request):
    """Gate do CONTRATO: além do gate da aba, a conta precisa ser de eventos.

    A trava vive aqui e não só no template porque a rota é POST e o navegador não
    é fonte confiável: esconder o card não impede ninguém de chamar a URL. E quem
    decide é `contrato.tem_contrato`, que delega pra mesma porta do modo do
    orçamento — uma regra nova aqui poderia divergir dela, e aí a conta emitiria
    orçamento de evento com contrato de serviço, ou o contrário."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return None, JSONResponse({"erro": "nao autorizado"}, status_code=403)
    # SÓ O DONO. Esconder o card no template não tranca nada: estas rotas são
    # POST e a URL é chamável direto. O vendedor abre a mesma aba (tem `vendas`),
    # então sem esta linha ele editaria as cláusulas que a empresa assina.
    from contas import equipe as _equipe
    if not _equipe.caps_do_papel(request.session.get("papel", "dono"))["gerir"]:
        return None, JSONResponse(
            {"erro": "só o dono da empresa configura o contrato"}, status_code=403)
    nicho = (emp.obter_dados_empresa(get_pool(), conta[0]) or {}).get("nicho")
    if not ctr.tem_contrato(nicho):
        return None, JSONResponse({"erro": "contrato de locação é do nicho de eventos"},
                                  status_code=404)
    return conta, None


def _contexto_de_exemplo(pool, conta_id: int):
    """(contexto, rótulo) montado com um orçamento REAL da conta, ou (None, "").

    O mais recente que tenha data de evento. Serve a duas coisas — a prévia da
    tela e o aviso de campo sem valor no card recolhido — e é o mesmo de
    propósito: se as duas usassem bases diferentes, uma diria que está tudo certo
    enquanto a outra apontava falta.

    `regras` vem de fora porque a prévia precisa refletir o que está NO
    FORMULÁRIO, não o que está gravado — é assim que o dono experimenta uma multa
    diferente antes de salvar."""
    with pool.connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select cliente, cnpj, whatsapp, setup_centavos, numero, evento
                 from orcamentos
                where conta_id=%s and coalesce(evento->>'data','') <> ''
                order by id desc limit 1""", (conta_id,)).fetchone()
    if not r:
        return None, ""
    orcamento = {"cliente": r[0], "cnpj": r[1], "whatsapp": r[2],
                 "setup_centavos": r[3], "numero": r[4], "evento": r[5] or {}}
    return orcamento, f"orçamento nº {r[4] or '—'} · {r[0] or ''}"


@router.get("/painel/servicos/contrato")
def painel_servicos_contrato(request: Request, padrao: int = 0):
    """O modelo da conta + a paleta de campos que a tela oferece.

    `padrao=1` devolve o modelo genérico SEM tocar no que está salvo: é o botão
    "restaurar" da tela, e ele só troca o texto na tela. O que estava gravado só
    morre quando o dono clicar em salvar — senão um clique curioso apagaria o
    contrato da empresa sem chance de desistir."""
    conta, erro = _conta_evento(request)
    if erro is not None:
        return erro
    pool = get_pool()
    modelo = ({"clausulas": ctr.modelo_padrao(), "regras": dict(ctr.REGRAS_PADRAO), "novo": True,
               "atualizado_em": None, "atualizado_por": ""}
              if padrao else ctr.carregar_modelo(pool, conta[0]))
    catalogo = scat.listar(pool, conta[0])
    # O QUE AJUSTAR no resumo do card fechado. Custa uma consulta a mais por
    # carregamento e vale: um campo sem valor não aparece em lugar nenhum até
    # sair no contrato DO CLIENTE — é o único erro deste fluxo que estreia na
    # frente dele. Melhor o dono ver com o card recolhido.
    orcamento, exemplo = _contexto_de_exemplo(pool, conta[0])
    diag = {"ajustes": [], "da_proposta": []}
    if orcamento and not modelo["novo"]:
        ctx = ctr.contexto(catalogo=catalogo, orcamento=orcamento, modelo=modelo,
                           empresa=emp.obter_dados_empresa(pool, conta[0]))
        _doc, faltas = ctr.montar(modelo["clausulas"], ctx)
        diag = ctr.diagnostico(faltas, ctx, catalogo)
    return JSONResponse({
        "clausulas": modelo["clausulas"], "regras": modelo["regras"],
        "novo": modelo["novo"], "campos": ctr.campos_disponiveis(catalogo),
        "resumo": {
            "n": len(modelo["clausulas"]),
            "em": modelo["atualizado_em"].strftime("%d/%m") if modelo.get("atualizado_em") else "",
            "por": modelo.get("atualizado_por") or "",
            # só o que o DONO tem como consertar vira alarme. Campo de proposta
            # vazio no exemplo não é defeito — ver ctr.diagnostico.
            "ajustes": diag["ajustes"], "da_proposta": diag["da_proposta"],
            "exemplo": exemplo,
        },
    })


class ContratoIn(BaseModel):
    clausulas: list[dict] = []
    regras: dict = {}


@router.post("/painel/servicos/contrato/salvar")
def painel_servicos_contrato_salvar(request: Request, dados: ContratoIn):
    conta, erro = _conta_evento(request)
    if erro is not None:
        return erro
    membro_id, _papel = _ator(request)
    r = ctr.salvar_modelo(get_pool(), conta[0], dados.clausulas, dados.regras,
                          por=str(membro_id or "dono"))
    return JSONResponse(r)


@router.post("/painel/servicos/contrato/previa")
def painel_servicos_contrato_previa(request: Request, dados: ContratoIn):
    """Monta o contrato com um orçamento REAL da conta — o mais recente que tenha
    data de evento — e devolve o texto pronto mais o que ficou faltando.

    Prévia com dados inventados esconderia justamente o erro que interessa: o
    campo que não resolve porque o item saiu do catálogo. Sem nenhum orçamento
    com evento, avisa em vez de fingir."""
    conta, erro = _conta_evento(request)
    if erro is not None:
        return erro
    pool = get_pool()
    orcamento, exemplo = _contexto_de_exemplo(pool, conta[0])
    if not orcamento:
        return JSONResponse({"erro": "nenhum orçamento com data de evento para usar de exemplo"},
                            status_code=404)
    catalogo = scat.listar(pool, conta[0])
    ctx = ctr.contexto(catalogo=catalogo, orcamento=orcamento,
                       modelo={"regras": dados.regras},
                       empresa=emp.obter_dados_empresa(pool, conta[0]))
    doc, faltas = ctr.montar(dados.clausulas, ctx)
    diag = ctr.diagnostico(faltas, ctx, catalogo)
    return JSONResponse({"clausulas": doc, "exemplo": exemplo,
                         "ajustes": diag["ajustes"], "da_proposta": diag["da_proposta"]})


class OrcDelIn(BaseModel):
    id: int


@router.post("/painel/servicos/excluir")
def painel_servicos_excluir(request: Request, dados: OrcDelIn):
    """Apaga um orçamento do funil.

    Não existia jeito nenhum de apagar: proposta gerada errada (e o agente gera
    sozinho agora) ficava no funil pra sempre, contando nos números e aparecendo pro
    vendedor. O catálogo já tinha excluir; o funil não.

    Duas travas, e as duas são de negócio, não de código:

    * PROPOSTA ASSINADA NÃO SE APAGA. `aprovada`/`fechado` é documento com aceite do
      cliente e, no fechado, título a receber no módulo Empresa — sumir com ele
      deixaria o financeiro apontando pra um orçamento que não existe. Erro em
      documento assinado se conserta emitindo outro, que é a mesma regra que a
      página da proposta já segue ao parar de reler o cadastro depois do aceite.
    * VENDEDOR SÓ APAGA O QUE É DELE. Mesmo recorte da listagem: quem vê só as
      próprias propostas não pode apagar as dos outros por id.

    O lead aponta pro orçamento (prospeccao.orcamento_id, FK sem on delete), então o
    vínculo é solto antes — senão o delete estoura no banco e o botão não funcionaria
    justamente no caso mais comum, o orçamento que nasceu de um lead."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    membro_id, papel = _ator(request)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        r = c.execute("select coalesce(status,'rascunho'), coalesce(criado_por,'') "
                      "from orcamentos where id=%s and conta_id=%s",
                      (int(dados.id), conta[0])).fetchone()
        if not r:
            return JSONResponse({"erro": "proposta não encontrada"}, status_code=404)
        status, criado_por = r
        if papel == "vendedor" and membro_id and criado_por != str(membro_id):
            return JSONResponse({"erro": "essa proposta não é sua"}, status_code=403)
        if status in ("aprovada", "fechado"):
            return JSONResponse(
                {"erro": "proposta assinada não pode ser apagada — emita outra"},
                status_code=409)
        c.execute("update prospeccao set orcamento_id=null "
                  "where orcamento_id=%s and conta_id=%s", (int(dados.id), conta[0]))
        c.execute("delete from orcamentos where id=%s and conta_id=%s",
                  (int(dados.id), conta[0]))
        c.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- template
_SERVICOS_TPL = r"""{% extends "base" %}{% block conteudo %}
<div class="sv-wrap{% if servico_avulso %} evento{% endif %}">
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
/* orçamento de evento tem uma coluna a mais na linha (qtd, valor e subtotal):
   a tela abre um pouco pra o nome do serviço não virar uma coluna de 3 letras. */
.sv-wrap.evento{max-width:1120px}
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
.oc-mod{display:grid; grid-template-columns:auto 1fr 84px 84px 84px 104px auto; gap:.55rem; align-items:center; padding:.6rem 0; border-bottom:1px solid var(--borda)}
.oc-mod.avulso,.oc-head.avulso{grid-template-columns:auto minmax(0,1fr) 56px 96px 104px 92px auto}
.sv-wrap.oc-margin .oc-mod.avulso,.sv-wrap.oc-margin .oc-head.avulso{grid-template-columns:auto minmax(0,1fr) 56px 96px 88px 104px 92px auto}
/* no orçamento de evento o rótulo vai EM CIMA do campo: "7200" e "10" precisam
   da largura inteira da caixinha, senão o número sai cortado. */
.oc-mod.avulso .oc-num{flex-direction:column; align-items:stretch; gap:1px; padding:.25rem .45rem}
.oc-mod.avulso .oc-num span{font-size:.58rem; text-align:right}
/* celular: a linha do serviço vira duas — nome em cima (com a foto e o
   toggle), números embaixo. Em grade de 6 colunas num telefone os campos caem
   em qualquer lugar. */
@media(max-width:700px){
  .oc-mod.avulso{display:flex; flex-wrap:wrap; align-items:flex-start; gap:.5rem .55rem}
  .oc-mod.avulso .oc-tog{order:1; flex:0 0 auto}
  .oc-mod.avulso .oc-nome{order:2; flex:1 1 140px; min-width:0}
  .oc-mod.avulso .oc-rowacts{order:3; flex:0 0 auto}
  .oc-mod.avulso .oc-num{order:4; flex:1 1 86px}
  /* DESCONTO E SUBTOTAL COLADOS: são os dois números que a pessoa compara ao
     negociar; separados obrigam a rolar de um pro outro. O par precisa de mais
     largura que um campo simples por causa do alternador %/R$. */
  .oc-mod.avulso .oc-desc-col{order:5; flex:1 1 118px}
  .oc-mod.avulso .oc-sub{order:6; flex:1 1 86px; justify-content:flex-end}
}
@media(max-width:700px){
  /* recorrente no estreito: a grade de 7 colunas não cabe, então vira lista */
  .oc-mod:not(.avulso){display:flex; flex-wrap:wrap; align-items:flex-start; gap:.5rem .55rem}
  .oc-mod:not(.avulso) .oc-nome{flex:1 1 140px; min-width:0}
  .oc-mod:not(.avulso) .oc-num{flex:1 1 86px}
  .oc-mod:not(.avulso) .oc-desc-col{flex:1 1 118px}
}
/* subtotal da linha: valor calculado, não campo — o vendedor lê enquanto monta */
.oc-sub{display:flex;flex-direction:column;gap:.15rem;text-align:right}
.oc-sub span{font-size:.62rem;letter-spacing:.04em;text-transform:uppercase;color:var(--txt-mut)}
.oc-sub b{font-size:.9rem;color:var(--verde-claro);white-space:nowrap}
.pg-row{display:grid; grid-template-columns:140px 110px minmax(0,1fr) minmax(0,1fr) auto; gap:.5rem; align-items:center; padding:.45rem 0; border-bottom:1px solid var(--borda)}
.pg-row:last-child{border-bottom:0}
.pg-row input{padding:.4rem .5rem; font-size:.88rem}
.pg-row .oc-valor{text-align:right}
@media(max-width:640px){.pg-row{grid-template-columns:1fr 1fr; gap:.4rem}.pg-row .pg-obs{grid-column:1/-1}}
.pg-aviso{color:#e6b877}
/* paleta de ícones do serviço (no lugar da foto): a biblioteca inteira à vista,
   com o escolhido aceso. Um clique troca — sem upload, sem espera, sem rede. */
.svc-icones{display:flex; gap:.3rem; flex-wrap:wrap; max-width:330px}
.svc-icones .op{width:34px;height:34px;border-radius:8px;border:1.5px solid var(--borda);
  display:flex;align-items:center;justify-content:center;cursor:pointer;
  color:var(--txt-mut);background:var(--bg)}
.svc-icones .op.on{border-color:var(--verde);color:var(--verde-claro);background:var(--card-2)}
.svc-thumb{width:46px;height:46px;border-radius:8px;flex:0 0 46px;border:1px solid var(--borda);
  background:var(--bg);display:flex;align-items:center;justify-content:center;color:var(--verde-claro)}
.oc-mod .svc-thumb{width:34px;height:34px;flex:0 0 34px;border-radius:7px}
.svc-thumb svg{width:22px;height:22px}
.oc-mod .svc-thumb svg{width:18px;height:18px}
.oc-nome-linha{display:flex;gap:.5rem;align-items:center;min-width:0}
.oc-cat{font-size:.66rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  color:var(--verde-claro);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.oc-mod.off{opacity:.5}
.oc-mod .oc-nome,.oc-browse-row .oc-nome{cursor:default; min-width:0}
.oc-desc-preview{white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%}
.oc-rowacts{display:flex; gap:.35rem; white-space:nowrap}
.oc-ic{background:var(--bg); border:1px solid var(--borda); color:var(--txt-mut); cursor:pointer; font-size:.85rem; width:30px; height:30px; padding:0; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; transition:border-color .15s,color .15s,background .15s}
.oc-ic:hover{color:var(--txt); border-color:var(--verde); background:var(--card)}
.oc-del:hover{color:#e0857a; border-color:#5c2a27}

/* A TELA DE ENVIO. Modal por cima do funil: o caminho de quem só quer mandar é
   abrir e apertar Enviar — tudo já vem preenchido. */
.env-fundo{position:fixed; inset:0; background:rgba(0,0,0,.6); z-index:60;
  display:none; align-items:flex-start; justify-content:center; padding:6vh 1rem 2rem; overflow:auto}
.env-fundo.on{display:flex}
.env-cx{background:var(--card); border:1px solid var(--borda); border-radius:14px;
  padding:1rem 1.1rem; width:100%; max-width:520px; display:flex; flex-direction:column; gap:.7rem}
.env-hd{display:flex; align-items:flex-start; justify-content:space-between; gap:1rem}
.env-hd h3{margin:0; font-size:1.05rem}
.env-x{background:none; border:0; color:var(--txt-mut); font-size:1rem; cursor:pointer; padding:.1rem .3rem}
.env-campo{display:flex; flex-direction:column; gap:.25rem}
.env-campo label{font-size:.72rem; font-weight:600; color:var(--txt-mut)}
.env-campo input,.env-campo textarea{background:var(--card-2); border:1px solid var(--borda);
  border-radius:9px; padding:.5rem .6rem; font-size:.9rem; color:var(--txt); font-family:inherit; width:100%}
.env-campo textarea{min-height:6rem; line-height:1.5; resize:vertical}
.env-campo input:focus,.env-campo textarea:focus{outline:0; border-color:var(--verde)}
.env-de{display:flex; gap:.5rem; align-items:flex-start; font-size:.78rem; color:var(--txt-mut);
  background:var(--card-2); border:1px solid var(--borda); border-radius:9px; padding:.5rem .6rem}
.env-de b{color:var(--txt)}
.env-acoes{display:flex; align-items:center; gap:.5rem; flex-wrap:wrap}
.env-hist{margin-left:auto; font-size:.72rem; color:var(--txt-mut); text-align:right}
.env-msg{font-size:.84rem; line-height:1.5; border-radius:9px; padding:.55rem .7rem; display:none}
.env-msg.on{display:block}
.env-msg.amb{background:var(--ambar-fundo); border:1px solid var(--ambar-borda); color:var(--amar)}
.env-msg.cor{background:var(--coral-fundo); border:1px solid var(--coral-borda); color:var(--verm)}
.env-msg.ok{background:var(--neon-fundo); border:1px solid var(--neon-borda); color:var(--verde-claro)}

/* PAGAMENTOS. Mesma caixa do envio — o dono já sabe como ela abre e fecha. */
.pg-tot{display:flex; gap:1.1rem; flex-wrap:wrap; font-size:.8rem; color:var(--txt-mut);
  border-bottom:1px solid var(--borda); padding-bottom:.55rem}
.pg-tot b{color:var(--txt); font-variant-numeric:tabular-nums}
.pg-tot .ok b{color:var(--verde-claro)}
.pg-tot .fl b{color:var(--amar)}
.pl{display:grid; grid-template-columns:3px 1fr auto auto; gap:.7rem; align-items:center;
  padding:.5rem 0; border-top:1px dashed var(--borda)}
.pl:first-of-type{border-top:0}
.pl .bar{align-self:stretch; min-height:2rem; border-radius:3px; background:var(--borda)}
.pl.paga .bar{background:var(--verde)}
.pl.hoje .bar{background:var(--ambar)}
.pl .tt{font-size:.85rem; font-weight:600}
.pl .mt{font-size:.71rem; color:var(--txt-mut)}
.pl .vl{font-size:.83rem; font-weight:700; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap}
.pl .ac{white-space:nowrap}
.pmini{font-size:.7rem; font-weight:600; border-radius:7px; padding:.24rem .5rem; cursor:pointer;
  border:1px solid var(--borda); color:var(--txt-mut); background:transparent; font-family:inherit;
  width:auto; margin:0; text-decoration:none; display:inline-block}
.pmini.up{border-color:var(--azul-borda); color:var(--azul); background:var(--azul-fundo)}
.pmini.ver{border-color:var(--neon-borda); color:var(--verde-claro); background:var(--neon-fundo)}
.pmini:disabled{opacity:.5; cursor:default}
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
.oc-head{display:grid; grid-template-columns:auto 1fr 84px 84px 84px 104px auto; gap:.55rem; font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--txt-mut); padding-bottom:.4rem; border-bottom:1px solid var(--borda)}
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

/* ---------------------------------------------- A LINHA DO FUNIL, LADO DIREITO
   A regra que organiza tudo aqui: SELO = PENDÊNCIA. Antes a linha pintava um
   selo pra cada coisa que tinha acontecido — aprovada, enviada, sinal recebido,
   data firme, contrato assinado — e uma proposta perfeitamente em dia carregava
   cinco caixinhas verdes dizendo que estava tudo bem. No meio disso, o selo que
   importava (a data que caiu, a parcela sem comprovante) tinha o mesmo tamanho
   e o mesmo peso de todos os outros e simplesmente sumia.
   O que já aconteceu foi pro subtítulo cinza, à esquerda. Aqui à direita fica
   só o que ainda falta — e quando não falta nada, um ✓ discreto. */
/* atalho de WhatsApp da linha: discreto, mas alcançável com o polegar */
.oc-zap{display:inline-flex; align-items:center; justify-content:center;
  width:30px; height:30px; border-radius:8px; text-decoration:none;
  border:1px solid var(--borda); background:var(--bg); font-size:.95rem; line-height:1}
.oc-zap:hover{border-color:var(--verde)}
.oc-acoes{display:flex; align-items:center; gap:.4rem; flex-wrap:wrap;
  justify-content:flex-end; margin-left:auto; position:relative; flex:0 1 auto}
/* O NOME NÃO CEDE ESPAÇO PROS SELOS. Uma proposta com quatro pendências enchia a
   direita e espremia "Priscila Ramos · Aniversário 50 anos" numa palavra por
   linha. Com um piso aqui, quem quebra pra linha de baixo é a barra de selos,
   que é o que sobra.
   (Isto morava num style inline no JS, com min-width:0 — e inline vence folha de
   estilo, então a regra daqui não tinha como valer. Mora aqui agora.) */
.oc-hist .oc-hist-open{display:flex; align-items:center; cursor:pointer;
  flex:1 1 320px; min-width:0}
.oc-hist .oc-hist-open > div{min-width:0}
.oc-badge.pend{display:inline-flex; align-items:center; font-size:.7rem; font-weight:600;
  padding:.22rem .55rem; border-radius:7px; letter-spacing:0; text-transform:none;
  white-space:nowrap; border:1px solid transparent}
/* coral = custa dinheiro se ficar assim (data fora da agenda, data liberada,
   parcela paga sem comprovante) */
.oc-badge.pend.coral{background:var(--coral-fundo); color:var(--verm); border-color:var(--coral-borda)}
/* âmbar = está de pé mas depende de alguém (contrato esperando assinatura) */
.oc-badge.pend.ambar{background:var(--ambar-fundo); color:var(--amar); border-color:var(--ambar-borda)}
/* âmbar TRACEJADO = provisório, e o prazo corre. É o mesmo par sólido/tracejado
   que a agenda usa pra separar reservado de segurado — quem não distingue cor
   lê pela forma. */
.oc-badge.pend.pre{background:var(--ambar-fundo); color:var(--amar);
  border:1px dashed var(--ambar-borda)}
/* azul = falta um passo seu, sem prazo nem prejuízo (nunca enviada ao cliente) */
.oc-badge.pend.azul{background:var(--azul-fundo); color:var(--azul); border-color:var(--azul-borda)}
.oc-nada{font-size:.72rem; color:var(--txt-mut); white-space:nowrap}

/* A AÇÃO. Uma só por linha, verde, com o nome do que falta fazer. Verde deixou
   de ser enfeite: onde ele estiver, é ali que se clica. */
.oc-fechar{background:var(--verde); color:var(--sobre-verde); border:0; border-radius:8px;
  padding:.4rem .8rem; font-weight:600; cursor:pointer; font-size:.8rem; white-space:nowrap}
.oc-fechar:disabled{opacity:.55; cursor:default}

/* AÇÕES ▾ — com a palavra escrita, não um ícone. Eram oito emojis sem rótulo
   (✏️ 🔗 📄 ✉️ 📎 📜 ↗ 🗑) encostados uns nos outros, cinco deles "abrir ou
   mandar um documento" pra DOIS documentos diferentes, e nada dizia qual era de
   qual. Ninguém decora fileira de emoji; todo mundo lê "Ações". */
.oc-menu-btn{background:var(--bg); border:1px solid var(--borda); color:var(--txt-mut);
  border-radius:8px; padding:.4rem .6rem; font-size:.78rem; font-weight:600; cursor:pointer;
  white-space:nowrap; display:inline-flex; align-items:center; gap:.3rem;
  transition:border-color .15s, color .15s}
.oc-menu-btn:hover{color:var(--txt); border-color:var(--verde)}
.oc-menu-btn .cv{font-size:.65rem; opacity:.8}

/* O menu é filho do .oc-acoes (que é relative) — abre ancorado na direita da
   própria linha, não no canto da tela. */
.oc-menu{position:absolute; top:calc(100% + 6px); right:0; z-index:50;
  min-width:230px; max-width:min(300px, calc(100vw - 2rem));
  background:var(--card); border:1px solid var(--borda); border-radius:11px;
  padding:.3rem; display:flex; flex-direction:column;
  box-shadow:0 12px 32px rgba(0,0,0,.5)}
/* agrupado POR DOCUMENTO: "Proposta nº 14" e "Contrato nº 5" viram os títulos, e
   aí o link que está embaixo de cada um não precisa mais ser adivinhado. */
.oc-mgrupo{font-size:.62rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  color:var(--txt-mut); padding:.5rem .55rem .25rem}
.oc-mgrupo:first-child{padding-top:.25rem}
.oc-mi{display:flex; align-items:center; gap:.5rem; width:100%; text-align:left;
  background:none; border:0; border-radius:8px; padding:.44rem .55rem; cursor:pointer;
  font-size:.83rem; font-family:inherit; color:var(--txt)}
.oc-mi:hover{background:var(--card-2)}
.oc-mi .e{flex:none; width:1.15rem; text-align:center; font-size:.85rem}
.oc-mi .t{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.oc-mi .s{flex:none; font-size:.7rem; color:var(--txt-mut)}
/* apagar fica sozinho, atrás de uma linha e em coral: era um 🗑 do mesmo tamanho
   e da mesma cor do 📄, encostado nele. */
.oc-mi.sep{margin-top:.3rem; border-top:1px solid var(--borda); padding-top:.5rem; border-radius:0 0 8px 8px}
.oc-mi.perigo{color:var(--verm)}
.oc-mi.perigo:hover{background:var(--coral-fundo)}
/* DESCONTO: campo + alternador %/R$. O mesmo par se repete na linha do item e no
   total, de propósito — dois controles diferentes pra mesma ideia viram duas ideias. */
.oc-dpar{display:flex; align-items:stretch; width:100%}
.oc-dpar > input{flex:1; min-width:0; text-align:right; border-radius:8px 0 0 8px; border-right:0}
.oc-dtog{display:flex; border:1px solid var(--linha); border-left:0; border-radius:0 8px 8px 0; overflow:hidden}
.oc-dtog button{border:0; background:var(--fundo-2); color:var(--txt-mut); font-size:.72rem;
  padding:0 .5rem; cursor:pointer; font-weight:600; min-width:2rem}
.oc-dtog button.on{background:var(--verde); color:var(--sobre-verde)}
.oc-dzero{margin-top:.45rem; width:100%; background:none; border:1px solid var(--linha);
  color:var(--txt-mut); border-radius:8px; padding:.32rem; font-size:.72rem; cursor:pointer;
  letter-spacing:.06em; text-transform:uppercase}
.oc-dzero:hover{color:var(--txt)}
/* as linhas de desconto do resumo: verdes, porque é dinheiro que o cliente ganha */
.oc-dline b{color:var(--verde-claro)}
/* subtotal da linha: o cheio riscado por cima do líquido, quando há desconto */
.oc-sub-risc{display:block; font-size:.72rem; color:var(--txt-mut); text-decoration:line-through}
/* mobile: cada serviço vira 2 linhas (toggle+nome+ações em cima, valores embaixo).
   Fica no FIM do bloco pra vencer a cascata das regras base acima. */
@media(max-width:600px){
  /* no celular a linha quebra em duas: quem/quanto em cima, pendências e ações
     embaixo — alinhadas à ESQUERDA, embaixo do nome, senão o ✓ e o "Ações" ficam
     pendurados sozinhos no canto direito. */
  .oc-hist .oc-acoes{width:100%; margin-left:0; justify-content:flex-start}
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

{% if pode_contrato %}
{# Contrato de locação: nicho de eventos E só pro DONO — ele define o que a
   empresa se compromete a cumprir, e isso não é decisão de quem vende. O gate
   de verdade está nas rotas (ver _conta_evento): esconder o card não impede
   um POST direto.

   PRIMEIRO CARD DA PÁGINA. Era o último, depois do Funil — quem ia gerar a
   proposta não passava por ele, e campo sem valor só aparecia no documento do
   cliente. Fechado ocupa uma linha: o selo responde "está tudo certo?" sem
   tirar espaço de quem só quer montar o orçamento, que é o trabalho diário. #}
<div class="card" id="ct-card">
  {# Cabeçalho clicável INTEIRO, não só a seta: alvo de 12px no celular é o que
     faz o dono achar que a tela travou. #}
  <div id="ct-cab" style="display:flex;align-items:center;gap:.6rem;cursor:pointer;user-select:none">
    <span id="ct-seta" style="color:var(--mut);font-size:.85rem;transition:transform .18s">▸</span>
    <div style="min-width:0">
      <div style="font-weight:700;font-size:1rem">Contrato de locação</div>
      <div id="ct-resumo" class="mut" style="font-size:.78rem;margin-top:.1rem">Carregando...</div>
    </div>
    <div id="ct-selo" style="margin-left:auto;flex-shrink:0"></div>
  </div>
  {# O QUE FAZER, não quais campos. O selo diz que há problema; esta linha diz o
     conserto e onde fica — é o que separa um aviso de uma tarefa. Fora do cabeçalho
     porque a frase é larga e espremer ao lado do selo cortaria a informação. #}
  <div id="ct-faltas" style="display:none;cursor:pointer;margin-top:.5rem;font-size:.74rem;
       line-height:1.5;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);
       border-radius:8px;padding:.4rem .55rem;color:var(--amar)"></div>
  <div id="ct-corpo" style="display:none;margin-top:.85rem;padding-top:.85rem;border-top:1px solid var(--borda)">
    <p class="mut" style="margin-top:0;font-size:.86rem">
      As cláusulas são suas — escreva como quiser. Onde entra um valor, use um
      <b style="color:var(--verde-claro)">campo</b>: ele é preenchido na hora com o preço do
      catálogo e os dados do orçamento, então o contrato nunca diz um número diferente da proposta.
    </p>
    <div id="ct-box"><p class="mut">Carregando...</p></div>
  </div>
</div>
{% endif %}

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
  {# A HORA DE INÍCIO É O QUE SEGURA A DATA. Sem ela a aprovação do cliente não
     vira compromisso na agenda — e saía calada: o vendedor prometia a data, o
     cliente assinava, e ninguém ficava sabendo que ela nunca foi reservada.
     AVISA, não bloqueia: às vezes se fecha a proposta com a hora ainda a
     combinar, e travar o botão travaria a venda. #}
  <div id="ev-sem-hora" style="display:none;margin-top:.6rem;font-size:.8rem;line-height:1.5;
       background:var(--ambar-fundo);border:1px solid var(--ambar-borda);
       border-radius:8px;padding:.45rem .6rem;color:var(--amar)">
    <b>Sem a hora de início, esta data não entra na agenda.</b>
    <div style="opacity:.85;margin-top:.15rem">Pode salvar assim — mas a data só fica
      segurada quando você preencher o Início.</div>
  </div>
  <div class="oc-field"><label>Tipo de evento</label>
    <div style="display:flex; gap:.4rem; flex-wrap:wrap" id="ev-tipos">
      {% for t in tipos_evento %}<button type="button" class="oc-pill ev-tipo">{{ t }}</button>{% endfor %}
    </div>
  </div>
  <div class="oc-field"><label>Tipo de contrato</label>
    <div style="display:flex; gap:.4rem; flex-wrap:wrap" id="ev-contratos">
      {% for ct in tipos_contrato %}<button type="button" class="oc-pill ev-ct" data-on="0">{{ ct }}</button>{% endfor %}
    </div>
  </div>
  <div class="oc-field" style="margin-bottom:.3rem"><label>Local</label><input id="ev-local" class="oc-inp" value="{{ local_padrao }}" data-padrao="{{ local_padrao }}" placeholder="Espaço 01 — Rua Deoclécio Brito, 3399"></div>
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
      <label id="oc-cnpj-label">CNPJ <span id="oc-cnpj-dica" style="color:var(--txt-mut);font-size:.78rem">— preenche empresa, segmento e contato automaticamente</span></label>
      <div style="display:flex; gap:.5rem; align-items:center">
        <input id="oc-cnpj" class="oc-inp" placeholder="00.000.000/0000-00" inputmode="numeric" style="flex:1">
        <button id="oc-cnpj-btn" type="button" style="background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.55rem 1.1rem;font-weight:600;cursor:pointer;white-space:nowrap">Buscar</button>
      </div>
      <span id="oc-cnpj-msg" style="font-size:.8rem;color:var(--txt-mut);display:block;margin-top:.25rem"></span>
    </div>
    <div style="display:grid; grid-template-columns:{{ '1fr 1fr 1fr' if servico_avulso else '1fr 1fr' }}; gap:.8rem">
      <div class="oc-field"><label id="oc-empresa-label">Empresa</label><input id="oc-empresa" class="oc-inp" placeholder="Nome da empresa"></div>
      {# UM CAMPO SÓ NO EVENTO. Pedir "Empresa" e "Contato" pra uma noiva foi a
         origem da bagunça: `empresa` recebia o nome e `contato` recebia o que
         sobrasse — em produção, telefone e nome pela metade. No recorrente os dois
         seguem, porque ali são coisas diferentes: a empresa e quem fala com você. #}
      <div class="oc-field" id="campo-oc-contato"{% if servico_avulso %} style="display:none"{% endif %}><label>Contato</label><input id="oc-contato" class="oc-inp" placeholder="Responsável"></div>
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
          <div class="oc-field" style="margin-bottom:.4rem"><label>Descrição</label><textarea id="svc-desc" class="oc-inp" rows="2" placeholder="O que está incluso — pode escrever a lista inteira, sai igual no orçamento"></textarea></div>
        </div>
        {% if servico_avulso %}
        <div style="display:grid; grid-template-columns:1fr auto; gap:.6rem; align-items:end; margin-bottom:.4rem">
          <div class="oc-field" style="margin-bottom:0"><label>Categoria <span style="color:var(--txt-mut);font-size:.78rem">— agrupa e soma por categoria no orçamento</span></label>
            <select id="svc-cat" class="oc-inp"><option value="">Sem categoria</option></select>
          </div>
          <div class="oc-field" style="margin-bottom:0"><label>Ícone
            <span style="color:var(--txt-mut);font-size:.78rem">— escolhido sozinho pelo nome; clique pra trocar</span></label>
            <input type="hidden" id="svc-icone">
            <div id="svc-icones" class="svc-icones"></div>
          </div>
        </div>
        {% endif %}
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
        <span></span><span>Serviço</span><span style="text-align:right">{{ 'Valor' if servico_avulso else 'Setup' }}</span>{% if not servico_avulso %}<span style="text-align:right">Mensal</span>{% endif %}<span style="text-align:right">{{ 'Custo' if servico_avulso else 'Custo/Margem' }}</span><span style="text-align:right">Desconto</span><span></span>
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
      <div class="oc-ll oc-dline" id="oc-r-descitens-l" style="display:none"><span class="mut">Descontos por item</span><b id="oc-r-descitens">R$ 0</b></div>
      <div class="oc-ll" id="oc-r-sub-l" style="display:none"><span class="mut">Subtotal com descontos</span><b id="oc-r-sub">R$ 0</b></div>
      <div class="oc-ll oc-dline" id="oc-r-descfim-l" style="display:none"><span class="mut">Desconto no total</span><b id="oc-r-descfim">R$ 0</b></div>
      <!-- o desconto do TOTAL vale nos dois modos: consultoria e advocacia vendem
           por orçamento igual, e só não tinham desconto porque ele morava dentro
           do jsonb do evento. -->
      <div class="oc-field" style="margin-top:.7rem; margin-bottom:0">
        <label class="mut" style="font-size:.76rem">Desconto no total</label>
        <div class="oc-dpar oc-dpar-tot" data-tipo="pct">
          <input id="oc-desconto" class="oc-inp oc-desc-inp" inputmode="numeric" value="0">
          <span class="oc-dtog">
            <button type="button" data-t="pct" class="on">%</button>
            <button type="button" data-t="valor">R$</button>
          </span>
        </div>
        <button id="oc-desc-zerar" class="oc-dzero" type="button">zerar desconto</button>
      </div>
      {% if not servico_avulso %}
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

{# A TELA DE ENVIO. Nasce vazia e é preenchida pelo servidor ao abrir — o assunto,
   a mensagem e o e-mail do cliente já vêm prontos, e por qual caixa vai sair é
   dito ANTES de apertar. Fora do .oc-wrap pra o fundo escuro cobrir a página. #}
<div class="env-fundo" id="env-fundo" role="dialog" aria-modal="true" aria-labelledby="env-tt">
  <div class="env-cx">
    <div class="env-hd">
      <h3 id="env-tt">Mandar por e-mail</h3>
      <button type="button" class="env-x" id="env-x" aria-label="Fechar">✕</button>
    </div>
    <div class="env-msg" id="env-msg"></div>
    <div class="env-campo"><label for="env-para">Para</label>
      <input id="env-para" type="email" inputmode="email" autocomplete="off" placeholder="email@do-cliente.com"></div>
    <div class="env-campo"><label for="env-assunto">Assunto</label>
      <input id="env-assunto" type="text"></div>
    <div class="env-campo"><label for="env-texto">Mensagem</label>
      <textarea id="env-texto"></textarea></div>
    <div class="env-de" id="env-de"></div>
    <div class="env-acoes">
      <button type="button" class="oc-btn oc-btn-g" style="width:auto;margin:0" id="env-enviar">Enviar</button>
      <button type="button" class="oc-pill" id="env-cancelar">Cancelar</button>
      <span class="env-hist" id="env-hist"></span>
    </div>
  </div>
</div>

{# PAGAMENTOS. Reusa a caixa do envio (.env-fundo/.env-cx): a mesma forma de abrir
   e fechar, o mesmo Esc, o mesmo clique no fundo. Duas caixas com comportamentos
   diferentes seriam duas coisas pra aprender. #}
<div class="env-fundo" id="pg-fundo" role="dialog" aria-modal="true" aria-labelledby="pg-tt">
  <div class="env-cx">
    <div class="env-hd">
      <h3 id="pg-tt">Pagamentos</h3>
      <button type="button" class="env-x" id="pg-x" aria-label="Fechar">✕</button>
    </div>
    <div class="env-msg" id="pg-msg"></div>
    <div class="pg-tot" id="pg-tot"></div>
    <div id="pg-lista"><p class="mut" style="font-size:.85rem">Carregando...</p></div>
    <input type="file" id="pg-arquivo" accept="application/pdf,image/*" style="display:none">
  </div>
</div>

<script>window.SERVICO_AVULSO = {{ 'true' if servico_avulso else 'false' }};</script>
<script>window.ZAQ_ICONES = {{ icones_paleta|tojson }};</script>
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

  // ─────────────────────────── DESCONTO ───────────────────────────
  // ESTA CONTA É A MESMA de finance/desconto.py, linha por linha. Se as duas
  // divergirem, a tela mostra um número e o cliente lê outro — foi assim que um
  // orçamento ficou com parcelas somando 12.105 e total de 9.405.
  //
  // No ITEM o desconto é SEMPRE percentual, mesmo digitado em reais: a linha do
  // recorrente tem setup E mensalidade, e `fechar_orcamento` gera um título de
  // cada. Valor único não teria como voltar a se dividir entre as duas pontas
  // sem chute, então reais viram o percentual equivalente da contribuição da
  // linha ao primeiro ano, e esse percentual cai igual nos dois.
  function pctLinha(r){
    var par=r.querySelector('.oc-dpar'); if(!par) return 0;
    var v=num(r.querySelector('.oc-desc'));
    if(v<=0) return 0;
    if((par.getAttribute('data-tipo')||'pct')==='pct') return Math.min(100,v);
    var base=num(r.querySelector('.oc-setup'))*qtd(r)+num(r.querySelector('.oc-mensal'))*12;
    return base>0?Math.min(100,100*v/base):0;
  }
  // o desconto do TOTAL, já resolvido em reais sobre a base que recebe
  function descFinal(base){
    var par=document.querySelector('.oc-dpar-tot'); if(!par||base<=0) return 0;
    var v=num(document.getElementById('oc-desconto'));
    if(v<=0) return 0;
    var d=((par.getAttribute('data-tipo')||'pct')==='valor')?v:Math.round(base*Math.min(100,v)/100);
    // NUNCA maior que a base: desconto que ultrapassa viraria acréscimo.
    return Math.max(0,Math.min(base,d));
  }

  function calc(){
    // BRUTO e LÍQUIDO andam juntos: o resumo mostra o líquido, mas é o BRUTO que
    // vai no payload — o servidor refaz a conta do desconto, e receber o já
    // descontado faria ele descontar de novo.
    var setup=0,mensal=0,modMensal=0,custo=0,mods=0,descItens=0;
    var setupBruto=0,mensalBruto=0;
    rows().forEach(function(r){
      if(r.getAttribute('data-on')==='1'){
        mods++;
        var q=qtd(r);
        var p=pctLinha(r);
        var sb=num(r.querySelector('.oc-setup'))*q, mb=num(r.querySelector('.oc-mensal'));
        var sl=Math.round(sb*(100-p)/100), ml=Math.round(mb*(100-p)/100);
        descItens+=(sb-sl)+(mb-ml)*12;
        setupBruto+=sb; mensalBruto+=mb;
        setup+=sl;
        mensal+=ml; modMensal+=ml;
        custo+=num(r.querySelector('.oc-custo'))*q;
      }
    });
    var inf=INFRA[seg('infra')]||INFRA.compartilhada;
    setup+=inf.s; mensal+=inf.m; setupBruto+=inf.s; mensalBruto+=inf.m;
    if(seg('volume')==='alto'){mensal+=600; mensalBruto+=600;}
    var integ=num(document.getElementById('oc-integ'));
    setup+=integ*250; mensal+=integ*120; setupBruto+=integ*250; mensalBruto+=integ*120;
    var canais=document.querySelectorAll('.oc-canal[data-on="1"]').length;
    setup+=canais*400; setupBruto+=canais*400;
    var ocSup=document.getElementById('oc-sup');
    if(ocSup&&ocSup.getAttribute('data-on')==='1'){mensal+=1500; mensalBruto+=1500;}
    if(SERVICO_AVULSO){
      var dFim=descFinal(setup), total=setup-dFim;
      // A MARGEM USA O VALOR JÁ DESCONTADO. Com o desconto fora dela, ela mentiria
      // exatamente quando mais importa: na hora de decidir quanto dá pra descontar.
      var margemAv=total-custo, margemAvPct=total>0?Math.round(margemAv/total*100):0;
      return {setup:setup,mensal:0,mensalCheio:0,ano1:total,margem:margemAv,
              margemPct:margemAvPct,mods:mods,subtotal:setup,descItens:descItens,
              descFim:dFim,economia:descItens+dFim,
              setupBruto:setupBruto,mensalBruto:0};
    }
    var anual=document.getElementById('oc-anual').getAttribute('data-on')==='1';
    var mensalEf=anual?mensal*0.85:mensal;
    var sub=setup+mensalEf*12;
    var dFim=descFinal(sub), ano1=sub-dFim;
    var margem=modMensal-custo, margemPct=modMensal>0?Math.round(margem/modMensal*100):0;
    return {setup:setup,mensal:mensalEf,mensalCheio:mensal,ano1:ano1,margem:margem,
            margemPct:margemPct,mods:mods,anual:anual,subtotal:sub,
            descItens:descItens,descFim:dFim,economia:descItens+dFim,
            setupBruto:setupBruto,mensalBruto:(anual?mensalBruto*0.85:mensalBruto)};
  }

  function pinta(){
    var c=calc();
    // subtotal de cada linha (qtd × valor unitário), ao vivo
    rows().forEach(function(r){
      var el=r.querySelector('.oc-sub-v');
      if(!el) return;
      var sb=num(r.querySelector('.oc-setup'))*qtd(r), p=pctLinha(r);
      var sl=Math.round(sb*(100-p)/100);
      // o cheio riscado só aparece QUANDO há desconto — riscar um valor igual ao
      // de baixo seria ruído.
      el.innerHTML=(sl<sb?'<span class="oc-sub-risc">'+fmt(sb)+'</span>':'')+fmt(sl);
    });
    document.getElementById('oc-r-setup').textContent=fmt(c.setup);
    var elMensal=document.getElementById('oc-r-mensal');
    if(elMensal)elMensal.textContent=fmt(c.mensal);
    document.getElementById('oc-r-ano').textContent=fmt(c.ano1);
    document.getElementById('oc-r-margem').textContent=fmt(c.margem)+' · '+c.margemPct+'%';
    // as três linhas do desconto: só aparecem quando existem, pra o resumo de quem
    // não usa desconto continuar do tamanho que sempre teve.
    function mostra(idL,id,val){
      var l=document.getElementById(idL); if(!l) return;
      l.style.display=val>0?'flex':'none';
      if(val>0) document.getElementById(id).textContent='− '+fmt(val);
    }
    mostra('oc-r-descitens-l','oc-r-descitens',c.descItens||0);
    mostra('oc-r-descfim-l','oc-r-descfim',c.descFim||0);
    var subL=document.getElementById('oc-r-sub-l');
    if(subL){
      var temDesc=(c.descItens||0)>0&&(c.descFim||0)>0;
      subL.style.display=temDesc?'flex':'none';
      if(temDesc) document.getElementById('oc-r-sub').textContent=fmt(c.subtotal||0);
    }
    var eco=document.getElementById('oc-r-eco');
    if((c.economia||0)>0){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.economia);}
    else if(!SERVICO_AVULSO&&c.anual){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.mensalCheio*12*0.15)+' no ano';}
    else eco.style.display='none';
    // mudou item/desconto -> o plano de pagamento pode ter deixado de fechar
    if(SERVICO_AVULSO) pintaParcelas();
  }

  // linhas de serviço são dinâmicas (catálogo por conta) — delegação:
  var MODS=document.getElementById('oc-mods');
  // ALTERNADOR %/R$ — um só ouvinte pro par da linha e o par do total, porque é o
  // mesmo controle. Delegado no documento: as linhas são recriadas a cada
  // renderização do catálogo e ouvinte preso à linha morreria junto.
  document.addEventListener('click',function(e){
    var b=e.target.closest('.oc-dtog button'); if(!b) return;
    var par=b.closest('.oc-dpar'); if(!par) return;
    par.setAttribute('data-tipo',b.getAttribute('data-t'));
    par.querySelectorAll('.oc-dtog button').forEach(function(x){
      x.classList.toggle('on',x===b);
    });
    pinta();
  });
  var zerar=document.getElementById('oc-desc-zerar');
  if(zerar) zerar.addEventListener('click',function(){
    // zera o do TOTAL e o de cada linha: "zerar desconto" que deixa desconto pra
    // trás é pior que não ter botão.
    var dt=document.getElementById('oc-desconto'); if(dt) dt.value='0';
    document.querySelectorAll('.oc-desc').forEach(function(i){i.value='0';});
    document.querySelectorAll('.oc-dpar').forEach(function(par){
      par.setAttribute('data-tipo','pct');
      par.querySelectorAll('.oc-dtog button').forEach(function(x){
        x.classList.toggle('on',x.getAttribute('data-t')==='pct');
      });
    });
    pinta();
  });

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
       ||e.target.classList.contains('oc-custo')||e.target.classList.contains('oc-qtd')
       ||e.target.classList.contains('oc-desc')) pinta();
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
            local:document.getElementById('ev-local').value||'',
            desconto:Math.max(0,Math.min(100,num(document.getElementById('oc-desconto'))))};
  }
  // O aviso da hora que falta. Aparece só quando há DATA e não há hora: orçamento
  // sem data nenhuma não prometeu nada e não tem o que avisar.
  function pintarSemHora(){
    var el=document.getElementById('ev-sem-hora');
    if(!el)return;
    var temData=!!(document.getElementById('ev-data')||{}).value;
    var temHora=!!((document.getElementById('ev-ini')||{}).value||'').trim();
    el.style.display=(temData&&!temHora)?'block':'none';
  }
  if(SERVICO_AVULSO){
    ['ev-data','ev-ini'].forEach(function(id){
      var el=document.getElementById(id);
      if(el){el.addEventListener('input',pintarSemHora); el.addEventListener('change',pintarSemHora);}
    });
    pintarSemHora();
  }
  function aplicarEvento(ev){
    if(!SERVICO_AVULSO)return;
    ev=ev||{};
    setv('ev-data',ev.data); setv('ev-conv',ev.convidados?String(ev.convidados):'');
    setv('ev-ini',ev.inicio); setv('ev-fim',ev.fim);
    pintarSemHora();
    // Local: quase toda festa é no salão da própria empresa, então o endereço
    // dela já vem escrito. Evento fora ("na casa do cliente") o vendedor troca.
    var loc=document.getElementById('ev-local');
    setv('ev-local',ev.local||(loc?loc.getAttribute('data-padrao')||'':''));
    if(ev.desconto!=null) setv('oc-desconto',String(ev.desconto));
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
  // Quanto o plano de pagamento DIVERGE do total do orçamento, em centavos.
  // Positivo = as parcelas passam do total. Zero = fecha (ou nem tem plano).
  function difParcelas(){
    var linhas=coletarParcelas();
    if(!linhas.length) return 0;
    var soma=linhas.reduce(function(a,p){return a+p.valor_centavos;},0);
    return soma - Math.round(calc().ano1*100);
  }
  // O aviso de "não fecha" existe desde sempre, mas é passivo: texto âmbar no canto.
  // Nos dois momentos em que o número vira compromisso — mandar pro cliente e virar
  // título a receber — ele passa a PERGUNTAR. Não bloqueia: divergir tem caso
  // legítimo (juros de cartão, acréscimo por forma de pagamento); o que não pode é
  // sair calado, que foi como um orçamento de R$ 9.405,00 virou R$ 12.105,00 cobrados.
  function confirmaDivergencia(acao){
    var dif=difParcelas();
    if(!dif) return true;
    var total=Math.round(calc().ano1*100), soma=total+dif;
    return confirm('O plano de pagamento não fecha com o total.\n\n'
      + 'Total do orçamento: R$ '+fmtc(total)+'\n'
      + 'Soma das parcelas: R$ '+fmtc(soma)+'\n'
      + (dif>0?'Passa R$ ':'Faltam R$ ')+fmtc(Math.abs(dif))+'.\n\n'
      + acao);
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

  // A célula de desconto da LINHA. Mesmo par do desconto do total — a forma se
  // repete porque a ideia é a mesma.
  function descTipoTot(){
    var par=document.querySelector('.oc-dpar-tot');
    return (par&&par.getAttribute('data-tipo')==='valor')?'valor':'pct';
  }
  function celDesc(tipo,val){
    var t=(tipo==='valor')?'valor':'pct';
    return '<div class="oc-num oc-desc-col"><span>Desconto</span>'
      +'<div class="oc-dpar" data-tipo="'+t+'">'
      +'<input class="oc-desc" inputmode="numeric" value="'+(parseInt(val,10)||0)+'">'
      +'<span class="oc-dtog">'
      +'<button type="button" data-t="pct" class="'+(t==='pct'?'on':'')+'">%</button>'
      +'<button type="button" data-t="valor" class="'+(t==='valor'?'on':'')+'">R$</button>'
      +'</span></div></div>';
  }

  // eventos: catálogo ordenado A-Z + "busca pra adicionar" — a lista só mostra
  // o que já foi escolhido pra esta proposta; o resto fica atrás da busca ou
  // do link "ver todos", pra não repetir o card gigante de antes com 26 linhas
  // sempre visíveis.
  function buildRowAvulso(s){
    var thumb='<div class="svc-thumb">'+(s.icone_svg||'')+'</div>';
    return '<button class="oc-tog on" type="button" title="Remover da proposta"></button>'
      +'<div class="oc-nome oc-nome-linha">'+thumb+'<div style="min-width:0">'
      +(s.categoria?'<div class="oc-cat">'+ec(s.categoria)+'</div>':'')
      +'<b>'+ec(s.nome)+'</b><div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div></div>'
      +'<div class="oc-num"><span>Qtd</span><input class="oc-qtd" inputmode="numeric" value="1"></div>'
      +'<div class="oc-num"><span>Vr. unit.</span><input class="oc-setup" inputmode="numeric" value="'+s.setup+'"></div>'
      +'<div class="oc-num oc-custo-col"><span>Custo</span><input class="oc-custo" inputmode="numeric" value="'+s.custo+'"></div>'
      +celDesc(s.desc_tipo,s.desc_val)
      +'<div class="oc-sub"><span>Subtotal</span><b class="oc-sub-v">'+fmt(s.setup)+'</b></div>'
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
        +celDesc('pct',0)
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
      var selCat=document.getElementById('svc-cat');
      if(selCat&&selCat.options.length<2){
        (d.categorias||[]).forEach(function(nome){
          var o=document.createElement('option'); o.value=nome; o.textContent=nome; selCat.appendChild(o);
        });
      }
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
      if(ed){var row2=ed.closest('.oc-browse-row'); var s=CATALOGO.filter(function(x){return x.slug===row2.getAttribute('data-id');})[0]; if(s)abrirForm({id:s.id,nome:s.nome,descricao:s.descricao,setup:s.setup,mensal:s.mensal,custo:s.custo,categoria:s.categoria,icone:s.icone});}
      else if(dl){var row3=dl.closest('.oc-browse-row'); var s2=CATALOGO.filter(function(x){return x.slug===row3.getAttribute('data-id');})[0]; if(s2&&confirm('Excluir "'+s2.nome+'" do seu catálogo?')){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:s2.id})}).then(function(){carregarCatalogo(true);});}}
    });
  }
  // editar / excluir (delegação)
  document.getElementById('oc-mods').addEventListener('click',function(e){
    var ed=e.target.closest('.oc-edit'), dl=e.target.closest('.oc-del');
    if(ed){var r=ed.closest('.oc-mod'); var sc=CATALOGO.filter(function(x){return x.slug===r.getAttribute('data-id');})[0]||{}; abrirForm({id:r.getAttribute('data-cid'),nome:r.getAttribute('data-nome'),descricao:r.getAttribute('data-desc'),setup:num(r.querySelector('.oc-setup')),mensal:num(r.querySelector('.oc-mensal')),custo:num(r.querySelector('.oc-custo')),categoria:sc.categoria,icone:sc.icone});}
    else if(dl){var r2=dl.closest('.oc-mod'); if(confirm('Excluir "'+r2.getAttribute('data-nome')+'" do seu catálogo?')){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:parseInt(r2.getAttribute('data-cid'),10)})}).then(function(){carregarCatalogo(true);});}}
  });
  // form de add/editar serviço do catálogo — PALETA DE ÍCONES (no lugar da foto)
  // Serviço não tem embalagem pra fotografar: metade dos itens ficava sem foto
  // e a linha do orçamento desalinhava. O ícone vem sozinho pelo nome; o
  // vendedor só clica quando quer outro.
  var PALETA=(window.ZAQ_ICONES||[]);
  var svcIconeSugerido='outros', svcSugTimer=null;

  function svcPintarIcones(){
    var box=document.getElementById('svc-icones'); if(!box)return;
    var fixo=(document.getElementById('svc-icone')||{}).value||'';
    var aceso=fixo||svcIconeSugerido;
    if(!box.childElementCount){
      PALETA.forEach(function(ic){
        var b=document.createElement('div');
        b.className='op'; b.setAttribute('data-k',ic.chave); b.title=ic.rotulo;
        b.innerHTML=ic.svg;
        b.addEventListener('click',function(){
          // clicou = FIXOU: daqui pra frente o nome pode mudar que o ícone não muda
          document.getElementById('svc-icone').value=ic.chave;
          svcPintarIcones();
        });
        box.appendChild(b);
      });
    }
    box.querySelectorAll('.op').forEach(function(x){
      x.classList.toggle('on', x.getAttribute('data-k')===aceso);
    });
  }

  function svcSugerirIcone(){
    var nome=(document.getElementById('svc-nome')||{}).value||'';
    var cat=((document.getElementById('svc-cat')||{}).value)||'';
    fetch('/painel/servicos/catalogo/icone-sugerido?nome='+encodeURIComponent(nome)
          +'&categoria='+encodeURIComponent(cat))
      .then(function(r){return r.json();})
      .then(function(d){ svcIconeSugerido=(d&&d.chave)||'outros'; svcPintarIcones(); })
      .catch(function(){});
  }

  var svcNome=document.getElementById('svc-nome');
  if(svcNome)svcNome.addEventListener('input',function(){
    clearTimeout(svcSugTimer); svcSugTimer=setTimeout(svcSugerirIcone,350);
  });
  var svcCat=document.getElementById('svc-cat');
  if(svcCat)svcCat.addEventListener('change',svcSugerirIcone);

  function abrirForm(s){
    s=s||{};
    document.getElementById('svc-id').value=s.id||'';
    var cat=document.getElementById('svc-cat');
    if(cat)cat.value=s.categoria||'';
    var ico=document.getElementById('svc-icone');
    if(ico){ico.value=s.icone||''; svcIconeSugerido='outros'; svcPintarIcones(); svcSugerirIcone();}
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
    var body={id:idv?parseInt(idv,10):null,nome:nome,descricao:document.getElementById('svc-desc').value||'',setup:num(document.getElementById('svc-setup')),mensal:num(document.getElementById('svc-mensal')),custo:num(document.getElementById('svc-custo')),categoria:((document.getElementById('svc-cat')||{}).value)||'',icone:((document.getElementById('svc-icone')||{}).value||'').trim()};
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
    // Rótulo diz UM documento, não os dois. E a dica do preenchimento automático
    // some no CPF: a consulta só existe pra CNPJ, então prometer isso pra pessoa
    // física era promessa vazia.
    var lbl=document.getElementById('oc-cnpj-label');
    if(lbl)lbl.childNodes[0].nodeValue=pj?'CNPJ ':'CPF ';
    var dica=document.getElementById('oc-cnpj-dica');
    if(dica)dica.style.display=pj?'inline':'none';
    document.getElementById('oc-empresa-label').textContent=pj?'Empresa':'Nome completo';
    document.getElementById('oc-empresa').placeholder=pj?'Nome da empresa':'Nome completo';
    // Cargo/Sócio/Telefone/Site/Segmento ficam ocultos sempre pra eventos (não é
    // sobre PJ×PF — esse segmento não precisa desses campos, ponto), então esse
    // toggle não mexe mais na visibilidade deles.
  }
  var btnTipoPj=document.getElementById('btn-tipo-pj'), btnTipoPf=document.getElementById('btn-tipo-pf');
  if(btnTipoPj)btnTipoPj.addEventListener('click',function(){aplicaTipoCliente('pj');});
  if(btnTipoPf)btnTipoPf.addEventListener('click',function(){aplicaTipoCliente('pf');});
  // abre no tipo que esta empresa mais cadastra (ver clientes.tipo_predominante)
  aplicaTipoCliente('{{ tipo_padrao|default("pj") }}');

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
  /* De qual lead é esta proposta. Vazio = proposta sem vínculo, que continua
     permitida — é o que o link "cadastrar um cliente novo, sem vínculo com lead"
     oferece de propósito. */
  var LEAD_ID=(typeof EDIT_LEAD_ID!=='undefined'?EDIT_LEAD_ID:null);
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
      /* O ID DO LEAD ERA JOGADO FORA AQUI. A tela copiava nome, telefone e e-mail e
         esquecia de QUEM era — e sem isso a proposta nasce solta, o gatilho
         "orçamento enviado" não acha card nenhum pra mover, e alguém arrasta na mão. */
      LEAD_ID=l.id||null;
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
    LEAD_ID=null;   /* cliente novo: não herda o vínculo de quem estava selecionado */
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
      var cat=CATALOGO.filter(function(x){return x.slug===r.getAttribute('data-id');})[0]||{};
      var par=r.querySelector('.oc-dpar');
      return {nome:r.getAttribute('data-nome'),desc:r.getAttribute('data-desc')||'',
              setup:u*q,mensal:num(r.querySelector('.oc-mensal')),qtd:q,unitario:u,
              categoria:cat.categoria||'',icone:cat.icone||'',
              desc_tipo:(par?(par.getAttribute('data-tipo')||'pct'):'pct'),
              desc_val:num(r.querySelector('.oc-desc'))};
    });
    var escEl=document.getElementById('oc-escopo-out');
    return {id:EDIT_ID,lead_id:LEAD_ID,cliente:document.getElementById('oc-contato').value||'',empresa:document.getElementById('oc-empresa').value||'',cnpj:document.getElementById('oc-cnpj').value||'',segmento:document.getElementById('oc-segmento').value||'',whatsapp:document.getElementById('oc-whats').value||'',email:document.getElementById('oc-email').value||'',telefone:document.getElementById('oc-tel').value||'',cidade:document.getElementById('oc-cidade').value||'',uf:document.getElementById('oc-uf').value||'',site:document.getElementById('oc-site').value||'',cargo:document.getElementById('oc-cargo').value||'',socio:document.getElementById('oc-socio').value||'',endereco:(document.getElementById('oc-endereco')||{}).value||'',cep:(document.getElementById('oc-cep')||{}).value||'',modulos:sel.map(function(r){return r.getAttribute('data-id');}),itens:itens,evento:coletarEvento(),parcelas:(SERVICO_AVULSO?coletarParcelas():[]),escopo:(escEl.getAttribute('data-escopo')||''),setup:Math.round(c.setupBruto),mensal:Math.round(c.mensalBruto),primeiro_ano:Math.round(c.ano1),n_modulos:c.mods,desconto_tipo:descTipoTot(),desconto_pct:(descTipoTot()==='pct'?num(document.getElementById('oc-desconto')):0),desconto_valor:(descTipoTot()==='valor'?num(document.getElementById('oc-desconto')):0)};
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
          if(m&&it.mensal!=null) m.value=it.mensal;
          // O DESCONTO DA LINHA VOLTA JUNTO. Sem isto, reabrir a proposta pra
          // trocar uma vírgula zeraria silenciosamente o desconto negociado — e o
          // cliente receberia um link mais caro que o que ele aprovou.
          var di=r.querySelector('.oc-desc'), par=r.querySelector('.oc-dpar');
          if(di) di.value=(it.desc_val!=null?it.desc_val:0);
          if(par){
            var t=(it.desc_tipo==='valor')?'valor':'pct';
            par.setAttribute('data-tipo',t);
            par.querySelectorAll('.oc-dtog button').forEach(function(x){
              x.classList.toggle('on',x.getAttribute('data-t')===t);
            });
          } }
      });
      // e o desconto do TOTAL, pelo mesmo motivo
      var dtPar=document.querySelector('.oc-dpar-tot');
      if(dtPar){
        var dt=(d.desconto_tipo==='valor')?'valor':'pct';
        dtPar.setAttribute('data-tipo',dt);
        dtPar.querySelectorAll('.oc-dtog button').forEach(function(x){
          x.classList.toggle('on',x.getAttribute('data-t')===dt);
        });
        setv('oc-desconto', String(dt==='valor'?(d.desconto_valor||0):(d.desconto_pct||0)));
      }
      var out=document.getElementById('oc-escopo-out');
      if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
      else{out.style.display='none'; out.removeAttribute('data-escopo');}
      var bn=document.getElementById('oc-editando');
      bn.style.display='flex';
      var aviso=(d.status==='aprovada')?' · ⚠ editar vai pedir nova aprovação do cliente':'';
      var quando=d.gerado_em?(' · gerada em '+d.gerado_em):'';
      bn.querySelector('.t').textContent='Editando proposta #'+d.id+' · '+d.status+quando+aviso+' — salve pra atualizar o link do cliente.';
      if(SERVICO_AVULSO)atualizarChip();
      pinta();
      window.scrollTo({top:0,behavior:'smooth'});
    }).catch(function(){alert('Erro de conexão.');});
  }
  document.getElementById('oc-novo').addEventListener('click',novo);
  function fechar(id,btn,dif){
    if(!confirm(SERVICO_AVULSO?'Fechar este contrato? Cada parcela do plano de pagamento vira um título a receber no módulo Empresa (sem plano, gera um título com o total).':'Fechar este contrato? Vai gerar título a receber (setup + mensalidade) no módulo Empresa.')){return;}
    // É AQUI que o plano vira dinheiro a receber. Se ele não fecha com o total, o
    // financeiro vai cobrar um valor diferente do que o cliente assinou — então a
    // segunda pergunta é sobre o número, não sobre a ação.
    if(dif && !confirm('Atenção: o plano de pagamento não fecha com o total do orçamento — '
        +(dif>0?'as parcelas passam R$ ':'faltam R$ ')+fmtc(Math.abs(dif))+'.\n\n'
        +'Os títulos a receber vão sair pelo valor das PARCELAS. Fechar assim mesmo?')){return;}
    btn.disabled=true; btn.textContent='Fechando...';
    fetch('/painel/servicos/fechar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui fechar.'); btn.disabled=false; btn.textContent='Fechar contrato'; return;}
        carregarHist();
      })
      .catch(function(){alert('Erro de conexão.'); btn.disabled=false; btn.textContent='Fechar contrato';});
  }
  // Confirma que o sinal caiu: a data segurada vira compromisso firme na agenda.
  // Confirma antes porque é dinheiro — e nomeia o cliente pelo mesmo motivo do
  // 🗑 logo abaixo: a lista é densa e "tem certeza?" não diz qual linha é.
  function sinalRecebido(id,nome,btn){
    if(!confirm('Confirmar que o sinal de '+nome+' foi recebido?\\n\\nA data deixa de ser provisória e vira compromisso firme na agenda. Se o contrato já estiver fechado, o título dessa parcela entra como recebido no livro-caixa, com a data de hoje.')){return;}
    btn.disabled=true; btn.textContent='Confirmando...';
    fetch('/painel/servicos/sinal-recebido',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui confirmar.'); btn.disabled=false; btn.textContent='Sinal recebido'; return;}
        // o pagamento gravou; se a agenda não firmou, dizemos — o botão volta e
        // apertar de novo só tenta a agenda (o sinal já está registrado).
        if(res.d&&res.d.reserva_firmada===false&&!res.d.ja_estava){
          alert('Sinal registrado, mas não consegui firmar o compromisso na agenda. Confira a data por lá.');
        }
        carregarHist();
        // O MOMENTO EM QUE O COMPROVANTE ESTÁ NA MÃO. O Pix acabou de cair e o
        // print está no celular — abrir a tela agora é o que evita o comprovante
        // que ninguém anexa e vira pendência âmbar semana que vem.
        abrirPagamentos(id,nome);
        pgMsg('Sinal confirmado. Se tiver o comprovante aí, anexa agora — '
             +'depois vira caça ao print.','ok');
      })
      .catch(function(){alert('Erro de conexão.'); btn.disabled=false; btn.textContent='Sinal recebido';});
  }
  // Põe na agenda a data que ficou de fora, ou segura de novo a que foi liberada.
  // Sem confirmação de propósito: marcar uma data que deveria estar marcada não
  // destrói nada, e o botão só aparece quando há de fato o que consertar.
  function marcarData(id,btn){
    var t=btn.textContent; btn.disabled=true; btn.textContent='Marcando...';
    fetch('/painel/servicos/marcar-data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui marcar.'); btn.disabled=false; btn.textContent=t; return;}
        carregarHist();
      })
      .catch(function(){alert('Erro de conexão.'); btn.disabled=false; btn.textContent=t;});
  }
  // ---------------------------------------------- mandar a proposta por e-mail
  //
  // O funil sabia gerar o link e abrir o PDF; mandar era por fora, na mão. A tela
  // abre PREENCHIDA — quem só quer mandar abre e aperta Enviar. Por qual caixa vai
  // sair é dito antes, porque o mesmo botão se comporta diferente na empresa que
  // tem caixa configurada e na que não tem (ver finance/proposta_email).
  var ENV_ID = null, ENV_LINK = '', ENV_ALVO = 'proposta';
  function envMsg(txt, cls){
    var el=document.getElementById('env-msg');
    el.className='env-msg'+(txt?(' on '+(cls||'amb')):'');
    el.innerHTML=txt||'';
  }
  function envFechar(){
    document.getElementById('env-fundo').classList.remove('on');
    ENV_ID=null; ENV_LINK=''; ENV_ALVO='proposta';
  }
  function abrirEnvio(id,alvo){
    ENV_ID=id; ENV_ALVO=alvo||'proposta';
    var fundo=document.getElementById('env-fundo');
    envMsg('');
    document.getElementById('env-hist').textContent='';
    document.getElementById('env-de').textContent='Carregando...';
    ['env-para','env-assunto','env-texto'].forEach(function(k){
      document.getElementById(k).value='';});
    fundo.classList.add('on');
    fetch('/painel/servicos/email/'+id+'?alvo='+encodeURIComponent(ENV_ALVO))
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){envMsg(esc((res.d&&res.d.erro)||'Não consegui abrir.'),'cor'); return;}
        var d=res.d;
        ENV_LINK=d.link||'';
        document.getElementById('env-tt').textContent=
          (ENV_ALVO==='contrato'?'Mandar pra assinar — ':'Mandar ')+esc(d.assunto||'a proposta');
        document.getElementById('env-para').value=d.para||'';
        document.getElementById('env-assunto').value=d.assunto||'';
        document.getElementById('env-texto').value=d.mensagem||'';
        var rem=d.remetente||{};
        var de=document.getElementById('env-de');
        if(rem.caixa==='própria'){
          de.innerHTML='<span>📮</span><span>Sai de <b>'+esc(d.empresa)+' &lt;'+esc(rem.endereco)+'&gt;</b>'
            +'<span style="display:block;opacity:.85;margin-top:.15rem">A resposta do cliente cai nessa mesma caixa.</span></span>';
        } else {
          de.innerHTML='<span>📮</span><span>Sai pelo Zaq, assinado como <b>'+esc(d.empresa)+'</b>'
            +'<span style="display:block;opacity:.85;margin-top:.15rem">'
            +(rem.reply_to?('A resposta volta pra <b>'+esc(rem.reply_to)+'</b>. '):'')
            +'Pra sair da sua própria caixa, configure em Canais → E-mail.</span></span>';
        }
        var n=(d.envios||[]).filter(function(e){return e.ok;});
        document.getElementById('env-hist').textContent = n.length
          ? ('enviado '+n.length+'× · último '+n[0].quando) : '';
        // sem e-mail do cliente o campo abre focado: é o único que falta preencher
        if(!d.para){
          envMsg('Este orçamento não tem o e-mail do cliente. <span style="opacity:.85">'
                +'Escreva aqui e ele fica salvo no orçamento — da próxima vez já vem preenchido.</span>','amb');
          document.getElementById('env-para').focus();
        }
      })
      .catch(function(){envMsg('Erro de conexão.','cor');});
  }
  function enviarEmail(){
    if(!ENV_ID) return;
    var b=document.getElementById('env-enviar'), t=b.textContent;
    b.disabled=true; b.textContent='Enviando...';
    fetch('/painel/servicos/enviar-email',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:ENV_ID, alvo:ENV_ALVO,
        para:document.getElementById('env-para').value,
        assunto:document.getElementById('env-assunto').value,
        mensagem:document.getElementById('env-texto').value})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        b.disabled=false; b.textContent=t;
        if(!res.ok){
          // O LINK VAI JUNTO DO ERRO. O vendedor tem um cliente esperando: mandar
          // pelo WhatsApp resolve o dia dele enquanto a caixa se conserta.
          var extra=(res.d&&res.d.link)
            ? '<span style="display:block;opacity:.9;margin-top:.3rem">O link continua valendo — '
              +'<a href="#" id="env-copiar" style="color:inherit;text-decoration:underline">copiar</a>'
              +' e mandar pelo WhatsApp.</span>' : '';
          envMsg(esc((res.d&&res.d.erro)||'Não consegui enviar.')+extra,'cor');
          var cp=document.getElementById('env-copiar');
          if(cp) cp.addEventListener('click',function(ev){
            ev.preventDefault(); navigator.clipboard.writeText(ENV_LINK); cp.textContent='copiado ✓';});
          return;
        }
        envMsg('✓ Enviado para '+esc(res.d.para||'')+'.','ok');
        carregarHist();
        setTimeout(envFechar, 1400);
      })
      .catch(function(){b.disabled=false; b.textContent=t; envMsg('Erro de conexão.','cor');});
  }
  document.getElementById('env-enviar').addEventListener('click',enviarEmail);
  document.getElementById('env-cancelar').addEventListener('click',envFechar);
  document.getElementById('env-x').addEventListener('click',envFechar);
  document.getElementById('env-fundo').addEventListener('click',function(ev){
    if(ev.target===this) envFechar();});
  document.addEventListener('keydown',function(ev){
    if(ev.key==='Escape' && ENV_ID) envFechar();});

  // ------------------------------------------------------- a ação e o menu
  //
  // A LINHA CHEGOU A MOSTRAR QUATORZE COISAS: seis selos e oito ícones sem
  // rótulo. Cada uma entrou por um bom motivo — e juntas viraram uma parede onde
  // nada se destacava. Cinco ícones faziam "abrir ou mandar documento", pra dois
  // documentos diferentes, e o 🗑 tinha o mesmo tamanho e a mesma cor do 📄.
  //
  // Agora: os avisos de pendência ficam à vista, uma ação principal com o nome do
  // que falta, e o resto atrás do "Ações ▾".
  function acaoDaLinha(it, chave, btn){
    if(chave==='sinal')       return sinalRecebido(it.id,it.cliente,btn);
    if(chave==='marcar')      return marcarData(it.id,btn);
    if(chave==='resegurar')   return marcarData(it.id,btn);
    if(chave==='comprovante') return abrirPagamentos(it.id,it.cliente);
    if(chave==='enviar')      return abrirEnvio(it.id);
    if(chave==='fechar')      return fechar(it.id,btn,it.plano_difere);
    // mesma tela de envio da proposta, só que com o link do contrato
    if(chave==='assinar')     return abrirEnvio(it.id,'contrato');
  }

  var _menuAberto=null;
  function fecharMenuLinha(){
    if(_menuAberto){_menuAberto.remove();_menuAberto=null;}
  }
  document.addEventListener('click',fecharMenuLinha);
  document.addEventListener('keydown',function(ev){if(ev.key==='Escape')fecharMenuLinha();});

  function _mi(texto, emoji, sufixo, aoClicar, classe){
    var b=document.createElement('button');
    b.className='oc-mi'+(classe?(' '+classe):'');
    b.innerHTML='<span class="e">'+emoji+'</span><span class="t">'+esc(texto)+'</span>'
      +(sufixo?('<span class="s">'+esc(sufixo)+'</span>'):'');
    b.addEventListener('click',function(ev){ev.stopPropagation();fecharMenuLinha();aoClicar();});
    return b;
  }
  function _mgrupo(texto){
    var d=document.createElement('div'); d.className='oc-mgrupo'; d.textContent=texto; return d;
  }

  function abrirMenuLinha(it, ancora){
    fecharMenuLinha();
    var m=document.createElement('div'); m.className='oc-menu'; m.setAttribute('role','menu');
    m.addEventListener('click',function(ev){ev.stopPropagation();});
    var fechado=it.status==='fechado', aprovada=it.status==='aprovada';
    var origem=window.location.origin;

    // AGRUPADO POR DOCUMENTO. É o que resolve o "qual link é de qual": antes o 🔗
    // copiava o da proposta e o 📜 o do contrato, e ninguém tinha como saber.
    m.appendChild(_mgrupo('Proposta'+(it.numero?(' nº '+it.numero):'')));
    if(!fechado){
      m.appendChild(_mi('Editar','✏️','',function(){abrir(it.id);}));
    }
    if(it.token){
      m.appendChild(_mi('Abrir / imprimir','📄','',function(){
        window.open('/proposta/'+it.token,'_blank');}));
      m.appendChild(_mi('Copiar link','🔗','',function(){
        navigator.clipboard.writeText(origem+'/proposta/'+it.token);}));
      m.appendChild(_mi('Mandar por e-mail','✉️',it.enviado_em?('enviada '+it.enviado_em):'',
        function(){abrirEnvio(it.id);}));
    }
    if(it.contrato_token){
      m.appendChild(_mgrupo('Contrato nº '+it.contrato_numero));
      m.appendChild(_mi('Abrir','📜',it.contrato_assinado?'assinado':'aguardando',function(){
        window.open('/contrato/'+it.contrato_token,'_blank');}));
      m.appendChild(_mi('Copiar link','🔗','',function(){
        navigator.clipboard.writeText(origem+'/contrato/'+it.contrato_token);}));
    }
    if(it.pgto&&it.pgto.total){
      m.appendChild(_mgrupo('Dinheiro'));
      m.appendChild(_mi('Pagamentos e comprovantes','📎',
        it.pgto.pagas+' de '+it.pgto.total, function(){abrirPagamentos(it.id,it.cliente);}));
    }
    // APAGAR FICA SOZINHO, ATRÁS DE UMA LINHA E EM CORAL. Era um 🗑 do mesmo
    // tamanho e da mesma cor do 📄, encostado nele.
    if(!fechado && !aprovada){
      m.appendChild(_mi('Apagar proposta','🗑','',function(){
        excluir(it.id,it.cliente,ancora);},'sep perigo'));
    }

    ancora.parentNode.appendChild(m);
    _menuAberto=m;
  }

  // ------------------------------------------------ pagamentos e comprovantes
  //
  // O comprovante é da PARCELA. Um botão só na linha não saberia de qual —
  // então o 📎 abre a lista, e o upload é por linha.
  //
  // O vendedor ABRE e VÊ (é ele que cobra o cliente); anexar é do dono e do
  // gestor. Quem decide é o servidor — `pode_anexar` aqui só evita oferecer um
  // botão que vai voltar 403.
  var PG_ID=null, PG_IDX=null;
  function pgMsg(txt, cls){
    var el=document.getElementById('pg-msg');
    el.className='env-msg'+(txt?(' on '+(cls||'amb')):'');
    el.innerHTML=txt||'';
  }
  function pgFechar(){
    document.getElementById('pg-fundo').classList.remove('on');
    PG_ID=null; PG_IDX=null;
  }
  function abrirPagamentos(id,nome){
    PG_ID=id;
    pgMsg('');
    document.getElementById('pg-tt').textContent='Pagamentos'+(nome?(' · '+nome):'');
    document.getElementById('pg-tot').innerHTML='';
    document.getElementById('pg-lista').innerHTML='<p class="mut" style="font-size:.85rem">Carregando...</p>';
    document.getElementById('pg-fundo').classList.add('on');
    pgCarregar();
  }
  function pgCarregar(){
    fetch('/painel/servicos/pagamentos/'+PG_ID)
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){pgMsg(esc((res.d&&res.d.erro)||'Não consegui abrir.'),'cor'); return;}
        var d=res.d;
        document.getElementById('pg-tot').innerHTML=
          '<span class="ok">Recebido <b>'+esc(d.recebido)+'</b></span>'
         +'<span class="fl">Falta <b>'+esc(d.falta)+'</b></span>'
         +'<span>Total <b>'+esc(d.total)+'</b></span>';
        var box=document.getElementById('pg-lista');
        box.innerHTML='';
        (d.parcelas||[]).forEach(function(p){
          var el=document.createElement('div');
          el.className='pl'+(p.pago?' paga':(p.vence_hoje?' hoje':''));
          var quando = p.pago ? ('pago em '+esc(p.pago_em||'—')) :
                       (p.venc?('vence '+esc(p.venc)):'sem vencimento');
          var falta = p.pago && !p.comprovante_id;
          var meta = quando + (p.forma?(' · '+esc(p.forma)):'')
                   + (falta?' · <b style="color:var(--amar)">sem comprovante</b>':'');
          el.innerHTML='<div class="bar"></div>'
            +'<div><div class="tt">'+esc(p.rotulo)+'</div><div class="mt">'+meta+'</div></div>'
            +'<div class="vl">'+esc(p.valor)+'</div>';
          var ac=document.createElement('div'); ac.className='ac';
          if(p.comprovante_id){
            var a=document.createElement('a'); a.className='pmini ver';
            a.href='/painel/servicos/comprovante/'+p.comprovante_id;
            a.target='_blank'; a.rel='noopener'; a.textContent='📎 ver';
            a.title=p.comprovante_nome||'Abrir o comprovante';
            ac.appendChild(a);
          }
          if(d.pode_anexar){
            var b=document.createElement('button');
            b.className='pmini'+(p.comprovante_id?'':' up');
            b.textContent=p.comprovante_id?'trocar':'📎 anexar';
            b.addEventListener('click',function(){pgEscolher(p.idx);});
            ac.appendChild(b);
          }
          el.appendChild(ac);
          box.appendChild(el);
        });
        if(!(d.parcelas||[]).length){
          box.innerHTML='<p class="mut" style="font-size:.85rem">Este orçamento não tem plano de pagamento.</p>';
        }
        if(d.sem_storage){
          pgMsg('O guardador de arquivos não está configurado nesta instalação, '
               +'então não dá pra anexar comprovante ainda.','amb');
        }
      })
      .catch(function(){pgMsg('Erro de conexão.','cor');});
  }
  function pgEscolher(idx){
    PG_IDX=idx;
    var inp=document.getElementById('pg-arquivo');
    inp.value='';            // escolher o MESMO arquivo de novo tem que disparar
    inp.click();
  }
  document.getElementById('pg-arquivo').addEventListener('change',function(){
    var f=this.files&&this.files[0];
    if(!f||PG_ID===null||PG_IDX===null) return;
    pgMsg('Enviando '+esc(f.name)+'...','amb');
    var fd=new FormData();
    fd.append('orcamento_id',PG_ID); fd.append('parcela_idx',PG_IDX); fd.append('arquivo',f);
    fetch('/painel/servicos/comprovante',{method:'POST',body:fd})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){pgMsg(esc((res.d&&res.d.erro)||'Não consegui anexar.'),'cor'); return;}
        pgMsg('✓ Comprovante anexado.','ok');
        pgCarregar();       // a linha vira "ver" sozinha
        carregarHist();     // e o selo do funil acompanha
      })
      .catch(function(){pgMsg('Erro de conexão.','cor');});
  });
  document.getElementById('pg-x').addEventListener('click',pgFechar);
  document.getElementById('pg-fundo').addEventListener('click',function(ev){
    if(ev.target===this) pgFechar();});
  document.addEventListener('keydown',function(ev){
    if(ev.key==='Escape' && PG_ID!==null) pgFechar();});

  // Apagar proposta do funil. Confirma sempre e nomeia o cliente na pergunta: a
  // lista é densa e o 🗑 fica ao lado do 📄, então "tem certeza?" sozinho não diz
  // qual das cinco linhas vai embora.
  function excluir(id,nome,btn){
    if(!confirm('Apagar a proposta de '+nome+'? Isso não tem volta.')){return;}
    btn.disabled=true;
    fetch('/painel/servicos/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
      .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
      .then(function(res){
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui apagar.'); btn.disabled=false; return;}
        carregarHist();
      })
      .catch(function(){alert('Erro de conexão.'); btn.disabled=false;});
  }
  function carregarHist(){
    fetch('/painel/servicos/lista').then(function(r){return r.json();}).then(function(d){
      var box=document.getElementById('oc-hist-box');
      if(!d.itens||!d.itens.length){box.innerHTML='<p class="mut">Nenhuma proposta no funil ainda.</p>'; return;}
      box.innerHTML='';
      d.itens.forEach(function(it){
        var el=document.createElement('div'); el.className='oc-hist';
        var fechado=it.status==='fechado', aprovada=it.status==='aprovada';
        var pn=it.painel||{selos:[],acao:null,resumo:''};
        var evento=it.modo==='evento';

        // ESQUERDA: quem, e o que JÁ ACONTECEU. O resumo em cinza é onde mora o
        // "aprovada · sinal recebido · data reservada" — continua visível, para de
        // gritar. Antes cada um desses era um selo colorido próprio, e uma proposta
        // sem pendência nenhuma carregava cinco caixinhas verdes dizendo que estava
        // tudo bem: era esse ruído que fazia os selos de VERDADE sumirem no meio.
        var left=document.createElement('div'); left.className='oc-hist-open';
        left.title='Abrir proposta';
        // O TÍTULO vem PRONTO do servidor (vendas.titulo_do_funil). Aqui ficava
        // `it.cliente + " · " + it.empresa` — dois campos livres pra mesma coisa,
        // que davam "−", telefone cru e nome repetido em 23 dos 26 orçamentos.
        // `it.sub` é a festa (modo evento) ou o contato (recorrente).
        var sub=[esc(it.sub||''), (it.numero?('nº '+it.numero):''),
                 (it.data?('gerada '+esc(it.data)):''),
                 esc(evento?it.setup:it.total), esc(pn.resumo||'')]
                .filter(Boolean).join(' · ');
        left.innerHTML='<div class="oc-av">'+esc(it.inicial)+'</div>'
          +'<div style="min-width:0"><b>'+esc(it.titulo)+'</b>'
          +'<div class="mut" style="font-size:.78rem">'+sub+'</div></div>';
        left.addEventListener('click',function(){abrir(it.id);});
        el.appendChild(left);

        var right=document.createElement('div'); right.className='oc-acoes';

        // O TELEFONE saiu do título (era ele que aparecia como nome em 2 das 26
        // linhas) e virou atalho: abre a conversa no WhatsApp. Sumir de vez seria
        // perder o caminho mais curto pro cliente que está ali na linha.
        if(it.zap_link){
          var zap=document.createElement('a');
          zap.className='oc-zap'; zap.target='_blank'; zap.rel='noopener';
          zap.href=it.zap_link;
          zap.title='Falar no WhatsApp'; zap.setAttribute('aria-label','Falar no WhatsApp');
          zap.textContent='💬';
          zap.addEventListener('click',function(e){e.stopPropagation();});
          right.appendChild(zap);
        }

        // DIREITA: só o que está PENDENTE. Selo colorido virou sinônimo de "tem
        // coisa a fazer" — quem não tem nada mostra um ✓ discreto, e some.
        (pn.selos||[]).forEach(function(sl){
          var b=document.createElement('span');
          b.className='oc-badge pend '+esc(sl.tom||'azul');
          b.textContent=sl.texto; b.title=sl.dica||'';
          right.appendChild(b);
        });
        if(!(pn.selos||[]).length){
          var okz=document.createElement('span'); okz.className='oc-nada';
          okz.textContent='✓ nada pendente';
          right.appendChild(okz);
        }

        // A AÇÃO. Uma só, com o nome do que falta — verde deixa de ser enfeite e
        // passa a significar "é isto". As outras pendências continuam nos selos:
        // some o botão, não o aviso.
        if(pn.acao){
          var ba=document.createElement('button'); ba.className='oc-fechar';
          ba.textContent=pn.acao.texto;
          ba.addEventListener('click',function(){acaoDaLinha(it,pn.acao.chave,ba);});
          right.appendChild(ba);
        }

        // AÇÕES ▾ — com a palavra escrita. Eram OITO ícones sem rótulo (✏️ 🔗 📄
        // ✉️ 📎 📜 ↗ 🗑), cinco deles "abrir ou mandar documento" pra DOIS
        // documentos diferentes, e nada dizia qual era de qual.
        var bmenu=document.createElement('button'); bmenu.className='oc-menu-btn';
        bmenu.innerHTML='Ações <span class="cv">▾</span>';
        bmenu.setAttribute('aria-haspopup','menu');
        bmenu.addEventListener('click',function(ev){ev.stopPropagation();abrirMenuLinha(it,bmenu);});
        right.appendChild(bmenu);

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
    if(!confirmaDivergencia('O cliente vai receber a proposta assim. Gerar mesmo assim?')){return;}
    var w=window.open('about:blank','_blank');
    if(w){try{w.document.write('<p style="font-family:system-ui;color:#8A8475;padding:24px">Gerando proposta…</p>');}catch(e){}}
    var btn=this, t0=btn.textContent; btn.textContent='Gerando...';
    salvarProposta(function(d){
      btn.textContent=t0; carregarHist();
      if(!d||!d.token){ if(w)w.close(); alert('Não consegui gerar a proposta.'); return; }
      if(w){ w.location='/proposta/'+d.token; } else { window.location='/proposta/'+d.token; }
    });
  });

  // ------------------------------------------------------------ contrato
  // Só existe no nicho de eventos; o card nem é renderizado nos outros, então a
  // ausência do #ct-box é a checagem.
  var ctBox=document.getElementById('ct-box');
  if(ctBox){ (function(){
    var CAMPOS=[], REGRAS={}, ultimo=null;   // ultimo = textarea que teve foco
    var corpo=document.getElementById('ct-corpo'), seta=document.getElementById('ct-seta');
    var LEMBRA='zaq_ct_aberto';

    // Recolhido é o estado normal: o contrato se escreve uma vez, e esta é a
    // tela do DIA A DIA (montar orçamento, ver o funil). Duas exceções, e as
    // duas são sobre não esconder trabalho de quem tem trabalho a fazer:
    // quem NUNCA configurou precisa achar isto sem procurar a seta, e quem
    // abriu nesta sessão está mexendo agora — recolher a cada F5 seria briga.
    function abrir(v){
      corpo.style.display = v ? 'block' : 'none';
      seta.style.transform = v ? 'rotate(90deg)' : '';
      try{ v ? localStorage.setItem(LEMBRA,'1') : localStorage.removeItem(LEMBRA); }catch(e){}
    }
    document.getElementById('ct-cab').addEventListener('click',function(){
      abrir(corpo.style.display==='none');
    });
    // o aviso leva ao conserto: apontar o erro e deixar a pessoa procurar a porta
    // seria metade do trabalho.
    document.getElementById('ct-faltas').addEventListener('click',function(){abrir(true);});

    function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

    function clausulas(){
      return [].slice.call(ctBox.querySelectorAll('.ct-cl')).map(function(el){
        return {titulo:el.querySelector('.ct-t').value, corpo:el.querySelector('.ct-c').value};
      });
    }
    function regras(){
      var r={};
      [].slice.call(ctBox.querySelectorAll('.ct-rg')).forEach(function(i){
        r[i.getAttribute('data-k')]=i.value;
      });
      return r;
    }

    // Inserir campo no ponto do cursor — e não no fim do texto: o dono está
    // escrevendo a frase, e "de {preco.hora-extra} por hora" precisa cair no meio.
    function inserir(campo){
      var ta=ultimo||ctBox.querySelector('.ct-c');
      if(!ta){return;}
      var a=ta.selectionStart||0, b=ta.selectionEnd||0, txt='{'+campo+'}';
      ta.value=ta.value.slice(0,a)+txt+ta.value.slice(b);
      ta.focus(); ta.selectionStart=ta.selectionEnd=a+txt.length;
    }

    function linhaClausula(c){
      var d=document.createElement('div');
      d.className='ct-cl';
      d.style.cssText='border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.5rem;background:var(--card-2)';
      d.innerHTML=
        '<div style="display:flex;gap:.4rem;align-items:center;margin-bottom:.35rem">'
        +'<input class="ct-t oc-inp" style="flex:1;font-weight:600" value="'+esc(c.titulo)+'" placeholder="Título da cláusula">'
        +'<button type="button" class="ct-up oc-pill" title="Subir">↑</button>'
        +'<button type="button" class="ct-dn oc-pill" title="Descer">↓</button>'
        +'<button type="button" class="ct-rm oc-pill" title="Remover">✕</button></div>'
        +'<textarea class="ct-c oc-inp" rows="4" style="width:100%;font-family:var(--mono);font-size:.8rem;line-height:1.6" placeholder="Texto da cláusula. Use os campos abaixo para os valores.">'+esc(c.corpo)+'</textarea>';
      d.querySelector('.ct-c').addEventListener('focus',function(){ultimo=this;});
      d.querySelector('.ct-rm').addEventListener('click',function(){
        if(confirm('Remover esta cláusula?')) d.remove();});
      d.querySelector('.ct-up').addEventListener('click',function(){
        var p=d.previousElementSibling; if(p&&p.classList.contains('ct-cl')) d.parentNode.insertBefore(d,p);});
      d.querySelector('.ct-dn').addEventListener('click',function(){
        var n=d.nextElementSibling; if(n&&n.classList.contains('ct-cl')) d.parentNode.insertBefore(n,d);});
      return d;
    }

    var ROTULO_REGRA={sinal_pct:'Entrada (%)',multa_cancelamento:'Multa de cancelamento (%)',
      taxa_reagendamento:'Taxa de reagendamento (%)',duracao_horas:'Horas de evento',
      tolerancia_min:'Tolerância (min)',quitacao_dias:'Quitar até (dias antes)',
      reagenda_dias:'Remarcar com (dias)',reagenda_prazo:'Nova data em até (dias)',
      retirada_horas:'Retirar materiais (h)',acesso_montagem:'Montagem a partir de'};

    // O resumo do card fechado. Responde "está no ar e é o meu?" sem abrir —
    // e o selo âmbar conta os ajustes pendentes, que é o único erro deste fluxo
    // que estrearia na frente do cliente, dentro do contrato dele.
    function resumir(d){
      var r=d.resumo||{}, res=document.getElementById('ct-resumo'), selo=document.getElementById('ct-selo');
      if(d.novo){
        res.textContent='Ainda não configurado — comece pelo modelo abaixo.';
        selo.innerHTML=''; return;
      }
      res.textContent=(r.n||0)+' cláusula'+((r.n||0)===1?'':'s')
        +(r.em?' · alterado em '+r.em:'')+(r.por?' por '+r.por:'');
      var lista=(r.ajustes||[]), f=lista.length;
      selo.innerHTML = f
        ? '<span style="font-size:.68rem;font-weight:700;background:var(--ambar-fundo);color:var(--amar);border:1px solid var(--ambar-borda);border-radius:5px;padding:.1rem .38rem;white-space:nowrap">⚠ '+f+(f===1?' ajuste':' ajustes')+'</span>'
        : '<span style="font-size:.68rem;font-weight:700;background:var(--neon-fundo);color:var(--verde-claro);border:1px solid var(--neon-borda);border-radius:5px;padding:.1rem .38rem;white-space:nowrap">✓ pronto</span>';
      pintarAjustes(lista);
    }

    // DIZ O QUE FAZER, não o nome do campo. "1 campo sem valor" mandava o dono
    // caçar; "{cliente.nome}" pior ainda — ele não escreveu aquilo e não sabe o
    // que é. Cada linha aqui é uma tarefa com endereço.
    //
    // E entra pouca coisa: só o que ELE resolve e que vale pra todo contrato.
    // Campo que vem de cada proposta ({cliente.nome} vazio num orçamento antigo)
    // não é defeito e não aparece aqui — ver finance/contrato.diagnostico.
    function pintarAjustes(lista){
      var el=document.getElementById('ct-faltas');
      if(!el) return;
      if(!lista.length){ el.style.display='none'; el.innerHTML=''; return; }
      // teto de 4: lista longa no card fechado vira parede de texto e ninguém lê.
      var itens=lista.slice(0,4).map(function(a){
        return '<div style="margin-top:.22rem">• <b>'+esc(a.titulo)+'</b> '+esc(a.detalhe)+'</div>';
      }).join('');
      var resto=lista.length-4;
      el.innerHTML='<b>Precisa de ajuste antes de mandar pro cliente</b>'+itens
        +(resto>0?'<div style="opacity:.8;margin-top:.22rem">e mais '+resto+'</div>':'')
        +'<div style="opacity:.85;margin-top:.35rem">Toque para abrir e corrigir.</div>';
      el.style.display='block';
    }

    function desenhar(d){
      CAMPOS=d.campos||[]; REGRAS=d.regras||{};
      resumir(d);
      ctBox.innerHTML=
        (d.novo?'<p class="mut" style="font-size:.84rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:.5rem .7rem">Este é um modelo inicial de contrato de locação. Ajuste ao que a sua empresa pratica e salve.</p>':'')
        +'<div id="ct-lista"></div>'
        +'<button type="button" id="ct-add" class="oc-pill" style="margin-bottom:.9rem">+ Cláusula</button>'
        +'<div id="ct-campos" style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem">Campos — clique para inserir no texto</div>'
        +'<div id="ct-chips" style="display:flex;flex-wrap:wrap;gap:.3rem"></div></div>'
        +'<div style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.5rem">Números da casa — é daqui que os campos {regra.*} saem</div>'
        +'<div id="ct-regras" class="mini-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem"></div></div>'
        +'<div style="display:flex;gap:.45rem;flex-wrap:wrap"><button type="button" id="ct-salvar" class="oc-btn oc-btn-g" style="width:auto">Salvar contrato</button>'
        +'<button type="button" id="ct-previa" class="oc-pill">Pré-visualizar</button>'
        +'<button type="button" id="ct-padrao" class="oc-pill">Restaurar modelo padrão</button></div>'
        +'<div id="ct-msg" style="margin-top:.7rem"></div>';

      var lista=document.getElementById('ct-lista');
      (d.clausulas||[]).forEach(function(c){lista.appendChild(linhaClausula(c));});

      var chips=document.getElementById('ct-chips');
      CAMPOS.forEach(function(f){
        var b=document.createElement('button');
        b.type='button'; b.className='oc-pill';
        b.style.cssText='font-family:var(--mono);font-size:.7rem;padding:.15rem .4rem';
        b.textContent='{'+f.campo+'}';
        b.title=f.rotulo;
        b.addEventListener('click',function(){inserir(f.campo);});
        chips.appendChild(b);
      });

      var gr=document.getElementById('ct-regras');
      Object.keys(ROTULO_REGRA).forEach(function(k){
        var w=document.createElement('div');
        w.innerHTML='<label class="mut" style="font-size:.68rem">'+esc(ROTULO_REGRA[k])+'</label>'
          +'<input class="ct-rg oc-inp" data-k="'+k+'" style="width:100%" value="'+esc(REGRAS[k])+'">';
        gr.appendChild(w);
      });

      document.getElementById('ct-add').addEventListener('click',function(){
        lista.appendChild(linhaClausula({titulo:'',corpo:''}));});
      document.getElementById('ct-salvar').addEventListener('click',salvar);
      document.getElementById('ct-previa').addEventListener('click',previa);
      document.getElementById('ct-padrao').addEventListener('click',function(){
        if(!confirm('Trocar o texto atual pelo modelo padrão? O que você escreveu será perdido.')) return;
        fetch('/painel/servicos/contrato?padrao=1').then(function(r){return r.json();})
          .then(function(d){d.novo=true;desenhar(d);});
      });
    }

    function msg(html){document.getElementById('ct-msg').innerHTML=html;}

    function salvar(){
      var b=document.getElementById('ct-salvar'), t=b.textContent; b.textContent='Salvando...';
      fetch('/painel/servicos/contrato/salvar',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({clausulas:clausulas(),regras:regras()})})
        .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
        .then(function(res){
          b.textContent=t;
          if(!res.ok){msg('<p style="color:var(--verm);font-size:.85rem">'+esc((res.d&&res.d.erro)||'Não consegui salvar.')+'</p>');return;}
          msg('<p style="color:var(--verde-claro);font-size:.85rem">✓ Contrato salvo — '+res.d.clausulas+' cláusulas. Vale para os próximos contratos; os já assinados não mudam.</p>');
          // Recolhe depois de mostrar a confirmação: o trabalho acabou, e deixar
          // aberto obriga a fechar à mão. Relê pra o resumo (e o selo de falta)
          // refletirem o que ACABOU de ser salvo.
          setTimeout(function(){
            fetch('/painel/servicos/contrato').then(function(r){return r.json();})
              .then(function(d){resumir(d);abrir(false);}).catch(function(){abrir(false);});
          }, 1600);
        }).catch(function(){b.textContent=t;msg('<p style="color:var(--verm);font-size:.85rem">Erro de conexão.</p>');});
    }

    // A prévia usa um orçamento REAL da conta. É o que revela a falta que
    // importa: o campo que não resolve porque o item saiu do catálogo.
    function previa(){
      var b=document.getElementById('ct-previa'), t=b.textContent; b.textContent='Montando...';
      fetch('/painel/servicos/contrato/previa',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({clausulas:clausulas(),regras:regras()})})
        .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
        .then(function(res){
          b.textContent=t;
          if(!res.ok){msg('<p class="mut" style="font-size:.85rem">'+esc((res.d&&res.d.erro)||'Não consegui montar.')+'</p>');return;}
          var h='';
          var aj=res.d.ajustes||[];
          if(aj.length){
            h+='<div style="background:#241C0F;border:1px solid var(--ambar-borda);border-radius:8px;padding:.55rem .7rem;margin-bottom:.6rem;font-size:.84rem">'
              +'<b style="color:var(--amar)">Precisa de ajuste</b> — isto não vai preencher em contrato nenhum:'
              +aj.map(function(a){
                  return '<div style="margin-top:.25rem">• <b>'+esc(a.titulo)+'</b> '+esc(a.detalhe)+'</div>';
                }).join('')
              +'</div>';
          }
          // Nota NEUTRA, não alarme: estes campos ficam à vista no texto porque o
          // orçamento de exemplo não tem o dado. Sem esta linha o dono lê o
          // {cliente.nome} do preview como defeito e vem perguntar o que quebrou.
          var dp=res.d.da_proposta||[];
          if(dp.length){
            h+='<div class="mut" style="border:1px solid var(--borda);border-radius:8px;padding:.5rem .7rem;margin-bottom:.6rem;font-size:.8rem">'
              +'Aparecem escritos assim — '
              +dp.map(function(c){return '<code>{'+esc(c)+'}</code>';}).join(' ')
              +' — porque este orçamento de exemplo não tem esses dados. Em cada proposta eles entram sozinhos, com os dados do cliente. Nada a fazer aqui.</div>';
          }
          h+='<div class="mut" style="font-size:.78rem;margin-bottom:.4rem">Prévia com '+esc(res.d.exemplo||'')+'</div>';
          h+='<div style="background:#fff;color:#1a1a1a;border-radius:8px;padding:.9rem 1rem;font-family:Georgia,serif;max-height:420px;overflow:auto">';
          (res.d.clausulas||[]).forEach(function(c){
            h+='<div style="margin-bottom:.7rem"><div style="font-weight:700;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em">'+esc(c.titulo)+'</div>'
              +'<div style="font-size:.85rem;line-height:1.6;white-space:pre-wrap">'+esc(c.corpo)+'</div></div>';
          });
          h+='</div>';
          msg(h);
        }).catch(function(){b.textContent=t;msg('<p style="color:var(--verm);font-size:.85rem">Erro de conexão.</p>');});
    }

    fetch('/painel/servicos/contrato').then(function(r){return r.json();})
      .then(function(d){
        desenhar(d);
        var lembrado=false;
        try{ lembrado = localStorage.getItem(LEMBRA)==='1'; }catch(e){}
        abrir(!!d.novo || lembrado);
      })
      .catch(function(){
        document.getElementById('ct-resumo').textContent='Erro ao carregar.';
        ctBox.innerHTML='<p class="mut">Erro ao carregar o contrato.</p>';
      });
  })(); }

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
