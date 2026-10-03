-- 202610031503_novidade_clinica_cartao.sql
-- O QUE FAZ: o aviso do cartão de clínica (entrega 3a), seguindo a seção 5 do
-- CLAUDE.md.
-- POR QUÊ: desenho "Cartão de clínica", aprovado pelo dono em 03/10/2026.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção é quem preenche
-- o cartão). QUEM RECEBE, conferido na produção em 03/10/2026 (só leitura, nicho
-- clinica): 39 Espaço Pelle Clínica Dermatologica Ltda, a única conta do nicho.
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-cartao-do-paciente', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'O cartão do paciente: cidade, tipo de atendimento e como chegou',
 'Na clínica, o cartão do funil passa a guardar a cidade, o tipo de atendimento, como a pessoa chegou e quem fala por ela quando o paciente é outra pessoa.',
 '/painel/prospeccao',
 $txt$O cartão de cada paciente no funil ganhou os campos da clínica. Abra um cartão e clique em editar.

O QUE MUDOU
- O paciente é quem está falando ou outra pessoa. Quando é outra pessoa (a mãe que marca para o filho), o cartão guarda o nome do paciente, o nascimento e quem fala pelo WhatsApp, com o parentesco.
- A cidade sai de uma lista: a sede e as cidades de viagem cadastradas em Clínica › Locais. "Outra" aceita o nome escrito.
- Tipo de atendimento: consulta, procedimento, serviço (vacina, teste, coleta) ou outro.
- Como chegou: Instagram, Google, Rádio, Indicação, Já é paciente, WhatsApp direto, Telefone ou Balcão.
- Saíram da ficha da clínica os campos de festa e de empresa (tipo de festa, convidados, CNPJ, sócio, porte).

PARA SAIR DE EM CONVERSA
Nos cartões em Novo e Em conversa aparece o que falta: cidade, tipo de atendimento, como chegou, se o preço já foi passado e a próxima ação. É um aviso: o cartão anda mesmo assim.

O que vem a seguir: o balão do quadro mostrando esses campos, a agenda e o anúncio preenchendo sozinhos, e o "+ Novo contato" curto para quem chega por telefone ou balcão.$txt$,
 timestamptz '2026-10-03 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-cartao-do-paciente';
