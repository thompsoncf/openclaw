"""A Fabrica de agentes.

Um Agente e' so' a combinacao de: persona (quem ele e'), ferramentas (o que
sabe fazer), memoria (o que lembra) e cerebro (o Claude). Trocando esse
recheio, a mesma fabrica produz qualquer agente: financeiro, agenda, etc.
"""
import threading
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
        self._lock = threading.Lock()   # uma execucao por vez (memoria compartilhada)

    def responder(self, texto: str, imagem_b64: str | None = None,
                  media_type: str = "image/jpeg") -> str:
        # Cadeado: mensagens simultaneas do mesmo usuario rodam UMA por vez.
        # Sem isso, duas threads intercalam blocos na mesma memoria e corrompem
        # a conversa (tool_use sem tool_result -> erro 400 pra sempre).
        with self._lock:
            self._sanear_memoria()
            try:
                return self._responder(texto, imagem_b64, media_type)
            except Exception as e:  # noqa: BLE001
                # Rede-de-seguranca: se a memoria estiver irrecuperavel (ex: erro
                # 400 de tool_use orfao que o saneamento nao pegou), ZERA tudo e
                # tenta uma vez do zero. Melhor perder o historico que travar.
                msg = str(e).lower()
                if "tool_use" in msg or "tool_result" in msg or "400" in msg:
                    self.memoria.limpar()
                    return self._responder(texto, imagem_b64, media_type)
                raise

    def _sanear_memoria(self, max_msgs: int = 40):
        """Conserta corrupcoes e poda o historico.

        Regras do Claude: todo assistant com tool_use precisa de um user com
        tool_result LOGO em seguida. Mensagens que violam isso sao removidas.
        Tambem limita o tamanho (conversas infinitas custam caro).
        """
        def tem(blocos, tipo):
            try:
                return any((getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)) == tipo
                           for b in blocos)
            except TypeError:
                return False

        msgs = self.memoria.mensagens()
        limpas = []
        i = 0
        while i < len(msgs):
            m = msgs[i]
            cont = m.get("content")
            blocos = cont if isinstance(cont, list) else []
            if m.get("role") == "assistant" and tem(blocos, "tool_use"):
                prox = msgs[i + 1] if i + 1 < len(msgs) else None
                prox_blocos = (prox or {}).get("content")
                prox_blocos = prox_blocos if isinstance(prox_blocos, list) else []
                if prox and prox.get("role") == "user" and tem(prox_blocos, "tool_result"):
                    limpas.extend([m, prox]); i += 2; continue
                i += 1; continue                      # tool_use orfao: descarta
            if m.get("role") == "user" and tem(blocos, "tool_result"):
                i += 1; continue                      # tool_result orfao: descarta
            limpas.append(m); i += 1
        # poda mantendo o fim (e nunca comecando com tool_result)
        if len(limpas) > max_msgs:
            limpas = limpas[-max_msgs:]
            while limpas and isinstance(limpas[0].get("content"), list) and tem(limpas[0]["content"], "tool_result"):
                limpas.pop(0)
        msgs[:] = limpas

    def _responder(self, texto: str, imagem_b64: str | None = None,
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
