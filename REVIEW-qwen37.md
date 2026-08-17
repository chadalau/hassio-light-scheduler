# REVIEW-qwen37.md - Revisao independente light_scheduler

**Revisor**: qwen3.7-max  
**Data**: 2026-08-16  
**Escopo**: `custom_components/light_scheduler/` (todos os arquivos) + `tests/`  
**Status**: **PRECISA DE ALTERACAO** (2 criticos, 6 medios, 7 menores)

---

## Comandos executados

```
pytest tests/ -v
# Resultado: 10 passed, 1 skipped (test_ignores_nonexistent_dst_time: tzdata ausente)
```

## Arquivos revisados

| Arquivo | Linhas |
|---------|--------|
| `const.py` | 51 |
| `__init__.py` | 537 |
| `scheduler.py` | 633 |
| `sensor.py` | 245 |
| `switch.py` | 27 |
| `binary_sensor.py` | 33 |
| `next_run.py` | 34 |
| `store.py` | 43 |
| `schedules.py` | 39 |
| `config_flow.py` | 247 |
| `power.py` | 37 |
| `frontend/light-schedule-card.js` | 1303 |
| `manifest.json` | 13 |
| `services.yaml` | 153 |
| `strings.json` | 59 |
| `translations/en.json` | 59 |
| `translations/pt-BR.json` | 59 |
| `tests/test_next_run.py` | 70 |
| `tests/test_confirmation.py` | 223 |

---

## Problemas por severidade

### CRITICO

#### C1. `add_schedule` permite IDs duplicados, corrompendo operacoes subsequentes

**Arquivo**: `__init__.py:339-349`

```python
async def add(call: ServiceCall) -> None:
    schedule = _validated_schedule(_service_data(call))
    for scheduler in await _resolve(hass, call):
        options = {
            **scheduler.options,
            CONF_SCHEDULES: [
                *scheduler.options.get(CONF_SCHEDULES, []),
                schedule,
            ],
        }
        await _update_options(hass, scheduler, options)
```

O `new_schedule` (em `schedules.py:38`) preserva um `id` fornecido pelo usuario: `result[CONF_SCHEDULE_ID] = result.get(CONF_SCHEDULE_ID) or uuid4().hex`. Se o caller passar um `id` que ja existe, dois schedules com o mesmo ID sao criados. As operacoes `update_schedule` e `remove_schedule` usam busca linear com `next(...)` implicito (`for schedule in ...: if schedule.get(CONF_SCHEDULE_ID) == schedule_id`), o que significa que o segundo schedule com o mesmo ID torna-se invisivel para edicao e impossivel de remover individualmente.

**Cenario**: card ou automacao chama `add_schedule` com `id: "meu_id"` duas vezes. O `update_schedule` com `id: "meu_id"` sempre edita o primeiro. O `remove_schedule` remove o primeiro, deixando o segundo como "fantasma" com o mesmo ID.

**Correcao**: Antes de adicionar, verificar se ja existe um schedule com o mesmo ID e lancar `ServiceValidationError`:

```python
existing_ids = {s.get(CONF_SCHEDULE_ID) for s in scheduler.options.get(CONF_SCHEDULES, [])}
if schedule.get(CONF_SCHEDULE_ID) in existing_ids:
    raise ServiceValidationError(f"ID duplicado: {schedule[CONF_SCHEDULE_ID]}")
```

---

#### C2. `update_schedule` e `remove_schedule` aplicam parcialmente em multiplas zonas

**Arquivo**: `__init__.py:408-429` (update) e `351-371` (remove)

```python
for scheduler in await _resolve(hass, call):
    schedules: list[dict[str, Any]] = []
    matched = False
    for schedule in scheduler.options.get(CONF_SCHEDULES, []):
        if schedule.get(CONF_SCHEDULE_ID) == schedule_id:
            schedules.append(_validated_schedule({...}))
            matched = True
        else:
            schedules.append(schedule)
    if not matched:
        raise ServiceValidationError(f"Agendamento desconhecido: {schedule_id}")
    await _update_options(hass, scheduler, {...})
```

Quando `_resolve` retorna multiplos schedulers (ex: `entity_id` apontando para entidades de duas zonas), o loop itera sobre cada um. Se o `schedule_id` existe na primeira zona mas nao na segunda, a primeira ja foi persistida via `_update_options` quando a segunda lanca `ServiceValidationError`. O resultado e uma atualizacao parcial: a primeira zona foi modificada, a segunda nao, e o caller recebe um erro.

O mesmo padrao se aplica a `remove_schedule` (linha 351-371).

**Correcao**: Validar que TODOS os schedulers contem o `schedule_id` ANTES de aplicar qualquer mudanca:

```python
schedulers = await _resolve(hass, call)
for scheduler in schedulers:
    if not any(s.get(CONF_SCHEDULE_ID) == schedule_id for s in scheduler.options.get(CONF_SCHEDULES, [])):
        raise ServiceValidationError(f"Agendamento desconhecido: {schedule_id} em {scheduler.entry.title}")
for scheduler in schedulers:
    # aplicar mudanca
```

---

### MEDIO

#### M1. Horario ambiguo no DST (fold) dispara sempre na primeira ocorrencia

**Arquivo**: `next_run.py:30`

```python
candidate = datetime.combine(date, schedule_time, tzinfo=now.tzinfo)
```

`datetime.combine` cria o datetime com `fold=0` por padrao. Em um dia de transicao DST onde o relogio volta (ex: 03:00 -> 02:00), o horario 02:30 existe duas vezes: uma antes do relogio voltar (fold=0, UTC mais tarde) e uma depois (fold=1, UTC mais cedo). O `_exists` (linha 14-17) retorna True para ambos os folds, mas o `combine` sempre produz fold=0.

**Cenario**: Usuario agenda para 02:30 em um dia de fall-back. O agendamento dispara na primeira ocorrencia de 02:30 (antes do relogio voltar), que e ~1h antes do que o usuario pode esperar.

**Correcao**: Documentar o comportamento ou, idealmente, gerar dois candidates (fold=0 e fold=1) e escolher o primeiro que ainda nao passou:

```python
for fold in (0, 1):
    candidate = datetime.combine(date, schedule_time, tzinfo=now.tzinfo, fold=fold)
    ...
```

---

#### M2. `_async_scheduled_start` estende duracao mas nao atualiza targets

**Arquivo**: `scheduler.py:299-310`

```python
if self._active:
    candidate_finish = dt_util.as_utc(scheduled_at) + timedelta(
        seconds=int(schedule[CONF_SCHEDULE_DURATION])
    )
    if not self._stopping and (
        self._finishes_at is None or candidate_finish > self._finishes_at
    ):
        self._finishes_at = candidate_finish
        self._arm_finish_timer()
        await self._save_runtime(immediate=True)
    self._schedule_next()
    return
```

Quando um segundo agendamento dispara enquanto um run esta ativo, o finish time e estendido, mas `_run_targets` nao e atualizado. Se o segundo agendamento tem `target_entity_ids` diferente (ex: inclui uma luz que nao estava no primeiro agendamento), essa luz adicional nunca e ligada. O run original continua com suas targets originais, e o `_async_stop_sequence` desliga apenas as targets originais.

**Cenario**: Agendamento A (18:00, luzes X+Y) inicia um run. Agendamento B (19:00, luzes X+Y+Z) dispara durante o run. O finish time e estendido, mas Z nunca e ligada.

**Correcao**: Adicionar as novas targets ao `_run_targets` e despachar `turn_on` para elas:

```python
new_targets = set(schedule.get(CONF_TARGET_ENTITY_IDS) or self.target_entity_ids) - set(self._run_targets)
for entity_id in new_targets:
    await self._dispatch(entity_id, "turn_on")
    self._run_targets.append(entity_id)
```

---

#### M3. `_on_light_changed` ignora mudancas durante run ativo, corrompendo historico externo

**Arquivo**: `scheduler.py:210-212`

```python
@callback
def _on_light_changed(self, event: Event) -> None:
    if self._active:
        return
```

Se uma luz e ligada manualmente ANTES de um run comecar, `_record_external` abre um registro com `source=SOURCE_EXTERNAL`. Quando o run comeca, `_active` torna-se True e mudancas subsequentes sao ignoradas. Quando o run termina, `_active` volta a False. Se a luz desliga depois, `_record_external` fecha o registro externo, mas a duracao calculada inclui todo o tempo do run.

**Cenario**: Luz ligada manualmente as 18:00. Run do agendamento comeca as 18:30 (duracao 1h). Run termina as 19:30. Luz desliga manualmente as 20:00. O historico externo mostra duracao de 2h (18:00-20:00) em vez de 1h30 (18:00-18:30 + 19:30-20:00).

**Correcao**: Ao iniciar um run, fechar quaisquer registros externos abertos para as targets do run. Ao terminar, reabrir se a luz ainda esta on.

---

#### M4. `_restore_active_run` usa targets que podem ter sido removidos da configuracao

**Arquivo**: `scheduler.py:190-195`

```python
stored_targets = value.get("targets")
self._run_targets = (
    [str(entity_id) for entity_id in stored_targets]
    if isinstance(stored_targets, list)
    else list(self.target_entity_ids)
)
```

Se o HA reinicia e uma entidade que fazia parte do run ativo foi removida da configuracao (via options flow ou edicao direta), os `_run_targets` restaurados incluem a entidade removida. O `_async_stop_sequence` tenta desligar todas as targets, incluindo a inexistente. O `_dispatch` captura a excecao, mas o `_confirm_group` espera `ACTUATION_GRACE` (15s) por uma entidade que nunca vai responder, atrasando o stop em ate 30s (grace + retry).

**Correcao**: Filtrar `_run_targets` contra `self.target_entity_ids` na restauracao:

```python
self._run_targets = [
    eid for eid in stored_targets
    if eid in self.target_entity_ids
] or list(self.target_entity_ids)
```

---

#### M5. `serialize_schedule` perde segundos do campo `time`

**Arquivo**: `schedules.py:19-21`

```python
value = result.get(CONF_SCHEDULE_TIME)
if isinstance(value, time):
    result[CONF_SCHEDULE_TIME] = value.strftime("%H:%M")
```

O `cv.time` do HA aceita "HH:MM:SS" e produz um `datetime.time` com segundos. O `strftime("%H:%M")` descarta os segundos. Se um usuario ou automacao criar um agendamento com `time: "18:30:45"`, os segundos sao silenciosamente perdidos.

**Correcao**: Usar `value.strftime("%H:%M:%S")` se segundos forem relevantes, ou `value.isoformat()` que preserva todos os componentes.

---

#### M6. `_prune_history` preserva indefinidamente registros com `started_at` invalido

**Arquivo**: `scheduler.py:551-564`

```python
for item in self._history:
    raw_started = item.get("started_at")
    started = (
        dt_util.parse_datetime(raw_started)
        if isinstance(raw_started, str)
        else None
    )
    if not started or dt_util.as_utc(started) >= cutoff:
        retained.append(item)
```

Registros com `started_at` corrompido (None, string invalida, ou nao-string) sao mantidos para sempre, pois `not started` e True. Com o tempo, esses registros zombie ocupam espaco no store e contam contra o `HISTORY_MAX_ENTRIES` (200), efetivamente reduzindo o historico util.

**Correcao**: Remover registros com `started_at` invalido:

```python
if started and dt_util.as_utc(started) >= cutoff:
    retained.append(item)
```

---

### MENOR

#### m1. `async_turn_on` nao verifica `_unloading`, permitindo ligar luzes durante unload

**Arquivo**: `scheduler.py:405-421`

```python
async def async_turn_on(self, ...):
    if self._active:
        return
    ...
    self._active, self._source = True, source
```

Se um timer de `async_track_point_in_time` dispara na janela entre o inicio do `async_unload` e o cancelamento do listener (`_unsub_next()`), o `scheduled_start` pode executar. O `_schedule_generation` nao e incrementado no unload (apenas no `_schedule_next`), entao o check de geracao passa. O `async_turn_on` nao verifica `_unloading` e prossegue com o dispatch.

Na pratica, o cancelamento do `TimerHandle` e sincrono e a janela e minima, mas o check defensivo custa nada:

```python
if self._active or self._unloading:
    return
```

---

#### m2. `_dispatch` captura `Exception` mas nao `BaseException` (CancelledError)

**Arquivo**: `scheduler.py:350-359`

```python
async def _dispatch(self, entity_id: str, service: str) -> None:
    try:
        await self.hass.services.async_call(
            "homeassistant", service, {"entity_id": entity_id}, blocking=True
        )
    except Exception:
        _LOGGER.exception(...)
```

Em Python 3.8+, `CancelledError` e subclasse de `BaseException`, nao de `Exception`. Se o task e cancelado durante o `_dispatch`, o `CancelledError` propaga sem ser logado. Isso e tecnicamente correto (cancelamento nao e erro), mas o `_LOGGER.exception` sugere que a intencao era capturar tudo. O comportamento atual e aceitavel, mas o comentario "Home Assistant reports the failing entity" e enganoso.

---

#### m3. `async_delay_save` pode perder dados em desligamento abrupto

**Arquivo**: `store.py:42-43`

```python
else:
    self._store.async_delay_save(lambda: self._data or {}, 2)
```

Mudancas nao-imediatas sao salvas com delay de 2 segundos. Se o HA desliga abruptamente (crash, power loss) nesse intervalo, os dados sao perdidos. Os pontos criticos (inicio/fim de run) usam `immediate=True`, mas registros de historico externo (`_record_external`) usam `immediate=False`. Um ciclo rapido de liga/desliga manual pode perder o registro.

---

#### m4. Card JS: `_state` getter faz busca linear em todos os estados do HA

**Arquivo**: `light-schedule-card.js:63-76`

```javascript
get _state() {
    const configured = this._hass?.states?.[this._config?.entity];
    if (!configured || Array.isArray(configured.attributes?.lights)) {
        return configured;
    }
    const entryId = configured.attributes?.entry_id;
    if (!entryId) return configured;
    return Object.values(this._hass.states).find(
        (state) => state.attributes?.entry_id === entryId && Array.isArray(state.attributes?.lights)
    ) || configured;
}
```

Se o usuario configurar o card com a entidade `switch.schedule_enabled` ou `binary_sensor.active` (que nao tem `lights`), o fallback faz `Object.values(this._hass.states).find(...)`, que e O(n) sobre todos os estados do HA. Em instalacoes com centenas de entidades, isso e chamado a cada atualizacao de estado.

---

#### m5. Card JS: `_durationBetween` retorna 86400 quando start == end

**Arquivo**: `light-schedule-card.js:906-911`

```javascript
_durationBetween(start, end) {
    const startSeconds = this._timeToSeconds(start);
    const endSeconds = this._timeToSeconds(end);
    if (startSeconds == null || endSeconds == null) return 0;
    return (endSeconds - startSeconds + 86400) % 86400 || 86400;
}
```

Quando start e end sao iguais, `(0 + 86400) % 86400 = 0`, e `0 || 86400 = 86400`. O resultado e 24h. O backend aceita (max e 86400), mas o usuario pode nao perceber que start==end significa "24 horas". O preview mostra "24h" mas nao ha aviso explicito.

---

#### m6. Card JS: `_formatNext` usa timezone do browser quando `hass.config.time_zone` nao esta disponivel

**Arquivo**: `light-schedule-card.js:1021-1024`

```javascript
const timeZone = this._hass?.config?.time_zone;
const dateKey = (value) => value.toLocaleDateString("en-CA", { timeZone, ... });
const prefix = dateKey(date) === dateKey(now) ? "hoje" : ...;
```

Se `this._hass.config` ainda nao foi carregado (startup), `timeZone` e `undefined`, e `toLocaleDateString` usa a timezone do browser. Se o browser esta em uma timezone diferente do HA, "hoje" e "amanha" podem estar errados.

---

#### m7. `_power_mapping` cache nao e invalidado quando `device_class` de um sensor muda

**Arquivo**: `sensor.py:221-227`

```python
@callback
def _handle_watched_state(self, event: Any) -> None:
    if event.data.get("new_state") is None:
        self._cached_power_mapping = None
        self._refresh_state_listener()
    self.async_write_ha_state()
```

O cache so e invalidado quando `new_state is None` (entidade removida). Se o `device_class` de um sensor muda (ex: de `temperature` para `power` apos reconfiguracao), o cache continua com o mapeamento antigo. Edge case improvavel mas possivel.

---

## Falsos positivos percebidos

1. **`_stopping` limpo duas vezes** (`scheduler.py:506` e `537`): O `_stopping = False` aparece no corpo de `_async_stop_sequence` e no `finally` de `async_stop`. Parece redundante, mas o `finally` garante limpeza mesmo se `_async_stop_sequence` lancar excecao. Nao e bug.

2. **`_register_services` chamado em `async_setup` e `async_setup_entry`**: O guard `hass.services.has_service` previne registro duplicado. O padrao e correto e necessario para o caso de `async_setup` nao ser chamado antes de `async_setup_entry` (config_entry_only).

3. **`_resolve` com `entry.runtime_data not in found`**: A verificacao de duplicatas usa `not in` com comparacao por identidade. Como `runtime_data` e uma instancia unica por entry, isso funciona corretamente.

4. **`_build_mappings` com power vazio**: O mapeamento posicional com fallback e intencional e documentado. Sensores de potencia sao opcionais.

5. **`_dispatch` com `blocking=True`**: O blocking e necessario para garantir que o servico foi executado antes da confirmacao. O timeout e herdado do HA (padrao generoso).

6. **Options flow preserva `CONF_SCHEDULES` e `CONF_ENABLED`**: O spread `**self.config_entry.options` no `config_flow.py:216` preserva todos os campos nao editados pelo form. Nao ha perda de dados.

---

## Sugestoes (nao sao bugs)

1. **Teste de DST ambiguo**: O teste `test_ignores_nonexistent_dst_time` esta skipado por falta de tzdata. Considere adicionar `tzdata` como dependencia de teste ou usar `zoneinfo` com fallback.

2. **Cobertura de testes**: Os testes cobrem `next_run` e `confirmation`, mas nao cobrem `config_flow`, `store`, `schedules`, `power`, ou o card JS. Adicionar testes para `_normalize_mappings`, `_build_mappings`, e `_durationBetween` preveniria regressoes.

3. **`_notify` excessivo**: O `_notify` e chamado em muitos pontos (`_schedule_next`, `_save_runtime`, `_async_stop_sequence`, `_on_light_changed`). Considere debouncing para evitar atualizacoes excessivas do card em zonas com muitas luzes.

4. **Card JS**: O `_render` reconstroi todo o DOM a cada mudanca. Para zonas com muitas luzes, considere atualizacao incremental (diffing) para evitar flicker e perda de foco em inputs.

---

## Resumo

| Severidade | Count | IDs |
|-----------|-------|-----|
| Critico | 2 | C1, C2 |
| Medio | 6 | M1, M2, M3, M4, M5, M6 |
| Menor | 7 | m1, m2, m3, m4, m5, m6, m7 |

**Status**: **PRECISA DE ALTERACAO**

Os dois problemas criticos (C1 e C2) sao bugs concretos e reproduziveis que podem corromper o estado da integracao. Os problemas medios (M1-M6) sao edge cases reais com impacto funcional verificavel. Os menores (m1-m7) sao melhorias defensivas e de performance.
