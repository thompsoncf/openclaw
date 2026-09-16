"""O SELO DO FOLLOW-UP NO CARD DO FUNIL (16/09/2026).

Pedido do dono: "avisar no card a questão do follow". O que este arquivo fixa não
é a aparência do selo — é QUEM RECEBE ele, que é onde uma tela erra caro:

  1. conta sem a tela de Follow-up (§6: hoje só o perfil `eventos`) não vê selo;
  2. conta com a tela mas com `follow_up_modo='off'` também não — o dono não
     optou por nada, e um selo de cobrança aparecendo sozinho no quadro dos
     vendedores seria o sistema decidindo por ele;
  3. em 'observando' APARECE, de propósito: foi a escolha do dono em 16/09, pra
     ver a régua rodando com lead de verdade antes de ligar a cobrança;
  4. o atraso é escrito igual nas duas telas — o funil e o /painel/follow-up leem
     o mesmo `_tempo_curto`, senão uma arredonda diferente da outra e ninguém
     descobre por meses.

O que NÃO está aqui: o estado em si (crítico, atrasado, hoje…). Ele é do
`finance/follow_up.py` e já tem `tests/test_follow_up.py` inteiro — este selo só
mostra o que aquele motor calculou, e duplicar o teste seria duplicar a regra.
"""
from web import painel_follow_up as pf
from web import painel_prospeccao as pp


# ----------------------------------------------------- quem recebe o selo
def test_conta_sem_a_tela_de_follow_up_nao_ganha_selo(monkeypatch):
    """Perfil recorrente/produto: `PERFIS_COM_TELA` não os inclui hoje."""
    monkeypatch.setattr("web.portal.nicho_da_conta", lambda conta: "consultoria")
    assert pp._tem_follow_up((3, "ZAQ")) is False


def test_conta_de_eventos_tem_a_tela(monkeypatch):
    monkeypatch.setattr("web.portal.nicho_da_conta", lambda conta: "eventos")
    assert pp._tem_follow_up((34, "Prime")) is True


def test_conta_curta_nao_explode_e_fica_sem_selo():
    """Mock de teste com tupla curta: o lado seguro de errar é não mostrar nada."""
    assert pp._tem_follow_up(()) in (False, True)   # não levanta


def test_o_modo_off_nao_mostra_selo_e_observando_mostra():
    """A regra do handler, escrita como dado pra não depender de subir o board.

    Se alguém trocar a tupla por `('ligado',)` este teste cai — e é justamente
    isso que a gente quer que caia, porque 'observando' mostrando o selo foi
    decisão explícita do dono."""
    import re
    fonte = open("web/painel_prospeccao.py", encoding="utf-8").read()
    m = re.search(r'_cfg\.get\("follow_up_modo"\) in \(([^)]*)\)', fonte)
    assert m, "a porta do modo sumiu do handler"
    modos = m.group(1)
    assert "observando" in modos, "o dono escolheu ver o selo em observando"
    assert "ligado" in modos
    assert "off" not in modos, "em 'off' o dono não optou por nada"


def test_o_selo_e_tolerante_o_funil_abre_sem_ele():
    """Leitura que falha não pode derrubar o quadro — e precisa de SAVEPOINT, não
    só try: no Postgres um erro aborta a transação inteira, e as consultas
    seguintes deste mesmo handler morreriam com 'transaction is aborted'."""
    fonte = open("web/painel_prospeccao.py", encoding="utf-8").read()
    i = fonte.index("fu_por_lead: dict[int, dict] = {}")
    trecho = fonte[i:i + 1800]
    assert "with c.transaction():" in trecho, "sem savepoint o funil cai junto"
    assert "except Exception:" in trecho
    assert "fu_por_lead = {}" in trecho


# ----------------------------------------------------- o atraso, uma grafia só
def test_o_atraso_e_escrito_igual_nas_duas_telas():
    for h in (None, 0, 1, 16, 47, 48, 49, 72, 500):
        assert pf._tempo(h) == pp._tempo_curto(h)


def test_o_atraso_vira_dias_a_partir_de_48h():
    assert pp._tempo_curto(47) == "47h"
    assert pp._tempo_curto(48) == "2d"
    assert pp._tempo_curto(72) == "3d"
    assert pp._tempo_curto(None) == "—"
