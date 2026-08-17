# Revisão independente — `light_scheduler`

## Escopo

Foram revisados do zero todos os arquivos solicitados: `const.py`, `__init__.py`,
`scheduler.py`, `sensor.py`, `switch.py`, `binary_sensor.py`, `next_run.py`,
`store.py`, `schedules.py`, `config_flow.py`, `power.py`,
`frontend/light-schedule-card.js`, `manifest.json`, `services.yaml`,
`strings.json`, `translations/en.json` e `translations/pt-BR.json`.

## Crítico

### C1 — chamadas de serviço podem ficar sem limite e travar parada/unload

- **Arquivo:** `custom_components/light_scheduler/scheduler.py:350-359`,
  `508-530`
- **Problema:** `_dispatch()` usa `async_call(..., blocking=True)` sem timeout.
  A sequência de desligamento aguarda cada entidade sequencialmente e depois
  aguarda confirmação. Se uma integração de dispositivo nunca completar a
  chamada, `stop`, o timer de fim e `async_unload()` ficam aguardando
  indefinidamente. O `try/except` não ajuda nesse caso, pois a exceção só
  ocorre se a chamada retornar.
- **Correção concreta:** envolver cada chamada em timeout (por exemplo,
  `asyncio.timeout`/helper compatível com a versão mínima do HA), registrar a
  entidade que expirou e continuar a sequência; aplicar também um limite ao
  caminho de parada para que unload nunca dependa de um dispositivo travado.

## Médio

### M1 — IDs de agendamento não são únicos e podem causar atualização/remoção ambígua

- **Arquivo:** `custom_components/light_scheduler/__init__.py:156-163`,
  `339-371`, `373-383`, `408-429`
- **Problema:** `add_schedule` e `set_schedules` aceitam IDs repetidos, pois
  `_validated_schedule()` apenas gera um ID quando ele está ausente. Depois,
  `remove_schedule` remove **todos** os itens com o ID (`365-369`) e
  `update_schedule` altera todos os itens correspondentes (`411-417`). O
  scheduler, por sua vez, trata a lista como se cada ID identificasse uma
  única linha.
- **Correção concreta:** validar unicidade no conjunto completo antes de
  persistir (e rejeitar duplicidade entre IDs novos e existentes); exigir que
  remove/update encontrem exatamente uma ocorrência.

### M2 — mutações concorrentes de opções perdem alterações

- **Arquivo:** `custom_components/light_scheduler/__init__.py:309-313`,
  `339-349`, `373-383`, `408-510`
- **Problema:** cada handler lê `scheduler.options`, cria um novo dicionário e
  chama `async_update_entry`. Duas chamadas simultâneas (por exemplo, dois
  `add_schedule`) podem ler a mesma lista; a segunda grava sua versão e apaga
  a primeira. O mesmo vale para `set_schedules`/`set_zone_options`.
- **Correção concreta:** serializar mutações por entry com `asyncio.Lock` e
  reler as opções dentro do lock antes de calcular a nova versão; ou usar uma
  operação de atualização atômica que reavalie o estado atual.

### M3 — horários ambíguos no fim do DST são aceitos sem política explícita

- **Arquivo:** `custom_components/light_scheduler/next_run.py:14-17`,
  `23-33`
- **Problema:** para uma zona com `ZoneInfo` e horário repetido (por exemplo,
  `01:30` no retorno do DST em `America/New_York`), `datetime.combine()` usa o
  fold padrão (`fold=0`). `_exists()` só aceita/rejeita a existência e não
  sinaliza que há duas ocorrências. Assim o usuário não pode escolher a
  segunda ocorrência e pode receber uma execução uma hora antes do esperado.
- **Correção concreta:** detectar `fold=0` e `fold=1` com instantes distintos e
  rejeitar o horário ambíguo na validação com erro orientando o usuário, ou
  persistir uma política explícita (`first`/`second`) e testá-la/documentá-la.

### M4 — seleção de luzes de um agendamento não é validada contra a zona

- **Arquivo:** `custom_components/light_scheduler/__init__.py:84-95`,
  `__init__.py:156-163`; `scheduler.py:426-430`
- **Problema:** o schema valida apenas o domínio (`light`/`switch`), não que a
  entidade pertence à zona. Uma chamada de serviço pode persistir uma entidade
  de outra zona (ou inexistente); na execução ela é silenciosamente ignorada
  porque o loop só percorre `self.target_entity_ids`. Isso mascara erro de
  configuração e faz o agendamento não atuar nas entidades que o usuário
  selecionou.
- **Correção concreta:** depois de resolver a zona, validar cada seleção contra
  `scheduler.target_entity_ids` e rejeitar IDs externos/desconhecidos com
  `ServiceValidationError`; repetir a validação ao carregar opções legadas.

### M5 — pareamento informado por serviço pode ser inexistente e vira confirmação permissiva

- **Arquivo:** `custom_components/light_scheduler/__init__.py:124-142`,
  `scheduler.py:332-348`
- **Problema:** `_normalize_mappings()` valida prefixo e, se o sensor não tem
  estado, aceita qualquer `sensor.*`. Mais tarde `read_power_watts()` retorna
  `None` e `_is_confirmed()` trata isso como confirmação baseada apenas no
  estado HA (`345-348`). Um typo ou sensor removido, portanto, desativa
  silenciosamente a confirmação de potência justamente no caminho que deveria
  detectar falha física.
- **Correção concreta:** distinguir sensor ausente/não utilizável de leitura
  temporariamente indisponível; rejeitar IDs inexistentes quando o serviço é
  chamado e expor o pareamento como warning/não confirmado até existir uma
  leitura válida (mantendo fallback apenas para indisponibilidade transitória
  explicitamente definida).

## Menor

### N1 — edição no card pode produzir horário com segundos incompatível com o input

- **Arquivo:** `custom_components/light_scheduler/frontend/light-schedule-card.js:913-918`,
  `518-522`, `269`
- **Problema:** `_scheduleEnd()` pode devolver `HH:MM:SS` para durações que não
  são múltiplas de minuto. Esse valor é atribuído a `<input type="time">` sem
  `step`; em browsers que aceitam somente minuto nesse controle, o valor pode
  ser descartado/considerado inválido e a edição subsequente não representa o
  agendamento original.
- **Correção concreta:** definir `step="1"` e tratar explicitamente segundos no
  formulário, ou arredondar/documentar a conversão e preservar a duração
  original separadamente.

## Falsos positivos considerados

- O options flow **não** demonstrou perda direta de dados: ele monta `options`
  a partir das opções existentes e retorna `async_create_entry(data=options)`;
  também atualiza o nome em `entry.data` (`config_flow.py:216-237`).
- A confirmação de grupo não espera um grace period por entidade: os testes e
  `scheduler.py:361-403` confirmam uma janela compartilhada e uma única
  repetição.
- A sequência de ramp/stop cancela o task de ramp (`scheduler.py:512-516`) e
  evita iniciar outro run enquanto `_active`; não foi classificada como corrida
  concreta sem um dispositivo que bloqueie `_dispatch` (coberta em C1).
- Listeners de estado/dispatcher são removidos por `async_on_remove` ou no
  unload; não encontrei vazamento inequívoco nos caminhos normais.
- O card usa escape HTML para valores interpolados e a checagem sintática JS
  passou; não encontrei injeção concreta no código revisado.

## Sugestões de cobertura

- Testar IDs duplicados em add/set/update/remove e chamadas simultâneas.
- Testar seleção externa à zona e sensor de potência inexistente.
- Testar horário ambíguo no retorno de DST, além do horário inexistente.
- Testar `async_call` que nunca retorna durante timer de fim e unload.
- Adicionar harness de browser para edição de duração com segundos e para
  autocomplete/card em atualização de estado.

## Comandos executados

- `python -m unittest discover -s tests -v` — **OK**, 11 testes; 1 pulado
  (`test_ignores_nonexistent_dst_time`, banco de timezones não instalado).
- `node --check custom_components/light_scheduler/frontend/light-schedule-card.js` — **OK**.
- `python -m compileall -q custom_components/light_scheduler` — **OK**.

## Status

**PRECISA DE ALTERAÇÃO**
