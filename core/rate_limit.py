"""Limitador de tentativas EM MEMÓRIA — trava brute-force de login.

Em memória de propósito: não cria tabela, não escreve no banco, não perde nada se
o processo reiniciar (só zera os contadores, que é o pior caso aceitável — um
atacante ganharia uma janela, não acesso).

O contador é POR INSTÂNCIA. Com N instâncias no Render o atacante enfrenta o
limite em cada uma; não é uma trava distribuída perfeita, mas transforma
"milhões de tentativas por hora" em "poucas dezenas", que é o que importa contra
uma senha provisória curta.

CHAVE = (ip, identificador). Limitar por IP+e-mail em vez de só por e-mail é
deliberado: travar por e-mail sozinho deixaria qualquer um DERRUBAR a conta de
outra pessoa de fora, errando a senha de propósito (lockout vira DoS).

Uso:
    from core.rate_limit import LOGIN

    espera = LOGIN.segundos_bloqueado(chave)
    if espera:
        ... # recusa sem nem checar a senha
    if senha_errada:
        LOGIN.registrar_falha(chave)
    else:
        LOGIN.limpar(chave)
"""
from __future__ import annotations

import threading
import time


class Limitador:
    """Contador de falhas com janela deslizante e bloqueio temporário.

    tentativas: quantas falhas dentro da janela disparam o bloqueio
    janela_s:   as falhas só somam se acontecerem dentro deste intervalo
    bloqueio_s: quanto tempo a chave fica recusada depois de estourar
    teto:       limite de chaves na memória (proteção contra atacante que manda
                um e-mail diferente a cada tentativa só pra inflar o dicionário)
    """

    def __init__(self, tentativas: int = 5, janela_s: int = 300,
                 bloqueio_s: int = 900, teto: int = 20_000):
        self.tentativas = tentativas
        self.janela_s = janela_s
        self.bloqueio_s = bloqueio_s
        self.teto = teto
        # chave -> [n_falhas, ts_primeira_falha, ts_fim_do_bloqueio]
        self._dados: dict[tuple, list] = {}
        # as rotas são `def` (síncronas) e o Starlette as roda em threadpool:
        # mais de uma thread mexe neste dicionário ao mesmo tempo.
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- consulta
    def segundos_bloqueado(self, chave: tuple) -> int:
        """Quantos segundos ainda faltam de bloqueio (0 = liberado)."""
        agora = time.monotonic()
        with self._lock:
            e = self._dados.get(chave)
            if e and e[2] > agora:
                return int(e[2] - agora) + 1
        return 0

    # ------------------------------------------------------------- escrita
    def registrar_falha(self, chave: tuple) -> int:
        """Conta uma falha. Devolve os segundos de bloqueio (0 = ainda liberado)."""
        agora = time.monotonic()
        with self._lock:
            self._podar(agora)
            e = self._dados.get(chave)
            # sem registro, ou a janela expirou -> recomeça a contagem
            if not e or (agora - e[1]) > self.janela_s:
                e = [0, agora, 0.0]
            e[0] += 1
            if e[0] >= self.tentativas:
                e[2] = agora + self.bloqueio_s     # estourou: bloqueia
                e[0] = 0                           # zera pra recontar depois
                e[1] = agora
            self._dados[chave] = e
            return int(e[2] - agora) + 1 if e[2] > agora else 0

    def limpar(self, chave: tuple) -> None:
        """Login deu certo: some com o histórico de falhas dessa chave."""
        with self._lock:
            self._dados.pop(chave, None)

    # ------------------------------------------------------------- interno
    def _podar(self, agora: float) -> None:
        """Só roda quando o dicionário passa do teto (chamado COM o lock preso)."""
        if len(self._dados) < self.teto:
            return
        # 1) fora o que já morreu (sem bloqueio ativo e com a janela vencida)
        for k in [k for k, v in self._dados.items()
                  if v[2] <= agora and (agora - v[1]) > self.janela_s]:
            del self._dados[k]
        # 2) ainda cheio? derruba os mais antigos até caber — nunca deixa crescer
        if len(self._dados) >= self.teto:
            sobra = len(self._dados) - self.teto // 2
            for k, _ in sorted(self._dados.items(), key=lambda kv: kv[1][1])[:sobra]:
                del self._dados[k]


# 5 erros em 5 min -> 15 min recusado. Folgado pra quem erra a senha de verdade,
# apertado pra quem está varrendo as ~170 milhões de senhas provisórias.
LOGIN = Limitador(tentativas=5, janela_s=300, bloqueio_s=900)
