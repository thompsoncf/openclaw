-- 485_novidade_titulo_vencimento_e_referencia.sql
-- O aviso de "mudar o vencimento e anotar o mês de referência" nas contas a
-- pagar, seguindo a seção 5 do CLAUDE.md. Pedido do dono em 02/10/2026. Precisa
-- da 484.
--
-- O QUE MUDOU NA TELA. Em Empresa › Contas a pagar, o "Editar" da conta aberta
-- ganhou a data do vencimento e o mês de referência; o formulário de nova conta
-- ganhou o mês de referência; a linha mostra "ref. MM/AAAA"; e o relatório de
-- Contas a pagar ganhou a coluna "Ref.". A referência é só informação (decisão do
-- dono): o DRE continua pela data do pagamento. Em branco, vale o mês anterior
-- ao vencimento.
--
-- O PORTÃO: `empresa` (o módulo PJ — Contas a pagar mora na aba Empresa). PRA
-- QUEM: dono e gestor; o vendedor não tem a aba Empresa.
--
-- QUEM RECEBE, conferido na produção em 02/10/2026 (só leitura, módulo PJ ativo
-- — mesma lista da 483):
--   3 Thompson · 7 João Pedro · 9 Zé do Arroz · 16 Danilo · 21 Maylson ·
--   23 Rawilson · 26 Katheley · 30 Paulo · 31 Juliana · 33 Pablo · 34 Manoel
--   (Prime) · 35 Louana · 37 Liberal · 39 Espaço Pelle Clínica Dermatológica ·
--   40 M.R. Rocha Assessoria de Imprensa
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('contas-pagar-vencimento-e-referencia', 'novidade', 'empresa', '{dono,gestor}',
 'Contas a pagar: mude o vencimento e anote o mês de referência',
 'Errou a data ou o boleto mudou? Agora dá pra mudar o vencimento de uma conta a pagar depois de lançada — e anotar a que mês ela se refere.',
 '/painel/empresa',
 $txt$Duas coisas novas nas contas a pagar.

MUDAR O VENCIMENTO

Em Empresa › Contas a pagar, abra o "Editar" da conta: a data do vencimento agora está ali. Se a conta repete, a próxima passa a contar da data nova.

O MÊS DE REFERÊNCIA

Ao lançar ou editar uma conta a pagar, preencha o "Mês de referência" — por exemplo, a conta de luz que vence em novembro é a de outubro. Ele aparece na lista ("ref. 10/2026") e na coluna "Ref." do relatório de Contas a pagar.

Em branco, vale o mês anterior ao vencimento. Se a conta repete, a referência anda junto com ela. É só informação: o resultado do mês (DRE) continua pela data em que a conta foi paga.$txt$,
 timestamptz '2026-10-02 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'contas-pagar-vencimento-e-referencia';
