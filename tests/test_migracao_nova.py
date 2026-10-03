"""O nome da migração nova (db/nova_migracao.py) e a ordem da fila (aplicar_migracoes.ordem)."""
from __future__ import annotations

import glob
import os
from datetime import datetime, timezone

import pytest

from db import nova_migracao as nm
from db.aplicar_migracoes import ordem

AGORA = datetime(2026, 10, 3, 15, 30, tzinfo=timezone.utc)


def _existentes() -> list[str]:
    return [os.path.basename(f) for f in glob.glob("db/migracoes/*.sql")]


def test_os_tres_digitos_ficam_na_mesma_ordem_de_antes():
    # a ordem antiga era a alfabética; para os prefixos de três dígitos, que são
    # todas as migrações até 03/10/2026, as duas têm que ser idênticas
    antigas = [n for n in _existentes() if len(n.split("_", 1)[0]) == 3]
    assert len(antigas) > 500
    assert sorted(antigas, key=ordem) == sorted(antigas)


def test_a_migracao_com_data_vai_para_o_fim_e_1000_nao_cai_no_meio():
    nomes = ["100_a.sql", "101_b.sql", "640_c.sql", "1000_d.sql", "202610031530_e.sql", "202610031529_f.sql"]
    assert sorted(nomes, key=ordem) == ["100_a.sql", "101_b.sql", "640_c.sql", "1000_d.sql",
                                        "202610031529_f.sql", "202610031530_e.sql"]


def test_prefixo_repetido_desempata_pelo_nome_como_antes():
    assert sorted(["610_travas.sql", "610_novidade.sql"], key=ordem) == ["610_novidade.sql", "610_travas.sql"]


def test_toda_migracao_tem_prefixo_numerico():
    # sem número a migração iria pro fim da fila sem ninguém perceber
    assert [n for n in _existentes() if not n.split("_", 1)[0].isdigit()] == []


def test_o_nome_novo_tem_data_e_hora_e_nome_limpo():
    assert nm.nome_do_arquivo("Novidade: funil da clínica!", AGORA) == "202610031530_novidade_funil_da_clinica.sql"
    assert nm.nome_do_arquivo("novidade_funil_tela", AGORA) == "202610031530_novidade_funil_tela.sql"
    with pytest.raises(ValueError):
        nm.nome_do_arquivo("  !! ", AGORA)


def test_criar_escreve_o_modelo_e_nao_sobrescreve(tmp_path):
    arq = nm.criar("novidade_x", tmp_path, AGORA)
    assert arq.name == "202610031530_novidade_x.sql"
    assert arq.read_text(encoding="utf-8").startswith("-- 202610031530_novidade_x.sql")
    with pytest.raises(FileExistsError):
        nm.criar("novidade_x", tmp_path, AGORA)
