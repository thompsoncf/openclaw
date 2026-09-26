"""O agente do WhatsApp na clínica (fase 3b, finance/clinica_agente.py).

Em cima da semente da Espaço Pelle (350): o Dr. Manoel atende seg a sex, 08:00–12:00
e 13:30–16:30 na sede; Consulta (R$ 500, o agente diz o preço e marca), Avaliação
(sem preço, marca), Retorno e Sessão (marca, mas só pra quem já foi atendido).

A IA é falsa (devolve o JSON que o teste manda) e o WhatsApp também (grava o que
sairia). O que se prova é o que o CÓDIGO faz com a decisão da IA: confere o
horário, marca, recusa, passa pra recepção com o texto fixo, fica quieto quando o
recado já está com a recepção — e que a Prime (festa) continua no caminho dela.
"""
import json
import os
import types
from datetime import datetime, time, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

import core.brain as cb
from finance import agente
from finance import clinica_agenda as ca
from finance import clinica_agente as cla
from finance import clinica_config as cc
from tests.test_clinica_agenda import _SQL, AGORA, BASE, CLINICA, SEG

FONE = "+5599988880001"
PRIME = 34


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_agente_teste"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("""alter table prospeccao add column segmento text, add column cidade text,
                       add column uf text, add column origem_detalhe text;
                     alter table conversas add column agente_ativo boolean default true;
                     alter table mensagens add column midia_tipo text;
                     create table agente_config (conta_id bigint primary key, ativo boolean default true,
                       limiar_confianca int, horario text default '24h', tom text default 'informal',
                       max_trocas int, escalar_para text, pode_responder boolean default true,
                       pode_qualificar boolean default true, pode_agendar boolean default false,
                       pode_orcamento boolean default false, orcamento_proativo boolean default false,
                       agendar_modo text default 'off');
                     create table agente_conhecimento (id bigserial primary key, conta_id bigint,
                       tipo text, pergunta text, resposta text, ordem int default 0);
                     insert into agente_config (conta_id) values (39), (34);
                     insert into membros (id, conta_id, nome, papel) values (52,39,'Dono','dono');""")
        for m in ("071_servicos_catalogo.sql", "148_servico_categoria_foto.sql", "153_servico_icone.sql",
                  "098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                  "136_visita_agenda.sql", "179_agenda_tipo_e_hora_sugerida.sql", "348_clinica_base.sql",
                  "350_clinica_semente_espaco_pelle.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("alter table eventos_agenda add column if not exists marcado_por text")
        for m in ("360_clinica_agenda.sql", "363_clinica_repasses.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


class _Resp:
    def __init__(self, txt):
        self.content = [types.SimpleNamespace(type="text", text=txt)]


@pytest.fixture()
def ia(monkeypatch):
    """A IA falsa: devolve `ia.json` e guarda o (system, pedido) de cada chamada."""
    estado = types.SimpleNamespace(json={"acao": "responder", "resposta": "Oi!"}, chamadas=[])

    class _Brain:
        def chamar(self, system, mensagens, ferramentas=None, model=None):
            estado.chamadas.append((system, mensagens[0]["content"]))
            return _Resp(json.dumps(estado.json))
    monkeypatch.setattr(cb, "Brain", _Brain)
    return estado


@pytest.fixture()
def zap(monkeypatch):
    """O WhatsApp falso e os avisos da recepção."""
    estado = types.SimpleNamespace(saiu=[], avisos=[])

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        estado.saiu.append(texto)
        return {"ok": True, "sid": f"s{len(estado.saiu)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    monkeypatch.setattr(cla, "_aviso", lambda pool, conta_id, membros, titulo, corpo, url:
                        estado.avisos.append((membros, titulo, corpo, url)))
    return estado


def _conversa(c, texto="quanto é a consulta?", nome="Maria Clara", fone=FONE, conta=CLINICA):
    lead = c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp, status, estagio)
                        values (%s,%s,%s,%s,'novo','lead') returning id""", (conta, nome, nome, fone)).fetchone()[0]
    conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, contato_nome)
                        values (%s,%s,%s,%s) returning id""", (conta, lead, fone, nome)).fetchone()[0]
    _diz(c, conv, texto)
    return lead, conv


def _diz(c, conv, texto, autor="lead"):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                 values (%s,'whatsapp',%s,%s,%s)""", (conv, "in" if autor == "lead" else "out", autor, texto))
    c.commit()


def _rodar(pool, conv, lead, fone=FONE):
    """Uma volta do agente da clínica, como o `agente._atender` chama."""
    with pool.connection() as c:
        msgs = c.execute("""select direcao, autor, texto from mensagens where conversa_id=%s
                             order by criado_em desc, id desc limit 12""", (conv,)).fetchall()
        cfg = {"pode_responder": True, "pode_qualificar": True, "tom": "informal"}
        conv_t = (True, lead, fone, "Maria Clara", fone, None, None, None, None, "whatsapp")
        cat = "\n".join(agente._linha_catalogo(s) for s in [])
        cla.atender(pool, c, CLINICA, conv, cfg, conv_t, msgs, historico="", gemeo_nota="",
                    instr="", faqs="", cat_txt=cat or "(sem catálogo)", canal="whatsapp", destino=fone,
                    enviar=lambda t: agente._enviar(c, CLINICA, conv, "whatsapp", fone, t), agora=AGORA)
        c.commit()


def _cod(c, h=8, m=0, dia=SEG, tipo="Consulta"):
    prof = cc.listar_profissionais(c, CLINICA)[0]["id"]
    t = next(x for x in cc.listar_tipos(c, CLINICA) if x["nome"] == tipo)["id"]
    return cla.codigo(prof, t, ca.utc(dia, time(h, m)))


def _eventos(c):
    return c.execute("""select paciente_nome, marcado_por, situacao, to_char(inicio - interval '3 hours', 'DD/MM HH24:MI')
                          from eventos_agenda where situacao is not null order by id""").fetchall()


def _repasses(c):
    return [r[0] for r in c.execute("select motivo from clinica_repasses order by id").fetchall()]


# ------------------------------------------------------------------ regras sem banco

def test_midia_e_urgencia_pela_regra():
    assert cla.midia("📷 Foto") == "foto" and cla.midia("🎤 Áudio (0:07)") == "audio"
    assert cla.midia("📄 Documento") == "arquivo" and cla.midia("oi 📷") is None
    assert cla.midia("olha minha perna", "imagem") == "foto"
    for t in ("está sangrando muito", "tive uma reação alérgica", "Emergência!!", "ta saindo muito sangue",
              "n consigo respirar", "nao to conseguindo respirar", "a garganta ta fechando",
              "meu rosto e a boca incharam muito", "inchou todo o olho", "Não consigo respirar"):
        assert cla.urgente(t), t
    for t in ("preciso de um horário urgente", "quanto é?", "quero fazer exame de sangue",
              "fui no pronto socorro ontem, quero marcar consulta", "sem armário", "não consigo ir segunda"):
        assert not cla.urgente(t), t


def test_conselho_so_quando_e_conselho():
    for t in ("Você pode passar uma pomada de antibiótico", "Recomendo o tratamento com laser",
              "Não é grave, fica tranquila", "pode ser micose", "Use protetor solar e evite o sol",
              "Passe uma compressa fria e tome um antialérgico", "Pode ser uma alergia, o médico confirma",
              "É normal arder depois do peeling"):
        assert cla.parece_conselho(t), t
    # recepção, não conselho
    for t in ("Recomendo chegar 10 minutos antes", "Você pode usar o estacionamento da frente",
              "Pode ser uma consulta na quinta?", "A aplicação do ácido é R$ 1.500",
              "É normal a consulta durar 30 minutos"):
        assert not cla.parece_conselho(t), t
    assert cla.diz_que_marcou("Prontinho, marquei pra você!") and not cla.diz_que_marcou("Quer que eu marque?")
    assert cla.falou_de("Sim! A Unimed cobre sim 😊") == "convenio"
    assert cla.falou_de("Dou 10% de desconto no Pix") == "desconto"
    assert cla.falou_de("O retorno é gratuito") == "desconto"
    assert cla.falou_de("A consulta é R$ 500, particular") is None


def test_codigo_vai_e_volta():
    ini = ca.utc(SEG, time(13, 30))
    assert cla.ler_codigo(cla.codigo(3, 7, ini)) == (3, 7, ini)
    assert cla.ler_codigo("3-7-26093013") is None and cla.ler_codigo("") is None
    assert cla.ler_codigo("3-7-2613321330") is None           # mês 13


# ------------------------------------------------------------------ o prompt

def test_prompt_tem_horario_real_e_esconde_o_que_nao_marca(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c)
    _rodar(pool, conv, lead)
    system, pedido = ia.chamadas[0]
    assert "NUNCA FAZ" in system and "convênio" in system
    assert f"{_cod_pool(pool)} = seg 28/09 às 08:00" in pedido
    assert "[Consulta com Dr. Manoel]" in pedido
    assert "[Retorno" not in pedido                    # ainda não é paciente
    assert "[Avaliação" not in pedido                  # é da Juliana, que ainda não tem grade
    assert "convidados" not in pedido and "festa" not in (system + pedido).lower()
    assert '"acao":"responder|consulta|repassar"' in pedido
    assert zap.saiu == ["Oi!"]


def _cod_pool(pool, **kw):
    with pool.connection() as c:
        return _cod(c, **kw)


def test_sem_grade_nao_oferece_e_manda_repassar(pool, ia, zap):
    with pool.connection() as c:
        c.execute("delete from clinica_grade")
        lead, conv = _conversa(c)
    _rodar(pool, conv, lead)
    assert "você não tem horário para oferecer agora" in ia.chamadas[0][1]


# ------------------------------------------------------------------ marcar

def test_escolheu_o_horario_marca_e_avisa(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "quinta não, segunda 8h pode ser")
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": "Maria Clara Souza"},
               "resposta": "Marquei!"}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _eventos(c) == [("Maria Clara Souza", "ia", "agendado", "28/09 08:00")]
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "qualificado"
    assert zap.saiu[0].startswith("Prontinho!! ✅ Maria, sua consulta com Dr. Manoel está marcada para seg 28/09 às 08:00, no Espaço Pelle")
    assert "Marquei!" not in zap.saiu                    # quem confirma é o código, não a IA
    assert zap.avisos[0][1] == "🗓️ O agente marcou"
    assert zap.avisos[0][3].startswith("/painel/clinica/agenda/evento/")


def test_a_mae_marca_pro_filho_no_mesmo_card(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c)
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool, h=9), "nome": "Pedro Henrique"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _eventos(c)[0][0] == "Pedro Henrique"
        assert c.execute("select prospeccao_id from eventos_agenda").fetchone()[0] == lead


def test_o_mesmo_codigo_de_novo_nao_marca_duas_vezes(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c)
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": "Maria Clara Souza"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        _diz(c, conv, "obrigada!")
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": ""}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert len(_eventos(c)) == 1
    assert "já está marcada para seg 28/09 às 08:00" in zap.saiu[-1]
    assert "ocupado" not in zap.saiu[-1]


def test_marcou_no_nome_da_mae_e_era_pro_filho(pool, ia, zap):
    """O protótipo marca primeiro e pergunta o nome depois: o nome muda na mesma consulta."""
    with pool.connection() as c:
        lead, conv = _conversa(c)
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": ""}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        _diz(c, conv, "é pro meu filho Pedro Souza")
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": "Pedro Souza"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert [e[0] for e in _eventos(c)] == ["Pedro Souza"]
    assert zap.saiu[-1].startswith("Prontinho!! ✅ Pedro, sua consulta")


def test_retorno_e_pela_pessoa_nao_pelo_card(pool, ia, zap):
    """A mãe já foi atendida; o filho, não: retorno pro filho não sai de graça."""
    with pool.connection() as c:
        lead, conv = _conversa(c)
        eid, _ = ca.agendar(c, CLINICA, profissional_id=cc.listar_profissionais(c, CLINICA)[0]["id"],
                            servico_id=next(x for x in cc.listar_tipos(c, CLINICA) if x["nome"] == "Consulta")["id"],
                            inicio=ca.utc(SEG, time(15)), lead_id=lead, agora=AGORA)
        c.execute("update eventos_agenda set situacao='finalizado' where id=%s", (eid,))
        c.commit()
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool, tipo="Retorno"), "nome": "Pedro Souza"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert len(_eventos(c)) == 1 and _repasses(c) == ["marcar"]
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool, tipo="Retorno"), "nome": "Maria Clara"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert [e[0] for e in _eventos(c)][-1] == "Maria Clara"


def test_nome_provisorio_nao_e_nome(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, nome="Contato WhatsApp")
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": ""}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _eventos(c) == []
    assert zap.saiu == ["Pra eu marcar, me diz o nome completo de quem vai ser atendido? 😊"]


def test_sem_celular_nao_oferece_horario(pool, ia, zap):
    """DM do Instagram: sem card e sem celular a agenda não confirma a véspera."""
    with pool.connection() as c:
        lead, conv = _conversa(c)
    with pool.connection() as c:
        msgs = c.execute("select direcao, autor, texto from mensagens where conversa_id=%s", (conv,)).fetchall()
        cla.atender(pool, c, CLINICA, conv, {"pode_responder": True}, (True, None, "ig-123", "", None, None,
                    None, None, None, "instagram"), msgs, historico="", gemeo_nota="", instr="", faqs="",
                    cat_txt="", canal="instagram", destino="ig-123",
                    enviar=lambda t: agente._enviar(c, CLINICA, conv, "instagram", "ig-123", t), agora=AGORA)
    assert "você não tem horário para oferecer agora" in ia.chamadas[0][1]


def test_json_com_texto_em_volta_e_json_nenhum(pool, ia, zap, monkeypatch):
    class _Brain:
        resposta = 'Claro! {"acao":"responder","resposta":"A consulta é R$ 500."} Abraço'

        def chamar(self, system, mensagens, ferramentas=None, model=None):
            return _Resp(_Brain.resposta)
    monkeypatch.setattr(cb, "Brain", _Brain)
    with pool.connection() as c:
        lead, conv = _conversa(c)
    _rodar(pool, conv, lead)
    assert zap.saiu == ["A consulta é R$ 500."]
    _Brain.resposta = "desculpe, não entendi"
    with pool.connection() as c:
        _diz(c, conv, "?")
    _rodar(pool, conv, lead)
    assert zap.saiu[-1] == cla.MOTIVOS["pessoa"][1]


def test_horario_ocupado_no_balcao_oferece_outros(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c)
        ca.agendar(c, CLINICA, profissional_id=cc.listar_profissionais(c, CLINICA)[0]["id"],
                   servico_id=next(x for x in cc.listar_tipos(c, CLINICA) if x["nome"] == "Consulta")["id"],
                   inicio=ca.utc(SEG, time(8)), nome="Outra", fone="99 97777-0001", agora=AGORA)
        c.commit()
    cod = _cod_pool(pool)
    ia.json = {"acao": "consulta", "consulta": {"codigo": cod, "nome": "Maria Clara"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert [e[0] for e in _eventos(c)] == ["Outra"]
    assert zap.saiu[0].startswith("Esse horário não está disponível 😕") or \
        zap.saiu[0].startswith("Esse horário acabou de ser ocupado 😕")
    assert "seg 28/09 às 08:00" not in zap.saiu[0] and zap.saiu[0].endswith("Qual fica melhor?")


def test_retorno_so_pra_quem_ja_foi_atendido(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "é retorno")
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool, tipo="Retorno"), "nome": "Maria Clara"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _eventos(c) == []
    assert zap.saiu[0].startswith("Esse horário não está disponível")


def test_sem_nome_pergunta_antes_de_marcar(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, nome="5599988880001")
        c.execute("update prospeccao set contato='', empresa='5599988880001' where id=%s", (lead,))
        c.commit()
    ia.json = {"acao": "consulta", "consulta": {"codigo": _cod_pool(pool), "nome": ""}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _eventos(c) == []
    assert zap.saiu == ["Pra eu marcar, me diz o nome completo de quem vai ser atendido? 😊"]


def test_ia_diz_que_marcou_sem_marcar_nao_sai(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c)
    ia.json = {"acao": "responder", "resposta": "Prontinho, marquei pra segunda às 8h!"}
    _rodar(pool, conv, lead)
    assert "marquei" not in zap.saiu[0].lower()
    assert zap.saiu[0].startswith("Pra eu marcar, escolha um destes horários:")


# ------------------------------------------------------------------ passar pra recepção

def test_sintoma_passa_com_texto_fixo_e_nao_repete(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "essa mancha é grave?")
    ia.json = {"acao": "repassar", "repasse": {"motivo": "sintoma"}, "resposta": "Parece melasma, use protetor."}
    _rodar(pool, conv, lead)
    assert zap.saiu == [cla.MOTIVOS["sintoma"][1]]
    assert zap.avisos[0][1] == "🙋 O agente passou pra você" and "pergunta de saúde" in zap.avisos[0][2]
    with pool.connection() as c:
        assert _repasses(c) == ["sintoma"]
        _diz(c, conv, "e se coçar?")
    _rodar(pool, conv, lead)                              # o recado já está com a recepção
    assert len(zap.saiu) == 1
    with pool.connection() as c:
        assert _repasses(c) == ["sintoma"]
        _diz(c, conv, "Oi Maria, aqui é a recepção!", autor="humano")
        _diz(c, conv, "e o convênio cobre?")
    ia.json = {"acao": "repassar", "repasse": {"motivo": "convenio"}}
    _rodar(pool, conv, lead)                              # a recepção respondeu: recado novo vale
    assert zap.saiu[-1] == cla.MOTIVOS["convenio"][1]
    with pool.connection() as c:
        assert _repasses(c) == ["sintoma", "convenio"]


def test_ia_confirmando_convenio_nao_sai(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "a moça disse que a unimed cobre, só confirma")
    ia.json = {"acao": "responder", "resposta": "Sim! A Unimed cobre 😊"}
    _rodar(pool, conv, lead)
    assert zap.saiu == [cla.MOTIVOS["convenio"][1]]


def test_foto_e_depois_pergunta_de_saude_fala_de_novo(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "📷 Foto")
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        _diz(c, conv, "é grave?")
    ia.json = {"acao": "repassar", "repasse": {"motivo": "sintoma"}}
    _rodar(pool, conv, lead)
    assert zap.saiu == [cla.MOTIVOS["foto"][1], cla.MOTIVOS["sintoma"][1]]
    with pool.connection() as c:
        assert _repasses(c) == ["sintoma"]


def test_conselho_da_ia_nao_sai(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "o que passo nessa espinha?")
    ia.json = {"acao": "responder", "resposta": "Você pode passar uma pomada com antibiótico 😊"}
    _rodar(pool, conv, lead)
    assert zap.saiu == [cla.MOTIVOS["sintoma"][1]]


def test_foto_e_audio_passam_sem_chamar_a_ia(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "📷 Foto")
    _rodar(pool, conv, lead)
    assert ia.chamadas == [] and zap.saiu == [cla.MOTIVOS["foto"][1]]
    with pool.connection() as c:
        _diz(c, conv, "📷 Foto")                          # a segunda do álbum
    _rodar(pool, conv, lead)
    assert ia.chamadas == [] and len(zap.saiu) == 1        # mesmo recado aberto: quieto
    with pool.connection() as c:
        _diz(c, conv, "🎤 Áudio (0:12)")                   # assunto novo: fala, no mesmo item
    _rodar(pool, conv, lead)
    assert ia.chamadas == [] and zap.saiu[-1] == cla.MOTIVOS["audio"][1]
    assert len(zap.avisos) == 1                            # a recepção foi chamada uma vez só
    with pool.connection() as c:
        assert _repasses(c) == ["audio"]


def test_audio_transcrito_e_texto_e_foto_com_legenda_e_foto(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "🎤 Áudio (0:09)\nbom dia, quanto é a consulta?")
    _rodar(pool, conv, lead)
    assert len(ia.chamadas) == 1 and zap.saiu == ["Oi!"]    # a transcrição a IA lê
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, midia_tipo)
                     values (%s,'whatsapp','in','lead','isso aqui precisa tirar?','imagem')""", (conv,))
        c.commit()
    _rodar(pool, conv, lead)
    assert len(ia.chamadas) == 1 and zap.saiu[-1] == cla.MOTIVOS["foto"][1]
    assert cla.midia("🎬 Vídeo (0:12)") == "foto" and cla.midia("🩷 Figurinha") is None


def test_urgencia_sempre_fala_e_sobe_o_item(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "📷 Foto")
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        _diz(c, conv, "está sangrando e inchando a boca")
    _rodar(pool, conv, lead)
    assert ia.chamadas == []
    assert zap.saiu[-1] == cla.MOTIVOS["urgencia"][1] and "192" in zap.saiu[-1]
    assert zap.avisos[-1][1] == "🚨 Urgência no WhatsApp"
    with pool.connection() as c:
        assert _repasses(c) == ["urgencia"]              # o mesmo item, agora urgente
        itens = cla.repasses_abertos(c, CLINICA)
    assert itens[0]["urgente"] and itens[0]["nome"] == "Maria Clara"


def test_motivo_inventado_vira_pessoa(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "quero falar com alguém")
    ia.json = {"acao": "repassar", "repasse": {"motivo": "qualquer"}}
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        assert _repasses(c) == ["pessoa"]


def test_lista_da_tela_hoje_some_com_resolvido(pool, ia, zap):
    with pool.connection() as c:
        lead, conv = _conversa(c, "📄 Documento")
    _rodar(pool, conv, lead)
    with pool.connection() as c:
        itens = cla.repasses_abertos(c, CLINICA)
        assert [(i["motivo"], i["rotulo"]) for i in itens] == [("arquivo", "mandou documento")]
        assert cla.resolver(c, CLINICA, itens[0]["id"], 51)
        assert not cla.resolver(c, 34, itens[0]["id"], 51)            # outra conta não mexe
        c.commit()
        assert cla.repasses_abertos(c, CLINICA) == []


# ------------------------------------------------------------------ o lembrete da véspera

def _com_lembrete(pool):
    with pool.connection() as c:
        lead, conv = _conversa(c, "oi")
        eid, _ = ca.agendar(c, CLINICA, profissional_id=cc.listar_profissionais(c, CLINICA)[0]["id"],
                            servico_id=next(x for x in cc.listar_tipos(c, CLINICA) if x["nome"] == "Consulta")["id"],
                            inicio=ca.utc(SEG, time(8)), lead_id=lead, agora=AGORA)
        # o lembrete saiu há 10 minutos (a conversa anda no relógio de verdade)
        c.execute("update eventos_agenda set confirmacao_enviada_em=now() - interval '10 minutes' where id=%s",
                  (eid,))
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','out','bot',
                             'Oi, Maria! Na segunda, 28/09, você tem consulta às 08:00. Responda 1 ou 2.',
                             now() - interval '10 minutes')""", (conv,))
        c.commit()
    return lead, conv, eid


def test_um_do_lembrete_confirma_sem_ia(pool, ia, zap):
    lead, conv, eid = _com_lembrete(pool)
    with pool.connection() as c:
        _diz(c, conv, "1")
    _rodar(pool, conv, lead)
    assert ia.chamadas == [] and zap.saiu == ["Obrigado! Está confirmado ✅ Até lá 😊"]
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["situacao"] == "confirmado"


def test_sim_com_pergunta_grava_e_deixa_a_ia_responder(pool, ia, zap):
    lead, conv, eid = _com_lembrete(pool)
    with pool.connection() as c:
        _diz(c, conv, "sim, confirmo! e quanto fica o estacionamento?")
    _rodar(pool, conv, lead)
    assert len(ia.chamadas) == 1
    with pool.connection() as c:
        assert ca.evento(c, CLINICA, eid)["situacao"] == "confirmado"
    assert "(confirmado)" in ia.chamadas[0][1]            # a IA vê a consulta já confirmada


def test_urgencia_curta_depois_do_lembrete_e_urgencia(pool, ia, zap):
    """"Não consigo respirar" começa igual a "não consigo ir": não vira remarcar."""
    lead, conv, eid = _com_lembrete(pool)
    with pool.connection() as c:
        _diz(c, conv, "Não consigo respirar")
    _rodar(pool, conv, lead)
    assert zap.saiu == [cla.MOTIVOS["urgencia"][1]]
    with pool.connection() as c:
        ev = c.execute("select situacao, pede_remarcar_em from eventos_agenda where id=%s", (eid,)).fetchone()
    assert ev == ("agendado", None)


def test_dois_com_a_consulta_ja_confirmada_fica_com_a_ia(pool, ia, zap):
    lead, conv, eid = _com_lembrete(pool)
    with pool.connection() as c:
        c.execute("update eventos_agenda set situacao='confirmado' where id=%s", (eid,))
        _diz(c, conv, "não vou poder")
    _rodar(pool, conv, lead)
    assert len(ia.chamadas) == 1                          # não promete remarcar sem ter gravado


def test_urgencia_fora_do_horario_ainda_fala(pool, ia, zap, monkeypatch):
    monkeypatch.setattr(agente, "_pode_falar_agora", lambda cfg: False)
    _atender_de_verdade(pool, CLINICA, "quanto é a consulta?", monkeypatch)
    assert zap.saiu == [] and ia.chamadas == []
    _atender_de_verdade(pool, CLINICA, "to com falta de ar depois do procedimento", monkeypatch)
    assert zap.saiu == [cla.MOTIVOS["urgencia"][1]] and ia.chamadas == []


def test_sim_pra_pergunta_do_agente_nao_e_confirmacao(pool, ia, zap):
    lead, conv, eid = _com_lembrete(pool)
    with pool.connection() as c:
        _diz(c, conv, "Quer que eu veja um horário pra sua mãe também?", autor="bot")
        _diz(c, conv, "sim")
    _rodar(pool, conv, lead)
    assert len(ia.chamadas) == 1                          # foi a IA, não o atalho do lembrete


# ------------------------------------------------------------------ o caminho inteiro, e a Prime

def _atender_de_verdade(pool, conta, texto, monkeypatch):
    from finance import evento_lead
    monkeypatch.setattr(evento_lead, "gravar", lambda *a, **k: None)
    # o aviso do mesmo número em outro chip tem teste próprio (test_conversa_por_chip)
    # e lê tabelas que este banco não tem
    monkeypatch.setattr(agente, "_nota_gemeo", lambda c, conta_id, conv: "")
    with pool.connection() as c:
        lead, conv = _conversa(c, texto, conta=conta)
    agente._atender(pool, conta, conv)
    return lead, conv


def test_clinica_desvia_pro_agente_dela(pool, ia, zap, monkeypatch):
    _atender_de_verdade(pool, CLINICA, "quanto é a consulta?", monkeypatch)
    system, pedido = ia.chamadas[0]
    assert '"acao":"responder|consulta|repassar"' in pedido and "HORÁRIOS LIVRES" in pedido
    assert "convidados" not in pedido and "orcamento" not in pedido
    assert zap.saiu == ["Oi!"]


def test_a_prime_continua_no_caminho_da_festa(pool, ia, zap, monkeypatch):
    """Não-regressão: a conta de festa não vê nada da clínica e o formato é o de sempre."""
    ia.json = {"acao": "responder", "resposta": "Oi! Pra quantos convidados?"}
    _atender_de_verdade(pool, PRIME, "quanto é o salão?", monkeypatch)
    system, pedido = ia.chamadas[0]
    assert '"acao":"responder|orcamento|visita"' in pedido
    assert '"evento":{"data":"AAAA-MM-DD","convidados":0' in pedido
    assert "Itens que são ALTERNATIVAS entre si" in system
    assert "HORÁRIOS LIVRES" not in pedido and "NUNCA FAZ" not in system
    assert zap.saiu == ["Oi! Pra quantos convidados?"]
    with pool.connection() as c:
        assert c.execute("select count(*) from clinica_repasses").fetchone()[0] == 0


def test_agente_desligado_na_conversa_nao_fala(pool, ia, zap, monkeypatch):
    from finance import evento_lead
    monkeypatch.setattr(evento_lead, "gravar", lambda *a, **k: None)
    with pool.connection() as c:
        lead, conv = _conversa(c, "📷 Foto")
        c.execute("update conversas set agente_ativo=false where id=%s", (conv,))
        c.commit()
    agente._atender(pool, CLINICA, conv)
    assert zap.saiu == [] and ia.chamadas == []
