# Light Scheduler

Integração customizada para Home Assistant que controla todas as luzes de uma sala por horários. Cada zona aceita várias entidades `light` ou `switch`, mostra o estado individual e soma o consumo medido pelas tomadas inteligentes.

## Instalação

1. Pelo HACS, adicione este repositório como **Integração** e instale **Light Scheduler**.
2. Reinicie o Home Assistant.
3. Acesse **Configurações → Dispositivos e serviços → Adicionar integração**.
4. Procure **Light Scheduler**, informe o nome da sala e selecione todas as luzes ou tomadas dela.
5. Os sensores de potência são opcionais. Quando possível, a integração os encontra automaticamente no mesmo dispositivo; também é possível escolhê-los manualmente.

As entidades podem ser alteradas depois pelo botão **Configurar** da integração. O ícone de engrenagem no card leva diretamente a essa tela.

## Card Lovelace

O recurso do card é carregado automaticamente pela integração. Depois de criar a zona, adicione um cartão manual usando o sensor **Próxima execução** daquela sala:

```yaml
type: custom:light-schedule-card
entity: sensor.indoor_proxima_execucao
```

Use o `entity_id` que existe na sua instalação; ele pode ser copiado em **Ferramentas do desenvolvedor → Estados**.

O card permite:

- ver e alternar cada luz ou tomada;
- acompanhar potência individual e total;
- ver a próxima ação e o tempo restante da execução;
- adicionar, editar e excluir agendamentos;
- abrir a configuração da zona pelo ícone de engrenagem.

## Serviços

- `light_scheduler.turn_on_now`: liga todas as luzes da zona pela duração padrão ou informada.
- `light_scheduler.stop`: encerra somente uma execução iniciada pela integração.
- `light_scheduler.add_schedule`: cria um horário.
- `light_scheduler.update_schedule`: edita um horário existente.
- `light_scheduler.remove_schedule`: exclui um horário.
- `light_scheduler.set_schedules`: substitui toda a agenda.
- `light_scheduler.set_zone_options`: altera entidades e duração padrão.

Os serviços podem receber uma entidade do Light Scheduler como alvo. O card usa internamente o `entry_id` da zona para que todos os seus controles funcionem sem configuração adicional.
