"""O leitor de QR de cupom não derruba o painel por memória, 27/09/2026.

Às 15:02 um PDF de poucos KB mandado pelo Telegram derrubou o painel
(`openclaw-web-bcu3-va`, 2 GB): o teto de leitura era só em BYTES, e a página
renderizada a 200 DPI e as ampliações x2..x5 da cascata crescem com os PIXELS.
Medido antes do conserto: PDF de 11 KB com página 2000x1500 pt → pico de 3,2 GB;
foto 8000x6000 de 733 KB → não terminou em 170 s.

O que este teste protege:
  * **PDF de página grande e foto grande ficam com pico limitado** (medido num
    processo à parte, como no Render);
  * **o QR continua sendo lido** — em foto comum, em foto grande e em PDF de
    qualquer tamanho de página;
  * **a chave do bot não vai pro log**: o httpx fica em WARNING.
"""
import io
import logging
import subprocess
import sys
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("cv2")
segno = pytest.importorskip("segno")
from PIL import Image  # noqa: E402

from finance import nfce_qr  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
URL = ("https://nfce.fazenda.sp.gov.br/qrcode?"
       "p=35250712345678000191650010000000011234567890|3|1|1|ABCDEF1234")
CHAVE = URL.split("p=")[1][:44]
#: o painel inteiro tem 2 GB e roda 2 workers de ~220 MB; uma leitura de QR
#: acima disto voltaria a ser o risco de 27/09
TETO_MB = 800


def _foto(w, h, frac):
    b = io.BytesIO()
    segno.make(URL, error="m").save(b, kind="png", scale=10, border=4)
    lado = int(w * frac)
    img = Image.new("RGB", (w, h), (238, 236, 230))
    img.paste(Image.open(b).convert("RGB").resize((lado, lado)),
              ((w - lado) // 2, int(h * 0.6)))
    return img


def _jpeg(img):
    o = io.BytesIO()
    img.save(o, "JPEG", quality=75)
    return o.getvalue()


def _pdf(w_pt, h_pt, img):
    d = pymupdf.open()
    p = d.new_page(width=w_pt, height=h_pt)
    p.insert_image(p.rect, stream=_jpeg(img))
    return d.tobytes()


# ------------------------------------------------------------------ memória
_MEDE = r"""
import io, resource, sys
from PIL import Image
import pymupdf
from finance.nfce_qr import ler_chave_de_pdf, ler_chave_da_imagem
tipo, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
img = Image.new("RGB", (800, 600) if tipo == "pdf" else (w, h), (235, 235, 235))
b = io.BytesIO(); img.save(b, "JPEG", quality=60)
if tipo == "pdf":
    d = pymupdf.open(); p = d.new_page(width=w, height=h)
    p.insert_image(p.rect, stream=b.getvalue())
    ler_chave_de_pdf(d.tobytes())
else:
    ler_chave_da_imagem(b.getvalue())
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
"""


@pytest.mark.parametrize("tipo,w,h", [
    ("pdf", 2000, 1500),   # o caso de 27/09: 3,2 GB antes
    ("pdf", 8000, 6000),   # foto de 48 MP convertida em PDF
    ("img", 8000, 6000),   # a mesma foto mandada como imagem
])
def test_arquivo_de_pagina_ou_foto_grande_tem_pico_limitado(tipo, w, h):
    r = subprocess.run([sys.executable, "-c", _MEDE, tipo, str(w), str(h)],
                       cwd=RAIZ, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    pico = int(r.stdout.strip().splitlines()[-1])
    assert pico < TETO_MB, f"{tipo} {w}x{h}: pico de {pico} MB"


def test_a_pagina_nunca_passa_do_lado_maximo():
    assert nfce_qr._zoom_pagina(595, 842) * 842 <= nfce_qr._MAX_LADO_PX
    assert nfce_qr._zoom_pagina(8000, 6000) * 8000 <= nfce_qr._MAX_LADO_PX
    # página pequena continua a 200 DPI
    assert nfce_qr._zoom_pagina(200, 300) == pytest.approx(200 / 72)


# ------------------------------------------------------------------ leitura
@pytest.mark.parametrize("w,h,frac", [
    (960, 1280, .25),    # foto do Telegram
    (1200, 1600, .15),   # foto do WhatsApp
    (4000, 3000, .045),  # foto direta, QR pequeno: encolher não pode perder
])
def test_o_qr_da_foto_continua_sendo_lido(w, h, frac):
    assert nfce_qr.ler_chave_da_imagem(_jpeg(_foto(w, h, frac))) == CHAVE


@pytest.mark.parametrize("w_pt,h_pt", [(595, 842), (2000, 1500), (8000, 6000)])
def test_o_qr_do_pdf_continua_sendo_lido(w_pt, h_pt):
    pdf = _pdf(w_pt, h_pt, _foto(1240, 1754, .22))
    assert nfce_qr.ler_chave_de_pdf(pdf) == CHAVE


# ------------------------------------------------------------------ o log
def test_a_chave_do_bot_nao_vai_pro_log():
    """O httpx em INFO escreve a URL de cada chamada — e a da API do Telegram
    tem a chave do bot no caminho. Tem que ficar em WARNING ou acima."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import logging, web.app, telegram_bot; "
         "print(logging.getLogger('httpx').getEffectiveLevel())"],
        cwd=RAIZ, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert int(r.stdout.strip().splitlines()[-1]) >= logging.WARNING
