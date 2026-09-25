-- 343_novidade_funil_cabecalho.sql
-- O aviso do cabeçalho do funil, seguindo a seção 5 do CLAUDE.md. Mockup aprovado
-- pelo dono em 25/09/2026 (docs/mockups/funil_cabecalho.html) com as respostas:
-- "1 - A" (o número da conversa no 💬 de cada card), "b - sim" (as abas leves em
-- todas as telas da Prospecção) e "faltou o nome do vendedor no card, senão toda
-- vez tenho que clicar" — que entrou junto.
--
-- PÚBLICO: `servico` — o funil é de quem vende serviço; produto não tem funil. O
-- texto não fala de festa. A parte do número no 💬 só existe em conta com dois
-- números de WhatsApp, e o texto diz isso (mesma escolha do aviso da 341).
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor vê as abas novas e, em conta com
-- dois números, o número no 💬; o nome do vendedor no card, os críticos em chips e
-- o aviso do modelo são de dono e gestor, e o texto diz isso.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, contas × nichos
-- pelo `vende_servico`):
--   3 Thompson Cavalcante Fernandes · 16 Danilo · 21 Maylson.ofc ·
--   23 Rawilson Osternes · 30 Paulo Costa · 33 Pablo Thyago G. Dias ·
--   34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa · 37 Liberal Neto ·
--   39 Espaço Pelle Clínica Dermatológica
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-cabecalho', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Funil: o nome do vendedor no cartão e um topo mais leve',
 'No funil, cada cartão passou a mostrar o nome do vendedor responsável e, em quem tem dois números de WhatsApp, por qual número é a conversa; o topo da tela ficou mais leve.',
 '/painel/prospeccao',
 $txt$O topo do funil ficou mais leve, e o cartão diz mais sem precisar abrir nada.

O NOME DO VENDEDOR NO CARTÃO (DONO E GESTOR)

No lugar das duas letras, o cartão mostra o primeiro nome do responsável: "Jacqueline", "Thiago". Clique no nome pra trocar o responsável, como antes. Dois vendedores com o mesmo primeiro nome aparecem com a inicial do sobrenome ("Pedro Y.").

POR QUAL NÚMERO É A CONVERSA

Em quem tem dois números de WhatsApp, o botão 💬 do cartão diz o número da conversa: o principal em cinza, o outro número em azul.

ABAS MAIS LEVES

As abas da Prospecção (Funil, Follow-up, Comunicação, Base e as outras) perderam o contorno. A aba aberta fica com um sublinhado verde.

O TOPO DO FUNIL

O nome da empresa e os números ficam numa linha embaixo do título, sem cortar. Pra dono e gestor, os críticos de cada vendedor aparecem em etiquetas, e o aviso do modelo do seu ramo virou um selo pequeno nessa mesma linha, que leva pra Régua. O período ("Entraram em") virou uma escolha só: o mês atual, os anteriores ou Tudo.$txt$,
 timestamptz '2026-09-25 13:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-cabecalho';
