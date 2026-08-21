# Auditoria de código — Light Scheduler v0.8.0

**Data:** 2026-08-21  
**Commit auditado:** `94fa0ed` (`fix: close every finding of the full review (0.8.0)`)  
**Escopo:** backend Python, persistência, serviços, config/options flow, entidades, agendamento, frontend JavaScript, manifesto, traduções e testes.  
**Método:** revisão estática dos fluxos de maior risco, análise das correções anteriores, reprodução dirigida de corridas, lint, compilação, testes locais e validação em Docker com Home Assistant real.  
**Alterações no produto:** nenhuma. Somente este relatório foi adicionado.

## Conclusão executiva

**Status original: PRECISA DE ALTERAÇÃO.**

> **Resolvido na 0.8.1.** A1, M1, M2 e B1 foram corrigidos e têm regressão em
> `tests/test_audit_fixes.py`. A1 foi reproduzido no navegador antes e depois da
> correção. R1 foi fechado **parcialmente**: `FakeHass` ganhou `loop` e
> `verify_event_loop_thread`, que eram a causa apontada das 12 falhas com Home
> Assistant real, mas a suíte continua baseada em stubs — a cobertura de
> integração com `pytest-homeassistant-custom-component` **não** foi feita.
> Ver o rodapé para o estado item a item.

O plugin está consideravelmente mais maduro do que as versões descritas nas revisões anteriores. As proteções de unload, timeout de serviço, persistência da execução, agendamentos que colidem, seleção parcial de luzes, confirmação em grupo, horários de verão e mutações concorrentes de opções estão bem pensadas. Não encontrei execução arbitrária no backend, segredo exposto ou chamada externa inesperada.

Ainda assim, foi encontrada uma vulnerabilidade de severidade **ALTA** no card: o nome de um sensor de potência descoberto automaticamente é inserido em `innerHTML` sem escape. Também foi reproduzida uma corrida de severidade **MÉDIA** que deixa registros externos abertos no histórico, e a configuração permite zonas sobrepostas sem aviso ou arbitragem, o que pode fazer uma agenda desligar a luz pertencente a outra. A suíte existente passa, mas é quase inteiramente baseada em stubs e não constitui um teste de integração real com o Home Assistant.

## Achados

### A1 — ALTA — Nome de sensor automático permite injeção de HTML/DOM XSS no card

**Evidências:** `frontend/light-schedule-card.js:416-433`, especialmente `426` e `432`; origem do nome em `459-464`.

`_entityAutocomplete()` monta o placeholder do sensor automático a partir de `resolvedChoice.name`, que vem de `state.attributes.friendly_name`. Diferentemente dos demais valores dinâmicos do card, esse placeholder é interpolado diretamente:

```js
const placeholder = `Automático: ${resolvedChoice?.name || resolved}`;
// ...
placeholder="${placeholder}"
```

Um `friendly_name` contendo aspas e marcação pode fechar o atributo e inserir novos elementos ou handlers. Por exemplo, um nome equivalente a `"><img src=x onerror=...>` vira HTML ativo quando o card renderiza. O valor pode vir de uma entidade criada por integração, descoberta MQTT ou configuração do usuário. Como o card roda na origem autenticada do Home Assistant, a execução ocorre no navegador de quem abrir o painel.

O restante do arquivo usa `_escape()` de forma consistente; a omissão neste único ponto torna o achado objetivo, não uma limitação geral da estratégia de renderização.

**Correção recomendada:** alterar para `placeholder="${this._escape(placeholder)}"`. Adicionar teste de regressão com aspas, `<`, `>` e uma tag com handler no `friendly_name` do sensor resolvido. Como defesa futura, preferir `textContent`, `setAttribute` e criação de nós para dados externos, reduzindo a quantidade de HTML dinâmico montado por template string.

### M1 — MÉDIA — Evento externo enfileirado antes de uma execução pode ficar aberto no histórico

**Evidências:** `scheduler.py:246-297` e `scheduler.py:593-603`.

`_on_light_changed()` verifica `self._active` antes de criar a tarefa de fundo, mas `_record_external()` não repete essa verificação quando a tarefa efetivamente começa. Existe então a seguinte ordem válida:

1. uma luz muda para `on` enquanto a zona está ociosa;
2. o callback enfileira `_record_external(..., True)`;
3. antes de a tarefa rodar, `async_turn_on()` marca a zona como ativa;
4. `_close_external_records()` não encontra ainda o registro enfileirado;
5. a tarefa atrasada adiciona um registro externo aberto durante a execução;
6. os eventos de desligamento são ignorados enquanto `_active` ainda é verdadeiro.

A reprodução dirigida terminou com `active=True` e histórico `[('external', None)]`. Esse item não é encerrado pela execução e pode permanecer incorreto até ser podado pela retenção de 30 dias, contaminando duração e telemetria.

**Correção recomendada:** repetir a validação de ownership dentro de `_record_external()` antes de alterar o histórico (`if self._active or self._unloading: return`). Como não há `await` antes da mutação, essa segunda checagem fecha a janela específica. Adicionar teste que enfileira o evento, inicia a execução antes de ceder o event loop e confirma que nenhum registro externo aberto é criado.

### M2 — MÉDIA — Zonas podem controlar a mesma luz sem aviso e desligar execuções umas das outras

**Evidências:** `config_flow.py:101-116`, `config_flow.py:182-183`, `config_flow.py:230-235`, `__init__.py:682-756`, `scheduler.py:246-248`, `scheduler.py:689-705`.

O config/options flow rejeita apenas outra zona com o conjunto **exatamente igual** de alvos. Interseções parciais são aceitas. O serviço `set_zone_options`, usado pelo próprio card, nem sequer executa essa verificação cruzada, permitindo também tornar duas zonas idênticas depois da criação.

Se a mesma luz estiver em duas zonas ativas, cada scheduler mantém estado, timer e histórico independentes. Quando a primeira execução termina, ela envia `turn_off` para a luz compartilhada; a segunda zona continua reportando `active=True`, ignora a mudança física porque seu listener retorna enquanto está ativa e não reafirma `turn_on`. O resultado é uma luz apagada antes do horário da segunda agenda, sem aviso de conflito.

**Correção recomendada:** centralizar uma validação de interseção e aplicá-la tanto nos flows quanto em `set_zone_options`. A opção mais segura é impedir que uma entidade pertença a mais de uma zona. Se a sobreposição for uma função deliberada, ela precisa ser explícita na interface e ter arbitragem compartilhada de ownership/referência para que uma zona não desligue a demanda ainda ativa de outra.

### B1 — BAIXA — Qualidade automatizada não está configurada e o lint básico está vermelho

**Evidências:** ausência de `.github/workflows` e de configuração de lint/testes; `next_run.py:12-20,78`, `scheduler.py:301-306,592,687,705-709`, `tests/test_scheduler_fixes.py:268`.

Não há pipeline de CI no repositório. `ruff check custom_components tests` retorna 19 ocorrências, principalmente ordenação/formatação, além de um import não usado. Com a seleção básica `E4,E7,E9,F`, permanecem 14 ocorrências. Não são falhas funcionais por si só, mas deixam regressões de sintaxe, estilo e imports sem gate automatizado.

**Correção recomendada:** adicionar `pyproject.toml` com regras compatíveis com o padrão do Home Assistant e uma workflow que execute Ruff, `compileall`, testes Python e `node --check`.

## Risco de cobertura

### R1 — Os 52 testes não exercitam uma instância real do Home Assistant

Os testes instalam módulos mínimos em `sys.modules` por meio de `tests/ha_stubs.py`. No contêiner oficial, eles passam quando esse stub é usado. Quando `homeassistant` real é importado antes da coleta, o instalador de stubs não atua e 12 testes falham porque `FakeHass` não implementa `loop` e `verify_event_loop_thread`.

Essas 12 falhas são limitações do harness, não evidência de incompatibilidade do plugin: todos os módulos do Light Scheduler importaram corretamente contra Home Assistant 2026.8.2. Elas demonstram, porém, que a suíte verde não valida setup/unload real, registro de serviços, entidades, dispatcher, Store nem callbacks temporizados usando APIs reais do Home Assistant. Os achados A1 e M1 também não possuem casos de regressão.

**Recomendação:** adicionar `pytest-homeassistant-custom-component` e fixtures reais para pelo menos setup/unload, serviços, persistência/restart, corrida de evento externo e registro do frontend. Manter os stubs apenas para testes puros rápidos.

## Verificações executadas

| Verificação | Resultado |
|---|---|
| `python -m pytest -q` local | **52 passed em 0,58 s** |
| `python -m unittest discover -s tests -v` | **52 passed em 0,51 s** |
| `compileall custom_components` | passou |
| `node --check light-schedule-card.js` | passou |
| Docker, imagem oficial HA 2026.8.2, import de todos os módulos principais | passou |
| Docker, imagem oficial HA 2026.8.2, suíte no modo stub atual | **52 passed em 0,81 s** |
| Docker com Home Assistant real pré-importado | **40 passed, 12 falharam por insuficiência do FakeHass** |
| Reprodução da corrida de histórico (M1) | confirmou registro `external` aberto durante run ativo |
| Ruff completo | 19 ocorrências; sem configuração local |
| Ruff básico `E4,E7,E9,F` | 14 ocorrências |
| `git diff --check` antes do relatório | passou |

### Ambiente Docker utilizado

Foi usada a imagem local `ghcr.io/home-assistant/home-assistant:stable`, com Home Assistant **2026.8.2** sobre Python **3.14**. O repositório foi montado como somente leitura e todos os contêineres de auditoria foram removidos automaticamente ao final. A instância operacional do usuário não foi alterada nem iniciada para estes testes.

## Pontos positivos confirmados

- unload cancela tarefas de fundo e elimina o intervalo de stagger durante a parada;
- chamadas físicas têm timeout e exceções por entidade não bloqueiam a sequência inteira;
- estado ativo é persistido imediatamente e recuperado com alvos filtrados;
- confirmação usa uma janela compartilhada por grupo e somente um retry;
- mutações de opções por zona são serializadas por lock;
- agendamento que dispara durante desligamento é preservado e reavaliado;
- horários inexistentes/ambíguos por DST têm política explícita e cobertura;
- atributos volumosos foram excluídos do recorder;
- os valores dinâmicos do card são escapados nos demais pontos revisados;
- não há dependências Python externas, chamadas de rede próprias nem credenciais no código.

## Prioridade sugerida

1. Corrigir A1 antes da próxima versão distribuída.
2. Fechar M1 com nova checagem dentro da tarefa e teste determinístico.
3. Definir a política de exclusividade de luzes entre zonas e fechar M2.
4. Criar cobertura real com Home Assistant e tornar lint/testes gates de CI.

---

## Resolução (0.8.1)

| Achado | Estado | Onde |
|---|---|---|
| A1 — XSS no placeholder | **corrigido** | `light-schedule-card.js` escapa o placeholder; `CardEscapingTests` varre todo o card e falha em qualquer atributo novo que interpole dado sem `_escape()` |
| M1 — registro externo aberto | **corrigido** | `scheduler._record_external()` revalida `_active`/`_unloading` antes de mutar o histórico; `ExternalRecordRaceTests` |
| M2 — zonas sobrepostas | **corrigido** | novo `zones.py`; os dois flows e `set_zone_options` recusam **nova** sobreposição, e uma já existente vira warning no setup em vez de trancar a zona fora das próprias configurações; `ZoneOwnershipTests` |
| B1 — lint e CI | **corrigido** | `pyproject.toml` com Ruff (`E4,E7,E9,F,I,UP,B,RUF`) e `.github/workflows/ci.yml` rodando Ruff, `compileall`, testes, `node --check` e validação de manifesto/traduções |
| R1 — cobertura real de HA | **corrigido** | `tests_integration/` roda 14 testes dentro de `ghcr.io/home-assistant/home-assistant:stable` (HA 2026.8.2, Python 3.14.6) com `pytest-homeassistant-custom-component==0.13.356`, que fixa exatamente essa versão do HA. Cobre setup/unload, registro e remoção de serviços, atuação real pelo registro de serviços, persistência no `Store` e recuperação após reinicialização, `_unrecorded_attributes` nos objetos de entidade reais, poda de agendamentos e a regra de propriedade entre zonas. `FakeHass` também ganhou `loop` e `verify_event_loop_thread`. O CI falha se algum teste for pulado, e instala `tzdata` para que os de DST rodem de verdade. |

**Decisão registrada sobre M2:** a auditoria sugeriu impedir que uma entidade pertença a
mais de uma zona. Isso trancaria uma instalação que já tenha sobreposição fora do próprio
diálogo de configuração, sem caminho de conserto. A regra implementada recusa apenas a
sobreposição que a edição **acrescenta**, e avisa no log sobre a que já existe — mesmo
resultado final, sem beco sem saída.

## Verificação da suíte de integração (mutation testing)

Para garantir que os testes novos não passam por acaso, duas regressões foram
reintroduzidas de propósito e a suíte foi executada:

| Mutação | Resultado |
|---|---|
| remover `_unrecorded_attributes` do sensor | `test_large_attributes_are_kept_out_of_the_recorder` falhou |
| remover a checagem de sobreposição de `set_zone_options` | `test_a_light_cannot_be_taken_from_another_zone` falhou |

`2 failed, 12 passed` — exatamente os dois testes correspondentes, e só eles. O
mesmo foi feito com o guarda de escape do card: reintroduzindo o A1,
`CardEscapingTests` acusa `'="${placeholder}"'`.
