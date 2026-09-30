"""Clientes/Fornecedores liberada pro vendedor (pedido do dono, 29/09/2026).

Hoje só o dono acessava — nem gestor, nem vendedor (o link some do menu E a
rota tem trava dura de papel em web/app.py:_gate_permissoes). Regra nova,
combinada com o dono:

- vendedor cadastra, edita e consulta, em QUALQUER nicho — MENOS Clínica, onde
  esta mesma aba é só Fornecedores (paciente de verdade mora em Pacientes) e
  fornecedor é decisão de compra, não de atendimento: ali continua só
  dono/gestor;
- "Arquivar" e "dar baixa" no fiado (lança no caixa) continuam só dono/gestor
  em QUALQUER nicho — a régua de sempre deste app pra ação financeira.

`_guard_clientes` (web/portal.py) é o ponto único: sem banco, `emp.acesso_pj`
e `rxp.perfil_por_nicho` são monkeypatchados.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from web import portal as pt

#: (id, tipo, nome, doc, plano, status, venc, limite, ..., tem_pj[11], ...,
#:  vende_produto[13], vende_servico[14], tem_cesta[15], nicho_slug[16])
_CONTA = (1, "pj", "Buffet", None, "app_pro", "ativa", None, 50, None, None,
          None, True, None, False, True, False, "eventos")


def _conta_clinica():
    return _CONTA[:16] + ("clinica",)


class _Sessao(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def _req(papel: str):
    return SimpleNamespace(session=_Sessao(papel=papel))


@pytest.fixture(autouse=True)
def _sem_banco(monkeypatch):
    monkeypatch.setattr(pt, "get_pool", lambda: object())
    from finance import empresa as emp
    monkeypatch.setattr(emp, "acesso_pj", lambda pool, conta_id: True)


def _nicho(monkeypatch, chave: str):
    from finance import raio_x_perfil as rxp
    monkeypatch.setattr(rxp, "perfil_por_nicho", lambda slug: chave)


def test_vendedor_entra_fora_da_clinica(monkeypatch):
    _nicho(monkeypatch, "eventos")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _CONTA)
    assert pt._guard_clientes(_req("vendedor")) is not None


def test_vendedor_nao_entra_na_clinica(monkeypatch):
    _nicho(monkeypatch, "clinica")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _conta_clinica())
    assert pt._guard_clientes(_req("vendedor")) is None


def test_gestor_entra_ate_na_clinica(monkeypatch):
    _nicho(monkeypatch, "clinica")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _conta_clinica())
    assert pt._guard_clientes(_req("gestor")) is not None


def test_dono_entra_ate_na_clinica(monkeypatch):
    _nicho(monkeypatch, "clinica")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _conta_clinica())
    assert pt._guard_clientes(_req("dono")) is not None


def test_financeiro_nao_entra_em_nicho_nenhum(monkeypatch):
    """`financeiro` não tem `caps.vendas` — a régua de sempre (não é gente de vendas)."""
    _nicho(monkeypatch, "eventos")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _CONTA)
    assert pt._guard_clientes(_req("financeiro")) is None


def test_arquivar_e_so_dono_gestor_mesmo_fora_da_clinica(monkeypatch):
    _nicho(monkeypatch, "eventos")
    monkeypatch.setattr(pt, "conta_logada", lambda req: _CONTA)
    assert pt._guard_clientes(_req("vendedor"), so_dono_gestor=True) is None
    assert pt._guard_clientes(_req("gestor"), so_dono_gestor=True) is not None
    assert pt._guard_clientes(_req("dono"), so_dono_gestor=True) is not None


def test_sem_sessao_nao_entra(monkeypatch):
    monkeypatch.setattr(pt, "conta_logada", lambda req: None)
    assert pt._guard_clientes(_req("vendedor")) is None


# ────────────────────────────────── a rota /painel/clientes está na whitelist

def test_painel_clientes_na_whitelist_do_vendedor():
    from contas import equipe as eq
    assert any(r == "/painel/clientes" or "/painel/clientes".startswith(r + "/")
               for r in eq.rotas_do_papel("vendedor"))


def test_painel_clientes_fora_da_whitelist_do_financeiro():
    from contas import equipe as eq
    rotas = eq.rotas_do_papel("financeiro")
    assert not any(r == "/painel/clientes" for r in rotas)


# ──────────────────────────────────────── o botão Arquivar não aparece na tela

def test_o_arquivar_fica_escondido_de_quem_nao_e_dono_nem_gestor():
    for nome, template_src in (("clientes", pt._CLIENTES),
                               ("cliente_detalhe", pt._CLIENTE_DETALHE)):
        assert "/arquivar" in template_src, f"{nome}: form de arquivar sumiu — teste desatualizado"
        assert "{% if papel in ('dono','gestor') %}" in template_src, (
            f"{nome}: o botão/form de arquivar não está guardado por papel")


def test_o_dar_baixa_no_fiado_fica_escondido_de_quem_nao_e_dono_nem_gestor():
    alvo = '/painel/clientes/{{ cliente.id }}/fiado/{{ f.id }}/baixar'
    assert alvo in pt._CLIENTE_DETALHE
    antes = pt._CLIENTE_DETALHE.split(alvo)[0][-120:]
    assert "papel in ('dono','gestor')" in antes
