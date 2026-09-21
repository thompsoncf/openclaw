-- 305_novidade_cotacao.sql
-- A tela de Cotações — o aviso da mudança (regra 5 do CLAUDE.md).
--
-- O PEDIDO (dono, 21/09/2026): "quero implementar uma API pra Liberal Seguros pra
-- cotação de seguros e, caso tenha, o envio de dados pra apólice por seguradora."
-- Tabelas na migração 304.
--
-- O QUE MUDOU NA TELA
-- 1) Menu novo: **Cotações** (/painel/cotacoes), só pra conta de corretora. O
--    corretor cria a cotação, compara as ofertas e transforma a escolhida em
--    proposta na carteira — sem redigitar nada.
-- 2) No WhatsApp: "cota um Argo 2022 pro Fulano" passa a criar a cotação.
-- 3) Pra gerência: chaves de API, pro site da corretora cotar de fora.
--
-- O PORTÃO: `seguros`, o mesmo de Renovações (migração 279) e pelo mesmo motivo —
-- cotação de auto numa conta de festa é o erro que a §6 nasceu pra impedir.
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor aqui é o CORRETOR, e cotar é a
-- rotina dele — é a tela que ele mais vai abrir. As chaves de API não são dele, e
-- isso a tela resolve (o botão só existe pra gerência); o aviso não precisa de
-- outra mira por causa de um botão.
--
-- CONTAS ALCANÇADAS em 21/09/2026: uma — a conta 37, Liberal Seguros (o único
-- nicho `seguros` da base, criada em 09/09/2026, ver a migração 242).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('cotacao-de-seguros', 'novidade', 'seguros', '{dono,gestor,vendedor}',
 'Agora a cotação também fica guardada — e vira proposta num toque',
 'A corretora passa a registrar cada cotação no sistema: o risco do cliente, os preços de cada seguradora lado a lado e a oferta escolhida, que vira proposta na carteira sem ninguém redigitar. Cotação também pelo WhatsApp e pelo site da corretora.',
 '/painel/cotacoes',
 $txt$Tem uma tela nova no menu: COTAÇÕES.

A carteira de apólices começa na proposta — o papel que a seguradora já emitiu. Tudo que vem antes dela (o risco que o cliente passou, os preços que cada seguradora deu, a oferta que ele escolheu) não ficava guardado em lugar nenhum: vivia no multicálculo, no WhatsApp e na sua cabeça. É isso que muda.

COMO FUNCIONA. Você preenche o risco uma vez — CPF, nascimento, CEP e o veículo (código FIPE, placa OU marca/modelo + ano, qualquer um serve). As ofertas ficam lado a lado, da mais barata pra mais cara, com prêmio, franquia, parcelas e a comissão estimada. Escolheu? Um toque gera a proposta na carteira, com o veículo, o condutor, o prêmio e a vigência já preenchidos — e ela entra na régua de renovação 60/30/15 no mesmo instante.

VOCÊ DIGITA AS OFERTAS, POR ENQUANTO. Não há multicálculo conectado ainda, e a tela foi feita pra funcionar assim desde o primeiro dia: você cota onde já cota hoje e lança o resultado aqui. Quando a corretora contratar uma API de cotação, as ofertas passam a chegar sozinhas e a tela não muda em nada.

PELO WHATSAPP. "Cota um Argo 2022 pro Fulano, CPF tal, CEP tal" cria a cotação e devolve o link. É pra quando você está com o cliente na frente e abrir o painel custa a cotação inteira.

PELO SITE DA CORRETORA. Em Cotações, a gerência emite uma chave de API: com ela, o site ou a landing manda o formulário direto pro sistema, e a cotação aparece nesta lista com a origem API. É um lead que chega com o risco já preenchido, não um "me liga".

A COMISSÃO SAI DO PRÊMIO LÍQUIDO, sem o IOF — o mesmo percentual por seguradora que você já cadastrou em Renovações. Quando a cotação não separa o IOF, a tela mostra "sem IOF separado" e não estima: rachar o total por um imposto chutado faria a comissão parecer maior do que é.

EMISSÃO. Escolhida a oferta, o botão "Enviar pra emissão" monta o resumo pronto pra digitar no portal da seguradora, na ordem em que os portais perguntam. Emitiu por lá? Volte, informe o número da proposta e ela entra na carteira. No dia em que uma seguradora aceitar receber os dados por API, o mesmo botão manda direto.

PERDEU A COTAÇÃO? Marque, e diga por quê. É o que vai permitir responder daqui a alguns meses a pergunta que nenhuma corretora sabe responder: quando eu perco, perco pra qual preço.$txt$,
 timestamptz '2026-09-21 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'cotacao-de-seguros';
