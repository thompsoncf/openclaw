-- 172_chip_rotulo.sql
-- O apelido do chip PRINCIPAL.
--
-- A migração 171 deu à empresa a possibilidade de ter mais de um chip. O apelido de
-- um chip secundário mora em `contas.nome` da linha dele — a linha existe só pra
-- isso, então o campo está livre. O chip PRINCIPAL não tem linha própria: ele É a
-- empresa, e `contas.nome` ali é o nome de quem abriu a conta.
--
-- POR QUE NÃO REUSAR `contas.nome` NO PRINCIPAL
--
-- Porque esse campo já trabalha em cinco lugares: contrato, cobrança, assinatura de
-- e-mail, convite de reunião e relatório. Gravar "Agência Alfa" ali arrumaria a
-- etiqueta do inbox e estragaria os outros cinco de uma vez — o convite passaria a
-- dizer "A Agência Alfa quer marcar com você".
--
-- Então o apelido do principal fica junto do CANAL, que é do que ele fala. Uma
-- coluna nova em `canais_config`, nula, sem default.
--
-- Nulo = sem apelido. A tela mostra "Chip 1" até alguém batizar, e o relatório
-- agrupa por chip do mesmo jeito — o rótulo é enfeite de leitura, não chave.
--
-- Aditivo e idempotente.

alter table public.canais_config
  add column if not exists rotulo text;

comment on column public.canais_config.rotulo is
  'Apelido do chip principal (ex.: "Agência Alfa"), mostrado no inbox e agrupado no '
  'relatório. Nulo = sem apelido. O apelido dos chips secundários mora em '
  'contas.nome da linha do chip; aqui é só o principal, que não tem linha própria.';

-- rollback:
--   alter table public.canais_config drop column if exists rotulo;
