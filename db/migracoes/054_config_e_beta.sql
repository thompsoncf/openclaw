-- 054_config_e_beta.sql
-- Configuração global do app (chave→valor). Começa com o "modo beta grátis" LIGADO.
-- Só tabela NOVA + 1 seed: rodar não muda nada de quem já está no ar.

create table if not exists app_config (
    chave       text primary key,
    valor       text not null,
    atualizado_em timestamptz not null default now()
);

-- beta_gratis: 'on' => cadastro mostra "Grátis (beta)" e ninguém é cobrado.
-- Quando você desligar ('off'), os preços da tabela planos passam a valer.
insert into app_config (chave, valor)
values ('beta_gratis', 'on')
on conflict (chave) do nothing;
