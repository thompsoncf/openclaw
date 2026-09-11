-- 228_funil_config_herda_do_nicho.sql
-- A configuração do funil deixa de ser COPIADA e passa a ser HERDADA.
--
-- O QUE MOTIVOU (medido em 11/09/2026, pedido do dono: "tudo isso é configurado,
-- deixa uma forma de parametrizar, porque serve pra outras empresas do mesmo nicho
-- ou outras — sempre tem que pensar dessa forma")
--
-- As 6 contas com linha em `funil_regua` carregavam EXATAMENTE os mesmos 12 valores
-- de prazo e janela, e nenhuma delas tinha escolhido nenhum: todas as colunas eram
-- `NOT NULL DEFAULT <valor>`, então o número era copiado pra dentro da conta no
-- INSERT. Copiar tem uma consequência que só aparece meses depois: no dia em que
-- descobrirmos que a escada certa pra eventos é 1·3·7 e não 2·4·7·15, NENHUMA das
-- contas recebe a correção — todas carregam a cópia velha, e olhando o banco não há
-- como distinguir "o dono escolheu 2,4,7,15" de "nasceu assim e ninguém mexeu".
--
-- E não é hipótese: a DOCE MELL (conta 35) é do mesmo nicho da PRIME (eventos) e
-- nasceu com o funil genérico — Novo · Contatado · Qualificado · Proposta · Ganho ·
-- Perdido —, o mesmo das contas de consultoria, sem visita, sem follow-up e sem uma
-- palavra de festa. O padrão do nicho não alcançava a segunda empresa do nicho.
--
-- O QUE MUDA
-- As 12 colunas de prazo/janela passam a aceitar NULL, e NULL quer dizer "usa o
-- padrão do meu perfil" (finance/raio_x_perfil._FUNIL_POR_PERFIL). O valor só é
-- gravado quando o dono encosta no campo; melhorar o padrão alcança no mesmo dia
-- todo mundo que nunca mexeu, e não desfaz a escolha de quem mexeu.
--
-- OS TRÊS MODOS FICAM DE FORA, de propósito. `gatilhos_modo`, `cobranca_modo` e
-- `follow_up_modo` continuam NOT NULL DEFAULT 'off': ligar uma automação é uma
-- afirmação sobre a EMPRESA, não sobre o ramo dela, e não existe "modo herdado" —
-- desligado é uma escolha, não a ausência de uma.
--
-- SOBRE O BACKFILL, E O QUE ELE PODE ERRAR
-- O UPDATE abaixo põe NULL onde o valor é IDÊNTICO ao default antigo. Não há como
-- distinguir isso de alguém que escolheu deliberadamente o mesmo número — e o erro,
-- se houver, é inofensivo nos dois sentidos: hoje o valor efetivo não muda (o padrão
-- do perfil eventos é exatamente o antigo default), e amanhã a conta passa a
-- acompanhar o padrão em vez de ficar congelada. Quem quiser fixar o número é só
-- salvar a Régua uma vez.
--
-- Aditiva e idempotente. Nenhuma linha é apagada; o único dado que muda é
-- "valor copiado" virando "sem valor, herda".

-- ------------------------------------------------------------- 1. aceitar NULL
alter table public.funil_regua
  alter column janela_dias       drop not null,
  alter column janela_abre       drop not null,
  alter column janela_fecha      drop not null,
  alter column sem_resposta_min  drop not null,
  alter column bola_nossa_min    drop not null,
  alter column bola_cliente_min  drop not null,
  alter column escala_min        drop not null,
  alter column teto_avisos_dia   drop not null,
  alter column fu_proposta_dias  drop not null,
  alter column fu_toques_dias    drop not null,
  alter column fu_festa_dias     drop not null,
  alter column fu_teto_dia       drop not null;

-- ------------------------------------------------------------- 2. parar de copiar
-- Sem o DROP DEFAULT a linha nova continua nascendo com o valor dentro, e a herança
-- nunca chega a acontecer pra conta nenhuma.
alter table public.funil_regua
  alter column janela_dias       drop default,
  alter column janela_abre       drop default,
  alter column janela_fecha      drop default,
  alter column sem_resposta_min  drop default,
  alter column bola_nossa_min    drop default,
  alter column bola_cliente_min  drop default,
  alter column escala_min        drop default,
  alter column teto_avisos_dia   drop default,
  alter column fu_proposta_dias  drop default,
  alter column fu_toques_dias    drop default,
  alter column fu_festa_dias     drop default,
  alter column fu_teto_dia       drop default;

-- ------------------------------------------------------------- 3. soltar as cópias
-- Só onde o valor é o default antigo. Quem tiver qualquer número diferente mantém
-- tudo como está — inclusive as outras colunas da mesma linha.
update public.funil_regua set janela_dias      = null where janela_dias      = '1,2,3,4,5,6';
update public.funil_regua set janela_abre      = null where janela_abre      = time '08:00';
update public.funil_regua set janela_fecha     = null where janela_fecha     = time '19:00';
update public.funil_regua set sem_resposta_min = null where sem_resposta_min = 120;
update public.funil_regua set bola_nossa_min   = null where bola_nossa_min   = 240;
update public.funil_regua set bola_cliente_min = null where bola_cliente_min = 4320;
update public.funil_regua set escala_min       = null where escala_min       = 240;
update public.funil_regua set teto_avisos_dia  = null where teto_avisos_dia  = 5;
update public.funil_regua set fu_proposta_dias = null where fu_proposta_dias = 3;
update public.funil_regua set fu_toques_dias   = null where fu_toques_dias   = '2,4,7,15';
update public.funil_regua set fu_festa_dias    = null where fu_festa_dias    = 30;
update public.funil_regua set fu_teto_dia      = null where fu_teto_dia      = 15;

comment on table public.funil_regua is
  'Config do funil POR CONTA. Coluna NULL = herda o padrão do perfil do nicho '
  '(finance/raio_x_perfil.funil_resolvido). Os três *_modo nunca herdam: off é escolha.';

-- rollback:
--   as colunas voltam a NOT NULL DEFAULT — mas antes é preciso preencher os NULLs,
--   senão o ALTER falha. Na ordem: update ... set <col>=<default antigo> where <col>
--   is null; depois alter column <col> set default <valor>, set not null.
