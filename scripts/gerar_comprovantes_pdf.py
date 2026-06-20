"""Converte comprovantes .txt em PDF pra validação com Vision API."""
import os
from pathlib import Path

def gerar_pdfs():
    """Converte todos os .txt em test_data/ para .pdf."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import mm
    except ImportError:
        print("Erro: reportlab não está instalado.")
        print("Instala com: pip install reportlab")
        return False

    test_data_dir = Path(__file__).parent.parent / "test_data"
    txt_files = list(test_data_dir.glob("comprovante_*.txt"))

    if not txt_files:
        print("Nenhum arquivo .txt encontrado em test_data/")
        return False

    for txt_file in txt_files:
        pdf_file = txt_file.with_suffix(".pdf")
        print(f"Convertendo {txt_file.name} → {pdf_file.name}...")

        # Lê o conteúdo do .txt
        with open(txt_file, "r", encoding="utf-8") as f:
            linhas = f.readlines()

        # Cria PDF
        c = canvas.Canvas(str(pdf_file), pagesize=letter)
        width, height = letter
        y = height - 20  # começa perto do topo

        for linha in linhas:
            linha = linha.rstrip()
            if y < 20:  # se saiu da página, próxima página
                c.showPage()
                y = height - 20
            c.drawString(20, y, linha)
            y -= 14  # espaçamento entre linhas

        c.save()
        print(f"  ✅ {pdf_file.name} gerado!")

    print(f"\n✅ {len(txt_files)} PDFs gerados em test_data/")
    return True


if __name__ == "__main__":
    gerar_pdfs()
