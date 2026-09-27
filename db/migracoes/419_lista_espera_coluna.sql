-- 419_lista_espera_coluna.sql
-- A LISTA DE ESPERA NO FUNIL — o funil novo de eventos, parte 2b (docs/mockups/
-- funil_novo_rotinas.html, aprovado pelo dono em 27/09/2026 "com as recomendações").
-- O motor é finance/lista_espera.py (existe desde a migração 216, desligado na Prime).
--
-- 1. A PRIME (34) passa a dizer quantas festas faz por dia: 1 (decisão 2 do mockup;
--    medido na agenda: 40 de 41 dias com festa tiveram uma só). É o número que liga a
--    lista de espera, o selo "data ocupada" e o aviso de "a data abriu". Só se o campo
--    está vazio: o dono pode ter preenchido em Empresa antes do deploy.
--    Medido em 27/09/2026 (só leitura): 50 cards em jogo pedem uma data que outro
--    cliente já tem (37 delas em festas lançadas direto na agenda, sem card). Eles
--    ganham o selo; NENHUM muda de coluna sozinho — o card só vai pra Lista de espera
--    quando o cliente aceita esperar.
--
-- 2. A coluna 'lista_espera' "Lista de espera" na Prime, antes do Perdido (ordem 908,
--    fase venda, sem gatilho: quem põe é gente, e quem tira é a data abrir ou passar).
--
-- Não toca conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1), nem
-- card nenhum. Aditiva e idempotente.

update public.contas set festas_por_dia = 1 where id = 34 and festas_por_dia is null;

insert into public.funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase, gatilho_ativo,
                                 sai_do_quadro, agenda_ao_entrar, semeado_de)
select 34, 'lista_espera', 'Lista de espera', 908, false, 'venda', false, false, false, 'eventos'
 where exists (select 1 from public.funil_etapas where conta_id = 34)
on conflict (conta_id, chave) do nothing;

-- rollback:
--   delete from public.funil_etapas where conta_id = 34 and chave = 'lista_espera'
--      and not exists (select 1 from public.prospeccao p where p.conta_id = 34
--                       and p.status = 'lista_espera');
--   update public.contas set festas_por_dia = null where id = 34;
