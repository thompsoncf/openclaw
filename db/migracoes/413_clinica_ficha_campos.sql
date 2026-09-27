-- 413_clinica_ficha_campos.sql
-- Clínica: a ficha do paciente com os campos que a clínica usava no Amigo, o check-in
-- no balcão e as etiquetas (pedido do dono em 27/09/2026, depois da comparação com o
-- cadastro de paciente do Amigo). finance/clinica_pacientes.py, finance/clinica_ficha_link.py.
--
--   * CAMPOS: nome social, sexo, profissão, RG, nome da mãe, contato de emergência e o
--     endereço separado (número, complemento, bairro — o que a nota da prefeitura pede).
--     Ficam na RELAÇÃO com a clínica (`clientes`), não na identidade global (`pessoas`).
--     Alergia e tipo sanguíneo NÃO entram aqui: são do prontuário (a recepção vê só o
--     aviso de alergia da pré-consulta). Raça/etnia, convênio, CNS e TISS ficam de fora
--     (dado sensível sem uso; a clínica é só particular).
--   * CHECK-IN NO BALCÃO: a recepção gera um código de uso único que vale 15 minutos;
--     o paciente abre a ficha dele no tablet da clínica (sem login) ou no celular.
--   * ETIQUETAS: marcadores livres da clínica (VIP, pós-operatório…), com filtro na lista.
--
-- Aditiva e idempotente.

alter table public.clientes
  add column if not exists nome_social text,
  add column if not exists sexo text,
  add column if not exists profissao text,
  add column if not exists rg text,
  add column if not exists nome_mae text,
  add column if not exists numero text,
  add column if not exists complemento text,
  add column if not exists bairro text,
  add column if not exists contato_emergencia text,
  add column if not exists fone_emergencia text,
  add column if not exists etiquetas text[] not null default '{}',
  add column if not exists ficha_balcao_codigo text,
  add column if not exists ficha_balcao_ate timestamptz;

do $$ begin
  alter table public.clientes add constraint clientes_sexo_check
    check (sexo is null or sexo in ('f','m','outro'));
exception when duplicate_object then null; end $$;

-- rollback:
--   alter table public.clientes drop constraint if exists clientes_sexo_check;
--   alter table public.clientes drop column if exists ficha_balcao_ate, drop column if exists ficha_balcao_codigo,
--     drop column if exists etiquetas, drop column if exists fone_emergencia, drop column if exists contato_emergencia,
--     drop column if exists bairro, drop column if exists complemento, drop column if exists numero,
--     drop column if exists nome_mae, drop column if exists rg, drop column if exists profissao,
--     drop column if exists sexo, drop column if exists nome_social;
