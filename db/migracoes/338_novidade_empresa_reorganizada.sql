-- 338_novidade_empresa_reorganizada.sql
-- O aviso da aba Empresa reorganizada, seguindo a seção 5 do CLAUDE.md. Pedido
-- do dono em 24/09/2026 ("vamos dar uma atenção pro layout da aba empresas"),
-- mockup aprovado em docs/mockups/empresa_layout.html com as respostas:
-- duas colunas "se couber com o ajuste bom", formulário fechado no celular,
-- os botões de cada conta refeitos ("melhore") e configurações no fim.
--
-- O PORTÃO É NOVO E É `empresa`, um portão de CONTA (como `canal_proprio`), não
-- de nicho. A aba é igual pros três perfis — eventos, recorrente e produto —,
-- mas só existe pra quem tem o módulo PJ; 'todos' avisaria de uma tela que parte
-- da base nem tem. O portão chama o mesmo `empresa.modulo_pj_ativo` da rota.
--
-- QUEM RECEBE, conferido na produção em 24/09/2026 (módulo PJ ativo):
--   3 Thompson Cavalcante Fernandes · 7 Joao Pedro M. de S. Barbosa ·
--   9 ze do arroz · 16 Danilo · 21 Maylson.ofc · 23 Rawilson Osternes ·
--   26 Katheley Martins · 30 Paulo Costa · 31 Juliana T. de Oliveira ·
--   33 Pablo Thyago G. Dias · 34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa ·
--   37 Liberal Neto · 39 Espaço Pelle Clínica Dermatológica
--   (7, 26 e 31 ainda não preencheram CNPJ e razão: veem a aba quando preencherem)
--
-- PRA QUEM: dono e gestor, que são quem alcança /painel/empresa. O vendedor não
-- tem a aba.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('empresa-reorganizada', 'novidade', 'empresa', '{dono,gestor}',
 'A aba Empresa mudou de ordem: as contas vêm primeiro',
 'A aba Empresa abre com um resumo do dia — a pagar, a receber, caixa da semana e resultado do mês — e as contas a pagar e receber vêm logo depois, com um formulário mais curto.',
 '/painel/empresa',
 $txt$A aba Empresa foi reorganizada pra que o que você usa todo dia apareça primeiro. Nada saiu: mudou a ordem, a largura e o tamanho de cada parte.

UM RESUMO NO TOPO

Quatro números: quanto tem a pagar (e quanto já atrasou), quanto tem a receber, quanto falta ou sobra no caixa da semana e o resultado do mês. Embaixo deles, as pendências — contas esperando você liberar, lançamentos sem conta contábil, despesas sem tipo. Cada um leva direto pra onde se resolve.

Logo abaixo, uma barra com as seções (Contas, Caixa, Resultado, Clientes, Equipe, Configurações) acompanha a rolagem.

AS CONTAS PRIMEIRO

No computador a aba usa a tela toda: as contas ficam à esquerda, e o caixa da semana e os maiores clientes à direita. Em tela menor, fica tudo numa coluna só.

O formulário de nova conta tem três linhas, na ordem em que se preenche: o que é a conta, de quem é e como classificar. O botão de adicionar é o último. No celular ele fica fechado atrás de "+ Nova conta".

CADA CONTA MOSTRA O PRÓXIMO PASSO DELA

Em vez de seis botões em toda conta, cada uma mostra só o que dá pra fazer com ela agora: liberar ou recusar, dar baixa, já foi paga. Editar, repetir e apagar ficam no ▾ ao lado, que abre embaixo da conta. Liberar várias de uma vez agora fica no cabeçalho de "Esperando liberação".

CONFIGURAÇÕES NO FIM

Plano de contas, centros de custo e avisos do sistema ficaram juntos no fim da página. Lançamentos a classificar ficaram ao lado do resultado do mês, que é onde eles fazem falta.$txt$,
 timestamptz '2026-09-24 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'empresa-reorganizada';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos'));
