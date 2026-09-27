-- 410_novidade_teste_resgate_chip.sql
-- O aviso da migração 409 (o teste do resgate no chip certo), seguindo a seção 5 do
-- CLAUDE.md.
--
-- PÚBLICO `resgate_ativo` (portão novo de CONTA: o resgate da IA em Ensaio OU
-- Ligado — `resgate.config`, o mesmo do botão "Testar comigo"). O `resgate_ligado`
-- da 397 deixaria de fora justamente a Prime, que testa em Ensaio.
-- PRA QUEM: dono e gestor — o supervisor do resgate e quem mexe na regra por número.
-- O vendedor não testa nem configura nada disso.
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): a Prime (34), a
-- única com o resgate fora do 'off' (em Ensaio).
--
-- Aditiva e idempotente.

alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia',
                     'resgate_ligado','resgate_eventos','esteira_ligada',
                     'resgate_ativo'));

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('teste-resgate-no-chip-certo', 'mudanca', 'resgate_ativo', '{dono,gestor}',
 'O teste do Resgate fala pelo chip certo',
 'O "Testar comigo" do Resgate da IA passa a falar pelo número onde a conversa do cliente está, com 🧪 em cada mensagem, e o supervisor pode testar a IA de outro número de verdade.',
 '/painel/prospeccao/comunicacao',
 $txt$O "Testar comigo" do Resgate da IA mudou:

- O teste fala pelo número onde a conversa do cliente está, o mesmo por onde a retomada de verdade sairia. Toda mensagem do teste leva o 🧪.
- O seu número só é o "cliente do teste" no número do teste. Mandou pra outro número da empresa? Ele atende você como atende qualquer contato, com a IA daquele número. É assim que se testa a IA de outro número de verdade.
- O cartão do Resgate mostra o teste aberto: quem você está fingindo ser, por qual número e até quando. Tem os botões "Ver a conversa" e "Encerrar teste".
- O lead com o número do supervisor ganha o selo 🧪 no funil e na ficha, e não conta no Desafio nem no Raio-X.
- Na regra por número, um aviso mostra quando quem é chamado pela IA (agenda, desconto, conferência) é a própria IA, que não tem celular. A IA não aparece mais nessas listas.$txt$,
 timestamptz '2026-09-27 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'teste-resgate-no-chip-certo';
--   (e o check volta ao da 402)
