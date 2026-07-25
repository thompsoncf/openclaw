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
                 max_iteracoes: int = 14, livro=None):
        self.nome = nome
        self.persona = persona
        self.ferramentas = {f.nome: f for f in ferramentas}
        self.brain = brain
        self.memoria = memoria or MemoriaConversa()
        self.max_iteracoes = max_iteracoes
        self.livro = livro  # webhook pode acessar pra setar chave_nfce_atual
        self._lock = threading.Lock()   # uma execucao por vez (memoria compartilhada)

    def responder(self, texto: str, imagem_b64: str | None = None,
                  media_type: str = "image/jpeg") -> str:
        # Cadeado: mensagens simultaneas do mesmo usuario rodam UMA por vez.
        # Sem isso, duas threads intercalam blocos na mesma memoria e corrompem
        # a conversa (tool_use sem tool_result -> erro 400 pra sempre).
        with self._lock:
            self._sanear_memoria()
            try:
                resultado = self._responder(texto, imagem_b64, media_type)
            except Exception as e:  # noqa: BLE001
                # Rede-de-seguranca: se a memoria estiver irrecuperavel (ex: erro
                # 400 de tool_use orfao que o saneamento nao pegou), ZERA tudo e
                # tenta uma vez do zero. Melhor perder o historico que travar.
                msg = str(e).lower()
                if "tool_use" in msg or "tool_result" in msg or "400" in msg:
                    self.memoria.limpar()
                    resultado = self._responder(texto, imagem_b64, media_type)
                else:
                    raise
            # OBSERVABILIDADE (Modulo de Comunicacao): loga o turno (best-effort)
            self._registrar_obs(texto, imagem_b64, media_type, resultado)
            return resultado

    def _sanear_memoria(self, max_msgs: int = 16):
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
        self._obs_tools = set(); self._obs_modelo = None; self._obs_custo = 0
        conteudo = []
        if imagem_b64:
            if media_type == "application/pdf":
                # PDF entra como documento (o modelo le' o conteudo do PDF)
                conteudo.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": imagem_b64}})
            else:
                conteudo.append({"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": imagem_b64}})
        conteudo.append({"type": "text", "text": texto})
        self.memoria.adicionar("user", conteudo)

        schemas = [f.schema() for f in self.ferramentas.values()]
        houve_ferramenta = False

        # Escolha de modelo por economia: interacao COM imagem (cupom/PDF) usa o
        # modelo forte (visao precisa de Sonnet); texto puro usa o modelo barato
        # (Haiku da' conta de gasto digitado, saldo, conversa). Texto e' a maioria
        # das mensagens, entao isso corta bastante o custo sem arriscar a leitura
        # de cupom. Strings configuraveis por env (fallback nos padroes).
        import os as _os
        _modelo_forte = _os.environ.get("MODELO_FOTO", "claude-sonnet-4-6")
        _modelo_barato = _os.environ.get("MODELO_TEXTO", "claude-haiku-4-5-20251001")
        _modelo_turno = _modelo_forte if imagem_b64 else _modelo_barato

        for _ in range(self.max_iteracoes):
            resp = self.brain.chamar(self.persona, self.memoria.mensagens(), schemas,
                                     model=_modelo_turno)
            _u = getattr(resp, "usage", None)
            if _u is not None:
                import logging
                _in = getattr(_u, "input_tokens", 0)
                _cr = getattr(_u, "cache_read_input_tokens", 0)
                _cw = getattr(_u, "cache_creation_input_tokens", 0)
                _out = getattr(_u, "output_tokens", 0)
                # origem: de qual conta veio esse gasto (pra rastrear o dreno).
                # o livro carrega conta_id; degustacao = conta tecnica de visitante.
                _conta = getattr(getattr(self, "livro", None), "conta_id", "?")
                # custo estimado em R$ pelo preco do MODELO usado neste turno
                # (Sonnet na foto, Haiku no texto). US$/milhao de tokens:
                #   Sonnet 4.6: in 3 / cache_write 3.75 / cache_read 0.30 / out 15
                #   Haiku 4.5:  in 1 / cache_write 1.25 / cache_read 0.10 / out 5
                if imagem_b64:
                    _p_in, _p_cw, _p_cr, _p_out = 3.0, 3.75, 0.30, 15.0
                else:
                    _p_in, _p_cw, _p_cr, _p_out = 1.0, 1.25, 0.10, 5.0
                _usd = (_in*_p_in + _cw*_p_cw + _cr*_p_cr + _out*_p_out) / 1_000_000
                _brl = _usd * 5.40
                self._obs_modelo = _modelo_turno
                self._obs_custo += int(round(_brl * 100))
                logging.getLogger("openclaw.custo").info(
                    "TOKENS conta=%s in=%s cache_read=%s cache_write=%s out=%s ~R$%.4f",
                    _conta, _in, _cr, _cw, _out, _brl,
                )
                # grava o uso no banco (pro painel de custos do admin). Best-effort:
                # nunca deixa um erro de gravacao quebrar a resposta ao usuario.
                try:
                    _pool = getattr(getattr(self, "livro", None), "pool", None)
                    if _pool is not None and isinstance(_conta, int):
                        _eh_foto = imagem_b64 is not None   # verdade direta: a interacao tem foto/PDF?
                        with _pool.connection() as _conn:
                            _conn.execute(
                                """insert into uso_api
                                   (conta_id, input_tokens, cache_read_tokens,
                                    cache_write_tokens, output_tokens, custo_centavos,
                                    eh_foto)
                                   values (%s,%s,%s,%s,%s,%s,%s)""",
                                (_conta, _in, _cr, _cw, _out,
                                 int(round(_brl * 100)), _eh_foto),
                            )
                            _conn.commit()
                except Exception:
                    pass   # gravacao de metrica nunca derruba a conversa

            # Resposta CORTADA por limite de tokens (ex: cupom gigante): o tool_use
            # veio incompleto. Nao salva (corromperia a memoria) e pede pra dividir.
            if resp.stop_reason == "max_tokens":
                tem_tool = any(getattr(b, "type", None) == "tool_use" for b in resp.content)
                if tem_tool:
                    return ("Esse cupom tem itens demais pra salvar de uma vez! "
                            "Me pede pra salvar em PARTES (ex: os 20 primeiros itens, "
                            "depois os 20 seguintes) - eu junto tudo no mesmo cupom "
                            "sem duplicar.")
                # so' texto cortado: salva o que veio e devolve
                self.memoria.adicionar("assistant", resp.content)
                return self._texto(resp) or "Pode repetir, por favor?"

            self.memoria.adicionar("assistant", resp.content)

            if resp.stop_reason != "tool_use":
                final = self._texto(resp)
                if final:
                    return final
                # terminou sem texto: se fez alguma acao, confirma; senao, pede pra repetir
                return ("Pronto, atualizei aqui! 👍" if houve_ferramenta
                        else "Desculpa, nao entendi. Pode repetir?")

            houve_ferramenta = True
            resultados = []
            for bloco in resp.content:
                if getattr(bloco, "type", None) != "tool_use":
                    continue
                ferr = self.ferramentas.get(bloco.name)
                self._obs_tools.add(bloco.name)
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

    def _registrar_obs(self, texto, imagem_b64, media_type, resultado):
        # Grava o turno em conversas_log (best-effort; nunca quebra a resposta).
        try:
            livro = getattr(self, "livro", None)
            pool = getattr(livro, "pool", None)
            conta_id = getattr(livro, "conta_id", None)
            if pool is None or not isinstance(conta_id, int):
                return
            if imagem_b64:
                tipo = "pdf" if media_type == "application/pdf" else "foto"
            else:
                tipo = "texto"
            falhas = ("me embananei", "nao entendi", "Pode repetir")
            sucesso = not any(f in (resultado or "") for f in falhas)
            from finance import observabilidade as _obs
            _obs.registrar_interacao(
                pool,
                conta_id=conta_id,
                membro_id=getattr(livro, "membro_id", None),
                canal=getattr(self, "canal_atual", None),
                tipo_midia=tipo,
                texto_usuario=texto,
                resposta=resultado,
                tools_usadas=",".join(sorted(getattr(self, "_obs_tools", set()))) or None,
                modelo=getattr(self, "_obs_modelo", None),
                sucesso=sucesso,
                repetiu=self._humano_repetiu(texto),
                custo_centavos=getattr(self, "_obs_custo", 0),
            )
        except Exception:  # noqa: BLE001
            pass

    def _humano_repetiu(self, texto_atual) -> bool:
        # True se a msg humana anterior na memoria for ~igual a atual (atrito).
        try:
            atual = " ".join((texto_atual or "").lower().split())
            if not atual:
                return False
            humanos = []
            for m in self.memoria.mensagens():
                if m.get("role") != "user":
                    continue
                c = m.get("content")
                if not isinstance(c, list):
                    continue
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    continue
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = " ".join((b.get("text") or "").lower().split())
                        if t:
                            humanos.append(t)
            return len(humanos) >= 2 and humanos[-1] == atual and humanos[-2] == atual
        except Exception:  # noqa: BLE001
            return False


def criar_agente(nome: str, persona: str, ferramentas: list[Ferramenta],
                 brain: Brain, memoria: MemoriaConversa | None = None, livro=None) -> Agente:
    """A porta da fabrica. Toda criacao de agente passa por aqui."""
    return Agente(nome, persona, ferramentas, brain, memoria, livro=livro)
