-- 373_perda_volta_em.sql
-- O lead perdido que VOLTA sozinho pro funil numa data (finance/funil_perda.py,
-- `voltar_os_vencidos`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seção 08 — "Restrição no CPF volta sozinho:
-- o lead volta ao funil quando o prazo que ele deu pra limpar o nome vence".
--
-- Na casa do Minha Casa Minha Vida, quem reprova é a Caixa, não o cliente: o
-- comprador com nome sujo que diz "limpo até dezembro" não é venda perdida, é
-- venda adiada. Sem a data guardada, ninguém lembra de voltar.
--
-- A coluna é do lead e vale pra qualquer conta; a tela só oferece o campo no
-- perfil `obras` por enquanto. Ao voltar, a data é zerada (o motivo e a data da
-- perda ficam: são o histórico).
--
-- Aditiva e idempotente.

alter table public.prospeccao add column if not exists perda_volta_em date;
create index if not exists idx_prospeccao_perda_volta
    on public.prospeccao (perda_volta_em) where perda_volta_em is not null;

-- rollback:
--   drop index if exists public.idx_prospeccao_perda_volta;
--   alter table public.prospeccao drop column if exists perda_volta_em;
