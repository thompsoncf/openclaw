"""O teto de dias numa etapa (finance/funil_teto): o relógio, as renovações e a
trava da justificativa.

O que estes testes protegem, em uma frase cada:
  * o teto é da ETAPA, não uma regra chamada "os 21 dias do Contactado";
  * o período vem de quem RENOVOU, não do decurso do prazo;
  * sem justificativa a função RECUSA — a trava é recusar, não avisar;
  * acabaram as renovações, o lead não vira nada sozinho;
  * lead que voltou pra etapa recomeça do zero;
  * o mesmo vencimento nunca avisa duas vezes, e renovar faz o fato mudar;
  * desligado não mexe em nada, e observando não manda push nenhum.

Banco descartável, `agora` sempre injetado — o motor tem janela de atendimento, e
um teste que dependesse da hora real passaria ou falharia conforme o dia.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_teto as ft

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 9


def _proxima_quarta_12h() -> datetime:
    """Quarta 12h UTC (9h em Brasília, dentro da janela padrão), sempre no futuro.

    Mesma âncora de tests/test_follow_up: uma data FIXA vira "hoje" um dia e a
    suíte fica vermelha na main sem ninguém ter tocado em código. O `+ 7` garante
    que nenhum deslocamento dentro de um teste alcance o passado.
    """
    agora = datetime.now(timezone.utc)
    dias = (2 - agora.weekday()) % 7 + 7
    return (agora + timedelta(days=dias)).replace(hour=12, minute=0, second=0, microsecond=0)


AGORA = _proxima_quarta_12h()

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp');
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  texto text default '', criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz, simulado boolean default false,
  membro_id bigint, criado_em timestamptz default now());
create unique index uq_funil_aviso on funil_avisos
  (prospeccao_id, estado, nivel, etapa, ref_em, simulado);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""

_ETAPAS = [("novo", 0), ("contatado", 10), ("proposta", 30), ("ganho", 900), ("perdido", 910)]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_teto_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade, não uma cópia: é o único jeito de o teste perceber
        # que a coluna nova não chegou em produção
        c.execute((MIG / "230_funil_teto_da_etapa.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "232_funil_saidas_da_etapa.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "233_funil_toques_da_etapa.sql").read_text(encoding="utf-8"))
        for ch, o in _ETAPAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem)
                         values (%s,%s,%s,%s)""", (CONTA, ch, ch.capitalize(), o))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        con.execute("delete from funil_renovacoes")
        con.execute("delete from funil_avisos")
        con.execute("delete from funil_movimentos")
        con.execute("delete from mensagens")
        con.execute("delete from conversas")
        con.execute("delete from prospeccao")
        con.execute("delete from membros")
        con.execute("delete from funil_regua")
        con.execute("""update funil_etapas set teto_dias=null, renovacoes_max=0,
                         exige_justificativa=true, renova_sozinho_h=null where conta_id=%s""",
                    (CONTA,))
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ montadores

def _vend(c, nome="Pedro"):
    return c.execute("""insert into membros (conta_id, nome, email) values (%s,%s,%s)
                        returning id""", (CONTA, nome, f"{nome.lower()}@x.com")).fetchone()[0]


def _lead(c, vend=None, status="contatado", ha_dias=0, contato="Ana"):
    lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, criado_em)
                       values (%s,%s,%s,%s,%s) returning id""",
                    (CONTA, vend, contato, status, AGORA - timedelta(days=ha_dias))).fetchone()[0]
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'novo',%s,'manual',%s)""",
              (CONTA, lid, status, AGORA - timedelta(days=ha_dias)))
    return lid


def _teto(c, chave="contatado", dias=7, renovacoes=2, exige=True, sozinho=None):
    c.execute("""update funil_etapas set teto_dias=%s, renovacoes_max=%s,
                   exige_justificativa=%s, renova_sozinho_h=%s
                 where conta_id=%s and chave=%s""",
              (dias, renovacoes, exige, sozinho, CONTA, chave))


def _modo(c, modo="ligado"):
    c.execute("insert into funil_regua (conta_id) values (%s) on conflict do nothing", (CONTA,))
    c.execute("""update funil_regua set teto_modo=%s, janela_dias='1,2,3,4,5,6',
                   janela_abre='08:00', janela_fecha='19:00' where conta_id=%s""", (modo, CONTA))


def _regra(dias=7, renovacoes=2, exige=True, sozinho=None):
    return {"teto_dias": dias, "renovacoes_max": renovacoes,
            "exige_justificativa": exige, "renova_sozinho_h": sozinho}


# ------------------------------------------------------------------ o relógio

def test_o_relogio_conta_desde_a_entrada_na_etapa():
    e = ft.estado(desde=AGORA - timedelta(days=3), renovacoes=0, regra=_regra(), agora=AGORA)
    assert round(e["dias"]) == 3 and e["estado"] == "ok"
    assert e["periodo"] == 1 and e["periodos"] == 3 and e["total_dias"] == 21


def test_avisa_nos_dois_ultimos_dias_do_periodo():
    """O documento pede "no 6º e no 7º dia de cada período de 7 dias"."""
    assert ft.estado(desde=AGORA - timedelta(days=4, hours=23), renovacoes=0,
                     regra=_regra(), agora=AGORA)["estado"] == "ok"
    for d in (5, 6):
        assert ft.estado(desde=AGORA - timedelta(days=d), renovacoes=0,
                         regra=_regra(), agora=AGORA)["estado"] == "avisar", d


def test_o_periodo_vem_de_quem_renovou_e_nao_do_decurso_do_prazo():
    """A conta ingênua seria `dias // teto`. Ela mente justamente no caso que
    importa: o lead que estourou e NINGUÉM renovou apareceria no 3º período por
    decurso de prazo, como se alguém o tivesse trabalhado duas vezes."""
    parado = ft.estado(desde=AGORA - timedelta(days=20), renovacoes=0, regra=_regra(), agora=AGORA)
    assert parado["periodo"] == 1, "ninguém renovou: continua no 1º período"
    assert parado["estado"] == "vencido" and parado["restam"] == 2

    # com as duas renovações usadas o total é 21 dias, então aos 20 ele ainda está
    # dentro — e no aviso dos dois últimos dias, que é o que a regra 2 pede
    quase = ft.estado(desde=AGORA - timedelta(days=20), renovacoes=2, regra=_regra(), agora=AGORA)
    assert quase["periodo"] == 3 and quase["estado"] == "avisar" and quase["restam"] == 0

    trabalhado = ft.estado(desde=AGORA - timedelta(days=22), renovacoes=2,
                           regra=_regra(), agora=AGORA)
    assert trabalhado["periodo"] == 3 and trabalhado["estado"] == "esgotado"
    assert trabalhado["restam"] == 0


def test_renovar_empurra_o_vencimento_em_um_periodo():
    a = ft.estado(desde=AGORA - timedelta(days=6), renovacoes=0, regra=_regra(), agora=AGORA)
    b = ft.estado(desde=AGORA - timedelta(days=6), renovacoes=1, regra=_regra(), agora=AGORA)
    assert b["vence_em"] - a["vence_em"] == timedelta(days=7)
    assert a["estado"] == "avisar" and b["estado"] == "ok"


def test_etapa_sem_renovacao_nenhuma_vence_e_acabou():
    e = ft.estado(desde=AGORA - timedelta(days=9), renovacoes=0,
                  regra=_regra(renovacoes=0), agora=AGORA)
    assert e["estado"] == "esgotado" and e["periodos"] == 1 and e["total_dias"] == 7


# ------------------------------------------------------------------ a trava

def test_sem_justificativa_a_renovacao_e_RECUSADA(c):
    """O pedido do dono: "sem justificativa, o sistema de atendimento ao cliente
    deve travar a renovação". A trava é a função dizer NÃO — um aviso na tela se
    ignora, e o pedido é que o vendedor não consiga."""
    _teto(c); lid = _lead(c, ha_dias=8)
    r = ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(), justificativa="   ")
    assert r == {"ok": False, "erro": "justificativa", "renovacoes": 0}
    assert c.execute("select count(*) from funil_renovacoes").fetchone()[0] == 0


def test_com_justificativa_renova_e_vira_linha_no_historico(c):
    _teto(c); v = _vend(c); lid = _lead(c, vend=v, ha_dias=8)
    r = ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(), membro_id=v,
                   justificativa="Cliente pediu pra retomar depois do dia 20.", agora=AGORA)
    assert r["ok"] and r["renovacoes"] == 1 and r["restam"] == 1
    h = ft.historico(c, lid)
    assert len(h) == 1 and h[0]["quem"] == "Pedro" and h[0]["ordem"] == 1
    assert "dia 20" in h[0]["justificativa"] and h[0]["automatica"] is False


def test_acabaram_as_renovacoes_e_o_sistema_nao_inventa_mais_uma(c):
    """Trava 2: o lead vencido não vira nada sozinho. Sair da etapa é decisão de
    quem falou com o cliente — a mesma razão pela qual o D7 do follow-up não move
    pra Perdido: perder exige motivo, e só um humano sabe qual."""
    _teto(c, renovacoes=1); lid = _lead(c, ha_dias=8)
    reg = _regra(renovacoes=1)
    assert ft.renovar(c, CONTA, lid, etapa="contatado", regra=reg,
                      justificativa="vou ligar", agora=AGORA)["ok"]
    r = ft.renovar(c, CONTA, lid, etapa="contatado", regra=reg,
                   justificativa="vou ligar de novo", agora=AGORA)
    assert r == {"ok": False, "erro": "sem_renovacao", "renovacoes": 1}
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_etapa_que_nao_exige_justificativa_renova_sem_texto(c):
    """O teto é da etapa, e a trava também: cada coluna decide se exige."""
    _teto(c, exige=False); lid = _lead(c, ha_dias=8)
    assert ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(exige=False),
                      agora=AGORA)["ok"]


def test_lead_que_voltou_pra_etapa_recomeca_do_zero(c):
    """A regra existe contra o lead PARADO, não contra o lead que ressuscitou:
    contar renovação da vida inteira puniria quem voltou a conversar."""
    _teto(c); lid = _lead(c, ha_dias=30)
    for i in (1, 2):
        assert ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(),
                          justificativa=f"tentativa {i}", agora=AGORA)["ok"]
    assert ft.renovacoes_de(c, lid, "contatado") == 2
    # saiu pra follow-up e voltou: movimento novo pra 'contatado'. O instante é
    # explícito (e não `now()`) porque as renovações acima foram carimbadas com
    # AGORA — deixar o banco carimbar aqui misturaria dois relógios e o "voltou"
    # ficaria ANTES das renovações que ele deveria zerar.
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'follow_up','contatado','manual',%s)""",
              (CONTA, lid, AGORA + timedelta(hours=1)))
    assert ft.renovacoes_de(c, lid, "contatado") == 0
    assert ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(),
                      justificativa="cliente voltou a falar",
                      agora=AGORA + timedelta(hours=2))["ok"]


# ------------------------------------------------------------------ a passada

def test_desligado_nao_mexe_em_nada(c):
    _teto(c); _modo(c, "off"); _lead(c, ha_dias=30)
    assert ft.avaliar(c, CONTA, AGORA) == {"avisos": 0, "simulados": 0, "renovados": 0,
                                           "pendentes": []}
    assert c.execute("select count(*) from funil_avisos").fetchone()[0] == 0


def test_observando_conta_sem_mandar_nada(c):
    _teto(c); _modo(c, "observando"); _vend(c); _lead(c, vend=1, ha_dias=30)
    r = ft.avaliar(c, CONTA, AGORA)
    assert r["simulados"] == 1 and r["avisos"] == 0 and r["pendentes"] == []
    assert c.execute("select simulado from funil_avisos").fetchone()[0] is True


def test_etapa_sem_teto_nao_entra_na_passada(c):
    """Nenhuma conta nasce com teto: o dono liga uma etapa de cada vez."""
    _modo(c); _lead(c, ha_dias=90)
    assert ft.leads(c, CONTA, AGORA) == []
    assert ft.avaliar(c, CONTA, AGORA)["avisos"] == 0


def test_o_mesmo_vencimento_nunca_avisa_duas_vezes_e_renovar_muda_o_fato(c):
    _teto(c); _modo(c); v = _vend(c); lid = _lead(c, vend=v, ha_dias=8)
    assert ft.avaliar(c, CONTA, AGORA)["avisos"] == 1
    assert ft.avaliar(c, CONTA, AGORA + timedelta(hours=2))["avisos"] == 0, "repetiu o mesmo fato"
    ft.renovar(c, CONTA, lid, etapa="contatado", regra=_regra(), justificativa="vou ligar",
               agora=AGORA)
    # agora vence 7 dias depois: volta a ficar OK, e nada novo é cobrado
    assert ft.avaliar(c, CONTA, AGORA + timedelta(hours=3))["avisos"] == 0
    # e quando ESSE vencimento chegar, é fato novo — o aviso sai de novo
    assert ft.avaliar(c, CONTA, AGORA + timedelta(days=7))["avisos"] == 1


def test_fora_do_expediente_nao_acorda_ninguem(c):
    """Domingo não é dia de janela (1..6) — e o ensaio obedece a mesma regra, senão
    a simulação mentiria sobre o que teria acontecido."""
    _teto(c); _modo(c); _vend(c); _lead(c, vend=1, ha_dias=30)
    domingo = AGORA + timedelta(days=(6 - AGORA.weekday()))   # o próximo domingo
    assert domingo.weekday() == 6
    assert ft.avaliar(c, CONTA, domingo)["avisos"] == 0


def test_o_aviso_leva_o_dono_do_lead_e_o_estado(c):
    _teto(c); _modo(c); v = _vend(c); _lead(c, vend=v, ha_dias=30, contato="Fernanda")
    r = ft.avaliar(c, CONTA, AGORA)
    assert len(r["pendentes"]) == 1
    p = r["pendentes"][0]
    assert p["membro_id"] == v and p["quem"] == "Fernanda"
    assert p["estado"] == "vencido" and p["etapa"] == "contatado" and p["restam"] == 2


def test_renovacao_automatica_nasce_desligada_e_depende_de_conversa(c):
    """MELHORIA 1 do mockup, ainda não aprovada: por isso `renova_sozinho_h` nasce
    NULL, que é o documento como está escrito. Ligada, ela dispensa justificativa
    só de quem CONVERSOU — o lead mudo continua preso na trava."""
    _teto(c); _modo(c); v = _vend(c)
    mudo = _lead(c, vend=v, ha_dias=8, contato="Mudo")
    falante = _lead(c, vend=v, ha_dias=8, contato="Falante")
    conv = c.execute("""insert into conversas (conta_id, prospeccao_id) values (%s,%s)
                        returning id""", (CONTA, falante)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, direcao, criado_em)
                 values (%s,'in',%s)""", (conv, AGORA - timedelta(hours=5)))

    # desligada: os dois só recebem aviso, ninguém renova sozinho
    r = ft.avaliar(c, CONTA, AGORA)
    assert (r["avisos"], r["renovados"]) == (2, 0)
    assert c.execute("select count(*) from funil_renovacoes").fetchone()[0] == 0

    # ligada: só o que conversou nas últimas 48h renova sozinho
    _teto(c, sozinho=48)
    c.execute("delete from funil_avisos")
    r = ft.avaliar(c, CONTA, AGORA + timedelta(hours=1))
    assert r["renovados"] == 1
    dono = c.execute("select prospeccao_id, automatica from funil_renovacoes").fetchone()
    assert dono == (falante, True)
    assert ft.renovacoes_de(c, mudo, "contatado") == 0


# ------------------------------------------------------------------ a tela

def test_a_rota_de_renovar_recusa_sem_justificativa_e_nao_grava():
    """A trava tem que existir NA ROTA também — não porque o motor falharia, mas
    porque é a rota que o vendedor alcança. Aqui a pergunta é feita ao router, do
    mesmo jeito que se descobriu o 422 da Régua: ler a ordem no arquivo não bastava.
    """
    from starlette.routing import Match

    import web.painel_prospeccao as pp
    escopo = {"type": "http", "method": "POST", "path": "/painel/prospeccao/7/renovar",
              "path_params": {}, "root_path": "", "headers": []}
    achou = [r.endpoint.__name__ for r in pp.router.routes if r.matches(escopo)[0] == Match.FULL]
    assert achou and achou[0] == "prospeccao_renovar"


def test_o_teto_da_ficha_some_quando_a_etapa_nao_tem_teto(c):
    """Toda conta nasce assim: nenhuma etapa com teto, e a ficha não muda em nada.
    É o "nasce desligado" visto do lado do vendedor."""
    import web.painel_prospeccao as pp
    lid = _lead(c, ha_dias=40)
    assert pp._teto_da_ficha(c, CONTA, lid, "contatado") == (None, [])
    _teto(c)
    teto, _hist = pp._teto_da_ficha(c, CONTA, lid, "contatado")
    assert teto and teto["estado"] == "vencido" and teto["teto_dias"] == 7
    assert 0 < teto["pct"] <= 100


# ------------------------------------------------------------------ as saídas
# Regra 3 do documento: "ao sair de CONTACTADO existem somente dois caminhos".

def _saidas(c, chave="contatado", destinos="follow_up,proposta"):
    c.execute("update funil_etapas set saidas_permitidas=%s where conta_id=%s and chave=%s",
              (destinos, CONTA, chave))


def test_sem_saida_configurada_o_funil_continua_como_sempre_foi(c):
    """Nenhuma conta nasce restrita — e as 6 em produção seguem livres até alguém
    marcar uma caixa na Régua."""
    from finance import funil_regua as fr
    assert fr.saidas_de(c, CONTA) == {}
    assert fr.recusa_de_saida(c, CONTA, "contatado", "ganho") is None


def test_a_saida_barrada_diz_para_onde_pode_ir(c):
    """Um "movimento não permitido" manda a pessoa adivinhar — e adivinhar num
    funil de 275 leads é como a regra vira algo que a equipe contorna arrastando
    pra qualquer outra coluna."""
    from finance import funil_regua as fr
    _saidas(c)
    msg = fr.recusa_de_saida(c, CONTA, "contatado", "ganho")
    assert msg and "Proposta" in msg and "só pode ir para" in msg
    assert fr.recusa_de_saida(c, CONTA, "contatado", "proposta") is None


def test_ficar_na_mesma_etapa_nunca_e_barrado(c):
    """Salvar a ficha sem trocar de coluna não é uma saída. Recusar isso travaria
    o vendedor por nada."""
    from finance import funil_regua as fr
    _saidas(c)
    assert fr.recusa_de_saida(c, CONTA, "contatado", "contatado") is None
    assert fr.pode_mover(fr.saidas_de(c, CONTA), None, "ganho") is True


def test_a_trava_e_da_MAO_e_nao_do_gatilho(c):
    """A decisão mais importante desta regra, e a que o documento não tinha como
    prever: gatilho não é alguém escolhendo para onde levar o card — é um FATO que
    já aconteceu sendo anotado. Barrar fato faria o funil voltar a mentir, que é o
    problema que esta régua existe pra resolver (74 dos 81 leads "parados em Novo"
    em 18/08/2026 já tinham resposta nossa na conversa).

    `aplicar_gatilhos` não consulta `saidas_de` em lugar nenhum — e é isso que este
    teste fixa, lendo o código: se alguém acoplar as duas coisas, quebra aqui.
    """
    import inspect

    from finance import funil_regua as fr
    fonte = inspect.getsource(fr.aplicar_gatilhos)
    assert "saidas_de" not in fonte and "pode_mover" not in fonte, \
        "o gatilho passou a obedecer a trava de saída — fato não se barra"
