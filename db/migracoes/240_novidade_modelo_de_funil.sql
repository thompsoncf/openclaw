-- 240_novidade_modelo_de_funil.sql
-- O aviso do modelo de funil por ramo (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): o modelo existe pros três perfis, e cada um recebe o dele —
-- eventos ganha "Agendado Visita", serviço recorrente ganha "Reunião marcada", e
-- produto continua com o funil genérico. O texto abaixo diz "do seu ramo" e não
-- nomeia festa: quem lê vê o nome do próprio ramo na tela, não o de outro.
--
-- PRA QUEM: dono e gestor. Quem adota o modelo é quem configura a empresa. O
-- vendedor vê o quadro mudar, mas só DEPOIS de o dono marcar as caixas — avisá-lo
-- antes seria avisar de uma mudança que talvez nunca aconteça na conta dele.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('modelo-de-funil-do-ramo', 'novidade', 'todos', '{dono,gestor}',
 'O funil agora nasce com as colunas do seu ramo — e quem já existe pode adotá-las',
 'Cada ramo tem um desenho de funil próprio. Conta nova já nasce com ele, e quem já tem funil vê na Régua o que mudaria e marca o que quiser adotar.',
 '/painel/prospeccao/regua',
 $txt$Até agora toda conta nascia com as mesmas seis colunas: Novo, Contatado, Qualificado, Proposta, Ganho e Perdido. Servisse ou não ao que a empresa vende.

Agora o desenho é do ramo. Quem trabalha com eventos nasce com Novo · Contatado · Follow-up · Agendado Visita · Proposta · Perdido, e o fechamento fora do quadro, indo direto pra Agenda. Quem vende serviço por mensalidade nasce com o mesmo desenho, mas marcando reunião em vez de visita — e sem a ponte com a Agenda, que só faz sentido pra quem tem data de evento no cadastro.

Pra quem já tem funil montado, nada mudou sozinho. Na Régua apareceu o bloco "O modelo do seu ramo": ele mostra o desenho do ramo, lista item por item o que mudaria no seu funil e deixa você marcar só o que quiser. O que já bate não aparece.

Três coisas que esse bloco nunca faz:

Não apaga etapa. Uma coluna sua que não existe no modelo é proposta pra sair do quadro, e só. Os leads dela continuam no cadastro, na busca, nos relatórios e na ficha — some a coluna, não o cliente.

Não move lead. Adotar o modelo mexe na tela, não no funil de ninguém. Nenhum lead troca de etapa, e o histórico continua contando só os movimentos que alguém realmente fez.

Não reescreve o nome que você deu. Se você já renomeou uma etapa, a proposta de trocar esse nome vem desmarcada, com um aviso. O padrão do ramo é sugestão, não correção.

Se o bloco disser que seu funil já está igual ao modelo, não há nada a fazer — é só a confirmação de que o desenho já é o do seu ramo.$txt$,
 timestamptz '2026-09-11 23:40:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'modelo-de-funil-do-ramo';
