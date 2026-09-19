-- 289_apolice_remetentes.sql
-- Quem pode mandar apólice pelo WhatsApp da empresa.
--
-- O PEDIDO (dono, 19/09/2026), depois de a lista do WhatsApp entrar no #734 e ele
-- perguntar se tudo passava pelo número vinculado: "limita a lista só pros
-- numeros de corretor e seguradora".
--
-- POR QUE A LISTA PRECISA DISSO. O #734 já prendia a lista ao número da EMPRESA
-- (a consulta casa mensagens -> conversas -> conta_id). O que ela não distinguia
-- era QUEM escreveu pra esse número. Medido na Liberal em 18/09/2026, nos 90 dias
-- anteriores: 36 PDFs, e a maioria não era apólice — uma dúzia de boletos da
-- mesma pessoa, extrato bancário do mês, um agravo de instrumento. Filtrar por
-- remetente tira o ruído na origem, em vez de pedir pro olho fazer isso toda vez.
--
-- A CHAVE É `contato_ref`, NÃO O NOME. O nome vem do perfil do WhatsApp (o
-- `pushName`) e muda quando a pessoa troca; o número não. `rotulo` guarda o nome
-- de quando foi liberado, só pra tela ter o que mostrar.
--
-- `tipo` é RÓTULO, não regra: corretor e seguradora entram pela mesma porta e
-- fazem a mesma coisa. Existe pra tela dizer quem é quem, e pra o dia em que a
-- diferença importar não precisar de migração.
--
-- NADA É LIBERADO SOZINHO. A tabela nasce vazia de propósito: semear com quem já
-- mandou PDF liberaria o fornecedor de boleto junto com o corretor. Quem libera é
-- a pessoa, um toque por remetente, na própria janela do cadastro.
--
-- Aditivo e idempotente.

create table if not exists public.apolice_remetentes (
    id          bigserial primary key,
    conta_id    bigint      not null references public.contas(id) on delete cascade,
    contato_ref text        not null,
    rotulo      text        not null default '',
    tipo        text        not null default 'corretor'
                check (tipo in ('corretor','seguradora')),
    criado_em   timestamptz not null default now(),
    criado_por  bigint
);

-- um número aparece uma vez por conta; liberar de novo é atualizar o rótulo
create unique index if not exists ux_apolice_remetentes
    on public.apolice_remetentes (conta_id, contato_ref);

-- rollback:
--   drop table if exists public.apolice_remetentes;
