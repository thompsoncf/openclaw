"""Cria o arquivo de uma migração nova, com o nome que não colide.

    python -m db.nova_migracao novidade_clinica_funil_tela
    -> db/migracoes/202610031530_novidade_clinica_funil_tela.sql

POR QUE A DATA NO LUGAR DO NÚMERO (03/10/2026). Com várias frentes abertas ao mesmo
tempo, escolher "o próximo número" exigia olhar a main, os PRs e as branches dos
outros, e mesmo assim dois PRs pegavam o mesmo (a 610 das travas e a do funil da
clínica, no mesmo dia). Cada colisão era um renomear e mais uma rodada de CI. O
prefixo de data e hora (UTC, até o minuto) não depende de ninguém: cada sessão gera
o seu, e ele cai sempre depois de todos os números antigos (`aplicar_migracoes.ordem`
ordena pelo número do prefixo).

E se duas sessões gerarem o mesmo minuto? Não quebra nada: o rastreamento é pelo
nome inteiro, e o nome desempata a ordem. Só o mesmo nome no mesmo minuto colidiria.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

PASTA = Path(__file__).parent / "migracoes"

_MODELO = """-- {arquivo}
-- O QUE FAZ: (uma linha)
-- POR QUÊ: (o pedido, a decisão ou o bug, com data)
--
-- Aditiva e idempotente (if not exists / on conflict do nothing).

-- rollback:
--   (como desfazer)
"""


def nome_do_arquivo(nome: str, agora: datetime | None = None) -> str:
    """`AAAAMMDDHHMM_<nome>.sql`, com o nome em minúsculas e sublinhado."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^a-z0-9]+", "_", sem_acento.strip().lower()).strip("_")
    if not limpo:
        raise ValueError("dê um nome à migração, ex.: novidade_funil_da_clinica")
    agora = agora or datetime.now(timezone.utc)
    return f"{agora:%Y%m%d%H%M}_{limpo}.sql"


def criar(nome: str, pasta: Path = PASTA, agora: datetime | None = None) -> Path:
    arquivo = pasta / nome_do_arquivo(nome, agora)
    if arquivo.exists():
        raise FileExistsError(f"{arquivo.name} já existe")
    arquivo.write_text(_MODELO.format(arquivo=arquivo.name), encoding="utf-8")
    return arquivo


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    print(criar(sys.argv[1]).as_posix())


if __name__ == "__main__":
    main()
