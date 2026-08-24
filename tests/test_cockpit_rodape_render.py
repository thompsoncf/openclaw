"""Mede no navegador quanta folga sobra embaixo do botão do rodapé.

O teste-irmão (test_cockpit_rodape_android.py) lê a FONTE e garante que o piso está
escrito. Este abre o CSS real num Chromium e mede o resultado — é a diferença entre
"a regra existe" e "o botão está alcançável".

Foi essa medição que fechou o diagnóstico em 23/08: com `env(safe-area-inset-bottom)`
voltando zero (que é o caso do Android com os três botões), o rodapé reservava 12,8px
e o botão parava a 13px do fim da tela, atrás de uma barra de ~48px. Com o piso, 61px.

Pula sozinho onde não houver Chromium — o valor está em rodar onde ele existe, não em
obrigar o ambiente a ter um.
"""
import os
import re

import pytest

from web import painel_cockpit as pc

#: barra de navegação de três botões do Android, o caso que motivou o piso
_BARRA_ANDROID = 48


def _pagina() -> str:
    """Só o CSS do app + o esqueleto do shell: sem servidor, sem sessão, sem banco.
    O que importa aqui é a caixa, não o conteúdo dela."""
    css = re.search(r"<style>(.*)</style>", pc._CSS, re.S).group(1)
    return ("<!doctype html><meta name=viewport "
            "content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<style>{css}</style>"
            "<div class=wrap><div class=scroll style='height:3000px'>miolo</div>"
            "<div class=rodape-b><button class=btn>Marcar</button></div></div>")


@pytest.fixture(scope="module")
def folga(tmp_path_factory):
    """Quantos px sobram entre o fim do botão e o fim da viewport."""
    playwright = pytest.importorskip("playwright.sync_api")
    alvo = tmp_path_factory.mktemp("rodape") / "shell.html"
    alvo.write_text(_pagina(), encoding="utf-8")
    # `PLAYWRIGHT_CHROMIUM_BIN` existe porque o Chromium pré-instalado de um ambiente
    # nem sempre bate com a versão que o Playwright procura sozinho — e aí o teste
    # pularia justamente onde há navegador. Sem a variável, o caminho é o de sempre.
    binario = os.environ.get("PLAYWRIGHT_CHROMIUM_BIN") or None
    try:
        with playwright.sync_playwright() as p:
            navegador = p.chromium.launch(executable_path=binario)
            pag = navegador.new_page(viewport={"width": 393, "height": 852})
            pag.goto(alvo.as_uri())
            medida = pag.evaluate(
                """() => {
                    const b = document.querySelector('.rodape-b .btn').getBoundingClientRect();
                    return Math.round(innerHeight - b.bottom);
                }""")
            navegador.close()
            return medida
    except Exception as e:  # noqa: BLE001 — sem navegador no ambiente, não é falha
        pytest.skip(f"sem Chromium utilizável: {e}")


def test_o_botao_do_rodape_nao_fica_atras_da_barra_do_android(folga):
    """O Chromium não reporta `safe-area-inset-bottom`, então ele reproduz exatamente
    o aparelho que quebrou: é o pior caso, e é nele que a medida tem que fechar."""
    assert folga >= _BARRA_ANDROID, (
        f"só {folga}px de folga embaixo do botão — a barra de navegação do Android "
        f"come ~{_BARRA_ANDROID}px e esconderia o botão, como em 23/08")
