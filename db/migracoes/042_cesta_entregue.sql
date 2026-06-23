-- ============================================================
-- Migração 042 — marca de "cesta entregue" (sub-aba Rotas)
-- ============================================================
-- Quando o fornecedor toca à Entregue na tela de Rotas, o status
-- vai pra 'entregue' E o timestamp fica gravado aqui (mesmo padrão
-- do `embalada_em` da 041). Permite métricas e auditoria.
--
-- Idempotente. Status 'entregue' já é válido no check de cesta_semana.
-- ============================================================

alter table cesta_semana
  add column if not exists entregue_em timestamptz;

-- Índice parcial: cestas confirmadas/embaladas pendentes de entrega
-- (útil pra Rotas listar rápido o "que ainda preciso entregar")
create index if not exists ix_cesta_pendente_entrega
  on cesta_semana (fornecedor_id, data_entrega)
  where entregue_em is null and status in ('confirmada','cobrada');

-- ============================================================
-- ROLLBACK:
--   drop index if exists ix_cesta_pendente_entrega;
--   alter table cesta_semana drop column if exists entregue_em;
-- ============================================================
