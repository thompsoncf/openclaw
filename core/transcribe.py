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
                    idioma: str = "pt") -> str:
        arquivo = io.BytesIO(audio_bytes)
        arquivo.name = nome  # a API usa a extensao pra saber o formato
        resp = self.client.audio.transcriptions.create(
            model=self.model, file=arquivo, language=idioma,
        )
        return (resp.text or "").strip()


def transcritor_se_configurado() -> "Transcritor | None":
    """Retorna um Transcritor se houver STT_API_KEY; senao None (degrada com elegancia)."""
    if os.environ.get("STT_API_KEY"):
        return Transcritor()
    return None
