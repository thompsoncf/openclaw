-- 350_novidade_nicho_construcao.sql
-- O aviso do nicho de construção e reforma, seguindo a seção 5 do CLAUDE.md: PR
-- que muda tela leva o aviso, no mesmo PR. Precisa da 348 (o nome novo do nicho),
-- da 349 (as contas do plano que o assistente passa a usar) e da 199 (pra_quem,
-- resumo, link). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html.
--
-- O PORTÃO É NOVO E É `construcao`, o quarto portão de um nicho só (depois de
-- `seguros`, na 243, `suplementos`, na 251, e `clinica`, na 347). A lista do
-- check abaixo é a da 347 com `construcao` no fim — por isso esta roda depois
-- dela: rodando antes, a 347 tiraria `construcao` da lista. Nenhum dos que existiam descrevia
-- o alcance: 'servico' pegaria advocacia e contabilidade, e 'recorrente' as
-- consultorias. O perfil `obras` e o que vier depois dele não abrem pra nenhuma
-- delas. O portão mora em finance/novidades._construcao.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--              (a primeira e, hoje, a única conta do ramo)
--
-- PRA QUEM: dono e gestor. O vendedor fica de fora: o que muda pra ele (as
-- colunas do funil) ele vê no próprio quadro, e o resto do aviso — o assistente
-- da empresa e o plano de contas — ele não alcança.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('nicho-construcao-e-reforma', 'novidade', 'construcao', '{dono,gestor}',
 'O Zaq agora fala a língua da obra',
 'Construção e reforma virou um ramo do sistema: funil com visita, proposta e crédito em análise, e um assistente que separa material de mão de obra e pergunta de qual obra é cada nota.',
 '/painel/prospeccao',
 $txt$Até hoje o seu ramo falava a língua de escritório: o funil marcava "reunião", pedia porte e segmento do CNPJ de cada cliente e somava mensalidade, e o assistente chamava a sua receita de honorário. Nada disso é obra. O ramo agora se chama "Construção e reforma", e quatro coisas mudam.

O FUNIL TEM AS SUAS COLUNAS

Novo, Contatado, Follow-up, Visita, Proposta, Crédito em análise, Fechado e Perdido. Visita serve pros dois negócios: a visita à casa pronta, pra quem compra, e a visita técnica, pra quem reforma. Proposta é a simulação da casa ou o orçamento da reforma. E "Crédito em análise" é a coluna que só o seu ramo tem: é onde fica quem está esperando a Caixa aprovar o financiamento da casa, ou o Reforma Casa Brasil da reforma. Enquanto o lead está ali, a vez não é sua — e agora dá pra ver isso, em vez de ele parecer esquecido em Proposta.

O FUNIL SABE POR QUE A VENDA CAI

Os motivos de perda são os da casa pela Caixa: crédito reprovado, restrição no CPF, renda que não enquadra, quem já tem imóvel ou financiamento (e perde o FGTS) e avaliação da Caixa abaixo do preço. E os da reforma: preço, fechou com outro construtor ou pedreiro, adiou a obra. Você pode ligar, desligar e renomear cada um na Régua.

O ASSISTENTE APRENDEU O RAMO

Quando você manda a foto de uma nota de material, ele pergunta de qual obra foi (a Casa 2, a reforma da Dona Maria) ou se é pra dividir entre as obras. Material vai pra "Insumos" e mão de obra de empreiteiro e diarista vai pra "Serviços" — é essa separação que diz se uma casa deu lucro. Nota de loja de material já nasce como gasto da empresa. Ele não promete crédito (quem aprova é a Caixa), não fala em "taxa de liberação" e não dá conselho de imposto: RET e INSS da obra são com o seu contador.

DUAS CONTAS NOVAS NO PLANO DE CONTAS

"Venda de Imóveis", pra casa vendida, e "Mão de Obra de Obras", pro custo da mão de obra de cada casa. A reforma continua em "Prestação de Serviços".

O que vem depois: a aba Obras, com o gasto de cada casa, as etapas marcadas pelo WhatsApp, os documentos (habite-se, CND, averbação) e o caminho até o dinheiro da Caixa cair na conta.$txt$,
 timestamptz '2026-09-25 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'nicho-construcao-e-reforma';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica'));
