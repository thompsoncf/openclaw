"""O JS que o NAVEGADOR recebe tem que compilar.

POR QUE ESTE TESTE EXISTE. Em 17/08 o bloco <script> da aba Canais ficou morto por
erro de sintaxe e ninguém percebeu por horas: o botão do QR ficava preso em
"Verificando…", `qrShow`/`qrPoll`/`qrIniciar`/`qrSair` nunca eram definidos, e a
tela mostrava o estado estático do HTML.

A causa foi de ESCAPE, e é uma armadilha estrutural deste arquivo: os templates são
strings Python COMUNS (`_COMUNICACAO_TPL = \"\"\"...\"\"\"`, sem `r`), então um `\\n`
escrito dentro de uma string JavaScript vira newline LITERAL no HTML servido — e
uma string de aspas simples cortada no meio é SyntaxError, que derruba o bloco
inteiro, não só aquela linha.

E o pior: nada disso aparece lendo o .py. `node --check` no trecho copiado do
código-fonte PASSA, porque ali `\\n` ainda são dois caracteres. O erro só existe
depois do render. Foi exatamente assim que passou pelo CI e por três revisões.

Este teste fecha esse buraco pelo único caminho que funciona: renderiza o template
de verdade e roda `node --check` em cada <script> do resultado.

Pula sozinho se não houver `node` no ambiente — o valor está em rodar no CI, não em
travar quem não tem Node instalado.
"""
from __future__ import annotations

import re
import shutil
import subprocess

import jinja2
import pytest

from web import painel_prospeccao as pp

# páginas do módulo que carregam JS próprio, pelo nome com que são registradas no
# loader. Crescer esta lista é de graça; deixar uma de fora é o que custou caro.
PAGINAS = ["prospeccao_comunicacao"]


class _Mudo(jinja2.Undefined):
    """Undefined que aceita qualquer acesso e imprime nada.

    O alvo aqui é a SINTAXE do JS, não a semântica dos dados: montar um contexto
    fiel de cada tela seria manutenção sem retorno, e um dado faltando não muda se
    o script compila."""

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _Mudo()

    def __getitem__(self, k):
        return _Mudo()

    def __call__(self, *a, **k):
        return _Mudo()

    def __str__(self):
        return ""

    def __html__(self):
        return ""

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _render(nome: str) -> str:
    env = jinja2.Environment(
        loader=jinja2.DictLoader(dict(pp._env.loader.mapping)),
        undefined=_Mudo, autoescape=True)
    for k, v in pp._env.filters.items():
        env.filters.setdefault(k, v)
    for k, v in pp._env.globals.items():
        env.globals.setdefault(k, v)
    # `gerencia` e o provedor 'qr' ligados: é o ramo que traz o bloco do QR, que é
    # justamente o que quebrou. Sem eles o {% if %} esconde o script e o teste
    # passaria sem olhar nada.
    return env.get_template(nome).render(
        gerencia=True,
        canais={"whatsapp": True, "wa_provedor": "qr",
                "numeros": {"whatsapp": "+5586999999999"}, "tokens_set": {}})


def _scripts(html: str) -> list[str]:
    return [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if b.strip()]


@pytest.mark.parametrize("pagina", PAGINAS)
def test_todo_script_da_pagina_compila(pagina, tmp_path):
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    html = _render(pagina)
    blocos = _scripts(html)
    assert blocos, f"{pagina}: nenhum <script> encontrado — o render mudou de forma?"
    erros = []
    for i, bloco in enumerate(blocos):
        alvo = tmp_path / f"{pagina}_{i}.js"
        alvo.write_text(bloco, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(alvo)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            erros.append(f"bloco {i}:\n{r.stderr.strip()[:600]}")
    assert not erros, (
        f"{pagina}: {len(erros)} bloco(s) de <script> não compilam no navegador.\n"
        + "\n\n".join(erros))


def test_o_bloco_do_qr_esta_mesmo_na_pagina():
    """Guarda do teste acima: se o bloco do QR sumir do render, o teste de sintaxe
    fica verde sem ter olhado o código que já quebrou uma vez."""
    js = "\n".join(_scripts(_render("prospeccao_comunicacao")))
    for fn in ("qrShow", "qrPoll", "qrIniciar", "qrSair", "qrApagar", "qrEsperando"):
        assert fn in js, f"{fn} não está no JS servido"


def test_nenhuma_string_js_quebrada_por_newline_literal():
    """A armadilha específica, dita por extenso: string JS de aspas simples com
    newline literal dentro. `node --check` já pega, mas este teste NOMEIA a causa —
    quem quebrar de novo lê o motivo em vez de decifrar um SyntaxError."""
    for bloco in _scripts(_render("prospeccao_comunicacao")):
        for n, linha in enumerate(bloco.split("\n"), 1):
            # aspas simples ímpares na linha = string aberta que atravessa o \n
            if linha.count("'") % 2:
                pytest.fail(
                    f"linha {n} deixa uma string JS aberta: {linha.strip()[:90]!r}\n"
                    "Se era pra ser uma quebra de linha no texto, escreva \\\\n — "
                    "o template é string Python comum e um \\n vira newline literal.")
