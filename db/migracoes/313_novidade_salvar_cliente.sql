-- 313_novidade_salvar_cliente.sql
-- O card do Cliente, na proposta de Serviços, ganhou o botão "Salvar cliente".
--
-- O QUE MUDOU NA TELA:
--   * Serviços → proposta → card "Cliente": no fim do formulário (o de cadastrar
--     um cliente novo, e o que abre em "Ver dados") agora há "Salvar cliente".
--     Ele grava a proposta com o cliente — mesmo antes de escolher os serviços —
--     e fecha o formulário no cartão do cliente. A proposta fica no funil como
--     rascunho.
--
-- POR QUE. Em 23/09/2026 o dono cadastrou um cliente na proposta da ZAQ (conta 3)
-- e não achou onde salvar: o único jeito era o "Salvar no funil", lá embaixo no
-- Resumo, e o que ele digitou não chegou ao banco.
--
-- PRA QUEM: dono, gestor e vendedor — é quem monta proposta.
--
-- O PORTÃO: `servico` — a tela é a mesma nos dois modos (eventos e recorrente),
-- e o botão entrou nos dois.
--
-- CONTAS ALCANÇADAS (contas × nichos, leitura em produção):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS · 34 PRIME EVENTOS ·
--   35 DOCE MELL · 37 LIBERAL NETO CORRETAGEM DE SEGUROS ·
--   39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-salvar-cliente', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Botão "Salvar cliente" na proposta',
 'Na proposta de serviços, o cadastro do cliente agora tem o próprio botão de salvar: dá pra gravar o cliente antes mesmo de escolher os serviços.',
 '/painel/servicos',
 $txt$O cadastro do cliente na proposta ganhou o próprio botão de salvar.

COMO FUNCIONA

Em Serviços, abra uma proposta (ou clique em "+ Nova proposta"). No card "Cliente", ao cadastrar um cliente novo ou ao clicar em "Ver dados", o formulário agora termina com o botão "Salvar cliente".

Ele grava a proposta com o cliente, mesmo que você ainda não tenha escolhido nenhum serviço. A proposta fica no funil como rascunho, e você pode continuar montando agora ou depois.

O QUE NÃO MUDA

O "Salvar no funil", no Resumo, continua lá e salva tudo junto, como sempre.$txt$,
 timestamptz '2026-09-23 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-salvar-cliente';
