"""O QR tem prazo, e a tela nunca disse isso.

A tela de conectar WhatsApp mostrava o código e uma frase só, do primeiro ao
último segundo: "Escaneie o QR no WhatsApp do celular (Aparelhos conectados >
Conectar aparelho)". Faltava contar três coisas ao cliente:

  1. que existe relógio — ele não sabia se tinha pressa;
  2. que o código vai ser TROCADO — quando trocava, parecia defeito;
  3. que existe um VÃO entre um lote de códigos e o seguinte, em que a caixa some
     da tela — e some calada, o que parecia travamento.

Os tempos NÃO são estimativa. Saem do Baileys, em
`node_modules/@whiskeysockets/baileys/lib/Socket/socket.js`:

    linha 464   let qrMs = qrTimeout || 60000;   // primeiro código do lote
    linha 478   qrMs = qrTimeout || 20000;       // os seguintes

Esses são os PADRÕES, de quem não configura nada. Desde 30/08 o serviço passa
`qrTimeout: 60000`, que vale pros dois — o lote inteiro sai de 140s (60+20x4) pra
300s. Não dá pra esticar só o primeiro: a opção é única.

e quando os `ref` do lote acabam:

    end(new Boom('QR refs attempts ended', { statusCode: DisconnectReason.timedOut }))

que é um 408. O nosso handler de `close` não trata 408 de forma especial, então
cai no religa-genérico: `iniciarSessao` em 2,5s, socket novo, LOTE NOVO de
códigos. Por isso o texto do vão diz "buscando um novo" e não "expirou" — expirar
pra sempre não é o que acontece, e prometer o contrário seria mentir.

Referência real de produção: a conta 23 pareou em 26/08 às 11:17:48 e o
"WhatsApp conectado" saiu 11:18:37 — o dono levou 42 s e passou no PRIMEIRO
código, com 18 s de folga. É a medida de que os 60 s iniciais são apertados pra
quem não está com o celular já aberto.

O que este teste protege:

  * os dois chips têm o relógio. O cartão do chip 2 é cópia deliberada do chip 1
    (ver o comentário no painel: parâmetro compartilhado erraria de chip e mexeria
    na sessão do outro) — e cópia é exatamente onde falta um pedaço sem ninguém
    ver. Metade da suíte aqui é "e o chip 2 também";
  * os tempos batem com o Baileys INSTALADO. Se um upgrade mudar 60000/20000, o
    teste quebra em vez de a tela passar a mentir;
  * a conta é conservadora (desconta o atraso do polling). O número na tela tem
    que ser piso, não promessa: dizer 60 quando faltam 57 é enganar o cliente bem
    na hora em que ele está com o celular na mão;
  * o relógio some quando não é hora dele (conectado, desconectado);
  * pedir QR novo REINICIA a contagem;
  * e os dois lados contam o MESMO prazo. O relógio roda no navegador sem o
    serviço mandar o número, então uma constante solta em cada lugar faz a tela
    mentir com cara de certeza — o teste que amarra os dois é a única coisa
    segurando isso.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_painel_js_sintaxe import _render, _scripts

RAIZ = Path(__file__).resolve().parent.parent
SOCKET_JS = (RAIZ / "services" / "wa-qr" / "node_modules" / "@whiskeysockets"
             / "baileys" / "lib" / "Socket" / "socket.js")


@pytest.fixture(scope="module")
def html() -> str:
    # O cartão do chip 2 vive atrás de `{% set chip2 = canais.chips[1] ... %}` +
    # `{% if chip2 %}`: sem DOIS chips no contexto ele não renderiza, e um teste
    # que só olhasse o chip 1 passaria feliz com metade da tela faltando — que é
    # exatamente o risco de um cartão duplicado à mão.
    return _render(
        "prospeccao_comunicacao",
        canais={"whatsapp": True, "wa_provedor": "qr",
                "numeros": {"whatsapp": "+5586999999999"}, "tokens_set": {},
                "chips": [
                    {"id": 34, "apelido": "", "nome": "Manoel", "numero": "+5586999999999",
                     "estado": "conectado", "sem_receber": None},
                    {"id": 36, "apelido": "CP Thiago", "nome": "CP Thiago",
                     "numero": "+5586988888888", "estado": "conectado", "sem_receber": None},
                ]})


@pytest.fixture(scope="module")
def js(html) -> str:
    return "\n".join(_scripts(html))


# ── os tempos vêm do Baileys, não da nossa cabeça ────────────────────────────
def test_o_qrTimeout_do_baileys_continua_valendo_pros_dois():
    """Trava contra upgrade da lib.

    O `qrTimeout` sobrescreve o prazo do PRIMEIRO código e o dos SEGUINTES — é o
    que torna impossível esticar só o primeiro, e é a razão de o serviço passar um
    valor único. Se um upgrade separar os dois, ou trocar o nome da opção, o nosso
    60 uniforme deixa de ser o que a tela está contando."""
    if not SOCKET_JS.exists():
        pytest.skip("baileys não instalado neste ambiente (npm install em services/wa-qr)")
    src = SOCKET_JS.read_text(encoding="utf-8")
    assert "qrTimeout || 60000" in src, "mudou o padrão do primeiro QR no Baileys"
    assert "qrTimeout || 20000" in src, "mudou o padrão dos QR seguintes no Baileys"


def test_o_servico_estica_os_seguintes_pra_60():
    """O ganho de 30/08: o lote inteiro passa de 140s (60+20x4) pra 300s, e quem
    demora a pegar o celular vê o código trocar 4x menos.

    60 e não mais: é o único prazo com prova de produção (a conta 23 pareou em
    26/08 levando 42s no primeiro código). Acima disso é chute, e chutar pra cima
    aqui tem custo assimétrico — se o servidor invalidar o ref antes do nosso
    relógio, a tela mostra código MORTO e quem escaneia não recebe erro nenhum."""
    srv = (RAIZ / "services" / "wa-qr" / "server.js").read_text(encoding="utf-8")
    assert "qrTimeout: QR_TIMEOUT_MS" in srv, "o serviço parou de passar o qrTimeout"
    m = re.search(r"WA_QR_QRTIMEOUT_MS \|\| '(\d+)'", srv)
    assert m, "sumiu o default do WA_QR_QRTIMEOUT_MS"
    ms = int(m.group(1))
    assert ms == 60000, f"prazo do QR em {ms}ms — só 60000 tem prova de produção"


def test_o_painel_conta_o_MESMO_prazo_que_o_servico_manda():
    """Os dois lados TÊM que bater. O relógio da tela é contado no navegador, sem
    o serviço mandar o prazo — então um número solto aqui e outro lá faz a tela
    mentir com cara de certeza. Este teste é a única coisa ligando os dois."""
    srv = (RAIZ / "services" / "wa-qr" / "server.js").read_text(encoding="utf-8")
    ms = int(re.search(r"WA_QR_QRTIMEOUT_MS \|\| '(\d+)'", srv).group(1))
    seg = ms // 1000
    tpl = (RAIZ / "web" / "painel_prospeccao.py").read_text(encoding="utf-8")
    m = re.search(r"var QR_1O=(\d+), QR_SEG=(\d+)", tpl)
    assert m, "não achei as constantes do relógio no painel"
    assert int(m.group(1)) == seg, (
        f"o painel conta {m.group(1)}s no primeiro código, o serviço manda {seg}s")
    assert int(m.group(2)) == seg, (
        f"o painel conta {m.group(2)}s nos seguintes, o serviço manda {seg}s.\n"
        f"qrTimeout vale pros DOIS — não existe primeiro diferente do resto.")


# ── a conta é conservadora ───────────────────────────────────────────────────
def test_desconta_o_atraso_do_polling(js):
    """O polling é de 3s: um código novo pode ser descoberto até 3s depois de
    nascer. Descontar faz o número virar piso."""
    limpo = js.replace(" ", "")
    assert "QR_ATRASO=3" in limpo
    assert "QR_1O:QR_SEG)-QR_ATRASO" in limpo, \
        "a semente da contagem tem que descontar o atraso do polling"


def test_a_contagem_nunca_comeca_negativa(js):
    assert "Math.max(1," in js, "sem o piso, um atraso maior que o prazo daria número negativo"


# ── os dois chips ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("elemento", [
    "relogio", "anel", "seg", "rel-tit", "rel-det", "passos", "fim"])
def test_os_dois_chips_tem_o_elemento(html, elemento):
    """O cartão do chip 2 é cópia; é aí que falta um pedaço calado."""
    assert f'id="qr-{elemento}"' in html, f"chip 1 sem {elemento}"
    assert f'id="c2-{elemento}"' in html, f"chip 2 sem {elemento}"


@pytest.mark.parametrize("fn", ["Relogio", "RelPinta", "RelNovo", "RelPara", "FimMostra"])
def test_os_dois_chips_tem_a_funcao(js, fn):
    assert f"function qr{fn}(" in js, f"chip 1 sem qr{fn}"
    assert f"function c2{fn}(" in js, f"chip 2 sem c2{fn}"


def test_os_dois_chips_chamam_o_relogio_no_show(js):
    assert re.search(r"function qrShow\(d\)\{[\s\S]{0,3000}?qrRelogio\(d\)", js), \
        "qrShow não chama qrRelogio"
    assert re.search(r"function c2Show\(d\)\{[\s\S]{0,3000}?c2Relogio\(d\)", js), \
        "c2Show não chama c2Relogio"


def test_gerar_qr_reinicia_a_contagem_nos_dois(js):
    """Sem isto o próximo código herda os 20s do fim da tentativa anterior."""
    assert re.search(r"function qrIniciar\(\)\{[\s\S]{0,600}?qrRelPara\(\)", js), \
        "qrIniciar não reinicia o relógio"
    assert re.search(r"function c2Iniciar\(\)\{[\s\S]{0,600}?c2RelPara\(\)", js), \
        "c2Iniciar não reinicia o relógio"


# ── o comportamento, rodando de verdade ──────────────────────────────────────
CENARIO = r"""
// DOM de mentira, só o suficiente pro relógio rodar: cada getElementById
// devolve um objeto com as propriedades que o código toca.
var _els={};
function _el(){return {textContent:'',style:{display:'',setProperty:function(k,v){this[k]=v;}}};}
global.document={getElementById:function(id){ if(!_els[id])_els[id]=_el(); return _els[id]; }};
var _timers=[];
global.setInterval=function(fn){_timers.push(fn);return _timers.length;};
global.clearInterval=function(){};
%s
function tique(n){for(var i=0;i<n;i++)_timers.forEach(function(f){f();});}
function saida(){return {
  seg:_els['qr-seg'].textContent,
  tit:_els['qr-rel-tit'].textContent,
  det:_els['qr-rel-det'].textContent,
  fim:_els['qr-fim'].style.display,
  relogio:_els['qr-relogio'].style.display,
  passos:_els['qr-passos'].style.display,
  cor:_els['qr-anel'].style['--zqr-cor']};}
var out=[];
// 1) primeiro código do lote
qrRelogio({status:'aguardando_qr',qr:'data:img,AAA'});
out.push(['primeiro',saida()]);
// 2) passa o tempo até entrar na faixa de aviso
tique(50);
out.push(['faltando_pouco',saida()]);
// 3) o prazo acaba sem código novo -> o vão
tique(10);
out.push(['vao',saida()]);
// 4) chega um código NOVO (segundo do lote, vale 20s)
qrRelogio({status:'aguardando_qr',qr:'data:img,BBB'});
out.push(['segundo',saida()]);
// 5) o mesmo código chegando de novo no polling NÃO pode reiniciar a conta
tique(5);
var antes=_els['qr-seg'].textContent;
qrRelogio({status:'aguardando_qr',qr:'data:img,BBB'});
out.push(['repolling',{seg:_els['qr-seg'].textContent,antes:antes}]);
// 6) 'aguardando_qr' SEM imagem = vão entre lotes
qrRelogio({status:'aguardando_qr'});
out.push(['sem_imagem',saida()]);
// 7) o VÃO DE VERDADE: quando os ref acabam o serviço reporta 'reconectando'
//    por ~2,5s. O aviso tem que CONTINUAR na tela nessa janela.
qrRelogio({status:'aguardando_qr',qr:'data:img,CCC'});   // volta pro fluxo
qrRelogio({status:'reconectando'});
out.push(['reconectando_1',saida()]);
qrRelogio({status:'reconectando'});                      // segunda volta do polling
out.push(['reconectando_2',saida()]);
// 8) o lote novo chega: o primeiro código dele vale 60s de novo, não 20
qrRelogio({status:'aguardando_qr',qr:'data:img,DDD'});
out.push(['lote_novo',saida()]);
// 9) conectou -> o relógio some
qrRelogio({status:'conectado'});
out.push(['conectado',saida()]);
// 10) 'reconectando' FORA de um fluxo de QR não pode acender nada
qrRelogio({status:'reconectando'});
out.push(['reconectando_solto',saida()]);
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def rodado(js, tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    # só o pedaço do relógio: o resto do bloco toca window/fetch e não interessa aqui
    ini = js.index("var QR_1O=")
    fim = js.index("function qrShow(d)")
    trecho = js[ini:fim]
    d = tmp_path_factory.mktemp("relogio")
    alvo = d / "cenario.js"
    alvo.write_text(CENARIO % trecho, encoding="utf-8")
    r = subprocess.run(["node", str(alvo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:900]
    import json
    return dict(json.loads(r.stdout))


def test_o_primeiro_codigo_mostra_a_janela_confortavel(rodado):
    p = rodado["primeiro"]
    assert p["seg"] == 57, "60 menos o atraso de 3s do polling"
    assert p["tit"] == "Escaneie agora"
    assert "57 segundos" in p["det"]
    assert p["relogio"] == "flex" and p["passos"] == "block"


def test_perto_do_fim_avisa_que_vai_trocar(rodado):
    p = rodado["faltando_pouco"]
    assert p["tit"] == "Vai trocar de código"
    assert "pode esperar" in p["det"], "o texto tem que acalmar, não assustar"
    assert p["cor"] == "var(--ambar)", "âmbar só nos últimos 10s"


def test_quando_o_prazo_acaba_a_tela_explica_o_vao(rodado):
    """Não diz 'expirou': o serviço religa e pega um lote novo."""
    p = rodado["vao"]
    assert p["fim"] == "block"
    assert p["tit"] == "Buscando um código novo"
    assert p["seg"] == 0


def test_codigo_novo_reinicia_a_contagem(rodado):
    """Desde 30/08 o segundo código vale os mesmos 60s do primeiro: o serviço passa
    qrTimeout=60000, e esse parâmetro do Baileys não distingue um do outro. Antes
    eram 20s aqui, que era o padrão de quem não configura nada."""
    p = rodado["segundo"]
    assert p["seg"] == 57, "60 do segundo código menos o atraso de 3s do polling"
    assert p["tit"] == "Código renovado"
    assert p["fim"] == "none", "o aviso do vão tem que sumir quando o código chega"


def test_o_mesmo_codigo_no_polling_nao_reinicia_a_conta(rodado):
    """O polling repete a mesma imagem de 3 em 3s. Reiniciar a cada volta deixaria
    o número parado e o cliente sem saber que o tempo anda."""
    p = rodado["repolling"]
    assert p["seg"] == p["antes"], "a contagem foi reiniciada por uma imagem repetida"


def test_aguardando_sem_imagem_e_o_vao(rodado):
    assert rodado["sem_imagem"]["fim"] == "block"


# ── o vão de verdade passa por 'reconectando' ────────────────────────────────
def test_o_aviso_do_vao_sobrevive_ao_reconectando(rodado):
    """O caso que quase passou batido. Quando os `ref` acabam, o serviço NÃO
    reporta 'aguardando_qr' — reporta 'reconectando' pelos ~2,5 s até o socket
    novo. A primeira versão deste código desmontava a tela nesse estado, e o
    aviso do vão aparecia e sumia: o cliente ficava sem explicação bem no sumiço
    que o aviso existe pra explicar."""
    for volta in ("reconectando_1", "reconectando_2"):
        p = rodado[volta]
        assert p["fim"] == "block", f"{volta}: o aviso do vão sumiu"
        assert p["relogio"] == "none", f"{volta}: anel de código morto na tela"


def test_o_lote_novo_volta_a_valer_sessenta(rodado):
    """Depois do vão vem um lote NOVO, e o primeiro código dele dura 60 s. Se o
    contador não zerasse, a tela mostraria 17 onde valem 57."""
    p = rodado["lote_novo"]
    assert p["seg"] == 57, "o primeiro código do lote novo tem que valer 60-3"
    assert p["tit"] == "Escaneie agora"
    assert p["fim"] == "none"


def test_reconectando_fora_do_fluxo_nao_acende_nada(rodado):
    """Uma queda comum de sessão conectada também passa por 'reconectando'. Ali
    não há pareamento em curso, e acender o aviso do QR seria ruído puro."""
    p = rodado["reconectando_solto"]
    assert p["fim"] == "none" and p["relogio"] == "none" and p["passos"] == "none"


def test_conectado_tira_o_relogio_da_tela(rodado):
    p = rodado["conectado"]
    assert p["relogio"] == "none" and p["passos"] == "none" and p["fim"] == "none"


# ── o passo a passo ──────────────────────────────────────────────────────────
def test_o_caminho_no_celular_virou_lista(html):
    """Antes era um parêntese no fim da frase. Vira três passos que o cliente
    segue enquanto o relógio corre."""
    for chip in ("qr", "c2"):
        bloco = html.split(f'id="{chip}-passos"', 1)[1][:600]
        assert "Aparelhos conectados" in bloco
        assert "Conectar aparelho" in bloco
        assert bloco.count("<li>") == 3, f"{chip}: o passo a passo tem que ter 3 passos"


def test_o_aviso_do_vao_diz_que_nada_se_perdeu(html):
    """O pior de ficar sem código não é ficar sem código — é achar que quebrou
    alguma coisa na conta."""
    for chip in ("qr", "c2"):
        bloco = html.split(f'id="{chip}-fim"', 1)[1][:400]
        assert "nada foi perdido" in bloco.lower()
