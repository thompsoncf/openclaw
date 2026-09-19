"""O resumo semanal por e-mail (finance/resumo_semanal, migração 274).

O QUE ESTES TESTES PROTEGEM, em uma frase cada:

* "fechou" é CONTRATO ASSINADO, não etapa do funil — o erro que o primeiro
  rascunho cometeu e que ia acusar duas pessoas por e-mail;
* semana sem movimento e sem nada travado NÃO vira e-mail;
* nasce desligado, e a config falha FECHADA (sem banco, não manda);
* o mesmo resumo não sai duas vezes pra ninguém;
* o dono vê nome, o gestor vê total, o vendedor vê só a carteira dele;
* a semana é a de Brasília, e é sempre a FECHADA;
* e (migração 276) QUEM vê os nomes sai do campo "Seu e-mail", e não de o cadastro
  do dono ter e-mail — que era o defeito medido na Prime no dia seguinte à 274.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import resumo_semanal as rs
from finance import resumo_semanal_html as rsh

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
BR = timezone(timedelta(hours=-3))


# ------------------------------------------------------------------ sem banco

def test_a_semana_e_sempre_a_FECHADA_e_nao_a_de_agora():
    """Resumo de semana pela metade compara meia semana com uma inteira, e todo
    número desce sem motivo nenhum."""
    # quarta, 17/09/2026
    ini, fim = rs.semana_passada(datetime(2026, 9, 17, 12, tzinfo=timezone.utc))
    assert (ini, fim) == (date(2026, 9, 7), date(2026, 9, 13))
    assert ini.weekday() == 0 and fim.weekday() == 6


def test_a_semana_vira_na_segunda_em_BRASILIA():
    """Domingo 21h em Brasília é segunda 00h em UTC. Pela hora do servidor, o
    resumo de domingo à noite já seria o da semana seguinte."""
    dom_21h_brt = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)   # 20/09, 21h
    assert rs.chave_semana(dom_21h_brt) == "2026-W38"
    assert rs.chave_semana(datetime(2026, 9, 21, 15, tzinfo=timezone.utc)) == "2026-W39"


def test_os_emails_de_gestor_aceitam_o_que_a_pessoa_cola():
    assert rs.parse_emails("a@b.com, c@d.com") == ["a@b.com", "c@d.com"]
    assert rs.parse_emails("A@B.COM\n a@b.com ;c@d.com") == ["a@b.com", "c@d.com"]


def test_o_que_nao_e_email_e_descartado_em_silencio():
    """A aba inteira é UM formulário: um lixo no fim da lista não pode impedir o
    dono de salvar a configuração do agente que ele acabou de mexer."""
    assert rs.parse_emails("gestor@empresa.com, joao, , @nada, socio@x.com.br") == \
        ["gestor@empresa.com", "socio@x.com.br"]
    # domínio de uma letra só não passa: "x@y.z" é erro de digitação com muito
    # mais frequência do que é e-mail de verdade
    assert rs.parse_emails("x@y.z") == []


def test_a_lista_de_gestores_tem_teto():
    muitos = ", ".join(f"g{i}@x.com" for i in range(12))
    assert len(rs.parse_emails(muitos)) == rs.EMAILS_MAX


# ------------------------------------------------------------------ com banco

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  nicho_id bigint references nichos(id), criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo', estagio text default 'lead',
  evento_em date, evento_tipo text, evento_convidados int, orcamento_id bigint,
  perda_motivo text, temperatura text, segmento text, origem text,
  whatsapp text, telefone text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  status text default 'rascunho', setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint, sinal_centavos int, sinal_pago_em timestamptz,
  aprovada_em timestamptz, criado_em timestamptz default now());
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  status text default 'enviado', valor_centavos bigint default 0,
  enviado_em timestamptz, assinado_em timestamptz, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  membro_id bigint, titulo text, inicio timestamptz, fim timestamptz, tipo text default 'empresa',
  tipo_evento text, desfecho text, status text default 'ativo');
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', criado_em timestamptz default now());
create table titulos (id bigserial primary key, conta_id bigint, tipo text,
  status text default 'aberto', valor_centavos bigint default 0, vencimento date);
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, imap_host text, imap_senha text, ativo boolean default true,
  ultimo_uid bigint, atualizado_em timestamptz default now(),
  unique (conta_id, canal));
"""

AGORA = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)   # quinta


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_resumo_semanal"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # as migrações DE VERDADE: são elas que o Render vai aplicar
        c.execute((MIG / "274_resumo_semanal.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "276_resumo_semanal_dono_emails.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


def _dt(dia, hora=12):
    return datetime(2026, 9, dia, hora, tzinfo=BR)


@pytest.fixture()
def cena(pool):
    """A Prime em miniatura: semana de 07 a 13/09, com contrato assinado."""
    with pool.connection() as c:
        c.execute("delete from contratos"); c.execute("delete from orcamentos")
        c.execute("delete from eventos_agenda"); c.execute("delete from prospeccao")
        c.execute("delete from membros"); c.execute("delete from titulos")
        c.execute("delete from resumo_semanal_envio"); c.execute("delete from contas")
        c.execute("delete from nichos")
        n = c.execute("insert into nichos (nome, slug, tipo) values "
                      "('Eventos','eventos','servico') returning id").fetchone()[0]
        conta = c.execute("insert into contas (nome, nome_fantasia, nicho_id, criado_em) "
                          "values ('Prime','Prime Eventos',%s, now() - interval '90 days') "
                          "returning id", (n,)).fetchone()[0]
        dono = c.execute("insert into membros (conta_id, nome, email, papel) values "
                         "(%s,'Manoel','dono@prime.com','dono') returning id", (conta,)).fetchone()[0]
        v1 = c.execute("insert into membros (conta_id, nome, email, papel) values "
                       "(%s,'Pedro Yan','pedro@prime.com','vendedor') returning id",
                       (conta,)).fetchone()[0]
        v2 = c.execute("insert into membros (conta_id, nome, email, papel) values "
                       "(%s,'Jacqueline','jac@prime.com','vendedor') returning id",
                       (conta,)).fetchone()[0]

        def lead(vend, criado, **kw):
            return c.execute(
                """insert into prospeccao (conta_id, vendedor_id, empresa, status,
                       evento_em, criado_em, atualizado_em)
                   values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                (conta, vend, kw.get("empresa", "Cliente"), kw.get("status", "contatado"),
                 kw.get("evento_em"), criado, criado)).fetchone()[0]

        # 3 leads na semana passada (07 a 13/09), 1 na anterior
        a = lead(v1, _dt(8), empresa="Beatriz", evento_em=date(2026, 10, 3))
        lead(v1, _dt(9), empresa="Sem data")
        lead(v2, _dt(10), empresa="Renata", evento_em=date(2026, 9, 30))
        lead(v2, _dt(2), empresa="Da semana anterior")

        # O CASO QUE MOTIVOU TUDO: contrato ASSINADO e lead parado em 'proposta'
        o = c.execute("""insert into orcamentos (conta_id, cliente, status,
                             primeiro_ano_centavos, criado_em)
                         values (%s,'Beatriz','enviado',750000,%s) returning id""",
                      (conta, _dt(8))).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s, status='proposta' where id=%s", (o, a))
        c.execute("""insert into contratos (conta_id, orcamento_id, status, valor_centavos,
                         enviado_em, assinado_em, criado_em)
                     values (%s,%s,'assinado',750000,%s,%s,%s)""",
                  (conta, o, _dt(8), _dt(9), _dt(8)))
        # uma visita realizada na semana (compromisso de empresa, sem tipo de festa)
        c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio,
                         tipo, tipo_evento, desfecho)
                     values (%s,%s,'Visita',%s,'empresa',null,'realizado')""", (conta, a, _dt(10)))
        c.commit()
    return {"conta": conta, "dono": dono, "v1": v1, "v2": v2, "lead": a}


def _ligar(pool, conta, **kw):
    return rs.salvar_config(pool, conta, ativo=kw.get("ativo", True),
                            emails=kw.get("emails", ""),
                            dono_emails=kw.get("dono_emails", ""),
                            vendedor=kw.get("vendedor", True),
                            dia=kw.get("dia", "segunda"))


def test_nasce_desligado(pool, cena):
    assert rs.config(pool, cena["conta"])["resumo_semanal"] is False
    assert rs.destinatarios(pool, cena["conta"]) == []


def test_a_config_falha_FECHADA_sem_banco(cena):
    """Sem conseguir ler a configuração, NÃO manda. O pior caso aqui não é uma
    faixa a mais na tela — é a caixa de entrada de um cliente."""
    class _Morto:
        def connection(self):
            raise RuntimeError("sem banco")
    assert rs.config(_Morto(), 1)["resumo_semanal"] is False


def test_fechou_e_CONTRATO_ASSINADO_e_nao_etapa_do_funil(pool, cena):
    """O erro que o primeiro rascunho cometeu: medir por `para='ganho'`. Nesta
    cena o contrato está assinado e o lead continua em 'proposta' — como na Prime
    de verdade. Se o resumo medisse pelo funil, diria "nenhuma venda fechada"."""
    d = rs.montar(pool, cena["conta"], AGORA)
    assert d is not None
    assert int(d["placar"]["contratos"]) == 1, "o contrato assinado sumiu do resumo"
    assert int(d["placar"]["contratos_valor"]) == 750000
    with pool.connection() as c:
        st = c.execute("select status from prospeccao where id=%s", (cena["lead"],)).fetchone()[0]
    assert st == "proposta", "a cena perdeu o ponto: o lead tem que estar fora de Ganho"


def test_o_funil_traz_a_visita(pool, cena):
    """Era o degrau que faltava: entram dezenas e visitam duas. Num negócio de
    festa, quem não visita raramente assina."""
    d = rs.montar(pool, cena["conta"], AGORA)
    assert int(d["placar"]["visitas_ok"]) == 1


def test_semana_sem_movimento_e_sem_nada_travado_NAO_vira_email(pool):
    """A regra que nasceu junto com o recurso, e não depois da medição: canal que
    fala quando não tem o que dizer ensina a ser ignorado."""
    with pool.connection() as c:
        vazia = c.execute("insert into contas (nome, criado_em) values "
                          "('Vazia', now() - interval '90 days') returning id").fetchone()[0]
        c.commit()
    assert rs.montar(pool, vazia, AGORA) is None


def test_semana_parada_mas_com_carteira_travada_AINDA_vai(pool):
    """Quando nada entrou, o que já está dentro é justamente o assunto."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, criado_em) values "
                          "('Parada', now() - interval '90 days') returning id").fetchone()[0]
        c.execute("""insert into prospeccao (conta_id, empresa, status, criado_em)
                     values (%s,'Antigo','contatado', now() - interval '60 days')""", (conta,))
        c.commit()
    d = rs.montar(pool, conta, AGORA)
    assert d is not None and d["vazia"] is True
    assert d["extras"]["sem_data"] >= 1


def test_o_dono_ve_nome_o_gestor_ve_o_total(pool, cena):
    d = rs.montar(pool, cena["conta"], AGORA)
    do_dono = rsh.corpo(d, "dono", nome="Manoel", empresa="Prime Eventos")
    do_gestor = rsh.corpo(d, "gestor", nome="", empresa="Prime Eventos")
    assert "Pedro Yan" in do_dono and "Jacqueline" in do_dono
    assert "Pedro Yan" not in do_gestor and "Jacqueline" not in do_gestor
    assert "vendedores ativos" in do_gestor


def test_o_email_do_vendedor_nao_compara_com_colega(pool, cena):
    d = rs.montar(pool, cena["conta"], AGORA)
    do_vend = rsh.corpo(d, "vendedor", nome="Pedro Yan", membro_id=cena["v1"])
    assert "Jacqueline" not in do_vend
    assert "Sua semana, Pedro" in do_vend
    # e abre pelo que ele FEZ: o contrato dele
    assert "Você fechou 1 contrato" in do_vend


def test_o_email_e_claro_e_sem_bloco_style(pool, cena):
    """Cliente de e-mail descarta `<style>` no topo e reescreve fundo no modo
    escuro. Tudo inline, e fundo branco."""
    d = rs.montar(pool, cena["conta"], AGORA)
    html = rsh.corpo(d, "dono", nome="Manoel", empresa="Prime")
    assert "<style" not in html
    assert "background:#ffffff" in html


def test_quem_e_dono_nao_recebe_dois_emails(pool, cena):
    """O e-mail do dono cadastrado também no campo de gestor: um e-mail só, o de
    maior alcance. Dois quase iguais no mesmo minuto faz a pessoa desligar tudo."""
    _ligar(pool, cena["conta"], emails="dono@prime.com, gestor@fora.com")
    ds = rs.destinatarios(pool, cena["conta"])
    emails = [d["email"] for d in ds]
    assert emails.count("dono@prime.com") == 1
    assert next(d for d in ds if d["email"] == "dono@prime.com")["tipo"] == "dono"
    assert next(d for d in ds if d["email"] == "gestor@fora.com")["tipo"] == "gestor"


# ------------------------------------ quem vê os nomes (migração 276, 17/09/2026)
# O defeito que isto conserta foi medido na Prime HORAS depois da 274 subir: o dono
# (membro 28, MANOEL SOARES) está sem e-mail no cadastro, e não existe tela onde ele
# possa pôr um. Os dois endereços que ele cadastrou entravam pelo único portão que
# sobrava — o de gestor —, que é a versão SEM os nomes. Dos seis destinatários da
# primeira segunda, NENHUM receberia o que ele pediu.

def _sem_email_no_cadastro(pool, membro_id):
    with pool.connection() as c:
        c.execute("update membros set email=null where id=%s", (membro_id,))
        c.commit()


def test_o_dono_SEM_email_no_cadastro_ainda_ve_os_nomes(pool, cena):
    """O caso exato da Prime. Quem decide é o campo "Seu e-mail", não o cadastro."""
    _sem_email_no_cadastro(pool, cena["dono"])
    _ligar(pool, cena["conta"], dono_emails="manoel@prime.com")
    ds = rs.destinatarios(pool, cena["conta"])
    meu = next(d for d in ds if d["email"] == "manoel@prime.com")
    assert meu["tipo"] == "dono", (
        "o e-mail do dono entrou como gestor — é o defeito de 17/09 voltando")


def test_o_campo_de_gestor_continua_SEM_os_nomes(pool, cena):
    """Os dois campos existem justamente pra separar isso. Se o de gestor também
    virasse 'dono', a migração 276 seria só um campo a mais sem efeito nenhum."""
    _sem_email_no_cadastro(pool, cena["dono"])
    _ligar(pool, cena["conta"], dono_emails="manoel@prime.com",
           emails="contador@fora.com")
    ds = {d["email"]: d["tipo"] for d in rs.destinatarios(pool, cena["conta"])}
    assert ds["manoel@prime.com"] == "dono"
    assert ds["contador@fora.com"] == "gestor"


def test_o_mesmo_endereco_nos_DOIS_campos_recebe_um_email_so(pool, cena):
    _ligar(pool, cena["conta"], dono_emails="manoel@prime.com",
           emails="manoel@prime.com, contador@fora.com")
    ds = rs.destinatarios(pool, cena["conta"])
    assert [d["email"] for d in ds].count("manoel@prime.com") == 1
    assert next(d for d in ds if d["email"] == "manoel@prime.com")["tipo"] == "dono"


def test_vendedor_no_campo_do_dono_passa_a_ver_a_equipe(pool, cena):
    """O dono que também vende: está no cadastro como vendedor E no campo "Seu
    e-mail". Ganha o de MAIOR alcance — e não o da última fonte lida, senão a ordem
    da varredura decidiria o que a pessoa vê."""
    _ligar(pool, cena["conta"], dono_emails="pedro@prime.com")
    ds = {d["email"]: d for d in rs.destinatarios(pool, cena["conta"])}
    assert ds["pedro@prime.com"]["tipo"] == "dono"
    # e o cadastro não se perde: o nome dele continua vindo de `membros`
    assert ds["pedro@prime.com"]["nome"] == "Pedro Yan"


def test_apagar_o_campo_na_tela_apaga_a_lista(pool, cena):
    """A aba manda o formulário inteiro. Um campo esvaziado lá tem que esvaziar
    aqui — senão a tela mostra vazio e o e-mail continua saindo."""
    _ligar(pool, cena["conta"], dono_emails="manoel@prime.com")
    assert rs.config(pool, cena["conta"])["emails_dono"] == ["manoel@prime.com"]
    _ligar(pool, cena["conta"], dono_emails="")
    assert rs.config(pool, cena["conta"])["emails_dono"] == []


def test_o_rodape_nao_chama_de_dono_quem_nao_tem_login(pool, cena):
    """A versão com os nomes agora vai também pra e-mail de texto, que não é membro
    de nada. "Você recebe porque é dono da conta" seria mentira — e é justamente a
    linha que explica como sair da lista."""
    d = rs.montar(pool, cena["conta"], AGORA)
    de_texto = rsh.corpo(d, "dono", nome="", empresa="Prime", membro_id=None)
    do_cadastro = rsh.corpo(d, "dono", nome="Manoel", empresa="Prime",
                            membro_id=cena["dono"])
    assert "porque é dono da conta" in do_cadastro
    assert "porque é dono da conta" not in de_texto
    assert "cadastrado pela empresa" in de_texto
    # e os dois veem os nomes: o rodapé muda, o conteúdo não
    assert "Pedro Yan" in de_texto and "Pedro Yan" in do_cadastro


def test_o_vendedor_so_entra_se_a_conta_ligou(pool, cena):
    _ligar(pool, cena["conta"], vendedor=False)
    tipos = {d["tipo"] for d in rs.destinatarios(pool, cena["conta"])}
    assert "vendedor" not in tipos
    _ligar(pool, cena["conta"], vendedor=True)
    tipos = {d["tipo"] for d in rs.destinatarios(pool, cena["conta"])}
    assert "vendedor" in tipos


def test_o_mesmo_resumo_nao_sai_duas_vezes(pool, cena, monkeypatch):
    """Deploy no meio da segunda, retry do Render: o cron roda de novo e ninguém
    recebe o mesmo e-mail outra vez."""
    _ligar(pool, cena["conta"])
    mandados = []
    from finance import email_inbound as ei
    monkeypatch.setattr(ei, "enviar_conta",
                        lambda pool, cid, destino, *a, **kw: mandados.append(destino) or True)
    r1 = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r1["enviados"] >= 2
    n = len(mandados)
    r2 = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r2["enviados"] == 0 and len(mandados) == n


def test_falha_num_destinatario_nao_impede_os_outros(pool, cena, monkeypatch):
    _ligar(pool, cena["conta"])
    from finance import email_inbound as ei
    from finance import email_sender as es

    def _capenga(pool, cid, destino, *a, **kw):
        if destino == "pedro@prime.com":
            raise RuntimeError("caixa fora")
        return True
    monkeypatch.setattr(ei, "enviar_conta", _capenga)
    # e a rede do SMTP global também falha, senão o Pedro seria salvo por ela
    monkeypatch.setattr(es, "enviar_email", lambda *a, **k: False)
    r = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r["enviados"] >= 2 and "pedro@prime.com" in r["falhas"]
    with pool.connection() as c:
        linha = c.execute("""select ok, motivo from resumo_semanal_envio
                              where conta_id=%s and destino='pedro@prime.com'""",
                          (cena["conta"],)).fetchone()
    assert linha and linha[0] is False and linha[1], "a falha não ficou registrada"


def test_conta_desligada_nao_manda_nada(pool, cena, monkeypatch):
    _ligar(pool, cena["conta"], ativo=False)
    from finance import email_inbound as ei
    monkeypatch.setattr(ei, "enviar_conta",
                        lambda *a, **k: pytest.fail("mandou e-mail com a conta desligada"))
    assert rs.enviar_conta(pool, cena["conta"], AGORA)["motivo"] == "desligado"


# ── quando o cron pega cada conta ────────────────────────────────────────────
# Em hora de Brasília, pra ler sem contar nos dedos:
#   12:00 UTC = 09:00 BRT  → a janela da opção "Segunda, 9h"
#   20:00 UTC = 17:00 BRT  → a janela da opção "Sexta, 17h"
SEG_9H = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
SEX_17H = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)
SEX_9H = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
QUA_9H = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def test_o_cron_so_pega_a_conta_no_dia_dela(pool, cena):
    _ligar(pool, cena["conta"], dia="segunda")
    assert cena["conta"] in rs.contas_do_dia(pool, SEG_9H)
    assert rs.contas_do_dia(pool, SEX_17H) == []
    assert rs.contas_do_dia(pool, QUA_9H) == []
    _ligar(pool, cena["conta"], dia="sexta")
    assert cena["conta"] in rs.contas_do_dia(pool, SEX_17H)
    assert rs.contas_do_dia(pool, SEG_9H) == []


def test_quem_escolheu_SEXTA_17H_nao_recebe_as_9H(pool, cena):
    """O defeito de 19/09/2026, e o único caso que distingue o conserto.

    A tela oferece "Sexta, 17h" desde a 274, mas quem decidia era só o cron: um
    disparo às 12:00 UTC e um `contas_do_dia` que olhava apenas o DIA. A conta de
    sexta receberia às 9h — oito horas antes do que a tela prometeu.

    Sexta às 9h é o instante que passa no dia e falha na hora. Testar só "sexta às
    17h funciona" passaria verde com o código velho, que mandava a sexta inteira.
    """
    _ligar(pool, cena["conta"], dia="sexta")
    assert rs.contas_do_dia(pool, SEX_9H) == [], (
        "a conta de sexta foi pega às 9h — a tela promete 17h")
    assert cena["conta"] in rs.contas_do_dia(pool, SEX_17H)


def test_o_disparo_atrasado_ainda_conta(pool, cena):
    """O Render não promete o minuto. Com igualdade exata na hora, um disparo às
    11:58 UTC cairia em 8h de Brasília e NINGUÉM receberia — e o log diria
    "nenhuma conta hoje", que parece normal. A folga de uma hora cobre isso sem
    confundir as duas janelas, que são oito horas distantes."""
    _ligar(pool, cena["conta"], dia="segunda")
    adiantado = datetime(2026, 9, 21, 11, 58, tzinfo=timezone.utc)   # 08:58 BRT
    atrasado = datetime(2026, 9, 21, 13, 5, tzinfo=timezone.utc)     # 10:05 BRT
    assert cena["conta"] in rs.contas_do_dia(pool, adiantado)
    assert cena["conta"] in rs.contas_do_dia(pool, atrasado)
    # mas a folga não é elástica: meio-dia de Brasília não é a janela de ninguém
    meio_dia = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)        # 12:00 BRT
    assert rs.contas_do_dia(pool, meio_dia) == []


def test_a_tela_e_o_cron_dizem_a_MESMA_hora():
    """A tela escreve "Segunda, 9h" e "Sexta, 17h" à mão, no template. Se um dia
    alguém mudar `QUANDO` e esquecer o texto, a tela volta a prometer o que o cron
    não cumpre — que é exatamente o defeito que esta leva conserta."""
    import inspect

    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp)
    miolo = fonte.split('name="resumo_dia"')[1][:500]
    for chave, (_dia, hora) in rs.QUANDO.items():
        assert f"{hora}h" in miolo, (
            f"a opção '{chave}' manda às {hora}h, e a tela não diz isso")


# ------------------------------------------------- o card na engrenagem (§ tela)

def test_o_card_esta_na_aba_do_agente_e_salva_no_mesmo_formulario():
    """Pedido do dono: "coloca lá na engrenagem da prospecção, em Agentes IA".

    E no MESMO formulário, não num segundo "Salvar": a aba é um form só, e duas
    gravações na mesma tela é como se perde configuração — a pessoa mexe nos dois
    e salva um."""
    from web.portal import _env
    import web.painel_prospeccao  # noqa: F401 — registra o template
    tpl = _env.loader.mapping["prospeccao_comunicacao"]
    miolo = tpl.split("{% elif aba=='agente' %}")[1].split("{% elif aba==")[0]
    assert 'name="resumo_semanal"' in miolo, "o interruptor não está na aba do agente"
    assert 'name="resumo_emails"' in miolo, "não dá pra cadastrar e-mail de gestor"
    assert 'name="resumo_dono_emails"' in miolo, "não dá pra cadastrar o e-mail que vê os nomes"
    assert 'name="resumo_dia"' in miolo and 'name="resumo_vendedor"' in miolo
    # um form só: o card fica DENTRO do que posta pra agente-config
    antes = tpl.split('name="resumo_semanal"')[0]
    assert antes.rindex('action="/painel/prospeccao/comunicacao/agente-config"') > \
        antes.rindex("</form>") if "</form>" in antes else True


def test_a_tela_avisa_que_email_cadastrado_nao_e_acesso():
    """Um e-mail numa caixa de texto parece convite. Não é: quem está ali recebe o
    resumo e nada mais. Se a tela não disser isso, alguém vai supor o contrário."""
    from web.portal import _env
    import web.painel_prospeccao  # noqa: F401
    tpl = _env.loader.mapping["prospeccao_comunicacao"]
    miolo = tpl.split("{% elif aba=='agente' %}")[1].split("{% elif aba==")[0]
    assert "não entra no painel" in miolo


def test_a_tela_diz_qual_campo_ve_os_nomes(pool, cena):
    """Dois campos de e-mail um embaixo do outro só funcionam se a tela disser o que
    os separa. Se o texto sumir, a diferença vira adivinhação — e é a diferença
    entre mostrar e não mostrar o resultado de uma pessoa com nome e sobrenome."""
    from web.portal import _env
    import web.painel_prospeccao  # noqa: F401
    tpl = _env.loader.mapping["prospeccao_comunicacao"]
    miolo = tpl.split("{% elif aba=='agente' %}")[1].split("{% elif aba==")[0]
    do_dono = miolo.split('name="resumo_dono_emails"')[1].split('name="resumo_emails"')[0]
    do_gestor = miolo.split('name="resumo_emails"')[1][:900]
    assert "pelo nome" in do_dono, "o campo de cima não diz que mostra os nomes"
    assert "total da equipe" in do_gestor, "o campo de baixo não diz que esconde"


def test_a_rota_do_agente_grava_o_resumo_junto():
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp).split('comunicacao/agente-config")')[1][:5000]
    assert "resumo_semanal" in fonte and "salvar_config" in fonte, (
        "salvar o agente deixou de salvar o resumo — a tela mente sobre o que gravou")
    assert "resumo_dono_emails" in fonte, (
        "o campo que decide quem vê os nomes não está sendo gravado")


# ------------------------------------------- pela caixa da empresa (17/09/2026)
# Decisão do dono ao ver o passo a passo do Render: "é melhor usar o que já
# funciona por dentro do Zaq em vez de configurar toda hora — o Zaq usa o e-mail
# da empresa pra esse tipo de relatório, e no caso da Prime já está configurado".

def _com_caixa(pool, conta):
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id, canal, identificador,
                         imap_host, imap_senha, ativo)
                     values (%s,'email','prime@empresa.com','imap.gmail.com','senha',true)
                     on conflict (conta_id, canal) do nothing""", (conta,))
        c.commit()


def test_sai_pela_caixa_da_EMPRESA_e_nao_pelo_smtp_do_zaq(pool, cena, monkeypatch):
    """É o que apaga um passo inteiro da instalação: o cron passa a precisar só de
    DATABASE_URL, e o resumo chega com o rosto de quem ele fala."""
    _com_caixa(pool, cena["conta"])
    _ligar(pool, cena["conta"])
    pela_empresa, pelo_zaq = [], []
    from finance import email_inbound as ei
    from finance import email_sender as es
    monkeypatch.setattr(ei, "enviar_conta",
                        lambda pool, cid, destino, *a, **kw: pela_empresa.append(destino) or True)
    monkeypatch.setattr(es, "enviar_email",
                        lambda destino, *a, **kw: pelo_zaq.append(destino) or True)
    rs.enviar_conta(pool, cena["conta"], AGORA)
    assert pela_empresa, "não usou a caixa da empresa"
    assert not pelo_zaq, "caiu no SMTP do Zaq tendo caixa própria"


def test_conta_sem_caixa_ainda_recebe_pelo_smtp_do_zaq(pool, cena, monkeypatch):
    """A rede. `email_inbound.enviar_conta` se recusa a cair no SMTP global de
    propósito — mas aquela regra é sobre e-mail que vai pro LEAD, que não pode sair
    da caixa de outra conta. Aqui quem recebe é o dono da própria conta, e um
    resumo que não chega porque a empresa ainda não ligou a caixa seria pior que um
    resumo assinado pelo Zaq."""
    _ligar(pool, cena["conta"])          # sem canais_config: a conta não tem caixa
    pelo_zaq = []
    from finance import email_sender as es
    monkeypatch.setattr(es, "enviar_email",
                        lambda destino, *a, **kw: pelo_zaq.append(destino) or True)
    r = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r["enviados"] >= 2 and pelo_zaq


def test_fica_gravado_POR_ONDE_o_email_saiu(pool, cena, monkeypatch):
    """"De qual caixa isso saiu" é a primeira pergunta quando alguém não recebe."""
    _com_caixa(pool, cena["conta"])
    _ligar(pool, cena["conta"])
    from finance import email_inbound as ei
    monkeypatch.setattr(ei, "enviar_conta", lambda *a, **kw: True)
    rs.enviar_conta(pool, cena["conta"], AGORA)
    with pool.connection() as c:
        motivos = {r[0] for r in c.execute(
            "select motivo from resumo_semanal_envio where conta_id=%s",
            (cena["conta"],)).fetchall()}
    assert motivos == {"caixa da empresa"}
