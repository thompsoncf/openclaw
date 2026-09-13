#!/usr/bin/env bash
# Roda a suíte com o relógio ADIANTADO e mostra o que quebra.
#
# POR QUE ISTO EXISTE. Três vezes em cinco dias a main amanheceu vermelha sem
# ninguém ter tocado em código:
#
#   09/09/2026  tests/test_follow_up.py            (#665)
#   13/09/2026  tests/test_painel_servicos_sinal.py (#682)
#   13/09/2026  finance/raio_x.py                   (esta varredura)
#
# Sempre a mesma causa: uma data escrita à mão dentro do teste, comparada com o
# relógio de verdade. Enquanto a data está no futuro a conta fecha sozinha; no dia
# em que o relógio passa por ela, o teste inverte — e quem está de plantão
# encontra um `assert not True` sem nenhuma pista de que o problema é a data.
#
# Ler o código não acha esses casos: os três passaram por revisão. Rodar acha.
#
# COMO USAR
#
#   scripts/varredura_relogio.sh              # 90 dias à frente, só os candidatos
#   scripts/varredura_relogio.sh +365d        # um ano à frente
#   scripts/varredura_relogio.sh +90d todos   # a suíte inteira (lento, ~20min)
#
# O QUE ELE FAZ. Adianta DOIS relógios ao mesmo tempo, e é o ponto: adiantar só o
# Python acusaria falha onde não há, porque metade das comparações acontece dentro
# do Postgres (`now()`, `criado_em default now()`). Os dois têm que andar juntos.
#
# PRÉ-REQUISITOS
#
#   apt-get install -y faketime
#   e um Postgres de teste SUBIDO TAMBÉM sob faketime, com o mesmo deslocamento:
#
#     su postgres -c "faketime '+90 days' pg_ctl -D <PGDATA> -o '-p 5432' -w start"
#
#   Confira antes de confiar no resultado — os dois têm que dizer a mesma data:
#     psql -h localhost -U postgres -tAc 'select now()'
#     LD_PRELOAD=$LIB FAKETIME=+90d python3 -c 'import datetime;print(datetime.datetime.now())'
#
# COMO LER O RESULTADO. Falhou aqui e passa com o relógio normal = bomba-relógio.
# Falhou nos DOIS = defeito comum, que esta varredura não é quem encontra. Sempre
# confira os dois lados antes de mexer: em 13/09 o `test_orcamento_evento.py`
# apareceu na lista e era isolamento entre testes, não relógio.
set -u

DESLOC="${1:-+90d}"
ESCOPO="${2:-candidatos}"
LIB=/usr/lib/x86_64-linux-gnu/faketime/libfaketimeMT.so.1

if [ ! -f "$LIB" ]; then
  echo "faketime não instalado (apt-get install -y faketime)" >&2
  exit 2
fi
# libfaketimeMT, e não a libfaketime comum: com a comum o pytest trava sem sair
# nem entrar — a suíte usa threads, e é exatamente pra isso que a MT existe.

if [ -z "${TEST_DATABASE_URL:-}" ]; then
  echo "TEST_DATABASE_URL não definida (a trava do conftest exige — CLAUDE.md §2)" >&2
  exit 2
fi

banco=$(psql "$TEST_DATABASE_URL" -tAc 'select now()' 2>/dev/null | cut -c1-10)
python=$(LD_PRELOAD=$LIB FAKETIME="$DESLOC" FAKETIME_NO_CACHE=1 \
         python3 -c 'import datetime;print(datetime.date.today())' 2>/dev/null)
echo "relógio do banco:  ${banco:-?}"
echo "relógio do python: ${python:-?}"
if [ "$banco" != "$python" ]; then
  echo
  echo "  ATENÇÃO: os dois relógios discordam. O resultado abaixo vai ter falha"
  echo "  que não é bomba-relógio, é a diferença entre os dois. Suba o Postgres"
  echo "  sob faketime com o mesmo deslocamento antes de confiar na lista."
  echo
fi

if [ "$ESCOPO" = "todos" ]; then
  ARQUIVOS=$(ls tests/test_*.py)
else
  # Só arquivo com data escrita à mão pode ter a bomba — os outros não têm por onde.
  ARQUIVOS=$(grep -rlE "(date|datetime)\(20[0-9]{2}, *[0-9]|'20[0-9]{2}-[0-9]{2}-[0-9]{2}|\"20[0-9]{2}-[0-9]{2}-[0-9]{2}" tests/*.py)
fi
total=$(echo "$ARQUIVOS" | wc -l)
echo "varrendo $total arquivo(s) com o relógio em $DESLOC"
echo

ruins=0
for f in $ARQUIVOS; do
  saida=$(LD_PRELOAD=$LIB FAKETIME="$DESLOC" FAKETIME_NO_CACHE=1 \
          timeout 240 python3 -m pytest -q --tb=no --color=no -p no:cacheprovider "$f" 2>&1)
  rc=$?
  # Um arquivo por processo de propósito: assim uma queda (ou um estouro de
  # memória, que aconteceu com a suíte inteira) não leva junto a varredura toda.
  if [ $rc -ne 0 ]; then
    ruins=$((ruins + 1))
    echo "── $(basename "$f")  $(echo "$saida" | grep -E 'passed|failed|error' | tail -1)"
    echo "$saida" | grep '^FAILED' | sed 's/^/     /'
  fi
done

echo
if [ $ruins -eq 0 ]; then
  echo "nada quebra em $DESLOC."
else
  echo "$ruins arquivo(s) quebram em $DESLOC — confira cada um com o relógio normal"
  echo "antes de mexer: o que falha nos DOIS não é bomba-relógio."
fi
exit 0
