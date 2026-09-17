"""O resumo semanal por e-mail (finance/resumo_semanal, migração 274).

O QUE ESTES TESTES PROTEGEM, em uma frase cada:

* "fechou" é CONTRATO ASSINADO, não etapa do funil — o erro que o primeiro
  rascunho cometeu e que ia acusar duas pessoas por e-mail;
* semana sem movimento e sem nada travado NÃO vira e-mail;
* nasce desligado, e a config falha FECHADA (sem banco, não manda);
* o mesmo resumo não sai duas vezes pra ninguém;
* o dono vê nome, o gestor vê total, o vendedor vê só a carteira dele;
* a semana é a de Brasília, e é sempre a FECHADA.
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
        # a migração DE VERDADE: é ela que o Render vai aplicar
        c.execute((MIG / "274_resumo_semanal.sql").read_text(encoding="utf-8"))
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
    from finance import email_sender as es
    monkeypatch.setattr(es, "enviar_email",
                        lambda destino, assunto, html, **kw: mandados.append(destino) or True)
    r1 = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r1["enviados"] >= 2
    n = len(mandados)
    r2 = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r2["enviados"] == 0 and len(mandados) == n


def test_falha_num_destinatario_nao_impede_os_outros(pool, cena, monkeypatch):
    _ligar(pool, cena["conta"])
    from finance import email_sender as es

    def _capenga(destino, assunto, html, **kw):
        if destino == "pedro@prime.com":
            raise RuntimeError("smtp fora")
        return True
    monkeypatch.setattr(es, "enviar_email", _capenga)
    r = rs.enviar_conta(pool, cena["conta"], AGORA)
    assert r["enviados"] >= 2 and "pedro@prime.com" in r["falhas"]
    with pool.connection() as c:
        linha = c.execute("""select ok, motivo from resumo_semanal_envio
                              where conta_id=%s and destino='pedro@prime.com'""",
                          (cena["conta"],)).fetchone()
    assert linha and linha[0] is False and linha[1], "a falha não ficou registrada"


def test_conta_desligada_nao_manda_nada(pool, cena, monkeypatch):
    _ligar(pool, cena["conta"], ativo=False)
    from finance import email_sender as es
    monkeypatch.setattr(es, "enviar_email",
                        lambda *a, **k: pytest.fail("mandou e-mail com a conta desligada"))
    assert rs.enviar_conta(pool, cena["conta"], AGORA)["motivo"] == "desligado"


def test_o_cron_so_pega_a_conta_no_dia_dela(pool, cena):
    _ligar(pool, cena["conta"], dia="segunda")
    segunda = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    sexta = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    quarta = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    assert cena["conta"] in rs.contas_do_dia(pool, segunda)
    assert rs.contas_do_dia(pool, sexta) == []
    assert rs.contas_do_dia(pool, quarta) == []
    _ligar(pool, cena["conta"], dia="sexta")
    assert cena["conta"] in rs.contas_do_dia(pool, sexta)
    assert rs.contas_do_dia(pool, segunda) == []


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


def test_a_rota_do_agente_grava_o_resumo_junto():
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp).split('comunicacao/agente-config")')[1][:5000]
    assert "resumo_semanal" in fonte and "salvar_config" in fonte, (
        "salvar o agente deixou de salvar o resumo — a tela mente sobre o que gravou")
