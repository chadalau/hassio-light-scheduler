const CARD_VERSION = "0.8.8";

class LightScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = {};
    this._timer = undefined;
    this._powerLoadId = 0;
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
    this.shadowRoot.addEventListener("focusin", (event) => this._handleFocusIn(event));
    this.shadowRoot.addEventListener("focusout", (event) => this._handleFocusOut(event));
    this.shadowRoot.addEventListener("keydown", (event) => this._handleKeyDown(event));
  }

  static getStubConfig(hass) {
    // Pick a zone that actually exists: a hardcoded id made the card preview in
    // the picker render "Entidade nao encontrada" on every install.
    const zone = Object.values(hass?.states || {}).find(
      (state) => Array.isArray(state.attributes?.lights) && state.attributes?.entry_id
    );
    return { entity: zone?.entity_id || "sensor.sala_proxima_execucao" };
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("Defina a entidade sensor da zona em 'entity'.");
    }
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    const previousState = this._state;
    this._hass = hass;
    const currentState = this._state;
    if (this._hasOpenDialog()) {
      const historyCard = this.shadowRoot.querySelector("[data-power-chart] > *");
      if (historyCard) historyCard.hass = hass;
      this._syncTimer();
      this._tick();
      return;
    }
    if (previousState && previousState === currentState && this.shadowRoot.querySelector("ha-card")) {
      this._syncTimer();
      this._tick();
      return;
    }
    this._render();
  }

  connectedCallback() {
    this._syncTimer();
  }

  disconnectedCallback() {
    this._clearTimer();
  }

  getCardSize() {
    const schedules = this._state?.attributes?.schedules?.length || 0;
    const lights = this._state?.attributes?.lights?.length || 0;
    return Math.max(5, 4 + Math.ceil(lights / 2) + schedules);
  }

  get _state() {
    const configured = this._hass?.states?.[this._config?.entity];
    if (!configured || Array.isArray(configured.attributes?.lights)) {
      return configured;
    }

    const entryId = configured.attributes?.entry_id;
    if (!entryId) return configured;
    return Object.values(this._hass.states).find(
      (state) =>
        state.attributes?.entry_id === entryId &&
        Array.isArray(state.attributes?.lights)
    ) || configured;
  }

  _render() {
    if (!this.shadowRoot || !this._config?.entity) return;

    const state = this._state;
    if (!this._hass) {
      this.shadowRoot.innerHTML = `${this._styles()}<ha-card><div class="loading">Carregando…</div></ha-card>`;
      return;
    }
    if (!state) {
      this.shadowRoot.innerHTML = `${this._styles()}<ha-card><div class="error">Entidade não encontrada: ${this._escape(this._config.entity)}</div></ha-card>`;
      return;
    }

    const attrs = state.attributes || {};
    const lights = Array.isArray(attrs.lights) ? attrs.lights : [];
    const schedules = Array.isArray(attrs.schedules) ? attrs.schedules : [];
    const enabled = attrs.enabled !== false;
    const onCount = Math.max(0, Math.round(this._number(attrs.lights_on)));
    const total = lights.length;
    const power = this._number(attrs.total_power_w);
    const zoneName = attrs.zone_name || attrs.friendly_name || "Sala";
    const statusIcon = enabled ? "mdi:calendar-check-outline" : "mdi:calendar-remove-outline";
    const statusTitle = enabled ? "Agendamento ativo" : "Agendamento pausado";
    const headerTiming = this._headerTiming(attrs, state.state);

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <section class="shell">
          <header class="hero-header">
            <div class="hero-top">
              <div class="hero-identity">
                <div class="hero-icon" aria-hidden="true">
                  <ha-icon icon="mdi:lightbulb-group-outline"></ha-icon>
                </div>
                <div class="hero-title-group">
                  <span class="hero-eyebrow">Iluminação</span>
                  <h2 title="${this._escape(zoneName)}">${this._escape(zoneName)}</h2>
                </div>
              </div>
              <div class="hero-actions">
                <span class="status-chip ${enabled ? "enabled" : "disabled"}" title="${this._escape(statusTitle)}">
                  <ha-icon icon="${this._escape(statusIcon)}"></ha-icon>
                  <span>${enabled ? "Agendada" : "Pausada"}</span>
                </span>
                ${this._toggleControl({
                  action: "toggle-zone-enabled",
                  checked: enabled,
                  label: "Ativar agendamento da zona",
                  title: this._scheduleSwitchEntityId()
                    ? (enabled ? "Pausar todos os agendamentos desta sala" : "Retomar os agendamentos desta sala")
                    : "Interruptor de agendamento da zona não encontrado",
                  disabled: !this._scheduleSwitchEntityId(),
                })}
                <button class="icon-button settings" type="button" data-action="open-zone-dialog" aria-label="Configurar zona" title="Configurar zona">
                  <ha-icon icon="mdi:cog-outline"></ha-icon>
                </button>
              </div>
            </div>

            <div class="hero-summary">
              <div class="hero-kpi">
                <strong>${onCount} de ${total} ${total === 1 ? "luz ligada" : "luzes ligadas"}</strong>
                <span class="hero-countdown" data-header-countdown>${this._escape(headerTiming.text)}</span>
              </div>
              <div class="hero-secondary">
                <span>Potência total</span>
                <strong>${this._formatPower(power)}</strong>
              </div>
            </div>

            <div class="hero-timeline" role="progressbar" aria-label="${this._escape(headerTiming.label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${this._escape(Math.round(headerTiming.progress))}" aria-valuetext="${this._escape(headerTiming.text)}" data-header-progress>
              <span style="width: ${this._escape(headerTiming.progress)}%" data-header-progress-fill></span>
            </div>
          </header>

          <div class="divider"></div>
          <h3>Luzes da sala</h3>
          ${lights.length ? `<div class="lights-grid">${lights.map((light) => this._lightTile(light)).join("")}</div>` : this._emptyLights()}

          <div class="divider section-divider"></div>
          <h3>Agenda automática</h3>
          <div class="schedules">
            ${schedules.length ? schedules.map((schedule) => this._scheduleRow(schedule, attrs.schedule_warnings)).join("") : `<div class="empty-schedule">Nenhum agendamento criado.</div>`}
          </div>
          <button class="add-button" type="button" data-action="add-schedule">
            <ha-icon icon="mdi:plus"></ha-icon>
            Adicionar agendamento
          </button>
        </section>
        ${this._scheduleDialog()}
        ${this._zoneDialog()}
        ${this._powerDialog()}
      </ha-card>
    `;

    this._syncTimer();
    this._tick();
  }

  _lightTile(light) {
    const entityId = String(light.entity_id || "");
    const isOn = light.state === "on";
    const unavailable = light.available === false || ["unavailable", "unknown"].includes(light.state);
    const powerEntityId = String(light.power_entity_id || "");
    const hasPower = Boolean(powerEntityId);
    const power = hasPower && light.power_w != null ? this._formatPower(light.power_w) : "—";
    const status = unavailable ? "Indisponível" : isOn ? "Ligada" : "Desligada";
    return `
      <div class="light-tile ${isOn ? "is-on" : ""} ${unavailable ? "is-unavailable" : ""}">
        <button class="light-main" type="button" data-action="toggle-light" data-entity-id="${this._escape(entityId)}"
          ${unavailable ? "disabled" : ""} aria-label="Alternar ${this._escape(light.name || entityId)}">
          <ha-icon class="bulb" icon="mdi:lightbulb"></ha-icon>
          <span class="light-copy">
            <strong title="${this._escape(light.name || entityId)}">${this._escape(light.name || entityId)}</strong>
            <small>${status}</small>
          </span>
        </button>
        <button class="power-pill" type="button" data-action="power-history"
          data-power-entity-id="${this._escape(powerEntityId)}" data-light-name="${this._escape(light.name || entityId)}"
          ${hasPower ? "" : "disabled"} aria-label="${hasPower ? `Abrir histórico de potência de ${this._escape(light.name || entityId)}` : "Sensor de potência não configurado"}"
          title="${hasPower ? "Histórico de potência das últimas 24 horas" : "Sensor de potência não configurado"}">
          <ha-icon icon="mdi:flash"></ha-icon>${power}
        </button>
      </div>
    `;
  }

  _emptyLights() {
    return `
      <button class="empty-lights" type="button" data-action="open-zone-dialog">
        <ha-icon icon="mdi:lightbulb-alert-outline"></ha-icon>
        <span><strong>Nenhuma luz configurada</strong><small>Clique aqui para escolher as lâmpadas ou tomadas desta sala.</small></span>
        <ha-icon icon="mdi:chevron-right"></ha-icon>
      </button>
    `;
  }

  _toggleControl({ action, checked, label, title, dataset = {}, disabled = false }) {
    const attributes = Object.entries(dataset)
      .map(([key, value]) => ` data-${key}="${this._escape(value)}"`)
      .join("");
    return `
      <label class="toggle" title="${this._escape(title)}">
        <input type="checkbox" data-action="${this._escape(action)}"${attributes}
          ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="${this._escape(label)}">
        <span></span>
      </label>
    `;
  }

  _warningText(code) {
    if (code === "targets_removed") {
      return "As luzes deste horário saíram da zona. Edite o horário e escolha as luzes de novo para reativá-lo.";
    }
    if (code === "ambiguous_time") {
      return "Este horário acontece duas vezes na volta do horário de verão; a primeira ocorrência será usada.";
    }
    return "";
  }

  _scheduleRow(schedule, computedWarnings) {
    const id = String(schedule.id || "");
    const enabled = schedule.enabled !== false;
    const start = String(schedule.time || schedule.start || "--:--").slice(0, 5);
    const end = this._scheduleEnd(schedule);
    // A persisted warning means the row needs the user to act; a computed one
    // only describes what will happen.
    const blocking = String(schedule.warning || "");
    const warning = blocking || String(computedWarnings?.[id] || "");
    const warningText = this._warningText(warning);
    return `
      <div class="schedule-row ${enabled ? "" : "is-disabled"} ${warning ? "has-warning" : ""}">
        ${this._toggleControl({
          action: "toggle-schedule-enabled",
          checked: enabled,
          label: "Ativar agendamento",
          title: blocking
            ? warningText
            : enabled ? "Desativar este agendamento" : "Ativar este agendamento",
          dataset: { "schedule-id": id },
          disabled: Boolean(blocking),
        })}
        ${warningText
          ? `<ha-icon class="row-warning" icon="mdi:alert-outline" title="${this._escape(warningText)}"></ha-icon>`
          : `<ha-icon class="clock" icon="mdi:clock-outline"></ha-icon>`}
        <span class="time-range"><strong>${this._escape(start)}</strong><i>→</i><strong>${this._escape(end)}</strong></span>
        <span class="duration-chip" title="Duração"><ha-icon icon="mdi:timer-outline"></ha-icon>${this._escape(this._formatDuration(schedule.duration))}</span>
        <span class="days" title="${this._escape(this._formatDays(schedule.days))}">${this._escape(this._formatDays(schedule.days))}</span>
        <button class="row-action" type="button" data-action="edit-schedule" data-schedule-id="${this._escape(id)}" aria-label="Editar agendamento" title="Editar"><ha-icon icon="mdi:pencil"></ha-icon></button>
        <button class="row-action delete" type="button" data-action="delete-schedule-row" data-schedule-id="${this._escape(id)}" aria-label="Excluir agendamento" title="Excluir"><ha-icon icon="mdi:trash-can-outline"></ha-icon></button>
      </div>
    `;
  }

  _scheduleDialog() {
    return `
      <dialog class="schedule-dialog">
        <form method="dialog" class="dialog-form">
          <div class="dialog-header">
            <div><small>Agenda automática</small><h3 data-dialog-title>Novo agendamento</h3></div>
            <button class="icon-button" type="button" data-action="close-dialog" aria-label="Fechar"><ha-icon icon="mdi:close"></ha-icon></button>
          </div>
          <input type="hidden" name="schedule_id">
          <div class="fields">
            <label>Horário de acender<input name="start" type="time" step="1" required value="18:30"></label>
            <label>Horário de apagar<input name="end" type="time" step="1" required value="22:30"></label>
          </div>
          <div class="duration-preview"><ha-icon icon="mdi:timer-outline"></ha-icon><span>Ficará acesa por <strong data-duration-preview>4h</strong></span></div>
          <label class="interval-setting">
            <span><strong>Intervalo entre as luzes</strong><small>Aplicado ao acender e apagar, na mesma ordem. Use 0 para acionar todas juntas.</small></span>
            <span class="interval-input"><input name="interval" type="number" min="0" max="300" step="1" value="0" required><b>seg</b></span>
          </label>
          ${this._lightsPicker()}
          <fieldset>
            <legend>Dias da semana</legend>
            <div class="day-grid">
              ${["seg", "ter", "qua", "qui", "sex", "sáb", "dom"].map((label, index) => `<label><input type="checkbox" name="day" value="${index}" checked><span>${label}</span></label>`).join("")}
            </div>
          </fieldset>
          <p class="dialog-error" data-dialog-error hidden></p>
          <div class="dialog-actions">
            <button class="delete-button" type="button" data-action="delete-schedule" hidden><ha-icon icon="mdi:trash-can-outline"></ha-icon>Excluir</button>
            <span></span>
            <button class="cancel-button" type="button" data-action="close-dialog">Cancelar</button>
            <button class="save-button" type="button" data-action="save-schedule">Salvar</button>
          </div>
        </form>
      </dialog>
    `;
  }

  _lightsPicker() {
    const lights = this._state?.attributes?.lights || [];
    if (!lights.length) {
      return `<div class="lights-picker is-empty">Nenhuma luz configurada nesta zona.</div>`;
    }
    return `
      <div class="lights-picker" data-lights-picker>
        <button class="lights-summary" type="button" data-action="toggle-lights-picker" aria-expanded="false">
          <ha-icon icon="mdi:lightbulb-group-outline"></ha-icon>
          <span>
            <strong data-lights-count>Todas as luzes</strong>
            <small>Escolha quais luzes este horário controla.</small>
          </span>
          <ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
        </button>
        <div class="lights-options" data-lights-options hidden>
          ${lights.map((light) => {
            const entityId = String(light.entity_id || "");
            const name = light.name || entityId;
            return `
              <label class="light-choice">
                <input type="checkbox" name="schedule_light" value="${this._escape(entityId)}" checked>
                <span class="light-choice-name" title="${this._escape(name)}">${this._escape(name)}</span>
              </label>
            `;
          }).join("")}
          <div class="lights-bulk">
            <button type="button" data-action="select-all-lights">Todas</button>
            <button type="button" data-action="select-no-lights">Nenhuma</button>
          </div>
        </div>
      </div>
    `;
  }

  _zoneDialog() {
    const attrs = this._state?.attributes || {};
    const zoneName = attrs.zone_name || attrs.friendly_name || "Sala";
    const mappings = Array.isArray(attrs.entity_mappings) && attrs.entity_mappings.length
      ? attrs.entity_mappings
      : (attrs.lights || []).map((light) => ({
          name: "",
          target_entity_id: light.entity_id || "",
          power_entity_id: "",
          display_name: light.name || "",
          resolved_power_entity_id: light.power_entity_id || "",
        }));
    return `
      <dialog class="zone-dialog">
        <form method="dialog" class="dialog-form">
          <div class="dialog-header">
            <div><small>Configuração da zona</small><h3>Escolher luzes e tomadas</h3></div>
            <button class="icon-button" type="button" data-action="close-zone-dialog" aria-label="Fechar"><ha-icon icon="mdi:close"></ha-icon></button>
          </div>
          <label class="zone-name-field">
            <span class="zone-name-icon"><ha-icon icon="mdi:rename-outline"></ha-icon></span>
            <span class="zone-name-copy">
              <strong>Nome da zona</strong>
              <small>Renomeia o card, a integração e o dispositivo</small>
            </span>
            <input name="zone_name" type="text" maxlength="64" required value="${this._escape(zoneName)}" aria-label="Nome da zona">
          </label>
          <div class="mapping-header" aria-hidden="true">
            <span></span><span>Nome</span><span>Luz ou interruptor</span><span>Potência</span><span></span>
          </div>
          <div class="mapping-list" data-mapping-list>
            ${(mappings.length ? mappings : [{}]).map((mapping, index) => this._mappingRow(mapping, index)).join("")}
          </div>
          <button class="add-mapping-button" type="button" data-action="add-mapping-row"><ha-icon icon="mdi:plus"></ha-icon>Adicionar entrada</button>
          <p class="dialog-error" data-zone-error hidden></p>
          <div class="zone-help">O sensor de potência é opcional. Sem seleção, a integração tenta encontrá-lo automaticamente no mesmo dispositivo.</div>
          <div class="dialog-actions zone-actions">
            <button class="advanced-button" type="button" data-action="integration-settings">Configuração avançada</button>
            <span></span>
            <button class="cancel-button" type="button" data-action="close-zone-dialog">Cancelar</button>
            <button class="save-button" type="button" data-action="save-zone">Salvar</button>
          </div>
        </form>
      </dialog>
    `;
  }

  _mappingRow(mapping = {}, index = 0) {
    const target = mapping.target_entity_id || "";
    // Only what the user actually chose goes into the fields. Pre-filling them
    // with the resolved name and the auto-discovered sensor turned "automatic"
    // into "manual" the moment anyone pressed save.
    const power = mapping.power_entity_id || "";
    const resolvedPower = mapping.resolved_power_entity_id || "";
    const displayName =
      mapping.display_name || this._hass?.states?.[target]?.attributes?.friendly_name || "";
    const threshold = mapping.power_threshold_w;
    return `
      <div class="mapping-row" data-mapping-row${threshold == null ? "" : ` data-threshold="${this._escape(threshold)}"`}>
        <span class="mapping-order">${index + 1}</span>
        <input class="mapping-name" name="mapping_name" type="text" value="${this._escape(mapping.name || "")}"
          placeholder="${this._escape(displayName || "Nome")}" aria-label="Nome da entrada ${index + 1}">
        ${this._entityAutocomplete("target", target, index)}
        ${this._entityAutocomplete("power", power, index, resolvedPower)}
        <button class="remove-mapping-button" type="button" data-action="remove-mapping-row" aria-label="Remover entrada ${index + 1}" title="Remover"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
      </div>
    `;
  }

  _entityAutocomplete(kind, selected = "", index = 0, resolved = "") {
    const choices = this._entityChoices(kind);
    const selectedChoice = choices.find((choice) => choice.id === selected);
    const value = selected ? selectedChoice?.label || selected : "";
    const field = kind === "target" ? "mapping_target" : "mapping_power";
    const label = kind === "target" ? "Luz ou interruptor" : "Potência";
    // Carries a friendly_name, i.e. text an integration or another user chose.
    // It is escaped at the interpolation below, like every other dynamic value
    // in this file; the literal placeholders used to make that easy to forget.
    const resolvedChoice = resolved ? choices.find((choice) => choice.id === resolved) : null;
    const placeholder = kind === "target"
      ? "Digite para buscar luz…"
      : resolved
        ? `Automático: ${resolvedChoice?.name || resolved}`
        : "Digite para buscar potência…";
    return `
      <div class="entity-autocomplete" data-autocomplete data-kind="${kind}">
        <input class="autocomplete-input" type="search" autocomplete="off" spellcheck="false"
          data-autocomplete-input data-field="${field}" data-selected-id="${this._escape(selected)}"
          value="${this._escape(value)}" placeholder="${this._escape(placeholder)}"
          aria-label="${label} da entrada ${index + 1}" aria-expanded="false">
        <div class="autocomplete-menu" data-autocomplete-menu hidden></div>
      </div>
    `;
  }

  _entityChoices(kind) {
    const states = Object.values(this._hass?.states || {});
    const schedulerEntryIds = new Set(
      states
        .filter((state) => Array.isArray(state.attributes?.lights) && state.attributes?.entry_id)
        .map((state) => state.attributes.entry_id)
    );
    const selected = kind === "target"
      ? states.filter((state) => {
          const domain = state.entity_id?.split(".")[0];
          return ["light", "switch"].includes(domain)
            && !schedulerEntryIds.has(state.attributes?.entry_id);
        })
      : states.filter((state) => {
          if (state.entity_id?.split(".")[0] !== "sensor") return false;
          const unit = state.attributes?.unit_of_measurement;
          return state.attributes?.device_class === "power" || ["W", "kW"].includes(unit);
        });
    const choices = selected
      .map((state) => {
        const name = state.attributes?.friendly_name || state.entity_id;
        return {
          id: state.entity_id,
          name,
          label: `${name} — ${state.entity_id}`,
          search: this._normalizeSearch(`${name} ${state.entity_id}`),
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name, "pt-BR"));
    if (kind === "power") {
      choices.unshift({ id: "", name: "Automático / nenhum", label: "Automático / nenhum", search: "automatico nenhum" });
    }
    return choices;
  }

  _powerDialog() {
    return `
      <dialog class="power-dialog">
        <div class="dialog-form">
          <div class="dialog-header">
            <div><small>Histórico de 24 horas</small><h3 data-power-title>Potência</h3></div>
            <button class="icon-button" type="button" data-action="close-power-dialog" aria-label="Fechar"><ha-icon icon="mdi:close"></ha-icon></button>
          </div>
          <div class="power-sensor-name" data-power-sensor></div>
          <div class="power-chart" data-power-chart><div class="chart-loading">Carregando histórico…</div></div>
        </div>
      </dialog>
    `;
  }

  async _handleClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || target.disabled) return;
    const action = target.dataset.action;

    try {
      if (action === "select-autocomplete-option") {
        this._selectAutocompleteOption(target);
      } else if (action === "toggle-light") {
        await this._hass.callService("homeassistant", "toggle", { entity_id: target.dataset.entityId });
      } else if (action === "power-history") {
        await this._openPowerHistory(target.dataset.powerEntityId, target.dataset.lightName);
      } else if (action === "close-power-dialog") {
        this._powerLoadId += 1;
        this._powerDialogElement()?.close();
      } else if (action === "stop-now") {
        await this._hass.callService("light_scheduler", "stop", { entry_id: this._entryId() });
      } else if (action === "open-zone-dialog") {
        this._openZoneDialog();
      } else if (action === "add-mapping-row") {
        this._addMappingRow();
      } else if (action === "remove-mapping-row") {
        this._removeMappingRow(target);
      } else if (action === "close-zone-dialog") {
        this._zoneDialogElement()?.close();
      } else if (action === "save-zone") {
        await this._saveZone();
      } else if (action === "integration-settings") {
        this._zoneDialogElement()?.close();
        this._navigate("/config/integrations/integration/light_scheduler");
      } else if (action === "add-schedule") {
        this._openDialog();
      } else if (action === "edit-schedule") {
        const schedule = this._state?.attributes?.schedules?.find((item) => String(item.id) === target.dataset.scheduleId);
        this._openDialog(schedule);
      } else if (action === "close-dialog") {
        this._dialog()?.close();
      } else if (action === "save-schedule") {
        await this._saveSchedule();
      } else if (action === "delete-schedule") {
        await this._deleteSchedule();
      } else if (action === "delete-schedule-row") {
        if (await this._confirmAndRemoveSchedule(target.dataset.scheduleId)) this._render();
      } else if (action === "toggle-schedule-enabled") {
        await this._toggleScheduleEnabled(target);
      } else if (action === "toggle-zone-enabled") {
        await this._toggleZoneEnabled(target);
      } else if (action === "toggle-lights-picker") {
        this._toggleLightsPicker();
      } else if (action === "select-all-lights") {
        this._setAllLights(null, true);
      } else if (action === "select-no-lights") {
        this._setAllLights(null, false);
      }
    } catch (error) {
      if (action === "save-zone") {
        this._showZoneError(error?.message || String(error));
      } else {
        this._showDialogError(error?.message || String(error));
      }
    }
  }

  _openDialog(schedule) {
    const dialog = this._dialog();
    if (!dialog) return;
    const form = dialog.querySelector("form");
    form.reset();
    form.elements.schedule_id.value = schedule?.id || "";
    form.elements.start.value = schedule?.time || schedule?.start || "18:30";
    form.elements.end.value = schedule ? this._scheduleEnd(schedule) : "22:30";
    form.elements.interval.value = Math.max(0, Number(schedule?.interval || 0));
    const days = Array.isArray(schedule?.days) ? schedule.days.map(Number) : [0, 1, 2, 3, 4, 5, 6];
    form.querySelectorAll('input[name="day"]').forEach((input) => { input.checked = days.includes(Number(input.value)); });
    // An empty stored list means the whole zone, so everything starts checked.
    const chosen = Array.isArray(schedule?.target_entity_ids) ? schedule.target_entity_ids : [];
    form.querySelectorAll('input[name="schedule_light"]').forEach((input) => {
      input.checked = !chosen.length || chosen.includes(input.value);
    });
    this._collapseLightsPicker(form);
    this._updateLightsCount(form);
    dialog.querySelector("[data-dialog-title]").textContent = schedule ? "Editar agendamento" : "Novo agendamento";
    dialog.querySelector("[data-action='delete-schedule']").hidden = !schedule;
    this._showDialogError("");
    this._updateDurationPreview(form);
    if (!dialog.open) dialog.showModal();
  }

  async _saveSchedule() {
    const dialog = this._dialog();
    const form = dialog?.querySelector("form");
    if (!form?.reportValidity()) return;
    const days = [...form.querySelectorAll('input[name="day"]:checked')].map((input) => Number(input.value));
    if (!days.length) {
      this._showDialogError("Selecione pelo menos um dia da semana.");
      return;
    }
    const allLights = [...form.querySelectorAll('input[name="schedule_light"]')];
    const chosenLights = allLights.filter((input) => input.checked);
    if (allLights.length && !chosenLights.length) {
      this._showDialogError("Selecione pelo menos uma luz para este horário.");
      this._expandLightsPicker(form);
      return;
    }
    const data = {
      entry_id: this._entryId(),
      time: form.elements.start.value,
      duration: this._durationBetween(form.elements.start.value, form.elements.end.value),
      interval: Math.max(0, Math.min(300, Math.round(Number(form.elements.interval.value) || 0))),
      // Everything checked is stored as "the whole zone" rather than a frozen
      // list, so a light added to the zone later joins this schedule too.
      target_entity_ids:
        chosenLights.length === allLights.length
          ? []
          : chosenLights.map((input) => input.value),
      days,
    };
    const scheduleId = form.elements.schedule_id.value;
    if (scheduleId) data.id = scheduleId;
    await this._hass.callService("light_scheduler", scheduleId ? "update_schedule" : "add_schedule", data);
    dialog.close();
    this._render();
  }

  async _deleteSchedule() {
    const dialog = this._dialog();
    const scheduleId = dialog?.querySelector('[name="schedule_id"]')?.value;
    if (await this._confirmAndRemoveSchedule(scheduleId)) {
      dialog.close();
      this._render();
    }
  }

  async _confirmAndRemoveSchedule(scheduleId) {
    if (!scheduleId || !window.confirm("Excluir este agendamento?")) return false;
    await this._hass.callService("light_scheduler", "remove_schedule", { entry_id: this._entryId(), id: scheduleId });
    return true;
  }

  _scheduleSwitchEntityId() {
    const entryId = this._state?.attributes?.entry_id;
    if (!entryId || !this._hass?.states) return "";
    const match = Object.values(this._hass.states).find(
      (state) =>
        state.entity_id?.startsWith("switch.") &&
        state.attributes?.entry_id === entryId
    );
    return match?.entity_id || "";
  }

  async _toggleZoneEnabled(checkbox) {
    // Goes through the zone switch rather than the options service: only the
    // switch also stops a run that is already on when the zone is paused.
    const entityId = this._scheduleSwitchEntityId();
    if (!entityId) {
      checkbox.checked = !checkbox.checked;
      return;
    }
    try {
      await this._hass.callService(
        "homeassistant",
        checkbox.checked ? "turn_on" : "turn_off",
        { entity_id: entityId }
      );
    } catch (error) {
      checkbox.checked = !checkbox.checked;
      throw error;
    }
  }

  async _toggleScheduleEnabled(checkbox) {
    const scheduleId = checkbox.dataset.scheduleId;
    if (!scheduleId) return;
    try {
      await this._hass.callService("light_scheduler", "update_schedule", {
        entry_id: this._entryId(),
        id: scheduleId,
        enabled: checkbox.checked,
      });
    } catch (error) {
      checkbox.checked = !checkbox.checked;
      throw error;
    }
    this._render();
  }

  _lightsPickerElements(scope) {
    const root = scope || this._dialog();
    return {
      picker: root?.querySelector("[data-lights-picker]"),
      summary: root?.querySelector("[data-action='toggle-lights-picker']"),
      options: root?.querySelector("[data-lights-options]"),
      count: root?.querySelector("[data-lights-count]"),
      inputs: [...(root?.querySelectorAll('input[name="schedule_light"]') || [])],
    };
  }

  _setLightsPickerOpen(scope, open) {
    const { picker, summary, options } = this._lightsPickerElements(scope);
    if (!picker || !options || !summary) return;
    options.hidden = !open;
    picker.classList.toggle("is-open", open);
    summary.setAttribute("aria-expanded", String(open));
  }

  _expandLightsPicker(scope) {
    this._setLightsPickerOpen(scope, true);
  }

  _collapseLightsPicker(scope) {
    this._setLightsPickerOpen(scope, false);
  }

  _toggleLightsPicker(scope) {
    const { options } = this._lightsPickerElements(scope);
    this._setLightsPickerOpen(scope, Boolean(options?.hidden));
  }

  _setAllLights(scope, checked) {
    const { inputs } = this._lightsPickerElements(scope);
    inputs.forEach((input) => { input.checked = checked; });
    this._updateLightsCount(scope);
  }

  _updateLightsCount(scope) {
    const { count, inputs } = this._lightsPickerElements(scope);
    if (!count) return;
    const chosen = inputs.filter((input) => input.checked).length;
    if (!inputs.length) {
      count.textContent = "Nenhuma luz configurada";
    } else if (chosen === inputs.length) {
      count.textContent = `Todas as luzes (${inputs.length})`;
    } else if (chosen === 0) {
      count.textContent = "Nenhuma luz selecionada";
    } else {
      count.textContent = `${chosen} de ${inputs.length} luzes selecionadas`;
    }
  }

  _dialog() {
    return this.shadowRoot.querySelector(".schedule-dialog");
  }

  _hasOpenDialog() {
    return Boolean(this.shadowRoot?.querySelector("dialog[open]"));
  }

  _zoneDialogElement() {
    return this.shadowRoot.querySelector(".zone-dialog");
  }

  _powerDialogElement() {
    return this.shadowRoot.querySelector(".power-dialog");
  }

  async _openPowerHistory(entityId, lightName) {
    if (!entityId) return;
    const dialog = this._powerDialogElement();
    const host = dialog?.querySelector("[data-power-chart]");
    if (!dialog || !host) return;

    dialog.querySelector("[data-power-title]").textContent = lightName || "Potência";
    dialog.querySelector("[data-power-sensor]").textContent = entityId;
    host.innerHTML = `<div class="chart-loading">Carregando histórico…</div>`;
    const loadId = ++this._powerLoadId;
    if (!dialog.open) dialog.showModal();

    try {
      const helpers = await window.loadCardHelpers();
      const historyCard = await helpers.createCardElement({
        type: "history-graph",
        entities: [entityId],
        hours_to_show: 24,
      });
      historyCard.hass = this._hass;
      if (dialog.open && loadId === this._powerLoadId) host.replaceChildren(historyCard);
    } catch (error) {
      if (dialog.open && loadId === this._powerLoadId) {
        host.innerHTML = `<div class="chart-error">Não foi possível carregar o gráfico.<small>${this._escape(error?.message || String(error))}</small></div>`;
      }
    }
  }

  _openZoneDialog() {
    const dialog = this._zoneDialogElement();
    if (!dialog) return;
    const attrs = this._state?.attributes || {};
    const nameInput = dialog.querySelector('[name="zone_name"]');
    if (nameInput) {
      nameInput.value = attrs.zone_name || attrs.friendly_name || "Sala";
    }
    const error = dialog.querySelector("[data-zone-error]");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    if (!dialog.open) dialog.showModal();
  }

  async _saveZone() {
    const dialog = this._zoneDialogElement();
    const zoneName = dialog.querySelector('[name="zone_name"]')?.value.trim() || "";
    const currentName = String(this._state?.attributes?.zone_name || "").trim();
    const error = dialog.querySelector("[data-zone-error]");
    if (!zoneName) {
      error.textContent = "Informe o nome da zona.";
      error.hidden = false;
      return;
    }
    const mappings = [...dialog.querySelectorAll("[data-mapping-row]")].map((row) => {
      const mapping = {
        name: row.querySelector('[name="mapping_name"]').value.trim(),
        target_entity_id: row.querySelector('[data-field="mapping_target"]').dataset.selectedId || "",
        power_entity_id: row.querySelector('[data-field="mapping_power"]').dataset.selectedId || "",
      };
      // Set through the service, not the dialog; saving from the card must not
      // silently reset it to the default.
      if (row.dataset.threshold) mapping.power_threshold_w = Number(row.dataset.threshold);
      return mapping;
    });
    if (!mappings.length || mappings.some((item) => !item.target_entity_id)) {
      error.textContent = "Selecione uma luz ou tomada em todas as entradas.";
      error.hidden = false;
      return;
    }
    const targets = mappings.map((item) => item.target_entity_id);
    if (new Set(targets).size !== targets.length) {
      error.textContent = "A mesma luz ou tomada não pode aparecer duas vezes.";
      error.hidden = false;
      return;
    }
    const powers = mappings.map((item) => item.power_entity_id).filter(Boolean);
    if (new Set(powers).size !== powers.length) {
      error.textContent = "O mesmo sensor de potência não pode ser associado a duas entradas.";
      error.hidden = false;
      return;
    }
    const data = {
      entry_id: this._entryId(),
      entity_mappings: mappings,
    };
    if (zoneName !== currentName) data.name = zoneName;
    await this._hass.callService("light_scheduler", "set_zone_options", data);
    dialog.close();
    this._render();
  }

  _handleInput(event) {
    if (event.target.matches("[data-autocomplete-input]")) {
      event.target.dataset.selectedId = "";
      this._renderAutocompleteMenu(event.target.closest("[data-autocomplete]"));
      return;
    }
    if (event.target.matches('[name="start"], [name="end"]')) {
      this._updateDurationPreview(event.target.form);
      return;
    }
    if (event.target.matches('[name="schedule_light"]')) {
      this._updateLightsCount();
    }
  }

  _handleFocusIn(event) {
    if (event.target.matches("[data-autocomplete-input]")) {
      event.target.select();
      this._renderAutocompleteMenu(event.target.closest("[data-autocomplete]"));
    }
  }

  _handleFocusOut(event) {
    const autocomplete = event.target.closest?.("[data-autocomplete]");
    if (!autocomplete || autocomplete.contains(event.relatedTarget)) return;
    this._closeAutocomplete(autocomplete);
  }

  _handleKeyDown(event) {
    if (!event.target.matches("[data-autocomplete-input]")) return;
    const autocomplete = event.target.closest("[data-autocomplete]");
    const firstOption = autocomplete?.querySelector("[data-action='select-autocomplete-option']");
    if (event.key === "ArrowDown" && firstOption) {
      event.preventDefault();
      firstOption.focus();
    } else if (event.key === "Enter" && firstOption) {
      event.preventDefault();
      this._selectAutocompleteOption(firstOption);
    } else if (event.key === "Escape") {
      event.preventDefault();
      this._closeAutocomplete(autocomplete);
      event.target.blur();
    }
  }

  _renderAutocompleteMenu(autocomplete) {
    const input = autocomplete?.querySelector("[data-autocomplete-input]");
    const menu = autocomplete?.querySelector("[data-autocomplete-menu]");
    if (!input || !menu) return;
    const query = this._normalizeSearch(input.value);
    const choices = this._entityChoices(autocomplete.dataset.kind)
      .filter((choice) => !query || choice.search.includes(query))
      .slice(0, 40);
    menu.innerHTML = choices.length
      ? choices.map((choice) => `
          <button type="button" data-action="select-autocomplete-option"
            data-entity-id="${this._escape(choice.id)}" data-entity-label="${this._escape(choice.label)}">
            <strong>${this._escape(choice.name)}</strong>
            ${choice.id ? `<small>${this._escape(choice.id)}</small>` : ""}
          </button>
        `).join("")
      : `<div class="autocomplete-empty">Nenhuma entidade corresponde à busca.</div>`;
    const rect = input.getBoundingClientRect();
    const menuWidth = Math.min(Math.max(rect.width, 260), window.innerWidth - 16);
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - menuWidth - 8);
    const roomBelow = window.innerHeight - rect.bottom - 12;
    const openAbove = roomBelow < 190 && rect.top > roomBelow;
    menu.style.width = `${menuWidth}px`;
    menu.style.left = `${left}px`;
    menu.style.top = openAbove ? "auto" : `${rect.bottom + 4}px`;
    menu.style.bottom = openAbove ? `${window.innerHeight - rect.top + 4}px` : "auto";
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  _selectAutocompleteOption(option) {
    const autocomplete = option.closest("[data-autocomplete]");
    const input = autocomplete?.querySelector("[data-autocomplete-input]");
    if (!input) return;
    input.value = option.dataset.entityLabel || "";
    input.dataset.selectedId = option.dataset.entityId || "";
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    this._closeAutocomplete(autocomplete);
  }

  _closeAutocomplete(autocomplete) {
    const menu = autocomplete?.querySelector("[data-autocomplete-menu]");
    const input = autocomplete?.querySelector("[data-autocomplete-input]");
    if (menu) menu.hidden = true;
    input?.setAttribute("aria-expanded", "false");
  }

  _addMappingRow() {
    const list = this._zoneDialogElement()?.querySelector("[data-mapping-list]");
    if (!list) return;
    list.insertAdjacentHTML("beforeend", this._mappingRow({}, list.children.length));
    this._renumberMappingRows(list);
    list.lastElementChild?.querySelector('[name="mapping_name"]')?.focus();
  }

  _removeMappingRow(button) {
    const list = button.closest("[data-mapping-list]");
    const row = button.closest("[data-mapping-row]");
    if (!list || !row) return;
    if (list.children.length === 1) {
      row.querySelectorAll("input, select").forEach((field) => { field.value = ""; });
      row.querySelectorAll("[data-selected-id]").forEach((field) => { field.dataset.selectedId = ""; });
    } else {
      row.remove();
    }
    this._renumberMappingRows(list);
  }

  _renumberMappingRows(list) {
    [...list.children].forEach((row, index) => {
      row.querySelector(".mapping-order").textContent = String(index + 1);
    });
  }

  _updateDurationPreview(form) {
    const output = form?.querySelector("[data-duration-preview]");
    if (!output) return;
    output.textContent = this._formatDuration(
      this._durationBetween(form.elements.start.value, form.elements.end.value)
    );
  }

  _durationBetween(start, end) {
    const startSeconds = this._timeToSeconds(start);
    const endSeconds = this._timeToSeconds(end);
    if (startSeconds == null || endSeconds == null) return 0;
    return (endSeconds - startSeconds + 86400) % 86400 || 86400;
  }

  _scheduleEnd(schedule) {
    const startSeconds = this._timeToSeconds(schedule.time || schedule.start);
    if (startSeconds == null) return "--:--";
    const durationSeconds = Math.max(1, Math.round(Number(schedule.duration || 0)));
    return this._secondsToTime(startSeconds + durationSeconds);
  }

  _timeToSeconds(value) {
    const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(value || ""));
    if (!match) return null;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    const seconds = Number(match[3] || 0);
    if (hours > 23 || minutes > 59 || seconds > 59) return null;
    return hours * 3600 + minutes * 60 + seconds;
  }

  _secondsToTime(value) {
    const seconds = ((Number(value) % 86400) + 86400) % 86400;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    const base = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
    return rest ? `${base}:${String(rest).padStart(2, "0")}` : base;
  }

  _showDialogError(message) {
    const element = this.shadowRoot.querySelector("[data-dialog-error]");
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  _showZoneError(message) {
    const element = this.shadowRoot.querySelector("[data-zone-error]");
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  _entryId() {
    const entryId = this._state?.attributes?.entry_id || this._config.entry_id;
    if (!entryId) {
      throw new Error("O card precisa apontar para o sensor de uma zona do Light Scheduler.");
    }
    return entryId;
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _syncTimer() {
    const attrs = this._state?.attributes || {};
    const target = attrs.active ? attrs.finishes_at : this._state?.state;
    const hasTarget = Number.isFinite(Date.parse(target || ""));
    if (hasTarget && !this._timer) this._timer = window.setInterval(() => this._tick(), 1000);
    if (!hasTarget) this._clearTimer();
  }

  _clearTimer() {
    if (this._timer) window.clearInterval(this._timer);
    this._timer = undefined;
  }

  _tick() {
    const attrs = this._state?.attributes || {};
    const timing = this._headerTiming(attrs, this._state?.state);
    const countdown = this.shadowRoot.querySelector("[data-header-countdown]");
    if (countdown) countdown.textContent = timing.text;
    const progress = this.shadowRoot.querySelector("[data-header-progress]");
    if (progress) {
      progress.setAttribute("aria-label", timing.label);
      progress.setAttribute("aria-valuenow", String(Math.round(timing.progress)));
      progress.setAttribute("aria-valuetext", timing.text);
    }
    const fill = this.shadowRoot.querySelector("[data-header-progress-fill]");
    if (fill) fill.style.width = `${timing.progress}%`;
  }

  _headerTiming(attrs, sensorState) {
    const active = Boolean(attrs.active);
    const action = active ? "desligar" : "ligar";
    const target = Date.parse(active ? attrs.finishes_at : sensorState);
    if (!Number.isFinite(target)) {
      return { text: `--:-- até ${action}`, label: `Tempo até ${action}`, progress: 0 };
    }

    const remaining = Math.max(0, target - Date.now());
    const start = Date.parse(
      active
        ? attrs.started_at || ""
        : attrs.idle_started_at || attrs.last_finished_at || ""
    );
    const duration = Number.isFinite(start) && target > start
      ? target - start
      : Number.NaN;
    if (!Number.isFinite(duration)) {
      return {
        text: `${this._formatCountdown(remaining)} até ${action}`,
        label: `Tempo até ${action}`,
        progress: 0,
      };
    }
    const elapsed = Math.max(0, Math.min(duration, duration - remaining));
    const progress = Math.round((elapsed / Math.max(1, duration)) * 1000) / 10;
    return {
      text: `${this._formatCountdown(remaining)} até ${action}`,
      label: `Tempo até ${action}`,
      progress,
    };
  }

  _formatDuration(seconds) {
    const minutes = Math.max(1, Math.round(Number(seconds || 0) / 60));
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (!hours) return `${minutes}min`;
    if (!rest) return `${hours}h`;
    return `${hours}h ${rest}min`;
  }

  _formatDays(days) {
    const list = Array.isArray(days) ? [...new Set(days.map(Number))].sort() : [];
    if (list.length === 7 && list.every((value, index) => value === index)) return "todos os dias";
    if (list.length === 5 && list.every((value, index) => value === index)) return "seg–sex";
    const labels = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
    return list.map((day) => labels[day]).filter(Boolean).join(", ") || "sem dias";
  }

  _formatPower(value) {
    const number = this._number(value);
    return `${number.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} W`;
  }

  _formatCountdown(milliseconds) {
    const totalMinutes = Math.max(0, Math.ceil(milliseconds / 60_000));
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }

  _number(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _normalizeSearch(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("pt-BR")
      .trim();
  }

  _styles() {
    return `
      <style>
        :host {
          display: block;
          --ls-blue: var(--primary-color, #2196f3);
          --ls-green: #76d84b;
          --ls-amber: #ffc421;
          --scheduler-header-accent: var(--ls-amber);
          --scheduler-header-accent-rgb: 255, 196, 33;
          --scheduler-state-ok: var(--ls-green);
          --scheduler-state-warning: #ffb300;
          --scheduler-state-critical: var(--error-color, #ff5252);
          --scheduler-state-neutral: var(--secondary-text-color, #a0a0a0);
        }
        * { box-sizing: border-box; }
        ha-card {
          display: block;
          overflow: hidden;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
          --ha-card-border-color: rgba(var(--scheduler-header-accent-rgb), .26);
        }
        ha-card:not(:defined) {
          border: 1px solid var(--ha-card-border-color);
          border-radius: var(--ha-card-border-radius, 12px);
        }
        button, input, select { font: inherit; }
        button { color: inherit; }
        .shell { --shell-inline: 14px; padding: 12px var(--shell-inline) 13px; }
        .loading, .error { padding: 18px; font-size: 14px; }
        .error { color: var(--error-color); }
        .hero-header {
          position: relative;
          margin: -12px calc(-1 * var(--shell-inline)) 0;
          padding: 15px 20px 13px;
          overflow: hidden;
          border-bottom: 1px solid rgba(var(--scheduler-header-accent-rgb), .26);
          background:
            radial-gradient(circle at 0 0, rgba(var(--scheduler-header-accent-rgb), .12), transparent 42%),
            linear-gradient(115deg, rgba(var(--scheduler-header-accent-rgb), .055), rgba(127, 127, 127, .025) 48%, transparent 78%);
        }
        .hero-header::after {
          content: "";
          position: absolute;
          inset: 0;
          pointer-events: none;
          background: linear-gradient(90deg, transparent, rgba(255,255,255,.018), transparent);
        }
        .hero-top { position: relative; z-index: 1; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px; }
        .hero-identity { min-width: 0; display: flex; align-items: center; gap: 11px; }
        .hero-icon {
          width: 46px;
          height: 46px;
          flex: none;
          display: grid;
          place-items: center;
          border: 1px solid rgba(var(--scheduler-header-accent-rgb), .34);
          border-radius: 50%;
          color: var(--scheduler-header-accent);
          background: linear-gradient(145deg, rgba(var(--scheduler-header-accent-rgb), .18), rgba(var(--scheduler-header-accent-rgb), .055));
          box-shadow: 0 0 22px rgba(var(--scheduler-header-accent-rgb), .13), inset 0 1px 0 rgba(255,255,255,.08);
        }
        .hero-icon ha-icon { --mdc-icon-size: 25px; filter: drop-shadow(0 0 6px rgba(var(--scheduler-header-accent-rgb), .35)); }
        .hero-title-group { min-width: 0; }
        .hero-eyebrow { display: block; margin-bottom: 2px; color: var(--scheduler-header-accent); font-size: 9px; line-height: 1.2; font-weight: 800; letter-spacing: 1.25px; text-transform: uppercase; }
        .hero-title-group h2 { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 20px; line-height: 1.2; font-weight: 700; }
        h3 { margin: 0 0 8px 6px; font-size: 13px; line-height: 1.25; font-weight: 600; }
        .hero-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
        .status-chip { height: 26px; padding: 0 9px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; border: 1px solid currentColor; border-radius: 999px; font-size: 10px; font-weight: 700; white-space: nowrap; }
        .status-chip ha-icon { --mdc-icon-size: 14px; }
        .status-chip.enabled { color: var(--scheduler-state-ok); background: rgba(73, 190, 42, .085); box-shadow: inset 0 0 12px rgba(73, 190, 42, .04); }
        .status-chip.disabled { color: var(--scheduler-state-neutral); background: rgba(127,127,127,.06); }
        .icon-button { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: 0; background: transparent; cursor: pointer; border-radius: 50%; }
        .icon-button:hover { background: rgba(127,127,127,.14); }
        .icon-button ha-icon { --mdc-icon-size: 20px; }
        .hero-summary { position: relative; z-index: 1; margin-top: 14px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 18px; }
        .hero-kpi { min-width: 0; }
        .hero-kpi strong { display: block; overflow: hidden; color: var(--primary-text-color); font-size: 22px; line-height: 1.08; font-weight: 750; letter-spacing: -.4px; text-overflow: ellipsis; white-space: nowrap; }
        .hero-countdown { display: block; margin-top: 6px; overflow: hidden; color: var(--secondary-text-color); font-size: 10px; line-height: 1.2; text-overflow: ellipsis; white-space: nowrap; }
        .hero-secondary { min-width: 88px; text-align: right; }
        .hero-secondary span { display: block; color: var(--secondary-text-color); font-size: 9px; line-height: 1.2; letter-spacing: .35px; text-transform: uppercase; }
        .hero-secondary strong { display: block; margin-top: 3px; color: var(--primary-text-color); font-size: 20px; line-height: 1; letter-spacing: -.25px; }
        .hero-timeline { position: relative; z-index: 1; height: 4px; margin-top: 12px; overflow: hidden; border-radius: 999px; background: rgba(127,127,127,.26); }
        .hero-timeline span { display: block; height: 100%; border-radius: inherit; background: var(--scheduler-header-accent); box-shadow: 0 0 8px rgba(var(--scheduler-header-accent-rgb), .32); transition: width .25s linear; }
        .divider { height: 1px; margin: 10px 6px 9px; background: rgba(127,127,127,.16); }
        .section-divider { margin-top: 11px; }
        .lights-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 6px; }
        .light-tile { min-width: 0; height: 52px; padding: 6px 7px; display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: 6px; text-align: left; border: 1px solid rgba(127,127,127,.22); border-radius: 7px; background: rgba(127,127,127,.045); }
        .light-tile:hover { background: rgba(127,127,127,.1); }
        .light-main { min-width: 0; height: 100%; padding: 0; display: grid; grid-template-columns: 30px minmax(0,1fr); align-items: center; gap: 6px; text-align: left; border: 0; background: transparent; cursor: pointer; }
        .light-main:disabled { cursor: default; }
        .light-tile .bulb { --mdc-icon-size: 29px; color: #686a6b; filter: grayscale(1); }
        .light-tile.is-on { border-color: rgba(255,196,33,.25); background: linear-gradient(90deg, rgba(255,196,33,.10), rgba(127,127,127,.035)); }
        .light-tile.is-on .bulb { color: var(--ls-amber); filter: drop-shadow(0 0 6px rgba(255,196,33,.55)); }
        .light-copy { min-width: 0; }
        .light-copy strong, .light-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .light-copy strong { font-size: 11px; line-height: 1.25; }
        .light-copy small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .is-on .light-copy small { color: var(--ls-amber); }
        .is-unavailable .light-main { opacity: .55; }
        .power-pill { height: 25px; padding: 0 6px; display: inline-flex; align-items: center; white-space: nowrap; border: 1px solid rgba(127,127,127,.35); border-radius: 6px; color: var(--secondary-text-color); background: transparent; font-size: 9px; cursor: pointer; }
        .power-pill:hover:not(:disabled) { background: rgba(33,150,243,.10); border-color: var(--ls-blue); }
        .power-pill:disabled { opacity: .5; cursor: default; }
        .power-pill ha-icon { --mdc-icon-size: 12px; }
        .is-on .power-pill { border-color: rgba(255,196,33,.6); color: var(--ls-amber); background: rgba(255,196,33,.06); }
        .empty-lights { width: 100%; min-height: 52px; padding: 7px 10px; display: grid; grid-template-columns: 25px minmax(0,1fr) 20px; align-items: center; gap: 8px; text-align: left; border: 1px dashed rgba(127,127,127,.45); border-radius: 7px; background: transparent; cursor: pointer; }
        .empty-lights > ha-icon { --mdc-icon-size: 20px; color: var(--warning-color, #ff9800); }
        .empty-lights > ha-icon:last-child { color: var(--secondary-text-color); }
        .empty-lights strong, .empty-lights small { display: block; }
        .empty-lights strong { font-size: 11px; }
        .empty-lights small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .schedules { display: grid; gap: 4px; }
        .schedule-row { width: 100%; min-width: 0; height: 35px; padding: 0 8px; display: grid; grid-template-columns: 30px 18px 92px auto minmax(0,1fr) 24px 24px; align-items: center; gap: 6px; text-align: left; border: 1px solid rgba(127,127,127,.22); border-radius: 6px; background: rgba(127,127,127,.04); }
        .schedule-row:hover { background: rgba(127,127,127,.08); }
        .schedule-row.is-disabled { opacity: .55; }
        .schedule-row .clock { --mdc-icon-size: 16px; color: var(--ls-blue); }
        .schedule-row strong { font-size: 11px; }
        .time-range { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; overflow: hidden; }
        .time-range i { color: var(--ls-blue); font-style: normal; font-size: 10px; }
        .schedule-row .days { color: var(--secondary-text-color); font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .toggle { position: relative; width: 30px; height: 18px; flex: none; }
        .toggle input { position: absolute; inset: 0; margin: 0; opacity: 0; cursor: pointer; }
        .toggle input:disabled { cursor: default; }
        .toggle span { position: absolute; inset: 0; border-radius: 999px; background: rgba(127,127,127,.4); transition: background .15s ease; pointer-events: none; }
        .toggle span::before { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.35); transition: transform .15s ease; }
        .toggle input:checked + span { background: var(--ls-green); }
        .toggle input:checked + span::before { transform: translateX(12px); }
        .toggle input:focus-visible + span { outline: 2px solid var(--ls-blue); outline-offset: 2px; }
        .toggle input:disabled + span { opacity: .45; }
        .duration-chip { display: inline-flex; align-items: center; gap: 3px; padding: 3px 8px; border-radius: 999px; background: rgba(33,150,243,.14); color: var(--ls-blue); font-size: 10px; font-weight: 700; white-space: nowrap; }
        .duration-chip ha-icon { --mdc-icon-size: 12px; }
        .row-action { width: 24px; height: 24px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 50%; color: var(--secondary-text-color); background: transparent; cursor: pointer; }
        .row-action:hover { background: rgba(33,150,243,.14); color: var(--ls-blue); }
        .row-action.delete:hover { background: rgba(255,80,80,.12); color: var(--error-color); }
        .row-action ha-icon { --mdc-icon-size: 15px; }
        .empty-schedule { padding: 9px; border: 1px dashed rgba(127,127,127,.35); border-radius: 6px; color: var(--secondary-text-color); text-align: center; font-size: 10px; }
        .add-button { width: 100%; height: 31px; margin-top: 5px; display: flex; align-items: center; justify-content: center; gap: 6px; border: 1px solid rgba(127,127,127,.22); border-radius: 6px; background: transparent; color: var(--ls-blue); font-size: 10px; cursor: pointer; }
        .add-button:hover { background: rgba(33,150,243,.08); }
        .add-button ha-icon { --mdc-icon-size: 17px; }
        dialog { width: min(390px, calc(100vw - 32px)); padding: 0; overflow: visible; border: 1px solid rgba(127,127,127,.35); border-radius: 13px; color: var(--primary-text-color); background: var(--card-background-color, #1c1c1c); box-shadow: 0 18px 60px rgba(0,0,0,.5); }
        dialog::backdrop { background: rgba(0,0,0,.62); backdrop-filter: blur(2px); }
        .zone-dialog { width: min(580px, calc(100vw - 24px)); }
        .power-dialog { width: min(470px, calc(100vw - 32px)); }
        .power-sensor-name { margin: 4px 0 0 6px; color: var(--secondary-text-color); font-size: 9px; }
        .power-chart { min-height: 260px; margin-top: 10px; overflow: hidden; border-radius: 7px; }
        .power-chart > * { display: block; }
        .chart-loading, .chart-error { min-height: 260px; display: grid; place-items: center; color: var(--secondary-text-color); font-size: 11px; }
        .chart-error { color: var(--error-color); }
        .chart-error small { display: block; color: var(--secondary-text-color); }
        .dialog-form { padding: 16px; }
        .dialog-header { display: flex; align-items: center; justify-content: space-between; }
        .dialog-header small { color: var(--secondary-text-color); font-size: 10px; }
        .dialog-header h3 { margin: 2px 0 0; font-size: 18px; }
        .zone-name-field { min-height: 58px; margin: 14px 0 12px; padding: 8px 10px; display: grid; grid-template-columns: 34px minmax(0,1fr) minmax(150px,.75fr); align-items: center; gap: 9px; border: 1px solid rgba(33,150,243,.26); border-radius: 8px; background: rgba(33,150,243,.045); }
        .zone-name-icon { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 8px; color: var(--ls-blue); background: rgba(33,150,243,.13); }
        .zone-name-icon ha-icon { --mdc-icon-size: 18px; }
        .zone-name-copy strong, .zone-name-copy small { display: block; }
        .zone-name-copy strong { font-size: 11px; }
        .zone-name-copy small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; line-height: 1.3; }
        .zone-name-field input { width: 100%; min-width: 0; height: 35px; padding: 0 9px; border: 1px solid rgba(127,127,127,.35); border-radius: 6px; outline: 0; color: var(--primary-text-color); background: var(--card-background-color, #1c1c1c); font-size: 11px; }
        .zone-name-field input:focus { border-color: var(--ls-blue); box-shadow: 0 0 0 2px rgba(33,150,243,.10); }
        .fields { margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .fields label, fieldset legend { color: var(--secondary-text-color); font-size: 11px; }
        .fields input { width: 100%; height: 39px; margin-top: 5px; padding: 0 10px; border: 1px solid rgba(127,127,127,.4); border-radius: 6px; outline: none; color: var(--primary-text-color); background: rgba(127,127,127,.08); }
        .fields input:focus { border-color: var(--ls-blue); }
        .duration-preview { margin-top: 10px; padding: 8px 10px; display: flex; align-items: center; gap: 7px; border-radius: 6px; color: var(--secondary-text-color); background: rgba(33,150,243,.08); font-size: 10px; }
        .duration-preview ha-icon { --mdc-icon-size: 17px; color: var(--ls-blue); }
        .duration-preview strong { color: var(--primary-text-color); }
        .interval-setting { min-height: 52px; margin-top: 10px; padding: 8px 10px; display: grid; grid-template-columns: minmax(0,1fr) 82px; align-items: center; gap: 12px; border: 1px solid rgba(127,127,127,.24); border-radius: 6px; }
        .interval-setting > span:first-child strong, .interval-setting > span:first-child small { display: block; }
        .interval-setting > span:first-child strong { font-size: 11px; }
        .interval-setting > span:first-child small { margin-top: 3px; color: var(--secondary-text-color); font-size: 9px; line-height: 1.35; }
        .interval-input { height: 34px; display: grid; grid-template-columns: minmax(0,1fr) 28px; align-items: center; overflow: hidden; border: 1px solid rgba(127,127,127,.35); border-radius: 5px; }
        .interval-input:focus-within { border-color: var(--ls-blue); }
        .interval-input input { width: 100%; min-width: 0; height: 100%; padding: 0 5px 0 8px; border: 0; outline: 0; color: var(--primary-text-color); background: transparent; }
        .interval-input b { color: var(--secondary-text-color); font-size: 9px; font-weight: 400; }
        .lights-picker { margin-top: 10px; border: 1px solid rgba(127,127,127,.24); border-radius: 6px; overflow: hidden; }
        .lights-picker.is-empty { padding: 10px; color: var(--secondary-text-color); text-align: center; font-size: 10px; }
        .lights-summary { width: 100%; min-height: 52px; padding: 8px 10px; display: grid; grid-template-columns: 20px minmax(0,1fr) 18px; align-items: center; gap: 9px; text-align: left; border: 0; background: transparent; cursor: pointer; }
        .lights-summary:hover { background: rgba(127,127,127,.07); }
        .lights-summary > ha-icon:first-child { --mdc-icon-size: 19px; color: var(--ls-blue); }
        .lights-summary strong, .lights-summary small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .lights-summary strong { font-size: 11px; }
        .lights-summary small { margin-top: 3px; color: var(--secondary-text-color); font-size: 9px; }
        .lights-summary .chevron { --mdc-icon-size: 18px; color: var(--secondary-text-color); transition: transform .15s ease; }
        .lights-picker.is-open .chevron { transform: rotate(180deg); }
        .lights-options { padding: 2px 10px 9px; border-top: 1px solid rgba(127,127,127,.2); }
        .lights-options[hidden] { display: none; }
        .light-choice { min-height: 34px; display: flex; align-items: center; gap: 9px; cursor: pointer; }
        .light-choice input { width: 16px; height: 16px; flex: none; accent-color: var(--ls-blue); }
        .light-choice-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
        .lights-bulk { margin-top: 6px; padding-top: 7px; display: flex; gap: 6px; border-top: 1px solid rgba(127,127,127,.16); }
        .lights-bulk button { height: 26px; padding: 0 10px; border: 1px solid rgba(127,127,127,.32); border-radius: 5px; background: transparent; color: var(--ls-blue); font-size: 10px; cursor: pointer; }
        .lights-bulk button:hover { background: rgba(33,150,243,.1); }
        fieldset { margin: 16px 0 0; padding: 0; border: 0; }
        .day-grid { margin-top: 7px; display: grid; grid-template-columns: repeat(7,1fr); gap: 4px; }
        .day-grid input { position: absolute; opacity: 0; pointer-events: none; }
        .day-grid span { height: 31px; display: grid; place-items: center; border: 1px solid rgba(127,127,127,.35); border-radius: 5px; color: var(--secondary-text-color); font-size: 10px; cursor: pointer; }
        .day-grid input:checked + span { border-color: var(--ls-blue); color: var(--ls-blue); background: rgba(33,150,243,.1); }
        .dialog-error { margin: 12px 0 0; color: var(--error-color); font-size: 11px; }
        .dialog-actions { margin-top: 18px; display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 8px; }
        .dialog-actions button { height: 34px; padding: 0 12px; border-radius: 6px; cursor: pointer; }
        .delete-button { display: flex; align-items: center; gap: 5px; padding-left: 0 !important; border: 0; color: var(--error-color); background: transparent; }
        .delete-button[hidden] { display: none; }
        .delete-button ha-icon { --mdc-icon-size: 16px; }
        .mapping-header, .mapping-row { display: grid; grid-template-columns: 24px minmax(90px,.7fr) minmax(140px,1.2fr) minmax(130px,1fr) 28px; align-items: center; gap: 6px; }
        .mapping-header { padding: 0 8px 5px; color: var(--secondary-text-color); font-size: 9px; }
        .mapping-list { max-height: min(390px, 48vh); overflow-y: auto; display: grid; gap: 5px; }
        .mapping-row { min-height: 48px; padding: 6px 7px; border: 1px solid rgba(127,127,127,.23); border-radius: 7px; background: rgba(127,127,127,.035); }
        .mapping-order { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; color: var(--secondary-text-color); background: rgba(127,127,127,.14); font-size: 9px; }
        .mapping-row input { width: 100%; min-width: 0; height: 34px; padding: 0 8px; overflow: hidden; text-overflow: ellipsis; border: 1px solid rgba(127,127,127,.32); border-radius: 5px; outline: 0; color: var(--primary-text-color); background: var(--card-background-color, #1c1c1c); font-size: 10px; }
        .mapping-row input:focus { border-color: var(--ls-blue); }
        .entity-autocomplete { min-width: 0; }
        .autocomplete-input { padding-right: 25px !important; background-image: linear-gradient(45deg,transparent 50%,var(--secondary-text-color) 50%),linear-gradient(135deg,var(--secondary-text-color) 50%,transparent 50%); background-position: calc(100% - 12px) 14px,calc(100% - 8px) 14px; background-size: 4px 4px,4px 4px; background-repeat: no-repeat; }
        .autocomplete-menu { position: fixed; z-index: 10000; max-height: min(260px, 42vh); padding: 4px; overflow-y: auto; border: 1px solid rgba(127,127,127,.42); border-radius: 7px; background: var(--card-background-color, #1c1c1c); box-shadow: 0 10px 30px rgba(0,0,0,.5); }
        .autocomplete-menu[hidden] { display: none; }
        .autocomplete-menu button { width: 100%; min-height: 42px; padding: 6px 8px; display: block; overflow: hidden; text-align: left; border: 0; border-radius: 5px; background: transparent; cursor: pointer; }
        .autocomplete-menu button:hover, .autocomplete-menu button:focus { outline: 0; background: rgba(33,150,243,.14); }
        .autocomplete-menu strong, .autocomplete-menu small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .autocomplete-menu strong { font-size: 10px; }
        .autocomplete-menu small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .autocomplete-empty { padding: 12px 8px; color: var(--secondary-text-color); text-align: center; font-size: 10px; }
        .remove-mapping-button { width: 28px; height: 28px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 50%; color: var(--secondary-text-color); background: transparent; cursor: pointer; }
        .remove-mapping-button:hover { color: var(--error-color); background: rgba(255,80,80,.08); }
        .remove-mapping-button ha-icon { --mdc-icon-size: 17px; }
        .add-mapping-button { width: 100%; height: 34px; margin-top: 7px; display: flex; align-items: center; justify-content: center; gap: 6px; border: 1px dashed rgba(33,150,243,.55); border-radius: 6px; color: var(--ls-blue); background: rgba(33,150,243,.04); font-size: 10px; cursor: pointer; }
        .add-mapping-button:hover { background: rgba(33,150,243,.1); }
        .add-mapping-button ha-icon { --mdc-icon-size: 17px; }
        .schedule-row.has-warning { box-shadow: inset 2px 0 0 var(--ls-amber); }
        .row-warning { color: var(--ls-amber); --mdc-icon-size: 15px; }
        .zone-help { margin-top: 9px; color: var(--secondary-text-color); font-size: 9px; line-height: 1.4; }
        .zone-actions { grid-template-columns: auto 1fr auto auto; }
        .advanced-button { padding-left: 0 !important; border: 0; color: var(--ls-blue); background: transparent; font-size: 10px; }
        .cancel-button { border: 1px solid rgba(127,127,127,.35); background: transparent; }
        .save-button { border: 1px solid var(--ls-blue); background: var(--ls-blue); color: white; }
        @media (max-width: 390px) {
          .shell { --shell-inline: 10px; }
          .hero-header { padding-inline: 14px; }
          .hero-top { gap: 8px; }
          .hero-identity { gap: 8px; }
          .hero-icon { width: 40px; height: 40px; }
          .hero-icon ha-icon { --mdc-icon-size: 22px; }
          .hero-actions { gap: 5px; }
          .status-chip { width: 28px; padding: 0; }
          .status-chip > span { display: none; }
          .hero-kpi strong { font-size: 19px; }
          .hero-secondary strong { font-size: 18px; }
          .power-pill { display: none; }
          .schedule-row { grid-template-columns: 26px 16px 84px auto minmax(0,1fr) 22px 22px; padding-inline: 6px; gap: 4px; }
        }
        @media (max-width: 560px) {
          .zone-name-field { grid-template-columns: 34px minmax(0,1fr); }
          .zone-name-field input { grid-column: 1 / -1; }
          .mapping-header { display: none; }
          .mapping-row { grid-template-columns: 24px minmax(0,1fr) minmax(0,1fr) 28px; }
          .mapping-order { grid-row: 1 / 3; }
          .mapping-name { grid-column: 2 / 4; }
          .mapping-row .entity-autocomplete:first-of-type { grid-column: 2; }
          .mapping-row .entity-autocomplete:last-of-type { grid-column: 3; }
          .remove-mapping-button { grid-column: 4; grid-row: 1 / 3; }
        }
        @media (prefers-reduced-motion: reduce) {
          .hero-timeline span { transition: none; }
        }
      </style>
    `;
  }
}

if (!customElements.get("light-schedule-card")) {
  customElements.define("light-schedule-card", LightScheduleCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "light-schedule-card")) {
  window.customCards.push({
    type: "light-schedule-card",
    name: "Light Scheduler",
    description: "Controle compacto de luzes, potência e horários por sala.",
    preview: true,
    documentationURL: "https://github.com/chadalau/hassio-light-scheduler",
  });
}

console.info(`%c LIGHT-SCHEDULE-CARD %c ${CARD_VERSION} `, "color:white;background:#2196f3;font-weight:700", "color:#2196f3;background:#e8f4ff");
