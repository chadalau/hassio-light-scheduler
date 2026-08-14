# Light Scheduler

Integração customizada para Home Assistant que liga e desliga uma sala inteira por horários. Cada zona aceita várias entidades `light` e sensores de potência das tomadas inteligentes, permitindo ver o estado e o consumo de cada lâmpada no card Lovelace.

## Instalação

1. Copie `custom_components/light_scheduler` para a pasta `config/custom_components/` da sua instalação Home Assistant.
2. Reinicie o Home Assistant.
3. Em **Configurações → Dispositivos e serviços**, adicione **Light Scheduler**.
4. Selecione as luzes da sala e, na mesma ordem, os sensores de potência das tomadas. Um sensor de potência é opcional; se faltar, o card exibirá `—` naquela luz.

## Card Lovelace

Depois de criar a zona, adicione um cartão manual:

```yaml
type: custom:light-schedule-card
entity: sensor.sala_next_run
title: Sala
```

O card exibe: luzes ativas, potência total, potência individual, agendamentos e controles de ligar/desligar.

## Serviços

- `light_scheduler.turn_on_now`: liga todas as luzes da zona pela duração padrão ou informada.
- `light_scheduler.stop`: encerra a execução iniciada pela integração.
- `light_scheduler.add_schedule`, `remove_schedule` e `set_schedules`: administram a agenda.
- `light_scheduler.set_zone_options`: troca luzes, sensores de potência ou duração padrão.

## Limitação atual

Os sensores de potência são associados por posição: o primeiro sensor corresponde à primeira luz, e assim por diante. Uma próxima versão pode transformar isso em pares explícitos `luz → sensor`, evitando essa dependência de ordem.
