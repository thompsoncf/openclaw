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
import re
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


# ====================================================== o pagamento na linha

def _fic(**kw):
    base = {"pct": 0, "vencidas": 0}
    base.update(kw)
    return base


def test_pagamento_na_linha_tem_uma_cor_por_situacao():
    """Duas festas no mesmo mês, uma quitada e outra sem um centavo, apareciam
    idênticas. Verde = quitado, âmbar = em parte, coral = tem parcela vencida (é o
    que exige ação, e por isso vence a regra do percentual)."""
    assert pa._pg_estado(_fic(pct=100)) == {"rot": "100%", "classe": "ok"}
    assert pa._pg_estado(_fic(pct=40)) == {"rot": "40%", "classe": "mid"}
    assert pa._pg_estado(_fic(pct=0)) == {"rot": "0%", "classe": "mid"}
    # vencida manda, mesmo com quase tudo pago
    assert pa._pg_estado(_fic(pct=90, vencidas=1)) == {"rot": "90%", "classe": "bad"}
    assert pa._pg_estado(_fic(pct=100, vencidas=1))["classe"] == "bad"


def test_sem_titulo_a_linha_nao_mostra_percentual_nenhum():
    """Contrato não fechado não tem cobrança: "0%" leria como calote quando ninguém
    cobrou ainda. E compromisso sem orçamento não tem o que medir."""
    assert pa._pg_estado(_fic(pct=None)) == {"rot": "", "classe": ""}
    assert pa._pg_estado(None) == {"rot": "", "classe": ""}


def test_ficha_formatada_vira_texto_e_some_quando_nao_existe():
    assert pa._ficha_rot(None) is None
    f = pa._ficha_rot({"orcamento_id": 9, "numero": 60, "status": "fechado",
                       "cliente": "Maria", "contato": "(86) 99999-0000",
                       "tipo": "Aniversário", "convidados": "50",
                       "itens": [{"nome": "DJ", "qtd": 1}], "total_centavos": 745000,
                       "tem_titulos": True, "pct": 23, "pago_centavos": 181000,
                       "titulos_centavos": 781000, "vencidas": 0,
                       "proxima": {"vencimento": date(2026, 9, 13),
                                   "valor_centavos": 600000, "vencida": False}})
    assert f["total"] == "R$ 7.450,00" and f["pago"] == "R$ 1.810,00"
    assert f["convidados"] == 50          # veio como string do jsonb, sai como número
    assert f["prox_valor"] == "R$ 6.000,00" and f["prox_venc"] == "13/09"
    assert f["fechado"] is True


# ====================================================== o nicho manda na tela
# A marca de estado, a legenda que fala de sinal e o "Só segurar a data" são
# vocabulário de quem VENDE DATA. Clínica, loja e escritório não seguram horário
# esperando sinal: pra eles a Agenda continua exatamente como estava.

def _render_agenda(vende_data: bool) -> str:
    from web.portal import _env
    agora = ag.agora_brt()
    cfg = {"lembrete_ativo": False, "resumo_ativo": False, "hora_resumo": 7,
           "aviso_antes_min": None, "feed_token": None, "avisar_convidados": True,
           "enviar_confirmacao": True, "pre_reserva_dias": 3}
    semanas = [[{"dia": d, "fora": False, "hoje": False, "iso": f"2026-08-{d:02d}",
                 "eventos": [], "tem_seg": False, "urg": False} for d in range(15, 22)]]
    return _env.get_template("agenda").render(
        titulo="Agenda", secao_ativa="agenda", historico=[], historico_total=0, fila=[],
        ano=2026, mes=8, mes_nome="Agosto", dias_sem=pa.DIAS_SEM, semanas=semanas,
        proximos=[], tipo_rot=pa.TIPO_ROT, eventos_dia={}, status_rot={}, reaproveitar=[],
        meses_js=pa.MESES, dias_sem_ext_js=pa.DIAS_SEM_EXT, agora_iso=agora.isoformat(),
        mes_prev="2026-07", mes_next="2026-09", mes_hoje="2026-08", hoje_iso="2026-08-01",
        abrir_novo=True, cfg=cfg, feed_url="", share=None, seguradas=[],
        confirmadas=[], vende_data=vende_data,
        rot=(pa._ROT_EVENTO if vende_data else pa._ROT_PADRAO),
        aviso="", ctx={}, conta={}, request=None)


def test_ficha_do_evento_so_e_buscada_pra_quem_vende_data(cliente):
    """Duas consultas a mais por abertura da Agenda. Fora do nicho de eventos não
    existe orçamento de festa pra ler — então nem se pergunta."""
    chamou = []
    fake = type("V", (), {
        "vende_data": staticmethod(lambda p, c: cliente.estado_nicho["vende"]),
        "fichas_de_eventos": staticmethod(
            lambda p, c, ids: (chamou.append(list(ids)) or {})),
    })
    import web.painel_agenda as _pa
    orig = _pa._vendas
    _pa._vendas = lambda: fake
    try:
        quando = ag.agora_brt() + timedelta(days=3)
        ag.criar_evento(cliente.pool, CONTA, "Festa", quando)
        # `?m=` prende o teste no mês do evento criado — sem isso, "hoje + 3 dias"
        # perto do fim do mês cai no mês seguinte, a página abre no mês ERRADO (o
        # padrão sem `m` é sempre "hoje"), `eventos` vem vazio e a ficha nunca é
        # pedida. Já aconteceu de verdade: rodando em 29/08, o evento nascia em
        # 01/09 e o teste falhava sem nenhuma mudança de código.
        mes_do_evento = f"{quando.year:04d}-{quando.month:02d}"
        cliente.estado_nicho["vende"] = True
        cliente.get(f"/painel/agenda?m={mes_do_evento}")
        assert len(chamou) == 1 and chamou[0]          # perguntou, com os ids do mês
        cliente.estado_nicho["vende"] = False
        cliente.get(f"/painel/agenda?m={mes_do_evento}")
        assert len(chamou) == 1                        # não perguntou de novo
    finally:
        _pa._vendas = orig


def test_o_formulario_fala_a_lingua_do_nicho():
    """Um buffet não marca "compromisso": marca EVENTO. E "O que é / Ex: reunião com
    o contador" não é a pergunta que ele faz. Mesmo formulário, mesma rota, mesmo
    dado — muda o que a pessoa lê."""
    ev, outros = _render_agenda(True), _render_agenda(False)
    for frag in ("＋ Novo evento", "<h2>Novo evento</h2>", "Ex: Casamento — Ana e Pedro",
                 # "Próximos eventos" virou "🚶 Visitas": quem vende data ganhou card
                 # próprio pra festa (Reservado) e pra data segurada (Pré-reserva), e
                 # o que sobra neste é a visita ao espaço, a degustação, o fornecedor.
                 "Data da festa", "Onde vai ser", "Marcar evento", "🚶 Visitas",
                 "Quem participa"):
        assert frag in ev and frag not in outros
    for frag in ("＋ Novo compromisso", "<h2>Novo compromisso</h2>",
                 "Ex: reunião com o contador", "Próximos compromissos"):
        assert frag in outros and frag not in ev


def test_os_valores_do_tipo_nao_mudam_com_a_palavra():
    """"Festa" é o rótulo; `empresa` continua sendo o valor gravado. A coluna tem
    check e o resto do sistema (cor do ponto, cockpit, .ics) lê por ele."""
    ev = _render_agenda(True)
    assert 'name="tipo" value="empresa"' in ev and ">Festa<" in ev
    assert 'name="tipo" value="pessoal"' in ev and ">Interno<" in ev
    assert ">Empresa<" not in ev
    # e o padrão marcado muda: num buffet o que se marca é festa, não compromisso interno
    assert 'value="empresa" checked' in ev
    assert 'value="pessoal" checked' in _render_agenda(False)


def test_evento_pergunta_a_hora_de_encerrar():
    """O orçamento de evento sempre perguntou; a agenda nunca. Sem o fim, a festa
    entra como compromisso de 1h e a checagem de choque compara a janela errada —
    duas festas na mesma noite não acusavam nada."""
    ev, outros = _render_agenda(True), _render_agenda(False)
    assert 'name="hora_fim"' in ev and "Encerra" in ev
    assert 'name="hora_fim"' not in outros
    # e o horário padrão acompanha o ramo
    assert 'id="fHora" type="time" value="19:00"' in ev
    assert 'id="fHora" type="time" value="09:00"' in outros


def test_so_o_nicho_de_eventos_ve_o_vocabulario_de_data_segurada():
    ev, outros = _render_agenda(True), _render_agenda(False)
    for frag in ('class="cal marca-estado"',        # a barra fixado/segurado
                 'name="segurar"',                   # o toggle "Só segurar a data"
                 "A barra da esquerda diz se a data é sua"):   # a legenda nova
        assert frag in ev
        assert frag not in outros


def test_agenda_dos_outros_nichos_segue_como_estava():
    outros = _render_agenda(False)
    assert "As cores separam" in outros          # a legenda de sempre, intacta
    assert "＋ Novo compromisso" in outros
    # nada de "sinal"/"segurada" no que a pessoa LÊ. Comentário de HTML e CSS são
    # fonte, não tela — o que se mede aqui é o texto renderizado.
    corpo = outros.split("<script")[0]
    corpo = re.sub(r"<style.*?</style>", "", corpo, flags=re.S)
    corpo = re.sub(r"<!--.*?-->", "", corpo, flags=re.S).lower()
    assert "sinal" not in corpo and "segurada" not in corpo
    # o aviso de choque de horário NÃO é de nicho nenhum: some sozinho quando não há
    # conflito, e marcar duas coisas no mesmo horário é problema de qualquer agenda.
    assert "choqueBox" in outros


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
        c.execute("create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Buffet')", (CONTA,))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Vizinha')", (OUTRA,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": "dono"}, None))
    # a conta do teste VENDE DATA (nicho de eventos). O schema mínimo daqui não tem
    # as tabelas da empresa, então o nicho vem por este atalho — e um teste abaixo
    # o desliga, pra provar que conta de outro nicho não segura data nem por POST.
    estado = {"vende": True}

    class _VendasFake:
        @staticmethod
        def vende_data(pool_, conta_id_):
            return estado["vende"]

        @staticmethod
        def fichas_de_eventos(pool_, conta_id_, ids):
            # o schema mínimo daqui não tem `orcamentos`; a ficha é leitura extra e
            # a tela abre sem ela — o que interessa aqui é a grade e os eventos.
            return {}

    monkeypatch.setattr(pa, "_vendas", lambda: _VendasFake)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    c.estado_nicho = estado
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


def test_a_pagina_decide_o_nicho_pelo_nicho_da_conta(cliente):
    """O gate tem que estar LIGADO na página, não só existir. Sem esta ida pelo
    GET, apagar a linha que calcula `vende_data` passaria batido — a tela voltaria
    a mostrar barra e toggle pra todo mundo."""
    r = cliente.get("/painel/agenda")
    assert r.status_code == 200
    assert 'class="cal marca-estado"' in r.text and 'name="segurar"' in r.text
    # e o VOCABULÁRIO vem do mesmo gate — sem isto, trocar a linha que escolhe os
    # rótulos passaria batido e o buffet voltaria a "Novo compromisso".
    assert "＋ Novo evento" in r.text and 'name="hora_fim"' in r.text
    cliente.estado_nicho["vende"] = False
    r2 = cliente.get("/painel/agenda")
    assert r2.status_code == 200
    assert 'class="cal marca-estado"' not in r2.text and 'name="segurar"' not in r2.text
    assert "＋ Novo compromisso" in r2.text and 'name="hora_fim"' not in r2.text


def test_hora_de_encerrar_vira_o_fim_do_compromisso(cliente):
    """E herda a regra do ramo por janela_evento: 'encerra às 24' acaba 00:00 do dia
    seguinte, e virar a noite (19h→02h) rola o dia."""
    dia = (ag.agora_brt() + timedelta(days=25)).date()
    _marcar(cliente, data=dia.isoformat(), hora="19:00", hora_fim="23:00")
    with cliente.pool.connection() as cx:
        ini, fim = cx.execute("select inicio, fim from eventos_agenda "
                              "order by id desc limit 1").fetchone()
    assert fim is not None
    assert fim.astimezone(ag.BRT).strftime("%d/%m %H:%M") == dia.strftime("%d/%m") + " 23:00"
    # vira a noite: 19h -> 02h termina no dia seguinte
    _marcar(cliente, data=dia.isoformat(), hora="19:00", hora_fim="02:00")
    with cliente.pool.connection() as cx:
        fim2 = cx.execute("select fim from eventos_agenda order by id desc limit 1").fetchone()[0]
    assert fim2.astimezone(ag.BRT).day == (dia + timedelta(days=1)).day


def test_sem_hora_de_encerrar_o_compromisso_segue_pontual(cliente):
    """Regressão: quem não preenche (ou não vê o campo) marca como sempre marcou."""
    _marcar(cliente)
    with cliente.pool.connection() as cx:
        assert cx.execute("select fim from eventos_agenda order by id desc limit 1").fetchone()[0] is None


def test_conta_de_outro_nicho_nao_segura_data_nem_por_post(cliente):
    """A tela não oferece o toggle fora do nicho de eventos — mas a tela não é fonte
    confiável. O servidor recusa igual: o compromisso nasce firme, como sempre."""
    cliente.estado_nicho["vende"] = False
    assert _marcar(cliente, segurar="1", sinal_esperado="1.810,00").status_code == 303
    _id, status, ate, sinal = _ultimo(cliente)
    assert status == "ativo" and ate is None and sinal is None


def test_centavos_le_o_que_o_dono_digita():
    assert pa._centavos("1.810,00") == 181000
    assert pa._centavos("R$ 1.810") == 181000
    assert pa._centavos("1810") == 181000
    assert pa._centavos("") is None and pa._centavos("abc") is None
    assert pa._centavos("0") is None          # zero não é sinal


# ================================================ trocar de mês sem recarregar
#
# A seta era um <a href> comum: dezesseis consultas ao banco e a página inteira
# remontada, quando a maior parte daquilo devolvia o mesmo resultado — próximas
# visitas, configuração de lembrete, prazo da reserva, histórico, fila. Nada
# disso muda de mês.
#
# /painel/agenda/mes devolve só o mês. Quem desenha as células continua sendo o
# `renderizarCelula` do navegador, com os dados daqui.

def test_o_mes_vem_em_json_com_a_grade_e_os_eventos(cliente):
    ev = ag.criar_evento(cliente.pool, CONTA, "Casamento",
                         ag.agora_brt().replace(day=15) + timedelta(days=0))
    m = ag.agora_brt().strftime("%Y-%m")
    d = cliente.get(f"/painel/agenda/mes?m={m}").json()
    assert d["m"] == m and d["mes_nome"]
    # a grade vem SEM os eventos: as células se preenchem no navegador, pela mesma
    # função que já redesenhava célula depois de cancelar e remarcar
    celula = d["dias"][0][0]
    assert set(celula) == {"dia", "fora", "hoje", "iso"}
    iso = ev["inicio"].astimezone(ag.BRT).date().isoformat()
    assert [e["id"] for e in d["eventos_dia"][iso]["eventos"]] == [ev["id"]]


def test_a_seta_sabe_pra_onde_ir_depois(cliente):
    """Sem `mes_prev`/`mes_next` na resposta, a segunda seta apontaria pro mesmo
    lugar da primeira — e o mês travaria depois de um clique."""
    d = cliente.get("/painel/agenda/mes?m=2026-09").json()
    assert d["mes_prev"] == "2026-08" and d["mes_next"] == "2026-10"


def test_o_mes_nao_traz_o_que_nao_muda_de_mes(cliente):
    """Os três cards de baixo falam do que vem por aí, não do mês na tela. Mandar
    de volta seria refazer consulta pra sobrescrever a tela com o mesmo dado — que
    é exatamente o custo que esta rota existe pra cortar."""
    d = cliente.get("/painel/agenda/mes?m=2026-09").json()
    for chave in ("seguradas", "confirmadas", "proximos", "cfg", "historico", "fila"):
        assert chave not in d


def test_a_data_segurada_chega_com_o_vinculo_do_orcamento(cliente):
    """A caixa do dia usa isso pra oferecer "Sinal recebido" e "Ver orçamento".
    Sem o vínculo, abrir uma data segurada depois de trocar de mês daria uma caixa
    sem os botões — e pareceria que a data perdeu o orçamento."""
    ev = _segurar(cliente, horas=48, sinal=150000)
    m = ev["inicio"].astimezone(ag.BRT).strftime("%Y-%m")
    iso = ev["inicio"].astimezone(ag.BRT).date().isoformat()
    d = cliente.get(f"/painel/agenda/mes?m={m}").json()
    linha, = d["eventos_dia"][iso]["eventos"]
    assert linha["pre"] is True and linha["sinal"] == "R$ 1.500,00"


def test_o_mes_e_da_conta_logada(cliente):
    ag.criar_evento(cliente.pool, OUTRA, "Festa da vizinha", ag.agora_brt() + timedelta(days=1))
    m = (ag.agora_brt() + timedelta(days=1)).strftime("%Y-%m")
    d = cliente.get(f"/painel/agenda/mes?m={m}").json()
    titulos = [e["titulo"] for dia in d["eventos_dia"].values() for e in dia["eventos"]]
    assert "Festa da vizinha" not in titulos


# ============================================ os três cards embaixo do calendário

def test_quem_vende_data_ve_os_tres_cards_fora_da_coluna_lateral():
    """Antes eram um card de abas (uma escondia a outra) e um card de próximos, os
    dois espremidos na coluna estreita. Lado a lado e na largura inteira, as três
    respostas se leem juntas — que é como a empresa pensa a semana."""
    ev = _render_agenda(True)
    for titulo in ("🚶 Visitas", "⏳ Pré-reserva", "✓ Reservado"):
        assert titulo in ev
    # e ficam FORA do .ag-grid — quem for mover de volta pra lateral quebra aqui
    assert ev.index('class="ag-tres"') > ev.index('class="ag-grid"')
    assert ev.index('class="ag-tres"') > ev.index('class="side-cards"')


def test_fora_do_nicho_de_eventos_a_lateral_continua_como_sempre():
    """Numa clínica não existe pré-reserva nem festa: três cards ali seriam dois
    vazios pra sempre e um renomeado sem motivo."""
    outros = _render_agenda(False)
    assert "ag-tres" not in outros
    assert "Próximos compromissos" in outros
    assert "Pré-reserva" not in outros and "Reservado" not in outros


def test_visitas_nao_repete_a_festa_que_ja_esta_em_reservado(cliente):
    """Sem o filtro, num mês cheio as festas ocupavam as oito linhas e a visita de
    amanhã não aparecia — além de a mesma data sair em dois cards."""
    base = ag.agora_brt()
    ag.criar_evento(cliente.pool, CONTA, "Casamento", base + timedelta(days=5), tipo="empresa")
    ag.criar_evento(cliente.pool, CONTA, "Visita — Bia", base + timedelta(days=1), tipo="pessoal")
    ag.criar_evento(cliente.pool, CONTA, "Buffet Sabor", base + timedelta(days=2), tipo="fornecedor")
    visitas = ag.proximos(cliente.pool, CONTA, limite=8, tipos=("pessoal", "fornecedor"))
    assert [e["titulo"] for e in visitas] == ["Visita — Bia", "Buffet Sabor"]
    assert [e["titulo"] for e in ag.confirmadas(cliente.pool, CONTA)] == ["Casamento"]
