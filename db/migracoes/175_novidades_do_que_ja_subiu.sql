-- 175_novidades_do_que_ja_subiu.sql
-- Os três avisos do que subiu esta semana — e o MODELO pra escrever os próximos.
--
-- ────────────────────────────────────────────────────────────────────────────
-- COMO ESCREVER UM AVISO (a receita, pra não errar a mira)
--
-- 1. NOMEIE O PORTÃO, não o nicho. `publico` é o nome de uma função que JÁ decide
--    quem vê a funcionalidade (finance/novidades.py:PUBLICOS):
--
--      todos       — vale pra qualquer conta
--      produto     — quem DECLAROU nicho que vende produto  (vende_produto)
--      servico     — quem vende serviço                     (vende_servico)
--      eventos     — quem vende data e tem contrato         (tem_contrato)
--      recorrente  — serviço que NÃO é evento               (mensalidade)
--
--    Se nenhum descreve quem recebeu a mudança, o problema não é o aviso: é que a
--    mudança foi pro ar sem ninguém saber quem ela atinge. Aí o certo é criar o
--    portão no código primeiro — e acrescentar o valor no check da 174 junto, que
--    o teste `test_o_banco_e_o_python_conhecem_os_mesmos_publicos` cobra.
--
-- 2. ESCOLHA O TIPO PELO QUE A PESSOA PRECISA FAZER, não pelo tamanho da mudança:
--      'novidade' — ganhou alguma coisa. Marca lida sozinho quando ela abre a tela.
--      'mudanca'  — perdeu um botão, ou o jeito de trabalhar mudou. Exige o
--                   "Entendi" explícito, e é isso que permite saber QUEM já viu.
--
-- 3. CONFIRA A LISTA ANTES. `finance.novidades.contas_alcancadas(pool, publico)`
--    devolve os nomes de quem vai receber. As travas do código pegam erro de
--    código; essa lista pega erro de julgamento.
--
-- 4. `chave` é estável e única — é ela que torna a inserção idempotente. Reaplicar
--    a migração não duplica aviso nem desmarca quem já leu.
--
-- 5. `publicado_em` é a data em que a mudança SUBIU, não a de hoje: quem se
--    cadastrou depois dela não recebe (finance/novidades.py:listar corta por
--    `contas.criado_em`). Ninguém abre o painel pela primeira vez com uma pilha de
--    changelog de coisas que nunca viveu.
--
-- Aditivo e idempotente.
-- ────────────────────────────────────────────────────────────────────────────

insert into public.novidades (chave, tipo, publico, titulo, corpo, publicado_em) values

-- Nicho de EVENTOS: a assinatura passou a ser o que abre o financeiro (#457, #459).
-- É 'mudanca' porque um botão SUMIU do funil — quem procurar não vai achar.
('contrato-assinatura-fecha', 'mudanca', 'eventos',
 'O contrato agora é quem fecha o negócio',
 $txt$O botão "Fechar contrato" saiu do funil.

O caminho agora é: você aprova o orçamento, o contrato nasce com um link próprio, o cliente abre esse link e assina. Quando ele assina, o sistema gera as contas a receber sozinho.

Enquanto ele não assinar, nada entra no financeiro — e é essa a diferença que importa: antes dava pra fechar o contrato antes da assinatura, e o dinheiro entrava no caixa de um negócio que ainda não existia.$txt$,
 timestamptz '2026-08-17 15:00:00+00'),

-- Nicho de EVENTOS: o vocabulário de data segurada na Agenda (#448, #490).
-- Mesmo portão da tela (vende_data -> modo_por_nicho == 'evento').
('agenda-de-eventos', 'novidade', 'eventos',
 'A agenda ficou com cara de eventos',
 $txt$O calendário agora fala a sua língua: data fixada, data segurada e o sinal que confirma.

No formulário dá pra só segurar a data — ela fica reservada esperando o sinal e se solta sozinha se ele não cair no prazo, sem você precisar lembrar de liberar.

E a agenda é da empresa: dono, gestor e vendedor veem o mesmo calendário, pra a mesma data não ser prometida duas vezes.$txt$,
 timestamptz '2026-08-16 15:00:00+00'),

-- Quem vende SERVIÇO (o painel inteiro é gateado por vende_servico) — não é só de
-- eventos: consultoria, advocacia e construção também fazem orçamento com desconto.
('desconto-por-item', 'novidade', 'servico',
 'Desconto por item, em % ou em R$',
 $txt$Cada linha do orçamento passou a ter o desconto dela, e o total tem outro por cima.

Você escolhe se digita em porcentagem ou em reais. Nos dois casos a tela e a proposta que o cliente recebe mostram o valor cheio riscado e o que ficou.

Os dois descontos encadeiam: o do total é calculado sobre o que já saiu com desconto nos itens.$txt$,
 timestamptz '2026-08-18 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades
--    where chave in ('contrato-assinatura-fecha','agenda-de-eventos','desconto-por-item');
