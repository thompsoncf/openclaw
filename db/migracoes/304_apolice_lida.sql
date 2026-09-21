-- 304_apolice_lida.sql
-- A apólice que chegou no WhatsApp já vem lida — o pré-cadastro.
--
-- O PEDIDO (dono, 21/09/2026), depois de eu levantar que nem o agente do WhatsApp
-- nem o bot do Telegram liam apólice: "pode fazer o leitor automatico".
--
-- O MOLDE É O LEITOR DE EVENTOS (`finance/evento_leitor.py`, migração 198). Ele roda
-- sozinho a cada mensagem que entra, tira o que dá da conversa, preenche só o que
-- está vazio e carimba de onde tirou. Este é o gêmeo dele no nicho seguros, com uma
-- diferença: o de eventos lê PALAVRA, o de seguros lê o DOCUMENTO anexado.
--
-- POR QUE UMA TABELA E NÃO A PRÓPRIA `apolices`. O leitor NÃO cadastra. Gravar
-- direto transformaria erro de leitura em dado errado no banco — e vigência lida
-- errada é alerta que não dispara, o pior defeito da tela de Renovações. O que ele
-- faz é deixar a conferência PRONTA: a lista do WhatsApp deixa de mostrar só o nome
-- do arquivo e passa a mostrar seguradora, segurado e vencimento, e um toque abre o
-- formulário já preenchido. Quem confirma continua sendo gente.
--
-- `mensagem_id` é único: a mesma mensagem lida duas vezes é a mesma apólice. É por
-- ele que o leitor sabe o que já passou e não baixa o mesmo PDF de novo.
--
-- `erro` guarda a falha com o mesmo peso do acerto. Sem isso o leitor tentaria pra
-- sempre o PDF que o CDN do WhatsApp já apagou, e a lista não teria como dizer por
-- que aquele arquivo não abriu.
--
-- `form` é o formulário pronto (o mesmo dicionário que a janela preenche). Guardar
-- custa alguns KB e economiza a releitura inteira no toque — que é justamente onde
-- o dono pediu pressa: "faz uma coisa rapida e deixa o back trabalhando".
--
-- Aditivo e idempotente.

create table if not exists public.apolice_lida (
    id              bigserial primary key,
    conta_id        bigint      not null references public.contas(id) on delete cascade,
    mensagem_id     bigint      not null,
    -- o PDF salvo no cofre: ler é também o que RESGATA o arquivo antes de o CDN
    -- do WhatsApp apagar, que é o prazo real de tudo isto
    pdf_caminho     text,
    pdf_nome        text,
    pdf_bytes       integer,
    -- o resumo pra lista, já pronto pra mostrar sem abrir nada
    seguradora      text,
    reconhecida     boolean     not null default false,
    segurado        text,
    numero_proposta text,
    vigencia_fim    date,
    -- o que a janela usa no toque: `form` preenche os campos, `lido` vai pro
    -- `apolices.pdf_lido` quando a pessoa confirmar
    form            jsonb,
    lido            jsonb,
    erro            text,
    criado_em       timestamptz not null default now()
);

-- uma leitura por mensagem: reentrega do wa-qr não relê nem rebaixa o que já passou
create unique index if not exists ux_apolice_lida_msg
    on public.apolice_lida (mensagem_id);

-- a lista da janela filtra por conta e desce por data
create index if not exists ix_apolice_lida_conta
    on public.apolice_lida (conta_id, criado_em desc);

-- rollback:
--   drop table if exists public.apolice_lida;
