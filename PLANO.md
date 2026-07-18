# PLANO.md — o volante do OpenClaw

Este arquivo é o **plano vivo** do sistema. Os agentes (humanos e de IA) leem
daqui pra saber o que fazer e por quê. Ele não descreve como o código está —
descreve **pra onde estamos indo**. Toda proposta de mudança deve caber neste
plano; o que não cabe, ou vira item novo aqui (com justificativa), ou não entra.

Mapa visual desta arquitetura: ver o "Mapa de Agentes · OpenClaw".

---

## 🧭 Métrica-norte: **lucro do cliente**

Toda decisão de todo agente responde a uma pergunta só:

> **Isto aumenta o lucro do cliente?**

Se o cliente lucra mais usando o OpenClaw, ele fica e paga — o lucro dele puxa o
nosso. Classificar lançamento, cobrar inadimplente, cortar custo de API, propor
feature nova: tudo é medido nessa régua. Quando duas mudanças competem, ganha a
que move mais a régua com menos risco.

Sinais que aproximam a régua (proxies enquanto não medimos lucro direto):
economia gerada pro cliente, tempo poupado, inadimplência recuperada, retenção,
custo por conta.

---

## 🎼 Maestro (orquestrador)

Acima das três camadas existe um **Maestro**: decide a prioridade quando as
camadas competem e roda o loop que se retroalimenta —

```
operar  →  gera dados reais (uso, custo, erros, atrasos)
          ↓
melhorar →  lê os dados e decide o que vale mudar (mira a métrica-norte)
          ↓
construir →  implementa em branch → testa → PR
          ↓
        (volta pra operar melhor)
```

O Maestro não age sozinho no que é irreversível: ele **propõe a ordem**, o
humano confirma enquanto a confiança não está madura.

---

## 🛡️ Guard-rails (regra de ouro)

É dinheiro real de gente real. Todo agente que **cobra, paga ou mexe no caixa**
começa assistido e sobe de autonomia só quando ganha confiança:

1. **Sugere e aprova** — o agente propõe, o humano confirma (um toque no WhatsApp).
2. **Limite de valor** — age sozinho até um teto; acima disso, sobe pra aprovação.
3. **Dry-run + trilha** — simula antes de agir e registra tudo (quem, quando, por
   quê) pra auditoria.

Nenhum agente da Camada 2 faz merge sozinho: sempre abre PR pra revisão.

---

## Camada 1 — Agentes que **operam o negócio**

Trabalham pelos clientes, dentro do bot. Fundação = módulos que já existem.

| Agente | O que faz | Fundação já existente | Esforço | Risco | Fase |
|---|---|---|---|---|---|
| 🏷️ Classificador de natureza | Etiqueta "a definir" (pessoal × empresa) pelo histórico; só pergunta no duvidoso | `livro_caixa.marcar_natureza`, `contar_a_definir`, `dre.a_definir` | Médio | Baixo | 1 |
| 📉 Vigia de caixa | Projeta o mês e alerta antes de faltar | `empresa.fluxo_projetado`, `notificar.py` | Baixo | Baixo | 1 |
| 👥 Guardião da folha | Lembra pagamento, vale, custo real com encargos | `folha_eventos`, `pagar_folha`, `FATOR_ENCARGOS` | Médio | Médio | 2 |
| 🔴 Cobrador de inadimplência | Vigia atrasados, dispara Pix + lembrete, dá baixa quando cai | `asaas.py`, `cobranca_link_url`, Twilio/WhatsApp | Alto | **Alto** | 3 |
| 🛒 Radar de preços | Monitora preços, sugere reposição / melhor compra | `banco_precos`, `sefaz_precos`, `datamarket` | Alto | Baixo | 3 |

## Camada 2 — Agentes que **constroem o sistema**

A fábrica que evolui o OpenClaw a partir deste plano.

| Agente | O que faz | Fundação | Esforço | Risco | Fase |
|---|---|---|---|---|---|
| ✅ Guardião do verde | Roda a suíte e abre PR de fix quando quebra | `pytest` + `tests/conftest.py` · **falta CI** | Baixo | Baixo | 1 |
| 🏗️ Builder do backlog | Lê este plano e implementa item → branch → teste → PR | Claude Code + skills | Médio | Médio | 2 |
| 🔎 Revisor + segurança | Revisa todo PR (bugs, regressão, brechas) | skills `review`, `security-review` | Baixo | Baixo | 2 |
| 🚨 Vigia de erros | Lê logs de produção e abre issue/PR com diagnóstico | `observabilidade.py` | Médio | Baixo | 3 |

## Camada 3 — Agentes que **melhoram & inovam**

Mantêm o sistema rápido, barato, atualizado e sempre puxando ideia nova.

| Agente | O que faz | Fundação | Esforço | Risco | Fase |
|---|---|---|---|---|---|
| ⚡ Otimizador de desempenho | Caça query/rota lenta e abre PR de otimização | histórico `perf:`, `observabilidade.py` | Médio | Baixo | 2 |
| 💰 Controlador de custo | Vigia gasto de API e escolhe o modelo certo por tarefa (Haiku no simples, Sonnet/Opus no difícil) | `uso_api`, `OPENCLAW_MODEL` | Médio | Baixo | 2 |
| 🔄 Atualizador | Acompanha versões de deps e de **modelos Claude** novos/mais baratos; testa e propõe a migração | `requirements.txt`, skill `claude-api` | Médio | Médio | 3 |
| 💡 Explorador de inovação (P&D) | Lê o uso real, mede se as mudanças movem a régua (evals) e traz features/experimentos pro plano | `eventos_conta`, `leads` | Alto | Baixo | 3 |

---

## Trilha (fases)

### Fase 1 — Fundação (baixo risco, valor imediato)
- [ ] **Este `PLANO.md`** versionado (o volante) — *em andamento*
- [ ] **CI** no GitHub Actions (hoje não existe) — sobe Postgres 16 de teste e roda a suíte
- [ ] Guardião do verde
- [ ] Classificador de natureza
- [ ] Vigia de caixa (só alerta)

### Fase 2 — A fábrica (autonomia de engenharia, com revisão)
- [ ] Builder do backlog
- [ ] Revisor + segurança em todo PR
- [ ] Controlador de custo
- [ ] Otimizador de desempenho
- [ ] Guardião da folha

### Fase 3 — Autonomia (toca dinheiro/mundo externo, só com guard-rails)
- [ ] Cobrador de inadimplência
- [ ] Radar de preços / reposição
- [ ] Atualizador (deps + modelos Claude)
- [ ] Explorador de inovação (P&D)
- [ ] Vigia de erros de produção

---

## Como os agentes usam este arquivo

1. **Ler a métrica-norte e os guard-rails primeiro.** Nenhuma exceção.
2. **Pegar o próximo item não-marcado da fase atual** (não pular fase sem o Maestro decidir).
3. **Uma mudança = uma branch = um PR** com testes. Nada de merge sem revisão.
4. **Se a ideia não está no plano**, propor como item novo aqui (com o porquê ligado à métrica-norte) antes de implementar.
5. **Marcar `[x]`** o item ao concluir e registrar o PR ao lado.

> Ambiente de teste (para qualquer agente rodar a suíte com segurança): usar um
> Postgres separado em `TEST_DATABASE_URL` — a trava em `tests/conftest.py` impede
> os testes de tocar produção.
