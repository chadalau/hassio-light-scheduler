# Revisão independente — `light_scheduler`

**Status final: PRECISA DE ALTERAÇÕES antes de considerar a integração segura.**

## Escopo

Foram lidos integralmente todos os arquivos solicitados em `custom_components/light_scheduler/`: os dez módulos Python, o card JS, `manifest.json`, `services.yaml`, `strings.json` e as duas traduções. Nenhum arquivo de produção foi alterado.

## Crítico

### C1 — O contrato do card não representa um horário local de desligamento

**Arquivos:** `frontend/light-schedule-card.js:472-477`, `__init__.py:66-84`, `next_run.py:19-33`

O card aceita dois horários, mas descarta o horário de desligar e envia somente uma duração:

```js
time: form.elements.start.value,
duration: this._durationBetween(form.elements.start.value, form.elements.end.value),
```

O backend agenda apenas `time + duration` e `find_next_run` compara instantes. Em uma mudança de DST, a intenção “ligar às 01:30 e desligar às 03:30” não equivale necessariamente a 2 horas reais; além disso, serviços e outros clientes não conseguem expressar o horário local de desligamento. O card mostra um horário derivado, não o valor persistido.

**Correção concreta:** persistir `on_time` e `off_time` (ou um modelo equivalente explícito), validar ambos no backend e calcular a próxima ocorrência e o término com o fuso configurado do HA, definindo também a política para horário inexistente/ambíguo no DST. Atualizar schemas, serviços e card conjuntamente.

### C2 — Há uma corrida de unload que permite atuação depois do descarregamento

**Arquivo:** `scheduler.py:145-161, 445-467`

`async_unload()` chama `async_stop()` somente quando `_active` e, se outra parada já estiver em andamento, `async_stop()` retorna imediatamente por causa de:

```python
if not self._active or self._stopping:
    return
```

O unload então cancela listeners e retorna sem aguardar a parada concorrente. A tarefa que ainda está fazendo `turn_off` pode continuar usando `hass.services` após a entrada/plataformas terem sido descarregadas. O mesmo padrão deixa uma execução parcial sem garantia de estado físico seguro.

**Correção concreta:** manter uma future/task única para a parada e fazer todo caller, inclusive unload, aguardá-la; tornar stop idempotente sem retornar antes da operação em andamento terminar. Cancelar e aguardar explicitamente ramp/start/stop antes de remover listeners.

## Médio

### M1 — `async_stop` pode ser perdido por chamadas concorrentes

**Arquivo:** `scheduler.py:449-456`

Quando `stop` manual, timer de término e unload chegam próximos, o segundo caller simplesmente retorna porque `_stopping` já é verdadeiro. O serviço pode responder como se tivesse parado, enquanto a sequência de desligamento ainda executa e pode falhar depois; isso também alimenta C2.

**Correção concreta:** guardar `_stop_task`/future e retornar `await` dessa mesma operação; somente a primeira chamada deve executar a sequência.

### M2 — Uma entidade fica registrada como desligada ao se tornar `unavailable`/`unknown`

**Arquivo:** `scheduler.py:208-220`

Qualquer mudança que não seja `on` é convertida em desligamento externo:

```python
elif new_state:
    self._create_background_task(
        self._record_external(event.data["entity_id"], False)
    )
```

Assim, a transição `on -> unavailable` fecha o histórico externo e grava uma duração que não representa um `off` confirmado. Depois, a entidade pode voltar `on` e abrir um novo período, duplicando/fragmentando a telemetria.

**Correção concreta:** tratar somente `new_state.state == STATE_OFF` como desligamento; ignorar `unknown` e `unavailable` (e, idealmente, registrar indisponibilidade separadamente).

### M3 — Pareamento de potência não é validado de forma consistente

**Arquivos:** `config_flow.py:107-112`, `__init__.py:105-129`, `power.py:18-28`

O options flow aceita qualquer entidade do domínio `sensor`, sem verificar dispositivo/classe/unidade. Já o serviço valida apenas se o estado existe; para sensor inexistente a validação passa. Mais tarde `read_power_watts()` interpreta qualquer valor numérico como watts porque não verifica se a entidade é realmente um sensor de potência. Isso pode confirmar uma lâmpada usando temperatura, tensão ou outro sensor numérico, ou simplesmente desativar a confirmação silenciosamente para um ID inexistente.

**Correção concreta:** validar IDs existentes no registry/estado e `device_class=power` ou unidade suportada no config flow, options flow e serviço; fazer `read_power_watts()` rejeitar entidades que não tenham metadados de potência.

### M4 — Campos de entidade do serviço podem apontar para entidades inexistentes

**Arquivo:** `__init__.py:153-171, 437-487`

`_normalize_entity_ids()` verifica somente o prefixo (`light.`, `switch.` ou `sensor.`), não existência no state machine/registry. Portanto `set_zone_options` pode persistir IDs digitados ou removidos. O scheduler depois os ignora na lista disponível, enquanto o card continua exibindo uma configuração aparentemente válida.

**Correção concreta:** validar cada ID no entity registry e rejeitar IDs ausentes (ou retornar erro explícito para entidades removidas); aplicar a mesma regra a mappings e aos campos legados.

### M5 — `set_zone_options` aceita combinações ambíguas e ignora silenciosamente parte da entrada

**Arquivo:** `__init__.py:434-487`

Quando `entity_mappings`, `target_entity_ids` e/ou `power_entity_ids` são enviados juntos, o ramo `if CONF_ENTITY_MAPPINGS in data` vence e os outros dois são ignorados:

```python
if CONF_ENTITY_MAPPINGS in data:
    ...
elif CONF_TARGET_ENTITY_IDS in data or CONF_POWER_ENTITY_IDS in data:
    ...
```

Uma automação pode acreditar que alterou os três campos, mas somente o mapping será salvo.

**Correção concreta:** rejeitar combinações mutuamente exclusivas com `ServiceValidationError`, ou definir e documentar uma fusão determinística; nunca descartar campos enviados sem erro.

## Menor

### m1 — Stub do card aponta para entidade inexistente

**Arquivo:** `frontend/light-schedule-card.js:18-20`

`getStubConfig()` retorna `sensor.light_scheduler`, mas as entidades reais têm IDs derivados do `entry_id` e o próprio card exige atributos específicos. O preview/configurador pode iniciar com “entidade não encontrada”.

**Correção concreta:** não fornecer um stub inválido ou gerar um stub baseado em uma entidade real selecionável.

### m2 — O total de potência mostra `0,0 W` quando não há medição

**Arquivos:** `sensor.py:140-181`, `frontend/light-schedule-card.js:122-125,867-869`

Sem sensores ou com todos indisponíveis, o backend deixa `total_power_w` em `0.0` e o card sempre formata o número como potência válida. Isso é enganoso, pois zero medido e “sem medição” são estados diferentes.

**Correção concreta:** publicar `None` quando nenhum watt foi lido e renderizar “—”/“indisponível” no card.

### m3 — O cache de pareamento não é invalidado quando metadados mudam

**Arquivo:** `sensor.py:198-227`

O cache só é limpo em dispatcher update ou quando `new_state is None`. Alteração de `device_class`/unidade sem remoção da entidade mantém o mapeamento antigo até a próxima atualização de opções.

**Correção concreta:** invalidar o cache em qualquer mudança relevante de atributos do sensor, ou não cachear a descoberta de forma indefinida.

## Pontos verificados sem achado

- O options flow preserva `self.config_entry.options` e, portanto, mantém schedules, enabled e max duration; não reproduzi a perda de dados alegada em versões anteriores.
- Listeners das entidades usam `async_on_remove`; o listener de estados do scheduler é recriado no update e removido no unload.
- `turn_on/turn_off` usam `blocking=True` e há confirmação por estado, retry e confirmação opcional por potência; o problema encontrado é a validação/concorrência acima, não a ausência total de confirmação.
- O tratamento de horário usa timestamps conscientes e rejeita horários locais inexistentes; ainda assim não resolve a perda do horário de desligamento separado descrita em C1.
# Revisão adversarial — `light_scheduler`

## Escopo

Revisados do zero todos os arquivos solicitados em `custom_components/light_scheduler/`:

`const.py`, `__init__.py`, `scheduler.py`, `sensor.py`, `switch.py`,
`binary_sensor.py`, `next_run.py`, `store.py`, `schedules.py`, `config_flow.py`,
`power.py`, `frontend/light-schedule-card.js`, `manifest.json`, `services.yaml`,
`strings.json`, `translations/en.json` e `translations/pt-BR.json`.

## Problemas

### Crítico

1. **O desligamento pode ficar muito além do horário de término.**
   - **Arquivo:** `scheduler.py:345-355, 357-384, 463-467`
   - **Problema/evidência:** `_actuate()` espera até `ACTUATION_GRACE` (15 s), repete a atuação e espera mais 15 s. `async_stop()` faz isso sequencialmente para cada alvo e ainda soma `interval`. Portanto, com três entidades que não confirmam `off`, o callback de término pode manter o run em `stopping` por até aproximadamente 90 s (mais intervalos), enquanto uma ou mais cargas continuam ligadas. O tempo de execução configurado não é mais um limite real de segurança.
   - **Correção concreta:** separar “iniciar o desligamento no prazo” de “aguardar confirmação”: despachar os `turn_off` dentro de um deadline absoluto (idealmente em paralelo ou com timeout total do grupo), finalizar o estado do run após esse deadline e registrar confirmações pendentes. Nunca permitir que o timeout de cada entidade seja multiplicado pela quantidade de entidades.

### Médio

2. **`set_zone_options` aceita IDs inexistentes e sensores não verificáveis.**
   - **Arquivo:** `__init__.py:96-140, 153-171, 434-496`
   - **Problema/evidência:** `_normalize_entity_ids()` valida apenas o prefixo (`light.`, `switch.` ou `sensor.`), e `_normalize_mappings()` só rejeita um sensor de potência quando ele já existe e tem atributos de potência. Um typo como `light.nao_existe` ou `sensor.medidor_errado` é persistido com sucesso; depois `async_turn_on()` simplesmente o exclui em `scheduler.py:403-409`, podendo resultar em “nenhuma luz disponível” sem erro no serviço.
   - **Correção concreta:** consultar `hass.states`/entity registry e exigir que cada entidade exista, esteja no domínio correto e, para potência, tenha `device_class=power` ou unidade suportada. Se a intenção for permitir entidades ainda não carregadas, rejeitar ao menos IDs malformados/inexistentes no registry e reportar explicitamente o estado indisponível.

3. **IDs de agendamento não são únicos, causando atualização/remoção ambígua e perda de dados.**
   - **Arquivo:** `schedules.py:24-28`; `__init__.py:326-358, 360-370, 394-415`
   - **Problema/evidência:** `add_schedule` preserva qualquer `id` fornecido e não verifica se ele já existe; `set_schedules` também não verifica duplicatas. `update_schedule` altera apenas o primeiro item correspondente (`matched` vira `True`), enquanto `remove_schedule` remove todos os itens com o mesmo ID. Assim, duas linhas com o mesmo ID podem ficar parcialmente atualizadas e ser apagadas juntas.
   - **Correção concreta:** rejeitar IDs duplicados dentro de `set_schedules` e contra a zona em `add_schedule`; alternativamente ignorar IDs recebidos e sempre gerar um UUID. Validar também unicidade antes de persistir opções.

4. **O options flow sobrescreve todo o `data` da entrada.**
   - **Arquivo:** `config_flow.py:207-214`
   - **Problema/evidência:** `async_update_entry(..., data={CONF_NAME: name})` substitui o dicionário inteiro, em vez de preservar `self.config_entry.data`. Hoje a criação grava somente `name`, mas qualquer chave adicionada por migração, versão futura ou instalação anterior é perdida ao editar opções.
   - **Correção concreta:** usar `data={**self.config_entry.data, CONF_NAME: name}` (ou não alterar `data` no options flow, se o nome não precisar ser atualizado ali).

5. **Horário ambíguo no retorno do DST pode ser silenciosamente perdido.**
   - **Arquivo:** `next_run.py:14-17, 24-33`
   - **Problema/evidência:** para o horário repetido no outono, `datetime.combine()` usa implicitamente `fold=0`. Se o Home Assistant reiniciar ou for configurado depois da primeira ocorrência, mas ainda durante a segunda ocorrência, o candidato único já é considerado passado e o agendamento só volta na semana seguinte. Não há política explícita nem teste para esse caso.
   - **Correção concreta:** definir/documentar a política (executar a primeira ocorrência, a segunda ou uma vez por dia) e tratar `fold` explicitamente; no mínimo, testar `America/New_York` durante a janela ambígua e garantir que o comportamento escolhido seja consistente.

6. **O armazenamento compartilhado não serializa leituras/gravações concorrentes.**
   - **Arquivo:** `store.py:17-30`
   - **Problema/evidência:** várias zonas compartilham um `RuntimeStore`; `async_get()` e `async_set()` fazem `async_load()` sem lock e `async_set(immediate=True)` pode executar vários `async_save()` concorrentes. Em inicialização simultânea ou término simultâneo de runs, uma operação pode carregar uma versão antiga e sobrescrever a atualização de outra zona.
   - **Correção concreta:** proteger load/mutação/save com `asyncio.Lock` no store e garantir que todo save imediato passe pela mesma fila crítica; adicionar teste com duas zonas salvando simultaneamente.

### Menor

7. **Validação de potência é sensível a capitalização da unidade.**
   - **Arquivo:** `power.py:7-15, 18-28`
   - **Problema/evidência:** `POWER_UNITS` aceita somente `"W"` e `"kW"`. Um sensor válido que publique `"w"`, `"KW"` ou uma unidade equivalente não é reconhecido, impedindo pareamento automático e confirmação por potência.
   - **Correção concreta:** normalizar a unidade (`casefold`, com tratamento específico de prefixos) antes da comparação, mantendo a conversão para watts coberta por testes.

8. **O card informa uma versão diferente da versão do manifest.**
   - **Arquivo:** `manifest.json:12`; `frontend/light-schedule-card.js:1`
   - **Problema/evidência:** o manifest declara `0.6.0`, enquanto o card imprime `0.7.0`. Isso dificulta diagnóstico de cache e identificação do conjunto realmente instalado.
   - **Correção concreta:** usar uma única fonte/versionamento e atualizar os dois valores no mesmo release.

## Falsos positivos percebidos

- Os listeners das entidades (`sensor.py:235-245`, `scheduler.py:145-161`) têm remoção registrada; não encontrei vazamento confirmado no unload.
- O options flow preserva schedules, mappings e duração ao retornar `async_create_entry` (`config_flow.py:193-215`); o problema encontrado é a substituição do `data` da entrada, não a perda normal das opções.
- A confirmação em `scheduler.py:327-343` aceita o estado próprio quando o sensor de potência está indisponível. Isso é uma escolha de degradação documentada no código, não foi classificado como bug isoladamente.
- Os testes existentes cobrem horário recorrente, zona desabilitada e salto de DST; a lacuna concreta é o horário ambíguo no retorno do DST.

## Sugestões adicionais

- Adicionar testes de integração para corrida `scheduled_start`/`async_stop`, unload durante ramp, falha de confirmação e vários alvos.
- Testar a semântica real de `TargetSelection` e do schema de serviços na versão de Home Assistant suportada, incluindo `entry_id` não declarado em `services.yaml`.
- Fazer o card aguardar a atualização de estado após operações de serviço, em vez de chamar `_render()` imediatamente com estado potencialmente antigo (`light-schedule-card.js:481-484, 597-603`).

## Comandos executados

- `python -m compileall -q custom_components/light_scheduler`
- `python -m pytest -q` — **3 passed, 1 skipped**
- `node --check custom_components/light_scheduler/frontend/light-schedule-card.js`
- Validação dos JSON com `python` — **JSON OK**
- `git status --short` (antes da revisão)

## Status

**PRECISA DE ALTERAÇÃO**
