-- 204_novidade_aditivo_texto.sql
-- O aviso do texto do aditivo virar editável (seção 5 do CLAUDE.md).
-- Precisa da 199 (pra_quem, resumo, link) e da 203 (a tabela do modelo).
--
-- PRA QUEM: dono e gestor, e AQUI o vendedor fica de fora — ao contrário do
-- aviso do aditivo em si (202), que foi pros três. A régua da seção 5 é "aviso
-- de tela que ele não tem, nunca", e o card do modelo é gateado por `gerir`:
-- o vendedor faz aditivo, mas não escreve o texto. Avisar ele de um card que não
-- abre seria treiná-lo a ignorar o próximo aviso.
--
-- A cláusula avulsa, essa sim, é dos três — mas ela é um bloco a mais no
-- formulário que ele já usa, e entra no corpo deste mesmo aviso em vez de virar
-- um segundo aviso pra mesma pessoa.
--
-- QUEM RECEBE, conferido na produção em 05/09/2026: conta 34 (PRIME EVENTOS) e
-- conta 35 (DOCE MELL) — as duas de nicho eventos.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('aditivo-texto-editavel', 'novidade', 'eventos', '{dono,gestor}',
 'O texto do termo aditivo agora é seu, como o do contrato',
 'As cláusulas do termo aditivo passaram a ser editáveis em Serviços, com os mesmos campos automáticos do contrato.',
 '/painel/servicos',
 $txt$O contrato de locação você já escrevia com as suas palavras. O termo aditivo, que altera esse mesmo contrato, saía com um texto que não era seu — e isso agora acabou.

Em Serviços, logo abaixo do card "Contrato de locação", tem um card novo: "Termo aditivo". Dentro dele, os cinco textos, um pra cada tipo de alteração — data, horário, convidados, serviços e valor. Reescreva como a sua empresa fala.

São cinco fixos, e não uma lista onde se acrescenta cláusula, porque cada texto casa com um bloco do formulário: uma a mais não teria o que a preenchesse.

OS NÚMEROS CONTINUAM VINDO SOZINHOS. Onde entra um valor, você usa um campo, igual ao contrato: {aditivo.convidados} traz "140 (cento e quarenta)" e {aditivo.convidados_antes} traz "115 (cento e quinze)", já por extenso. Você escreve a frase; o sistema põe os números. É o que impede o documento de dizer um número e o sistema outro.

O de convidados tem dois títulos, um pra quando aumenta e outro pra quando diminui — o documento não pode anunciar acréscimo numa redução.

Tem "Pré-visualizar", que monta o texto com os números do seu contrato mais recente, e "Restaurar modelo padrão", que traz de volta o texto de fábrica sem salvar nada até você mandar.

E NO FORMULÁRIO DO ADITIVO entrou um sexto bloco: "Outra alteração", com título e texto livre. É pro que não é data, horário, convidado, serviço nem valor — trocar quem retira as chaves, liberar a entrada de um fornecedor. Ela entra no documento e é assinada junto, mas não mexe em nada no sistema, porque o sistema não tem como saber o que o texto significa. Esse bloco está disponível pra toda a equipe.

Quem não abrir o card não precisa fazer nada: o texto que já estava no ar continua igual.$txt$,
 timestamptz '2026-09-05 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'aditivo-texto-editavel';
