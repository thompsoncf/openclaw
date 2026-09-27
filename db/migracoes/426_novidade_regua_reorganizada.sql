-- 426_novidade_regua_reorganizada.sql
-- O aviso da Régua do funil reorganizada (mockup docs/mockups/regua_funil_reorganizada.html,
-- aprovado em 27/09/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `servico`: a Régua configura o funil, e o funil é de quem vende serviço (§6:
-- produto não tem funil). O que é só de festa (as rotinas) continua atrás do portão do
-- nicho dentro da tela. PRA QUEM: dono e gestor — a Régua é configuração da empresa
-- e o vendedor não a abre.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('regua-reorganizada', 'novidade', 'servico', '{dono,gestor}',
 'A Régua do funil, mais curta e organizada por automação',
 'A tela que configura o funil ficou mais curta: cada etapa numa linha, cada automação num cartão com os números dela, e um resumo no topo do que está agindo agora.',
 '/painel/prospeccao/regua',
 $txt$A Régua do funil (Funil › Régua) mudou de forma. Nada do que você configurou mudou de valor.

- O QUE ESTÁ AGINDO AGORA: no topo, um resumo das automações. Verde é ligado, azul é observando.
- AS ETAPAS: cada uma numa linha, com o nome, quando entra sozinha, o teto e quantos leads estão nela. O resto (tentativas, motivo, Agenda, reativação, pra onde a mão leva) fica em "mais regras". Regra em uso aparece como selo na linha, e o Perdido e o Fechado agora mostram as deles. As etapas fora do quadro ficam recolhidas no fim.
- AS AUTOMAÇÕES: um cartão por motor, com a chave e os números dele juntos. O Espelho do vendedor foi pra dentro do cartão da Esteira. Os números de um motor desligado ficam recolhidos, a um clique.
- A ORDEM: resumo, etapas, automações, rotinas de festa (pra quem vende festa) e os motivos de perda.

E um conserto: renomear o Perdido ou salvar o Fechado não desliga mais as regras que a tela não mostrava.$txt$,
 timestamptz '2026-09-28 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'regua-reorganizada';
