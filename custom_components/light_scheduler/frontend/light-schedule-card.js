const CARD_VERSION = "0.2.1";

class LightScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = {};
    this._timer = undefined;
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("input", (event) => this._handleInput(event));
  }

  static getStubConfig() {
    return { entity: "sensor.light_scheduler" };
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("Defina a entidade sensor da zona em 'entity'.");
    }
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
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
    const active = Boolean(attrs.active);
    const onCount = Number(attrs.lights_on ?? attrs.on_count ?? 0);
    const total = Number(attrs.total_lights ?? lights.length ?? 0);
    const power = this._number(attrs.total_power_w);
    const zoneName = attrs.zone_name || attrs.friendly_name || "Sala";

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        <section class="shell">
          <header class="header">
            <div class="room-icon"><ha-icon icon="mdi:sofa-single"></ha-icon></div>
            <h2 title="${this._escape(zoneName)}">${this._escape(zoneName)}</h2>
            <span class="status-chip ${enabled ? "enabled" : "disabled"}">
              <ha-icon icon="mdi:calendar-check-outline"></ha-icon>
              ${enabled ? "Agendada" : "Pausada"}
            </span>
            <button class="icon-button settings" type="button" data-action="open-zone-dialog" aria-label="Configurar zona" title="Configurar zona">
              <ha-icon icon="mdi:cog-outline"></ha-icon>
            </button>
          </header>

          <div class="summary">
            <div>
              <strong>${onCount} de ${total} ${total === 1 ? "luz ligada" : "luzes ligadas"}</strong>
              <span>Próxima ação: ${this._escape(this._formatNext(attrs, state.state))}</span>
            </div>
            <div class="power-total">
              <span>Potência total</span>
              <strong>${this._formatPower(power)}</strong>
            </div>
          </div>

          ${active ? this._activeRun(attrs) : ""}

          <div class="divider"></div>
          <h3>Luzes da sala</h3>
          ${lights.length ? `<div class="lights-grid">${lights.map((light) => this._lightTile(light)).join("")}</div>` : this._emptyLights()}

          <div class="divider section-divider"></div>
          <h3>Agenda automática</h3>
          <div class="schedules">
            ${schedules.length ? schedules.map((schedule) => this._scheduleRow(schedule)).join("") : `<div class="empty-schedule">Nenhum agendamento criado.</div>`}
          </div>
          <button class="add-button" type="button" data-action="add-schedule">
            <ha-icon icon="mdi:plus"></ha-icon>
            Adicionar agendamento
          </button>
        </section>
        ${this._scheduleDialog()}
        ${this._zoneDialog()}
      </ha-card>
    `;

    this._syncTimer();
    this._tick();
  }

  _activeRun(attrs) {
    const source = (attrs.source || attrs.active_source) === "manual" ? "Acionamento manual" : `Ligada desde ${this._formatClock(attrs.started_at || attrs.active_start)}`;
    return `
      <div class="run-status">
        <div class="run-line">
          <span><i></i>${this._escape(source)}</span>
          <span class="countdown" data-countdown>--:--:-- restantes</span>
        </div>
        <div class="run-controls">
          <div class="progress"><span data-progress></span></div>
          <button type="button" data-action="stop-now"><ha-icon icon="mdi:stop"></ha-icon>Desligar</button>
        </div>
      </div>
    `;
  }

  _lightTile(light) {
    const entityId = String(light.entity_id || "");
    const isOn = light.state === "on";
    const unavailable = light.available === false || ["unavailable", "unknown"].includes(light.state);
    const power = this._number(light.power_w);
    const status = unavailable ? "Indisponível" : isOn ? "Ligada" : "Desligada";
    return `
      <button class="light-tile ${isOn ? "is-on" : ""} ${unavailable ? "is-unavailable" : ""}" type="button"
        data-action="toggle-light" data-entity-id="${this._escape(entityId)}" ${unavailable ? "disabled" : ""}
        aria-label="Alternar ${this._escape(light.name || entityId)}">
        <ha-icon class="bulb" icon="mdi:lightbulb"></ha-icon>
        <span class="light-copy">
          <strong title="${this._escape(light.name || entityId)}">${this._escape(light.name || entityId)}</strong>
          <small>${status}</small>
        </span>
        <span class="power-pill"><ha-icon icon="mdi:flash"></ha-icon>${this._formatPower(power)}</span>
      </button>
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

  _scheduleRow(schedule) {
    const id = String(schedule.id || "");
    return `
      <button class="schedule-row" type="button" data-action="edit-schedule" data-schedule-id="${this._escape(id)}">
        <ha-icon class="clock" icon="mdi:clock-outline"></ha-icon>
        <strong>${this._escape(schedule.time || schedule.start || "--:--")}</strong>
        <b>•</b>
        <span class="duration">${this._escape(this._formatDuration(schedule.duration))}</span>
        <b>•</b>
        <span class="days" title="${this._escape(this._formatDays(schedule.days))}">${this._escape(this._formatDays(schedule.days))}</span>
        <ha-icon class="edit" icon="mdi:pencil"></ha-icon>
      </button>
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
            <label>Horário<input name="start" type="time" required value="18:30"></label>
            <label>Duração (minutos)<input name="duration" type="number" required min="1" max="1440" step="1" value="240"></label>
          </div>
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

  _zoneDialog() {
    const selected = new Set(this._state?.attributes?.target_entity_ids || []);
    const entities = Object.values(this._hass?.states || {})
      .filter(
        (state) =>
          ["light", "switch"].includes(state.entity_id?.split(".")[0]) &&
          !state.attributes?.entry_id
      )
      .sort((a, b) => (a.attributes?.friendly_name || a.entity_id).localeCompare(b.attributes?.friendly_name || b.entity_id, "pt-BR"));
    return `
      <dialog class="zone-dialog">
        <form method="dialog" class="dialog-form">
          <div class="dialog-header">
            <div><small>Configuração da zona</small><h3>Escolher luzes e tomadas</h3></div>
            <button class="icon-button" type="button" data-action="close-zone-dialog" aria-label="Fechar"><ha-icon icon="mdi:close"></ha-icon></button>
          </div>
          <label class="entity-search">
            <ha-icon icon="mdi:magnify"></ha-icon>
            <input type="search" data-zone-search placeholder="Pesquisar entidade">
          </label>
          <div class="entity-list">
            ${entities.length ? entities.map((state) => {
              const entityId = state.entity_id;
              const name = state.attributes?.friendly_name || entityId;
              return `
                <label class="entity-option" data-search-value="${this._escape(`${name} ${entityId}`.toLowerCase())}">
                  <input type="checkbox" name="zone_target" value="${this._escape(entityId)}" ${selected.has(entityId) ? "checked" : ""}>
                  <ha-icon icon="${entityId.startsWith("light.") ? "mdi:lightbulb" : "mdi:power-socket"}"></ha-icon>
                  <span><strong>${this._escape(name)}</strong><small>${this._escape(entityId)}</small></span>
                  <ha-icon class="check-icon" icon="mdi:check-circle"></ha-icon>
                </label>
              `;
            }).join("") : `<div class="empty-entities">Nenhuma entidade light ou switch encontrada.</div>`}
          </div>
          <p class="dialog-error" data-zone-error hidden></p>
          <div class="zone-help">Os sensores de potência serão associados automaticamente quando estiverem no mesmo dispositivo.</div>
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

  async _handleClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || target.disabled) return;
    const action = target.dataset.action;

    try {
      if (action === "toggle-light") {
        await this._hass.callService("homeassistant", "toggle", { entity_id: target.dataset.entityId });
      } else if (action === "stop-now") {
        await this._hass.callService("light_scheduler", "stop", { entry_id: this._entryId() });
      } else if (action === "open-zone-dialog") {
        this._openZoneDialog();
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
      }
    } catch (error) {
      this._showDialogError(error?.message || String(error));
    }
  }

  _openDialog(schedule) {
    const dialog = this._dialog();
    if (!dialog) return;
    const form = dialog.querySelector("form");
    form.reset();
    form.elements.schedule_id.value = schedule?.id || "";
    form.elements.start.value = schedule?.time || schedule?.start || "18:30";
    form.elements.duration.value = Math.max(1, Math.round(Number(schedule?.duration || 14400) / 60));
    const days = Array.isArray(schedule?.days) ? schedule.days.map(Number) : [0, 1, 2, 3, 4, 5, 6];
    form.querySelectorAll('input[name="day"]').forEach((input) => { input.checked = days.includes(Number(input.value)); });
    dialog.querySelector("[data-dialog-title]").textContent = schedule ? "Editar agendamento" : "Novo agendamento";
    dialog.querySelector("[data-action='delete-schedule']").hidden = !schedule;
    this._showDialogError("");
    dialog.showModal();
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
    const data = {
      entry_id: this._entryId(),
      time: form.elements.start.value,
      duration: Math.round(Number(form.elements.duration.value) * 60),
      days,
    };
    const scheduleId = form.elements.schedule_id.value;
    if (scheduleId) data.id = scheduleId;
    await this._hass.callService("light_scheduler", scheduleId ? "update_schedule" : "add_schedule", data);
    dialog.close();
  }

  async _deleteSchedule() {
    const dialog = this._dialog();
    const scheduleId = dialog?.querySelector('[name="schedule_id"]')?.value;
    if (!scheduleId || !window.confirm("Excluir este agendamento?")) return;
    await this._hass.callService("light_scheduler", "remove_schedule", { entry_id: this._entryId(), id: scheduleId });
    dialog.close();
  }

  _dialog() {
    return this.shadowRoot.querySelector(".schedule-dialog");
  }

  _zoneDialogElement() {
    return this.shadowRoot.querySelector(".zone-dialog");
  }

  _openZoneDialog() {
    const dialog = this._zoneDialogElement();
    if (!dialog) return;
    const error = dialog.querySelector("[data-zone-error]");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    dialog.showModal();
  }

  async _saveZone() {
    const dialog = this._zoneDialogElement();
    const targets = [...dialog.querySelectorAll('input[name="zone_target"]:checked')].map((input) => input.value);
    const error = dialog.querySelector("[data-zone-error]");
    if (!targets.length) {
      error.textContent = "Selecione pelo menos uma luz ou tomada.";
      error.hidden = false;
      return;
    }
    await this._hass.callService("light_scheduler", "set_zone_options", {
      entry_id: this._entryId(),
      target_entity_ids: targets,
    });
    dialog.close();
  }

  _handleInput(event) {
    if (!event.target.matches("[data-zone-search]")) return;
    const query = event.target.value.trim().toLocaleLowerCase("pt-BR");
    this.shadowRoot.querySelectorAll(".entity-option").forEach((option) => {
      option.hidden = Boolean(query) && !option.dataset.searchValue.includes(query);
    });
  }

  _showDialogError(message) {
    const element = this.shadowRoot.querySelector("[data-dialog-error]");
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  _entryId() {
    return this._state?.attributes?.entry_id || this._config.entry_id;
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _syncTimer() {
    const attrs = this._state?.attributes || {};
    const active = Boolean(attrs.active && (attrs.finishes_at || attrs.active_end));
    if (active && !this._timer) this._timer = window.setInterval(() => this._tick(), 1000);
    if (!active) this._clearTimer();
  }

  _clearTimer() {
    if (this._timer) window.clearInterval(this._timer);
    this._timer = undefined;
  }

  _tick() {
    const attrs = this._state?.attributes || {};
    const end = Date.parse(attrs.finishes_at || attrs.active_end || "");
    const start = Date.parse(attrs.started_at || attrs.active_start || "");
    if (!Number.isFinite(end)) return;
    const remaining = Math.max(0, end - Date.now());
    const countdown = this.shadowRoot.querySelector("[data-countdown]");
    if (countdown) countdown.textContent = `${this._formatCountdown(remaining)} restantes`;
    const progress = this.shadowRoot.querySelector("[data-progress]");
    if (progress) {
      const duration = Math.max(1, end - start);
      const elapsed = Math.max(0, Math.min(duration, Date.now() - start));
      progress.style.width = `${(elapsed / duration) * 100}%`;
    }
  }

  _formatNext(attrs, sensorState) {
    const finish = attrs.finishes_at || attrs.active_end;
    if (attrs.active && finish) return `desligar às ${this._formatClock(finish)}`;
    const next = attrs.next_run || (!["unknown", "unavailable", "none"].includes(String(sensorState).toLowerCase()) ? sensorState : null);
    if (!next) return "nenhuma ação programada";
    const date = new Date(next);
    if (Number.isNaN(date.getTime())) return String(next);
    const now = new Date();
    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const prefix = sameDay(date, now) ? "hoje" : sameDay(date, tomorrow) ? "amanhã" : date.toLocaleDateString("pt-BR", { weekday: "long" });
    return `${prefix}, ${date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
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
    const total = Math.max(0, Math.floor(milliseconds / 1000));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
  }

  _formatClock(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--:--";
    return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
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

  _styles() {
    return `
      <style>
        :host { display: block; --ls-blue: var(--primary-color, #2196f3); --ls-green: #76d84b; --ls-amber: #ffc421; }
        * { box-sizing: border-box; }
        ha-card { overflow: hidden; color: var(--primary-text-color); background: var(--ha-card-background, var(--card-background-color)); }
        button, input { font: inherit; }
        button { color: inherit; }
        .shell { padding: 12px 14px 13px; }
        .loading, .error { padding: 18px; font-size: 14px; }
        .error { color: var(--error-color); }
        .header { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto 30px; align-items: center; gap: 8px; }
        .room-icon { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: rgba(128,128,128,.22); color: var(--secondary-text-color); }
        .room-icon ha-icon { --mdc-icon-size: 20px; }
        h2 { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 20px; line-height: 1.2; font-weight: 700; }
        h3 { margin: 0 0 8px 6px; font-size: 13px; line-height: 1.25; font-weight: 600; }
        .status-chip { height: 24px; padding: 0 8px; display: inline-flex; align-items: center; gap: 4px; border: 1px solid currentColor; border-radius: 5px; font-size: 11px; font-weight: 600; }
        .status-chip ha-icon { --mdc-icon-size: 14px; }
        .status-chip.enabled { color: var(--ls-green); background: rgba(73, 190, 42, .09); }
        .status-chip.disabled { color: var(--secondary-text-color); }
        .icon-button { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: 0; background: transparent; cursor: pointer; border-radius: 50%; }
        .icon-button:hover { background: rgba(127,127,127,.14); }
        .icon-button ha-icon { --mdc-icon-size: 20px; }
        .summary { margin: 12px 6px 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 14px; }
        .summary > div:first-child { min-width: 0; }
        .summary strong { display: block; font-size: 22px; line-height: 1.1; letter-spacing: -.35px; }
        .summary span { display: block; margin-top: 5px; color: var(--secondary-text-color); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .power-total { text-align: right; min-width: 78px; }
        .power-total span { margin: 0; font-size: 10px; }
        .power-total strong { margin-top: 1px; font-size: 21px; }
        .run-status { margin: 10px 6px 0; }
        .run-line { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--secondary-text-color); font-size: 10px; }
        .run-line span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .run-line i { display: inline-block; width: 7px; height: 7px; margin-right: 5px; border-radius: 50%; background: var(--ls-green); }
        .run-line .countdown { color: var(--ls-blue); white-space: nowrap; }
        .run-controls { margin-top: 7px; display: grid; grid-template-columns: minmax(0,1fr) 88px; align-items: center; gap: 10px; }
        .progress { height: 6px; border-radius: 999px; overflow: hidden; background: rgba(127,127,127,.28); }
        .progress span { display: block; height: 100%; border-radius: inherit; background: var(--ls-blue); }
        .run-controls button { height: 31px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; border: 1px solid rgba(127,127,127,.32); border-radius: 5px; background: transparent; color: var(--ls-blue); font-size: 11px; cursor: pointer; }
        .run-controls ha-icon { --mdc-icon-size: 13px; }
        .divider { height: 1px; margin: 10px 6px 9px; background: rgba(127,127,127,.16); }
        .section-divider { margin-top: 11px; }
        .lights-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 6px; }
        .light-tile { min-width: 0; height: 52px; padding: 6px 7px; display: grid; grid-template-columns: 30px minmax(0,1fr) auto; align-items: center; gap: 6px; text-align: left; border: 1px solid rgba(127,127,127,.22); border-radius: 7px; background: rgba(127,127,127,.045); cursor: pointer; }
        .light-tile:hover { background: rgba(127,127,127,.1); }
        .light-tile .bulb { --mdc-icon-size: 29px; color: #686a6b; filter: grayscale(1); }
        .light-tile.is-on { border-color: rgba(255,196,33,.25); background: linear-gradient(90deg, rgba(255,196,33,.10), rgba(127,127,127,.035)); }
        .light-tile.is-on .bulb { color: var(--ls-amber); filter: drop-shadow(0 0 6px rgba(255,196,33,.55)); }
        .light-copy { min-width: 0; }
        .light-copy strong, .light-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .light-copy strong { font-size: 11px; line-height: 1.25; }
        .light-copy small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .is-on .light-copy small { color: var(--ls-amber); }
        .is-unavailable { opacity: .55; cursor: default; }
        .power-pill { height: 25px; padding: 0 6px; display: inline-flex; align-items: center; white-space: nowrap; border: 1px solid rgba(127,127,127,.35); border-radius: 6px; color: var(--secondary-text-color); font-size: 9px; }
        .power-pill ha-icon { --mdc-icon-size: 12px; }
        .is-on .power-pill { border-color: rgba(255,196,33,.6); color: var(--ls-amber); background: rgba(255,196,33,.06); }
        .empty-lights { width: 100%; min-height: 52px; padding: 7px 10px; display: grid; grid-template-columns: 25px minmax(0,1fr) 20px; align-items: center; gap: 8px; text-align: left; border: 1px dashed rgba(127,127,127,.45); border-radius: 7px; background: transparent; cursor: pointer; }
        .empty-lights > ha-icon { --mdc-icon-size: 20px; color: var(--warning-color, #ff9800); }
        .empty-lights > ha-icon:last-child { color: var(--secondary-text-color); }
        .empty-lights strong, .empty-lights small { display: block; }
        .empty-lights strong { font-size: 11px; }
        .empty-lights small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .schedules { display: grid; gap: 4px; }
        .schedule-row { width: 100%; min-width: 0; height: 35px; padding: 0 8px; display: grid; grid-template-columns: 19px 43px 8px 48px 8px minmax(0,1fr) 20px; align-items: center; gap: 3px; text-align: left; border: 1px solid rgba(127,127,127,.22); border-radius: 6px; background: rgba(127,127,127,.04); cursor: pointer; }
        .schedule-row:hover { background: rgba(127,127,127,.1); }
        .schedule-row .clock, .schedule-row .edit { --mdc-icon-size: 16px; color: var(--ls-blue); }
        .schedule-row strong { font-size: 11px; }
        .schedule-row b { color: var(--ls-blue); text-align: center; font-size: 10px; }
        .schedule-row .duration, .schedule-row .days { color: var(--secondary-text-color); font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .schedule-row .edit { justify-self: end; }
        .empty-schedule { padding: 9px; border: 1px dashed rgba(127,127,127,.35); border-radius: 6px; color: var(--secondary-text-color); text-align: center; font-size: 10px; }
        .add-button { width: 100%; height: 31px; margin-top: 5px; display: flex; align-items: center; justify-content: center; gap: 6px; border: 1px solid rgba(127,127,127,.22); border-radius: 6px; background: transparent; color: var(--ls-blue); font-size: 10px; cursor: pointer; }
        .add-button:hover { background: rgba(33,150,243,.08); }
        .add-button ha-icon { --mdc-icon-size: 17px; }
        dialog { width: min(390px, calc(100vw - 32px)); padding: 0; border: 1px solid rgba(127,127,127,.35); border-radius: 13px; color: var(--primary-text-color); background: var(--card-background-color, #1c1c1c); box-shadow: 0 18px 60px rgba(0,0,0,.5); }
        dialog::backdrop { background: rgba(0,0,0,.62); backdrop-filter: blur(2px); }
        .dialog-form { padding: 16px; }
        .dialog-header { display: flex; align-items: center; justify-content: space-between; }
        .dialog-header small { color: var(--secondary-text-color); font-size: 10px; }
        .dialog-header h3 { margin: 2px 0 0; font-size: 18px; }
        .fields { margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .fields label, fieldset legend { color: var(--secondary-text-color); font-size: 11px; }
        .fields input { width: 100%; height: 39px; margin-top: 5px; padding: 0 10px; border: 1px solid rgba(127,127,127,.4); border-radius: 6px; outline: none; color: var(--primary-text-color); background: rgba(127,127,127,.08); }
        .fields input:focus { border-color: var(--ls-blue); }
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
        .entity-search { height: 39px; margin-top: 14px; padding: 0 10px; display: flex; align-items: center; gap: 7px; border: 1px solid rgba(127,127,127,.4); border-radius: 6px; background: rgba(127,127,127,.08); }
        .entity-search:focus-within { border-color: var(--ls-blue); }
        .entity-search ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .entity-search input { width: 100%; border: 0; outline: 0; color: var(--primary-text-color); background: transparent; }
        .entity-list { max-height: min(360px, 48vh); margin-top: 10px; overflow-y: auto; display: grid; gap: 4px; }
        .entity-option { min-height: 46px; padding: 6px 9px; display: grid; grid-template-columns: 20px 22px minmax(0,1fr) 20px; align-items: center; gap: 7px; border: 1px solid rgba(127,127,127,.2); border-radius: 6px; cursor: pointer; }
        .entity-option[hidden] { display: none; }
        .entity-option:hover { background: rgba(127,127,127,.08); }
        .entity-option input { width: 15px; height: 15px; accent-color: var(--ls-blue); }
        .entity-option > ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .entity-option span { min-width: 0; }
        .entity-option strong, .entity-option small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .entity-option strong { font-size: 11px; }
        .entity-option small { margin-top: 2px; color: var(--secondary-text-color); font-size: 9px; }
        .entity-option .check-icon { color: transparent; }
        .entity-option:has(input:checked) { border-color: rgba(33,150,243,.55); background: rgba(33,150,243,.08); }
        .entity-option:has(input:checked) .check-icon { color: var(--ls-blue); }
        .empty-entities { padding: 16px; color: var(--secondary-text-color); text-align: center; font-size: 11px; }
        .zone-help { margin-top: 9px; color: var(--secondary-text-color); font-size: 9px; line-height: 1.4; }
        .zone-actions { grid-template-columns: auto 1fr auto auto; }
        .advanced-button { padding-left: 0 !important; border: 0; color: var(--ls-blue); background: transparent; font-size: 10px; }
        .cancel-button { border: 1px solid rgba(127,127,127,.35); background: transparent; }
        .save-button { border: 1px solid var(--ls-blue); background: var(--ls-blue); color: white; }
        @media (max-width: 390px) {
          .shell { padding-inline: 10px; }
          .summary strong { font-size: 20px; }
          .power-total strong { font-size: 19px; }
          .light-tile { grid-template-columns: 27px minmax(0,1fr); }
          .power-pill { display: none; }
          .schedule-row { grid-template-columns: 18px 41px 7px 42px 7px minmax(0,1fr) 18px; padding-inline: 6px; }
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
