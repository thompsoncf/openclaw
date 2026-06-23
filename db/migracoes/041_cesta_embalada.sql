-- ============================================================
-- Migração 041 — marca de "cesta embalada" (sub-aba Embalagem)
-- ============================================================
-- Quando o fornecedor marca a cesta como embalada na tela de
-- Embalagem (pack-list), gravamos o momento aqui. Útil pra:
--  - Mostrar visualmente quais cestas já foram montadas
--  - Métricas futuras: "tempo médio entre confirmar e embalar"
--  - Não bloqueia nada: é informativo, não muda fluxo
--
-- Idempotente. Sem alteração de status_check da cesta.
-- ============================================================

alter table cesta_semana
  add column if not exists embalada_em timestamptz;

-- Índice parcial: só linhas ainda não embaladas (úteis pra "preciso embalar")
create index if not exists ix_cesta_nao_embalada
  on cesta_semana (fornecedor_id, data_entrega)
  where embalada_em is null and status = 'confirmada';

-- ============================================================
-- ROLLBACK:
--   drop index if exists ix_cesta_nao_embalada;
--   alter table cesta_semana drop column if exists embalada_em;
-- ============================================================
