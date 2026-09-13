-- 251_novidade_nicho_suplementos.sql
-- O aviso do nicho de loja de suplemento, seguindo a seção 5 do CLAUDE.md: PR que
-- muda tela leva o aviso, no mesmo PR. Precisa da 250 (o nicho que está sendo
-- anunciado) e da 199 (pra_quem, resumo, link).
--
-- O PORTÃO É NOVO E É `suplementos`, o segundo portão de um nicho só (o primeiro
-- foi `seguros`, na 243). Nenhum dos que existiam descrevia o alcance: 'produto'
-- pegaria o hortifrúti do Zé do Arroz (conta 9) e a mercearia; 'servico' pegaria
-- advocacia e contabilidade; 'recorrente' pegaria as quatro consultorias. Nenhuma
-- dessas contas ganha tela nenhuma com este PR. O portão mora em
-- finance/novidades._suplementos, e o comentário dele explica por quê.
--
-- QUEM RECEBE, conferido na produção em 13/09/2026:
--   conta 16 · Danilo / SUPER FIT · Teresina-PI · trial · 1 membro
--              (é a primeira e, hoje, a única loja de suplemento da base)
--
-- E ninguém mais — de propósito. O nicho novo só existe pra quem for marcado com
-- ele, e marcar a conta 16 é passo separado, na tela Empresa.
--
-- PRA QUEM: dono e gestor. O vendedor fica de fora porque quem escolhe o nicho e
-- configura a empresa é quem alcança /painel/empresa, e ele não alcança.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('nicho-loja-de-suplementos', 'novidade', 'suplementos', '{dono,gestor}',
 'O Zaq agora fala a língua de loja de suplemento',
 'Loja de suplemento com cozinha virou um ramo do sistema: o cadastro passa a falar em pote, whey, creatina e marmita, em vez de prato e porção.',
 '/painel/empresa',
 $txt$Até hoje quem vendia suplemento caía no ramo "Alimentação / Lanche" — o de lanchonete. Na hora de cadastrar um pote de whey, a tela oferecia prato, porção e combo, e sugeria as categorias salgado, doce e bebida. Nenhuma delas serve, e o resultado a gente viu no catálogo: produto sem categoria nenhuma, e whey de morango classificado como fruta.

Agora existe o ramo "Suplementos / Nutrição esportiva", e três coisas mudam de lugar.

O QUE VOCÊ VENDE é medido em pote (o padrão), unidade, caixa, frasco, sachê, kit, marmita ou quilo. Pote porque é a unidade de quase tudo — 900g, 300g, 220g; frasco pras cápsulas; sachê pra dose avulsa de pré-treino.

AS CATEGORIAS SÃO AS DA SUA PRATELEIRA: whey, proteína, creatina, pré-treino, aminoácido, cafeína, termogênico, vitamina, colágeno, barra e snack, bebida, marmita, insumo e acessório. Cinco delas saíram das palavras que você já estava digitando na mão. É isso que deixa o relatório responder "quanto vendi de whey este mês" — com categoria livre, essa conta nunca fecha.

E A COZINHA ENTROU JUNTO. Este ramo entende que você não só revende: você TRANSFORMA. O frango e a batata-doce que você compra não vão pra prateleira, viram prato — e agora têm categoria própria de despesa ("Insumos" e "Embalagens", que também nasceram neste pacote). É essa separação que faz o custo da comida parar de se misturar com compra de mercado, e sem ela não existe CMV.

O assistente também aprendeu o ramo: ele sabe que produto é marca + sabor + gramatura (e pergunta o sabor antes de baixar estoque do item errado), que o distribuidor é fornecedor, que marmita fresca tem validade curta e estoque do dia — e que ele NÃO dá conselho de saúde: dose e tratamento são com nutricionista ou médico.

O que ainda NÃO existe, e vem depois: a ficha técnica do prato (quanto de cada insumo, e quanto custa cada marmita), o registro de produção do dia e o plano de marmitas como assinatura.$txt$,
 timestamptz '2026-09-13 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'nicho-loja-de-suplementos';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros'));
