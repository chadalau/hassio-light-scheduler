# Testes de integração — Home Assistant real

Esta pasta roda o `light_scheduler` dentro de um Home Assistant de verdade: o
loader dele, as config entries, o registro de serviços, as plataformas de
entidade, o dispatcher e o `Store`. É o complemento da suíte de `tests/`, que
usa stubs e é rápida mas só consegue afirmar que **chamamos** as APIs certas —
não que o Home Assistant faz o que esperamos com elas.

## Rodando

```bash
./tests_integration/run.sh
```

No Windows:

```powershell
.\tests_integration\run.ps1
```

Qualquer argumento vai direto para o pytest:

```bash
./tests_integration/run.sh -k ownership -v
```

O primeiro comando constrói a imagem; depois disso ela é reaproveitada. O
repositório é montado **somente leitura**, então nenhum teste consegue escrever
na árvore de trabalho.

## Por que uma imagem, e não um venv

A imagem é construída a partir de `ghcr.io/home-assistant/home-assistant:stable`,
então a integração é exercitada contra exatamente o Home Assistant e o Python que
o add-on roda na prática — hoje **2026.8.2 sobre Python 3.14.6**.

`pytest-homeassistant-custom-component` fixa uma versão do Home Assistant em cada
release. O pin do `Dockerfile` (`0.13.356`) resolve para `homeassistant==2026.8.2`,
que é o que a imagem já traz, então instalá-lo **não troca a versão por baixo dos
testes**. Ao subir a imagem base, confira o par:

```bash
docker run --rm ghcr.io/home-assistant/home-assistant:stable \
  pip index versions pytest-homeassistant-custom-component
```

Se as versões divergirem, o pip reinstala o Home Assistant em silêncio e a suíte
deixa de testar o que é distribuído.

## Por que os stubs não são usados aqui

`tests/ha_stubs.py` injeta módulos falsos em `sys.modules` e desiste se o
`homeassistant` real já tiver sido importado. Misturar as duas suítes na mesma
sessão de pytest faz uma sabotar a outra — daí duas pastas e dois comandos. A
suíte rápida continua sendo a de sempre:

```bash
python -m unittest discover -s tests
```

## O que só é possível verificar aqui

- as três entidades da zona nascem e morrem com a config entry;
- os sete serviços são registrados no setup e removidos quando a última zona sai;
- `turn_on_now` e `stop` realmente acionam as luzes pelo registro de serviços;
- a execução ativa é persistida no `Store` do Home Assistant e **recuperada após
  reinício**, com o horário de desligar preservado — e encerrada se ele passou
  enquanto o Home Assistant estava fora do ar;
- `_unrecorded_attributes` mantém os atributos grandes fora do recorder,
  conferido nos objetos de entidade que o Home Assistant instanciou;
- a regra de "uma luz, uma zona" é imposta pelo serviço que o card chama;
- a poda de agendamentos presos a uma luz removida acontece de ponta a ponta.

Estas asserções foram validadas por mutação: removendo `_unrecorded_attributes` e
a checagem de sobreposição, exatamente os dois testes correspondentes falham.
