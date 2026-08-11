"""Motor de lembretes da agenda (resumo do dia + aviso antes).

Não manda Telegram de verdade: monkeypatcha notificar.enviar_para_dono pra capturar
o que sairia. Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance import convites as cv
from finance import lembretes as lb
from finance import notificar


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                    "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                    "128_lembretes_aviso_convidado.sql", "130_evento_desfecho.sql",
                    "131_evento_link_online.sql",
                    "132_convidado_canal_resposta.sql"):
            c.execute((migr / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _isola(pool):
    """rodar() varre TODAS as contas (certo em produção). No teste, limpa as tabelas
    da agenda antes de cada caso pra config/eventos de um teste não vazarem pro outro."""
    with pool.connection() as c:
        c.execute("truncate table lembretes_enviados, agenda_config, eventos_agenda "
                  "restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pf','Teste Lembrete') returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def enviados(monkeypatch):
    """Captura o que sairia pro dono (em vez de mandar Telegram)."""
    saidas = []
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, conta_id, texto: (saidas.append((conta_id, texto)) or True))
    return saidas


def test_resumo_do_dia_manda_uma_vez(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=3, second=0, microsecond=0)
    # 2 eventos hoje
    ag.criar_evento(pool, conta_id, "Dentista", agora.replace(hour=10))
    ag.criar_evento(pool, conta_id, "Reunião", agora.replace(hour=15), local="Online")
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=None)
    r = lb.rodar(pool, agora=agora)
    assert r["resumo"] == 1 and len(enviados) == 1
    txt = enviados[0][1]
    assert "agenda de hoje" in txt and "Dentista" in txt and "Reunião" in txt
    # roda de novo na mesma hora -> NÃO repete (dedup por dia)
    lb.rodar(pool, agora=agora.replace(minute=40))
    assert len(enviados) == 1


def test_resumo_nao_manda_fora_da_hora_nem_sem_evento(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=0, second=0, microsecond=0)
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=None)
    # sem eventos hoje -> nada
    assert lb.rodar(pool, agora=agora)["resumo"] == 0 and enviados == []
    # com evento, mas fora da hora do resumo -> nada
    ag.criar_evento(pool, conta_id, "Almoço", agora.replace(hour=12))
    assert lb.rodar(pool, agora=agora.replace(hour=9))["resumo"] == 0 and enviados == []


def test_aviso_antes_dispara_na_janela_uma_vez(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Consulta", agora + timedelta(minutes=20), local="Clínica")
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    r = lb.rodar(pool, agora=agora)                 # evento a 20 min, janela 30 -> avisa
    assert r["aviso"] == 1 and len(enviados) == 1
    assert "Consulta" in enviados[0][1] and "min" in enviados[0][1]
    lb.rodar(pool, agora=agora.replace(minute=5))   # de novo -> dedup por evento
    assert len(enviados) == 1


def test_aviso_dono_falha_na_primeira_tentativa_tenta_de_novo(pool, conta_id, monkeypatch):
    """Bug relatado em produção: se o Telegram falhar (rede, fora do ar) na
    primeira vez que o evento entra na janela, o aviso pro DONO não pode ficar
    queimado pra sempre — o próximo ciclo (~2min depois, evento ainda na janela)
    tem que tentar de novo. Mesmo bug de dedup-on-attempt já corrigido pro
    aviso ao convidado, mas aqui no aviso que vai pro dono via Telegram."""
    tentativas = []
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, conta_id, texto: (tentativas.append(texto), False)[1])
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Reunião com Paulo", agora + timedelta(minutes=20), local="Sala 2")
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    r = lb.rodar(pool, agora=agora)                          # Telegram falha -> não marca como enviado
    assert r["aviso"] == 0 and len(tentativas) == 1
    with pool.connection() as c:
        assert c.execute("select 1 from lembretes_enviados where tipo='aviso'").fetchone() is None

    # "Telegram volta" e o próximo ciclo (2min depois, evento ainda na janela) tenta de novo
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, conta_id, texto: (tentativas.append(texto), True)[1])
    r2 = lb.rodar(pool, agora=agora.replace(minute=2))
    assert r2["aviso"] == 1 and len(tentativas) == 2
    assert "Paulo" in tentativas[1]


def test_aviso_nao_dispara_fora_da_janela(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Longe", agora + timedelta(minutes=90))
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    assert lb.rodar(pool, agora=agora)["aviso"] == 0 and enviados == []


def test_convidado_confirmado_recebe_aviso_dentro_da_janela(pool, conta_id, enviados, monkeypatch):
    """Reproduz o bug de produção: tipo='aviso_convidado' violava o check de
    lembretes_enviados (nunca tinha sido adicionado), e a exceção subia até
    rodar() — não só o convidado ficava sem aviso, o ciclo inteiro abortava."""
    from finance import whatsapp_out as wout
    livres = []
    monkeypatch.setattr(wout, "enviar",
                        lambda c, cid, numero, texto: (livres.append((numero, texto)) or {"ok": True}))
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ev = ag.criar_evento(pool, conta_id, "Reunião com Ana", agora + timedelta(minutes=20))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    # canal="whatsapp": só uma confirmação de VERDADE pelo WhatsApp abre a janela de
    # 24h — confirmar pela página pública (canal padrão "web") não abre sessão nenhuma.
    cv.responder(pool, conv["token"], "confirmado", canal="whatsapp")
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30,
                     avisar_convidados=True)
    r = lb.rodar(pool, agora=agora)                   # não pode levantar CheckViolation
    assert r["aviso"] == 2                             # 1 pro dono + 1 pro convidado
    assert len(enviados) == 1 and len(livres) == 1
    assert livres[0][0] == "86999990000"
    with pool.connection() as c:
        row = c.execute("select 1 from lembretes_enviados where tipo='aviso_convidado'").fetchone()
    assert row is not None


def test_convidado_confirmado_pela_web_usa_template_nao_texto_livre(pool, conta_id, monkeypatch):
    """Bug relatado em produção: convidado confirmava pela página pública
    (/convite/<token>, sem login) e nunca recebia o aviso antes da reunião,
    mesmo com o template do lembrete aprovado e funcionando. Causa: respondido_em
    ficava "recente" mesmo sem NENHUMA sessão de WhatsApp aberta (confirmar pela
    web não é mensagem nenhuma), então o sistema tentava texto livre — que o
    WhatsApp recusa fora de sessão de verdade — em vez do template que funcionaria."""
    from finance import whatsapp_out as wout
    livres, templates = [], []
    monkeypatch.setattr(wout, "enviar",
                        lambda c, cid, numero, texto: (livres.append(numero) or {"ok": True}))
    monkeypatch.setattr(wout, "enviar_template",
                        lambda c, cid, numero, sid, variaveis: (templates.append((numero, sid)) or {"ok": True}))
    monkeypatch.setenv("TWILIO_TMPL_LEMBRETE_SID", "HXlembretetest")
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ev = ag.criar_evento(pool, conta_id, "Reunião com Ana", agora + timedelta(minutes=20))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    cv.responder(pool, conv["token"], "confirmado")   # canal padrão "web" — confirmação AGORA MESMO
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30,
                     avisar_convidados=True)
    lb.rodar(pool, agora=agora)
    assert livres == []                                # NÃO tentou texto livre (não tinha sessão de verdade)
    assert templates == [("86999990000", "HXlembretetest")]   # foi pelo template, que funciona


def test_convidado_confirmado_pela_web_sem_template_nao_manda_mas_pode_reter(pool, conta_id, monkeypatch):
    """Sem template configurado, uma confirmação pela web não pode mandar nada
    (não tem canal livre nem template) — mas a falha NÃO pode "queimar" a
    tentativa: se o template for configurado depois, o próximo ciclo (~2min)
    ainda dentro da janela do evento consegue mandar."""
    from finance import whatsapp_out as wout
    monkeypatch.delenv("TWILIO_TMPL_LEMBRETE_SID", raising=False)
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ev = ag.criar_evento(pool, conta_id, "Reunião com Ana", agora + timedelta(minutes=20))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    cv.responder(pool, conv["token"], "confirmado")
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30,
                     avisar_convidados=True)
    lb.rodar(pool, agora=agora)
    with pool.connection() as c:
        assert c.execute("select 1 from lembretes_enviados where tipo='aviso_convidado'").fetchone() is None

    # "configura" o template e roda o próximo ciclo (2min depois, evento ainda na janela)
    templates = []
    monkeypatch.setattr(wout, "enviar_template",
                        lambda c, cid, numero, sid, variaveis: (templates.append(numero) or {"ok": True}))
    monkeypatch.setenv("TWILIO_TMPL_LEMBRETE_SID", "HXlembretetest")
    lb.rodar(pool, agora=agora.replace(minute=2))
    assert templates == ["86999990000"]                # tentou de novo e dessa vez foi


def test_so_aviso_ligado_nao_manda_resumo(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Hoje cedo", agora.replace(hour=9))
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=15)
    # 7h, resumo desligado -> mesmo com evento hoje, não manda resumo
    assert lb.rodar(pool, agora=agora)["resumo"] == 0 and enviados == []
