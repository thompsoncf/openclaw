-- 330_novidade_visao_periodo_so_datas.sql
-- O aviso da 329 (Visão da Equipe) descrevia a pílula Período com atalhos —
-- "ontem, 7 dias, 30 dias, mês passado". Na mesma noite, o dono abriu a tela e
-- tirou os atalhos: "sem necessidade, só deixa as datas mesmo com calendário".
-- Hoje · Semana · Mês já estão nas pílulas; o Período ficou só com as duas datas.
--
-- POR QUE ATUALIZA EM VEZ DE CRIAR OUTRO. Mesmo recurso, mesmo dia, e o aviso da
-- 329 ainda nem foi publicado (publicado_em 24/09 18:00 UTC). Mesma receita da
-- 221: só o `corpo` muda; `titulo`, `resumo`, `link` e `publicado_em` ficam.
--
-- `replace` de um trecho fixo: idempotente, e sem efeito se a 329 não rodou.

update public.novidades
   set corpo = replace(corpo,
       'a pílula Período: atalhos (ontem, 7 dias, 30 dias, mês passado) ou as duas datas.',
       'a pílula Período: escolha as duas datas no calendário.')
 where chave = 'visao-da-equipe';

-- rollback:
--   update public.novidades set corpo = replace(corpo,
--     'a pílula Período: escolha as duas datas no calendário.',
--     'a pílula Período: atalhos (ontem, 7 dias, 30 dias, mês passado) ou as duas datas.')
--    where chave = 'visao-da-equipe';
