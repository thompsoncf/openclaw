-- 297_erro_cliente.sql
-- O que deu errado NA TELA fica registrado.
--
-- O PEDIDO (dono, 19/09/2026): "vamos fazer o zapFetch agora". Mas o motivo de
-- ele vir PRIMEIRO, antes de espalhar pelas 119 chamadas, é esta tabela.
--
-- O BURACO. Em 15/09 o envio de texto parou por volta das 19:33 BRT, e a pergunta
-- "o que houve?" não teve resposta: o painel dizia "Falha de rede." pra cinco
-- causas diferentes e não guardava NADA. Sem registro, todo diagnóstico vira
-- chute — e chute nesta base já custou uma manhã de trabalho e 9.714 linhas de
-- cofre de sessão (ver CLAUDE.md § 1).
--
-- O QUE ENTRA AQUI, e só isto: troca em que o servidor NÃO respondeu algo que a
-- tela saiba ler — 5xx, ou corpo que não é JSON. Ver `zap_fetch._semResposta`.
--
-- O QUE NÃO ENTRA, de propósito:
--   * `{ok:false, erro:'motivo_obrigatorio'}` — o servidor respondeu, e bem. Isso
--     é conversa normal, não ocorrido.
--   * 401 (sessão expirada) — é rotina, e registrar daria uma linha por aba
--     esquecida durante a noite.
--   * estar sem internet — não há como registrar de um navegador offline, e a
--     causa não é nossa.
--
-- POR QUE `conta_id` E `membro_id` SÃO NULOS. A rota grava o que a SESSÃO diz, e
-- uma sessão que expirou não diz nada. Linha sem dono ainda vale: o `url` e o
-- `status` são o que responde "o que houve às 19:33". Sem FK pelo mesmo motivo —
-- um registro de erro não pode falhar porque a conta foi apagada depois.
--
-- RETENÇÃO. Fica pra faxina (`scripts/faxina_retencao.py`) quando houver volume:
-- hoje a tabela nasce vazia e só recebe linha quando algo quebra. Se um dia
-- encher, é sinal de que há o que consertar, não de que a tabela é grande demais.
--
-- Aditivo e idempotente.

create table if not exists public.erro_cliente (
  id          bigserial primary key,
  conta_id    bigint,                  -- sem FK: ver o comentário acima
  membro_id   bigint,
  url         text not null,
  metodo      text,
  status      integer,                 -- HTTP; nulo quando o fetch nem saiu
  corpo       text,                    -- os primeiros 300 caracteres da resposta
  versao_aba  text,                    -- a versão que a aba tinha carregado
  versao_app  text,                    -- a que o servidor está servindo agora
  agente      text,                    -- user-agent, cortado
  ocorrido_em timestamptz not null default now()
);

-- "o que houve entre 19:30 e 19:40" é a pergunta, e ela é por TEMPO.
create index if not exists idx_erro_cliente_quando
    on public.erro_cliente (ocorrido_em desc);
-- e "esta conta está vendo erro?" é a segunda.
create index if not exists idx_erro_cliente_conta
    on public.erro_cliente (conta_id, ocorrido_em desc);

-- rollback:
--   drop table if exists public.erro_cliente;
