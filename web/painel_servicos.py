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
from web import estaticos as _estaticos
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


#: O que o DDL abaixo garante, pra conferir no catálogo ANTES de rodá-lo. Ver
#: `_orcamentos_em_dia`. A lista de colunas é LIDA DO PRÓPRIO DDL (regex sobre o
#: fonte de `_criar_orcamentos`), então coluna nova entra aqui sozinha: uma lista
#: escrita à mão envelheceria, e o sintoma seria o DDL voltar a rodar calado.
_INDICES_ORCAMENTOS = ("idx_orcamentos_status", "idx_orcamentos_conta", "idx_orcamentos_token",
                       "uq_orcamentos_conta_numero", "idx_orc_envios_orcamento",
                       "uq_orc_comprovante", "idx_orc_comprovante_conta")


def _colunas_do_ddl() -> list[str]:
    import inspect
    import re
    return re.findall(r"alter table orcamentos add column if not exists\s+(\w+)",
                      inspect.getsource(_criar_orcamentos))


def _orcamentos_em_dia(c) -> bool:
    """O banco JÁ TEM tudo o que `_criar_orcamentos` criaria? Só leitura de catálogo.

    POR QUE ISTO EXISTE (medido em 19/09/2026). "Uma vez por processo" não bastou:
    a cada deploy ou reinício, cada processo novo rodava os 57 comandos, e dois
    deles são DROP/ADD CONSTRAINT, que pegam ACCESS EXCLUSIVE em `orcamentos`. O
    pedido de lock ENFILEIRA todo mundo que chega depois — até SELECT —, então a aba
    Propostas do app e o funil congelavam enquanto ele esperava. O log do Postgres
    mostrou a espera: 24 s às 11:25 e 7,6 s às 12:16 daquele dia, nos reinícios.

    Em produção quem cria o esquema é a migração (preDeployCommand), então isto
    responde True e nenhum ALTER roda. Num banco novo (testes, ambiente local) as
    peças faltam, a resposta é False e o DDL roda como sempre rodou.

    Ler o catálogo não disputa lock com ninguém: `pg_attribute`, `pg_constraint` e
    `to_regclass` não tocam na tabela `orcamentos`.
    """
    cols = _colunas_do_ddl()
    r = c.execute(
        """select
             (select count(*) from pg_attribute
               where attrelid = to_regclass('orcamentos') and attnum > 0
                 and not attisdropped and attname = any(%s)),
             (select count(*) from unnest(%s::text[]) i where to_regclass(i) is not null),
             exists(select 1 from pg_constraint
                     where conrelid = to_regclass('orcamentos')
                       and conname = 'orcamentos_status_check'
                       and pg_get_constraintdef(oid) like '%%aprovada%%'),
             exists(select 1 from pg_constraint
                     where conrelid = to_regclass('orcamentos')
                       and conname = 'orcamentos_modo_check'),
             to_regclass('orcamento_envios') is not null
               and to_regclass('orcamento_comprovantes') is not null""",
        (cols, list(_INDICES_ORCAMENTOS))).fetchone()
    return bool(r and r[0] == len(cols) and r[1] == len(_INDICES_ORCAMENTOS)
                and r[2] and r[3] and r[4])


def _criar_orcamentos(c):
    """O DDL em si. Chamado uma vez por processo, via _garantir_tabela — e só roda
    quando falta alguma peça (ver `_orcamentos_em_dia`)."""
    if _orcamentos_em_dia(c):
        return
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
        alter table orcamentos add column if not exists pagamento_anual boolean not null default false;
        alter table orcamentos add column if not exists setup_liquido_centavos bigint;
        alter table orcamentos add column if not exists mensal_liquido_centavos bigint;
        alter table orcamentos add column if not exists dia_vencimento smallint;
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


# A numeração mora em `finance.vendas` desde 06/09: quatro portas criam orçamento
# e só esta numerava. Manter uma cópia aqui seria a segunda leitura da mesma
# regra — e foi de duas leituras que nasceu o e-mail que mostrava contrato e
# mandava proposta (#601).
_com_retry_numero = vendas.com_retry_numero


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
    # Desde 23/09/2026 o recorrente também tem card: o contrato de PRESTAÇÃO DE
    # SERVIÇOS, que o dono liga por conta. O aditivo continua só no evento
    # ("contrato sem o aditivo ainda", pediu o dono pra ZAQ).
    pode_contrato = _equipe.caps_do_papel(
        request.session.get("papel", "dono"))["gerir"]
    # A tela abre no tipo que ESTA empresa mais cadastra, em vez de sempre em PJ.
    # Ver clientes.tipo_predominante: na Prime, 23 de 23 clientes são PF.
    from finance import clientes as _cli
    tipo_padrao = _cli.tipo_predominante(pool, conta[0]) if servico_avulso else "pj"
    # QUEM VÊ O FUNIL INTEIRO também precisa do filtro por vendedor: na Prime são
    # três (Pedro Yan 17 propostas, Jacqueline 5, Thiago 5) e até aqui não havia
    # como separar. O vendedor não recebe o filtro — a lista dele já é só a dele,
    # e um seletor de um nome só é ruído.
    ve_todos = request.session.get("papel", "dono") != "vendedor"
    return _render("servicos", request, empresa_nome=conta[2],
                   tem_pj=True, vende_servico=True, servico_avulso=servico_avulso,
                   ve_todos=ve_todos,
                   pode_contrato=pode_contrato, tipo_padrao=tipo_padrao,
                   tipos_evento=scat.TIPOS_EVENTO, tipos_contrato=scat.TIPOS_CONTRATO,
                   local_padrao=_local_padrao(dados_emp) if servico_avulso else "",
                   # cada nicho com o SEU jogo de ícones (finance/icones_servico)
                   icones_paleta=ics.paleta("evento" if servico_avulso else "recorrente"))


# ---------------------------------------------------------------- catálogo (por conta)
def _modo_dos_icones(pool, conta_id: int) -> str:
    """Qual jogo de ícones a conta usa. Tolerante, ao contrário de
    `vendas.modo_do_orcamento`: aqui errar custa um desenho, não um orçamento no
    modo errado — e a lista do catálogo não pode cair por isso. Na dúvida, o
    jogo do evento, que é o que a tela sempre mostrou."""
    try:
        return vendas.modo_do_orcamento(pool, conta_id)
    except Exception:  # noqa: BLE001
        return "evento"


@router.get("/painel/servicos/catalogo")
def painel_servicos_catalogo(request: Request):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    pool = get_pool()
    scat.garantir_tabela(pool)
    modo = _modo_dos_icones(pool, conta[0])
    itens = [{
        "id": s["id"], "slug": s["slug"], "nome": s["nome"],
        "descricao": s["descricao"],
        # com centavos: o catálogo sempre guardou centavos, e a tela arredondava
        # (R$ 1.397,50 aparecia R$ 1.398)
        "setup": (s["setup_centavos"] or 0) / 100,
        "mensal": (s["mensal_centavos"] or 0) / 100,
        "custo": (s["custo_centavos"] or 0) / 100,
        "categoria": s["categoria"], "foto_url": s["foto_url"],
        # `icone` é o que o vendedor fixou (pode ser vazio); `icone_svg` é o que
        # a tela desenha — já resolvido pelo nome/categoria quando não fixaram.
        "icone": s["icone"],
        "icone_svg": ics.svg(ics.escolher(s["nome"], s["categoria"], s["icone"],
                                          modo=modo), px=20),
    } for s in scat.listar(pool, conta[0])]
    # Os INATIVOS viajam junto, e não numa rota própria: são poucos (4 na Prime,
    # contra 42 ativos) e a tela precisa dos dois na mesma renderização — a lista
    # ativa e a gaveta recolhida no fim dela. Uma segunda viagem só pra desenhar
    # uma gaveta fechada seria pedágio por nada.
    inativos = [{
        "id": s["id"], "slug": s["slug"], "nome": s["nome"],
        "descricao": s["descricao"],
        "setup": round(s["setup_centavos"] / 100),
        "mensal": round(s["mensal_centavos"] / 100),
        "parecido": s["parecido"],
    } for s in scat.listar_inativos(pool, conta[0])]
    return JSONResponse({"itens": itens, "inativos": inativos,
                         "categorias": scat.CATEGORIAS_EVENTOS})


class ServicoIn(BaseModel):
    id: int | None = None
    nome: str = ""
    descricao: str = ""
    setup: float = 0   # REAIS, com centavos (o catálogo guarda centavos)
    mensal: float = 0  # REAIS
    custo: float = 0   # REAIS
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
                    setup_centavos=dsc.centavos(dados.setup),
                    mensal_centavos=dsc.centavos(dados.mensal),
                    custo_centavos=dsc.centavos(dados.custo),
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
    modo = _modo_dos_icones(get_pool(), conta[0])
    return JSONResponse({"chave": ics.escolher(nome, categoria, modo=modo)})


@router.post("/painel/servicos/catalogo/excluir")
def painel_servicos_catalogo_excluir(request: Request, dados: ServicoDelIn):
    """INATIVA o serviço. A rota mantém o nome antigo de propósito — trocar a URL
    quebraria a aba que alguém deixou aberta, e o que mudou não foi o efeito
    (sempre foi `ativo=false`) e sim o nome que a tela dá pra ele."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = scat.excluir(get_pool(), conta[0], int(dados.id))
    if not r.get("ok"):
        return JSONResponse({"erro": "serviço não encontrado"}, status_code=404)
    return JSONResponse(r)


@router.post("/painel/servicos/catalogo/reativar")
def painel_servicos_catalogo_reativar(request: Request, dados: ServicoDelIn):
    """Traz de volta um serviço inativo. A metade que faltava desde sempre."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    r = scat.definir_ativo(get_pool(), conta[0], int(dados.id), True)
    if not r.get("ok"):
        return JSONResponse({"erro": "serviço não encontrado ou já está ativo"},
                            status_code=404)
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


def _reais2(v) -> float | int:
    """Reais com no máximo duas casas; inteiro quando não há centavos.

    Inteiro quando dá, de propósito: o JSON do item continua `1500` (e não
    `1500.0`) pra todo valor redondo — as propostas de hoje não mudam de forma
    nenhuma, e quem lê o item com `int()` segue lendo o mesmo número."""
    try:
        c = int(round(float(v or 0) * 100))
    except (TypeError, ValueError):
        return 0
    return c // 100 if c % 100 == 0 else c / 100


class ItemIn(BaseModel):
    nome: str = ""
    desc: str = ""
    # REAIS com até duas casas desde 24/09/2026 ("sim aceitar centavos", dono).
    # Eram `int`, e o pydantic recusava 1397.5 — o que faria a tela nova falhar
    # ao salvar qualquer valor quebrado.
    setup: float = 0          # total da linha em REAIS (qtd × unitário)
    mensal: float = 0
    qtd: int = 1              # evento: quantidade contratada
    unitario: float = 0       # evento: valor unitário em REAIS
    categoria: str = ""       # evento: agrupa e soma por categoria na folha
    icone: str = ""           # evento: selo do item na folha (vazio = deduzido)
    # DESCONTO DA LINHA. Fica aqui, no snapshot do item, e não em coluna: `itens`
    # já é o retrato da linha no momento da proposta, e o desconto dela é parte do
    # mesmo retrato. Lista separada obrigaria a casar duas por índice — a armadilha
    # que a 162 tirou dos títulos.
    # `desc_val` e não `desc`: `desc` já é a DESCRIÇÃO do item, logo acima.
    desc_tipo: str = "pct"    # 'pct' | 'valor'
    desc_val: float = 0       # % ou REAIS, conforme desc_tipo
    # recorrente: o R$ é POR MÊS (finance.desconto.por_mes). Só vale com 'valor'.
    desc_mes: bool = False


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
    setup: float = 0          # em REAIS, BRUTO (antes de qualquer desconto)
    mensal: float = 0         # em REAIS, bruto
    # o líquido NÃO vem mais da tela: o servidor recalcula com finance.desconto.
    # Continua no modelo porque a tela ainda o manda, e ignorá-lo em silêncio é
    # melhor que quebrar o payload de uma aba aberta durante o deploy.
    primeiro_ano: float = 0   # IGNORADO — derivado no servidor
    n_modulos: int = 0
    desconto_tipo: str = "pct"    # 'pct' | 'valor' — desconto do TOTAL
    desconto_pct: float = 0       # 0–100
    desconto_valor: float = 0     # em REAIS (com centavos)
    # recorrente: o botão "Pagamento anual (-15%)". Só existia na tela — reabrir a
    # proposta trazia ele desligado e o próximo Salvar mudava o preço (311).
    anual: bool = False


@router.post("/painel/servicos/salvar")
def painel_servicos_salvar(request: Request, dados: SalvarIn):
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    validos = scat.slugs_validos(get_pool(), conta[0])
    modulos = [i for i in (dados.modulos or []) if i in validos]
    # o MODO vem do nicho da conta, não do navegador: quem vende evento emite
    # orçamento de evento, e só. (mesma regra do servico_avulso da tela)
    modo = vendas.modo_do_orcamento(get_pool(), conta[0])
    # a descrição do item é o que o cliente lê ("o espaço inclui: ..."): num
    # orçamento de evento ela tem parágrafos inteiros, então o corte é largo.
    itens = []
    for it in (dados.itens or [])[:50]:
        linha = {"nome": (it.nome or "")[:120], "desc": (it.desc or "")[:2000],
                 "setup": _reais2(it.setup), "mensal": _reais2(it.mensal),
                 "qtd": max(1, int(it.qtd or 1)), "unitario": _reais2(it.unitario),
                 "categoria": (it.categoria or "")[:60], "icone": (it.icone or "")[:30],
                 "desc_tipo": "valor" if (it.desc_tipo or "") == "valor" else "pct",
                 "desc_val": max(0, _reais2(it.desc_val))}
        # R$ POR MÊS só existe no recorrente (finance.desconto.por_mes). A marca só
        # entra quando vale, pra item de evento e desconto em % saírem iguais aos
        # de sempre.
        if modo != "evento" and it.desc_mes and linha["desc_tipo"] == "valor":
            linha["desc_mes"] = True
        itens.append(linha)
    itens_json = json.dumps(itens)
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
    bruto_setup = max(0, dsc.centavos(dados.setup))
    bruto_mensal = max(0, dsc.centavos(dados.mensal))
    itens_setup = sum(max(0, dsc.centavos(i["setup"])) for i in itens)
    itens_mensal = sum(max(0, dsc.centavos(i["mensal"])) for i in itens)
    # O PAGAMENTO ANUAL (311). A tela que conhece o campo `anual` manda a
    # mensalidade CHEIA, e o -15% é aplicado aqui, na mesma ordem da tela (depois
    # do desconto da linha, antes do desconto no total). Aba antiga aberta
    # durante o deploy não manda o campo: segue com a mensalidade já reduzida
    # que ela sempre mandou, e fator 1 — o mesmo resultado de ontem.
    anual = bool(dados.anual) and modo != "evento"
    fator_mensal = 0.85 if anual else 1.0
    tot = dsc.totais(
        itens,
        tipo="valor" if (dados.desconto_tipo or "") == "valor" else "pct",
        pct=max(0.0, float(dados.desconto_pct or 0)),
        valor=max(0, dsc.centavos(dados.desconto_valor)),
        extra_setup=max(0, bruto_setup - itens_setup),
        extra_mensal=max(0, bruto_mensal - itens_mensal),
        fator_mensal=fator_mensal,
    )
    # `mensal_centavos` continua querendo dizer o que sempre disse — a mensalidade
    # bruta, já com o anual —, porque funil, cockpit e Raio-X leem a coluna assim.
    bruto_mensal = int(round(bruto_mensal * fator_mensal))
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
            max(0, dsc.centavos(dados.desconto_valor)))
    pool = get_pool()
    reabriu = None
    # CONTRATO ASSINADO NÃO SE EDITA POR BAIXO. Documento congelado, com aceite e
    # IP do cliente — mudar os itens e valores do orçamento de origem faria o
    # assinado dizer uma coisa e o sistema outra, que é o mesmo tipo de divergência
    # que o contrato no sistema nasceu pra matar. Mesma regra do `fechado`, e pelo
    # mesmo motivo. Precisou mudar? É aditivo (contrato novo).
    if dados.id:
        try:
            _ct = ctr.por_orcamento(pool, conta[0], int(dados.id))
            if _ct and _ct.get("assinado_em"):
                # O BECO VIROU PORTA. Esta resposta mandava "faça um aditivo" desde
                # a 164 sem ter para onde mandar — e pior, o JS que a recebia só
                # escrevia "Erro ao salvar", então nem a frase chegava. Agora vai o
                # caminho junto, e a tela leva o vendedor até lá.
                return JSONResponse(
                    {"erro": "esta proposta tem contrato assinado e não pode ser editada — "
                             "faça um aditivo",
                     "aditivo_url": f"/painel/servicos/aditivo/{_ct['id']}"},
                    status_code=409)
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
                       -- REDE DE SEGURANÇA, não a regra: desde 06/09 toda porta
                       -- numera ao criar (finance.vendas.NUMERO_SQL). Isto só
                       -- alcança proposta antiga que nasceu sem número antes disso.
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
        # a data, o tipo e os convidados da proposta vão pro lead que já está
        # amarrado a ela (migração 197) — tolerante por dentro, na mesma transação
        if oid is not None:
            from finance import evento_lead as _evl
            _evl.sincronizar_do_orcamento(c, conta[0], oid)
            # o ANUAL fica gravado (311). Aqui, e não no UPDATE/INSERT de cima, pra
            # não empurrar as posições dos `vals` que as duas consultas dividem.
            # No evento é sempre false: lá não existe mensalidade pra descontar.
            # E AS PONTAS LÍQUIDAS: implantação e mensalidade depois de TODOS os
            # descontos (linha, anual, total). É delas que o contrato de serviço
            # tira os números e o financeiro abre os títulos — antes os títulos
            # saíam do BRUTO, e com desconto o cliente seria cobrado a mais do
            # que aprovou.
            #
            # SAVEPOINT: base sem a 311 não pode derrubar o salvamento da proposta
            # inteira por causa de um botão — perde-se o anual, não a proposta.
            try:
                with c.transaction():
                    c.execute("update orcamentos set pagamento_anual=%s, "
                              "setup_liquido_centavos=%s, mensal_liquido_centavos=%s "
                              "where id=%s and conta_id=%s",
                              (anual,
                               tot["setup"] if modo != "evento" else None,
                               tot["mensal"] if modo != "evento" else None,
                               oid, conta[0]))
            except Exception as e:  # noqa: BLE001
                logging.getLogger("servicos.salvar").warning(
                    "orçamento %s: não gravei o pagamento anual: %s: %s",
                    oid, type(e).__name__, e)
        c.commit()
    if oid is None:
        return JSONResponse({"erro": "proposta não encontrada ou já fechada"}, status_code=400)
    # o cliente do orçamento entra na base de Clientes. Falhar aqui não pode
    # derrubar o orçamento, que é o que o vendedor está tentando salvar.
    # NOS DOIS MODOS. Era só no evento; no recorrente o cliente ficava preso na
    # proposta e não aparecia na aba Clientes. Pedido do dono em 23/09/2026, na
    # ZAQ ("sim" a "quer que o cliente entre em Clientes, como na Prime?").
    # `criar_cliente` só preenche o que está vazio: salvar de novo não estraga o
    # cadastro. Contrato: `completar_do_cadastro` só tapa buraco, o orçamento vence.
    cliente_id = None
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


# OS DOIS `left join lateral` ABAIXO no lugar de sete subconsultas
# correlacionadas que liam as MESMAS duas linhas: cinco em `contratos`
# (token, número, assinado, enviado, id) e duas em `eventos_agenda` (o
# prazo da pré-reserva e o status). Cada uma era uma busca própria POR
# LINHA do funil — numa lista de cinquenta, 350 buscas pra trazer cem
# linhas. O `ux_contratos_orcamento` (migração 164) já garante UM contrato
# vivo por orçamento, então o `order by ct.id desc limit 1` continua aqui
# como cinto e suspensório; o que muda é rodar uma vez por orçamento em
# vez de cinco.
#
# As colunas de `orcamentos` vão TODAS qualificadas. Com join no meio, um
# nome solto que amanhã também exista do outro lado derruba a lista
# inteira com "column reference is ambiguous" — foi exatamente assim que
# o `criado_em` sem apelido já derrubou este funil uma vez.
_COLS_FUNIL = """select orcamentos.id, orcamentos.cliente, orcamentos.empresa,
                  orcamentos.setup_centavos, orcamentos.mensal_centavos,
                  orcamentos.primeiro_ano_centavos, orcamentos.n_modulos,
                  orcamentos.criado_em, orcamentos.status,
                  orcamentos.token, orcamentos.aprovada_por,
                  orcamentos.aprovada_em, orcamentos.numero,
                  coalesce(orcamentos.modo,'recorrente'),
                  orcamentos.sinal_centavos, orcamentos.sinal_pago_em,
                  orcamentos.parcelas,
                  ctr.ct_token,
                  ctr.ct_numero,
                  ctr.ct_assinado,
                  -- só vem preenchido enquanto a data está SEGURADA
                  -- esperando o sinal: virou firme ou foi cancelada, é null
                  -- e o botão "Sinal recebido" some sozinho.
                  evt.ev_pre_reserva_ate,
                  orcamentos.evento,
                  evt.ev_status,
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
                  coalesce(orcamentos.whatsapp, orcamentos.telefone, '') as zap,
                  -- desde quando o contrato está na mão do cliente
                  -- esperando assinatura — não desde quando foi CRIADO.
                  ctr.ct_enviado_em,
                  -- O ID do contrato, no FIM da lista de propósito: inserir
                  -- coluna no meio empurraria todos os r[n] de baixo, e o
                  -- `_fmt_item` lê por posição.
                  --
                  -- Precisa do id, e não do token: o token abre a folha
                  -- pública do cliente; a tela de termo aditivo é rota
                  -- interna, endereçada por id.
                  ctr.ct_id,
                  -- QUEM VENDEU. `criado_por` guarda o id do membro OU a
                  -- palavra 'dono' (conta sem vendedor específico) — mesma
                  -- leitura de `finance.vendas._membro_id_de` e a mesma
                  -- redação que Relatórios → Vendas já usa pra essa coluna
                  -- (web/painel_relatorios.py): sem o 2º ramo, todo
                  -- orçamento do dono ficava "—", como se não tivesse
                  -- autor nenhum.
                  coalesce(
                    (select mm.nome from membros mm
                      where mm.id::text = orcamentos.criado_por
                        and mm.conta_id = orcamentos.conta_id),
                    case when orcamentos.criado_por = 'dono' then %s end,
                    '—') as vendedor"""
# o FROM com os dois laterais. Fica separado do SELECT porque as duas
# consultas abaixo (vendedor × dono) só diferem no WHERE.
_DE_FUNIL = """ from orcamentos
          left join lateral (
            select ct.id                          as ct_id,
                   ct.token                       as ct_token,
                   ct.numero                      as ct_numero,
                   (ct.assinado_em is not null)   as ct_assinado,
                   ct.enviado_em                  as ct_enviado_em
              from contratos ct
             where ct.orcamento_id = orcamentos.id
               and ct.substitui_id is null
             order by ct.id desc limit 1) ctr on true
          left join lateral (
            select e.status as ev_status,
                   case when e.status = 'pre_reservado'
                        then e.pre_reserva_ate end as ev_pre_reserva_ate
              from eventos_agenda e
             where e.id = orcamentos.evento_agenda_id) evt on true"""


def _curar_tokens(c, conta_id: int, linhas) -> dict[int, str]:
    """Gera o token das propostas desta página do funil que ainda não têm.

    O token é a chave do link público (a folha que o cliente abre) e do PDF.
    `salvar` já grava um no INSERT e no UPDATE desde que a coluna existe, então
    quem chega aqui sem token é proposta anterior à migração — em produção,
    nenhuma.

    A versão antiga desta cura era um `update ... where conta_id=%s and token is
    null` MAIS um commit rodando antes de toda leitura do funil, e a tela relê o
    funil em nove pontos. Eram nove transações de escrita pra atualizar zero
    linhas. Aqui a pergunta "tem o que curar?" é respondida pelo SELECT que já
    rodou: sem linha sem token, não se toca no banco.

    Devolve {id: token} só do que foi curado agora — o chamador costura no JSON.
    """
    sem = [r[0] for r in linhas if not r[9]]
    if not sem:
        return {}
    novos = dict(c.execute(
        """update orcamentos set token = substr(md5(random()::text || id::text
             || clock_timestamp()::text), 1, 22)
           where conta_id=%s and id = any(%s) and token is null
           returning id, token""", (conta_id, sem)).fetchall())
    c.commit()
    return novos


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
        # A AUTO-CURA DO TOKEN SAIU DAQUI — ver `_curar_tokens`, chamada DEPOIS do
        # SELECT. Aqui em cima ela era um UPDATE da conta inteira mais um commit em
        # TODA leitura do funil, e o funil é relido em nove pontos da tela (salvar,
        # fechar, sinal, marcar data, enviar, comprovante, excluir, gerar, abrir).
        # Nove transações de escrita por sessão de trabalho pra, em produção,
        # atualizar zero linhas: `salvar` já grava o token no INSERT e no UPDATE
        # (`token=coalesce(token, %s)`), então só proposta anterior à coluna nasce
        # sem. Agora a cura só toca no banco quando o próprio SELECT mostra que há
        # o que curar.
        # vendedor vê só as propostas dele; dono/gestor veem o funil inteiro.
        # o prazo da pré-reserva vem da agenda, não do orçamento: quem manda na
        # data é o compromisso. Se ele já virou firme (ou foi cancelado), a
        # subconsulta devolve null e o botão "Sinal recebido" some sozinho.
        if papel == "vendedor" and membro_id:
            rows = c.execute(
                _COLS_FUNIL + _DE_FUNIL + """ where orcamentos.conta_id=%s
                     and orcamentos.criado_por=%s
                   order by orcamentos.criado_em desc limit 50""",
                (conta[2], conta[0], str(membro_id))).fetchall()
        else:
            rows = c.execute(
                _COLS_FUNIL + _DE_FUNIL + """ where orcamentos.conta_id=%s
                   order by orcamentos.criado_em desc limit 50""",
                (conta[2], conta[0])).fetchall()
        # a cura do token, agora depois do SELECT e só sobre as linhas que vieram
        # sem: no caso normal não abre transação nenhuma.
        tokens_novos = _curar_tokens(c, conta[0], rows)
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
        # o curado agora entra no lugar do vazio: sem isto a linha recém-curada
        # ficaria sem "Abrir proposta" até o próximo carregamento.
        "token": r[9] or tokens_novos.get(r[0], ""),
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
        # só serve depois de assinado (é quando existe o que aditar), mas vai
        # sempre: o menu do funil decide com `contrato_assinado`
        "contrato_id": r[27],
        "vendedor": r[28],
        # QUANDO o contrato foi mandado — o mesmo dado que a proposta já mostra em
        # `enviado_em`. Sem ele o menu ofereceria "Mandar por e-mail" sem dizer se
        # já foi mandado, e é justamente essa dúvida que faz o vendedor mandar de
        # novo "por via das dúvidas". O cru continua em `_contrato_enviado_em`
        # (o motor de pendências conta dias com ele); aqui vai o formatado.
        "contrato_enviado_em": (r[26].astimezone(ag.BRT).strftime("%d/%m %H:%M")
                                if r[26] else ""),
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
    # A ORDEM QUE ESTA EMPRESA ESCOLHEU (migração 194). Lido UMA vez, fora do laço:
    # é parâmetro da conta, não da linha — dentro daria o mesmo N+1 que já custou
    # caro na Agenda. `assina_antes_do_sinal` falha fechada na ordem de hoje.
    _assina_antes = _ctr.assina_antes_do_sinal(get_pool(), conta[0])
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
            contrato_enviado_em=it["_contrato_enviado_em"],
            # no recorrente o contrato é da CONTA (311), e só prende a linha que
            # já tem um: proposta aprovada antes de ligar fecha pelo botão.
            tem_contrato=_nicho_tem_contrato or bool(it["contrato_numero"]),
            assinar_antes_do_sinal=_assina_antes,
            # o modo do ORÇAMENTO, não o da conta: é ele que diz se o plano de
            # pagamento mora em `parcelas` (evento) ou em setup + mensalidade
            # (recorrente). `titulo_do_funil`, logo abaixo, já usava o mesmo campo.
            modo=it["modo"])
        # EM QUAL ABA A LINHA CAI e O BLOCO DE DATA que abre ela. Os dois saem de
        # funções puras (`finance.vendas`), pelo mesmo motivo do painel e do
        # título: a tela desenha, não decide. E a aba é DERIVADA do painel que
        # acabou de ser montado, então ela nunca discorda do que a linha mostra.
        # Vêm ANTES do título porque o título depende do bloco de data (abaixo).
        it["grupo"] = vendas.grupo_do_funil(status=it["status"], painel=it["painel"])
        it["data_linha"] = vendas.data_da_linha(it.get("evento"), modo=it["modo"])
        # o NOME, resolvido no servidor pela mesma função pura que os testes cobrem.
        # A tela não decide mais quem é o cliente desta linha.
        # `com_data` OFF quando a linha abre com o bloco de data: senão a mesma
        # data aparece duas vezes, a meio centímetro de distância ("04 SET 27" e
        # "Casamento · 04/09/2027 · 150 convidados").
        it.update(vendas.titulo_do_funil(
            cadastro=it["_cadastro"], empresa=it["empresa"], cliente=it["cliente"],
            modo=it["modo"], evento=it.get("evento"), numero=it["numero"],
            com_data=not it["data_linha"]))
        for _k in ("_cadastro", "_contrato_enviado_em"):
            it.pop(_k, None)
    # a CONTAGEM POR ABA vem do servidor: a tela mostra "Precisa de mim (4)" antes
    # de o vendedor clicar em nada, e contar no navegador daria um número que só
    # existe depois de a lista inteira ser desenhada.
    contagem = {chave: 0 for chave, _ in vendas.GRUPOS}
    for it in itens:
        contagem[it["grupo"]] = contagem.get(it["grupo"], 0) + 1
    return JSONResponse({
        "itens": itens,
        "grupos": [{"chave": c, "rotulo": r, "n": contagem.get(c, 0)}
                   for c, r in vendas.GRUPOS],
        # quem vende, pra o filtro do dono. Sai da própria lista (e não de uma
        # consulta a `membros`) pra oferecer só nomes que têm linha pra mostrar —
        # um filtro que resulta em lista vazia é um filtro que mente.
        "vendedores": sorted({it["vendedor"] for it in itens if it["vendedor"] != "—"}),
    })


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
                      desconto_tipo, desconto_pct, desconto_centavos, criado_em,
                      coalesce((to_jsonb(orcamentos)->>'pagamento_anual')::boolean, false)
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
        "desconto_valor": _reais2(int(r[29] or 0) / 100),
        # QUANDO ESTA PROPOSTA FOI GERADA. A folha do cliente sempre disse
        # ("Emitido em"); quem vende, não — nem no funil nem aqui no editor. E é
        # quem vende que precisa saber se aquilo ainda está de pé.
        "gerado_em": r[30].strftime("%d/%m/%Y") if r[30] else "",
        # o anual volta ligado (311) — sem isto o editor reabria com a mensalidade
        # cheia e o próximo Salvar subia o preço que o cliente recebeu
        "anual": bool(r[31]),
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
    _assinado = False
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
        _assinado = bool(_ct.get("assinado_em"))
        _assunto = pmail.assunto_contrato(_ct.get("numero"), nome_emp)
        _msg = pmail.texto_contrato(_quem, assinado=_assinado)
    return JSONResponse({
        "para": d["email"],
        "cliente": d["cliente"] or d["empresa_cli"],
        "assunto": _assunto, "mensagem": _msg, "link": _link,
        # QUEM DECIDE É O SERVIDOR, aqui também. É ele que já escolheu a redação
        # da mensagem (pedir assinatura × mandar a via); deixar a tela adivinhar
        # o título por conta própria seria a segunda leitura do mesmo fato, e é
        # assim que o título passa a dizer uma coisa e o corpo do e-mail outra.
        "assinado": _assinado,
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
        assunto_padrao_ = pmail.assunto_contrato(_ct.get("numero"), nome_emp)
        mensagem_padrao_ = pmail.texto_contrato(quem, assinado=bool(_ct.get("assinado_em")))
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
# QUEM FAZ O QUÊ:
#   ver a lista e o arquivo   dono, gestor e VENDEDOR — é ele que cobra o cliente,
#                             e cobrar sem saber o que já entrou é ligar no escuro
#   anexar                    os mesmos três, desde 03/09/2026
#
# POR QUE O VENDEDOR PASSOU A ANEXAR. Era gate de `financeiro` (dono e gestor), com
# a régua "papel de dinheiro é do financeiro". A régua continua certa e o
# enquadramento é que estava errado: ANEXAR NÃO É MEXER EM DINHEIRO. Quem marca a
# parcela como paga é o "Sinal recebido" — e isso o vendedor já faz do celular
# desde 01/09. Anexar é juntar a prova do que ele mesmo acabou de registrar, e ele
# é quem tem o arquivo na mão, porque o PIX chega no WhatsApp dele.
#
# O efeito de manter fechado era este: o vendedor confirmava o sinal em campo e
# deixava para trás um selo coral "1 parcela sem comprovante" que só o dono limpava,
# sentado no computador.
#
# O QUE NÃO MUDOU: editar valor, dar baixa e fechar contrato seguem com dono e
# gestor. A trava que importa é a que mexe no número, não a que junta o papel.


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
        # QUANTOS COMPROVANTES EXISTEM, à parte de quantas parcelas estão pagas —
        # as duas coisas viviam misturadas debaixo de "pagas de total", e um
        # comprovante já anexado não aparecia em lugar nenhum do topo do modal.
        "anexados": len(anexos),
        # a tela só oferece o botão quando ele tem pra onde mandar o arquivo —
        # botão que engole comprovante é pior que botão nenhum. O PAPEL saiu da
        # conta em 03/09: quem abre esta tela (dono, gestor, vendedor) anexa.
        "pode_anexar": comprov.configurado(),
        "sem_storage": not comprov.configurado(),
    })


@router.post("/painel/servicos/comprovante")
async def painel_servicos_comprovante_subir(
        request: Request, orcamento_id: int = Form(...), parcela_idx: int = Form(...),
        arquivo: UploadFile = File(...)):
    """Anexa (ou substitui) o comprovante de uma parcela.

    Mesmo gate de quem ABRE a tela — dono, gestor e vendedor. Ver a nota longa
    acima: anexar é juntar prova, não mexer em dinheiro."""
    conta, redir = _conta_servico(request)
    if redir is not None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
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
    """Gate do CONTRATO: além do gate da aba, só o dono.

    Até 23/09/2026 a conta também precisava ser de eventos. Desde então o
    recorrente escreve o contrato de PRESTAÇÃO DE SERVIÇOS (ver finance/contrato,
    "DOIS CONTRATOS, UM MOTOR") — então a porta do nicho saiu daqui, e quem decide
    QUAL documento a tela mostra é `contrato.modo_da_conta`. O nome ficou pra não
    mexer nas três rotas que o chamam.

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
    return conta, None


def _contexto_de_exemplo(pool, conta_id: int, modo: str = "locacao"):
    """(contexto, rótulo) montado com um orçamento REAL da conta, ou (None, "").

    O mais recente que tenha data de evento. Serve a duas coisas — a prévia da
    tela e o aviso de campo sem valor no card recolhido — e é o mesmo de
    propósito: se as duas usassem bases diferentes, uma diria que está tudo certo
    enquanto a outra apontava falta.

    `regras` vem de fora porque a prévia precisa refletir o que está NO
    FORMULÁRIO, não o que está gravado — é assim que o dono experimenta uma multa
    diferente antes de salvar.

    No contrato de SERVIÇO o exemplo é o orçamento recorrente mais recente que
    tenha serviço — não existe data de evento pra procurar."""
    if modo == ctr.MODO_SERVICO:
        return _exemplo_servico(pool, conta_id)
    with pool.connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select cliente, cnpj, whatsapp, setup_centavos, numero, evento,
                      empresa, endereco, cep, cidade, uf, cliente_id
                 from orcamentos
                where conta_id=%s and coalesce(evento->>'data','') <> ''
                order by id desc limit 1""", (conta_id,)).fetchone()
    if not r:
        return None, ""
    orcamento = {"cliente": r[0], "cnpj": r[1], "whatsapp": r[2],
                 "setup_centavos": r[3], "numero": r[4], "evento": r[5] or {},
                 # os cinco abaixo faltavam, e a falta MENTIA: a prévia acusava
                 # `cliente.endereco` sem valor num orçamento que tinha endereço,
                 # só porque a consulta não o trazia. Aviso que erra é aviso que
                 # o dono aprende a ignorar.
                 "empresa": r[6], "endereco": r[7], "cep": r[8],
                 "cidade": r[9], "uf": r[10]}
    # o MESMO completar da folha do cliente: a prévia existe pra antecipar o que
    # ele vai ver, e uma prévia mais pessimista que o documento real ensina o dono
    # a desconfiar dela.
    orcamento = ctr.completar_do_cadastro(pool, conta_id, orcamento, r[11])
    return orcamento, f"orçamento nº {r[4] or '—'} · {r[0] or ''}"


def _exemplo_servico(pool, conta_id: int):
    """O orçamento recorrente de exemplo — o mesmo desenho do de eventos, com as
    duas pontas do dinheiro no lugar da data. Mesmo dicionário que a folha pública
    monta (`contrato_publico.qualificacao`), pra prévia e documento lerem igual."""
    with pool.connection() as c:
        _garantir_tabela(c)
        r = c.execute(
            """select cliente, cnpj, whatsapp, coalesce(primeiro_ano_centavos, 0), numero,
                      empresa, endereco, cep, cidade, uf, cliente_id,
                      coalesce((to_jsonb(orcamentos)->>'setup_liquido_centavos')::bigint, setup_centavos),
                      coalesce((to_jsonb(orcamentos)->>'mensal_liquido_centavos')::bigint, mensal_centavos),
                      itens,
                      coalesce((to_jsonb(orcamentos)->>'pagamento_anual')::boolean, false)
                 from orcamentos
                where conta_id=%s and coalesce(modo,'recorrente') <> 'evento'
                  and jsonb_array_length(coalesce(itens,'[]'::jsonb)) > 0
                order by id desc limit 1""", (conta_id,)).fetchone()
    if not r:
        return None, ""
    orcamento = {"cliente": r[5] or r[0] or "", "empresa": r[5], "cnpj": r[1],
                 "whatsapp": r[2], "setup_centavos": r[3], "numero": r[4],
                 "endereco": r[6], "cep": r[7], "cidade": r[8], "uf": r[9],
                 "recorrente": {"setup_centavos": int(r[11] or 0),
                                "mensal_centavos": int(r[12] or 0),
                                "ano1_centavos": int(r[3] or 0), "anual": bool(r[14]),
                                "itens": r[13] if isinstance(r[13], list) else []}}
    orcamento = ctr.completar_do_cadastro(pool, conta_id, orcamento, r[10])
    return orcamento, f"orçamento nº {r[4] or '—'} · {r[5] or r[0] or ''}"


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
    # QUAL DOS DOIS DOCUMENTOS: locação (eventos) ou prestação de serviços
    # (recorrente). Quem decide é o nicho, pela mesma porta do modo do orçamento.
    modo = ctr.modo_da_conta(pool, conta[0])
    salvo = ctr.carregar_modelo(pool, conta[0], modo)
    modelo = ({"clausulas": ctr.modelo_padrao(modo), "regras": ctr.regras_padrao(modo),
               "novo": True, "atualizado_em": None, "atualizado_por": ""}
              if padrao else salvo)
    catalogo = scat.listar(pool, conta[0])
    empresa = emp.obter_dados_empresa(pool, conta[0])
    # O QUE AJUSTAR no resumo do card fechado. Custa uma consulta a mais por
    # carregamento e vale: um campo sem valor não aparece em lugar nenhum até
    # sair no contrato DO CLIENTE — é o único erro deste fluxo que estreia na
    # frente dele. Melhor o dono ver com o card recolhido.
    orcamento, exemplo = _contexto_de_exemplo(pool, conta[0], modo)
    diag = {"ajustes": [], "da_proposta": []}
    if orcamento and not modelo["novo"]:
        ctx = ctr.contexto(catalogo=catalogo, orcamento=orcamento, modelo=modelo,
                           empresa=empresa, modo=modo)
        _doc, faltas = ctr.montar(modelo["clausulas"], ctx)
        diag = ctr.diagnostico(faltas, ctx, catalogo)
    elif modo == ctr.MODO_SERVICO:
        # sem orçamento de exemplo, o de serviço ainda sabe o que é do DONO: os
        # números da casa em branco. É justo o que a ZAQ vai ver no primeiro dia.
        diag["ajustes"] = ctr.pendencias_pra_ligar(modelo["clausulas"], modelo["regras"],
                                                   empresa)
    return JSONResponse({
        "modo": modo,
        # a chave do recorrente (311). No de eventos o contrato é do nicho e
        # esta chave não existe na tela.
        "pedir_assinatura": bool(salvo.get("pedir_assinatura")) if modo == ctr.MODO_SERVICO else None,
        "clausulas": modelo["clausulas"], "regras": modelo["regras"],
        "novo": modelo["novo"], "campos": ctr.campos_disponiveis(catalogo, modo),
        # `padrao=1` é o botão "restaurar", e ele mexe só nas CLÁUSULAS — a ordem
        # que a empresa escolheu não é texto de contrato e não se restaura junto.
        "assinar_antes_do_sinal": ctr.assina_antes_do_sinal(pool, conta[0]),
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
    # a ordem que a empresa escolhe (194). Default False = a ordem de hoje, então
    # uma porta antiga que não mande o campo não desliga o que o dono ligou... e é
    # por isso que a tela SEMPRE manda o valor atual, nunca só quando muda.
    assinar_antes_do_sinal: bool = False
    # a chave do contrato de SERVIÇO (311). None = a tela não mandou (a de eventos
    # nunca manda): não mexe no que está gravado.
    pedir_assinatura: bool | None = None


@router.post("/painel/servicos/contrato/salvar")
def painel_servicos_contrato_salvar(request: Request, dados: ContratoIn):
    conta, erro = _conta_evento(request)
    if erro is not None:
        return erro
    membro_id, _papel = _ator(request)
    pool = get_pool()
    modo = ctr.modo_da_conta(pool, conta[0])
    pedir = dados.pedir_assinatura if modo == ctr.MODO_SERVICO else None
    # LIGAR COM NÚMERO EM BRANCO NÃO SALVA. Ligado, a próxima proposta aprovada
    # vira contrato na hora — e com `{regra.aviso_previo_dias}` cru no texto, o
    # cliente receberia um aviso prévio que não diz quantos dias. Recusa o salvar
    # inteiro (e não só a chave): salvar metade e dizer "salvo" faria o dono sair
    # achando que ligou.
    if pedir:
        pend = ctr.pendencias_pra_ligar(dados.clausulas, dados.regras,
                                        emp.obter_dados_empresa(pool, conta[0]))
        if pend:
            return JSONResponse(
                {"erro": "Pra pedir assinatura, preencha antes: "
                         + "; ".join(f"{p['titulo']} ({p['detalhe']})" for p in pend[:6])
                         + ("…" if len(pend) > 6 else ""),
                 "pendencias": pend}, status_code=409)
    r = ctr.salvar_modelo(pool, conta[0], dados.clausulas, dados.regras,
                          por=str(membro_id or "dono"),
                          assinar_antes_do_sinal=dados.assinar_antes_do_sinal,
                          pedir_assinatura=pedir)
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
    modo = ctr.modo_da_conta(pool, conta[0])
    orcamento, exemplo = _contexto_de_exemplo(pool, conta[0], modo)
    if not orcamento:
        return JSONResponse({"erro": ("nenhum orçamento com serviço para usar de exemplo"
                                      if modo == ctr.MODO_SERVICO else
                                      "nenhum orçamento com data de evento para usar de exemplo")},
                            status_code=404)
    catalogo = scat.listar(pool, conta[0])
    ctx = ctr.contexto(catalogo=catalogo, orcamento=orcamento,
                       modelo={"regras": dados.regras},
                       empresa=emp.obter_dados_empresa(pool, conta[0]), modo=modo)
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
# ---------------------------------------------------------------- estáticos
# A FOLHA E O SCRIPT SAEM DE DENTRO DA PÁGINA. Eram 23 KB de CSS e 112 KB de JS
# viajando dentro do HTML em TODA carga da aba — e o HTML muda (nome da empresa,
# nicho, paleta de ícones), então o navegador não tinha como reaproveitar nada:
# baixava e reinterpretava os 135 KB a cada F5 e a cada volta pro funil.
#
# É o mesmo conserto que a Agenda fez em 19/08 (ver web/estaticos.py): o endereço
# carrega o resumo do conteúdo, o navegador guarda por um ano, e um caractere
# mudado já é outro endereço — não existe versão velha grudada.
#
# O que NÃO pode vir pra cá é o que tem {{ }} dentro. Aqui não tem: os dois
# dados que a tela precisa do servidor (`SERVICO_AVULSO` e a paleta de ícones)
# já moravam em duas linhas `<script>` próprias, que continuam inline. Elas
# rodam durante a análise da página; o `defer` faz o código rodar depois — a
# ordem que o IIFE precisa continua sendo a mesma.
_CSS_CRU = r""".sv-wrap{width:100%;max-width:960px;padding:0 1rem 2rem;box-sizing:border-box}
/* A TELA USA A LARGURA INTEIRA, como o Raio-X.
   O `.rx` não tem trava nenhuma — ele preenche a área de conteúdo. Aqui a trava
   era 1120px, e com o funil na frente (com data, valor, selos, ação e menu na
   mesma linha) sobrava faixa vazia à direita enquanto o texto da linha quebrava.
   Nasceu na Prime e passou pro recorrente em 23/09/2026 junto com o resto do
   layout (`.funil`). */
.sv-wrap.funil{max-width:none}
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
.oc-mod.avulso{grid-template-columns:auto minmax(0,1fr) 56px 96px 104px 92px auto}
.sv-wrap.oc-margin .oc-mod.avulso{grid-template-columns:auto minmax(0,1fr) 56px 96px 88px 104px 92px auto}
/* no orçamento de evento o rótulo vai EM CIMA do campo: "7200" e "10" precisam
   da largura inteira da caixinha, senão o número sai cortado. */
.oc-mod.avulso .oc-num,.oc-mod.rec .oc-num{flex-direction:column; align-items:stretch; gap:1px; padding:.25rem .45rem}
.oc-mod.avulso .oc-num span,.oc-mod.rec .oc-num span{font-size:.58rem; text-align:right}
/* A LINHA DO RECORRENTE: nome | setup | mensal | (custo) | desconto | 🗑.
   Uma coluna por caixa que APARECE. A grade antiga tinha 7 colunas pra 6 caixas
   (o custo fica escondido fora do Modo margem), então tudo escorregava uma casa:
   o desconto caía numa coluna de 84px e invadia o ✎/🗑, e o nome era espremido
   até quebrar no meio da palavra ("Atendimen"). O Modo margem acrescenta a
   coluna do custo em vez de reaproveitar uma que já estava ocupada. */
.oc-mod.rec{grid-template-columns:minmax(0,1fr) 112px 120px 184px auto}
.sv-wrap.oc-margin .oc-mod.rec{grid-template-columns:minmax(0,1fr) 112px 120px 104px 184px auto}
.oc-mod.rec .oc-nome b{display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
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
  /* recorrente no estreito: nome e 🗑 em cima, setup e mensal lado a lado, o
     desconto na linha inteira embaixo — é o par que precisa de largura. */
  .oc-mod.rec{display:flex; flex-wrap:wrap; align-items:flex-start; gap:.5rem .55rem}
  .oc-mod.rec .oc-nome{order:1; flex:1 1 140px; min-width:0}
  .oc-mod.rec .oc-rowacts{order:2; flex:0 0 auto}
  .oc-mod.rec .oc-num{order:3; flex:1 1 86px}
  .oc-mod.rec .oc-desc-col{order:4; flex:1 1 100%}
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
/* serviço que a proposta tem e o catálogo não tem mais: a linha vale igual, e o
   selo explica por que a busca não acha esse nome. Âmbar, não coral: não é
   prejuízo, é uma informação que faltava. */
.oc-fora{display:inline-block;margin-left:.4rem;font-size:.62rem;font-weight:700;
  letter-spacing:.03em;text-transform:uppercase;white-space:nowrap;
  border-radius:5px;padding:.05rem .35rem;
  color:var(--amar);border:1px solid var(--ambar-borda);background:var(--ambar-fundo)}
.oc-mod.off{opacity:.5}
.oc-mod .oc-nome,.oc-browse-row .oc-nome{cursor:default; min-width:0}
.oc-desc-preview{white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%}
.oc-rowacts{display:flex; gap:.35rem; white-space:nowrap}
.oc-ic{background:var(--bg); border:1px solid var(--borda); color:var(--txt-mut); cursor:pointer; font-size:.85rem; width:30px; height:30px; padding:0; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; transition:border-color .15s,color .15s,background .15s}
.oc-ic:hover{color:var(--txt); border-color:var(--verde); background:var(--card)}
.oc-del:hover{color:#e0857a; border-color:#5c2a27}

/* A GAVETA DOS INATIVOS — fim da lista completa, fechada por padrão. Fechada
   porque ela não é trabalho do dia: quem abre a lista está montando orçamento,
   e o inativo só interessa no dia em que se procura o que sumiu. Na Prime são 4
   contra 42 ativos; aberta, ela empurraria a lista toda pra baixo por nada. */
.oc-inativos{margin-top:.9rem; border-top:1px dashed var(--borda); padding-top:.6rem}
.oc-inativos-cab{display:flex; justify-content:space-between; align-items:center; gap:.6rem;
  width:100%; background:none; border:0; padding:.45rem .2rem; cursor:pointer;
  color:var(--txt-mut); font:inherit; font-size:.82rem; text-align:left}
.oc-inativos-cab:hover{color:var(--txt)}
.oc-inativos-cab b{color:var(--txt); font-weight:600}
.oc-inativos-cab .mut{font-size:.72rem}
.oc-inativos-corpo{display:none}
.oc-inativos.open .oc-inativos-corpo{display:block}
.oc-inativo-row{display:flex; align-items:center; gap:.6rem; padding:.5rem .2rem;
  border-top:1px solid var(--card-2)}
.oc-inativo-row .oc-nome{flex:1 1 auto; min-width:0; opacity:.7}
.oc-inativo-av{font-size:.72rem; color:#f0c05a; margin-top:.15rem; opacity:1}
.oc-reativar{background:none; border:1px solid #1E4A3A; color:var(--verde-claro);
  border-radius:8px; padding:.3rem .7rem; font-size:.78rem; cursor:pointer;
  width:auto; white-space:nowrap}
.oc-reativar:hover{background:#10241A}
.oc-reativar:disabled{opacity:.5; cursor:default}

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
.pg-tot .an b{color:var(--azul)}
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
.oc-browse-row.rec{grid-template-columns:auto 1fr 150px auto}
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

/* QUANDO A PROPOSTA FOI CRIADA — coluna própria, à direita do nome.
   Antes era "gerada 16/09/2026" no meio de "nº 27 · gerada … · vendido por …".
   O dado estava certo e ilegível: data no meio de frase não se compara com a da
   linha de baixo. Em coluna, as 27 datas viram uma leitura só.
   Com ANO SEMPRE, a pedido do dono: proposta de evento atravessa a virada do
   ano, e "16/09" sem ano é ambíguo justamente no funil, que guarda 2026, 2027 e
   2028 ao mesmo tempo. */
.oc-criada{flex:none; text-align:right; padding-left:.9rem; line-height:1.25}
.oc-criada .rot{font-size:.6rem; letter-spacing:.06em; text-transform:uppercase;
  color:var(--text-faint); font-weight:700}
.oc-criada .dt{font-size:.84rem; color:var(--txt); white-space:nowrap;
  font-variant-numeric:tabular-nums}
@media(max-width:600px){
  /* No celular a linha já quebra sozinha. A coluna vira uma faixa própria, à
     esquerda como o resto e numa linha só ("CRIADA EM 16/09/2026"), logo abaixo
     do nome e ANTES da barra de ações: o verde continua sendo a última coisa da
     linha, que é onde o polegar procura. */
  .oc-criada{text-align:left; padding-left:0; width:100%; order:1;
    display:flex; align-items:baseline; gap:.35rem; margin-top:.15rem}
  .oc-hist .oc-acoes{order:2}
}

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
/* recorrente: o que a linha cobra por mês depois do desconto dela */
.oc-rliq{display:block; font-size:.72rem; color:var(--verde-claro); margin-top:.2rem; text-align:right; white-space:nowrap}
.oc-rliq:empty{display:none}
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

/* ================================ O FUNIL NA FRENTE
   A ordem vem da FOLHA e não da marcação. Nasceu na Prime (eventos) e passou pro
   recorrente em 23/09/2026, a pedido do dono ("deixa o mesmo modelo que já tem
   na Prime eventos, as ordens, botões e tudo"). A classe `.funil` é a FORMA da
   tela; o que é de festa (card do evento, plano de parcelas, cobrar × incluso)
   continua preso a `.evento` e ao `servico_avulso` do template. */
.sv-wrap.funil{display:flex; flex-direction:column}
.sv-wrap.funil > .sv-topo{order:-2}
.sv-wrap.funil > #oc-funil{order:-1}
/* o editor abre sob demanda: "+ Nova proposta" ou clique numa linha do funil */
.sv-wrap.funil #oc-editor{display:none}
.sv-wrap.funil #oc-editor.on{display:flex; flex-direction:column}
/* A ORDEM DENTRO DO EDITOR, tambem pela folha.
   Duas mudancas, as duas medidas na Prime:
   1. CLIENTE ANTES DO EVENTO. Na conversa real pergunta-se quem e antes de
      quando e; a tela pedia a data da festa antes de saber de quem era.
   2. CONTRATO E ADITIVO PRO FIM. Sao configuracao — escrevem-se uma vez e
      abriam a tela todo dia, na frente do trabalho diario. */
.sv-wrap.funil #oc-editando{order:0}
.sv-wrap.funil #oc-cli-card{order:1}
.sv-wrap.funil #oc-ev-card{order:2}
/* no recorrente, o lugar do card do evento é o do escopo pela IA: é ali que se
   descreve o que o cliente precisa, depois de saber quem ele é. */
.sv-wrap.funil #oc-esc-card{order:2}
.sv-wrap.funil .oc-grid{order:3}
.sv-wrap.funil #ct-card{order:4}
.sv-wrap.funil #ad-card{order:5}

.fn-cab{display:flex; align-items:center; justify-content:space-between; gap:.6rem;
  flex-wrap:wrap; margin-bottom:.7rem}
.fn-novo{border:0; border-radius:10px; padding:.6rem 1.1rem; font-weight:700;
  font-size:.92rem; cursor:pointer; width:auto; margin:0}

/* AS TRES ABAS. O numero e o que responde "quanto me falta hoje" antes de
   qualquer clique - por isso vem do servidor e aparece sem a pessoa pedir. */
.fn-abas{display:flex; gap:.4rem; flex-wrap:wrap; margin-bottom:.6rem}
.fn-aba{display:inline-flex; align-items:center; gap:.45rem; padding:.5rem .85rem;
  border-radius:99px; border:1px solid var(--borda); background:var(--card-2);
  color:var(--txt-mut); cursor:pointer; font-size:.84rem; font-weight:600;
  font-family:inherit; width:auto; margin:0}
.fn-aba .pt{width:7px; height:7px; border-radius:50%; flex:none}
.fn-aba .n{font-variant-numeric:tabular-nums; opacity:.8}
.fn-aba.on{border-color:var(--verde); background:var(--neon-fundo); color:var(--verde-claro)}

.fn-filtros{display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin-bottom:.8rem}
.fn-filtros .oc-inp{width:auto; flex:1 1 220px; min-width:0; padding:.45rem .7rem; font-size:.88rem}
.fn-vend{display:flex; align-items:center; gap:.4rem; font-size:.8rem; flex:0 1 auto}
.fn-vend select{flex:0 1 auto; width:auto; max-width:190px}
.fn-vazio{font-size:.86rem; padding:.9rem 0 .2rem}

/* A DATA ABRE A LINHA. No evento e ela que identifica o negocio - e o que o dono
   procura, o que nao pode ser vendido duas vezes, e o que falta quando algo deu
   errado. Antes a linha abria com a inicial do nome, que e o que se usa quando
   nao se tem nada melhor. */
.oc-data{width:58px; flex:none; text-align:center; border-radius:9px;
  padding:.3rem .15rem; margin-right:.7rem; border:1px solid var(--borda);
  background:var(--card-2); color:var(--txt-mut)}
.oc-data .d{font-size:1.05rem; font-weight:700; line-height:1.1; color:var(--txt)}
.oc-data .m{font-size:.6rem; letter-spacing:.05em; text-transform:uppercase}
/* sem data e problema, e problema e coral - e a proposta no 22, R$ 9.650 ja
   enviada ao cliente sem data nenhuma. */
.oc-data.vazia{border-color:var(--coral-borda); background:var(--coral-fundo); color:var(--verm)}
.oc-data.vazia .d{color:var(--verm)}

/* ============================== COBRAR x INCLUSO, no lugar do desconto de 100%
   122 das 217 linhas da Prime usavam "100% de desconto" pra dizer "vem junto no
   pacote". Agora existe a palavra. */
.oc-cob{display:flex; border:1px solid var(--borda); border-radius:99px;
  overflow:hidden; height:28px; width:126px; flex:none}
.oc-cob button{flex:1; border:0; cursor:pointer; font-size:.68rem; font-weight:700;
  font-family:inherit; width:auto; margin:0; padding:0; background:var(--card-2);
  color:var(--txt-mut); letter-spacing:.02em}
.oc-cob button.on{background:var(--verde); color:var(--sobre-verde)}
.oc-cob button.on.inc{background:var(--neon-borda); color:var(--verde2)}
.oc-mod.incluso .oc-nome b{color:var(--txt-mut)}
.oc-sub b.incl{color:var(--verde-claro); font-size:.82rem}
/* o resumo do que vem junto: tracejado, porque nao entra na conta */
.oc-inclbox{margin-top:.8rem; padding:.6rem .7rem; border-radius:10px;
  background:var(--card-2); border:1px dashed var(--neon-borda)}
.oc-inclbox .lin{display:flex; align-items:baseline; justify-content:space-between; gap:.5rem}
.oc-inclbox .lin span{font-size:.78rem; color:var(--verde-claro); font-weight:600}
.oc-inclbox .lin b{font-size:.88rem; color:var(--verde-claro); font-variant-numeric:tabular-nums}
.oc-inclbox p{margin:.3rem 0 0; font-size:.72rem; line-height:1.5; color:var(--txt-mut)}

/* AVISO COM O CONSERTO DO LADO. Aviso que so aponta o problema deixa a pessoa
   procurando onde arrumar - e ai ela nao arruma. */
.oc-avi{display:flex; gap:.55rem; align-items:flex-start; border-radius:9px;
  padding:.55rem .7rem; margin-top:.7rem; font-size:.79rem; line-height:1.5}
.oc-avi.amb{background:var(--ambar-fundo); border:1px solid var(--ambar-borda); color:var(--amar)}
.oc-avi.cor{background:var(--coral-fundo); border:1px solid var(--coral-borda); color:var(--verm)}
.oc-avi .txt{flex:1; min-width:0}
.oc-avi button{border:0; border-radius:8px; padding:.4rem .7rem; font-size:.76rem;
  font-weight:700; cursor:pointer; white-space:nowrap; font-family:inherit; width:auto; margin:0}
.oc-avi.amb button{background:var(--ambar); color:#241C0F}
.oc-avi.cor button{background:var(--coral); color:#241313}

/* ============ A BARRA DE TOTAL NO CELULAR
   Abaixo de 820px a grade vira uma coluna e o Resumo cai DEPOIS dos servicos e
   do plano de pagamento: monta-se o orcamento inteiro sem ver o valor. E
   justamente o vendedor, no telefone, que perde isso. */
.oc-barra{display:none}
@media(max-width:820px){
  .oc-barra.on{display:flex; position:fixed; left:0; right:0; bottom:0; z-index:55;
    align-items:center; gap:.7rem; padding:.55rem .8rem calc(.55rem + env(safe-area-inset-bottom));
    background:var(--card); border-top:1px solid var(--neon-borda);
    box-shadow:0 -8px 24px rgba(0,0,0,.45)}
  .oc-barra .vl{min-width:0}
  .oc-barra .rot{font-size:.62rem; text-transform:uppercase; letter-spacing:.07em;
    color:var(--verde-claro); font-weight:700}
  .oc-barra .num{font-size:1.25rem; font-weight:700; color:var(--verde-claro);
    line-height:1.15; font-variant-numeric:tabular-nums}
  .oc-barra .leg{font-size:.65rem; color:var(--txt-mut)}
  .oc-barra .esp{flex:1}
  .oc-barra button{min-height:44px; border-radius:11px; font-weight:700;
    font-size:.85rem; cursor:pointer; padding:0 .9rem; width:auto; margin:0}
  .oc-barra .g{border:0; background:var(--verde); color:var(--sobre-verde)}
  .oc-barra .o{border:1px solid var(--borda); background:var(--bg); color:var(--txt)}
  .sv-wrap.funil{padding-bottom:5.5rem}
}
@media(max-width:640px){
  .fn-cab .fn-novo{width:100%}
  .fn-aba{flex:1 1 auto; justify-content:center}
  .oc-cob{width:112px}
}

/* ================= VALORES E PÍLULAS (24/09/2026) =================
   Mockup docs/mockups/zaq_servicos_valores_pilulas.html, aprovado pelo dono pros
   DOIS nichos: dinheiro com R$ e centavos em todo campo, e uma pílula só. */
/* o campo de dinheiro: R$ fixo à esquerda, valor com centavos à direita */
.oc-rsin{display:flex; align-items:center; gap:.3rem; min-width:0}
.oc-rs{font-style:normal; font-size:.72rem; color:var(--txt-mut); flex:none}
.oc-rsin input{flex:1; min-width:0}
/* o alternador %|R$ DENTRO do campo: segmento de 22px, não um botão verde ao lado */
.sv-wrap .oc-dpar{align-items:center; gap:.3rem; border:1px solid var(--borda); border-radius:8px;
  background:var(--bg); padding:0 .25rem 0 .5rem}
.sv-wrap .oc-dpar > input{border:0; background:transparent; border-radius:0; padding:.4rem 0; min-width:0}
.sv-wrap .oc-dpar > input:focus{outline:none}
.sv-wrap .oc-dpar[data-tipo="pct"] .oc-rs{display:none}
.sv-wrap .oc-dtog{flex:none; height:22px; border:1px solid var(--borda); border-radius:6px; align-self:center}
.sv-wrap .oc-dtog button{height:100%; min-width:0; padding:0 .42rem; font-size:.64rem; background:transparent;
  color:var(--txt-mut); font-weight:600}
.sv-wrap .oc-dtog button.on{background:#10241d; color:var(--verde-claro)}
.oc-desc-col .oc-dpar{border:0; padding:0; background:transparent}
/* a lixeira: ícone discreto, fica vermelho só quando o mouse passa */
.sv-wrap .oc-rm{border-color:transparent; background:transparent; color:var(--txt-mut)}
.sv-wrap .oc-rm:hover{border-color:var(--coral-borda,#5A2B2B); color:var(--verm,#E0574F); background:transparent}
/* A PÍLULA ÚNICA: mesma altura, borda e texto em todo botão-pílula da tela */
.sv-wrap .oc-pill{min-height:30px; padding:.25rem .8rem; font-size:.8rem; line-height:1.2;
  display:inline-flex; align-items:center; gap:.3rem}
/* o "anual à vista" é liga/desliga: interruptor, não ↻/✓ */
.sv-wrap #oc-anual{min-height:40px; border-radius:11px}
.oc-sw{width:34px; height:20px; border-radius:99px; background:#243029; position:relative; flex:none}
.oc-sw::after{content:""; position:absolute; top:3px; left:3px; width:14px; height:14px; border-radius:50%;
  background:#9aa9a0; transition:left .15s}
#oc-anual[data-on="1"] .oc-sw{background:var(--verde)}
#oc-anual[data-on="1"] .oc-sw::after{left:17px; background:#fff}
/* `min-height:0`: o CSS global do painel dá `button{min-height:48px}`, e era ele
   que empurrava o % | R$/mês pra fora do segmento de 22px */
.sv-wrap .oc-dtog button{white-space:nowrap; line-height:1; width:auto; margin:0; min-height:0}
/* as linhas do resumo: o valor nunca quebra, e o rótulo encolhe primeiro */
.oc-ll{gap:.6rem}
.oc-ll span{min-width:0}
.oc-ll b{white-space:nowrap; font-size:1rem}
/* as pílulas dos parâmetros e o passo de integrações: a mesma altura da pílula */
.sv-wrap .oc-seg button{min-height:30px; padding:.25rem .75rem; font-size:.8rem; border-radius:99px; line-height:1.2}
.sv-wrap .oc-step button{height:30px; width:30px; min-height:0; border-radius:99px; font-size:.95rem}
/* o número grande do recorrente: a mensalidade, com o "/mês" pequeno ao lado */
.oc-total .oc-per{font-size:.8rem; font-weight:500; color:var(--txt-mut)}
"""

_CSS = f'<link rel="stylesheet" href="{_estaticos.registrar("servicos.css", _CSS_CRU)}">'

# A REGRA QUE DECIDE DE ONDE VEM CADA LINHA DO ORÇAMENTO DE EVENTO — sozinha,
# sem DOM, porque é a única parte desta tela que, errada, APAGA coisa do cliente
# (regra 0 do CLAUDE.md). Fora do IIFE e com nome próprio pra `tests/
# test_servicos_orfaos.py` poder rodá-la no node; o CI desta base só roda pytest,
# então o teste chama o node de dentro do pytest.
#
# O problema que ela resolve: a linha era montada cruzando `modulos` (lista de
# slugs) com o catálogo ATIVO. Se o serviço saiu do catálogo, ou se a proposta
# não tem `modulos` (o orçamento nº 22 da Prime tem OITO itens e `modulos` nulo),
# a linha sumia da tela — e o "Salvar no funil" seguinte grava `itens` a partir
# da TELA, apagando o que o cliente já tinha recebido.
_JS_PAREAR_CRU = r"""window.ZAQ_PAREAR = function (mods, itens, catalogo) {
  mods = mods || []; itens = itens || []; catalogo = catalogo || [];

  var noCatalogo = {}, contagemNome = {}, slugDoNome = {};
  catalogo.forEach(function (s) {
    noCatalogo[s.slug] = true;
    var n = s.nome || '';
    contagemNome[n] = (contagemNome[n] || 0) + 1;
    slugDoNome[n] = s.slug;
  });

  // Sem itens gravados (proposta anterior à coluna `itens`), o slug é tudo que
  // existe: continua valendo o que sempre valeu.
  if (!itens.length) {
    return mods.map(function (slug) {
      return { slug: slug, item: null, orfao: !noCatalogo[slug] };
    });
  }

  // `coletarBody` monta `modulos` e `itens` varrendo as MESMAS linhas, na mesma
  // ordem. Mesmo tamanho, então, quer dizer que o índice casa e o slug é
  // confiável. Tamanho diferente só acontece quando algum slug se perdeu no
  // caminho (o servidor filtra `modulos` contra o catálogo ativo em `salvar`),
  // e aí parear por índice trocaria o serviço de uma linha pelo de outra —
  // pior do que não parear.
  var porIndice = mods.length === itens.length;

  return itens.map(function (it, i) {
    var slug = porIndice ? mods[i] : '';
    if (!slug) {
      var nome = it.nome || '';
      // o nome só serve quando é ÚNICO no catálogo. Dois serviços com o mesmo
      // nome não dizem qual é qual, e chutar aqui colocaria o slug do errado em
      // `modulos` — o preço de um item viraria o do outro na próxima abertura.
      if (contagemNome[nome] === 1) slug = slugDoNome[nome];
    }
    // id sintético: só existe dentro desta tela. `coletarBody` não o manda pro
    // banco, então ele nunca polui `modulos`.
    if (!slug) slug = 'orfao:' + i + ':' + (it.nome || '');
    return { slug: slug, item: it, orfao: !noCatalogo[slug] };
  });
};
"""


_JS_CRU = r"""(function(){
  var SERVICO_AVULSO = window.SERVICO_AVULSO;
  var INFRA={compartilhada:{s:0,m:0},dedicada:{s:1500,m:800},onpremise:{s:6000,m:1500}};
  // DINHEIRO COM CENTAVOS (24/09/2026, dono: "sim aceitar centavos", pros dois
  // nichos). `fmt` escreve R$ 1.397,50; `dinTxt` o mesmo sem o cifrão, pro campo.
  function r2(n){return Math.round((+n||0)*100)/100;}
  function dinTxt(n){return r2(n).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});}
  function fmt(n){return 'R$ '+dinTxt(n);}
  // Lê o que está no campo: "1.397,50", "1397,5", "1.500" (milhar) e também o
  // número cru que o próprio JS pôs lá ("1397.5" — um ponto com 1 ou 2 casas no
  // fim é decimal; com 3, é milhar). Nunca negativo.
  function lerReais(s){
    s=String(s==null?'':s).replace(/[^\d,.]/g,'');
    if(!s) return 0;
    if(s.indexOf(',')>=0) s=s.replace(/\./g,'').replace(',','.');
    else if(!/^\d+\.\d{1,2}$/.test(s)) s=s.replace(/\./g,'');
    var v=parseFloat(s);
    return isFinite(v)?Math.max(0,r2(v)):0;
  }
  function rows(){return [].slice.call(document.querySelectorAll('.oc-mod'));}
  function num(el){return lerReais(el&&el.value);}
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
  // RECORRENTE: R$ no item é POR MÊS (finance/desconto.por_mes, 23/09/2026). Sai
  // inteiro da mensalidade e não toca a implantação. O % continua como sempre.
  function porMes(r){
    if(SERVICO_AVULSO) return false;
    var par=r.querySelector('.oc-dpar');
    return !!par && par.getAttribute('data-tipo')==='valor';
  }
  // setup e mensal da linha depois do desconto dela — a MESMA conta de
  // `finance.desconto.liquido_do_item`
  function liqLinha(r, sb, mb){
    if(porMes(r)) return {s:sb, m:Math.max(0, mb-num(r.querySelector('.oc-desc')))};
    var p=pctLinha(r);
    return {s:Math.round(sb*(100-p)/100), m:Math.round(mb*(100-p)/100)};
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
    var setup=0,mensal=0,modMensal=0,custo=0,mods=0,descItens=0,descItensMes=0;
    var setupBruto=0,mensalBruto=0;
    // INCLUSO NO PACOTE sai por fora: não soma no total e não é desconto. É a
    // conta que o resumo passa a mostrar no lugar de "Economia de R$ 14.850"
    // numa proposta onde ninguém descontou nada. Ver finance/desconto.eh_incluso.
    var inclusos=0, inclusoValor=0;
    rows().forEach(function(r){
      if(r.getAttribute('data-on')==='1'){
        mods++;
        var q=qtd(r);
        var incl=r.getAttribute('data-incluso')==='1';
        var sbTodo=num(r.querySelector('.oc-setup'))*q;
        if(incl){
          inclusos++; inclusoValor+=sbTodo;
          // o custo CONTINUA contando: o que vem junto no pacote custa dinheiro
          // pra empresa, e tirá-lo da margem faria a margem mentir pra cima
          // exatamente na hora de decidir o que dá pra incluir.
          custo+=num(r.querySelector('.oc-custo'))*q;
          return;
        }
        var sb=sbTodo, mb=num(r.querySelector('.oc-mensal'));
        var lq=liqLinha(r, sb, mb), sl=lq.s, ml=lq.m;
        descItens+=(sb-sl)+(mb-ml)*12;
        descItensMes+=(mb-ml);        // o resumo do recorrente fala POR MÊS
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
              inclusos:inclusos,inclusoValor:inclusoValor,
              cobrados:mods-inclusos,
              setupBruto:setupBruto,mensalBruto:0};
    }
    var anual=document.getElementById('oc-anual').getAttribute('data-on')==='1';
    var mensalEf=anual?mensal*0.85:mensal;
    var sub=setup+mensalEf*12;
    var dFim=descFinal(sub), ano1=sub-dFim;
    var margem=modMensal-custo, margemPct=modMensal>0?Math.round(margem/modMensal*100):0;
    // A MENSALIDADE QUE O CLIENTE PAGA: o desconto no total cai proporcional nas
    // duas pontas (finance/desconto.totais), então a mensal final é a mensal
    // efetiva na mesma proporção em que o total caiu.
    var mensalFinal=sub>0?mensalEf*(ano1/sub):mensalEf;
    return {setup:setup,mensal:mensalEf,mensalCheio:mensal,ano1:ano1,margem:margem,
            mensalFinal:mensalFinal,setupFinal:(sub>0?setup*(ano1/sub):setup),
            descItensMes:descItensMes,mensalTabela:mensalBruto,
            margemPct:margemPct,mods:mods,anual:anual,subtotal:sub,
            descItens:descItens,descFim:dFim,economia:descItens+dFim,
            inclusos:inclusos,inclusoValor:inclusoValor,cobrados:mods-inclusos,
            setupBruto:setupBruto,mensalBruto:(anual?mensalBruto*0.85:mensalBruto),
            // a CHEIA, que é o que vai pro servidor desde a 311: ele aplica o -15%
            // sabendo que é anual, em vez de receber o número já reduzido
            mensalBrutoCheio:mensalBruto};
  }

  function pinta(){
    var c=calc();
    // subtotal de cada linha (qtd × valor unitário), ao vivo
    rows().forEach(function(r){
      // RECORRENTE: o que a linha cobra por mês depois do desconto dela. Na HLED
      // o dono deu R$ 500 esperando R$ 1.000/mês e não tinha onde conferir.
      var rl=r.querySelector('.oc-rliq');
      if(rl){
        var mb=num(r.querySelector('.oc-mensal')), lq=liqLinha(r, 0, mb);
        rl.textContent=(lq.m<mb)?('= '+fmt(lq.m)+'/mês'):'';
      }
      var el=r.querySelector('.oc-sub-v');
      if(!el) return;
      if(r.getAttribute('data-incluso')==='1'){
        // a palavra no lugar do zero: é o que o vendedor quer dizer, e é o que
        // a folha do cliente passa a imprimir.
        el.className='oc-sub-v incl'; el.textContent='Incluso';
        return;
      }
      el.className='oc-sub-v';
      var sb=num(r.querySelector('.oc-setup'))*qtd(r), p=pctLinha(r);
      var sl=Math.round(sb*(100-p)/100);
      // o cheio riscado só aparece QUANDO há desconto — riscar um valor igual ao
      // de baixo seria ruído.
      el.innerHTML=(sl<sb?'<span class="oc-sub-risc">'+fmt(sb)+'</span>':'')+fmt(sl);
    });
    // RECORRENTE (24/09/2026, pergunta 3 do mockup): o número grande é o que o
    // cliente paga POR MÊS; implantação sem valor vira "sem taxa". No evento
    // nada muda — o número grande continua sendo o total.
    var elSetup=document.getElementById('oc-r-setup');
    elSetup.textContent=(!SERVICO_AVULSO&&!(c.setup>0))?'sem taxa':fmt(SERVICO_AVULSO?c.setup:c.setupFinal);
    var elMensal=document.getElementById('oc-r-mensal');
    if(elMensal)elMensal.textContent=c.anual?fmt(c.mensalFinal*12):fmt(c.mensalFinal);
    var rotM=document.getElementById('oc-r-mensal-rot');
    if(rotM)rotM.textContent=c.anual?'Anual à vista (12 meses)':'Investimento mensal';
    var perM=document.getElementById('oc-r-mensal-per');
    if(perM)perM.style.display=c.anual?'none':'';
    var elTab=document.getElementById('oc-r-tabela');
    if(elTab)elTab.textContent=fmt(c.mensalTabela);
    document.getElementById('oc-r-ano').textContent=fmt(c.ano1);
    document.getElementById('oc-r-margem').textContent=fmt(c.margem)+' · '+c.margemPct+'%';
    // as três linhas do desconto: só aparecem quando existem, pra o resumo de quem
    // não usa desconto continuar do tamanho que sempre teve.
    function mostra(idL,id,val){
      var l=document.getElementById(idL); if(!l) return;
      l.style.display=val>0?'flex':'none';
      if(val>0) document.getElementById(id).textContent='− '+fmt(val);
    }
    if(SERVICO_AVULSO) mostra('oc-r-descitens-l','oc-r-descitens',c.descItens||0);
    else {
      // o desconto dos serviços POR MÊS: "R$ 32.400" (o do ano) lia como um
      // desconto de 32 mil numa proposta de R$ 3.000/mês (HLED, 23/09/2026)
      mostra('oc-r-descitens-l','oc-r-descitens',c.descItensMes||0);
      var di=document.getElementById('oc-r-descitens');
      if(di&&(c.descItensMes||0)>0) di.textContent='− '+fmt(c.descItensMes)+'/mês';
    }
    mostra('oc-r-descfim-l','oc-r-descfim',c.descFim||0);
    var subL=document.getElementById('oc-r-sub-l');
    if(subL){
      var temDesc=(c.descItens||0)>0&&(c.descFim||0)>0;
      subL.style.display=temDesc?'flex':'none';
      if(temDesc) document.getElementById('oc-r-sub').textContent=fmt(c.subtotal||0);
    }
    var eco=document.getElementById('oc-r-eco');
    if(SERVICO_AVULSO&&(c.economia||0)>0){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.economia);}
    else if(!SERVICO_AVULSO&&c.anual){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.mensalFinal/0.85*12*0.15)+' no ano';}
    else eco.style.display='none';
    // O QUE VEM JUNTO NO PACOTE, dito com a palavra certa. Fica fora do total de
    // propósito: é o que separa "nove itens inclusos, R$ 13.850 de tabela" de
    // "Economia de R$ 14.850" — que era o que a tela dizia numa proposta onde
    // ninguém tinha descontado nada.
    var cxInc=document.getElementById('oc-inclusos');
    if(cxInc){
      var temInc=(c.inclusos||0)>0;
      cxInc.style.display=temInc?'block':'none';
      if(temInc){
        document.getElementById('oc-incl-n').textContent=
          c.inclusos+(c.inclusos===1?' item':' itens');
        document.getElementById('oc-incl-v').textContent=fmt(c.inclusoValor||0);
      }
    }
    // A BARRA DO CELULAR. Mesmo número do resumo, lido da mesma conta — dois
    // lugares somando por conta própria seriam dois números.
    var bt=document.getElementById('barra-total');
    if(bt){
      // recorrente: a barra mostra a mensalidade (o número grande do resumo)
      bt.textContent=SERVICO_AVULSO?fmt(c.ano1):(c.anual?fmt(c.mensalFinal*12):fmt(c.mensalFinal)+'/mês');
      var leg=document.getElementById('barra-leg');
      // a legenda fala a língua de cada nicho: no evento, o que é cobrado e o
      // que vem no pacote; no recorrente, quantos serviços e a mensalidade.
      if(leg) leg.textContent=SERVICO_AVULSO
        ? (c.cobrados||0)+' cobrados'+((c.inclusos||0)?' · '+c.inclusos+' inclusos':'')
        : (c.mods||0)+(c.mods===1?' serviço':' serviços')+' · 1º ano '+fmt(c.ano1)+(c.anual?' · anual à vista':'');
    }
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
    // trocou pra R$: o número ganha os centavos; trocou pra %: fica sem eles
    var inp=par.querySelector('input');
    if(inp) inp.value=(b.getAttribute('data-t')==='valor')?dinTxt(num(inp)):String(r2(num(inp))).replace('.',',');
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
    // Cobrar × Incluso só existe na linha do evento; a do recorrente cai direto
    // no 🗑, que é o mesmo nos dois.
    {
      var cb=e.target.closest('.oc-cob button');
      if(cb){
        var rowc=cb.closest('.oc-mod'); if(!rowc) return;
        var vira=cb.getAttribute('data-cob')==='1';
        rowc.setAttribute('data-incluso',vira?'1':'0');
        rowc.classList.toggle('incluso',vira);
        rowc.querySelectorAll('.oc-cob button').forEach(function(x){
          x.classList.toggle('on',x===cb);
        });
        // VOLTAR PRA "COBRAR" ZERA O DESCONTO DA LINHA. Sem isto, quem marcou
        // incluso e se arrependeu receberia a linha de volta com 100% de
        // desconto gravado — ou seja, valendo zero e parecendo cobrada.
        if(!vira){ var di=rowc.querySelector('.oc-desc'); if(di) di.value='0'; }
        pinta();
        return;
      }
      var rm=e.target.closest('.oc-rm');
      if(!rm) return;
      var row=rm.closest('.oc-mod'); if(!row) return;
      delete SELECIONADOS[row.getAttribute('data-id')];
      renderCatalogoAvulso();
      return;
    }
  });
  // PONTO DE MILHAR NA LINHA DO RECORRENTE: "1.200", não "1200" — é como o
  // resumo ao lado escreve, e "MENSAL 1200" numa caixa estreita já foi lido
  // como 120. Só ao SAIR do campo (formatar enquanto digita pularia o cursor),
  // e `num()` descarta o ponto na leitura, então a conta não muda.
  // Desde 24/09/2026 vale nos DOIS nichos e escreve os CENTAVOS ("1.500,00"):
  // implantação, mensal, valor unitário, custo e o desconto quando é em R$.
  function milhar(root){
    (root||MODS).querySelectorAll('.oc-mod .oc-setup,.oc-mod .oc-mensal,.oc-mod .oc-custo,.oc-mod .oc-desc').forEach(function(i){
      if(document.activeElement===i) return;
      if(i.classList.contains('oc-desc')){
        var par=i.closest('.oc-dpar');
        if(!par||par.getAttribute('data-tipo')!=='valor') return;   // % fica como digitou
      }
      i.value=dinTxt(num(i));
    });
  }
  MODS.addEventListener('focusout',function(){ milhar(); });
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
      // o "anual" virou interruptor (24/09/2026): quem desenha ligado/desligado é
      // o CSS pelo data-on do botão — o ↻/✓ de texto saiu.
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
  // A HORA QUE O SERVIDOR ENTENDE — espelho de finance/agenda._minutos, que é quem
  // transforma o orçamento em compromisso. tests/test_evento_hora.py cruza as duas.
  function horaOk(h){
    var s=String(h==null?'':h).trim().toLowerCase().replace(/h/g,':').replace(/:+$/,'');
    if(!s)return false;
    var p=s.split(':');
    if(!/^[+-]?\d+$/.test(p[0]))return false;
    if(p.length>1&&p[1]!==''&&!/^[+-]?\d+$/.test(p[1]))return false;
    var hh=parseInt(p[0],10), mm=(p.length>1&&p[1]!=='')?parseInt(p[1],10):0;
    return hh>=0&&hh<=24&&mm>=0&&mm<60;
  }
  // O aviso da hora que falta. Aparece só quando há DATA: orçamento sem data
  // nenhuma não prometeu nada e não tem o que avisar.
  //
  // Cobre DOIS casos, e o segundo entrou em 01/09/2026: hora em branco, e hora
  // escrita de um jeito que o sistema não lê. O segundo é o pior dos dois porque na
  // tela parece preenchido — o vendedor da Prime escreveu "18:00/23:40" no Início,
  // `janela_evento` devolveu (None, None), e a data do casamento não ficou segurada
  // sem nenhum aviso em lugar nenhum.
  function pintarSemHora(){
    var el=document.getElementById('ev-sem-hora');
    if(!el)return;
    var t=document.getElementById('ev-sem-hora-t');
    var d=document.getElementById('ev-sem-hora-d');
    var temData=!!(document.getElementById('ev-data')||{}).value;
    var ini=((document.getElementById('ev-ini')||{}).value||'').trim();
    var fim=((document.getElementById('ev-fim')||{}).value||'').trim();
    var tit='', txt='';
    if(temData&&!ini){
      tit='Sem a hora de início, esta data não entra na agenda.';
      txt='Pode salvar assim — mas a data só fica segurada quando você preencher o Início.';
    }else if(ini&&!horaOk(ini)){
      tit='Não entendi o horário de início.';
      txt='Escreva só a hora — 19:00, 19h ou 19h30. Do jeito que está, a data não fica '
        +'segurada na agenda.';
    }else if(fim&&!horaOk(fim)){
      tit='Não entendi o horário de encerramento.';
      txt='Escreva só a hora — 24:00, 02h ou 23h30.';
    }
    if(t)t.textContent=tit;
    if(d)d.textContent=txt;
    el.style.display=tit?'block':'none';
  }
  if(SERVICO_AVULSO){
    ['ev-data','ev-ini','ev-fim'].forEach(function(id){
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
    // quantas parcelas estão sem data. `coletarParcelas` já normaliza o campo.
    var semVenc=coletarParcelas().filter(function(p){return !p.venc;}).length;
    var cx=document.getElementById('pg-sem-venc');
    if(cx){
      cx.style.display=semVenc?'flex':'none';
      if(semVenc) document.getElementById('pg-sem-venc-t').textContent=
        (semVenc===1?'1 parcela está sem data de vencimento.'
                    :'As '+semVenc+' parcelas estão sem data de vencimento.');
    }
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
  // OS ÓRFÃOS: serviços que a proposta TEM e o catálogo NÃO tem mais.
  //
  // A linha do editor era montada cruzando `modulos` (a lista de slugs) com o
  // catálogo ATIVO. Duas maneiras de a linha simplesmente sumir da tela:
  //   1. o serviço foi excluído do catálogo depois (a exclusão é soft, mas
  //      `scat.listar` só devolve ativo=true);
  //   2. a proposta não tem `modulos` — é o caso do orçamento nº 22 da Prime,
  //      salvo com `modulos` nulo e OITO itens gravados.
  // Em qualquer um dos dois a folha do cliente continua certa (ela lê `itens`),
  // mas o editor abria sem a linha — e o "Salvar no funil" seguinte grava
  // `itens` a partir do que está na TELA, ou seja, apaga o que o cliente já
  // recebeu. Regra 0 do CLAUDE.md: informação do cliente não se perde.
  //
  // Agora o que o catálogo não sabe explicar vem do próprio item salvo, e a
  // linha aparece do mesmo jeito — marcada, pra quem edita saber por que aquele
  // serviço não está na busca.
  var ORFAOS={};         // slug (ou id sintético) -> serviço reconstruído do item
  var VERTODOS_OPEN=false;
  var INATIVOS=[], INATIVOS_OPEN=false;
  // o serviço de um slug, venha ele do catálogo ou do item salvo.
  function servicoPorSlug(slug){
    for(var i=0;i<CATALOGO.length;i++){ if(CATALOGO[i].slug===slug) return CATALOGO[i]; }
    return ORFAOS[slug]||null;
  }
  function ec(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

  // A célula de desconto da LINHA. Mesmo par do desconto do total — a forma se
  // repete porque a ideia é a mesma.
  function descTipoTot(){
    var par=document.querySelector('.oc-dpar-tot');
    return (par&&par.getAttribute('data-tipo')==='valor')?'valor':'pct';
  }
  // o campo de dinheiro da linha: R$ fixo à esquerda e o valor com centavos
  function campoRS(cls,v){
    return '<div class="oc-rsin"><i class="oc-rs">R$</i><input class="'+cls+'" inputmode="decimal" value="'+dinTxt(v)+'"></div>';
  }
  function celDesc(tipo,val){
    var t=(tipo==='valor')?'valor':'pct';
    return '<div class="oc-num oc-desc-col"><span>Desconto</span>'
      +'<div class="oc-dpar" data-tipo="'+t+'"><i class="oc-rs">R$</i>'
      +'<input class="oc-desc" inputmode="decimal" value="'+(t==='valor'?dinTxt(val):String(r2(val)).replace('.',','))+'">'
      +'<span class="oc-dtog">'
      +'<button type="button" data-t="pct" class="'+(t==='pct'?'on':'')+'">%</button>'
      +'<button type="button" data-t="valor" class="'+(t==='valor'?'on':'')+'"'
      +(SERVICO_AVULSO?'>R$':' title="Reais a menos na mensalidade, todo mês">R$/mês')+'</button>'
      +'</span></div>'
      +(SERVICO_AVULSO?'':'<small class="oc-rliq"></small>')+'</div>';
  }

  // eventos: catálogo ordenado A-Z + "busca pra adicionar" — a lista só mostra
  // o que já foi escolhido pra esta proposta; o resto fica atrás da busca ou
  // do link "ver todos", pra não repetir o card gigante de antes com 26 linhas
  // sempre visíveis.
  function buildRowAvulso(s){
    var thumb='<div class="svc-thumb">'+(s.icone_svg||'')+'</div>';
    // o selo do órfão: a linha vale e é editável como qualquer outra, mas quem
    // procurar esse serviço na busca não vai achar — e é melhor dizer isso do
    // que deixar a pessoa procurando.
    var selo=s.orfao
      ? '<span class="oc-fora" title="Este serviço saiu do seu catálogo. A linha continua valendo nesta proposta.">fora do catálogo</span>'
      : '';
    // COBRAR × INCLUSO no lugar do interruptor liga/desliga. O interruptor
    // servia pra remover da proposta — trabalho que o 🗑 do fim da linha já faz,
    // e fazia melhor. A coluna é a mesma, então a grade não muda.
    //
    // O que entra aqui é a palavra que faltava: em 122 das 217 linhas da Prime o
    // vendedor escrevia "100" no desconto pra dizer "vem junto no pacote".
    var inc = !!s.incluso;
    var cob = '<div class="oc-cob" role="group" aria-label="Como esta linha é cobrada">'
      + '<button type="button" class="cb' + (inc ? '' : ' on') + '" data-cob="0">Cobrar</button>'
      + '<button type="button" class="cb inc' + (inc ? ' on' : '') + '" data-cob="1"'
      + ' title="Entra na proposta e aparece pro cliente, mas não é cobrado">Incluso</button>'
      + '</div>';
    return cob
      +'<div class="oc-nome oc-nome-linha">'+thumb+'<div style="min-width:0">'
      +(s.categoria?'<div class="oc-cat">'+ec(s.categoria)+'</div>':'')
      +'<b>'+ec(s.nome)+'</b>'+selo+'<div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div></div>'
      +'<div class="oc-num"><span>Qtd</span><input class="oc-qtd" inputmode="numeric" value="1"></div>'
      +'<div class="oc-num"><span>Vr. unit.</span>'+campoRS('oc-setup',s.setup)+'</div>'
      +'<div class="oc-num oc-custo-col"><span>Custo</span>'+campoRS('oc-custo',s.custo)+'</div>'
      +celDesc(s.desc_tipo,s.desc_val)
      +'<div class="oc-sub"><span>Subtotal</span><b class="oc-sub-v'+(inc?' incl':'')+'">'
        +(inc?'Incluso':fmt(s.setup))+'</b></div>'
      +'<div class="oc-rowacts"><button class="oc-ic oc-rm" type="button" title="Remover da proposta">🗑</button></div>';
  }
  // recorrente: a mesma lista "só o que está nesta proposta" do evento, com as
  // duas pontas do dinheiro (setup e mensalidade) no lugar de qtd × unitário.
  // Sem Cobrar × Incluso: a palavra "incluso" foi desenhada pro pacote de festa
  // (seção 6 do CLAUDE.md). O ícone entrou em 23/09/2026, com o jogo PRÓPRIO do
  // recorrente (anúncio, redes, vídeo, IA...), o mesmo que sai na folha.
  function buildRowRec(s){
    var selo=s.orfao
      ? '<span class="oc-fora" title="Este serviço saiu do seu catálogo. A linha continua valendo nesta proposta.">fora do catálogo</span>'
      : '';
    var thumb=s.icone_svg?'<div class="svc-thumb">'+s.icone_svg+'</div>':'';
    return '<div class="oc-nome oc-nome-linha">'+thumb+'<div style="min-width:0"><b title="'+ec(s.nome)+'">'+ec(s.nome)+'</b>'+selo
      +'<div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div></div>'
      +'<div class="oc-num"><span>Implantação</span>'+campoRS('oc-setup',s.setup)+'</div>'
      +'<div class="oc-num"><span>Mensal</span>'+campoRS('oc-mensal',s.mensal)+'</div>'
      +'<div class="oc-num oc-custo-col"><span>Custo/mês</span>'+campoRS('oc-custo',s.custo)+'</div>'
      +celDesc(s.desc_tipo,s.desc_val)
      +'<div class="oc-rowacts"><button class="oc-ic oc-rm" type="button" title="Remover da proposta">🗑</button></div>';
  }
  // o nome ficou por razões históricas: desde 23/09/2026 é a lista dos DOIS
  // nichos (o recorrente passou a usar o mesmo "busca pra adicionar").
  function renderCatalogoAvulso(){
    var box=document.getElementById('oc-mods');
    var valores={};
    // `|| {}` em cada campo: a linha do recorrente não tem qtd, e a do evento
    // não tem mensalidade.
    rows().forEach(function(r){valores[r.getAttribute('data-id')]={setup:(r.querySelector('.oc-setup')||{}).value,mensal:(r.querySelector('.oc-mensal')||{}).value,custo:(r.querySelector('.oc-custo')||{}).value,qtd:(r.querySelector('.oc-qtd')||{}).value,incluso:r.getAttribute('data-incluso')==='1',desc:(r.querySelector('.oc-desc')||{}).value,tipo:((r.querySelector('.oc-dpar')||{getAttribute:function(){return 'pct';}}).getAttribute('data-tipo'))};});
    box.innerHTML='';
    // catálogo primeiro (ordem alfabética, como sempre); os órfãos entram no fim,
    // porque não têm lugar na ordem de uma lista onde não estão mais.
    var itens=CATALOGO.filter(function(s){return SELECIONADOS[s.slug];});
    Object.keys(ORFAOS).forEach(function(k){
      if(SELECIONADOS[k] && !CATALOGO.some(function(s){return s.slug===k;})) itens.push(ORFAOS[k]);
    });
    itens.forEach(function(s){
      var v0=valores[s.slug];
      // o "incluso" sobrevive à re-renderização junto com os valores: perder ele
      // ao adicionar outro serviço poria de volta no total o que vem no pacote.
      var inc0 = v0 ? !!v0.incluso : !!s.incluso;
      var r=document.createElement('div');
      r.className='oc-mod '+(SERVICO_AVULSO?'avulso':'rec')+(inc0?' incluso':'');
      r.setAttribute('data-id',s.slug); r.setAttribute('data-on','1');
      r.setAttribute('data-incluso', inc0?'1':'0');
      s = Object.assign({}, s, {incluso: inc0});
      r.setAttribute('data-nome',s.nome); r.setAttribute('data-desc',s.descricao||''); r.setAttribute('data-cid',s.id);
      r.innerHTML=SERVICO_AVULSO?buildRowAvulso(s):buildRowRec(s);
      box.appendChild(r);
      var v=valores[s.slug];
      if(v){ var volta=function(sel,val){var el=r.querySelector(sel); if(el&&val!==undefined&&val!==null) el.value=val;};
             volta('.oc-setup',v.setup); volta('.oc-mensal',v.mensal); volta('.oc-custo',v.custo);
             if(v.qtd) volta('.oc-qtd',v.qtd);
             volta('.oc-desc',v.desc);
             // o % × R$ da linha volta junto com o número: "200" em reais
             // relido como 200% viraria desconto total.
             var pr=r.querySelector('.oc-dpar');
             if(pr && v.tipo){ pr.setAttribute('data-tipo',v.tipo);
               pr.querySelectorAll('.oc-dtog button').forEach(function(x){x.classList.toggle('on',x.getAttribute('data-t')===v.tipo);}); } }
    });
    milhar(box);
    var vazioTotal=CATALOGO.length===0;
    box.style.display=itens.length?'block':'none';
    document.getElementById('oc-sel-empty').style.display=(itens.length||vazioTotal)?'none':'block';
    // "você ainda não cadastrou seus serviços" só quando não há MESMO nada na
    // tela: uma proposta antiga cheia de órfãos tem catálogo vazio e linhas.
    document.getElementById('oc-mods-empty').style.display=(vazioTotal&&!itens.length)?'block':'none';
    var busca=document.getElementById('oc-buscabox'); if(busca)busca.style.display=vazioTotal?'none':'block';
    var vt=document.getElementById('oc-vertodos');
    if(vt){
      vt.style.display=vazioTotal?'none':'inline-block';
      vt.innerHTML='📋 '+(VERTODOS_OPEN?'esconder a lista completa ‹':('ver os <span id="oc-vertodos-n">'+CATALOGO.length+'</span> serviços em ordem alfabética ›'));
    }
    // SERVIÇO SEM CATEGORIA nesta proposta. `_subtotais` (web/proposta.py) só
    // imprime o bloco por categoria quando TODOS os itens têm uma — um item sem
    // categoria derruba o bloco inteiro, e ninguém descobre por quê.
    var cxCat=document.getElementById('oc-sem-cat');
    if(cxCat){
      var semCat=itens.filter(function(x){return !(x.categoria||'').trim();}).length;
      cxCat.style.display=(semCat && itens.length)?'flex':'none';
      if(semCat) document.getElementById('oc-sem-cat-t').textContent=
        (semCat===itens.length
          ? (semCat===1?'Este serviço está sem categoria.'
                       :'Os '+semCat+' serviços desta proposta estão sem categoria.')
          : semCat+' de '+itens.length+' serviços desta proposta estão sem categoria.');
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
      // o preço de vitrine: um valor no evento, as duas pontas no recorrente
      var preco=SERVICO_AVULSO
        ? '<div class="oc-num"><span>Valor</span><input value="'+s.setup+'" readonly></div>'
        : '<div class="oc-num"><span>Setup · mensal</span><input value="'+s.setup+' · '+s.mensal+'/mês" readonly></div>';
      return '<div class="oc-browse-row'+(SERVICO_AVULSO?'':' rec')+'" data-id="'+ec(s.slug)+'"><button class="oc-tog'+(on?' on':'')+'" type="button" title="'+(on?'Remover da proposta':'Adicionar à proposta')+'"></button>'
        +'<div class="oc-nome"><b>'+ec(s.nome)+'</b><div class="mut oc-desc-preview" style="font-size:.78rem" title="'+ec(s.descricao||'')+'">'+ec(s.descricao||'')+'</div></div>'
        +preco
        +'<div class="oc-rowacts"><button class="oc-ic oc-edit" type="button" title="Editar serviço">✎</button><button class="oc-ic oc-del" type="button" title="Inativar serviço">⊘</button></div></div>';
    }).join('');
    // A GAVETA DOS INATIVOS, no fim da lista e fechada. O botão sempre foi
    // inativação (`ativo=false`, pra não quebrar orçamento antigo que aponta
    // pro slug), mas se chamava "excluir" e o item sumia sem volta. Na Prime
    // isso custou dois serviços recadastrados iguais — ver `listar_inativos`.
    if(INATIVOS.length){
      box.innerHTML += '<div class="oc-inativos'+(INATIVOS_OPEN?' open':'')+'">'
        +'<button type="button" class="oc-inativos-cab">'
        +'<span>'+(INATIVOS_OPEN?'▾':'▸')+' <b>'+INATIVOS.length+' serviço'+(INATIVOS.length===1?'':'s')+' inativo'+(INATIVOS.length===1?'':'s')+'</b></span>'
        +'<span class="mut">fora dos orçamentos novos</span></button>'
        +'<div class="oc-inativos-corpo">'
        +INATIVOS.map(function(s){
          return '<div class="oc-inativo-row" data-iid="'+s.id+'">'
            +'<div class="oc-nome"><b>'+ec(s.nome)+'</b>'
            +(s.parecido?'<div class="oc-inativo-av">⚠ já existe um ativo com nome parecido: “'+ec(s.parecido)+'”</div>':'')
            +'</div><button type="button" class="oc-reativar">reativar</button></div>';
        }).join('')
        +'</div></div>';
    }
  }

  function carregarCatalogo(preserva){
    return zapFetch('/painel/servicos/catalogo').then(function(d){if(!d)return;
      CATALOGO=d.itens||[];
      INATIVOS=d.inativos||[];
      var selCat=document.getElementById('svc-cat');
      if(selCat&&selCat.options.length<2){
        (d.categorias||[]).forEach(function(nome){
          var o=document.createElement('option'); o.value=nome; o.textContent=nome; selCat.appendChild(o);
        });
      }
      // A-Z e a purga que poupa o órfão valem pros DOIS nichos desde 23/09/2026:
      // a lista do recorrente passou a ser a mesma "só o que está na proposta".
      {
        CATALOGO.sort(function(a,b){return (a.nome||'').localeCompare(b.nome||'','pt-BR');});
        // A PURGA AGORA POUPA O ÓRFÃO. Esta linha apagava da seleção tudo que não
        // estivesse no catálogo ativo — e ela roda a cada recarga (salvar ou
        // excluir um serviço). Com a proposta aberta, excluir QUALQUER serviço do
        // catálogo tirava calado da tela o item que a proposta tinha, e o próximo
        // "Salvar no funil" gravava a proposta sem ele.
        Object.keys(SELECIONADOS).forEach(function(id){
          if(CATALOGO.some(function(s){return s.slug===id;})){ delete ORFAOS[id]; return; }
          if(!ORFAOS[id]) delete SELECIONADOS[id];
        });
        renderCatalogoAvulso();
      }
    });
  }
  {
    var ocBusca=document.getElementById('oc-busca'), ocDrop=document.getElementById('oc-drop');
    var renderDropBusca=function(){
      var q=(ocBusca.value||'').trim().toLowerCase();
      if(!q){ocDrop.style.display='none'; ocDrop.innerHTML=''; return;}
      var m=CATALOGO.filter(function(s){return !SELECIONADOS[s.slug] && (s.nome||'').toLowerCase().indexOf(q)>=0;});
      ocDrop.innerHTML = m.length
        ? m.slice(0,8).map(function(s){return '<div class="oc-drop-item" data-id="'+ec(s.slug)+'"><span class="nome">'+ec(s.nome)+'</span><span class="preco">'+fmt(s.setup)+(SERVICO_AVULSO?'':' + '+fmt(s.mensal)+'/mês')+'</span></div>';}).join('')
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
      var cab=e.target.closest('.oc-inativos-cab');
      if(cab){ INATIVOS_OPEN=!INATIVOS_OPEN; renderCatalogoCompleto(); return; }
      var rea=e.target.closest('.oc-reativar');
      if(rea){
        var linha=rea.closest('.oc-inativo-row');
        var iid=parseInt(linha.getAttribute('data-iid'),10);
        var inat=INATIVOS.filter(function(x){return x.id===iid;})[0];
        if(inat && inat.parecido && !confirm('Reativar "'+inat.nome+'"?\n\nJá existe um serviço ATIVO com nome parecido: "'+inat.parecido+'". Os dois vão aparecer juntos na lista dos orçamentos novos.')) return;
        rea.disabled=true;
        zapFetch('/painel/servicos/catalogo/reativar',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:iid})}).then(function(res){
          if(!res||!res.ok){rea.disabled=false;return;}
          INATIVOS_OPEN=true; carregarCatalogo(true);
        });
        return;
      }
      var ed=e.target.closest('.oc-edit'), dl=e.target.closest('.oc-del');
      if(ed){var row2=ed.closest('.oc-browse-row'); var s=CATALOGO.filter(function(x){return x.slug===row2.getAttribute('data-id');})[0]; if(s)abrirForm({id:s.id,nome:s.nome,descricao:s.descricao,setup:s.setup,mensal:s.mensal,custo:s.custo,categoria:s.categoria,icone:s.icone});}
      else if(dl){var row3=dl.closest('.oc-browse-row'); var s2=CATALOGO.filter(function(x){return x.slug===row3.getAttribute('data-id');})[0]; if(s2&&confirm(txtInativar(s2.nome))){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:s2.id})}).then(function(){carregarCatalogo(true);});}}
    });
  }
  // O texto do confirm mora num lugar só porque as DUAS listas (a completa e a
  // dos itens da proposta) fazem a mesma coisa pelo mesmo botão — e dizer
  // "excluir" numa e "inativar" na outra seria prometer dois efeitos diferentes
  // pra mesma rota.
  function txtInativar(nome){
    return 'Inativar "'+nome+'"?\n\nEle sai da lista e dos orçamentos NOVOS. '
      +'Os orçamentos já feitos não mudam, e dá pra reativar depois — '
      +'a gaveta "serviços inativos" fica no fim da lista completa.';
  }
  // editar / excluir (delegação)
  document.getElementById('oc-mods').addEventListener('click',function(e){
    var ed=e.target.closest('.oc-edit'), dl=e.target.closest('.oc-del');
    if(ed){var r=ed.closest('.oc-mod'); var sc=CATALOGO.filter(function(x){return x.slug===r.getAttribute('data-id');})[0]||{}; abrirForm({id:r.getAttribute('data-cid'),nome:r.getAttribute('data-nome'),descricao:r.getAttribute('data-desc'),setup:num(r.querySelector('.oc-setup')),mensal:num(r.querySelector('.oc-mensal')),custo:num(r.querySelector('.oc-custo')),categoria:sc.categoria,icone:sc.icone});}
    else if(dl){var r2=dl.closest('.oc-mod'); if(confirm(txtInativar(r2.getAttribute('data-nome')))){fetch('/painel/servicos/catalogo/excluir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:parseInt(r2.getAttribute('data-cid'),10)})}).then(function(){carregarCatalogo(true);});}}
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
    zapFetch('/painel/servicos/catalogo/icone-sugerido?nome='+encodeURIComponent(nome)
          +'&categoria='+encodeURIComponent(cat)).then(function(d){if(!d)return; svcIconeSugerido=(d&&d.chave)||'outros'; svcPintarIcones(); });
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
    document.getElementById('svc-setup').value=dinTxt(s.setup||0);
    document.getElementById('svc-mensal').value=dinTxt(s.mensal||0);
    document.getElementById('svc-custo').value=dinTxt(s.custo||0);
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
    zapFetch('/painel/servicos/catalogo/salvar',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(res){if(!res){b.disabled=false;return;}b.disabled=false; if(!res.ok){document.getElementById('svc-msg').textContent=(res.d&&res.d.erro)||'Não consegui salvar.';return;} fecharForm(); carregarCatalogo(true);});
  });
  var ocImport=document.getElementById('oc-import');
  if(ocImport)ocImport.addEventListener('click',function(){
    var b=this; b.disabled=true; b.textContent='Importando...';
    fetch('/painel/servicos/catalogo/importar-modelo',{method:'POST'}).then(function(){return carregarCatalogo(false);}).finally(function(){b.disabled=false; b.textContent='Usar modelo de tecnologia';});
  });

  document.getElementById('oc-sugerir').addEventListener('click',function(){
    var desc=document.getElementById('oc-desc').value.trim();
    if(!desc){return;}
    var btn=this, msg=document.getElementById('oc-ia-msg');
    btn.disabled=true; var t0=btn.textContent; btn.textContent='Analisando...'; msg.textContent='';
    zapFetch('/painel/servicos/sugerir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({descricao:desc})}).then(function(d){if(!d)return;
        if(d.erro){msg.textContent='Não consegui gerar agora. Tente de novo.'; return;}
        var ids=d.modules||[];
        // a IA ESCOLHE os serviços: vira a seleção da proposta (só o que existe
        // no catálogo — um slug inventado não tem preço pra mostrar).
        if(ids.length){
          // o órfão (serviço que a proposta tem e o catálogo não) FICA: a IA não
          // o conhece, e sumir com ele apagaria o que o cliente já recebeu.
          var antes=SELECIONADOS; SELECIONADOS={};
          Object.keys(ORFAOS).forEach(function(k){ if(antes[k]) SELECIONADOS[k]=true; });
          ids.forEach(function(id){ if(CATALOGO.some(function(x){return x.slug===id;})) SELECIONADOS[id]=true; });
          renderCatalogoAvulso();
        }
        if(d.segmento){document.getElementById('oc-segmento').value=d.segmento;}
        var out=document.getElementById('oc-escopo-out');
        if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
        pinta();
      })
      .finally(function(){btn.disabled=false; btn.textContent=t0;});
  });

  document.getElementById('oc-cnpj-btn').addEventListener('click',function(){
    var dig=(document.getElementById('oc-cnpj').value||'').replace(/\D/g,'');
    var msg=document.getElementById('oc-cnpj-msg');
    if(dig.length!==14){msg.textContent='Digite os 14 dígitos do CNPJ.'; return;}
    var btn=this, t0=btn.textContent; btn.disabled=true; btn.textContent='Buscando...'; msg.textContent='';
    zapFetch('/painel/servicos/cnpj?cnpj='+encodeURIComponent(dig),{comStatus:true}).then(function(res){if(!res)return;
        if(!res.ok){msg.textContent=(res.d&&res.d.erro)||'Não encontrei esse CNPJ.'; return;}
        var d=res.d;
        function set(id,v){if(v){document.getElementById(id).value=v;}}
        set('oc-empresa',d.empresa); set('oc-segmento',d.segmento);
        set('oc-whats',d.whatsapp); set('oc-email',d.email);
        set('oc-cidade',d.cidade); set('oc-uf',d.uf);
        var loc=[d.cidade,d.uf].filter(Boolean).join('/');
        msg.textContent='Preenchido pela Receita'+(loc?' — '+loc:'')+'. Confira e ajuste se precisar.';
        atualizarChip();
      })
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
        zapFetch('/painel/servicos/leads/buscar?q='+encodeURIComponent(q)).then(function(d){if(!d)return;cliRenderDrop(d.itens||[]);});
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
      // `servicoPorSlug` e não `CATALOGO.filter`: a linha órfã também tem
      // categoria e ícone (vieram do item salvo), e perdê-los aqui faria a folha
      // do cliente sair sem o subtotal da categoria na primeira regravação.
      var cat=servicoPorSlug(r.getAttribute('data-id'))||{};
      var par=r.querySelector('.oc-dpar');
      // INCLUSO grava a MARCA e os 100% JUNTOS, de propósito. A marca carrega a
      // intenção pra tela e pra folha; os 100% fazem o dinheiro sair igual em
      // qualquer leitor que ainda não conheça a marca — o financeiro, um deploy
      // antigo, um relatório. Ver finance/desconto.eh_incluso.
      var incl=r.getAttribute('data-incluso')==='1';
      return {nome:r.getAttribute('data-nome'),desc:r.getAttribute('data-desc')||'',
              setup:r2(u*q),mensal:num(r.querySelector('.oc-mensal')),qtd:q,unitario:u,
              categoria:cat.categoria||'',icone:cat.icone||'',
              incluso:incl,
              desc_tipo:incl?'pct':(par?(par.getAttribute('data-tipo')||'pct'):'pct'),
              desc_val:incl?100:num(r.querySelector('.oc-desc')),
              desc_mes:porMes(r)};
    });
    var escEl=document.getElementById('oc-escopo-out');
    return {id:EDIT_ID,lead_id:LEAD_ID,cliente:document.getElementById('oc-contato').value||'',empresa:document.getElementById('oc-empresa').value||'',cnpj:document.getElementById('oc-cnpj').value||'',segmento:document.getElementById('oc-segmento').value||'',whatsapp:document.getElementById('oc-whats').value||'',email:document.getElementById('oc-email').value||'',telefone:document.getElementById('oc-tel').value||'',cidade:document.getElementById('oc-cidade').value||'',uf:document.getElementById('oc-uf').value||'',site:document.getElementById('oc-site').value||'',cargo:document.getElementById('oc-cargo').value||'',socio:document.getElementById('oc-socio').value||'',endereco:(document.getElementById('oc-endereco')||{}).value||'',cep:(document.getElementById('oc-cep')||{}).value||'',modulos:sel.map(function(r){return r.getAttribute('data-id');}).filter(function(id){return id.indexOf('orfao:')!==0;}),itens:itens,evento:coletarEvento(),parcelas:(SERVICO_AVULSO?coletarParcelas():[]),escopo:(escEl.getAttribute('data-escopo')||''),setup:r2(c.setupBruto),mensal:r2(SERVICO_AVULSO?c.mensalBruto:c.mensalBrutoCheio),primeiro_ano:r2(c.ano1),n_modulos:c.mods,desconto_tipo:descTipoTot(),desconto_pct:(descTipoTot()==='pct'?num(document.getElementById('oc-desconto')):0),desconto_valor:(descTipoTot()==='valor'?num(document.getElementById('oc-desconto')):0),anual:!!c.anual};
  }
  function salvarProposta(cb){
    zapFetch('/painel/servicos/salvar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(coletarBody())}).then(function(d){if(!d){if(cb)cb(null);return;}if(d&&d.id){EDIT_ID=d.id;} if(cb)cb(d);});
  }
  document.getElementById('oc-salvar').addEventListener('click',function(){
    var btn=this; btn.textContent='Salvando...';
    salvarProposta(function(d){
      // CONTRATO ASSINADO: o servidor responde por que não dá e para onde ir. Até
      // aqui a tela engolia a frase e escrevia só "Erro ao salvar" — quem tentava
      // mudar convidado de um contrato assinado não descobria que existe aditivo.
      if(d && d.aditivo_url){
        btn.textContent='Salvar no funil';
        if(confirm((d.erro||'Contrato assinado.')+'.\n\nAbrir a tela de termo aditivo agora?')){
          window.location = d.aditivo_url;
        }
        return;
      }
      if(d && d.erro){ btn.textContent='Erro ao salvar'; alert(d.erro);
                       setTimeout(function(){btn.textContent='Salvar no funil';},1500); return; }
      btn.textContent=(d&&d.id)?'Salvo!':'Erro ao salvar'; carregarHist();
      setTimeout(function(){btn.textContent='Salvar no funil';},1500);
    });
  });
  /* "Salvar cliente", no card do Cliente. Grava a proposta inteira — é nela que o
     cliente mora — e fecha o formulário no chip. Sem nome não há o que salvar:
     avisa ali mesmo, em vez de gravar uma proposta sem dono. */
  var cliSalvar=document.getElementById('cli-salvar');
  if(cliSalvar)cliSalvar.addEventListener('click',function(){
    var btn=this, msg=document.getElementById('cli-salvar-msg');
    if(!(document.getElementById('oc-empresa').value||'').trim()){
      msg.textContent='Preencha o nome ('+(document.getElementById('oc-empresa-label').textContent||'Empresa')+') pra salvar.';
      document.getElementById('oc-empresa').focus();
      return;
    }
    msg.textContent=''; btn.disabled=true; btn.textContent='Salvando...';
    salvarProposta(function(d){
      btn.disabled=false; btn.textContent='Salvar cliente';
      if(d && d.aditivo_url){
        if(confirm((d.erro||'Contrato assinado.')+'.\n\nAbrir a tela de termo aditivo agora?')){
          window.location = d.aditivo_url;
        }
        return;
      }
      if(!d || d.erro || !d.id){ msg.textContent=(d&&d.erro)||'Não consegui salvar. Tente de novo.'; return; }
      atualizarChip(); carregarHist();
      var ok=document.getElementById('cli-salvo');
      if(ok){ ok.style.display='block'; setTimeout(function(){ok.style.display='none';},6000); }
    });
  });

  function esc(s){var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML;}
  function setv(id,v){var e=document.getElementById(id); if(e){e.value=v||'';}}
  // A MESMA LEITURA QUE `finance.desconto.eh_incluso` FAZ no servidor. Duas
  // leituras diferentes de "esta linha é cobrada?" seriam dois totais — é a
  // mesma razão de `finance/desconto.py` existir. Se um dia elas divergirem, a
  // tela mostra um número e a folha do cliente outro.
  function incluisoDoItem(it){
    if(!it) return false;
    if(it.incluso) return true;
    return (it.desc_tipo||'pct')==='pct' && (parseFloat(it.desc_val)||0) >= 100;
  }

  // `itensSalvos` é o que está GRAVADO no orçamento — a folha que o cliente já
  // recebeu. É dele que a linha sai quando o catálogo não sabe mais explicá-la.
  function marcaMods(ids, itensSalvos){
    {
      SELECIONADOS={}; ORFAOS={};
      // quem decide de onde vem cada linha é `window.ZAQ_PAREAR` — função pura,
      // definida fora deste IIFE porque é ela que os testes exercitam.
      window.ZAQ_PAREAR(ids, itensSalvos, CATALOGO).forEach(function(p){
        SELECIONADOS[p.slug]=true;
        if(!p.orfao || !p.item) return;
        // o catálogo não explica esta linha: ela vem do item GRAVADO, que é o
        // mesmo que a folha do cliente imprime.
        var it=p.item;
        ORFAOS[p.slug]={
          slug:p.slug, id:null, orfao:true,
          nome: it.nome||'(sem nome)', descricao: it.desc||'',
          // proposta antiga não tem `unitario`: ali o `setup` salvo ERA o
          // unitário (é o mesmo cuidado que a restauração de valores já toma).
          setup: (it.unitario!=null&&it.unitario>0)?it.unitario:(it.setup||0),
          mensal: it.mensal||0, custo: 0,
          categoria: it.categoria||'', icone: it.icone||'', icone_svg:'',
          desc_tipo: it.desc_tipo||'pct', desc_val: it.desc_val||0,
          incluso: incluisoDoItem(it)
        };
      });
      renderCatalogoAvulso();
    }
  }
  var EDIT_ID=null;

  // O EDITOR ABRE SOB DEMANDA (nos dois nichos desde 23/09/2026). Quem chega na aba vem
  // ver o funil; o editor de orçamento é o que se faz DEPOIS de decidir em qual
  // proposta mexer. Antes ele estava sempre aberto e o funil era o rodapé.
  var EDITOR = document.getElementById('oc-editor');
  var BARRA = document.getElementById('oc-barra');

  function editorAberto(){ return !!(EDITOR && EDITOR.classList.contains('on')); }

  function abrirEditor(rolar){
    if(!EDITOR) return;
    EDITOR.classList.add('on');
    if(BARRA) BARRA.classList.add('on');
    // `pinta` de novo com o editor VISÍVEL: enquanto estava escondido os campos
    // não tinham medida, e a barra do celular nasceria com o total errado.
    pinta();
    if(rolar !== false) EDITOR.scrollIntoView({behavior:'smooth', block:'start'});
  }

  function fecharEditor(){
    if(!EDITOR) return;
    EDITOR.classList.remove('on');
    if(BARRA) BARRA.classList.remove('on');
    var fn = document.getElementById('oc-funil');
    if(fn) fn.scrollIntoView({behavior:'smooth', block:'start'});
  }

  // OS BOTÕES DOS AVISOS levam pro conserto que JÁ existe — o gerador de
  // parcelas e a lista do catálogo. Um segundo caminho pro mesmo código, nunca
  // uma segunda implementação.
  (function(){
    var bv=document.getElementById('pg-sem-venc-b');
    if(bv) bv.addEventListener('click',function(){
      var g=document.getElementById('pg-gerar'); if(g) g.click();
      var ger=document.getElementById('pg-gerador');
      if(ger) ger.scrollIntoView({behavior:'smooth',block:'center'});
    });
    var bc=document.getElementById('oc-sem-cat-b');
    if(bc) bc.addEventListener('click',function(){
      if(!VERTODOS_OPEN){ VERTODOS_OPEN=true; renderCatalogoAvulso(); }
      var lst=document.getElementById('oc-catalogo-completo');
      if(lst) lst.scrollIntoView({behavior:'smooth',block:'start'});
    });
  })();

  (function(){
    var b = document.getElementById('fn-novo');
    if(b) b.addEventListener('click', function(){ novo(); abrirEditor(); });
    var v = document.getElementById('oc-voltar');
    if(v) v.addEventListener('click', fecharEditor);
  })();

  // o botão "Pagamento anual" no estado gravado (311). Só existe no recorrente.
  function marcaAnual(on){
    var b=document.getElementById('oc-anual'); if(!b) return;
    b.setAttribute('data-on',on?'1':'0'); b.classList.toggle('on',on);
    // ligado/desligado é desenhado pelo CSS a partir do data-on (interruptor)
  }
  function novo(){
    EDIT_ID=null;
    marcaAnual(false);
    ['oc-empresa','oc-contato','oc-cnpj','oc-segmento','oc-whats','oc-email','oc-tel','oc-cidade','oc-uf','oc-site','oc-cargo','oc-socio','oc-desc','oc-endereco','oc-cep'].forEach(function(id){setv(id,'');});
    aplicarEvento({});
    var pgb=document.getElementById('pg-linhas');
    if(pgb){pgb.innerHTML=''; pintaParcelas();}
    var out=document.getElementById('oc-escopo-out'); out.style.display='none'; out.removeAttribute('data-escopo'); out.textContent='';
    document.getElementById('oc-editando').style.display='none';
    marcaMods([]);   // proposta nova começa sem nenhum serviço marcado
    atualizarChip();
    pinta();
  }
  function abrir(id){
    zapFetch('/painel/servicos/item/'+id).then(function(d){if(!d)return;
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
      // os `itens` vão junto: é deles que sai a linha que o catálogo não tem mais.
      marcaMods(d.modulos, d.itens);
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
          // INCLUSO: a linha volta marcada, e o campo de desconto volta ZERADO.
          // Devolver "100" ali seria mostrar como desconto justamente o que esta
          // entrega existe pra parar de chamar de desconto — e, se a pessoa
          // trocasse pra "Cobrar", a linha voltaria valendo zero.
          var incl=incluisoDoItem(it);
          r.setAttribute('data-incluso', incl?'1':'0');
          r.classList.toggle('incluso', incl);
          r.querySelectorAll('.oc-cob button').forEach(function(x){
            x.classList.toggle('on', (x.getAttribute('data-cob')==='1')===incl);
          });
          var di=r.querySelector('.oc-desc'), par=r.querySelector('.oc-dpar');
          if(di) di.value=(incl?0:(it.desc_val!=null?it.desc_val:0));
          if(par){
            var t=(!incl && it.desc_tipo==='valor')?'valor':'pct';
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
        setv('oc-desconto', dt==='valor'?dinTxt(d.desconto_valor||0):String(d.desconto_pct||0).replace('.',','));
      }
      milhar();
      marcaAnual(!!d.anual);
      var out=document.getElementById('oc-escopo-out');
      if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
      else{out.style.display='none'; out.removeAttribute('data-escopo');}
      var bn=document.getElementById('oc-editando');
      bn.style.display='flex';
      var aviso=(d.status==='aprovada')?' · ⚠ editar vai pedir nova aprovação do cliente':'';
      var quando=d.gerado_em?(' · gerada em '+d.gerado_em):'';
      bn.querySelector('.t').textContent='Editando proposta #'+d.id+' · '+d.status+quando+aviso+' — salve pra atualizar o link do cliente.';
      atualizarChip();
      pinta();
      // o editor estava fechado: abrir a proposta é abrir ele junto, e a
      // rolagem passa a ser pro editor (o topo agora é o funil).
      abrirEditor();
    });
  }
  document.getElementById('oc-novo').addEventListener('click',function(){
    novo();
    // "Nova proposta" a partir da faixa mantém o editor aberto — quem apertou já
    // está editando.
    abrirEditor(false);
  });
  // os dois botões da barra do celular são atalhos pros que já existem: um
  // segundo caminho pro mesmo código, nunca uma segunda implementação.
  (function(){
    var g=document.getElementById('barra-gerar'), sv=document.getElementById('barra-salvar');
    if(g) g.addEventListener('click',function(){document.getElementById('oc-gerar').click();});
    if(sv) sv.addEventListener('click',function(){document.getElementById('oc-salvar').click();});
  })();
  function fechar(id,btn,dif){
    if(!confirm(SERVICO_AVULSO?'Fechar este contrato? Cada parcela do plano de pagamento vira um título a receber no módulo Empresa (sem plano, gera um título com o total).':'Fechar este contrato? Vai gerar título a receber (setup + mensalidade) no módulo Empresa.')){return;}
    // É AQUI que o plano vira dinheiro a receber. Se ele não fecha com o total, o
    // financeiro vai cobrar um valor diferente do que o cliente assinou — então a
    // segunda pergunta é sobre o número, não sobre a ação.
    if(dif && !confirm('Atenção: o plano de pagamento não fecha com o total do orçamento — '
        +(dif>0?'as parcelas passam R$ ':'faltam R$ ')+fmtc(Math.abs(dif))+'.\n\n'
        +'Os títulos a receber vão sair pelo valor das PARCELAS. Fechar assim mesmo?')){return;}
    btn.disabled=true; btn.textContent='Fechando...';
    zapFetch('/painel/servicos/fechar',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})}).then(function(res){if(!res){btn.disabled=false; btn.textContent='Fechar contrato';return;}
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui fechar.'); btn.disabled=false; btn.textContent='Fechar contrato'; return;}
        carregarHist();
      });
  }
  // Confirma que o sinal caiu: a data segurada vira compromisso firme na agenda.
  // Confirma antes porque é dinheiro — e nomeia o cliente pelo mesmo motivo do
  // 🗑 logo abaixo: a lista é densa e "tem certeza?" não diz qual linha é.
  function sinalRecebido(id,nome,btn){
    if(!confirm('Confirmar que o sinal de '+nome+' foi recebido?\\n\\nA data deixa de ser provisória e vira compromisso firme na agenda. Se o contrato já estiver fechado, o título dessa parcela entra como recebido no livro-caixa, com a data de hoje.')){return;}
    btn.disabled=true; btn.textContent='Confirmando...';
    zapFetch('/painel/servicos/sinal-recebido',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})}).then(function(res){if(!res){btn.disabled=false; btn.textContent='Sinal recebido';return;}
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
      });
  }
  // Põe na agenda a data que ficou de fora, ou segura de novo a que foi liberada.
  // Sem confirmação de propósito: marcar uma data que deveria estar marcada não
  // destrói nada, e o botão só aparece quando há de fato o que consertar.
  function marcarData(id,btn){
    var t=btn.textContent; btn.disabled=true; btn.textContent='Marcando...';
    zapFetch('/painel/servicos/marcar-data',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})}).then(function(res){if(!res){btn.disabled=false; btn.textContent=t;return;}
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui marcar.'); btn.disabled=false; btn.textContent=t; return;}
        carregarHist();
      });
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
    zapFetch('/painel/servicos/email/'+id+'?alvo='+encodeURIComponent(ENV_ALVO),{comStatus:true}).then(function(res){if(!res)return;
        if(!res.ok){envMsg(esc((res.d&&res.d.erro)||'Não consegui abrir.'),'cor'); return;}
        var d=res.d;
        ENV_LINK=d.link||'';
        document.getElementById('env-tt').textContent=
          (ENV_ALVO==='contrato'
             ? (d.assinado?'Mandar a via assinada — ':'Mandar pra assinar — ')
             : 'Mandar ')+esc(d.assunto||'a proposta');
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
      });
  }
  function enviarEmail(){
    if(!ENV_ID) return;
    var b=document.getElementById('env-enviar'), t=b.textContent;
    b.disabled=true; b.textContent='Enviando...';
    zapFetch('/painel/servicos/enviar-email',{comStatus:true,method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:ENV_ID, alvo:ENV_ALVO,
        para:document.getElementById('env-para').value,
        assunto:document.getElementById('env-assunto').value,
        mensagem:document.getElementById('env-texto').value})}).then(function(res){if(!res){b.disabled=false; b.textContent=t;return;}
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
      });
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
      // MANDAR O CONTRATO ESTAVA SÓ NO BOTÃO VERDE, e o botão verde é UM só: quando
      // a linha tem pendência de prioridade maior (marcar, resegurar, sinal,
      // comprovante — ver `_ORDEM_ACAO`), ele mostra a outra e o e-mail do contrato
      // some da tela. Depois de ASSINADO some de vez: a ação vira "Fechar negócio"
      // e, com o negócio fechado, não há ação nenhuma — justamente quando o cliente
      // liga pedindo a via dele. O grupo Contrato tinha Abrir e Copiar link, e o de
      // Proposta logo acima tinha o "Mandar por e-mail" que faltava aqui.
      //
      // Depois de assinado o pedido é OUTRO — a via, não a assinatura —, e o texto
      // do e-mail acompanha (`pmail.texto_contrato`).
      m.appendChild(_mi(it.contrato_assinado?'Mandar a via assinada':'Mandar pra assinar','✉️',
        it.contrato_enviado_em?('mandado '+it.contrato_enviado_em):'nunca mandado',
        function(){abrirEnvio(it.id,'contrato');}));
      // DEPOIS DE ASSINADO, mudar data, horário, convidados, serviço ou valor só
      // por aditivo — é o que as três travas do sistema já respondiam sem ter pra
      // onde mandar. Aparece aqui, e não escondido atrás do erro de salvar: quem
      // vai remarcar uma festa procura o contrato, não o botão de editar.
      if(it.contrato_assinado && it.contrato_id){
        m.appendChild(_mi('Fazer termo aditivo','📝','muda data, convidados ou valor',
          function(){ window.location='/painel/servicos/aditivo/'+it.contrato_id; }));
      }
    }
    if(it.pgto&&it.pgto.total){
      m.appendChild(_mgrupo('Dinheiro'));
      // "pagas" e "anexados" são coisas DIFERENTES (anexar não marca como pago —
      // ver o comentário em finance.vendas.resumo_pagamentos). Antes só "pagas de
      // total" aparecia aqui, sob o rótulo "...e comprovantes" — um dono com
      // comprovante já anexado lia aquele número e achava que não tinha nenhum.
      m.appendChild(_mi('Pagamentos e comprovantes','📎',
        it.pgto.pagas+' de '+it.pgto.total+' pagas · '+it.pgto.anexados+' anexados',
        function(){abrirPagamentos(it.id,it.cliente);}));
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
    zapFetch('/painel/servicos/pagamentos/'+PG_ID,{comStatus:true}).then(function(res){if(!res)return;
        if(!res.ok){pgMsg(esc((res.d&&res.d.erro)||'Não consegui abrir.'),'cor'); return;}
        var d=res.d;
        var n=(d.parcelas||[]).length;
        document.getElementById('pg-tot').innerHTML=
          '<span class="ok">Recebido <b>'+esc(d.recebido)+'</b></span>'
         +'<span class="fl">Falta <b>'+esc(d.falta)+'</b></span>'
         +'<span>Total <b>'+esc(d.total)+'</b></span>'
         // anexado é coisa diferente de pago — ver o comentário de
         // finance.vendas.resumo_pagamentos. Sem este dado aqui, quem já anexou
         // comprovante só descobria isso rolando a lista parcela por parcela.
         +(n?'<span class="an">📎 Anexados <b>'+d.anexados+' de '+n+'</b></span>':'');
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
      });
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
    zapFetch('/painel/servicos/comprovante',{comStatus:true,method:'POST',body:fd}).then(function(res){if(!res)return;
        if(!res.ok){pgMsg(esc((res.d&&res.d.erro)||'Não consegui anexar.'),'cor'); return;}
        pgMsg('✓ Comprovante anexado.','ok');
        pgCarregar();       // a linha vira "ver" sozinha
        carregarHist();     // e o selo do funil acompanha
      });
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
    zapFetch('/painel/servicos/excluir',{comStatus:true,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})}).then(function(res){if(!res){btn.disabled=false;return;}
        if(!res.ok){alert((res.d&&res.d.erro)||'Não consegui apagar.'); btn.disabled=false; return;}
        carregarHist();
      });
  }
  // O FUNIL VIRA A PRIMEIRA COISA DA TELA (no nicho de eventos), com três abas.
  //
  // Medido na Prime em 18/09/2026: 27 propostas numa lista só, sem filtro e sem
  // busca, QUINZE delas rascunho e a mais velha de 19/08. O que precisava de
  // alguém hoje ficava no meio do que já fechou. Quem decide a aba de cada linha
  // é `vendas.grupo_do_funil`, no servidor, derivada do MESMO painel que pinta os
  // selos — a aba nunca discorda da linha.
  var FUNIL = [];          // tudo que veio do servidor, sem filtro
  var FN_ABA = 'precisa_de_mim';
  var FN_BUSCA = '';
  var FN_VENDEDOR = '';

  function fnVisiveis(){
    var q = FN_BUSCA.trim().toLowerCase();
    return FUNIL.filter(function(it){
      if (it.grupo !== FN_ABA) return false;
      if (FN_VENDEDOR && it.vendedor !== FN_VENDEDOR) return false;
      if (!q) return true;
      // nome, número e data — os três jeitos de procurar uma proposta. A data
      // vale nas duas escritas: "24/07" é como se fala, "2027-07-24" é o que
      // sai de um copiar-colar da agenda.
      var alvo = [it.titulo, it.sub, 'nº ' + (it.numero || ''), it.numero,
                  (it.data_linha || {}).titulo, (it.data_linha || {}).iso,
                  it.vendedor].join(' ').toLowerCase();
      return alvo.indexOf(q) >= 0;
    });
  }

  function fnPintarAbas(grupos){
    var box = document.getElementById('fn-abas');
    if (!box) return;
    var TOM = {precisa_de_mim:'var(--coral)', com_o_cliente:'var(--azul)', fechada:'var(--verde)'};
    box.innerHTML = '';
    (grupos || []).forEach(function(g){
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'fn-aba' + (g.chave === FN_ABA ? ' on' : '');
      b.innerHTML = '<span class="pt" style="background:' + (TOM[g.chave] || 'var(--txt-mut)') + '"></span>'
                  + ec(g.rotulo) + ' <span class="n">' + (g.n || 0) + '</span>';
      b.addEventListener('click', function(){ FN_ABA = g.chave; fnPintarAbas(grupos); fnDesenhar(); });
      box.appendChild(b);
    });
  }

  function fnPintarVendedores(nomes){
    var sel = document.getElementById('fn-vendedor');
    if (!sel || sel.options.length) return;
    sel.innerHTML = '<option value="">Todos (' + (nomes || []).length + ')</option>'
      + (nomes || []).map(function(n){ return '<option>' + ec(n) + '</option>'; }).join('');
    sel.addEventListener('change', function(){ FN_VENDEDOR = sel.value; fnDesenhar(); });
  }

  (function(){
    var busca = document.getElementById('fn-busca');
    if (busca) busca.addEventListener('input', function(){ FN_BUSCA = busca.value || ''; fnDesenhar(); });
  })();

  function carregarHist(){
    zapFetch('/painel/servicos/lista').then(function(d){if(!d){document.getElementById('oc-hist-box').innerHTML='<p class="mut">Erro ao carregar.</p>';return;}
      FUNIL = d.itens || [];
      fnPintarAbas(d.grupos);
      fnPintarVendedores(d.vendedores);
      fnDesenhar();
    });
  }

  function fnDesenhar(){
    (function(d){
      var box=document.getElementById('oc-hist-box');
      var vazio=document.getElementById('fn-vazio');
      if(vazio) vazio.style.display='none';
      if(!FUNIL.length){box.innerHTML='<p class="mut">Nenhuma proposta no funil ainda.</p>'; return;}
      box.innerHTML='';
      if(!d.itens.length && vazio){
        // VAZIO QUE INFORMA: "Precisa de mim (0)" é boa notícia, e a tela tem que
        // dizer isso em vez de parecer que quebrou.
        vazio.style.display='block';
        vazio.textContent = FN_BUSCA || FN_VENDEDOR
          ? 'Nenhuma proposta com esse filtro.'
          : (FN_ABA === 'precisa_de_mim' ? '✓ Nada esperando por você aqui.'
             : 'Nenhuma proposta nesta aba.');
        return;
      }
      d.itens.forEach(function(it){
        var el=document.createElement('div'); el.className='oc-hist';
        var fechado=it.status==='fechado', aprovada=it.status==='aprovada';
        var pn=it.painel||{selos:[],acao:null,resumo:''};

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
        // O VALOR é SEMPRE `it.total` (primeiro_ano_centavos, o líquido — já com
        // desconto). Aqui ficava `evento?it.setup:it.total`: `it.setup` é o BRUTO
        // (antes do desconto, comentado assim no próprio SalvarIn). Toda proposta
        // de evento com desconto mostrava dois valores diferentes pro mesmo
        // orçamento — o funil (bruto) e a folha que o cliente assina, o e-mail de
        // envio e o título a receber (todos os três já liam primeiro_ano_centavos
        // de propósito). O dinheiro sempre saiu certo; só a etiqueta mentia.
        // QUEM VENDEU (05/09/2026): dono e gestor viam o funil inteiro sem saber
        // de qual vendedor era cada proposta — só dava pra descobrir abrindo uma
        // por uma. `it.vendedor` já vem resolvido do servidor (mesma redação de
        // Relatórios → Vendas, "—" só se de fato não há como saber quem foi).
        //
        // A linha virou DUAS: quem é o cliente/nº/vendedor em cima, valor e o que
        // já aconteceu embaixo — a de cima tinha virado um parágrafo só, cada vez
        // mais difícil de escanear.
        // A DATA DE CRIAÇÃO SAI DAQUI no evento: ela ganhou coluna própria à
        // direita (ver `criada`, logo abaixo). Enterrada entre o nº e o vendedor
        // ela era o dado certo no lugar errado — ninguém compara datas que estão
        // no meio de frases diferentes. Vale pros dois nichos desde 23/09/2026,
        // quando o recorrente ganhou o funil da Prime.
        var sub1=[esc(it.sub||''), (it.numero?('nº '+it.numero):''),
                  (it.vendedor?('vendido por '+esc(it.vendedor)):'')]
                 .filter(Boolean).join(' · ');
        var sub2=[esc(it.total), esc(pn.resumo||'')].filter(Boolean).join(' · ');
        // A DATA ABRE A LINHA no nicho de eventos (o servidor manda `data_linha`
        // pronto; no recorrente ele manda null e fica a inicial de sempre). É a
        // data que identifica o negócio aqui: é o que o dono procura e o que não
        // pode ser vendido duas vezes. Sem data sai em coral — é a nº 22.
        var dl = it.data_linha;
        var abre = dl
          ? '<div class="oc-data' + (dl.sem_data ? ' vazia' : '') + '" title="' + esc(dl.titulo) + '">'
              + '<div class="d">' + esc(dl.dia) + '</div>'
              + '<div class="m">' + esc(dl.mes) + '</div></div>'
          : '<div class="oc-av">' + esc(it.inicial) + '</div>';
        left.innerHTML=abre
          +'<div style="min-width:0"><b>'+esc(it.titulo)+'</b>'
          +'<div class="mut" style="font-size:.78rem">'+sub1+'</div>'
          +'<div class="mut" style="font-size:.78rem">'+sub2+'</div></div>';
        left.addEventListener('click',function(){abrir(it.id);});
        el.appendChild(left);

        // QUANDO A PROPOSTA FOI CRIADA, em coluna própria e alinhada. A data
        // COMPLETA, com ano: é o dado que se procura, e 27 linhas alinhadas em
        // coluna viram uma leitura só (16/09 · 09/09 · 31/08 · 28/08) — dá pra
        // ver a idade da carteira de cima pra baixo, coisa que a data no meio da
        // frase não permitia.
        if(it.data){
          var criada=document.createElement('div');
          criada.className='oc-criada';
          criada.innerHTML='<div class="rot">Criada em</div><div class="dt">'+esc(it.data)+'</div>';
          el.appendChild(criada);
        }

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
    })({itens: fnVisiveis()});
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
    var CAMPOS=[], REGRAS={}, ANTES=false, ultimo=null;   // ultimo = textarea que teve foco
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
    // os números do contrato de SERVIÇO (recorrente). Nascem em branco — ver
    // finance/contrato.REGRAS_SERVICO_PADRAO — e o placeholder mostra o formato.
    // Sem fidelidade, sem multa rescisória e sem dia fixo: o dia de vencimento é
    // o CLIENTE quem escolhe, ao assinar (dono da ZAQ, 23/09/2026).
    var ROTULO_REGRA_SERVICO={indice_reajuste:['Reajuste anual pelo','reajuste do salário mínimo vigente'],
      aviso_previo_dias:['Aviso prévio p/ cancelar (dias)','30'],
      implantacao_dias:['Prazo da implantação (dias)','60 a 90'],
      suporte_horario:['Suporte','24 horas por dia'],
      setup_parcelas:['Implantação em (nº de parcelas)','3'],
      multa_atraso_pct:['Multa por atraso (%)','2'],juros_mora_pct_mes:['Juros de mora (% ao mês)','1']};
    var MODO='locacao', PEDIR=false;

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
        +(r.em?' · alterado em '+r.em:'')+(r.por?' por '+r.por:'')
        // no serviço, o card fechado responde também "está pedindo assinatura?"
        +(d.modo==='servico'?(d.pedir_assinatura?' · pedindo assinatura':' · assinatura desligada'):'');
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
      CAMPOS=d.campos||[]; REGRAS=d.regras||{}; ANTES=!!d.assinar_antes_do_sinal;
      MODO=d.modo||'locacao'; PEDIR=!!d.pedir_assinatura;
      var SERV=MODO==='servico';
      resumir(d);
      // O BLOCO DE BAIXO MUDA COM O DOCUMENTO. Na locação é a ORDEM (assinar
      // antes ou depois do sinal); no serviço não existe sinal, e o que o dono
      // decide é se a proposta aprovada vira contrato — a chave da 311.
      var blocoOrdem = SERV
        ? '<div style="background:var(--card-2);border:1px solid var(--neon-borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
          +'<label style="display:flex;gap:.5rem;align-items:flex-start;cursor:pointer">'
          +'<input type="checkbox" id="ct-pedir" style="margin-top:.25rem;width:auto;flex:none;padding:0"'+(PEDIR?' checked':'')+'>'
          +'<span style="font-size:.85rem"><b>Pedir assinatura de contrato</b>'
          +'<span class="mut" style="display:block;font-size:.76rem;margin-top:.15rem">'
          +'A proposta aprovada vira contrato, com link próprio pro cliente assinar. '
          +'A implantação e as mensalidades entram no financeiro quando ele assinar. '
          +'Só dá pra ligar com os números da casa preenchidos. Propostas aprovadas antes '
          +'de ligar continuam fechando pelo botão.</span></span></label></div>'
        : '<div style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
          +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.5rem">Ordem de cobrança</div>'
          +'<label style="display:flex;gap:.5rem;align-items:flex-start;cursor:pointer">'
          +'<input type="checkbox" id="ct-antes" style="margin-top:.25rem"'+(ANTES?' checked':'')+'>'
          +'<span style="font-size:.85rem">Pedir a <b>assinatura do contrato antes</b> da entrada'
          +'<span class="mut" style="display:block;font-size:.76rem;margin-top:.15rem">'
          +'O funil passa a mostrar “Mandar o contrato pra assinar” antes de “Sinal recebido”, '
          +'e a folha do cliente diz que a entrada vem depois de assinar. '
          +'A data continua só ficando reservada com a entrada (cláusula 4.1), e o prazo da '
          +'pré-reserva segue contando da aprovação.</span></span></label></div>';
      ctBox.innerHTML=
        (d.novo?'<p class="mut" style="font-size:.84rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:.5rem .7rem">Este é um modelo inicial de contrato de '+(SERV?'prestação de serviços':'locação')+'. Ajuste ao que a sua empresa pratica e salve.</p>':'')
        +'<div id="ct-lista"></div>'
        +'<button type="button" id="ct-add" class="oc-pill" style="margin-bottom:.9rem">+ Cláusula</button>'
        +'<div id="ct-campos" style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem">Campos — clique para inserir no texto</div>'
        +'<div id="ct-chips" style="display:flex;flex-wrap:wrap;gap:.3rem"></div></div>'
        +'<div style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.5rem">Números da casa — é daqui que os campos {regra.*} saem</div>'
        +'<div id="ct-regras" class="mini-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.5rem"></div></div>'
        // A ORDEM (194) — ou, no serviço, a chave (311) — fica FORA do bloco dos
        // números: aquilo é o que preenche {regra.*} nas cláusulas, isto muda o
        // que o funil pede. Junto, pareceria mais um campo de texto do contrato.
        +blocoOrdem
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
      if(SERV){
        Object.keys(ROTULO_REGRA_SERVICO).forEach(function(k){
          var rr=ROTULO_REGRA_SERVICO[k], w=document.createElement('div');
          var vazio=(REGRAS[k]===undefined||REGRAS[k]===null||REGRAS[k]==='');
          w.innerHTML='<label class="mut" style="font-size:.68rem">'+esc(rr[0])+'</label>'
            +'<input class="ct-rg oc-inp" data-k="'+k+'" style="width:100%'+(vazio?';border-color:var(--ambar-borda)':'')+'"'
            +' placeholder="ex.: '+esc(rr[1])+'" value="'+esc(vazio?'':REGRAS[k])+'">';
          gr.appendChild(w);
        });
      } else {
      Object.keys(ROTULO_REGRA).forEach(function(k){
        var w=document.createElement('div');
        w.innerHTML='<label class="mut" style="font-size:.68rem">'+esc(ROTULO_REGRA[k])+'</label>'
          +'<input class="ct-rg oc-inp" data-k="'+k+'" style="width:100%" value="'+esc(REGRAS[k])+'">';
        gr.appendChild(w);
      });
      }

      document.getElementById('ct-add').addEventListener('click',function(){
        lista.appendChild(linhaClausula({titulo:'',corpo:''}));});
      document.getElementById('ct-salvar').addEventListener('click',salvar);
      document.getElementById('ct-previa').addEventListener('click',previa);
      document.getElementById('ct-padrao').addEventListener('click',function(){
        if(!confirm('Trocar o texto atual pelo modelo padrão? O que você escreveu será perdido.')) return;
        zapFetch('/painel/servicos/contrato?padrao=1').then(function(d){if(!d)return;d.novo=true;desenhar(d);});
      });
    }

    function msg(html){document.getElementById('ct-msg').innerHTML=html;}

    // o que vai no Salvar. A chave do serviço (311) só vai quando o card é de
    // serviço: a tela de eventos não conhece a chave e não pode desligá-la.
    function corpoSalvar(){
      var b={clausulas:clausulas(),regras:regras(),
             assinar_antes_do_sinal:!!(document.getElementById('ct-antes')||{}).checked};
      if(MODO==='servico') b.pedir_assinatura=!!(document.getElementById('ct-pedir')||{}).checked;
      return b;
    }

    function salvar(){
      var b=document.getElementById('ct-salvar'), t=b.textContent; b.textContent='Salvando...';
      zapFetch('/painel/servicos/contrato/salvar',{comStatus:true,method:'POST',
        headers:{'Content-Type':'application/json'},
        // o valor VAI SEMPRE, mesmo sem ter sido tocado: o servidor tem default
        // false, e omitir o campo desligaria a ordem que o dono ligou ontem.
        body:JSON.stringify(corpoSalvar())}).then(function(res){if(!res){b.textContent=t;msg('<p style="color:var(--verm);font-size:.85rem">Erro de conexão.</p>');return;}
          b.textContent=t;
          if(!res.ok){msg('<p style="color:var(--verm);font-size:.85rem">'+esc((res.d&&res.d.erro)||'Não consegui salvar.')+'</p>');return;}
          msg('<p style="color:var(--verde-claro);font-size:.85rem">✓ Contrato salvo — '+res.d.clausulas+' cláusulas. Vale para os próximos contratos; os já assinados não mudam.</p>');
          // Recolhe depois de mostrar a confirmação: o trabalho acabou, e deixar
          // aberto obriga a fechar à mão. Relê pra o resumo (e o selo de falta)
          // refletirem o que ACABOU de ser salvo.
          setTimeout(function(){
            zapFetch('/painel/servicos/contrato').then(function(d){if(!d){abrir(false);return;}resumir(d);abrir(false);});
          }, 1600);
        });
    }

    // A prévia usa um orçamento REAL da conta. É o que revela a falta que
    // importa: o campo que não resolve porque o item saiu do catálogo.
    function previa(){
      var b=document.getElementById('ct-previa'), t=b.textContent; b.textContent='Montando...';
      zapFetch('/painel/servicos/contrato/previa',{comStatus:true,method:'POST',
        headers:{'Content-Type':'application/json'},
        // o valor VAI SEMPRE, mesmo sem ter sido tocado: o servidor tem default
        // false, e omitir o campo desligaria a ordem que o dono ligou ontem.
        body:JSON.stringify({clausulas:clausulas(),regras:regras(),
          assinar_antes_do_sinal:!!(document.getElementById('ct-antes')||{}).checked})}).then(function(res){if(!res){b.textContent=t;msg('<p style="color:var(--verm);font-size:.85rem">Erro de conexão.</p>');return;}
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
        });
    }

    zapFetch('/painel/servicos/contrato').then(function(d){if(!d){document.getElementById('ct-resumo').textContent='Erro ao carregar.';
        ctBox.innerHTML='<p class="mut">Erro ao carregar o contrato.</p>';return;}
        desenhar(d);
        var lembrado=false;
        try{ lembrado = localStorage.getItem(LEMBRA)==='1'; }catch(e){}
        abrir(!!d.novo || lembrado);
      });
  })(); }

  carregarCatalogo(false).then(function(){
    var ab=new URLSearchParams(location.search).get('abrir');
    if(ab && /^[0-9]+$/.test(ab)){ abrir(ab); history.replaceState({},'','/painel/servicos'); }
  });
  carregarHist();
})();

  // ---------------------------------------------------------------- ADITIVO
  // O MESMO card do contrato, pro documento que altera ele. Vive aqui e não em
  // arquivo próprio porque compartilha o `esc` e o mesmo desenho de "inserir
  // campo no cursor" — duas cópias disso divergiriam na primeira correção.
  (function(){
    var adBox=document.getElementById('ad-box');
    if(!adBox) return;                       // conta sem contrato: não tem card
    var CAMPOS=[], ORDEM=[], ROTULOS={}, ultimo=null;
    function esc(t){var d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML;}

    document.getElementById('ad-cab').addEventListener('click',function(){
      var c=document.getElementById('ad-corpo'), st=document.getElementById('ad-seta');
      var abrindo=c.style.display==='none';
      c.style.display=abrindo?'block':'none';
      st.style.transform=abrindo?'rotate(90deg)':'';
    });

    function inserir(campo){
      var t=ultimo; if(!t) return;
      var a=t.selectionStart||0, b=t.selectionEnd||0, v=t.value;
      t.value=v.slice(0,a)+'{'+campo+'}'+v.slice(b);
      t.focus(); t.selectionStart=t.selectionEnd=a+campo.length+2;
    }

    function bloco(chave,t){
      var d=document.createElement('div');
      d.className='ad-cl'; d.setAttribute('data-k',chave);
      d.style.cssText='border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.5rem;background:var(--card-2)';
      var extra = (chave==='convidados')
        ? '<label class="mut" style="font-size:.68rem">Título quando DIMINUI</label>'
          +'<input class="ad-tr oc-inp" style="width:100%;margin-bottom:.35rem" value="'+esc(t.titulo_reduz||'')+'">'
        : '';
      d.innerHTML=
        '<div class="mut" style="font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.35rem">'
        +esc(ROTULOS[chave]||chave)+'</div>'
        +'<label class="mut" style="font-size:.68rem">'
        +(chave==='convidados'?'Título quando AUMENTA':'Título')+'</label>'
        +'<input class="ad-t oc-inp" style="width:100%;font-weight:600;margin-bottom:.35rem" value="'+esc(t.titulo||'')+'">'
        +extra
        +'<label class="mut" style="font-size:.68rem">Texto</label>'
        +'<textarea class="ad-c oc-inp" rows="3" style="width:100%;font-family:var(--mono);font-size:.78rem;line-height:1.6">'+esc(t.corpo||'')+'</textarea>';
      d.querySelector('.ad-c').addEventListener('focus',function(){ultimo=this;});
      return d;
    }

    function textos(){
      var out={};
      document.querySelectorAll('#ad-lista .ad-cl').forEach(function(d){
        var k=d.getAttribute('data-k');
        out[k]={titulo:d.querySelector('.ad-t').value,corpo:d.querySelector('.ad-c').value};
        var tr=d.querySelector('.ad-tr'); if(tr) out[k].titulo_reduz=tr.value;
      });
      out.disposicoes=(document.getElementById('ad-disp')||{}).value||'';
      out.fecho=(document.getElementById('ad-fecho')||{}).value||'';
      return out;
    }

    function desenhar(d){
      CAMPOS=d.campos||[]; ORDEM=d.ordem||[]; ROTULOS=d.rotulos||{};
      var r=d.resumo||{};
      document.getElementById('ad-resumo').textContent = d.novo
        ? 'Usando o texto padrão — abra pra escrever com as suas palavras.'
        : '5 cláusulas · alterado em '+(r.em||'?')+(r.por?' por '+r.por:'');
      adBox.innerHTML='<div id="ad-lista"></div>'
        +'<div id="ad-campos" style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem">Campos — clique para inserir no texto</div>'
        +'<div id="ad-chips" style="display:flex;flex-wrap:wrap;gap:.3rem"></div></div>'
        +'<div style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.8rem">'
        +'<div class="mut" style="font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;margin-bottom:.4rem">Disposições gerais e fecho</div>'
        +'<textarea id="ad-disp" class="oc-inp" rows="3" style="width:100%;font-family:var(--mono);font-size:.78rem;line-height:1.6;margin-bottom:.4rem">'+esc(d.textos.disposicoes||'')+'</textarea>'
        +'<textarea id="ad-fecho" class="oc-inp" rows="3" style="width:100%;font-family:var(--mono);font-size:.78rem;line-height:1.6">'+esc(d.textos.fecho||'')+'</textarea></div>'
        +'<div style="display:flex;gap:.45rem;flex-wrap:wrap">'
        +'<button type="button" id="ad-salvar" class="oc-btn oc-btn-g" style="width:auto">Salvar modelo do aditivo</button>'
        +'<button type="button" id="ad-previa" class="oc-pill">Pré-visualizar</button>'
        +'<button type="button" id="ad-padrao" class="oc-pill">Restaurar modelo padrão</button></div>'
        +'<div id="ad-msg" style="margin-top:.7rem"></div>';

      var lista=document.getElementById('ad-lista');
      ORDEM.forEach(function(k){ lista.appendChild(bloco(k, d.textos[k]||{})); });

      var chips=document.getElementById('ad-chips');
      CAMPOS.forEach(function(f){
        var b=document.createElement('button');
        b.type='button'; b.className='oc-pill';
        b.style.cssText='font-family:var(--mono);font-size:.7rem;padding:.15rem .4rem'
          +(f.grupo==='aditivo'?';border-color:var(--verde-borda,#2C6E52)':'');
        b.textContent='{'+f.campo+'}'; b.title=f.rotulo;
        b.addEventListener('click',function(){inserir(f.campo);});
        chips.appendChild(b);
      });

      document.getElementById('ad-salvar').addEventListener('click',salvar);
      document.getElementById('ad-previa').addEventListener('click',previa);
      document.getElementById('ad-padrao').addEventListener('click',function(){
        if(!confirm('Trocar o texto atual pelo modelo padrão? O que você escreveu será perdido.')) return;
        zapFetch('/painel/servicos/aditivo-modelo/padrao',{method:'POST'}).then(function(x){if(!x)return;x.campos=CAMPOS;x.ordem=ORDEM;x.rotulos=ROTULOS;desenhar(x);});
      });
    }

    function admsg(h){document.getElementById('ad-msg').innerHTML=h;}

    function salvar(){
      var b=document.getElementById('ad-salvar'), t=b.textContent; b.textContent='Salvando...';
      zapFetch('/painel/servicos/aditivo-modelo/salvar',{comStatus:true,method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({textos:textos()})}).then(function(res){if(!res){b.textContent=t;admsg('<span style="color:var(--coral)">Não consegui salvar.</span>');return;}
          b.textContent=t;
          admsg(res.ok?'<span style="color:var(--verde-claro)">Salvo.</span>'
                     :'<span style="color:var(--coral)">'+esc((res.d&&res.d.erro)||'Não consegui salvar.')+'</span>');
          if(res.ok) carregar();
        });
    }

    function previa(){
      var b=document.getElementById('ad-previa'), t=b.textContent; b.textContent='Montando...';
      zapFetch('/painel/servicos/aditivo-modelo/previa',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({textos:textos()})}).then(function(d){if(!d){b.textContent=t;admsg('<span style="color:var(--coral)">Não consegui montar.</span>');return;}
          b.textContent=t;
          if(d.erro){admsg('<span class="mut">'+esc(d.erro)+'</span>');return;}
          admsg('<div class="mut" style="font-size:.72rem;margin-bottom:.4rem">'
                +'Prévia com os números do contrato nº '+esc(d.contrato)+' — só pra conferir o texto.</div>'
                +'<div style="background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:.7rem .85rem">'
                +(d.clausulas||[]).map(function(c){
                    return '<div style="margin-bottom:.6rem"><b style="font-size:.82rem">'+esc(c.titulo)
                          +'</b><div style="font-size:.82rem;line-height:1.6;white-space:pre-wrap;color:var(--mut)">'
                          +esc(c.corpo)+'</div></div>';}).join('')
                +'</div>');
        });
    }

    function carregar(){
      zapFetch('/painel/servicos/aditivo-modelo')
        .then(function(d){
          if(!d){adBox.innerHTML='<p class="mut">Não consegui carregar.</p>';return;}
          desenhar(d);
        });
    }
    carregar();
  })();
"""

_JS_TAG = ('<script src="'
           + _estaticos.registrar("servicos.js", _JS_PAREAR_CRU + "\n" + _JS_CRU)
           + '" defer></script>')


_SERVICOS_TPL = r"""{% extends "base" %}{% block conteudo %}
<div class="sv-wrap funil{% if servico_avulso %} evento{% endif %}">
<div class="sv-topo">
  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.4rem">
    <h1 style="margin:.2rem 0">Vendas de Serviços</h1>
    <span class="mut" style="font-size:.85rem">{{ empresa_nome }}</span>
  </div>
  <p class="mut" style="margin:0 0 .2rem">{{ 'Monte a proposta, salve no funil e feche o contrato — ao fechar, vira título a receber no módulo Empresa.' if servico_avulso else 'Monte a proposta, salve no funil e feche o contrato — ao fechar, vira título a receber (setup + mensalidade) no módulo Empresa.' }}</p>
</div>

""" + _CSS + r"""

{# O EDITOR INTEIRO MORA AQUI DENTRO. Dois motivos, e os dois são do nicho de
   eventos:

   1. o funil é o trabalho de todo dia (ver o que está pendente) e estava no
      RODAPÉ de uma página de editor — no celular, a ~1.300px de rolagem. A
      folha de estilo põe `#oc-funil` na frente deste bloco com `order`, em vez
      de duplicar a marcação: no recorrente a ordem continua a de sempre, e a
      ZAQ não é mexida por uma decisão que foi tomada olhando a Prime.
   2. quem só veio olhar o funil não precisa do editor aberto. Ele abre em
      "Nova proposta" ou ao clicar numa linha.  #}
<div id="oc-editor">
{# O CAMINHO DE VOLTA. O funil agora é o topo da tela, então o editor precisa
   dizer como se sai dele — senão a única saída é rolar. #}
<div class="fn-cab" style="margin-bottom:.6rem">
  <button type="button" class="oc-pill" id="oc-voltar">&#8592; Funil</button>
</div>
<div id="oc-editando" style="display:none;align-items:center;justify-content:space-between;gap:.6rem;background:#10241d;border:1px solid #1c3a30;border-radius:10px;padding:.5rem .8rem;margin-bottom:.8rem">
  <span class="t" style="font-size:.85rem;color:var(--verde-claro)"></span>
  <button id="oc-novo" type="button" class="oc-pill">Nova proposta</button>
</div>

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
      <div style="font-weight:700;font-size:1rem">{{ 'Contrato de locação' if servico_avulso else 'Contrato de prestação de serviços' }}</div>
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

{# TERMO ADITIVO: o mesmo card, pro documento que ALTERA o contrato acima.
   Pedido do dono em 05/09/2026 — "deixar o aditivo igual o contrato, podendo
   alterar alguma coisa nas cláusulas... e a gente replica". A incoerência que
   ele apontou: o contrato era escrito por ele e o aditivo saía com texto escrito
   dentro do código, no mesmo negócio e pro mesmo cliente.

   Vem DEPOIS do contrato porque é o que emenda o de cima, e o mesmo gate
   (`pode_contrato` = eventos + gerir): FAZER aditivo é dos três papéis, mas
   ESCREVER o texto é do dono, igual ao contrato. #}
{% if servico_avulso %}
<div class="card" id="ad-card">
  <div id="ad-cab" style="display:flex;align-items:center;gap:.6rem;cursor:pointer;user-select:none">
    <span id="ad-seta" style="color:var(--mut);font-size:.85rem;transition:transform .18s">▸</span>
    <div style="min-width:0">
      <div style="font-weight:700;font-size:1rem">Termo aditivo</div>
      <div id="ad-resumo" class="mut" style="font-size:.78rem;margin-top:.1rem">Carregando...</div>
    </div>
  </div>
  <div id="ad-corpo" style="display:none;margin-top:.85rem;padding-top:.85rem;border-top:1px solid var(--borda)">
    <p class="mut" style="margin-top:0;font-size:.86rem">
      O texto de cada alteração é seu. Os <b style="color:var(--verde-claro)">campos</b> trazem o
      número novo e o antigo — é o que faz o documento dizer “passa a ser 140, em substituição a
      115” sem ninguém digitar 140 nem 115.
    </p>
    <div id="ad-box"><p class="mut">Carregando...</p></div>
  </div>
</div>
{% endif %}
{% endif %}

<div class="card" id="oc-esc-card"{% if servico_avulso %} style="display:none"{% endif %}>
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
<div class="card" id="oc-ev-card">
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
    <b id="ev-sem-hora-t">Sem a hora de início, esta data não entra na agenda.</b>
    <div style="opacity:.85;margin-top:.15rem" id="ev-sem-hora-d">Pode salvar assim — mas a data só fica
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

<div class="card" id="oc-cli-card">
  <h2 style="margin-top:0">Cliente</h2>

  {# A BUSCA NA BASE vale pros dois nichos (pedido do dono em 23/09/2026: "o
     mesmo modelo que já tem na Prime"). O que muda de nicho pra nicho são os
     CAMPOS do formulário abaixo, não o caminho até ele. #}
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

  <div id="cli-form-full" style="display:none; margin-top:.8rem; border-top:1px dashed var(--borda); padding-top:.8rem">
    <div style="display:flex; gap:.4rem; margin-bottom:.8rem">
      <button type="button" class="oc-pill" id="btn-tipo-pj" data-tipo="pj">🏢 Pessoa Jurídica</button>
      <button type="button" class="oc-pill" id="btn-tipo-pf" data-tipo="pf">🧑 Pessoa Física</button>
    </div>
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
    {# O CARD NÃO TINHA SALVAR. O cliente só era gravado junto com a proposta, pelo
       "Salvar no funil" do Resumo — e em 23/09/2026 o dono preencheu o cliente na
       ZAQ, procurou o botão aqui, não achou, e o que digitou se perdeu. Este botão
       salva a PROPOSTA (é nela que o cliente mora), mesmo sem serviço ainda: ela
       fica no funil como rascunho, e o cliente não se perde mais. #}
    <div style="display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; margin-top:.9rem">
      <button type="button" id="cli-salvar" class="oc-btn-g" style="border:0; border-radius:8px; padding:.55rem 1.1rem; font-weight:600; cursor:pointer">Salvar cliente</button>
      <span id="cli-salvar-msg" class="mut" style="font-size:.8rem"></span>
    </div>
  </div>
  <div id="cli-salvo" class="mut" style="display:none; font-size:.8rem; margin-top:.5rem; color:var(--verde-claro)">✓ Cliente salvo — a proposta está no funil como rascunho. Agora escolha os serviços.</div>
</div>

<div class="oc-grid">
  <div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem">
        <h2 style="margin:0">Meus serviços</h2>
        <div style="display:flex; gap:.5rem; flex-wrap:wrap; align-items:center">
          <span class="oc-contador"><b id="oc-contador-n">0</b> de <span id="oc-contador-total">0</span> na proposta</span>
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
        {# O ÍCONE vale pros dois nichos desde 23/09/2026, cada um com o seu jogo
           (a paleta vem por nicho). A CATEGORIA continua só no evento: é o
           subtotal por categoria da festa. #}
        <div style="display:grid; grid-template-columns:{{ '1fr auto' if servico_avulso else 'auto' }}; gap:.6rem; align-items:end; margin-bottom:.4rem">
          {% if servico_avulso %}
          <div class="oc-field" style="margin-bottom:0"><label>Categoria <span style="color:var(--txt-mut);font-size:.78rem">— agrupa e soma por categoria no orçamento</span></label>
            <select id="svc-cat" class="oc-inp"><option value="">Sem categoria</option></select>
          </div>
          {% endif %}
          <div class="oc-field" style="margin-bottom:0"><label>Ícone
            <span style="color:var(--txt-mut);font-size:.78rem">— escolhido sozinho pelo nome; clique pra trocar</span></label>
            <input type="hidden" id="svc-icone">
            <div id="svc-icones" class="svc-icones"></div>
          </div>
        </div>
        <div style="display:flex; gap:.6rem; flex-wrap:wrap; align-items:flex-end">
          <div class="oc-field" style="margin-bottom:0"><label>{{ 'Valor (R$)' if servico_avulso else 'Implantação (R$)' }}</label><input id="svc-setup" class="oc-inp" inputmode="decimal" value="0,00" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0{% if servico_avulso %};display:none{% endif %}"><label>Mensal (R$)</label><input id="svc-mensal" class="oc-inp" inputmode="decimal" value="0,00" style="text-align:right; max-width:120px"></div>
          <div class="oc-field" style="margin-bottom:0"><label>Custo (R$)</label><input id="svc-custo" class="oc-inp" inputmode="decimal" value="0,00" style="text-align:right; max-width:120px"></div>
          <div style="flex:1; display:flex; gap:.4rem; justify-content:flex-end">
            <button id="svc-salvar" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">Salvar</button>
            <button id="svc-cancelar" class="oc-pill" type="button">Cancelar</button>
          </div>
        </div>
        <div id="svc-msg" class="mut" style="font-size:.8rem; margin-top:.4rem"></div>
      </div>

      {# BUSCA PRA ADICIONAR, nos dois nichos: a lista mostra só o que está NESTA
         proposta, e o resto do catálogo fica atrás da busca ou do "ver todos".
         Era assim só na Prime; na ZAQ a grade listava o catálogo inteiro com um
         interruptor por linha, e foi nela que as colunas se sobrepuseram. #}
      <div class="oc-buscabox" id="oc-buscabox" style="position:relative; margin-top:.8rem; display:none">
        <span class="oc-buscaic">🔍</span>
        <input id="oc-busca" class="oc-inp" placeholder="{{ 'Buscar serviço pra adicionar… (ex.: drinks, dj, buffet)' if servico_avulso else 'Buscar serviço pra adicionar…' }}" autocomplete="off" style="padding-left:2.2rem">
        <div id="oc-drop" style="display:none; position:absolute; left:0; right:0; top:calc(100% + 6px); background:var(--card-2); border:1px solid var(--borda); border-radius:10px; max-height:280px; overflow-y:auto; z-index:5; box-shadow:0 12px 30px rgba(0,0,0,.4)"></div>
      </div>
      <div id="oc-sel-empty" class="oc-empty" style="display:none">
        <b>Nenhum serviço nesta proposta ainda</b>
        <p class="mut" style="margin:.3rem 0 0; font-size:.85rem">Busque acima e clique pra adicionar — só o que você escolher aparece aqui embaixo.</p>
      </div>
      {# SEM CABEÇALHO DE COLUNAS: cada caixa traz o rótulo em cima do número. Era
         o cabeçalho que desalinhava a grade do recorrente — "Custo/Margem" ficava
         na tela com a coluna escondida embaixo, e tudo escorregava uma casa. #}
      <div id="oc-mods" style="display:none"></div>
      {% if servico_avulso %}
      {# SERVIÇO SEM CATEGORIA. A folha do cliente só imprime subtotal por
         categoria quando TODOS os itens têm uma (ver `_subtotais` em
         web/proposta.py) — um item sem categoria derruba o bloco inteiro e
         ninguém descobre por quê. No catálogo da Prime são 27 de 38. #}
      <div class="oc-avi amb" id="oc-sem-cat" style="display:none">
        <div class="txt"><b id="oc-sem-cat-t"></b>
          <span style="opacity:.85">A folha do cliente sai sem os subtotais por categoria.</span></div>
        <button type="button" id="oc-sem-cat-b">Ver o catálogo</button>
      </div>
      {% endif %}
      <a id="oc-vertodos" href="#" class="oc-vertodos-link" style="display:none">📋 ver os <span id="oc-vertodos-n">0</span> serviços em ordem alfabética ›</a>
      <div id="oc-catalogo-completo" class="oc-catalogo-completo"></div>
      <div id="oc-mods-empty" class="oc-empty" style="display:none">
        <b>Você ainda não cadastrou seus serviços</b>
        <p class="mut" style="margin:.3rem 0 0">{{ 'Adicione o que a sua empresa vende — nome e valor. Isso vira o seu catálogo pra montar orçamentos.' if servico_avulso else 'Adicione o que a sua empresa vende — nome, setup e mensalidade. Isso vira o seu catálogo pra montar orçamentos.' }}</p>
        <div style="display:flex; gap:.5rem; justify-content:center; margin-top:.8rem; flex-wrap:wrap">
          <button id="oc-add2" class="oc-btn-g" type="button" style="border:0; border-radius:8px; padding:.5rem 1rem; font-weight:600; cursor:pointer">+ Adicionar serviço</button>
          {% if not servico_avulso %}<button id="oc-import" class="oc-pill" type="button">Usar modelo de tecnologia</button>{% endif %}
        </div>
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
      {# PARCELA SEM VENCIMENTO. Título sem data nasce sem cobrança e não entra no
         fluxo de caixa — e as seis parcelas do orçamento nº 23 da Prime estão
         assim. O aviso vem com o conserto do lado: aviso que só aponta o
         problema deixa a pessoa procurando onde arrumar, e aí ela não arruma. #}
      <div class="oc-avi cor" id="pg-sem-venc" style="display:none">
        <div class="txt"><b id="pg-sem-venc-t"></b>
          <span style="opacity:.9">Sem vencimento o título nasce sem cobrança e não entra no fluxo de caixa.</span></div>
        <button type="button" id="pg-sem-venc-b">Definir vencimentos</button>
      </div>
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
      {% if servico_avulso %}
      <div class="oc-ll"><span class="mut">Investimento inicial</span><b id="oc-r-setup">R$ 0,00</b></div>
      <div class="oc-ll" id="oc-r-margem-l" style="display:none"><span class="mut">Margem</span><b id="oc-r-margem" style="color:var(--verde-claro); font-size:.95rem">-</b></div>
      <div class="oc-total"><div class="mut" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--verde-claro)">Total</div><div class="v" id="oc-r-ano">R$ 0,00</div><div class="mut" id="oc-r-eco" style="display:none; font-size:.8rem; color:var(--verde-claro); margin-top:.3rem"></div></div>
      {% else %}
      {# RECORRENTE (24/09/2026, mockup zaq_servicos_valores_pilulas): o número
         grande é o que o cliente paga POR MÊS — é o que se negocia e o que a
         folha do cliente destaca. O 1º ano vira uma linha. No evento, nada muda:
         lá o número grande continua sendo o total ("só valor mesmo", dono). #}
      <div class="oc-total"><div class="mut" id="oc-r-mensal-rot" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--verde-claro)">Investimento mensal</div>
        <div class="v"><span id="oc-r-mensal">R$ 0,00</span><span id="oc-r-mensal-per" class="oc-per"> /mês</span></div>
        <div class="mut" id="oc-r-eco" style="display:none; font-size:.8rem; color:var(--verde-claro); margin-top:.3rem"></div></div>
      <div class="oc-ll"><span class="mut">Mensalidade de tabela</span><b id="oc-r-tabela">R$ 0,00</b></div>
      {% endif %}
      {% if servico_avulso %}
      {# O QUE VEM JUNTO. Fora do total de propósito: é o que separa "nove itens
         inclusos, R$ 13.850 de tabela" de "Economia de R$ 14.850" — que era o
         que esta tela dizia numa proposta onde ninguém tinha descontado nada. #}
      <div class="oc-inclbox" id="oc-inclusos" style="display:none">
        <div class="lin"><span>Incluso no pacote · <b id="oc-incl-n" style="font-weight:600">0 itens</b></span><b id="oc-incl-v">R$ 0</b></div>
        <p>Não entra no total. Na folha do cliente cada um sai marcado <b style="color:var(--verde-claro)">Incluso</b>, com o valor de tabela ao lado.</p>
      </div>
      {% endif %}
      <div class="oc-ll oc-dline" id="oc-r-descitens-l" style="display:none"><span class="mut">{{ 'Descontos por item' if servico_avulso else 'Desconto nos serviços' }}</span><b id="oc-r-descitens">R$ 0,00</b></div>
      <div class="oc-ll" id="oc-r-sub-l" style="display:none"><span class="mut">Subtotal com descontos</span><b id="oc-r-sub">R$ 0,00</b></div>
      <div class="oc-ll oc-dline" id="oc-r-descfim-l" style="display:none"><span class="mut">Desconto no total</span><b id="oc-r-descfim">R$ 0,00</b></div>
      {% if not servico_avulso %}
      <div class="oc-ll"><span class="mut">Implantação</span><b id="oc-r-setup">sem taxa</b></div>
      <div class="oc-ll"><span class="mut">Total 1º ano</span><b id="oc-r-ano">R$ 0,00</b></div>
      <div class="oc-ll" id="oc-r-margem-l" style="display:none"><span class="mut">Margem/mês</span><b id="oc-r-margem" style="color:var(--verde-claro); font-size:.95rem">-</b></div>
      {% endif %}
      <!-- o desconto do TOTAL vale nos dois modos: consultoria e advocacia vendem
           por orçamento igual, e só não tinham desconto porque ele morava dentro
           do jsonb do evento. -->
      <div class="oc-field" style="margin-top:.7rem; margin-bottom:0">
        <label class="mut" style="font-size:.76rem">Desconto no total</label>
        <div class="oc-dpar oc-dpar-tot" data-tipo="pct">
          <i class="oc-rs">R$</i><input id="oc-desconto" class="oc-inp oc-desc-inp" inputmode="decimal" value="0">
          <span class="oc-dtog">
            <button type="button" data-t="pct" class="on">%</button>
            <button type="button" data-t="valor">R$</button>
          </span>
        </div>
        <button id="oc-desc-zerar" class="oc-dzero" type="button">zerar desconto</button>
      </div>
      {% if not servico_avulso %}
      <button id="oc-anual" class="oc-pill" data-on="0" type="button" style="width:100%; margin-top:.7rem; text-align:left; display:flex; justify-content:space-between; align-items:center">Pagamento anual à vista (−15%) <span id="oc-anual-mk" class="oc-sw" aria-hidden="true"></span></button>
      {% endif %}
      <button id="oc-gerar" class="oc-btn oc-btn-g">Gerar proposta</button>
      <button id="oc-salvar" class="oc-btn oc-btn-o">Salvar no funil</button>
    </div>
  </div>
</div>

</div>{# /#oc-editor #}

<div class="card" id="oc-funil">
  <div class="fn-cab">
    <h2 style="margin:0">Funil</h2>
    <button id="fn-novo" class="oc-btn-g fn-novo" type="button">+ Nova proposta</button>
  </div>
  {# AS TRÊS ABAS. Quem decide em qual delas a linha cai é `vendas.grupo_do_funil`,
     derivada do mesmo painel que desenha os selos — a aba nunca discorda da
     linha. Os rótulos e a contagem vêm do servidor. #}
  <div class="fn-abas" id="fn-abas"></div>
  <div class="fn-filtros">
    <input type="search" id="fn-busca" class="oc-inp" autocomplete="off"
           placeholder="Buscar por nome, nº ou data…" aria-label="Buscar no funil">
    {% if ve_todos %}
    <label class="fn-vend"><span class="mut">Vendedor</span>
      <select id="fn-vendedor" class="oc-inp" aria-label="Filtrar por vendedor"></select>
    </label>
    {% endif %}
  </div>
  <div id="oc-hist-box"><p class="mut">Carregando...</p></div>
  <p class="mut fn-vazio" id="fn-vazio" style="display:none"></p>
</div>

{# A BARRA DE TOTAL DO CELULAR. Fixa no rodapé enquanto o editor está aberto e a
   tela é estreita — é onde o vendedor monta o orçamento. Some junto com o
   editor; no desktop o Resumo grudado à direita continua sendo o que manda. #}
<div class="oc-barra" id="oc-barra">
  <div class="vl">
    <div class="rot">{{ 'Total' if servico_avulso else 'Investimento mensal' }}</div>
    <div class="num" id="barra-total">R$ 0</div>
    <div class="leg" id="barra-leg"></div>
  </div>
  <div class="esp"></div>
  <button type="button" class="o" id="barra-salvar">Salvar</button>
  <button type="button" class="g" id="barra-gerar">Gerar</button>
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
""" + _JS_TAG + r"""
{% endblock %}"""

# Registra o template no env do portal (reusa base/nav/gate do painel).
_env.loader.mapping["servicos"] = _SERVICOS_TPL
