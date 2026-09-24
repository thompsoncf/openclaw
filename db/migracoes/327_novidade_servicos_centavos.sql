-- 327_novidade_servicos_centavos.sql
-- A tela de Serviços passou a escrever dinheiro com R$ e centavos, e as pílulas
-- ficaram do mesmo tamanho.
--
-- O QUE MUDOU NA TELA (os dois nichos, pedido do dono em 24/09/2026):
--   * Todo campo de dinheiro da proposta mostra "R$" e os centavos (R$ 1.500,00)
--     e ACEITA centavos: R$ 1.397,50 fica R$ 1.397,50. Antes o valor era
--     arredondado pro real inteiro ao salvar, e o catálogo mostrava arredondado.
--   * O desconto em R$ ganhou ponto de milhar e centavos (antes "1200").
--   * O alternador % | R$ foi pra dentro do campo, pequeno; a lixeira virou um
--     ícone discreto; todas as pílulas têm a mesma altura; o "pagamento anual à
--     vista" virou um interruptor.
--   * RECORRENTE: o número grande do resumo passou a ser o INVESTIMENTO MENSAL
--     (ou o anual à vista, quando ligado); o desconto dos serviços aparece POR MÊS;
--     implantação sem valor diz "sem taxa"; o "Total 1º ano" virou uma linha.
--     No EVENTO o número grande continua sendo o total ("só valor mesmo", dono).
--
-- POR QUE. O dono mandou o print da tela e pediu "ajustar as casas decimais pra
-- pessoas verem valores em reais corretos e as pílulas". Medido na proposta da
-- HLED (ZAQ): "Descontos por item R$ 32.400" era o desconto do ANO (R$ 2.700 ×
-- 12), e parecia um desconto de 32 mil numa proposta de R$ 3.000/mês. Mockup
-- aprovado: docs/mockups/zaq_servicos_valores_pilulas.html.
--
-- É 'mudanca': o número grande do recorrente mudou de lugar.
--
-- PRA QUEM: dono, gestor e vendedor — é a tela de quem monta proposta.
--
-- O PORTÃO: `servico` — a tela é a mesma nos dois modos.
--
-- CONTAS ALCANÇADAS (contas × nichos, leitura em produção, as mesmas da 313):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS · 34 PRIME EVENTOS ·
--   35 DOCE MELL · 37 LIBERAL NETO CORRETAGEM DE SEGUROS ·
--   39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-centavos-e-pilulas', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'Valores com centavos na montagem da proposta',
 'Na montagem da proposta, os valores passaram a aparecer em reais com centavos — e a aceitar centavos —, com os botões do mesmo tamanho e o resumo mais claro.',
 '/painel/servicos',
 $txt$A tela onde você monta a proposta ficou mais fácil de ler.

O QUE MUDOU

• Todo valor aparece em reais com centavos: R$ 1.500,00. E agora dá para digitar centavos — R$ 1.397,50 fica R$ 1.397,50. Antes o sistema arredondava para o real inteiro.
• O desconto em R$ ganhou ponto de milhar e centavos (antes aparecia "1200").
• Os botões ficaram do mesmo tamanho. O % | R$ do desconto foi para dentro do campo, a lixeira ficou discreta e o pagamento anual à vista virou um interruptor.

PARA QUEM VENDE MENSALIDADE

O número grande do resumo agora é o Investimento mensal — o que o cliente paga por mês. O desconto dos serviços aparece por mês, a implantação sem valor diz "sem taxa" e o total do 1º ano fica logo abaixo. Com o anual à vista ligado, o número grande mostra o valor do ano.

O QUE NÃO MUDA

As propostas já salvas continuam com os mesmos valores. Quem vende evento continua vendo o total como número principal.$txt$,
 timestamptz '2026-09-24 14:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-centavos-e-pilulas';
