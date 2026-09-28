"""O que se sabe ao marcar vai pra ficha (passo 0c-2 do prontuário, seção 12 do mockup):
o agente e a recepção guardam a data de nascimento, a mãe que marca pro filho vira a
responsável dele, e a mensagem cumprimenta quem recebe ("Lúcia, a consulta de Pedro").

Tudo por SINAL EXPLÍCITO de que a consulta é de outra pessoa (o "para" do agente, a caixa
da recepção, o "era pro meu filho"), nunca comparando nomes.
"""
from datetime import date, datetime, time

import pytest

from finance import clinica_agenda as ca
from finance import clinica_agente as cla
from finance import clinica_pacientes as cpa
from tests.test_clinica_agenda import AGORA, CLINICA
from tests.test_clinica_ficha_link import _ligar, cli  # noqa: F401
from tests.test_clinica_ficha_link import banco  # noqa: F401
from tests.test_clinica_pacientes import _ficha_do_evento
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import FONE, _manoel, _paciente, _tipo, pool, zap  # noqa: F401


# RELÓGIO FIXO SÓ PRA QUEM PASSA PELA ROTA HTTP. `_marcar` (acima) chama
# `cla.marcar` direto, que aceita `agora=` e recebe AGORA explícito — os testes
# que usam esse caminho nunca dependem do relógio de verdade.
#
# `test_card_sem_nome_nao_vira_responsavel` e
# `test_a_recepcao_marca_o_filho_com_a_caixa_e_sem_ela_o_nome_nao_manda` marcam
# pela ROTA (`cli.post`), que chama `web.painel_clinica_agenda.novo_salvar` →
# `ca.agendar` SEM passar `agora=` — de propósito: é o caminho de produção, e lá
# "agora" tem que ser o relógio de verdade. `ca.agendar` então usa
# `datetime.now(timezone.utc)`, o instante REAL em que o teste roda.
#
# Só que a data escolhida do slot livre destes dois testes vem de
# `ca.livres(..., agora=AGORA)`, com o AGORA FIXO desta suíte (25/09/2026,
# sexta). Enquanto o relógio de verdade não passava de 28/09, o horário
# escolhido (na tarde de segunda) continuava no futuro dos dois lados. A partir
# do momento em que o relógio real passa desse horário, `ca.agendar` recusa com
# "Esse horário já passou" — o POST redireciona de volta pro formulário sem
# criar o evento, e a leitura seguinte (`_ficha_do_evento`) quebra com
# `TypeError: 'NoneType' object is not subscriptable`: não é o dado que falta,
# é o evento que nunca chegou a nascer.
#
# O ajuste é só nestes dois testes: fixa `datetime.now()` de
# `finance.clinica_agenda` no mesmo AGORA que escolheu o slot, pro caminho HTTP
# enxergar o mesmo "agora" que o resto do arquivo já usa.
class _AgoraDaSuite(datetime):
    @classmethod
    def now(cls, tz=None):
        return AGORA.astimezone(tz) if tz else AGORA.replace(tzinfo=None)


@pytest.fixture
def relogio_da_rota(monkeypatch):
    monkeypatch.setattr(ca, "datetime", _AgoraDaSuite)


def _marcar(pool_, c, lead, conv, nome, nascimento="", para="", h=9, dia=date(2026, 9, 28)):
    t = _tipo(c, "Consulta")
    menu = {"tipos": [dict(t, agente_marca=True)], "slots": []}
    saiu = []
    r = cla.marcar(pool_, c, CLINICA, conv, lead, FONE, cla.codigo(_manoel(c), t["id"], ca.utc(dia, time(h))),
                   nome, menu, AGORA, saiu.append, nascimento=nascimento, para=para)
    c.commit()
    return r, saiu


def test_o_agente_marca_pro_filho_e_a_mae_vira_a_responsavel(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Lúcia Ferreira")
        r, saiu = _marcar(banco, c, lead, conv, "Pedro Ferreira", nascimento="10/03/2017", para="outra")
        assert r["ok"], r
        p = cpa.ficha(c, CLINICA, _ficha_do_evento(c, r["evento_id"]), AGORA)
        ev = ca.evento(c, CLINICA, r["evento_id"])
        vespera = ca.texto_vespera(c, CLINICA, ev, AGORA)
    assert p["nome"] == "Pedro Ferreira" and p["nascimento"] == date(2017, 3, 10) and p["idade"] == 9
    assert p["responsavel"] and p["responsavel"]["nome"] == "Lúcia Ferreira"
    assert saiu[0].startswith("Prontinho!! ✅ Lúcia, a consulta de Pedro com Dr. Manoel")
    assert vespera.startswith("Oi, Lúcia! Na segunda, 28/09, Pedro tem consulta")


def test_a_adolescente_no_proprio_celular_nao_ganha_responsavel(banco, zap):  # noqa: F811
    """O perfil "Duda 💕" é a própria Maria Eduarda: sem o "para outra", nada muda."""
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Duda 💕")
        r, saiu = _marcar(banco, c, lead, conv, "Maria Eduarda Silva", nascimento="05/06/2010", para="proprio")
        p = cpa.ficha(c, CLINICA, _ficha_do_evento(c, r["evento_id"]), AGORA)
        n = c.execute("select count(*) from clientes where dono_id=%s", (CLINICA,)).fetchone()[0]
    assert p["responsavel"] is None and p["nascimento"] == date(2010, 6, 5) and n == 1   # nenhuma ficha fantasma
    assert saiu[0].startswith("Prontinho!! ✅ Maria, sua consulta")


def test_a_propria_pessoa_e_a_data_que_nao_troca(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Lúcia Ferreira")
        r, saiu = _marcar(banco, c, lead, conv, "Lúcia Ferreira", nascimento="17/05/1990", para="proprio")
        kid = _ficha_do_evento(c, r["evento_id"])
        _marcar(banco, c, lead, conv, "Lúcia Ferreira", nascimento="01/01/2000", h=10, dia=date(2026, 9, 29))
        p = cpa.ficha(c, CLINICA, kid, AGORA)
    assert p["nascimento"] == date(1990, 5, 17) and p["responsavel"] is None
    assert saiu[0].startswith("Prontinho!! ✅ Lúcia, sua consulta")


def test_pro_filho_sem_o_nome_o_agente_pergunta(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Lúcia Ferreira")
        r, saiu = _marcar(banco, c, lead, conv, "", nascimento="10/03/2017", para="outra")
        n = c.execute("select count(*) from eventos_agenda where situacao is not null").fetchone()[0]
    assert not r["ok"] and "nome completo de quem vai ser atendido" in saiu[0] and n == 0


def test_marcou_no_nome_da_mae_e_era_pro_filho_troca_a_ficha(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Lúcia Ferreira")
        r, _s = _marcar(banco, c, lead, conv, "")                   # sem nome: fica no nome do card
        eid = r["evento_id"]
        lucia = _ficha_do_evento(c, eid)
        c.execute("update eventos_agenda set marcado_por='ia' where id=%s", (eid,))
        c.commit()
        _r2, saiu = _marcar(banco, c, lead, conv, "Pedro Ferreira", nascimento="10/03/2017")
        pedro = _ficha_do_evento(c, eid)
        p = cpa.ficha(c, CLINICA, pedro, AGORA)
        m = cpa.ficha(c, CLINICA, lucia, AGORA)
    assert pedro != lucia and p["nome"] == "Pedro Ferreira"          # a consulta foi pra ficha do Pedro
    assert p["responsavel"]["id"] == lucia and p["nascimento"] == date(2017, 3, 10)
    assert m["nascimento"] is None                                   # a data não foi pra mãe
    assert saiu[-1].startswith("Prontinho!! ✅ Lúcia, a consulta de Pedro")


def test_a_irma_de_nome_parecido_nao_vira_responsavel(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Ana")
        ana_clara = cpa.achar_ou_criar(c, CLINICA, lead, "Ana Clara Souza", FONE)
        c.commit()
        r, saiu = _marcar(banco, c, lead, conv, "Pedro Souza", nascimento="10/03/2017", para="outra")
        p = cpa.ficha(c, CLINICA, _ficha_do_evento(c, r["evento_id"]), AGORA)
    assert p["responsavel"] and p["responsavel"]["id"] != ana_clara and p["responsavel"]["nome"] == "Ana"


def test_mae_e_filha_com_o_mesmo_primeiro_nome(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, conv = _paciente(c, nome="Maria Lúcia Souza")
        _r, saiu = _marcar(banco, c, lead, conv, "Maria Eduarda Souza", nascimento="05/06/2015", para="outra")
    assert saiu[0].startswith("Prontinho!! ✅ Maria, a consulta de Maria Eduarda")


def test_card_sem_nome_nao_vira_responsavel(cli, banco, zap, relogio_da_rota):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c, nome="Contato WhatsApp")
        prof, tipo = _manoel(c), _tipo(c, "Consulta")["id"]
        livre = ca.livres(c, CLINICA, prof, tipo, date(2026, 9, 28), dias=3, agora=AGORA)[0]["inicio"]
        c.commit()
    cli.get("/_papel/vendedor/51")
    r = cli.post("/painel/clinica/agenda/novo", data={"prof": prof, "tipo": tipo, "inicio": livre.isoformat(),
                                                      "lead_id": lead, "nome": "Pedro Ferreira", "para_outro": "1",
                                                      "nascimento": "2017-03-10", "acao": "agendar"})
    assert r.status_code == 303 and "erro" not in r.headers["location"]
    with banco.connection() as c:
        eid = c.execute("select max(id) from eventos_agenda").fetchone()[0]
        p = cpa.ficha(c, CLINICA, _ficha_do_evento(c, eid), AGORA)
        n = c.execute("select count(*) from clientes where dono_id=%s", (CLINICA,)).fetchone()[0]
        txt = ca.texto_marcado(c, CLINICA, ca.evento(c, CLINICA, eid))
    assert p["nome"] == "Pedro Ferreira" and p["responsavel"] is None and n == 1
    assert txt.startswith("Prontinho!! ✅ A consulta de Pedro com")


def test_a_recepcao_marca_o_filho_com_a_caixa_e_sem_ela_o_nome_nao_manda(cli, banco, zap, relogio_da_rota):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c, nome="Lúcia Ferreira")
        prof, tipo = _manoel(c), _tipo(c, "Consulta")["id"]
        livres = ca.livres(c, CLINICA, prof, tipo, date(2026, 9, 28), dias=3, agora=AGORA)
        c.commit()
    cli.get("/_papel/vendedor/51")
    base = {"prof": prof, "tipo": tipo, "lead_id": lead, "acao": "agendar"}
    # sem a caixa: o nome que sobrou no campo não renomeia ninguém
    cli.post("/painel/clinica/agenda/novo", data={**base, "inicio": livres[0]["inicio"].isoformat(),
                                                  "nome": "João Silva"})
    cli.post("/painel/clinica/agenda/novo", data={**base, "inicio": livres[1]["inicio"].isoformat(),
                                                  "nome": "Pedro Ferreira", "para_outro": "1",
                                                  "nascimento": "2017-03-10"})
    with banco.connection() as c:
        nomes = [x[0] for x in c.execute("select paciente_nome from eventos_agenda where situacao is not null "
                                         "order by id").fetchall()]
        eid = c.execute("select max(id) from eventos_agenda").fetchone()[0]
        p = cpa.ficha(c, CLINICA, _ficha_do_evento(c, eid), AGORA)
    assert nomes == ["Lúcia Ferreira", "Pedro Ferreira"]
    assert p["nascimento"] == date(2017, 3, 10) and p["responsavel"]["nome"] == "Lúcia Ferreira"


def test_a_data_dita_de_qualquer_jeito():
    assert cpa.ler_data("10/03/2017") == date(2017, 3, 10)
    assert cpa.ler_data("10/3/17") == date(2017, 3, 10)
    assert cpa.ler_data("17.05.90") == date(1990, 5, 17)
    assert cpa.ler_data("2017-03-10") == date(2017, 3, 10)
    assert cpa.ler_data("dez de março") is None and cpa.ler_data("31/02/2017") is None


def test_o_prompt_pede_nome_nascimento_e_para_quem():
    import inspect
    src = inspect.getsource(cla.prompt)
    assert "consulta.nascimento" in src and '"para":"proprio|outra"' in src
    assert "alergia e remédio são do formulário" in src
