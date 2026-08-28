"""Nenhum caminho novo pode abrir conversa de WhatsApp por número sem a trava.

Esta é uma regra do repositório, no molde do `test_relatorios_largura.py`: ela não
exercita comportamento, ela lê a fonte e cobra uma invariante.

POR QUE ELA EXISTE — e é uma cicatriz, não uma precaução. Em 27/08/2026 a corrida
que fazia o mesmo cliente virar dois leads (ver `_trava_numero`) foi diagnosticada e
travada. Só que a trava foi posta em UM dos caminhos, o webhook de entrada, e o
autor (eu) declarou o problema resolvido. Havia outros três, e o de maior volume
ficou de fora: o ECO de saída respondia por 8 das 18 mensagens que a produção tinha
gravadas em duas conversas.

O erro não foi de raciocínio sobre concorrência — foi de inventário. Ninguém tinha
a lista de quem cria conversa a partir de um número, então "cobri o caminho" e
"cobri os caminhos" pareceram a mesma frase. É exatamente isso que este arquivo
impede de repetir: a lista passa a ser computada, não lembrada.

DUAS REGRAS, porque uma sozinha tem ponto cego:

  1. Toda função que insere em `conversas` com o canal `'whatsapp'` escrito no SQL
     chama `_trava_numero`.
  2. Toda função que insere em `conversas` DEPOIS de procurar por
     `_conversa_wa_do_contato` chama `_trava_numero` — mesmo que o canal chegue por
     parâmetro e o literal 'whatsapp' não apareça. Procurar por número é a
     assinatura real do problema; o literal é só a pista mais fácil.
"""
from __future__ import annotations

import ast
import functools
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ALVO = RAIZ / "web" / "painel_prospeccao.py"

TRAVA = "_trava_numero("
BUSCA_POR_NUMERO = "_conversa_wa_do_contato("

# Exceção só com NOME e MOTIVO escrito. Exceção anônima é porta dos fundos: quem
# vier depois não tem como saber se aquilo foi pensado ou esquecido.
SEM_TRAVA = {
    "prospeccao_enviar_convite_wa": (
        "não procura a conversa por NÚMERO — procura por `prospeccao_id`, e o "
        "UNIQUE (conta_id, prospeccao_id, canal) da migração 080 já torna o insert "
        "duplo impossível. Corrida por número não a alcança."
    ),
}


@functools.lru_cache(maxsize=1)
def _corpos():
    """{nome: (texto da função, linha)} pra TODA função de `painel_prospeccao.py`.

    Fatiando as linhas na mão, e não com `ast.get_source_segment`: aquele refaz o
    split do arquivo a cada chamada, e num módulo de 14 mil linhas com centenas de
    funções a varredura levava 20 s — por teste. Suíte lenta é suíte que alguém
    deixa de rodar."""
    linhas = ALVO.read_text(encoding="utf-8").splitlines(keepends=True)
    corpos = {}
    for no in ast.walk(ast.parse("".join(linhas))):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            corpos[no.name] = ("".join(linhas[no.lineno - 1:no.end_lineno]), no.lineno)
    return corpos


def _funcoes_com_insert_de_conversa():
    """{nome: (texto da função, linha)} pra toda função que insere em `conversas`."""
    return {nome: v for nome, v in _corpos().items()
            if "insert into conversas" in v[0].lower()}


def test_quem_cria_conversa_de_whatsapp_por_numero_pega_a_trava():
    """A regra 1: o canal escrito no SQL."""
    faltando = []
    for nome, (texto, linha) in _funcoes_com_insert_de_conversa().items():
        if nome in SEM_TRAVA or "whatsapp" not in texto.lower():
            continue
        if TRAVA not in texto:
            faltando.append(f"{nome} (web/painel_prospeccao.py:{linha})")
    assert not faltando, (
        "estas funções abrem conversa de WhatsApp sem pegar a trava do número:\n  "
        + "\n  ".join(faltando)
        + "\n\nChame `_trava_numero(c, conta_id, <numero>)` ANTES da primeira leitura "
          "por número (não antes do insert — é a leitura que abre a janela da "
          "corrida). Se a função for exceção legítima, entre em SEM_TRAVA com o "
          "motivo escrito.")


def test_quem_procura_conversa_por_numero_e_insere_tambem_pega_a_trava():
    """A regra 2, que fecha o ponto cego da 1.

    `_conversa_id` insere com o canal PARAMETRIZADO, então o literal 'whatsapp' não
    aparece no corpo dela — e ela É chamada com 'whatsapp'. Um caminho novo escrito
    nesse molde passaria batido pela regra 1. O que não dá pra disfarçar é procurar
    a conversa pelo número: é isso que a regra 2 cobra."""
    faltando = []
    for nome, (texto, linha) in _funcoes_com_insert_de_conversa().items():
        if nome in SEM_TRAVA:
            continue
        if BUSCA_POR_NUMERO in texto and TRAVA not in texto:
            faltando.append(f"{nome} (web/painel_prospeccao.py:{linha})")
    assert not faltando, (
        "estas funções procuram a conversa pelo NÚMERO e inserem, sem trava:\n  "
        + "\n  ".join(faltando))


def test_a_varredura_ainda_enxerga_os_quatro_caminhos():
    """Sem isto, reescrever o SQL (`INSERT INTO conversas` em maiúscula, ou o literal
    quebrado em duas strings) esvazia as regras acima EM SILÊNCIO, e os testes
    continuam verdes protegendo nada.

    Os quatro nomes estão escritos à mão de propósito: se um deles for renomeado ou
    fundido, quem fizer isso tem que passar por aqui e reafirmar que o novo desenho
    continua travado."""
    achadas = _funcoes_com_insert_de_conversa()
    esperadas = {"_wa_inbound_conversa", "_wa_conversa_simples",
                 "_wa_historico_conversa", "_wa_saida_conversa"}
    sumidas = esperadas - set(achadas)
    assert not sumidas, (
        f"a varredura não achou mais {sorted(sumidas)} — ou a função mudou de nome, "
        "ou o SQL foi reescrito de um jeito que o padrão não pega. Conserte o padrão "
        "antes de seguir: regra que não enxerga nada passa sempre.")
    for nome in esperadas:
        assert TRAVA in achadas[nome][0], f"{nome} perdeu a trava"


def test_sem_trava_nao_tem_entrada_morta():
    """Exceção que sobrevive à função que a justificava vira permissão esquecida."""
    achadas = _funcoes_com_insert_de_conversa()
    mortas = [n for n in SEM_TRAVA if n not in achadas]
    assert not mortas, (
        f"SEM_TRAVA lista {mortas}, que não inserem mais em `conversas`. "
        "Tire da lista.")
    for nome, motivo in SEM_TRAVA.items():
        assert len(motivo) > 40, f"o motivo de {nome} em SEM_TRAVA está vago demais"


def test_a_trava_e_de_transacao_e_pela_chave_certa():
    """`_trava_numero` de fato trava, e do jeito que tem que travar.

    Sem isto, esvaziar o corpo dela deixaria as regras acima verdes — todo mundo
    "chama a trava", e a trava não faz nada."""
    achada = _corpos().get("_trava_numero")
    assert achada, "`_trava_numero` sumiu de web/painel_prospeccao.py"
    corpo = achada[0]
    assert "pg_advisory_xact_lock" in corpo, "a trava deixou de ser tomada"
    assert "hashtext" in corpo, "a chave deixou de ser derivada do número"
    # `pg_advisory_lock(` sem o `xact` é a trava MANUAL: ela não solta no rollback,
    # e a conexão volta pro pool segurando o número pra sempre.
    assert not re.search(r"pg_advisory_lock\s*\(", corpo), (
        "a trava virou manual (`pg_advisory_lock`) — ela tem que ser `xact`, que "
        "solta sozinha no commit E no rollback")
    assert "[-8:]" in corpo, (
        "a chave deixou de ser os 8 dígitos finais. Ela tem que casar com a chave da "
        "BUSCA (`_conversa_wa_do_contato`), senão a trava serializa uma coisa e a "
        "busca casa outra")


def test_nenhum_outro_modulo_abriu_uma_quinta_porta():
    """A varredura acima só olha `painel_prospeccao.py`. Mover o caminho novo pra
    `finance/` seria o jeito mais fácil de escapar dela sem má intenção nenhuma."""
    fora = []
    for pasta in ("web", "finance"):
        for arq in sorted((RAIZ / pasta).glob("*.py")):
            if arq == ALVO:
                continue
            texto = arq.read_text(encoding="utf-8")
            baixo = texto.lower()
            if "insert into conversas" in baixo and "'whatsapp'" in baixo:
                fora.append(str(arq.relative_to(RAIZ)))
    assert not fora, (
        f"{fora} passaram a abrir conversa de WhatsApp. Ou a criação volta pra "
        "`painel_prospeccao.py`, ou a varredura destes testes passa a cobrir esses "
        "arquivos — o que não pode é a regra deixar de valer por mudança de pasta.")
