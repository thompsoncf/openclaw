"""Os botões da tela de Canais leem a CREDENCIAL, não o status da sessão.

O #520 pôs no ar o disjuntor da guerra de sessão: quando uma conta leva uma enxurrada
de falhas ao decifrar, o serviço a ESTACIONA de propósito — derruba o socket e deixa o
vigia retomá-la minutos depois. Isso criou um estado que não existia antes:

    status == 'desconectado'  E  a conta continua PAREADA (credencial no banco).

Os dois botões da tela liam só o status, e cada um quebrou pra um lado:

  • "Desconectar" — que APAGA a credencial — era escondido quando o status era
    'desconectado'. Ou seja: sumia justamente de quem tem credencial pra apagar. Foi o
    que aconteceu com a conta 35 (Doce Mell): estacionada pelo disjuntor, ficou sem
    saída nenhuma pelo painel.

  • "Apagar histórico" — irreversível — APARECIA nessa mesma conta. Ela volta sozinha
    em minutos, e aí o celular ressincroniza e reescreve parte do que foi apagado: o
    resultado pela metade que a barreira do "desconecte primeiro" existe pra impedir.

`pareada` responde a pergunta certa e devolve a exclusividade dos dois.

A tabela-verdade roda o JS DE VERDADE no Node, com um DOM de mentira — grep de texto
não provaria que a expressão decide o que se espera dela.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_prospeccao as pp

_TPL = pp._COMUNICACAO_TPL
_TEM_NODE = shutil.which("node") is not None


def _funcao(nome: str) -> str:
    """O corpo de uma função JS do template, do 'function <nome>' até a linha que a
    fecha. Recorta por chaves balanceadas: contar '}' na mão erraria nos objetos."""
    i = _TPL.index("function %s(" % nome)
    prof, j = 0, i
    while True:
        if _TPL[j] == "{":
            prof += 1
        elif _TPL[j] == "}":
            prof -= 1
            if prof == 0:
                return _TPL[i:j + 1]
        j += 1


_IDS = ("qr-box", "qr-img", "qr-msg", "qr-sair", "qr-btn", "qr-sync", "qr-sync-bar",
        "qr-sync-pct", "qr-apagar", "qr-retencao")
_IDS_C2 = ("c2-box", "c2-img", "c2-msg", "c2-sair", "c2-btn", "c2-st")

_DOM = """
var _els = {};
%s.forEach(function(id){ _els[id] = {id:id, style:{}, classList:{toggle:function(){},remove:function(){}},
                                     dataset:{}, textContent:'', src:'', title:'', disabled:false}; });
var document = { getElementById: function(id){ return _els[id] || null; },
                 addEventListener: function(){} };
var clearInterval=function(){}, setInterval=function(){return 0;},
    clearTimeout=function(){}, setTimeout=function(){return 0;};
var _qrTimer=null,_c2Timer=null,_qrEspera=null;
function qrIndefinido(){ _els['qr-sair'].style.display='INDEFINIDO';
                         _els['qr-apagar'].style.display='INDEFINIDO'; }
%s
%s
// '' e não undefined: JSON.stringify some com a chave, e o teste leria KeyError
// em vez de "esse botão não foi mexido".
console.log(JSON.stringify({sair:_els['%s'].style.display||'',
                            apagar:_els['qr-apagar'].style.display||''}));
"""


def _mostra(status, pareada, *, c2=False):
    """Roda qrShow/c2Show com esse payload e devolve o que ficou visível."""
    d = {"status": status, "ok": True}
    if pareada is not ...:
        d["pareada"] = pareada
    fn = _funcao("c2Show" if c2 else "qrShow")
    ids = json.dumps(list(_IDS_C2 if c2 else _IDS) + list(_IDS))
    js = _DOM % (ids, fn, "%s(%s);" % ("c2Show" if c2 else "qrShow", json.dumps(d)),
                 "c2-sair" if c2 else "qr-sair")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    return {"sair": out["sair"] == "inline-flex", "apagar": out["apagar"] == "inline-flex"}


pytestmark = pytest.mark.skipif(not _TEM_NODE, reason="precisa do node pra rodar o JS")


# ── o bug que motivou tudo ────────────────────────────────────────────────
def test_conta_estacionada_pelo_disjuntor_tem_como_desconectar():
    """O caso da conta 35: 'desconectado' porque o disjuntor a parou, mas com a
    credencial no banco. Este é o estado em que o botão SUMIU."""
    assert _mostra("desconectado", True)["sair"] is True


def test_conta_estacionada_nao_oferece_apagar_historico():
    """O outro lado, mais perigoso: ela volta sozinha em minutos e ressincroniza."""
    assert _mostra("desconectado", True)["apagar"] is False


def test_conta_de_verdade_desconectada_oferece_apagar_e_nao_desconectar():
    """Sem credencial não há o que desconectar — e é aqui que apagar faz sentido."""
    v = _mostra("desconectado", False)
    assert v["sair"] is False and v["apagar"] is True


# ── a tabela-verdade inteira ──────────────────────────────────────────────
@pytest.mark.parametrize("status", ["conectado", "reconectando", "aguardando_qr",
                                    "desconectado"])
@pytest.mark.parametrize("pareada", [True, False])
def test_os_dois_botoes_sao_exclusivos(status, pareada):
    """Nunca os dois juntos: 'Desconectar' e 'Apagar histórico' são passos opostos, e
    ver os dois lado a lado é o convite pro clique errado no botão irreversível."""
    v = _mostra(status, pareada)
    assert not (v["sair"] and v["apagar"]), f"{status}/{pareada} mostrou os dois"


@pytest.mark.parametrize("status", ["conectado", "reconectando", "aguardando_qr"])
def test_sessao_viva_nunca_oferece_apagar(status):
    """A trava original, que não pode ter se perdido no caminho: com o celular
    sincronizando, apagar reescreve pela metade."""
    assert _mostra(status, True)["apagar"] is False
    assert _mostra(status, False)["apagar"] is False


def test_conectado_sempre_tem_desconectar():
    assert _mostra("conectado", True)["sair"] is True


# ── o que acontece quando não dá pra saber ────────────────────────────────
@pytest.mark.parametrize("ausente", [..., None])
def test_sem_o_campo_cai_no_comportamento_antigo(ausente):
    """Serviço de versão antiga (janela de deploy) ou banco fora do ar mandam o campo
    ausente/null. Aí a tela repete o que fazia antes — não aposta."""
    assert _mostra("desconectado", ausente) == {"sair": False, "apagar": True}
    assert _mostra("conectado", ausente)["sair"] is True


def test_sem_status_nenhum_nao_conclui_nada():
    """A trava mais antiga do arquivo: sem status o qrShow desvia pro qrIndefinido em
    vez de tratar como desconectado — e nenhum dos dois botões é decidido aqui."""
    fn = _funcao("qrShow")
    js = _DOM % (json.dumps(list(_IDS)), fn, "qrShow({ok:true});", "qr-sair")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["sair"] == "INDEFINIDO"


# ── o cartão do chip 2 ────────────────────────────────────────────────────
def test_o_chip_2_recebeu_a_mesma_correcao():
    """Ele tem função própria de propósito (ver o comentário do CHIP 2), então a
    correção não chega sozinha — e o chip 2 pode ser estacionado igual."""
    assert _mostra("desconectado", True, c2=True)["sair"] is True
    assert _mostra("desconectado", False, c2=True)["sair"] is False
    assert _mostra("conectado", True, c2=True)["sair"] is True


# ── a outra ponta: o campo tem que CHEGAR na tela ─────────────────────────
def test_as_rotas_repassam_pareada():
    """A tabela-verdade acima decide certo com o campo em mãos. Se a rota engolir o
    campo, a tela nunca sai do comportamento antigo e o bug continua de pé — foi
    exatamente assim que o #490 passou verde com o menu quebrado."""
    import inspect
    for rota in (pp.comunicacao_whatsapp_qr_status, pp.comunicacao_whatsapp_qr_iniciar):
        fonte = inspect.getsource(rota)
        assert '"pareada": r.get("pareada")' in fonte, \
            f"{rota.__name__} não repassa o campo"


def test_o_servico_responde_pareada_nas_duas_rotas():
    """E o serviço Node tem que MANDAR. `pareadaPraResposta` responde None quando o
    banco falha, pra tela cair no comportamento antigo em vez de apostar."""
    fonte = open("services/wa-qr/server.js", encoding="utf-8").read()
    assert fonte.count("pareada: await pareadaPraResposta(contaId, s)") == 2, \
        "o /status e o /iniciar têm que mandar o campo"
    corpo = fonte.split("async function pareadaPraResposta")[1].split("\n}")[0]
    assert "return null" in corpo, "banco fora do ar tem que virar indefinido, não false"
    assert "'conectado'" in corpo, "conectada é pareada por definição — sem consulta à toa"
