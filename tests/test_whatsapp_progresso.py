"""Avisos de progresso no WhatsApp (foto/PDF): ack imediato + "ainda trabalhando".

O Twilio (Programmable Messaging) nao tem "digitando..." como o Telegram. Entao,
no caso lento (foto/PDF = QR + varias idas ao modelo), o Zaq avisa por mensagem
que esta' trabalhando, e PARA assim que a resposta final sai. Como cada aviso
custa uma msg Twilio, ha' cap de 2 avisos.
"""
import time

import web.app as wa


def test_intervalos_parsing(monkeypatch):
    monkeypatch.delenv("WPP_PROGRESSO_SEGUNDOS", raising=False)
    assert wa._intervalos_progresso_wpp() == [15, 20]        # default
    monkeypatch.setenv("WPP_PROGRESSO_SEGUNDOS", "10,20,30")
    assert wa._intervalos_progresso_wpp() == [10, 20]        # cap em 2
    monkeypatch.setenv("WPP_PROGRESSO_SEGUNDOS", "lixo")
    assert wa._intervalos_progresso_wpp() == [15, 20]        # invalido -> default


def test_para_antes_do_1o_aviso_nao_manda_nada(monkeypatch):
    # resposta rapida: parar() antes do 1o intervalo -> ZERO avisos (nao gasta msg)
    enviadas = []
    monkeypatch.setattr(wa, "_responder_whatsapp", lambda to, txt: enviadas.append(txt))
    monkeypatch.setattr(wa, "_intervalos_progresso_wpp", lambda: [5, 5])  # 5s: nao chega
    parar = wa._avisador_progresso_wpp("whatsapp:+5511999999999")
    parar()                     # resposta veio na hora
    time.sleep(0.1)
    assert enviadas == []


def test_avisa_quando_demora(monkeypatch):
    # processamento longo: os avisos saem enquanto nao parar()
    enviadas = []
    monkeypatch.setattr(wa, "_responder_whatsapp", lambda to, txt: enviadas.append(txt))
    monkeypatch.setattr(wa, "_intervalos_progresso_wpp", lambda: [0.05, 0.05])
    parar = wa._avisador_progresso_wpp("whatsapp:+5511999999999")
    time.sleep(0.3)             # tempo pros 2 avisos
    parar()
    assert len(enviadas) >= 1   # pelo menos o 1o aviso saiu (mecanismo funciona)
    assert len(enviadas) <= 2   # nunca passa do cap
