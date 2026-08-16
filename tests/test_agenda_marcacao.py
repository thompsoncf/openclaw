"""A MARCAÇÃO da data na agenda: fixado × segurado, e as decisões sobre ela.

O calendário já respondia "que tipo de compromisso é" (a bolinha colorida). Pra
quem vende data, a pergunta que vem antes é outra — "essa data já é minha?" — e
ela não estava marcada em lugar nenhum que se enxergasse de longe.

Aqui prende-se o que passou a existir:
  • a marca de estado e o prazo na linha do calendário (_prazo, _monta_semanas);
  • a lista das datas seguradas, que `proximos()` não pode mostrar (ag.pre_reservas);
  • soltar a data antes do prazo (ag.liberar_pre_reserva);
  • as rotas, pelo HTTP — firmar, soltar, marcar segurando, e o choque de horário.

Banco dedicado e descartável, no padrão de tests/test_painel_servicos_sinal.py.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_agenda as pa

CONTA = 5
OUTRA = 6
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


# =========================================================== parte pura: o prazo

class _Rel:
    """Evento mínimo pro _prazo — só o que ele lê."""
    def __new__(cls, horas, status=ag.PRE_RESERVADO):
        return {"status": status,
                "pre_reserva_ate": (ag.agora_brt() + timedelta(hours=horas)
                                    if horas is not None else None)}


def test_prazo_conta_dias_e_vira_horas_no_ultimo_dia():
    agora = ag.agora_brt()
    assert pa._prazo(_Rel(72), agora)["rot"] == "3d"
    assert pa._prazo(_Rel(25), agora)["rot"] == "1d"
    # abaixo de 24h o número muda de unidade E a data vira urgência, não aviso
    p = pa._prazo(_Rel(5), agora)
    assert p["rot"] == "5h" and p["urgente"] is True
    assert pa._prazo(_Rel(23), agora)["urgente"] is True
    assert pa._prazo(_Rel(25), agora)["urgente"] is False


def test_prazo_de_menos_de_uma_hora_nao_vira_zero():
    """"0h" leria como "sem prazo". Quem tem 40 minutos precisa ver que tem algum."""
    assert pa._prazo(_Rel(0.7), ag.agora_brt())["rot"] == "1h"


def test_prazo_vencido_e_compromisso_firme():
    agora = ag.agora_brt()
    assert pa._prazo(_Rel(-1), agora)["rot"] == "vencido"
    # compromisso firme não tem prazo correndo — a linha não mostra número nenhum
    assert pa._prazo(_Rel(48, status="ativo"), agora) == {"rot": "", "horas": None,
                                                          "urgente": False}


def test_celula_do_mes_se_pinta_quando_tem_data_segurada():
    """É o que se enxerga do mês inteiro sem ler linha nenhuma."""
    agora = ag.agora_brt().replace(year=2026, month=8, day=1)
    quando = agora.replace(day=20, hour=21, minute=0, second=0, microsecond=0)
    firme = {"id": 1, "titulo": "Aniversário", "tipo": "empresa", "inicio": quando,
             "status": "ativo", "pre_reserva_ate": None}
    seg = {"id": 2, "titulo": "Formatura", "tipo": "empresa",
           "inicio": quando + timedelta(hours=1), "status": ag.PRE_RESERVADO,
           "pre_reserva_ate": agora + timedelta(hours=6)}
    semanas = pa._monta_semanas(2026, 8, [firme, seg], date(2026, 8, 1), agora)
    celulas = {c["dia"]: c for wk in semanas for c in wk if not c["fora"]}
    dia20 = celulas[20]
    assert dia20["tem_seg"] is True and dia20["urg"] is True     # vence em 6h
    assert [e["pre"] for e in dia20["eventos"]] == [False, True]
    assert dia20["eventos"][1]["prazo"] == "6h"
    assert dia20["eventos"][0]["prazo"] == ""                    # firme não tem prazo
    assert celulas[19]["tem_seg"] is False and celulas[19]["urg"] is False


# =========================================================== banco / rotas

@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_marcacao"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, tipo text, nome text)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Buffet')", (CONTA,))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Vizinha')", (OUTRA,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": "dono"}, None))

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _segurar(c, *, conta_id=CONTA, horas=48, titulo="Formatura — Bia", sinal=None):
    return ag.criar_evento(c.pool, conta_id, titulo, ag.agora_brt() + timedelta(days=20),
                           pre_reserva_ate=ag.agora_brt() + timedelta(hours=horas),
                           sinal_centavos=sinal)


def _status(c, ev_id):
    with c.pool.connection() as cx:
        return cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0]


# ------------------------------------------------------ a lista das seguradas

def test_pre_reservas_vem_da_que_vence_primeiro(cliente):
    _segurar(cliente, horas=72, titulo="Casamento")
    _segurar(cliente, horas=6, titulo="Formatura")
    _segurar(cliente, horas=30, titulo="Bodas")
    ag.criar_evento(cliente.pool, CONTA, "Degustação", ag.agora_brt() + timedelta(days=2))
    assert [e["titulo"] for e in ag.pre_reservas(cliente.pool, CONTA)] == \
        ["Formatura", "Bodas", "Casamento"]


def test_pre_reservas_nao_atravessa_conta(cliente):
    _segurar(cliente, conta_id=OUTRA, titulo="Da vizinha")
    assert ag.pre_reservas(cliente.pool, CONTA) == []


def test_pre_reservas_sem_orcamentos_no_banco_nao_quebra(cliente):
    """A agenda roda em conta que nem tem o módulo de orçamentos. Um join
    obrigatório faria a tela inteira cair por causa de um enfeite."""
    ev = _segurar(cliente, sinal=181000)
    linha = ag.pre_reservas(cliente.pool, CONTA)[0]
    assert linha["id"] == ev["id"] and linha["sinal_centavos"] == 181000
    assert linha.get("orcamento_id") is None


# ------------------------------------------------------ soltar antes do prazo

def test_liberar_solta_a_data_e_e_idempotente(cliente):
    ev = _segurar(cliente)
    assert ag.liberar_pre_reserva(cliente.pool, CONTA, ev["id"]) is True
    assert _status(cliente, ev["id"]) == "cancelado"
    assert ag.liberar_pre_reserva(cliente.pool, CONTA, ev["id"]) is False


def test_liberar_nao_encosta_em_compromisso_firme(cliente):
    """Botão mal ligado não pode cancelar compromisso de verdade — esse tem caminho
    próprio, com confirmação e aviso pros convidados."""
    ev = ag.criar_evento(cliente.pool, CONTA, "Casamento fechado",
                         ag.agora_brt() + timedelta(days=9))
    assert ag.liberar_pre_reserva(cliente.pool, CONTA, ev["id"]) is False
    assert _status(cliente, ev["id"]) == "ativo"


def test_liberar_nao_atravessa_conta(cliente):
    ev = _segurar(cliente, conta_id=OUTRA)
    assert ag.liberar_pre_reserva(cliente.pool, OUTRA + 99, ev["id"]) is False
    assert _status(cliente, ev["id"]) == ag.PRE_RESERVADO


# ------------------------------------------------------ as rotas, pelo HTTP

def test_rota_liberar_solta_a_data(cliente):
    ev = _segurar(cliente)
    r = cliente.post("/painel/agenda/liberar",
                     data={"evento_id": ev["id"], "m": "2026-08"})
    assert r.status_code == 303 and "/painel/agenda?m=2026-08" in r.headers["location"]
    assert _status(cliente, ev["id"]) == "cancelado"


def test_rota_sinal_recebido_firma_a_data_sem_orcamento(cliente):
    """Pré-reserva nascida na própria agenda não tem orçamento: aí só firma a data,
    que é tudo que existe pra firmar."""
    ev = _segurar(cliente)
    r = cliente.post("/painel/agenda/sinal-recebido",
                     data={"evento_id": ev["id"], "m": ""})
    assert r.status_code == 303
    with cliente.pool.connection() as cx:
        assert cx.execute("select status, pre_reserva_ate from eventos_agenda where id=%s",
                          (ev["id"],)).fetchone() == ("ativo", None)


def test_rota_conflitos_lista_o_que_ja_esta_marcado(cliente):
    """O choque de horário passa a existir NA TELA, na hora de marcar — antes só
    saía por Telegram, e só quando vinha de aprovação de orçamento."""
    dia = (ag.agora_brt() + timedelta(days=6)).date().isoformat()
    inicio, fim = ag.janela_evento(dia, "19:00", "23:00")
    ag.criar_evento(cliente.pool, CONTA, "Aniversário 15 anos", inicio, fim=fim)
    r = cliente.get(f"/painel/agenda/conflitos?data={dia}&hora=21:00")
    itens = r.json()["itens"]
    assert [i["titulo"] for i in itens] == ["Aniversário 15 anos"]
    assert itens[0]["pre"] is False
    # fora da janela não acusa nada
    assert cliente.get(f"/painel/agenda/conflitos?data={dia}&hora=08:00").json()["itens"] == []


def test_conflitos_marca_a_data_segurada_como_segurada(cliente):
    dia = (ag.agora_brt() + timedelta(days=7)).date().isoformat()
    inicio, fim = ag.janela_evento(dia, "19:00", "23:00")
    ag.criar_evento(cliente.pool, CONTA, "Segurada", inicio, fim=fim,
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=2))
    itens = cliente.get(f"/painel/agenda/conflitos?data={dia}&hora=20:00").json()["itens"]
    assert itens[0]["pre"] is True


# ------------------------------------------------------ marcar segurando

def _marcar(c, **extra):
    dados = {"titulo": "Formatura — Bia", "data": (ag.agora_brt() + timedelta(days=25)).date().isoformat(),
             "hora": "21:00", "local": "", "descricao": "", "tipo": "empresa",
             "link_online": "", "m": ""}
    dados.update(extra)
    return c.post("/painel/agenda/novo", data=dados)


def _ultimo(c):
    with c.pool.connection() as cx:
        return cx.execute("select id, status, pre_reserva_ate, sinal_centavos "
                          "from eventos_agenda order by id desc limit 1").fetchone()


def test_marcar_normal_continua_nascendo_firme(cliente):
    """Regressão: o caminho de sempre não muda."""
    assert _marcar(cliente).status_code == 303
    _id, status, ate, sinal = _ultimo(cliente)
    assert status == "ativo" and ate is None and sinal is None


def test_so_segurar_a_data_nasce_pre_reservada_com_o_prazo_da_conta(cliente):
    """O telefonema "segura o dia 20 pra mim": antes o dono só podia marcar firme
    (mentira) ou não marcar (e vender a data duas vezes)."""
    ag.salvar_pre_reserva_dias(cliente.pool, CONTA, 5)
    assert _marcar(cliente, segurar="1", sinal_esperado="1.810,00").status_code == 303
    _id, status, ate, sinal = _ultimo(cliente)
    assert status == ag.PRE_RESERVADO and sinal == 181000
    assert (ate - ag.agora_brt()).days == 4      # 5 dias cheios, arredondando pra baixo


def test_segurar_ate_a_data_escolhida_vale_o_dia_todo(cliente):
    """Quem digita 17/08 quer o dia 17 inteiro, não 17 às 00:00 — que já nasceria
    vencido pra quem marca de tarde."""
    ate_txt = (ag.agora_brt() + timedelta(days=2)).date().isoformat()
    _marcar(cliente, segurar="1", segurar_ate=ate_txt)
    _id, status, ate, _s = _ultimo(cliente)
    assert status == ag.PRE_RESERVADO
    assert ate.astimezone(ag.BRT).strftime("%Y-%m-%d %H:%M") == f"{ate_txt} 23:59"


def test_segurar_sem_valor_de_sinal_e_permitido(cliente):
    """O valor é opcional: nem todo mundo combina número na hora do telefonema."""
    _marcar(cliente, segurar="1", sinal_esperado="")
    _id, status, _ate, sinal = _ultimo(cliente)
    assert status == ag.PRE_RESERVADO and sinal is None


def test_centavos_le_o_que_o_dono_digita():
    assert pa._centavos("1.810,00") == 181000
    assert pa._centavos("R$ 1.810") == 181000
    assert pa._centavos("1810") == 181000
    assert pa._centavos("") is None and pa._centavos("abc") is None
    assert pa._centavos("0") is None          # zero não é sinal
