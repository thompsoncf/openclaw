-- 229_novidade_regua_padrao_do_ramo.sql
-- O aviso da Régua que passa a herdar o padrão do ramo (CLAUDE.md §5).
--
-- PÚBLICO 'todos' e não 'eventos' (§6): a Régua é de toda conta que tem funil, e a
-- herança vale pros três perfis — o que muda por nicho é o VALOR herdado, não a
-- existência da tela. O aviso não fala de festa; o único campo que só aparece pra
-- quem vende data (o relógio do evento) está descrito como tal.
--
-- PRA QUEM: dono e gestor. O vendedor não abre a Régua — é configuração da
-- empresa, e o gate da tela já barra ele (`gerencia`). Aviso de tela que ele não
-- tem, nunca.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('regua-padrao-do-ramo', 'novidade', 'todos', '{dono,gestor}',
 'A Régua passa a vir com o padrão do seu ramo, e diz o que é seu e o que é herdado',
 'Cada prazo da Régua agora mostra se o número é o padrão do seu ramo ou uma escolha sua. Campo em branco usa o padrão — e apagar um campo é como se volta atrás.',
 '/painel/prospeccao/regua',
 $txt$Até agora, toda conta nascia com os mesmos prazos gravados dentro dela. Parecia certo, mas tinha um efeito ruim: quando a gente melhorava o padrão, ninguém recebia a melhoria — cada empresa continuava com a cópia do dia em que foi criada, e não havia como saber se aquele número tinha sido escolhido por alguém ou se só tinha nascido ali.

Mudou. Agora cada campo da Régua diz de onde o número vem: "padrão eventos" quando é o do seu ramo, ou "você" quando foi você quem definiu. O número cinza dentro do campo vazio é o padrão em vigor.

Para mudar, é só digitar. Para voltar atrás, apague o campo e salve — ele volta a seguir o padrão do ramo, inclusive as melhorias que vierem depois. Antes não existia esse caminho de volta: campo vazio queria dizer "não mexi", então um número digitado uma vez ficava para sempre.

E os prazos do follow-up ganharam tela. A escada de toques, o prazo da proposta parada e o teto de leads cobrados por vendedor por dia agora são campos na Régua — antes só dava para mudar em atualização do sistema. Quem vende data também configura a partir de quantos dias o evento próximo aperta o prazo.

Nada mudou de valor: os números continuam exatamente os que a sua conta já usava, e nenhuma automação foi ligada ou desligada. Ligar e desligar segue onde sempre esteve, e continua sendo escolha sua.$txt$,
 timestamptz '2026-09-11 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'regua-padrao-do-ramo';
