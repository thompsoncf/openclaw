# O Telegram agora e' atendido pelo web (webhook), junto do WhatsApp e do portal.
# Um servico so'; o antigo "worker: python telegram_bot.py" (long polling) foi
# aposentado. Pra rodar o polling em dev local: `python telegram_bot.py`.
web: uvicorn web.app:app --host 0.0.0.0 --port $PORT --workers 2
