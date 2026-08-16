# REVIEW — light_scheduler (revisão independente/adversarial)

**Data:** 2026-08-14
**Revisor:** opencode-go/deepseek-v4-flash
**Escopo:** `custom_components/light_scheduler/*.py`, `frontend/light-schedule-card.js`, `manifest.json`, `services.yaml`, `strings.json`, `translations/{en,pt-BR}.json`
**Escopo de comportamento verificado contra o core do Home Assistant (branch `dev` em 14/08/2026):** `config_entries.py`, `helpers/event.py`, `components/http/{__init__,server,static}.py`
**Status:** ⛔ **PRECISA DE ALTERAÇÃO** (encontrados 3 itens críticos, 8 médios e 7 menores)

---

## Arquivos revisados

| Arquivo | Linhas | Observação |
|---|---|---|
| `custom_components/light_scheduler/const.py` | 49 | Sem bugs; notar que `MAX_SCHEDULE_DURATION=86400` == `DEFAULT_MAX_DURATION` |
| `custom_components/light_scheduler/__init__.py` | 403 | Bugs médios nos serviços e no unload de frontend |
| `custom_components/light_scheduler/scheduler.py` | 286 | 1 crítico + 3 médios (corrida/colisão de agendamentos, vazamento de listener, deriva do horário de desligar) |
| `custom_components/light_scheduler/sensor.py` | 249 | Performance O(registry) em toda leitura de atributos; side-effect no getter de `native_value` |
| `custom_components/light_scheduler/switch.py` | 27 | OK |
| `custom_components/light_scheduler/binary_sensor.py` | 32 | OK (atributo `history` pesado, menor) |
| `custom_components/light_scheduler/next_run.py` | 34 | Lógica DST gap/fold correta em HA moderno (zoneinfo) |
| `custom_components/light_scheduler/store.py` | 26 | OK; sem flush no unload (menor) |
| `custom_components/light_scheduler/schedules.py` | 28 | `time.isoformat()` produz `"18:30:00"` (com segundos) → vaza para o card |
| `custom_components/light_scheduler/config_flow.py` | 189 | **1 crítico** (default de duração) + 1 médio (re-pareamento posicional) |
| `custom_components/light_scheduler/frontend/light-schedule-card.js` | 900 | Sem XSS (verificado); bugs menores de formato/hora |
| `manifest.json` / `services.yaml` / `strings.json` / `translations/*` | — | OK (strings coerentes com as `_attr_translation_key`) |

---

## 🔴 CRÍTICO

### C1 — `config_flow.py:61-62` — default de duração do formulário inicial é 14.400 **minutos** (deveria ser 240)

```python
raw_duration = int(values.get(CONF_DEFAULT_DURATION, DEFAULT_DEFAULT_DURATION))  # 14400 (segundos)
duration_minutes = max(1, raw_duration // 60) if persisted else max(1, raw_duration)
```

`DEFAULT_DEFAULT_DURATION = 14400` **segundos** (4 h). No primeiro render de `async_step_user(None)` → `persisted=False` → o campo "Duração padrão (min)" (NumberSelector `max=1440`) é exibido com **14400 minutos** em vez de 240.

**Cenário/evidência (reproduzido):**
```
initial config-flow form default (minutes): 14400
expected default (minutes): 240
```
Ao salvar sem editar: `int(user_input[CONF_DEFAULT_DURATION]) * 60` → **864.000 s (10 dias)** persistidos em `options["default_duration"]` (só é contido em runtime pelo clamp `min(…, max_duration)` em `scheduler.py:191`, mas o valor errado fica gravado no `.storage`). Se o frontend bloquear o valor fora do `max=1440`, o usuário fica impedido de criar a zona até mexer no campo — de qualquer forma, o default está errado.

**Sugestão:** tratar a constante sempre como segundos no fluxo inicial:
```python
duration_minutes = max(1, raw_duration // 60)  # em ambos os ramos
```
e usar `persisted` apenas para decidir a origem (options em segundos vs. input cru em minutos já normalizado pelo fluxo).

---

### C2 — `scheduler.py:130-132` — restart do HA no meio de um run deixa as luzes acesas indefinidamente (o "horário de desligar" é perdido)

```python
async def _async_reconcile_after_start(self, _: Event) -> None:
    """Do not infer stale active runs; only make sure upcoming work is armed."""
    self._schedule_next()
```

Após reinício às 19:00 com um run agendado 18:30→22:30:
- `_active=False`, `_unsub_finish=None` → o desligar agendado das 22:30 **não existe mais**;
- `_schedule_next()` arma somente a **próxima** execução (amanhã 18:30) e **nenhuma lógica desliga as luzes** que ficaram acesas antes do restart.

**Consequência:** as luzes permanecem acesas do restart até o fim do próximo run (ex.: ~27 h). O comentário no `store.py:11` ("active execution is always reconstructed safely") não corresponde ao comportamento: nada é reconstruído.

**Sugestão:** persistir o estado do run ativo (`started_at`, `finishes_at`, `source`, `interval`) no `RuntimeStore` a cada `async_turn_on`/`async_stop` e, em `_async_reconcile_after_start`, se `finishes_at > now`, re-armar `_unsub_finish` (sem religar luzes) para garantir o desligar na hora prevista — ou, no mínimo, documentar explicitamente e emitir um aviso/notificação de que as luzes ficarão acesas.

---

### C3 — `scheduler.py:165-176` + `185-188` — horário de LIGAR de um agendamento que colide com um run ativo é **descartado silenciosamente** e seu horário de DESLIGAR também

```python
async def _async_scheduled_start(self, _: datetime) -> None:
    schedule = self._next_schedule
    self._next_run = None
    self._next_schedule = None
    if schedule is not None:
        await self.async_turn_on(...)
    else:
        self._schedule_next()
```
```python
async def async_turn_on(...):
    if self._active:
        # Repeated clicks and overlapping schedules must never invert the current run.
        return
```

Com o novo modelo "horário de ligar **e** de desligar", agendamentos sobrepostos são plausíveis (ex.: A 18:30–22:30, B 19:00–20:00). Se o timer de B disparar durante o run de A: `async_turn_on` retorna, `_unsub_next` foi consumido, **e nenhum código rearma `_schedule_next()`** (o re-arm só ocorre no fim do run, em `async_stop`, ou em updates de options). O agendamento B é pulado no dia, sem `_notify()` e sem log. O "off time" de B (20:00) também é perdido — as luzes seguem o desligar de A (22:30).

**Sugestão:** no ramo de colisão, reagendar em vez de descartar:
```python
async def _async_scheduled_start(self, _):
    schedule = self._next_schedule
    self._next_run = None
    self._next_schedule = None
    if schedule is None:
        self._schedule_next()
        return
    if self._active:
        _LOGGER.info("Skipping %s while a run is active", schedule.get("id"))
        self._schedule_next()   # re-arma a próxima ocorrência
        return
    await self.async_turn_on(...)
```
Adicionalmente, considerar persistir "desligar pendente" por agendamento (ver C2).

---

## 🟠 MÉDIO

### M1 — `__init__.py:369-375` + `scheduler.py:49-68` — campos `target_entity_ids`/`power_entity_ids` de `set_zone_options` são **no-op** quando `entity_mappings` existe

`services.yaml:113-116` expõe `target_entity_ids` e `power_entity_ids`, e o handler grava:

```python
for key in (CONF_DEFAULT_DURATION, CONF_TARGET_ENTITY_IDS, CONF_POWER_ENTITY_IDS):
    if key in data and CONF_ENTITY_MAPPINGS not in data:
        options[key] = data[key]
```

Porém `CONF_ENTITY_MAPPINGS` é sempre gravado pelo config flow (`config_flow.py:122`) e pelo próprio `set_zone_options` com `entity_mappings`, e a propriedade usada em runtime **ignora** as listas cruas:

```python
@property
def target_entity_ids(self):
    mappings = self.options.get(CONF_ENTITY_MAPPINGS, [])
    if mappings:
        return [item["target_entity_id"] for item in mappings ...]   # ← ignora CONF_TARGET_ENTITY_IDS
    return list(self.options.get(CONF_TARGET_ENTITY_IDS, []))
```

Qualquer chamada de serviço/automação que use `target_entity_ids` sem `entity_mappings` altera silenciosamente nada.

**Sugestão:** remover os dois campos do `services.yaml` (e do handler) ou, ao recebê-los sem `entity_mappings`, reconstruir `entity_mappings` via `_build_mappings(targets, powers, existing)` mantendo pareamento por target.

---

### M2 — `config_flow.py:48-55` e `162-171` — options flow reconstrói o pareamento luz↔sensor **posicionalmente**, corrompendo o pareamento explícito

```python
return [
    {"name": ..., "target_entity_id": target,
     "power_entity_id": powers[index] if index < len(powers) else ""}
    for index, target in enumerate(targets)
]
```

`_build_mappings` só preserva o `name` por target; o sensor de potência é reatribuído por **índice**. Se o usuário reordenar as luzes no multi-select (ou adicionar/remover uma luz no meio), os pares luz↔sensor mudam silenciosamente — exatamente o recurso "pareamento explícito luz↔sensor" que a feature `279f504` introduziu. Sensores descobertos automaticamente (que não estão em `power_entity_ids`) também são descartados do form e perdem o vínculo após salvar.

**Sugestão:** em `_build_mappings`, preservar também `power_entity_id` do `existing_by_target[target]` quando o usuário não mudou a seleção (ex.: manter o par anterior e só usar `powers[index]` para targets novos/sem par anterior).

---

### M3 — `scheduler.py:232-264` — sequência de DESLIGAR com stagger termina **depois** do horário agendado

```python
async def async_stop(self, interval=None):
    ...
    for index, entity_id in enumerate(targets):
        await ... turn_off ...
        if interval and index < len(targets) - 1:
            await asyncio.sleep(interval)      # ← sem checagem de "remaining"
```

O timer `_async_finish` dispara em `_finishes_at` (hora exata de apagar), mas o rampeamento de desligar leva `interval × (n-1)` segundos adicionais — a última luz apaga `n-1 × interval` depois do horário previsto. O próprio `async_turn_on` tem proteção (`scheduler.py:224-227`: `remaining = (self._finishes_at - utcnow())…; if remaining <= 0: break`), mas `async_stop` não tem equivalente. O comentário "Arm the exact finish time … keeps the requested off time stable" (`scheduler.py:207-208`) é contradito pelo comportamento.

**Sugestão:** em `async_stop`, antes de dormir entre luzes, verificar `remaining` e abortar/compactar o sono (`sleep(min(interval, remaining))`) como no `turn_on`.

---

### M4 — `scheduler.py:121` e `125-128` — vazamento do listener `EVENT_HOMEASSISTANT_STARTED` (memória em reloads)

```python
self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._async_reconcile_after_start)
...
async def async_unload(self) -> None:
    for unsub in (self._unsub_next, self._unsub_finish, self._unsub_states):
        if unsub:
            unsub()
```

O retorno de `async_listen_once` não é guardado nem removido em `async_unload`. Se o HA já iniciou (caso normal de reload/editar zona), o evento nunca mais dispara e o listener fica registrado **para sempre**, segurando referência ao `LightScheduler` (e ao histórico) — a cada reload, mais um listener morto. Além disso, em reload `_async_reconcile_after_start` nunca roda (irrelevante, pois `async_setup` já chama `_schedule_next()`, mas o vazamento permanece).

**Sugestão:** guardar o unsub (`self._unsub_started = self.hass.bus.async_listen_once(...)`) e chamá-lo em `async_unload`; ou trocar por `async_when_setup_or_start`/checar `hass.is_running` antes de registrar.

---

### M5 — `sensor.py:60-124` + `211-231` — varredura O(registry) em toda leitura de atributos (2× por sinal)

`_power_mapping()` itera `registry.entities.values()` + `hass.states.get()` para **todo sensor do HA** e é chamado:
1. dentro de `extra_state_attributes` (toda escrita de estado da entidade);
2. dentro de `_refresh_state_listener()` (toda notificação do dispatcher).

Como `_on_light_changed` → `_notify()` roda a cada toggle de luz (fora de run), cada toggle gera 2 varreduras completas do registry. Em instalações com centenas/milhares de entidades isso trava o event loop periodicamente.

**Sugestão:** cachear `power_mapping` e invalidá-lo somente quando options mudarem (ou quando um `ENTITY_REGISTRY_UPDATED`/`STATE_CHANGED` do próprio sensor-alvo ocorrer), em vez de recalcular em toda escrita de estado.

---

### M6 — `scheduler.py:158-163` + `sensor.py:47-50` — getter com efeito colateral: `native_value` sobrescreve `_next_schedule` entre o armamento e o disparo do timer

```python
@property
def next_run(self):
    self._next_run, self._next_schedule = find_next_run(...)   # side effect
    return self._next_run
```

`LightScheduleStatus.native_value` chama `self.scheduler.next_run` a cada escrita de estado. Se uma leitura do sensor ocorrer **após** o instante armado (ex.: 18:30:00.1) mas **antes** do callback do timer rodar, `find_next_run` retorna a ocorrência seguinte (amanhã) e sobrescreve `_next_schedule` — e o timer ainda dispara agora com o agendamento errado (duração/intervalo do dia seguinte executados no horário de hoje). Janela sub-segundo, mas o design (propriedade com efeito colateral + leitor externo) torna a corrida possível.

**Sugestão:** `next_run` não deve mutar estado interno; computar em `_schedule_next`/`_async_scheduled_start` e armazenar localmente; o sensor lê `self.scheduler._next_run` sem recomputar.

---

### M7 — `__init__.py:354-376` — validação insuficiente em `set_zone_options` (e campos expostos sem validação)

- `default_duration` é gravado cru (`options[key] = data[key]`). Um valor não numérico (`"abc"`) quebra `scheduler.async_turn_on` com `ValueError` não tratado em `int(self.options.get(CONF_DEFAULT_DURATION, 14400))` (`scheduler.py:190`); valores > `max_duration` ou negativos ficam gravados errado no `.storage`.
- `target_entity_ids`/`power_entity_ids` aceitam qualquer string sem checar domínio `light.`/`switch.`/`sensor.` (diferente de `_normalize_mappings`, que valida).
- Nada limita `entity_mappings` a um número razoável de entradas.

**Sugestão:** validar com schema análogo ao config flow:
```python
vol.Optional(CONF_DEFAULT_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_SCHEDULE_DURATION)),
vol.Optional(CONF_TARGET_ENTITY_IDS): cv.ensure_list([vol.All(cv.string, vol.Any(vol.Match(r"^light\."), vol.Match(r"^switch\.")))]),
```

---

### M8 — `__init__.py:264-352` — `vol.Invalid` não tratado em `add_schedule`/`update_schedule`/`set_schedules` (inconsistente com `turn_on`)

`turn_on` converte `vol.Invalid` → `ServiceValidationError` (`__init__.py:250-256`), mas `SCHEDULE_SCHEMA(...)` em `add` (`:265`), `set_schedules` (`:302-304`) e `update` (`:331-339`) deixa `vol.Invalid` escapar como exceção genérica → o chamador recebe "Unknown error"/erro 500 no log em vez de uma mensagem de validação clara.

**Sugestão:** envolver todas as validações de schema em um helper comum que converta `vol.Invalid` em `ServiceValidationError`.

---

## 🟡 MENOR

### m1 — `schedules.py:16` + card `light-schedule-card.js:210` — horário exibido com segundos: `"18:30:00 → 22:30"`

`serialize_schedule` grava `time.isoformat()` → `"18:30:00"` (confirmado: `time(18,30).isoformat() == "18:30:00"`). O card exibe `schedule.time` cru em `_scheduleRow` (`start = schedule.time || …`), então o horário de início aparece com segundos e o fim (derivado por `_scheduleEnd`) sem — formato inconsistente.
**Sugestão:** normalizar no backend (`isoformat(timespec="minutes")`) ou no card (`start.slice(0,5)` / `_timeToMinutes`+`_minutesToTime`).

### m2 — card `_formatNext` (`light-schedule-card.js:656-669`) — entidade configurada = `switch.*` mostra "Próxima ação: on"

Se `entity` do card for `switch.<zona>_schedule_enabled` e o fallback por `entry_id` (`_state`, linhas 60-64) não encontrar o sensor (ainda não carregado), `sensorState` = "on"/"off" → `new Date("on")` → NaN → `return String(next)` → exibe "Próxima ação: on".
**Sugestão:** validar `next` com regex de ISO/`Number.isNaN(date.getTime())` e não usar o state do switch.

### m3 — `__init__.py:181-183` — `_unregister_frontend` não remove o static path registrado

`_register_frontend` registra `StaticPathConfig(CARD_JS_URL, …)` (`:174-176`), mas no unload apenas `remove_extra_js_url` é chamado. Em reload, `async_register_static_paths` registra rota/`StaticResource` duplicados (o core dev não faz dedupe em `http/server.py:_async_register_static_paths`).
**Sugestão:** chamar `await hass.http.async_remove_static_paths`/remover o recurso no `_unregister_frontend` (tornando-o `async`), ou manter o static path registrado de forma idempotente (guardar se já registrado).

### m4 — `scheduler.py:266-270` + `143-146` — histórico externo nunca fecha e `_save_history` re-parsa tudo 2× por item

`_record_external` grava `{"finished_at": None, "duration": None}` e **nada** registra o desligamento da luz externa; além disso `_save_history` chama `dt_util.parse_datetime(item["started_at"])` duas vezes por item (e mantém itens com `started_at` falsy para sempre).
**Sugestão:** rastrear entradas abertas por `entity_id` e fechá-las no evento de OFF; parsear uma única vez por item.

### m5 — card não permite desabilitar um agendamento individual

O backend suporta `enabled` por schedule (`update_schedule`, `SCHEDULE_SCHEMA`), mas o dialog do card não expõe o toggle — o usuário só consegue desabilitar via serviço/YAML.
**Sugestão:** adicionar checkbox/switch "Ativo" no dialog de agendamento e enviar `enabled` em `update_schedule`.

### m6 — `sensor.py:96-124` — sensor de potência configurado que foi excluído fica "preso" no mapeamento

Se `configured.get(target_id)` retorna um entity_id que não existe mais, `_power_mapping` o mantém (`mapping[target_id] = selected`) sem tentar re-descoberta → o card mostra "—" permanentemente.
**Sugestão:** se `hass.states.get(selected)` for None/`unavailable` e houver candidato no mesmo device, fazer fallback.

### m7 — `config_flow.py:61-62`/`171` — arredondamento para minutos perde segundos (90 s → 60 s) no options flow

`raw_duration // 60` seguido de `* 60` no save (`:171`) degrada durações que não são múltiplas de 60 (e valores persistidos > 1440 min ficam fora do range do selector). Ver também C1.
**Sugestão:** usar `round(raw_duration / 60)` e permitir `max=1440`; documentar que a precisão do options flow é de 1 min.

---

## ✅ Falsos positivos percebidos (verificados e descartados)

1. **XSS no card** — não confirmado. `_escape()` (linhas 712-719) cobre todas as interpolações de dados do usuário (nomes, entity_ids, títulos, aria-labels, `option value`, mensagens de erro via `textContent`). Padrão `innerHTML` com dados não sanitizados não foi encontrado.
2. **`scheduler.entry.options` obsoleto após `async_update_entry`** — não confirmado. No core atual (`config_entries.py:_async_update_entry`), `options` é atualizado **in-place** (`object.__setattr__(entry, "options", MappingProxyType(options))`) e os update listeners são agendados como tasks; `runtime_data`/listeners permanecem válidos.
3. **`async_track_state_change_event` com lista vazia** — não confirmado. O core retorna um listener no-op (`helpers/event.py:_remove_empty_listener`).
4. **`async_track_point_in_time` com datetime aware em UTC** — não confirmado. O core converte via `dt_util.as_utc` (`helpers/event.py:async_track_point_in_time`).
5. **Corrida read-modify-write nos serviços de agenda (`add`/`remove`/`update`/`set_schedules`/`set_zone_options`)** — não confirmado. Não há `await` entre a leitura de `scheduler.options` e `async_update_entry` (que é síncrono na mutação), logo a operação é atômica dentro do event loop. Deixar de fora do relatório como bug (residual apenas se um futuro `await` for inserido no meio).
6. **DST em `find_next_run`** — não confirmado. `_exists()` (round-trip `astimezone`) descarta corretamente horários inexistentes (gap de spring-forward) e lida com fold no fall-back em HA moderno (zoneinfo). Ressalva de portabilidade: em HA antigo (pytz), `datetime.combine(..., tzinfo=pytz_tz)` aplicaria offset errado; o core atual usa `ZoneInfo`.

---

## Sugestões gerais

1. Introduzir um campo explícito de "off time" (ou persistir `finishes_at`) no agendamento, em vez de derivar só `duration`, eliminando os bugs C2/C3 e o drift de M3.
2. Mover a lógica de "próximo run" para um único lugar que armazene o resultado (sem getter com side effect) — corrige M6.
3. Adicionar testes unitários para `find_next_run` (boundary de meia-noite, DST, `enabled`) e para `_build_mappings` (reordenação de luzes preserva pares).
4. Em `_resolve`, incluir o próprio `entry_id` na resposta de erro quando `entry_id` for passado mas inválido, para diagnóstico melhor.

---

## Comandos executados

```
git log --oneline -15; git status
git show --stat 72ae2d2; git diff 72ae2d2..c98a13d --stat
python -m py_compile custom_components/light_scheduler/*.py      → OK (10 arquivos)
node --check custom_components/light_scheduler/frontend/light-schedule-card.js → OK
python -c "… time(18,30).isoformat() …"                          → '18:30:00' (confirma m1)
node -e "… _durationBetween …"                                   → 18:30→22:30 = 14400 s (OK)
python (teste find_next_run com tz fixo)                         → OK (next run, pós-horário, disabled)
python (simulação _schema duration)                              → 14400 min vs. 240 esperado (confirma C1)
webfetch core: config_entries.py, helpers/event.py,
              components/http/{__init__,server,static}.py        → verificação de APIs (F1-F5)
```

---

## Status

⛔ **PRECISA DE ALTERAÇÃO** — recomenda-se corrigir C1-C3 antes do próximo release; M1-M8 em seguida.
