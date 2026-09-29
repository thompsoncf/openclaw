-- 449_novidade_dre_pdf.sql
-- O aviso do "baixar PDF" do DRE, seguindo a seção 5 do CLAUDE.md.
--
-- Pedido chegou de fora: a Iris (cliente da conta 34, Manoel Soares/Prime)
-- perguntou pelo WhatsApp, olhando a tela DRE do mês, "No caso da DRE pode
-- configurar pra imprimir em PDF na mesma estrutura?" — o dono repassou em
-- 29/09/2026 pedindo pra pensar e, com o protótipo aprovado, pra implementar.
--
-- O QUE MUDOU NA TELA. Ao lado de "baixar planilha" (relatório do contador),
-- o card do DRE (`empresa#dre`) ganhou "baixar PDF": mesma estrutura da tela
-- (grupo por grupo do plano de contas, com os subtotais Receita Líquida e
-- Lucro Bruto e o total Resultado do Mês), num PDF pronto pra imprimir ou
-- guardar — reaproveita o motor de PDF que já existe pra clínica
-- (finance/clinica_documentos.render_pdf).
--
-- O PORTÃO: `empresa` (mesmo de 338_novidade_empresa_reorganizada.sql e
-- 448_novidade_baixa_parcial_pagar.sql) — é quem tem o módulo PJ e vê o DRE.
--
-- PRA QUEM: dono e gestor. O vendedor não tem a aba Empresa.
--
-- QUEM RECEBE, conferido na produção em 29/09/2026 (só leitura, módulo PJ
-- ativo — mesma consulta da 338/448):
--   3 Thompson · 7 João Pedro · 9 Zé do Arroz · 16 Danilo · 21 Maylson ·
--   23 Rawilson · 26 Katheley · 30 Paulo · 31 Juliana · 33 Pablo · 34 Manoel
--   (Prime) · 35 Louana · 37 Liberal · 39 Espaço Pelle Clínica Dermatológica ·
--   40 M.R. Rocha Assessoria de Imprensa
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('empresa-dre-pdf', 'novidade', 'empresa', '{dono,gestor}',
 'O DRE do mês agora sai em PDF',
 'O card do DRE ganhou "baixar PDF", do lado de "baixar planilha" — mesma estrutura da tela, pronta pra imprimir ou guardar.',
 '/painel/empresa',
 $txt$Antes, o DRE do mês só saía como planilha (CSV) pro contador.

AGORA tem também "baixar PDF", no mesmo lugar: um documento com a mesma estrutura da tela — cada grupo do plano de contas, os subtotais (Receita Líquida, Lucro Bruto) e o Resultado do Mês — pronto pra imprimir ou guardar.$txt$,
 timestamptz '2026-09-29 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'empresa-dre-pdf';
