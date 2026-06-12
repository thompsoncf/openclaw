"""A Fabrica de agentes.

Um Agente e' so' a combinacao de: persona (quem ele e'), ferramentas (o que
sabe fazer), memoria (o que lembra) e cerebro (o Claude). Trocando esse
recheio, a mesma fabrica produz qualquer agente: financeiro, agenda, etc.
"""
from dataclasses import dataclass
from typing import Callable

from .brain import Brain
from .memory import MemoriaConversa


@dataclass
class Ferramenta:
    nome: str
    descricao: str
    parametros: dict          # JSON schema (input_schema do Claude)
    executar: Callable        # recebe dict de entrada, devolve str

    def schema(self) -> dict:
        return {"name": self.nome, "description": self.descricao, "input_schema": self.parametros}


class Agente:
    def __init__(self, nome: str, persona: str, ferramentas: list[Ferramenta],
                 brain: Brain, memoria: MemoriaConversa | None = None,
                 max_iteracoes: int = 10):
        self.nome = nome
        self.persona = persona
        self.ferramentas = {f.nome: f for f in ferramentas}
        self.brain = brain
        self.memoria = memoria or MemoriaConversa()
        self.max_iteracoes = max_iteracoes

    def responder(self, texto: str, imagem_b64: str | None = None,
                  media_type: str = "image/jpeg") -> str:
        conteudo = []
        if imagem_b64:
            conteudo.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": imagem_b64}})
        conteudo.append({"type": "text", "text": texto})
        self.memoria.adicionar("user", conteudo)

        schemas = [f.schema() for f in self.ferramentas.values()]
        houve_ferramenta = False

        for _ in range(self.max_iteracoes):
            resp = self.brain.chamar(self.persona, self.memoria.mensagens(), schemas)
            self.memoria.adicionar("assistant", resp.content)

            if resp.stop_reason != "tool_use":
                final = self._texto(resp)
                if final:
                    return final
                # terminou sem texto: se fez alguma acao, confirma; senao, pede pra repetir
                return ("Pronto, atualizei aqui! ✅" if houve_ferramenta
                        else "Desculpa, nao entendi. Pode repetir?")

            houve_ferramenta = True
            resultados = []
            for bloco in resp.content:
                if getattr(bloco, "type", None) != "tool_use":
                    continue
                ferr = self.ferramentas.get(bloco.name)
                try:
                    saida = ferr.executar(bloco.input) if ferr else f"Ferramenta '{bloco.name}' nao existe."
                except Exception as e:  # noqa: BLE001 - o agente recebe o erro e segue
                    saida = f"Erro ao executar {bloco.name}: {e}"
                resultados.append({"type": "tool_result", "tool_use_id": bloco.id, "content": str(saida)})
            self.memoria.adicionar("user", resultados)

        # estourou o limite de iteracoes
        return ("Registrei o que deu, mas me embananei no meio. Confere o saldo pra garantir?"
                if houve_ferramenta
                else "Desculpa, me embananei aqui e nao consegui finalizar. Pode repetir?")

    @staticmethod
    def _texto(resp) -> str:
        partes = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(partes).strip()


def criar_agente(nome: str, persona: str, ferramentas: list[Ferramenta],
                 brain: Brain, memoria: MemoriaConversa | None = None) -> Agente:
    """A porta da fabrica. Toda criacao de agente passa por aqui."""
    return Agente(nome, persona, ferramentas, brain, memoria)
