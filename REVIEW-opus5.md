# REVIEW-opus5.md — revisão completa do `light_scheduler`

**Status:** ✅ **CORRIGIDO** — os 2 críticos, 9 médios e 12 menores foram fechados na
versão 0.8.0. O texto abaixo é o laudo original, mantido como registro do que estava
quebrado e por quê; a cobertura de regressão vive em `tests/test_review_fixes.py`.

Revisão linha a linha de todo o componente (não só do diff): `const.py`, `store.py`,
`schedules.py`, `power.py`, `next_run.py`, `scheduler.py`, `__init__.py`, `sensor.py`,
`switch.py`, `binary_sensor.py`, `config_flow.py`, `frontend/light-schedule-card.js`,
`manifest.json`, `services.yaml`, `strings.json` e as duas traduções.

Os dois críticos foram **reproduzidos** com um script contra `tests/ha_stubs.py`, não
apenas deduzidos da leitura.

---

## 🔴 CRÍTICO

### C1 — um agendamento que dispara durante o desligamento em sequência é descartado em silêncio

- **Arquivo:** `scheduler.py:305-323` (`_async_scheduled_start`)
- **Problema:** o desligamento em sequência mantém `_active = True` e `_stopping = True`
  do começo ao fim. Quando um agendamento dispara nessa janela:
  - o ramo `if self._active:` impede `async_turn_on`;
  - a condição `if not self._stopping and ...` impede a extensão do horário de desligar;
  - `_async_extend_targets()` retorna na primeira linha porque `_stopping` é `True`.

  O resultado é `_schedule_next(); return` — nenhuma luz acende, nenhum horário muda,
  **nenhum log é emitido**. A janela não é teórica: com `interval = 300` e 5 luzes, a
  sequência de desligamento dura 20 minutos, mais até 30 s de confirmação.
- **Reprodução:**

  ```
  2) calls during stop  : []
  2) finishes_at moved  : 0:00:10   (inalterado — o agendamento de 2h sumiu)
  ```

- **Correção concreta:** guardar o agendamento pendente e reavaliá-lo ao final de
  `_async_stop_sequence` (antes de `_schedule_next()`), ou, no mínimo, emitir um
  `_LOGGER.warning` e um aviso na zona dizendo que o horário foi perdido. Silêncio é o
  pior resultado possível aqui.

### C2 — remover uma luz da zona faz agendamentos restritos a ela pararem de rodar, sem aviso

- **Arquivos:** `config_flow.py:216-228`, `__init__.py:510-589` (`set_options`),
  `scheduler.py:333-345` (`_selected_targets`), `scheduler.py:514-517`
- **Problema:** o `target_entity_ids` de cada agendamento é validado contra a zona **só na
  hora de criar ou editar o agendamento** (`_assert_targets_in_zone`). Nenhum dos dois
  caminhos que alteram as luzes da zona — o options flow e `set_zone_options` — faz a poda
  das seleções já persistidas. Depois disso `_selected_targets()` devolve `[]` e
  `async_turn_on` aborta.

  Pior: uma seleção vazia significa "a zona inteira" no modelo de dados, mas uma seleção
  **obsoleta** significa "nada". Os dois estados são indistinguíveis no card, que continua
  exibindo o agendamento como "Agendada".
- **Reprodução:** zona `[light.a]`, agendamento restrito a `light.b` (removida):

  ```
  1) selected targets   : []
  1) service calls made : []
  1) run active         : False
  log: No available lights in Sala
  ```

- **Correção concreta:** ao gravar novas entidades da zona, podar `target_entity_ids` de
  todos os agendamentos. Se a poda esvaziar a lista, **não** cair no comportamento "zona
  inteira" — isso alargaria o agendamento sem o usuário pedir. Desabilitar o agendamento e
  registrar um aviso visível no card.
- **Nota:** o log `"No available lights in %s"` (`scheduler.py:516`) é enganoso — aparece
  tanto quando a zona realmente não tem luz disponível quanto quando a seleção está
  obsoleta. Duas causas distintas, mesma mensagem.

---

## 🟠 MÉDIO

### M1 — atributos grandes gravados no recorder a cada leitura de potência

- **Arquivos:** `sensor.py:129-196`, `binary_sensor.py:22-32`
- **Problema:** nenhuma das entidades define `_unrecorded_attributes`. O sensor publica
  `schedules`, `lights`, `entity_mappings`, `target_entity_ids` e `power_entity_ids`; o
  binary sensor publica `history` (até 200 registros). O sensor reescreve o estado a cada
  mudança de qualquer sensor de potência vigiado (`sensor.py:204-227`) — em uma tomada
  inteligente, isso é a cada poucos segundos. Como `power_w` muda junto, o recorder grava
  uma linha de atributos nova a cada vez.
- **Correção concreta:** `_unrecorded_attributes = frozenset({"schedules", "lights",
  "entity_mappings", "target_entity_ids", "power_entity_ids"})` no sensor e
  `frozenset({"history"})` no binary sensor.

### M2 — `turn_on_now` e `stop` bloqueiam o chamador por minutos

- **Arquivos:** `__init__.py:380-396`, `scheduler.py:531-557`, `scheduler.py:605-613`
- **Problema:** o handler do serviço faz `await scheduler.async_turn_on(...)` e a rampa
  inteira roda dentro dessa chamada: `interval` segundos entre cada luz mais até duas
  janelas de 15 s de confirmação. Com `interval = 300` e 5 luzes, `light_scheduler.stop`
  só retorna depois de ~20 minutos. Qualquer automação ou script que chame o serviço fica
  preso, e `async_unload()` (reload da integração, remoção da zona) espera o mesmo tempo.
- **Correção concreta:** disparar a rampa e a sequência de desligamento como tarefas de
  fundo (`_create_background_task` já existe) e retornar do serviço assim que o run estiver
  registrado; limitar explicitamente o caminho de unload.

### M3 — o nome de exibição da luz vira "nome personalizado" ao salvar pelo card

- **Arquivos:** `sensor.py:146,152,169-176`, `frontend/light-schedule-card.js:367-371,745-773`
- **Problema:** o atributo `entity_mappings[].name` publicado pelo sensor já vem
  **resolvido** (`custom_name or target.name`). O card preenche o campo "Nome" com esse
  valor e `_saveZone()` manda de volta o que está no campo. Basta o usuário abrir a
  engrenagem por qualquer motivo e clicar em Salvar para o `friendly_name` atual virar um
  nome personalizado fixo — renomear a luz no HA depois não tem mais efeito.
- **Correção concreta:** publicar o nome configurado cru em uma chave separada
  (`custom_name`) e usar o `friendly_name` só como `placeholder` do input, nunca como
  `value`.

### M4 — o sensor de potência descoberto automaticamente vira seleção explícita ao salvar

- **Arquivos:** `sensor.py:52-123,169-176`, `frontend/light-schedule-card.js:376-377`
- **Problema:** mesma mecânica de M3. `entity_mappings[].power_entity_id` no atributo é o
  resultado de `_power_mapping()`, que inclui a descoberta automática por dispositivo. O
  card mostra isso como se o usuário tivesse escolhido, e Salvar congela a descoberta. Se
  o sensor for substituído no dispositivo mais tarde, a descoberta não se aplica mais.
- **Correção concreta:** separar "configurado" de "resolvido" no payload do sensor, como em
  M3, e deixar o autocomplete no estado "Automático / nenhum" quando não houver escolha
  explícita.

### M5 — varredura do registro inteiro de entidades a cada sinal da zona

- **Arquivos:** `sensor.py:60-123`, `sensor.py:198-202`
- **Problema:** `_power_mapping()` itera `registry.entities.values()` e chama
  `hass.states.get` para cada sensor da instalação. O cache é invalidado em
  `_handle_update`, que roda em **todo** `SIGNAL_UPDATE` — e `_notify()` é chamado a cada
  mudança de luz enquanto a zona está ociosa, a cada `_save_runtime`, a cada
  `_schedule_next`. Em uma instalação com milhares de entidades e várias zonas, é uma
  varredura O(entidades) por sinal, por zona.
- **Correção concreta:** montar o índice dispositivo→sensor de potência uma vez e
  invalidá-lo por evento de registro (`EVENT_ENTITY_REGISTRY_UPDATED`), não por sinal da
  própria zona.

### M6 — mutações concorrentes de opções perdem alterações *(aberto desde REVIEW-luna M2)*

- **Arquivo:** `__init__.py:403-589`
- **Problema:** todo handler faz leitura-modificação-escrita sobre `scheduler.options` e
  chama `async_update_entry`. Duas ações do card no mesmo instante — alternar dois
  agendamentos, por exemplo — leem a mesma lista e a segunda apaga a primeira.
- **Correção concreta:** um `asyncio.Lock` por entry, relendo as opções dentro do lock.

### M7 — horário ambíguo no fim do DST sem política explícita *(aberto desde REVIEW-luna M3)*

- **Arquivo:** `next_run.py:14-17`
- **Problema:** `_exists()` só distingue existe / não existe. Um horário repetido no retorno
  do horário de verão sempre usa `fold=0`, sem o usuário poder escolher e sem aviso.

### M8 — o `unique_id` da entrada congela na lista de luzes inicial

- **Arquivo:** `config_flow.py:164-165`
- **Problema:** o `unique_id` é `"|".join(sorted(targets))` no momento da criação, e o
  options flow (`config_flow.py:216-237`) nunca o atualiza. Consequências: criar uma zona
  nova com uma luz que a zona A já **não controla mais** é rejeitado como
  `already_configured`; e duas zonas podem acabar controlando exatamente as mesmas luzes
  sem que a proteção perceba.
- **Correção concreta:** ou atualizar o `unique_id` junto com as entidades, ou abandoná-lo
  como chave de unicidade e usar apenas o nome da zona.

### M9 — limiar de confirmação de potência fixo em 1 W, e por-luz

- **Arquivos:** `const.py:31`, `scheduler.py:405-421`
- **Problema:** `POWER_CONFIRM_THRESHOLD_W = 1.0` é global. Duas situações reais falham
  sempre: uma fita LED de menos de 1 W nunca confirma o `turn_on`; um medidor compartilhado
  por várias luzes nunca confirma o `turn_off` de uma delas. Cada falha custa um reenvio do
  comando mais duas janelas de 15 s **em todo run**, e marca um aviso permanente na zona.
- **Correção concreta:** limiar por entrada no `entity_mappings` (padrão 1 W), e ignorar a
  confirmação por potência quando o mesmo sensor estiver pareado a mais de um alvo.

---

## 🟡 MENOR

### Código morto

- **m1** `scheduler.py:17-19` — `CONF_SCHEDULE_TIME` e `CONF_SCHEDULE_DAYS` são importados
  e nunca usados (confirmado por varredura AST).
- **m2** `const.py:21` — `DEFAULT_ENABLED` nunca é referenciado em lugar nenhum.
- **m3** `light-schedule-card.js:930-938` — `_timeToMinutes()` e `_minutesToTime()` são
  definidos e nunca chamados.
- **m4** o card ainda carrega 9 fallbacks para atributos que o backend nunca emite:
  `attrs.on_count` (l96), `attrs.total_lights` (l97), `attrs.active_source` (l167),
  `attrs.active_start` (l169, l991), `attrs.active_end` (l978, l990, l1011) e
  `attrs.next_run` (l1014). São nomes de uma versão anterior do sensor.
- **m5** `config_flow.py:97-111` — o ramo `DEFAULT_DEFAULT_DURATION if ... supplied_values`
  é inalcançável: o campo é `vol.Required`, então `values` sempre tem a chave quando o
  formulário é reexibido após erro.

### Defeitos pequenos

- **m6** `light-schedule-card.js:18-20` — `getStubConfig()` devolve
  `sensor.light_scheduler`, que nunca existe. Com `preview: true` (l1298), o card aparece
  no seletor de cards mostrando "Entidade não encontrada".
- **m7** `light-schedule-card.js:269-270,913-918` *(aberto desde REVIEW-luna N1)* —
  `<input type="time">` sem `step` recusa `HH:MM:SS`. Um agendamento criado via serviço com
  duração que não é múltiplo de minuto não pode ser editado pelo card: o campo fica vazio e
  `reportValidity()` bloqueia o Salvar.
- **m8** `strings.json` e as duas traduções não têm `config.abort.already_configured`. Ao
  tentar recriar uma zona já existente, o usuário vê a chave crua.
- **m9** `__init__.py:368-372` — `_update_options` é `async` mas não tem nada assíncrono
  dentro; são 5 `await` desnecessários.
- **m10** `__init__.py:403-417` — quando o serviço resolve várias zonas, o **mesmo objeto**
  `schedule` é anexado às opções de todas elas. Aliasing entre entradas distintas.
- **m11** `sensor.py:166,177` — usa as strings cruas `"schedules"`, `"default_duration"` e
  `"entity_mappings"` em vez das constantes `CONF_*` usadas no resto do componente.
- **m12** `__init__.py:267-274` — `async_unload_entry` descarrega o scheduler (e desliga as
  luzes) **antes** de saber se `async_unload_platforms` teve sucesso. Se o unload das
  plataformas falhar, a entrada continua carregada com o scheduler já morto.

---

## ✅ Falsos positivos verificados e descartados

- **`_on_light_changed` não notifica durante um run ativo** (`scheduler.py:221-223`): não é
  problema — o sensor mantém o próprio listener de estado (`sensor.py:204-219`) e continua
  atualizando as luzes e a potência independentemente do dispatcher.
- **Cancelar `_ramp_task` cancelaria o task do serviço**: o guard
  `ramp_task is not current_task` (`scheduler.py:596`) cobre o caso, e `turn_on` para várias
  zonas é sequencial — nunca há duas rampas da mesma zona sobrepostas.
- **Deadlock em `async_stop` reentrante**: `stop_task is not asyncio.current_task()`
  (`scheduler.py:575`) evita o auto-await.
- **`store.py` perderia escritas entre zonas**: o `asyncio.Lock` e o `_data` compartilhado
  resolvem; `async_save` do HA cancela o delay pendente.
- **Injeção de HTML no card**: todo valor interpolado passa por `_escape()`; os únicos
  trechos sem escape são literais do próprio código.
- **Options flow apagaria as opções**: `async_create_entry(data=options)` monta a partir de
  `self.config_entry.options`; o comentário em `config_flow.py:229-231` está correto.
- **IDs duplicados e aplicação parcial em multi-zona**: fechados no commit `56667dc`; a
  validação anterior ao apply está correta.

---

## Comandos executados

- `python -m unittest discover -s tests -v` — **OK**, 32 testes, 1 pulado
  (`test_ignores_nonexistent_dst_time`, `tzdata` ausente neste Windows).
- `python -m compileall -q custom_components/light_scheduler` — **OK**.
- `node --check custom_components/light_scheduler/frontend/light-schedule-card.js` — **OK**.
- Varredura AST de imports não utilizados em todos os módulos Python.
- Script de reprodução dos dois críticos contra `tests/ha_stubs.py`.

## Cobertura sugerida

- Agendamento que dispara com `_stopping = True` (C1).
- Remoção de uma luz da zona com agendamento restrito a ela, pelos dois caminhos — options
  flow e `set_zone_options` (C2).
- `turn_on_now` com `interval` alto: o serviço deve retornar antes do fim da rampa (M2).
- Ida e volta do diálogo da zona: abrir e salvar sem alterar nada não pode mudar
  `entity_mappings` (M3, M4).
- Confirmação com sensor de potência abaixo do limiar e com medidor compartilhado (M9).

## Status

**CORRIGIDO na 0.8.0** — 52 testes, sem pulados.
