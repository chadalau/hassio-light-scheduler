# Light Scheduler — Plano

Uma segunda integração, irmã da `irrigation_scheduler`, aplicando o mesmo motor de
agendamento e a mesma linguagem visual do card à iluminação — sem reservatório, sem pH,
com uma luz (ou grupo de luzes) no lugar da válvula.

**Decisões já fechadas:**

- Só liga/desliga por horário — sem brilho, sem cor.
- Ativação externa (interruptor físico, app do fabricante, outra automação) só registra
  no histórico; nunca desliga a luz sozinha.
- Uma zona aceita **várias** luzes (lista de entidades, não uma só).

**Legenda usada na matriz abaixo:** reuso = migra quase sem mudança da água · adapta =
muda ou é novo, específico de luz · decisão em aberto = confirmar antes de implementar.

---

## 1. O que muda e o que fica igual

A arquitetura da água já resolveu os problemas difíceis — token de geração por execução,
confirmação de atuação com tolerância, retry de desligamento com confirmação, recuperação
após restart, badge de aviso por horário. Nada disso é específico de água; a maior parte
migra praticamente sem alteração.

| Arquivo | O que muda | Tratamento |
|---|---|---|
| `next_run.py` | Nada — já é puro e não sabe o que está agendando. | reuso |
| `store.py` | `RuntimeStore`, `run_uid` idempotente, validação estrutural do payload — mesmo formato. | reuso |
| `scheduler.py` | Mantém o ciclo de vida da execução (start/finish, grace de atuação, retry de desligamento). Remove pH/EC/reservatório inteiro. Alvo vira uma *lista* de entidades. Ativação externa vira só um evento de log, sem timer. | adapta |
| `config_flow.py` | Seletor de alvo vira multi-seleção de luzes. Remove os campos de pH/reservatório. Mantém `_safe_int`/`_safe_float`. | adapta |
| `__init__.py` | Mesmos 3 tipos de entidade e o mesmo padrão de serviços, sem os campos de pH/EC/reservatório. | adapta |
| `frontend/utils.ts` | Formatação de hora/dia/duração e `scheduleStatusToday` são genéricos — não sabem que existe água. | reuso |
| `frontend/card.ts` | Cabeçalho perde os badges de pH/EC/reservatório; ganha um seletor de várias luzes no painel de configurações. Estrutura de horários e diálogo de histórico ficam iguais. | adapta |
| pH gate, reservatório, EC | Sem equivalente em iluminação — a feature inteira sai. | remove |
| Suporte a lista de alvos | Água sempre teve um alvo só; luz precisa de N. Toca `scheduler.py`, `config_flow.py` e o card. | novo |

---

## 2. Modelo de dados

Mesmo formato de três entidades por zona; troca só o vocabulário.

### `switch.<zona>_schedule_enabled`
Liga/desliga o agendamento inteiro da zona — idêntico à água.

### `sensor.<zona>_next_run`
- `schedules`
- `target_entity_ids` *(lista, não singular)*
- `default_duration`
- `schedule_warnings`

### `binary_sensor.<zona>_active`
- `started_at` / `finishes_at` / `duration`
- `source`: `schedule` · `manual`
- `last_run` / `history` (30 dias)
- Reflete só execuções **nossas** — ativação externa não aparece aqui, só no histórico.

### Serviços
- `turn_on_now`
- `stop`
- `add_schedule` / `update_schedule` / `remove_schedule` / `set_schedules`
- `set_zone_options` — `default_duration`, `target_entity_ids`

---

## 3. Mockups do card

Mesma casca visual do card de água — cabeçalho, badge de status, lista de horários com
indicador de dia e ícone de resultado, botões circulares de ação — só sem os badges de
reservatório/pH que não fazem sentido aqui.

Versão visual completa (HTML, fiel ao CSS real do card):
https://claude.ai/code/artifact/6f2efcd8-52d9-4f63-8a19-5812dd74a077

### Estado padrão (idle, próxima execução hoje)

```
┌─────────────────────────────────────────┐
│ Sala                    [Agendada] ⏻ ⚙  │
│ 💡 2 de 3 luzes ligadas agora            │
│ Próximo: hoje às 18:30                   │
│ Última: Hoje 06:30 · agendada · 3h       │
├─────────────────────────────────────────┤
│ ⏻ 06:30  S T Q Q S S D   ✓   3h·3 luzes  ✎ 🗑│
│ ⏻ 18:30  S T Q Q S S D   🕐  4h·3 luzes  ✎ 🗑│
│ ⏻ 23:00  S · · · · · ·   ⚠  1h·3 luzes  ✎ 🗑│
│                                           │
│        (+)                    (▶)        │
└─────────────────────────────────────────┘
```

Sem badge de pH/EC/reservatório no cabeçalho — só um chip discreto de "N de M luzes
ligadas agora", que é a única leitura ao vivo que faz sentido pra um grupo de luzes. O
ícone de resultado por horário (✓ concluído hoje, 🕐 ainda vai rodar, ⚠ aviso) é
exatamente o `scheduleStatusToday` que já existe, sem mudar nada.

### Agendamento ativo agora (barra ao vivo)

```
┌─────────────────────────────────────────┐
│ Sala                       [Ligada] ⏻ ⚙  │
│ 💡 3 de 3 luzes ligadas agora            │
├─────────────────────────────────────────┤
│ 💡 Ligada                      2:34:10   │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░            │
│                            [■ Desligar]  │
├─────────────────────────────────────────┤
│ ⏻ 18:30  S T Q Q S S D       4h·3 luzes ✎ 🗑│
│                                           │
│        (+)                    (▶ apagado)│
└─────────────────────────────────────────┘
```

Mesmo padrão da barra "Regando · ativada no dispositivo" da água — só aparece pra
execuções que a própria integração está gerenciando (agendada ou "ligar agora"), com
contagem regressiva e botão Desligar. Uma ativação externa (interruptor físico) não
entra aqui — só no histórico.

### Configurações — seletor de luzes

```
┌─────────────────────────────────────────┐
│ Sala                    [Agendada] ⏻ ⚙  │
├─────────────────────────────────────────┤
│ Duração padrão (min)                     │
│ [ 180                                 ]  │
│                                           │
│ Luzes controladas por esta zona          │
│ (💡 Luminária sofá ✕) (💡 Spot estante ✕) │
│ (💡 Pendente mesa ✕)  (+ adicionar luz)   │
│                                           │
│                   [ Fechar ]  [ Salvar ]  │
└─────────────────────────────────────────┘
```

No lugar dos campos de vazão/vasos/reservatório/pH: um seletor de várias luzes com chips
removíveis, igual ao `entity: {domain: light}` do editor visual — essa é a peça
genuinamente nova da UI.

---

## 4. Decisões em aberto

Chutes razoáveis que tomei pra fechar o mockup — confirme ou corrija antes de eu começar
a implementar.

1. **Nome e domínio.** `light_scheduler` / "Light Scheduler" / card `light-schedule-card`
   — mesmo padrão de nomes da água. Serve?
2. **Confirmação de atuação em grupo.** Se a zona tem 3 luzes e só 2 confirmam ligar,
   isso dispara o aviso "⚠ não confirmou" (exige TODAS) ou passa como sucesso parcial
   (basta UMA)? Sugiro exigir todas — mais fail-safe, mesma filosofia da água.
3. **Ativação externa: fica só no histórico ou aparece em algum lugar ao vivo?** A
   decisão já fechada foi "nunca desliga sozinha"; falta decidir se a barra ao vivo do
   card também deve refletir "alguém ligou pelo interruptor agora" (informativo, sem
   controle) ou se fica mesmo restrito ao diálogo de histórico.
4. **Onde mora o código.** Proponho `custom_components/light_scheduler/` neste mesmo
   repositório, ao lado do `irrigation_scheduler` — fácil de separar num repo HACS
   próprio depois, se quiser.

---

Plano preparado a partir da arquitetura já existente em `custom_components/irrigation_scheduler`.
Nenhum código foi escrito ainda — este documento e os mockups são a base pra alinhar o
desenho antes da implementação.
