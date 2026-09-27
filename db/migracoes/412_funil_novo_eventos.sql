-- 412_funil_novo_eventos.sql
-- O FUNIL NOVO DE EVENTOS, parte 1: as colunas e os gatilhos (docs/mockups/
-- funil_novo_eventos.html, aprovado pelo dono em 27/09/2026 "com as recomendações",
-- inclusive a troca das colunas da Prime por migração, com a autorização dele).
--
-- 1. `prospeccao.ficha_completa_em`: o instante em que tipo de festa, data e
--    convidados ficaram os três preenchidos. Quem grava é o PRÓPRIO BANCO (gatilho
--    de linha), qualquer que seja o caminho — o leitor da conversa, a IA, a ficha, o
--    orçamento —, e só a PRIMEIRA vez: o instante não anda a cada mexida, senão
--    atravessaria a TRAVA 3 do funil (evento anterior ao último movimento manual não
--    mexe no card). Se um dos três for apagado, a marca volta a nulo.
--    O backfill carimba quem já está completo com o `atualizado_em` do lead.
--
-- 2. As colunas da PRIME (conta 34), e só dela — nenhuma outra conta muda aqui. As
--    outras contas de eventos recebem o modelo novo como PROPOSTA (a faixa "colunas
--    fora do modelo" no topo do funil, finance/funil_modelo.py): nada muda sem o dono
--    marcar. Na Prime, medido em produção em 27/09/2026 (só leitura):
--      'qualificado'      "Agendado Visita"    → "Visita marcada" (só o nome)
--      'evento_realizado' "Orcamento Assinado" → "Data segurada", VOLTA ao quadro
--                         (0 leads hoje: o card saía do quadro na aprovação)
--      'follow_up'        "Follow-up"          → sai do quadro (0 leads; nunca se apaga
--                         etapa — CLAUDE.md §0, funil_modelo regra 1)
--      novas: 'ficha_completa' "Qualificado" (gatilho ficha_completa), 'visita_feita'
--             "Visita feita" (gatilho compromisso_feito), 'pos_festa' "Pós-festa"
--             (fase pós-venda, gatilho festa_passou) — com os gatilhos LIGADOS, como a
--             régua da Prime já está (gatilhos_modo = 'ligado').
--    No primeiro ciclo depois do deploy devem andar, pelo medido: 34 cards de
--    Contatado → Qualificado, 8 de Agendado Visita → Visita feita e 1 de Fechado →
--    Pós-festa. Nenhum card anda pra trás (TRAVA 1), nenhum perdido se mexe.
--
-- Não toca em conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1).
-- Aditiva e idempotente: roda de novo sem efeito.

alter table public.prospeccao add column if not exists ficha_completa_em timestamptz;

create or replace function public.prospeccao_ficha_completa() returns trigger
language plpgsql as $$
begin
  if coalesce(btrim(new.evento_tipo), '') <> '' and new.evento_em is not null
     and new.evento_convidados is not null then
    if new.ficha_completa_em is null then
      new.ficha_completa_em := now();
    end if;
  else
    new.ficha_completa_em := null;
  end if;
  return new;
end $$;

drop trigger if exists prospeccao_ficha_completa on public.prospeccao;
create trigger prospeccao_ficha_completa
  before insert or update of evento_tipo, evento_em, evento_convidados on public.prospeccao
  for each row execute function public.prospeccao_ficha_completa();

-- `atualizado_em` existe em produção mas não nasce de migração nenhuma (o replay do
-- CI, que monta só o esqueleto das tabelas, não a tem): sem ela, o carimbo é agora.
do $$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'prospeccao'
                and column_name = 'atualizado_em') then
    execute $q$update public.prospeccao
                  set ficha_completa_em = coalesce(atualizado_em, criado_em, now())
                where ficha_completa_em is null
                  and coalesce(btrim(evento_tipo), '') <> '' and evento_em is not null
                  and evento_convidados is not null$q$;
  else
    update public.prospeccao
       set ficha_completa_em = now()
     where ficha_completa_em is null
       and coalesce(btrim(evento_tipo), '') <> '' and evento_em is not null
       and evento_convidados is not null;
  end if;
end $$;

-- ── a Prime (34) ─────────────────────────────────────────────────────────────
update public.funil_etapas set rotulo = 'Visita marcada', semeado_de = 'eventos'
 where conta_id = 34 and chave = 'qualificado' and rotulo = 'Agendado Visita';

update public.funil_etapas
   set rotulo = 'Data segurada', sai_do_quadro = false, semeado_de = 'eventos'
 where conta_id = 34 and chave = 'evento_realizado' and rotulo = 'Orcamento Assinado';

update public.funil_etapas set sai_do_quadro = true
 where conta_id = 34 and chave = 'follow_up' and not sai_do_quadro
   and not exists (select 1 from public.prospeccao p
                    where p.conta_id = 34 and p.status = 'follow_up');

insert into public.funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase, gatilho,
                                 gatilho_ativo, sai_do_quadro, agenda_ao_entrar, semeado_de)
select 34, v.chave, v.rotulo, v.ordem, false, v.fase, v.gatilho, true, false, false, 'eventos'
  from (values ('ficha_completa', 'Qualificado',  20,  'venda', 'ficha_completa'),
               ('visita_feita',   'Visita feita', 35,  'venda', 'compromisso_feito'),
               ('pos_festa',      'Pós-festa',    905, 'pos',   'festa_passou'))
       as v(chave, rotulo, ordem, fase, gatilho)
 where exists (select 1 from public.funil_etapas where conta_id = 34)
on conflict (conta_id, chave) do nothing;

-- rollback:
--   drop trigger if exists prospeccao_ficha_completa on public.prospeccao;
--   drop function if exists public.prospeccao_ficha_completa();
--   (a coluna `ficha_completa_em` pode ficar: só é lida pelo gatilho do funil)
--   delete from public.funil_etapas where conta_id = 34
--      and chave in ('ficha_completa','visita_feita','pos_festa')
--      and not exists (select 1 from public.prospeccao p where p.conta_id = 34
--                       and p.status = funil_etapas.chave);
--   update public.funil_etapas set rotulo = 'Agendado Visita' where conta_id = 34 and chave = 'qualificado';
--   update public.funil_etapas set rotulo = 'Orcamento Assinado', sai_do_quadro = true
--    where conta_id = 34 and chave = 'evento_realizado';
--   update public.funil_etapas set sai_do_quadro = false where conta_id = 34 and chave = 'follow_up';
