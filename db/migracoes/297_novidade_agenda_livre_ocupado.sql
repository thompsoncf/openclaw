-- 297_novidade_agenda_livre_ocupado.sql
-- A Agenda do nicho de eventos passa a dizer se a data está livre, e a faixa do
-- ano mostra os sábados que sobram.
--
-- O QUE MUDOU NA TELA:
--   * O CALENDÁRIO DIZ LIVRE OU OCUPADO. Tarja âmbar com o nome = o espaço é de
--     alguém naquele dia; tracejada = data segurada esperando o sinal; verde =
--     sábado livre; coral = ninguém disse ainda se aquilo ocupa o espaço.
--   * A VISITA DEIXOU DE OCUPAR A DATA. Ela aparece como "N visitas", discreta,
--     e o dia continua à venda.
--   * FAIXA DO ANO acima do calendário: treze meses, cada um com quantos
--     SÁBADOS ainda estão livres. Mês lotado vem em vermelho; clicar troca o mês
--     sem recarregar a tela.
--   * NO CELULAR o calendário finalmente cabe: a célula encolheu e a tarja virou
--     uma barra de cor, em vez de texto cortado.
--
-- POR QUE EXISTE. Medido na Prime (conta 34) em 19/09/2026:
--   * setembro tinha 28 marcas no calendário e só OITO ocupavam o espaço — 19
--     eram visitas. E a bolinha era pintada por tipo (pessoal, empresa,
--     fornecedor), então visita e casamento eram a MESMA bolinha azul: olhando o
--     mês não dava pra saber se a data estava livre;
--   * a casa vende de agosto de 2026 a fevereiro de 2028 — dezenove meses de
--     seta pra doze meses com evento, sete cliques caindo em mês vazio. Julho de
--     2027 ficava a dez cliques;
--   * dos 42 dias vendidos, VINTE E QUATRO são sábado (57%). Por isso a faixa
--     conta sábado: terça vaga não é estoque;
--   * no celular, cada célula ficava com ~50px e o nome do cliente não aparecia
--     — e é o vendedor, no telefone, quem responde "tem data livre?".
--
-- O QUE O SISTEMA ADMITE NÃO SABER. Cinco dos 84 compromissos da conta não têm
-- como ser classificados sozinhos, e a tela diz "a conferir" em vez de chutar.
-- Dois deles provam por quê: "REUNIÃO COM ENGENHEIRA" não ocupa o espaço e
-- "Reunião Política - Bianca - Pedro" ocupa (teve sinal). Adivinhar pelo título
-- marcaria a reunião com a engenheira como data vendida, e o vendedor perderia
-- aquele dia achando que já era de alguém. A tela de responder esses cinco vem
-- na sequência.
--
-- PRA QUEM: dono, gestor e vendedor. É o vendedor que vive nesta tela — quem
-- responde "tem data livre em março?" no WhatsApp é ele, no celular.
--
-- O PORTÃO: `eventos`. A ZAQ e as demais contas não foram tocadas: numa clínica
-- "sábado livre" não quer dizer nada, e a Agenda de lá segue exatamente como
-- estava (seção 6 do CLAUDE.md).
--
-- CONTAS ALCANÇADAS: 34 (MANOEL SOARES) e 35 (Louana vanessa cardoso Santos
-- costa) — as duas de nicho `eventos` em 19/09/2026.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('agenda-livre-ocupado', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A Agenda passou a dizer quais datas estão livres',
 'O calendário deixou de tratar visita e festa como a mesma coisa: agora ele mostra em cores se o espaço está ocupado, segurado ou livre naquele dia, e uma faixa no topo mostra, mês a mês, quantos sábados ainda estão disponíveis para venda.',
 '/painel/agenda',
 $txt$A Agenda mostrava uma bolinha colorida por tipo de compromisso — pessoal, empresa, fornecedor. Só que a visita de um cliente e o casamento dele eram os dois "empresa": a mesma bolinha azul. Olhando o mês, não dava pra saber se a data estava livre.

Em setembro, por exemplo, o calendário mostrava 28 marcas — e só oito ocupavam o espaço. As outras eram visitas.

AGORA O CALENDÁRIO RESPONDE

Cada dia mostra o que ele é:

• Tarja âmbar com o nome — o espaço é de alguém nesse dia.
• Tarja tracejada — data segurada, esperando o sinal.
• "Sábado livre", em verde — dá pra vender.
• "N visitas", discreto — o cliente vem conhecer, e o dia continua livre.

A VISITA NÃO OCUPA MAIS A DATA

Era a confusão principal: a visita é só pra mostrar o espaço. Agora ela aparece na célula sem tirar o dia do estoque.

A FAIXA DO ANO

Acima do calendário, treze meses numa linha, cada um com quantos sábados ainda estão livres. O mês que lotou aparece em vermelho — e isso é argumento de venda: "dezembro já está todo vendido".

Clicar num mês troca o calendário na hora, sem recarregar a tela. Antes, chegar em julho do ano que vem eram dez cliques na seta, sete deles caindo em mês vazio.

POR QUE SÁBADO

Porque é o que a casa vende: de cada dez datas vendidas, quase seis são sábado. Uma terça livre não é oportunidade; um sábado livre é.

NO CELULAR

O calendário não tinha regra de celular nenhuma: a grade continuava com sete colunas e o nome do cliente não cabia. Agora a célula encolhe e a tarja vira uma barra de cor — a cor responde livre ou ocupado de longe, e o nome completo fica a um toque, na caixa do dia.

QUANDO O SISTEMA NÃO SABE, ELE PERGUNTA

Alguns compromissos antigos não dá pra classificar sozinho — "REUNIÃO COM ENGENHEIRA" não ocupa o espaço, mas "Reunião Política" ocupou. Esses aparecem como "a conferir", em vez de o sistema chutar e você perder uma data achando que ela já era de alguém. A tela pra responder essas pendências vem na sequência.$txt$,
 timestamptz '2026-09-19 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-livre-ocupado';
