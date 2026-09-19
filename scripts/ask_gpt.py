"""Consulta o GPT durante o DESENVOLVIMENTO — segunda opinião sobre código.

Ferramenta de DEV: nada do app importa este arquivo. Serve pra pedir revisão
cruzada, tirar dúvida de API ou comparar abordagem — sem trocar o modelo do
agente de código.

O `openai` já é dependência do projeto (requirements.txt), então não precisa
instalar nada. Só falta a chave no .env:

    OPENAI_API_KEY=sk-...
    OPENAI_MODEL=gpt-5.4        # opcional; veja --models pro nome exato

Uso:
    python -m scripts.ask_gpt "sua pergunta aqui"
    python -m scripts.ask_gpt --models            # modelos que a sua chave acessa
    type finance\\empresa.py | python -m scripts.ask_gpt "revisa esta funcao"
    cat finance/empresa.py | python -m scripts.ask_gpt "revisa esta funcao"
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

# No Windows o console entrega stdin/stdout na codepage local (cp1252), o que
# corrompe acento/emoji ao pipar arquivo. Força UTF-8 nos três fluxos.
for _f in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # já é UTF-8, ou fluxo sem reconfigure
        pass

MODELO = os.environ.get("OPENAI_MODEL", "gpt-5.4")

INSTRUCAO = (
    "Você é um engenheiro sênior revisando código. Seja direto e específico: "
    "aponte bugs concretos, riscos e alternativas melhores. Sem preâmbulo, sem "
    "elogio vazio. Se não tiver contexto suficiente, diga exatamente o que falta."
)


def _cliente():
    chave = os.environ.get("OPENAI_API_KEY")
    if not chave:
        sys.exit(
            "❌ OPENAI_API_KEY não configurada.\n"
            "   Pegue em https://platform.openai.com/api-keys e adicione no .env:\n"
            "     OPENAI_API_KEY=sk-...\n"
            "   (o .env já é gitignored — nunca commite a chave)"
        )
    from openai import OpenAI
    return OpenAI(api_key=chave)


def _listar_modelos(cli) -> None:
    """Lista o que a chave REALMENTE acessa — evita chutar nome de modelo."""
    nomes = sorted(
        m.id for m in cli.models.list().data
        if m.id.startswith(("gpt", "o1", "o3", "o4"))
    )
    print(f"Modelos disponíveis para esta chave ({len(nomes)}):")
    for n in nomes:
        print("  " + n)
    print(f"\nAtual (OPENAI_MODEL ou default): {MODELO}")


def main() -> None:
    args = sys.argv[1:]
    cli = _cliente()

    if "--models" in args:
        _listar_modelos(cli)
        return

    pergunta = " ".join(args).strip()
    # contexto pipado (arquivo/diff); se for terminal interativo, não trava
    contexto = "" if sys.stdin.isatty() else sys.stdin.read().strip()
    if not pergunta and not contexto:
        sys.exit('Uso: python -m scripts.ask_gpt "sua pergunta"   (ou pipe um arquivo)')

    # pergunta primeiro, contexto depois — deixa claro pro modelo o que é o quê
    partes = [p for p in (pergunta, f"--- CONTEXTO ---\n{contexto}" if contexto else "") if p]
    conteudo = "\n\n".join(partes)

    try:
        r = cli.chat.completions.create(
            model=MODELO,
            messages=[{"role": "system", "content": INSTRUCAO},
                      {"role": "user", "content": conteudo}],
        )
    except Exception as e:  # noqa: BLE001 — é script de dev, erro cru ajuda mais
        msg = str(e)
        print(f"❌ Erro: {msg}", file=sys.stderr)
        if "model" in msg.lower():
            print("   Dica: rode `python -m scripts.ask_gpt --models` pra ver os nomes\n"
                  "   válidos e ponha OPENAI_MODEL=<nome> no .env.", file=sys.stderr)
        sys.exit(1)

    print(r.choices[0].message.content or "(resposta vazia)")
    if r.usage:
        print(f"\n— {MODELO} | tokens: {r.usage.prompt_tokens} in / "
              f"{r.usage.completion_tokens} out —", file=sys.stderr)


if __name__ == "__main__":
    main()
