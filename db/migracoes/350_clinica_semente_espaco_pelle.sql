-- 350_clinica_semente_espaco_pelle.sql
-- Os dados aprovados da Espaço Pelle (conta 39) no cadastro da 348, pra clínica
-- ver a própria cara no sistema no primeiro dia — decisão do resumo aprovado
-- (docs/mockups/clinica_visao_geral.html, seção 10: "vale começar pela fase 1 já
-- com os dados reais da Espaço Pelle").
--
-- DE ONDE VEM: o protótipo aprovado (clinica_prototipo.html), que foi levantado no
-- Amigo da clínica em 22/09/2026 só lendo, sem dado de paciente. Nenhum paciente
-- entra aqui: são os profissionais, os tipos de atendimento, a sede, as cidades e
-- a grade do Dr. Manoel.
--
-- PREÇO: só a consulta tem preço real (R$ 500, o texto que a recepção já usa). Os
-- outros ficam zerados = "sob consulta", porque os valores do protótipo eram de
-- exemplo; a clínica preenche na tela.
--
-- O QUE FICA A CONFIRMAR (aparece no topo da tela): a função e os atendimentos da
-- Brenda e da Cristiane, a grade da Juliana e o endereço das cidades.
--
-- SÓ RODA SE: a conta 39 existe, é do nicho clínica e ainda não tem profissional.
-- Em qualquer outro banco (teste, outro ambiente) é no-op. Idempotente.

do $$
declare
  cid constant bigint := 39;
  sede bigint;
  manoel bigint; juliana bigint;
  t record;
begin
  -- banco sem nicho (o esqueleto do test_blindagem_migracoes): nada a semear
  if to_regclass('public.nichos') is null or not exists (
       select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'contas' and column_name = 'nicho_id') then
    return;
  end if;
  if not exists (select 1 from public.contas ct join public.nichos n on n.id = ct.nicho_id
                  where ct.id = cid and n.slug = 'clinica') then
    return;
  end if;
  if exists (select 1 from public.clinica_profissionais where conta_id = cid) then
    return;
  end if;

  -- locais
  insert into public.clinica_locais (conta_id, nome, endereco, cidade, tipo, ordem)
    values (cid, 'Espaço Pelle', 'Rua Óscar Galvão, 38', 'Pedreiras', 'sede', 0)
    returning id into sede;
  insert into public.clinica_locais (conta_id, nome, cidade, tipo, ordem)
    select cid, c, c, 'viagem', o from (values
      ('Bacabal',1),('Codó',2),('Lago da Pedra',3),('Timbiras',4),('Vitorino Freire',5),
      ('Poção de Pedras',6),('Igarapé Grande',7),('Paulo Ramos',8),('Esperantinópolis',9)) v(c, o);

  -- tipos de atendimento (catálogo), sem pisar em slug que já exista
  for t in select * from (values
      ('consulta','Consulta',30,'consulta','#25D366',50000,30,'faltou ou quer reagendar',true,true,1),
      ('consulta-social','Consulta social',30,'consulta','#25D366',0,null,null,false,false,2),
      ('avaliacao','Avaliação',30,'consulta','#46F58A',0,null,null,false,true,3),
      ('retorno','Retorno',30,'retorno','#8FA197',0,null,null,false,true,4),
      ('cortesia','Cortesia',30,'retorno','#8FA197',0,null,null,false,false,5),
      ('procedimento-clinico','Procedimento clínico',60,'procedimento','#E0A32E',0,null,
         'cauterização: reaplicação · biópsia: resultado',false,false,6),
      ('procedimento-estetico','Procedimento estético',60,'procedimento','#E0574F',0,null,
         'botox: reaplicação · ácido hialurônico: continuação · criolipólise: nova sessão',false,false,7),
      ('procedimento-parceria','Procedimento parceria',60,'procedimento','#E0A32E',0,null,null,false,false,8),
      ('cirurgia-dermatologica','Cirurgia dermatológica',45,'cirurgia','#C9A3E0',0,null,null,false,false,9),
      ('testes-alergicos','Testes alérgicos',15,'exame','#229ED9',0,null,'retirada: fazer o exame',false,false,10),
      ('retirada-teste-alergico','Retirada teste alérgico',15,'retorno','#229ED9',0,null,null,false,false,11),
      ('coleta-cimlab','Coleta CIMLAB',15,'exame','#229ED9',0,null,'coleta ou entrega de resultado',false,false,12),
      ('vacina','Vacina',30,'procedimento','#8FE3B8',0,null,null,false,false,13),
      ('sessao-de-pacote','Sessão de pacote',30,'sessao','#8FE3B8',0,null,'agendar próxima sessão',false,true,14)
    ) v(slug, nome, dur, cat, cor, preco, volta, motivo, diz, marca, ordem)
  loop
    insert into public.servicos_catalogo
      (conta_id, slug, nome, duracao_min, categoria, cor, setup_centavos, mensal_centavos,
       volta_dias, volta_motivo, agente_diz_preco, agente_marca, ordem)
    values (cid, t.slug, t.nome, t.dur, t.cat, t.cor, t.preco, 0, t.volta, t.motivo,
            t.diz, t.marca, t.ordem)
    on conflict (conta_id, slug) do nothing;
  end loop;

  -- profissionais
  insert into public.clinica_profissionais (conta_id, nome, funcao, especialidade, cor, acesso, aviso_agenda, ordem)
    values (cid, 'Dr. Manoel', 'Dermatologista', 'Dermatologia', '#25D366', 'sem_login', true, 1)
    returning id into manoel;
  insert into public.clinica_profissionais (conta_id, nome, funcao, especialidade, cor, acesso, ordem)
    values (cid, 'Juliana', 'Fisioterapeuta dermatofuncional', 'Fisioterapia dermatofuncional',
            '#229ED9', 'sem_login', 2)
    returning id into juliana;
  insert into public.clinica_profissionais (conta_id, nome, cor, acesso, ordem)
    values (cid, 'Brenda', '#C9A3E0', 'sem_login', 3), (cid, 'Cristiane', '#E0A32E', 'sem_login', 4);

  -- quem faz o quê
  insert into public.clinica_profissional_tipos (conta_id, profissional_id, servico_id)
    select cid, manoel, s.id from public.servicos_catalogo s
     where s.conta_id = cid and s.slug in ('consulta','retorno','procedimento-clinico',
           'procedimento-estetico','cirurgia-dermatologica','testes-alergicos')
    on conflict do nothing;
  insert into public.clinica_profissional_tipos (conta_id, profissional_id, servico_id)
    select cid, juliana, s.id from public.servicos_catalogo s
     where s.conta_id = cid and s.slug in ('procedimento-estetico','sessao-de-pacote','avaliacao')
    on conflict do nothing;

  -- a grade do Dr. Manoel na sede: seg a sex, 08:00–12:00 e 13:30–16:30
  insert into public.clinica_grade (conta_id, profissional_id, local_id, dias, inicio, fim, repete, encaixes)
    values (cid, manoel, sede, '1,2,3,4,5', '08:00', '12:00', 'semanal', 2),
           (cid, manoel, sede, '1,2,3,4,5', '13:30', '16:30', 'semanal', 0);
end $$;

-- rollback (só da semente; a 348 guarda as tabelas):
--   delete from public.clinica_grade where conta_id = 39;
--   delete from public.clinica_profissional_tipos where conta_id = 39;
--   delete from public.clinica_profissionais where conta_id = 39;
--   delete from public.clinica_locais where conta_id = 39;
--   delete from public.servicos_catalogo where conta_id = 39 and slug in (...os 14 acima...);
