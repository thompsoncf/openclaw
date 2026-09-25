-- 340_novidade_funil_topo.sql
-- O aviso do funil enxuto, parte 2 (o topo), seguindo a seção 5 do CLAUDE.md.
-- Pedido do dono em 24/09/2026, mockup aprovado em
-- docs/mockups/prospeccao_layout.html; parte 1 (o cartão e as colunas) na 339.
--
-- DOIS AVISOS, porque são dois alcances (§6: vocabulário de um nicho não vaza pro
-- outro):
--
--   * `funil-topo` — público `servico` (todo mundo que tem funil). Busca, gavetas
--     de Captar e Etapas, vendedor com contagem, críticos por vendedor, mês quase
--     vazio e as abas do celular. O texto não fala de festa.
--     QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, contas ×
--     nichos passadas pelo `vende_servico`):
--       3 Thompson Cavalcante Fernandes · 16 Danilo · 21 Maylson.ofc ·
--       23 Rawilson Osternes · 30 Paulo Costa · 33 Pablo Thyago G. Dias ·
--       34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa · 37 Liberal Neto ·
--       39 Espaço Pelle Clínica Dermatológica
--
--   * `funil-regua-festa` — público `eventos`. A régua "Festa em" só existe em
--     quem vende data.
--     QUEM RECEBE: 34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa
--
-- PRA QUEM: dono, gestor e vendedor nos dois — a busca, as gavetas, a régua e as
-- abas do celular mudam a rotina de todo mundo que usa o quadro. Os críticos por
-- vendedor são só de dono e gestor, e o texto diz isso.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-topo', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Funil: busca por nome ou telefone, e o topo numa linha só',
 'O funil ganhou busca por nome ou telefone, e o topo ficou mais curto: o primeiro cartão aparece mais alto na tela, no computador e no celular.',
 '/painel/prospeccao',
 $txt$O topo do funil foi reorganizado pra o quadro começar mais alto na tela.

BUSCA

O campo "Buscar nome ou telefone" fica no alto do funil. Digitando, os cartões da tela vão sendo filtrados na hora. Aperte Enter pra procurar em todos os meses, não só no que está aberto. No computador, a tecla / leva direto pra busca.

CAPTAR E ETAPAS ABREM DO LADO

"+ Captar lead" e "⚙ Etapas" ficam na linha do título e abrem numa gaveta à direita, por cima do quadro, sem empurrar os cartões pra baixo. Pra fechar, clique fora ou aperte Esc.

VENDEDOR COM A CONTAGEM

O filtro de vendedor mostra quantos cartões cada um tem no quadro, por exemplo "Jacqueline · 48".

CRÍTICOS POR VENDEDOR (DONO E GESTOR)

Em quem usa a tela de Follow-up, uma linha em cima do quadro mostra quantos leads críticos cada vendedor tem. Clique no nome pra ver só os cartões dele. "ver fila" leva pro Follow-up.

MÊS QUASE VAZIO

Se o mês aberto tem pouca coisa e o último lote entrou em outro mês, o quadro avisa e leva pra lá com um clique.

NO CELULAR

As abas de etapa ficam presas no topo quando você rola e mostram em verde quantos clientes esperam resposta em cada uma (●7). Busca, etapas e captar viraram os ícones 🔍 ⚙ + ao lado do título.$txt$,
 timestamptz '2026-09-25 01:30:00+00'),
('funil-regua-festa', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Funil: a data da festa virou uma régua de meses',
 'No funil, o filtro pela data da festa virou uma régua com uma barra por mês: dá pra ver de uma vez em que meses estão as festas, sem rolar.',
 '/painel/prospeccao',
 $txt$A linha "Festa em", em cima do funil, agora é uma régua: uma barra por mês, com o número de festas em cima e o mês embaixo. Quando vira o ano aparece uma divisa (2027, 2028). A barra de "Sem data", na ponta, mostra quantos leads ainda não disseram quando é a festa.

Antes eram pílulas numa faixa que rolava de lado, e os meses do fim do ano que vem só apareciam arrastando.

Clicar num mês continua fazendo o que fazia: o quadro inteiro passa a mostrar só as festas daquele mês, em todas as etapas.$txt$,
 timestamptz '2026-09-25 01:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('funil-topo', 'funil-regua-festa');
