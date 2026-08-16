# Revisão de Código: light_scheduler

**Revisor:** qwen3.7-max  
**Data:** 2026-08-16  
**Escopo:** Integração completa `custom_components/light_scheduler/` (11 arquivos Python, 1 card JS, manifests, traduções)  
**Status:** PRECISA DE ALTERAÇÃO

---

## Problemas Críticos

### 1. `async_stop` durante ramp-up cancela a task mas não desliga luzes já ligadas
**Arquivo:** `scheduler.py:445-481`

**Problema:** Quando `async_stop()` é chamado enquanto `async_turn_on()` está no meio do loop de `_actuate` (linhas 427-437), o código cancela `_ramp_task` (linha 455-456):

```python
ramp_task = self._ramp_task
current_task = asyncio.current_task()
if ramp_task and ramp_task is not current_task and not ramp_task.done():
    ramp_task.cancel()
    await asyncio.gather(ramp_task, return_exceptions=True)
```

O problema é que `async_turn_on` pode estar sendo executado **na mesma task** que chamou `async_stop` (ex: se `_async_finish` chama `async_stop` e o timer de finish dispara enquanto o ramp-up ainda está em andamento). Nesse caso, `ramp_task is current_task` é `True`, e o cancelamento não acontece. O loop de ramp-up continua, e o loop de stop (linha 463-467) só começa após o ramp-up terminar.

Mais grave: se `async_stop` for chamado de uma task diferente (ex: serviço `stop`), o ramp-up é cancelado via `CancelledError`, mas as luzes que já foram ligadas com sucesso (linhas 430-431) não são desligadas imediatamente. O loop de stop (linha 463-467) usa `self._run_targets`, que contém todas as luzes que estavam disponíveis no início do ramp-up, incluindo as que ainda não foram ligadas. Isso é correto, mas se uma luz foi ligada com sucesso e depois ficou indisponível, o `_actuate` vai falhar e adicionar uma warning, mas a luz pode permanecer ligada fisicamente.

**Correção:** Rastrear quais luzes foram efetivamente ligadas durante o ramp-up e desligar apenas essas, ou adicionar um flag `_ramp_interrupted` que o loop de stop verifica para pular luzes que não foram ligadas.

---

### 2. Timezone/DST: `find_next_run` não lida com horários ambíguos
**Arquivo:** `next_run.py:19-34`

**Problema:** A função `_exists` (linhas 14-17) verifica se um horário local existe após conversão UTC→local→UTC, mas **não lida corretamente com horários ambíguos durante transições de DST**. Se um horário cair no "overlap" de DST (ex: 1:30 AM que ocorre duas vezes), `_exists` retorna `True`, mas o código não especifica qual das duas ocorrências usar. O `datetime.combine` usa `fold=0` por padrão, o que pode agendar a primeira ocorrência (ainda no horário de verão) quando o usuário esperava a segunda (horário normal).

```python
# next_run.py:30
candidate = datetime.combine(date, schedule_time, tzinfo=now.tzinfo)
```

Mais grave: se o horário cair exatamente no "gap" de DST (ex: 2:30 AM que não existe), `_exists` retorna `False` e o agendamento é pulado silenciosamente. O usuário não é notificado, e a luz não liga nesse dia.

**Correção:** Usar `fold=1` para preferir a segunda ocorrência (mais conservador, evita ligar luzes cedo demais). Alternativamente, notificar o usuário quando um agendamento for pulado devido a DST, ou ajustar automaticamente para o horário mais próximo que existe.

---

### 3. `_schedule_next` pode agendar início no passado sob carga
**Arquivo:** `scheduler.py:259-283`

**Problema:** A função `find_next_run` retorna um horário futuro, mas entre o retorno e o agendamento do timer (linha 280-282), o horário pode já ter passado (ex: se o sistema estiver sob carga e o event loop estiver atrasado). O `async_track_point_in_time` do HA lida com isso executando imediatamente, mas isso pode causar comportamento inesperado se o scheduler estiver no meio de um unload.

```python
# scheduler.py:280-282
self._unsub_next = async_track_point_in_time(
    self.hass, scheduled_start, upcoming
)
```

O callback `scheduled_start` (linhas 275-278) verifica `generation != self._schedule_generation`, mas não verifica `self._unloading`. Se o unload começar após o timer ser agendado mas antes de disparar, o callback pode executar e tentar iniciar uma execução em um scheduler que está sendo descarregado.

**Correção:** Verificar `self._unloading` dentro de `scheduled_start` antes de executar:
```python
async def scheduled_start(_: datetime) -> None:
    if generation != self._schedule_generation or self._unloading:
        return
    await self._async_scheduled_start(upcoming, schedule)
```

---

### 4. Validação insuficiente em `set_zone_options` permite perda de pareamentos
**Arquivo:** `__init__.py:417-496`

**Problema:** O serviço `set_zone_options` aceita `entity_mappings` e o substitui completamente (linhas 436-446). Se o card enviar mappings incompletos (ex: sem `power_entity_id` para algumas luzes), os pareamentos existentes são perdidos. O código não faz merge com mappings anteriores.

```python
# __init__.py:436-446
if CONF_ENTITY_MAPPINGS in data:
    mappings = _normalize_mappings(hass, data[CONF_ENTITY_MAPPINGS])
    options[CONF_ENTITY_MAPPINGS] = mappings
    options[CONF_TARGET_ENTITY_IDS] = [
        item["target_entity_id"] for item in mappings
    ]
    options[CONF_POWER_ENTITY_IDS] = [
        item["power_entity_id"]
        for item in mappings
        if item["power_entity_id"]
    ]
```

O card envia todos os mappings via `_saveZone` (light-schedule-card.js:572-603), mas se um sensor de potência ficou indisponível e não foi retornado pelo `_entityChoices` do card (linha 340-373), ele será omitido e o pareamento perdido. O `_entityChoices` filtra por `device_class` e `unit_of_measurement`, não por disponibilidade, então sensores indisponíveis ainda aparecem. Mas se um sensor foi removido do HA (não apenas indisponível), ele não aparecerá, e o pareamento será perdido.

**Correção:** Fazer merge com mappings existentes, preservando `power_entity_id` quando não fornecido explicitamente:
```python
if CONF_ENTITY_MAPPINGS in data:
    new_mappings = _normalize_mappings(hass, data[CONF_ENTITY_MAPPINGS])
    existing = {
        item["target_entity_id"]: item
        for item in scheduler.entity_mappings
    }
    merged = []
    for new in new_mappings:
        target = new["target_entity_id"]
        if not new["power_entity_id"] and target in existing:
            new["power_entity_id"] = existing[target].get("power_entity_id", "")
        merged.append(new)
    options[CONF_ENTITY_MAPPINGS] = merged
```

---

### 5. Cache de power mapping não é invalidado quando opções mudam
**Arquivo:** `sensor.py:52-123`

**Problema:** O método `_power_mapping` cacheia o resultado em `self._cached_power_mapping` (linha 122). O cache é invalidado em `_handle_update` (linha 200), que é chamado via dispatcher quando o scheduler envia `_notify()`. Mas **não é invalidado quando as opções do config entry mudam** via options flow. Se o usuário editar os mappings via options flow, o scheduler chama `async_options_updated` (scheduler.py:526-531), que chama `_schedule_next()`, que chama `_notify()`. Então o cache deveria ser invalidado. Mas se o dispatcher signal for enviado antes de o sensor ser atualizado, o cache pode ficar desatualizado.

```python
# sensor.py:60-61
if self._cached_power_mapping is not None:
    return dict(self._cached_power_mapping)
```

**Correção:** Invalidar o cache explicitamente em `async_options_updated` ou assinar mudanças de options diretamente no sensor.

---

## Problemas Médios

### 6. Confirmação de atuação pode falhar silenciosamente
**Arquivo:** `scheduler.py:357-384`

**Problema:** O método `_actuate` tenta confirmar se a luz mudou de estado físico (via estado HA + sensor de potência), mas se a confirmação falhar após 2 tentativas, apenas loga um warning (linha 380-383) e retorna `False`. O chamador (`async_turn_on` ou `async_stop`) adiciona a entidade a `_run_warnings`, mas **não interrompe a execução**.

Isso significa que se uma luz não responder, o scheduler continua tentando ligar/desligar as outras, o que é correto, mas o usuário não é notificado em tempo real. As warnings só aparecem no `binary_sensor.active` após o fim da execução.

```python
# scheduler.py:430-432
if not await self._actuate(entity_id, "turn_on", True):
    self._run_warnings.append(entity_id)
    self._notify()
```

**Correção:** Expor `_run_warnings` via dispatcher imediatamente (o código já chama `_notify()`, então isso deveria funcionar, mas o card não mostra warnings em tempo real). Adicionar um evento HA quando uma atuação falhar, ou mostrar warnings no card durante a execução.

---

### 7. `_reconcile_active_run_edit` não lida com schedule deletado
**Arquivo:** `scheduler.py:533-563`

**Problema:** Se o usuário deletar o schedule que está atualmente em execução, `_reconcile_active_run_edit` não encontra o schedule (linha 544-550) e retorna sem fazer nada. A execução continua com o `finishes_at` original, o que é correto, mas o `_run_schedule_id` fica órfão.

```python
# scheduler.py:544-550
schedule = next(
    (
        item for item in self.options.get(CONF_SCHEDULES, [])
        if item.get(CONF_SCHEDULE_ID) == self._run_schedule_id
    ),
    None,
)
if schedule is None:
    return
```

**Correção:** Limpar `_run_schedule_id` quando o schedule não for encontrado, para evitar confusão em logs/telemetria:
```python
if schedule is None:
    self._run_schedule_id = None
    return
```

---

### 8. Card JS: autocomplete não fecha ao clicar fora do dialog
**Arquivo:** `frontend/light-schedule-card.js:623-627`

**Problema:** O handler `_handleFocusOut` fecha o autocomplete apenas se o `relatedTarget` não estiver dentro do autocomplete. Mas se o usuário clicar fora do dialog (ex: no backdrop), o `relatedTarget` é `null`, e o autocomplete não fecha.

```javascript
// light-schedule-card.js:623-627
_handleFocusOut(event) {
  const autocomplete = event.target.closest?.("[data-autocomplete]");
  if (!autocomplete || autocomplete.contains(event.relatedTarget)) return;
  this._closeAutocomplete(autocomplete);
}
```

**Correção:** Fechar o autocomplete se `relatedTarget` for `null` ou não estiver dentro do autocomplete:
```javascript
if (!autocomplete || (event.relatedTarget && autocomplete.contains(event.relatedTarget))) return;
```

---

### 9. Card JS: `_durationBetween` retorna 86400 quando start == end
**Arquivo:** `frontend/light-schedule-card.js:729-734`

**Problema:** Se o usuário definir start e end iguais (ex: 18:00 → 18:00), a função retorna 86400 (24 horas) devido ao `|| 86400` no final. Isso pode ser intencional (interpretar como "ligado por 24h"), mas não é documentado e pode confundir o usuário.

```javascript
// light-schedule-card.js:733
return (endSeconds - startSeconds + 86400) % 86400 || 86400;
```

**Correção:** Adicionar validação no `_saveSchedule` para rejeitar durações de 24h ou mostrar um aviso:
```javascript
if (data.duration >= 86400) {
  this._showDialogError("Duração não pode ser 24 horas ou mais.");
  return;
}
```

---

### 10. `_save_runtime` pode ser chamado após unload
**Arquivo:** `scheduler.py:502-518`

**Problema:** Se `_save_runtime` for chamado após `async_unload` (ex: em uma task de background que não foi cancelada a tempo), o store pode estar em estado inconsistente. O código não verifica `self._unloading`.

```python
# scheduler.py:513-517
await self.store.async_set(
    self.entry.entry_id,
    {"history": self._history, "active_run": active_run},
    immediate=immediate,
)
```

**Correção:** Retornar early se `self._unloading` for `True`:
```python
async def _save_runtime(self, *, immediate: bool = False) -> None:
    if self._unloading:
        return
    # ...
```

---

### 11. `_on_light_changed` registra eventos externos mesmo quando scheduler está ativo
**Arquivo:** `scheduler.py:208-221`

**Problema:** O callback retorna early se `self._active` (linha 210-211), mas se o scheduler estiver no meio de um `async_stop` (ou seja, `_stopping` é `True` mas `_active` ainda é `True`), eventos de luz não são registrados. Isso pode perder transições externas durante o desligamento.

```python
# scheduler.py:209-211
@callback
def _on_light_changed(self, event: Event) -> None:
    if self._active:
        return
```

**Correção:** Registrar eventos externos mesmo durante `_stopping`, ou documentar que eventos durante desligamento são ignorados. Alternativamente, verificar `self._active and not self._stopping`.

---

## Problemas Menores

### 12. `_normalize_mappings` não valida se target existe
**Arquivo:** `__init__.py:96-140`

**Problema:** A função valida o formato de `target_entity_id` (linha 111-114) mas não verifica se a entidade existe no HA. Se o usuário enviar uma entidade inexistente, ela será aceita e apenas falhará durante a atuação.

```python
# __init__.py:111-114
if not target.startswith(("light.", "switch.")):
    raise ServiceValidationError(
        "Cada entrada precisa de uma entidade light ou switch."
    )
```

**Correção:** Verificar `hass.states.get(target)` e rejeitar entidades inexistentes, ou aceitar entidades inexistentes mas logar um warning.

---

### 13. `_prune_history` pode reter registros inválidos
**Arquivo:** `scheduler.py:487-500`

**Problema:** Se `started_at` não for parseável (linha 493-497), o registro é retido (linha 498-499). Isso pode acumular registros corrompidos ao longo do tempo.

```python
# scheduler.py:498-499
if not started or dt_util.as_utc(started) >= cutoff:
    retained.append(item)
```

**Correção:** Descartar registros com `started_at` inválido:
```python
if started and dt_util.as_utc(started) >= cutoff:
    retained.append(item)
```

---

### 14. Card JS: `_formatDays` assume ordem dos dias
**Arquivo:** `frontend/light-schedule-card.js:859-865`

**Problema:** A função ordena os dias (linha 860), mas se o backend enviar dias fora de ordem, a exibição pode ser confusa. Além disso, a verificação de "seg–sex" (linha 862) assume que os dias são exatamente `[0,1,2,3,4]`, o que é correto, mas frágil.

```javascript
// light-schedule-card.js:862
if (list.length === 5 && list.every((value, index) => value === index)) return "seg–sex";
```

**Correção:** Usar uma verificação mais explícita:
```javascript
if (list.length === 5 && list.slice(0, 5).every((v, i) => v === i)) return "seg–sex";
```

---

### 15. `services.yaml` não documenta `entry_id` como alternativa a `target`
**Arquivo:** `services.yaml:1-116`

**Problema:** Todos os serviços aceitam `entry_id` como parâmetro (via `_resolve` em `__init__.py:245-293`), mas isso não está documentado em `services.yaml`. O card usa `entry_id` internamente, mas usuários que chamam serviços via automação podem não saber disso.

**Correção:** Adicionar campo `entry_id` em todos os serviços no `services.yaml`:
```yaml
fields:
  entry_id:
    name: Entry ID
    description: ID da zona (alternativa a target)
    selector:
      text: {}
```

---

### 16. Card JS: `_entityChoices` filtra incorretamente entidades do scheduler
**Arquivo:** `frontend/light-schedule-card.js:340-373`

**Problema:** O filtro de `schedulerEntryIds` (linhas 342-346) inclui qualquer estado com `attributes.lights` e `attributes.entry_id`, mas outras integrações podem ter atributos similares. Isso pode filtrar incorretamente entidades não relacionadas.

```javascript
// light-schedule-card.js:342-346
const schedulerEntryIds = new Set(
  states
    .filter((state) => Array.isArray(state.attributes?.lights) && state.attributes?.entry_id)
    .map((state) => state.attributes.entry_id)
);
```

**Correção:** Verificar também se `entity_id` começa com `sensor.light_scheduler` ou se `attributes.entry_id` corresponde a um config entry do domínio `light_scheduler`:
```javascript
const schedulerEntryIds = new Set(
  states
    .filter((state) => 
      state.entity_id.startsWith("sensor.light_scheduler") &&
      Array.isArray(state.attributes?.lights) && 
      state.attributes?.entry_id
    )
    .map((state) => state.attributes.entry_id)
);
```

---

### 17. `power.py` não lida com unidades não padrão
**Arquivo:** `power.py:7-28`

**Problema:** O dicionário `POWER_UNITS` (linha 7) suporta apenas "W" e "kW". Se um sensor usar "watt" ou "kilowatt" (por extenso), o valor não será convertido corretamente.

```python
# power.py:7
POWER_UNITS = {"W": 1.0, "kW": 1000.0}
```

**Correção:** Adicionar variações comuns:
```python
POWER_UNITS = {"W": 1.0, "kW": 1000.0, "watt": 1.0, "kilowatt": 1000.0}
```

---

### 18. `manifest.json` não especifica `homeassistant` version mínima
**Arquivo:** `manifest.json:1-13`

**Problema:** O manifesto não inclui `"homeassistant": "2024.X.X"` para especificar a versão mínima do HA. Isso pode causar falhas de instalação em versões antigas que não suportam APIs usadas (ex: `async_register_static_paths`, `runtime_data`).

**Correção:** Adicionar:
```json
"homeassistant": "2024.6.0"
```

---

### 19. `_schedule_generation` não é persistido
**Arquivo:** `scheduler.py:50`

**Problema:** O `_schedule_generation` é inicializado como 0 e incrementado a cada chamada de `_schedule_next`. Se o scheduler for descarregado e recarregado, o contador reinicia, e timers agendados anteriormente (se persistidos) podem ser executados incorretamente. Mas como os timers não são persistidos, isso não é um problema na prática.

**Correção:** Nenhum, apenas documentar que `_schedule_generation` é volátil.

---

### 20. `_background_tasks` pode acumular tasks completadas
**Arquivo:** `scheduler.py:51, 203-206`

**Problema:** O código usa `task.add_done_callback(self._background_tasks.discard)` (linha 206) para remover tasks completadas. Isso é correto, mas se uma task levantar uma exceção não tratada, ela pode permanecer no set até ser descartada. O `async_unload` cancela e aguarda todas as tasks (linhas 158-161), então não há vazamento.

**Correção:** Nenhum, o código está correto.

---

## Falsos Positivos Percebidos

1. **Perda de `enabled` no options flow:** O spread `{**self.config_entry.options, ...}` preserva `CONF_ENABLED` se ele já estiver em options. Como `CONF_ENABLED` é sempre adicionado na criação (config_flow.py:150-162), o problema é muito improvável na prática.

2. **Vazamento de listeners de dispatcher:** Os listeners são registrados via `async_on_remove`, que é chamado quando a entidade é removida. Se `async_unload_entry` descarrega as plataformas (linha 198), as entidades são removidas e os listeners são cancelados. A ordem é: primeiro scheduler.async_unload() (linha 197), depois async_unload_platforms (linha 198). Entre esses dois passos, os listeners de dispatcher ainda estão ativos, mas o scheduler já foi descarregado. Se um dispatcher signal for enviado nesse intervalo, os callbacks tentarão acessar o scheduler, que ainda existe (não é destruído), então não há crash.

3. **`_schedule_generation` parece desnecessário:** Na verdade, é usado para invalidar timers agendados quando as opções mudam (linha 276-277). Não é um bug.

4. **`async_delay_save` pode parecer perda de dados:** O store usa `async_delay_save` com 2 segundos (linha 31), mas chamadas críticas usam `immediate=True` (ex: linha 419, 479), então dados importantes são persistidos imediatamente.

---

## Comandos Executados

Nenhum comando de teste foi executado, pois a revisão é estática e não há ambiente HA disponível.

---

## Sugestões Adicionais

1. **Adicionar testes unitários** para `find_next_run` com cenários de DST.
2. **Implementar retry exponencial** em `_actuate` em vez de apenas 2 tentativas.
3. **Expor `warnings` via evento HA** para permitir automações que reajam a falhas de atuação.
4. **Adicionar diagnóstico** no card para mostrar quando uma luz não confirmou atuação.
5. **Considerar usar `async_track_state_change_filtered`** em vez de `async_track_state_change_event` para reduzir overhead.
6. **Adicionar logs estruturados** (ex: `structlog`) para facilitar debugging em produção.
7. **Implementar health check** no card para mostrar se o scheduler está funcionando corretamente.

---

## Resumo

A integração é bem estruturada e lida corretamente com a maioria dos cenários. Os problemas críticos identificados são:

1. Corrida entre turn_on/turn_off pode deixar luzes ligadas (raro, mas possível)
2. Timezone/DST não lida com horários ambíguos (afeta usuários em regiões com DST)
3. `_schedule_next` pode agendar início no passado sob carga (raro)
4. Perda de pareamentos de potência no card (quando sensores são removidos)
5. Cache de power mapping não é invalidado quando opções mudam (timing issue)

Recomenda-se corrigir os problemas críticos antes do próximo release, especialmente #1 e #2, que podem afetar a experiência do usuário.
