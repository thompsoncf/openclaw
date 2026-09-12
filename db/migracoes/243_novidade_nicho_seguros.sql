-- 243_novidade_nicho_seguros.sql
-- O aviso do nicho de corretora, seguindo a seção 5 do CLAUDE.md: PR que muda
-- tela leva o aviso, no mesmo PR. Precisa da 242 (o nicho que está sendo
-- anunciado) e da 199 (pra_quem, resumo, link).
--
-- O PORTÃO É NOVO E É `seguros`. Nenhum dos que existiam descrevia o alcance:
-- 'servico' pegaria advocacia, contabilidade e agência, que não têm apólice nem
-- seguradora; 'todos' pegaria a Prime Eventos. Pelo passo 1 da regra, o portão
-- nasce no código primeiro — está em finance/novidades._seguros, e é o único
-- portão de um nicho só (o comentário dele explica por que isso é aceitável).
--
-- QUEM RECEBE, conferido na produção em 12/09/2026:
--   conta 37 · Liberal Neto / Liberal Seguros · Teresina-PI · trial · 1 membro
--              (é a primeira e, hoje, a única corretora da base)
--
-- E ninguém mais — de propósito. As outras contas não ganham tela nenhuma com
-- este PR: o nicho novo só existe pra quem for marcado com ele.
--
-- PRA QUEM: dono e gestor. O vendedor fica de fora porque quem escolhe o nicho e
-- configura a empresa é quem alcança /painel/empresa, e ele não alcança — avisar
-- ele de uma tela que não abre é o erro que a regra 2 da receita nomeia.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('nicho-corretora-seguros', 'novidade', 'seguros', '{dono,gestor}',
 'O Zaq agora fala a língua de corretora de seguros',
 'Corretora de seguros virou um ramo do sistema: o cadastro passa a falar em apólice e ramo, e o financeiro entende que quem paga a comissão é a seguradora.',
 '/painel/empresa',
 $txt$Até hoje o sistema não conhecia corretora. Quem se cadastrava caía no ramo genérico e ia cadastrar apólice medida em quilo, caixa e pacote — o vocabulário de uma mercearia.

Agora existe o ramo "Corretora de Seguros", e três coisas mudam de lugar.

O QUE VOCÊ VENDE é medido em apólice (o padrão), veículo, vida, mensal ou avulso. Frota se cota por veículo e vida em grupo por vida segurada — por isso as duas estão lá separadas.

O RAMO virou a categoria: auto, frota, vida, residencial, empresarial, condomínio, saúde, RC, garantia, viagem. Auto vem primeiro porque é o carro-chefe. É isso que deixa o relatório responder "quanto entrou de auto este ano" — com categoria livre, essa conta nunca fecha.

E O DINHEIRO ENTRA DO LADO CERTO. Numa corretora quem paga é a seguradora, não o segurado: a receita é comissão, um percentual do prêmio. O assistente já sabe disso — ele lança a comissão a receber com a SEGURADORA no lugar de quem deve, e põe o segurado na sua carteira de clientes, que é onde ele importa. Nos outros ramos de serviço é o contrário, e usar a regra deles aqui encheria seu "quem me deve" de gente que não deve nada.

A CATEGORIA DE RECEITA "Comissões" nasceu junto — não existia, e nenhuma das antigas servia: "Vendas" é varejo e "Honorários" é honorário de profissional.

APÓLICE É ANUAL, e o sistema trata assim: a comissão vira uma conta a receber que se renova sozinha daqui a doze meses. É esse título que vai virar o seu lembrete de renovação.

O que ainda NÃO existe, e vem depois: a carteira de apólices propriamente dita — número, seguradora, vigência, endosso — e a tela do que vence em 30, 60 e 90 dias.$txt$,
 timestamptz '2026-09-12 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'nicho-corretora-seguros';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente','canal_proprio'));
