"""Transcricao de audio (voz -> texto).

O Claude nao ouve audio, entao usamos o Whisper pra transformar a voz em texto
antes de mandar pro agente. Funciona com qualquer endpoint compativel com a
API da OpenAI (a propria OpenAI ou a Groq, que tem Whisper rapido e barato).

Config por variaveis de ambiente:
    STT_API_KEY   chave do provedor de transcricao
    STT_BASE_URL  (opcional) ex: https://api.groq.com/openai/v1 pra usar Groq
    STT_MODEL     (opcional) "whisper-1" (OpenAI) ou "whisper-large-v3" (Groq)
"""
import io
import os

# Dica de vocabulário (prompt do Whisper): "empurra" a transcrição pros termos do
# nosso contexto — o maior ganho é grafar "Zaq" certo (senão vira "Zac/ZAC") e
# firmar o português. Não resolve nome próprio incomum (STT sempre varia alguns),
# mas trava o que é do domínio. Ajustável por env STT_PROMPT (ex: pra somar nomes
# de contatos frequentes).
_PROMPT_PADRAO = (
    "Assistente financeiro Zaq no WhatsApp e Telegram, em português do Brasil. "
    "Termos comuns: Zaq, Pix, boleto, CNPJ, CPF, nota fiscal, cupom, reunião, "
    "agenda, compromisso, remarcar, confirmar, reais."
)


class Transcritor:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=api_key or os.environ.get("STT_API_KEY"),
            base_url=base_url or os.environ.get("STT_BASE_URL") or None,
        )
        self.model = model or os.environ.get("STT_MODEL", "whisper-1")

    def transcrever(self, audio_bytes: bytes, nome: str = "audio.oga",
                    idioma: str = "pt", prompt: str | None = None,
                    vocab: str | None = None) -> str:
        arquivo = io.BytesIO(audio_bytes)
        arquivo.name = nome  # a API usa a extensao pra saber o formato
        dica = prompt if prompt is not None else os.environ.get("STT_PROMPT", _PROMPT_PADRAO)
        if vocab:  # nomes da agenda da pessoa (títulos/convidados) — ajuda a voz
            dica = (f"{dica} {vocab}".strip() if dica else vocab)[:900]
        kwargs = {"model": self.model, "file": arquivo, "language": idioma,
                  "temperature": 0}
        if dica:
            kwargs["prompt"] = dica
        try:
            resp = self.client.audio.transcriptions.create(**kwargs)
        except TypeError:
            # provedor nao aceita prompt/temperature -> tenta o basico
            arquivo.seek(0)
            resp = self.client.audio.transcriptions.create(
                model=self.model, file=arquivo, language=idioma)
        except Exception as e:  # noqa: BLE001
            # Ponto unico de alerta do STT: se a falha for sistemica (sem
            # credito/chave invalida/quota) o admin e' avisado. Re-levanta pra
            # quem chama tratar (mostrar msg amigavel / degradar).
            from core.falhas import avaliar_falha_provedor
            avaliar_falha_provedor(e, servico="STT (transcricao)")
            raise
        return (resp.text or "").strip()


def transcritor_se_configurado() -> "Transcritor | None":
    """Retorna um Transcritor se houver STT_API_KEY; senao None (degrada com elegancia)."""
    if os.environ.get("STT_API_KEY"):
        return Transcritor()
    return None
