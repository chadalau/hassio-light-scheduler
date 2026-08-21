# Light Scheduler

Integração customizada para Home Assistant que controla todas as luzes de uma sala por horários. Cada zona aceita várias entidades `light` ou `switch`, mostra o estado individual e soma o consumo medido pelas tomadas inteligentes.

## Instalação

1. Pelo HACS, adicione este repositório como **Integração** e instale **Light Scheduler**.
2. Reinicie o Home Assistant.
3. Acesse **Configurações → Dispositivos e serviços → Adicionar integração**.
4. Procure **Light Scheduler**, informe o nome da sala e selecione todas as luzes ou tomadas dela.
5. Os sensores de potência são opcionais. Quando possível, a integração os encontra automaticamente no mesmo dispositivo; também é possível escolhê-los manualmente.

As entidades podem ser alteradas depois diretamente pelo ícone de engrenagem do card. Cada entrada possui nome personalizado e um campo de autocomplete para a luz ou tomada e outro para o respectivo sensor de potência. Basta digitar parte do nome ou do `entity_id` para restarem apenas os resultados correspondentes. A ordem das linhas é a mesma usada no card, e o botão **Adicionar entrada** inclui novas luzes sem recriar a zona.

## Card Lovelace

O recurso do card é carregado automaticamente pela integração. Depois de criar a zona, adicione um cartão manual usando o sensor **Próxima execução** daquela sala:

```yaml
type: custom:light-schedule-card
entity: sensor.indoor_proxima_execucao
```

Use o `entity_id` que existe na sua instalação; ele pode ser copiado em **Ferramentas do desenvolvedor → Estados**. A partir da versão 0.2.1, o card também aceita o sensor binário **Execução ativa** ou o switch **Agendamento** e localiza automaticamente o sensor completo da mesma zona.

O card permite:

- alternar cada luz ou tomada clicando no ícone ou nome;
- abrir o gráfico das últimas 24 horas clicando na potência individual;
- acompanhar potência individual e total;
- ver a próxima ação e o tempo restante da execução;
- adicionar, pausar, editar e excluir agendamentos pelos horários de acender e apagar, com duração calculada automaticamente, inclusive ao atravessar a meia-noite;
- definir um intervalo de 0 a 300 segundos para acender e apagar as entradas uma por vez, sempre na mesma ordem; o horário de apagar inicia a sequência de desligamento;
- abrir a configuração da zona pelo ícone de engrenagem.

Um agendamento marcado com ⚠ precisa de atenção. Se as luzes escolhidas naquele
horário saírem da zona, o horário é pausado automaticamente e o interruptor dele fica
travado até você editá-lo e escolher as luzes de novo — pausar é mais seguro do que
voltar a acender a sala inteira sem você pedir. O aviso também aparece, sem travar
nada, no horário que cai na hora repetida da volta do horário de verão; nesse caso a
integração usa a primeira ocorrência.

No campo **Nome** e no campo de potência, o texto em cinza é o que a integração
encontrou sozinha. Deixe em branco para continuar automático: o nome acompanha
renomeações da luz no Home Assistant e o sensor continua sendo descoberto pelo
dispositivo. Só o que você digitar vira configuração fixa.

Execuções em andamento são persistidas. Se o Home Assistant reiniciar, a integração restaura o horário de desligamento; ao remover ou descarregar uma zona ativa, as luzes da execução são desligadas na ordem configurada.

## Serviços

- `light_scheduler.turn_on_now`: liga todas as luzes da zona pela duração padrão ou informada.
- `light_scheduler.stop`: encerra somente uma execução iniciada pela integração.
- `light_scheduler.add_schedule`: cria um horário.
- `light_scheduler.update_schedule`: edita um horário existente.
- `light_scheduler.remove_schedule`: exclui um horário.
- `light_scheduler.set_schedules`: substitui toda a agenda.
- `light_scheduler.set_zone_options`: altera entidades e duração padrão.

Os serviços podem receber uma entidade do Light Scheduler como alvo. O card usa internamente o `entry_id` da zona para que todos os seus controles funcionem sem configuração adicional.

`turn_on_now` e `stop` retornam assim que a execução é registrada: a sequência com
intervalo entre as luzes roda em segundo plano, então um script que chame esses
serviços não fica preso durante o acendimento ou o desligamento. Acompanhe o
resultado pelo sensor binário **Execução ativa**.

Em `set_zone_options`, cada entrada de `entity_mappings` aceita `power_threshold_w`:
os watts acima dos quais aquela luz conta como acesa, usados na confirmação por
potência. O padrão é 1 W; baixe para uma fita de LED de menos de 1 W, que nunca
confirmaria o acendimento com o padrão, e suba para uma tomada com consumo de repouso
mais alto.
