-- 448_evento_stands.sql
-- Estande numerado de feira/evento, com posição na planta e reserva com prazo.
--
-- O PEDIDO (Outlet Chic, conta 40, 28/09/2026): venda de estandes de uma feira
-- via WhatsApp/Zaq, com mapa da planta oficial, reserva por 3 dias e confirmação
-- MANUAL de pagamento pelo dono (sem gateway automático nessa ponta).
--
-- POR QUE NÃO servicos_catalogo. servicos_catalogo (071) já resolve "catálogo
-- de item que a conta vende", mas é 1 LINHA = 1 TIPO de item — não modela
-- unidade física individual com posição própria. Um estande 4x2 não é "um tipo
-- de estande 4x2": é o estande G58, numerado, num lugar específico da planta,
-- e SÓ ELE fica indisponível quando vende — os outros 4x2 continuam livres.
-- Forçar isso em servicos_catalogo exigiria 1 linha de catálogo por estande
-- (200+ linhas) só pra ter uma PK pra reservar, e ainda faltaria pavilhão/zona.
--
-- POR QUE NÃO eventos_agenda. eventos_agenda é "uma DATA do calendário do
-- dono" (a data de UMA festa). Aqui a unidade que reserva é o ESTANDE, não uma
-- data — o evento (Outlet Chic 2026) é um só, com 200+ estandes vendáveis nele.
-- É o mesmo problema de fundo, um nível abaixo: por isso a pré-reserva daqui é
-- clonada do padrão de eventos_agenda (160_agenda_pre_reserva.sql) — mesmo
-- status/prazo/job, aplicado por ESTANDE em vez de por DATA.
--
-- POR QUE UM STATUS PRÓPRIO e não boolean "vendido": o painel do gestor precisa
-- diferenciar livre / pré-reservado (aguardando pagamento, ainda pode cair) /
-- vendido (fechado) pra decidir o que mostrar e o que cobrar. Dois estados
-- booleanos (reservado + pago) deixam estado inválido representável
-- (reservado=false, pago=true); um enum não deixa.
--
-- Genérico o bastante pra servir qualquer conta do nicho 'eventos' que venda
-- espaço numerado (feira, salão de festa com estandes, exposição) — não é
-- exclusivo da Outlet Chic, só nasce com ela.
create table if not exists public.evento_stands (
    id               bigserial primary key,
    conta_id         bigint not null references public.contas(id) on delete cascade,
    codigo           text not null,       -- 'i05', 'G58', 'S103', 'C159' — código oficial da planta
    pavilhao         text not null,       -- 'inferior' | 'superior' | 'outlet_car' (livre por conta)
    zona             text,                -- 'Outlet Grifes', 'Home Decor'... (livre, mesmo padrão de servicos_catalogo.categoria)
    tamanho          text not null,       -- '2x2' | '3x2' | '3x3' | '4x2' | '4x3' (m²) OU 'tenda' | 'personalizado'
                                           -- (produto do Outlet Car, vendido por TIPO, não por m²) — combina com sizeLabel do mapa
    preco_centavos   bigint,              -- null = "consultar" (fallback; toda unidade conhecida hoje tem preço fechado — até o Outlet Car, que vende por tipo "tenda"/"personalizado", não por m²)
    status           text not null default 'livre'
                     check (status in ('livre', 'pre_reservado', 'vendido')),
    pre_reserva_ate  timestamptz,         -- prazo da pré-reserva; null = não está reservado
    comprovante_url  text,                -- comprovante enviado pela página pública ou pelo WhatsApp
    comprovante_em   timestamptz,         -- quando chegou — é o que dispara pre_reservado no modo 'pagamento'
    prospeccao_id    bigint references public.prospeccao(id) on delete set null, -- lead/CRM dono da reserva
    -- Sinal + parcelas do estande, IGUAL ao plano de pagamento de evento comum:
    -- vivem em orcamentos.sinal_centavos/parcelas (migrações 147/161), não aqui.
    -- Esta linha só aponta pra qual orçamento é o dono da venda; confirmar o
    -- sinal manualmente (dono vendo o comprovante) é o que passa livre -> vendido
    -- no modo 'pagamento' — o resto das parcelas continua correndo no orçamento
    -- normalmente, sem travar nem destravar o estande de novo.
    orcamento_id     bigint references public.orcamentos(id) on delete set null,
    ordem            int default 0,       -- ordem de exibição no mapa, dentro da zona
    criado_em        timestamptz not null default now(),
    atualizado_em    timestamptz not null default now(),
    unique (conta_id, codigo)
);

-- "o que mostrar no mapa" — filtro mais comum da página pública e do painel
create index if not exists idx_evento_stands_conta
    on public.evento_stands (conta_id, status);

-- por onde o job de expiração varre; parcial porque a maioria nunca chega a
-- ficar pré-reservada ao mesmo tempo (mesmo raciocínio da 160).
create index if not exists idx_evento_stands_pre_reserva
    on public.evento_stands (pre_reserva_ate)
 where status = 'pre_reservado';

-- Config por conta: quando a pré-reserva trava, quantos dias ela segura, e o
-- slug da página pública (/e/<slug>). Mesmo padrão de agenda_config/raio_x_config
-- — tabela separada, só existe linha pra quem usa a feature, sem poluir `contas`.
--
-- SLUG SEM ANO DE PROPÓSITO (pedido do dono, 28/09/2026): "outlet-chic", não
-- "outlet-chic-2026" — o link é da EMPRESA, não da edição. A edição (32ª,
-- datas, local) é conteúdo que o dono edita ano a ano; o link que já circulou
-- (cartão, bio do Instagram, anúncio antigo) continua valendo.
--
-- BLOQUEIA_EM (pedido do dono, 28/09/2026): "o bloqueio do stand só existe
-- depois que paga o sinal" — travar na hora do PEDIDO deixa qualquer um
-- segurar um estande bom por 3 dias sem nenhuma intenção real de pagar.
--   'pedido'    -> trava assim que o cliente pede (Zaq já marca pre_reservado
--                  na conversa). Bom pra quem prefere garantir a venda em
--                  andamento e aceita o risco de segurar sem pagamento.
--   'pagamento' -> continua 'livre', visível e concorrendo com outros
--                  interessados, até o cliente ENVIAR o comprovante (pela
--                  página ou pelo WhatsApp) — é o comprovante que dispara
--                  pre_reservado. pre_reserva_dias aqui vira o prazo que a
--                  EQUIPE tem pra conferir e confirmar antes de expirar
--                  sozinho — uma rede de segurança, não um prazo pro cliente.
-- Padrão 'pagamento' porque foi o pedido explícito da Outlet Chic; conta nova
-- decide na hora de configurar o evento.
create table if not exists public.evento_stands_config (
    conta_id         bigint primary key references public.contas(id) on delete cascade,
    slug             text not null unique,  -- ex.: 'outlet-chic' — da empresa, não da edição
    bloqueia_em      text not null default 'pagamento'
                     check (bloqueia_em in ('pedido', 'pagamento')),
    pre_reserva_dias int not null default 3,
    whatsapp_numero  text,                  -- link secundário de dúvida na página pública
    pix_chave        text,                  -- chave Pix mostrada na página pública
    pix_titular      text,                  -- nome de quem recebe, pro cliente conferir
    -- conteúdo da EDIÇÃO atual, editável sem mexer em código nem na URL:
    edicao_label     text,                  -- ex.: '32ª edição'
    evento_inicio    date,
    evento_fim       date,
    evento_local     text,                  -- ex.: 'Centro de Convenções · Teresina-PI'
    criado_em        timestamptz not null default now(),
    atualizado_em    timestamptz not null default now()
);

-- rollback:
--   drop table if exists public.evento_stands_config;
--   drop index if exists idx_evento_stands_pre_reserva;
--   drop index if exists idx_evento_stands_conta;
--   drop table if exists public.evento_stands;
