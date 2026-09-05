-- 199_novidades_pra_quem.sql
-- O aviso aprende PRA QUEM ele é e ganha um resumo pro site (mockup
-- docs/mockups/novidades_tres_lugares.html, aprovado em 05/09/2026). As
-- entregas de hoje entram como aviso na 200.
--
-- O QUE ACONTECEU
-- O último aviso publicado era de 19/08. Tudo que subiu depois — inclusive as
-- quatro entregas do funil de 05/09 (#622, #625, #626, #627) — foi pro ar sem
-- aviso. É exatamente o caso que a 174 existe pra impedir, e desta vez foi o
-- próprio autor do sistema de avisos que esqueceu de usá-lo.
--
-- E TEM UM BURACO QUE NÃO ERA DE ESQUECIMENTO: o vendedor nunca recebe aviso
-- nenhum. A tela é de dono e gestor (contas.equipe.recebe_novidades), e o app
-- dele não tem onde mostrar. Dos 7 avisos de agosto, 3 falavam do app DELE (o
-- microfone, o desconto no celular, o atalho que saiu) — e ele soube por
-- ninguém.
--
-- TRÊS COLUNAS, ADITIVAS
--   pra_quem  text[]  quem recebe, por PAPEL: dono, gestor, vendedor. Padrão
--                     {dono,gestor}, que é o comportamento de hoje — nenhum aviso
--                     antigo muda de público por causa desta migração. A lista
--                     que o check aceita é espelhada em finance.novidades.PAPEIS,
--                     e um teste compara as duas (a mesma trava dos públicos).
--   resumo    text    uma linha pro site (zaq-ia.com/atualizacoes) e pra faixa do
--                     app. Fala pra fora ("o funil abre no mês atual"), enquanto
--                     o corpo fala com quem já usa. Nulo = o site não lista.
--   link      text    onde "Ver como ficou" leva. Caminho relativo do próprio Zaq.
--
-- E os três avisos de agosto que eram do app do vendedor passam a chegar nele
-- também (array_append, sem tirar dono e gestor). Quem já leu no painel continua
-- lido: novidade_lida é por pessoa, e o vendedor é outra pessoa.
--
-- Aditiva e idempotente.

-- ────────────────────────────────────────────── 1. as colunas
alter table public.novidades
    add column if not exists pra_quem text[] not null default '{dono,gestor}';
alter table public.novidades
    add column if not exists resumo text;
alter table public.novidades
    add column if not exists link text;

alter table public.novidades drop constraint if exists novidades_pra_quem_check;
alter table public.novidades add constraint novidades_pra_quem_check
    check (pra_quem <@ array['dono','gestor','vendedor']::text[]
           and cardinality(pra_quem) > 0);

-- ────────────────────────────────────────────── 2. os de agosto que eram do vendedor
update public.novidades
   set pra_quem = array_append(pra_quem, 'vendedor')
 where chave in ('voz-no-app-do-vendedor', 'desconto-no-app-do-vendedor',
                 'atalho-whatsapp-fechado')
   and not ('vendedor' = any(pra_quem));

-- ────────────────────────────────────────────── 3. o resumo dos que já existem
update public.novidades set resumo = v.resumo
  from (values
    ('agenda-de-eventos',           'A agenda ganhou cara de eventos: a festa, o horário e quem cuida, no dia certo.'),
    ('contrato-assinatura-fecha',   'O contrato assinado é o que fecha o negócio e abre o financeiro.'),
    ('desconto-por-item',           'Desconto por item da proposta, em % ou em R$, com o total por cima.'),
    ('desconto-no-app-do-vendedor', 'O vendedor dá desconto pelo celular, direto na proposta.'),
    ('agenda-pendencias-e-tipos',   'A agenda avisa quando duas festas caem no mesmo dia.'),
    ('voz-no-app-do-vendedor',      'O vendedor manda áudio pelo Zaq, e o áudio chega transcrito no histórico.'),
    ('atalho-whatsapp-fechado',     'O atalho "Mandar no WhatsApp" saiu do app de quem fala pelo próprio número.')
  ) as v(chave, resumo)
 where public.novidades.chave = v.chave
   and public.novidades.resumo is null;

-- Os avisos de hoje ficam na 200 (200_novidade_funil_mes_atual.sql): esta
-- migração é só o schema e o acerto dos avisos que já existem.

-- rollback:
--   alter table public.novidades drop constraint if exists novidades_pra_quem_check;
--   alter table public.novidades drop column if exists link;
--   alter table public.novidades drop column if exists resumo;
--   alter table public.novidades drop column if exists pra_quem;
