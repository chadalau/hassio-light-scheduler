const TAG = "light-schedule-card";
const weekdays = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

const css = `
  :host{display:block} ha-card{background:#111;color:#f5f5f5;border:1px solid #3c3c3c;border-radius:22px;padding:22px;font-family:var(--primary-font-family,Arial);box-sizing:border-box}.top{display:flex;align-items:center;gap:12px}.room{font-size:29px;font-weight:700;flex:1}.icon-button,.chip,button{font:inherit}.icon-button{background:none;border:0;color:#b6b6b6;font-size:24px;cursor:pointer}.chip{border:2px solid #438f37;color:#71df54;border-radius:11px;padding:8px 10px;font-weight:600}.summary{display:grid;grid-template-columns:1fr auto;gap:6px;margin:24px 0 18px}.amount{font-size:29px;font-weight:700}.next,.muted{color:#b6b6b6}.power{text-align:right}.power b{display:block;font-size:31px}.live{border-top:1px solid #333;border-bottom:1px solid #333;padding:18px 0;overflow:auto}.live-line{display:flex;justify-content:space-between;gap:8px}.green{color:#71df54}.blue{color:#48a4ff}.bar{height:10px;border-radius:8px;background:#3a3a3a;margin:18px 0}.bar i{display:block;height:100%;width:58%;border-radius:8px;background:#3493e8}.off{background:#282828;color:#48a4ff;border:1px solid #525252;padding:10px 15px;border-radius:10px;font-size:16px;float:right;cursor:pointer}.section{margin-top:24px}.section h3{font-size:22px;margin:0 0 14px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.light{display:flex;align-items:center;gap:11px;width:100%;text-align:left;color:#f5f5f5;padding:12px;border:1px solid #3b3b3b;background:#1a1a1a;border-radius:14px;cursor:pointer}.light:hover,.light:focus-visible{border-color:#48a4ff;background:#222}.bulb{font-size:28px}.label{flex:1;min-width:0}.name{font-size:16px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.state{margin-top:4px}.on{color:#ffd142}.watt{border:1px solid #5a5a5a;border-radius:9px;padding:8px;white-space:nowrap;color:#bebebe}.watt.on{border-color:#987600;color:#ffd142}.schedule{display:flex;align-items:center;gap:12px;border:1px solid #3b3b3b;background:#1a1a1a;border-radius:12px;padding:13px;margin-top:8px}.schedule .time{font-size:20px;font-weight:700}.schedule .days{flex:1;color:#bbb}.schedule button{border:0;background:transparent;color:#48a4ff;font-size:18px;cursor:pointer}.schedule .delete{color:#ee6a61}.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.add{border:1px solid #3b3b3b;border-radius:12px;background:#151515;color:#48a4ff;padding:13px;font-size:16px;cursor:pointer}.empty{color:#b6b6b6;border:1px dashed #484848;border-radius:12px;padding:14px}@media(max-width:480px){.grid{grid-template-columns:1fr}.summary{grid-template-columns:1fr}.power{text-align:left}.room{font-size:24px}.schedule{gap:8px}.schedule .days{display:none}}`;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"})[char]);
const timeValue = (value) => String(value ?? "").slice(0, 5);

class LightScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", this._onClick.bind(this));
  }

  static getStubConfig() { return { entity: "sensor.sala_next_run" }; }

  setConfig(config) {
    if (!config.entity) throw new Error("Defina a entidade sensor.<zona>_next_run");
    this.config = config;
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  _schedulerCall(service, data = {}) {
    return this._hass.callService("light_scheduler", service, data, { entity_id: this.config.entity });
  }

  _toggle(entityId) {
    return this._hass.callService("homeassistant", "toggle", { entity_id: entityId });
  }

  _openInfo() {
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId: this.config.entity }, bubbles: true, composed: true,
    }));
  }

  _editSchedule(schedule) {
    const time = window.prompt("Horário de início (HH:MM)", timeValue(schedule.time));
    if (time === null) return;
    const minutes = window.prompt("Duração em minutos", String(Math.round(Number(schedule.duration) / 60)));
    if (minutes === null) return;
    const days = window.prompt("Dias (0=seg … 6=dom), separados por vírgula", (schedule.days || []).join(","));
    if (days === null) return;
    const duration = Number(minutes) * 60;
    const parsedDays = days.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value >= 0 && value <= 6);
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time) || !Number.isFinite(duration) || duration < 1 || !parsedDays.length) {
      window.alert("Revise o horário, a duração e os dias informados.");
      return;
    }
    this._schedulerCall("update_schedule", { id: schedule.id, time: `${time}:00`, duration, days: parsedDays });
  }

  _addSchedule() {
    const time = window.prompt("Horário de início (HH:MM)", "18:30");
    if (time === null) return;
    const minutes = window.prompt("Duração em minutos", "240");
    if (minutes === null) return;
    const days = window.prompt("Dias (0=seg … 6=dom), separados por vírgula", "0,1,2,3,4,5,6");
    if (days === null) return;
    const duration = Number(minutes) * 60;
    const parsedDays = days.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value >= 0 && value <= 6);
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time) || !Number.isFinite(duration) || duration < 1 || !parsedDays.length) {
      window.alert("Revise o horário, a duração e os dias informados.");
      return;
    }
    this._schedulerCall("add_schedule", { time: `${time}:00`, duration, days: parsedDays });
  }

  _onClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || !this._hass) return;
    const action = target.dataset.action;
    if (action === "run") this._schedulerCall("turn_on_now");
    if (action === "stop") this._schedulerCall("stop");
    if (action === "toggle") this._toggle(target.dataset.entityId);
    if (action === "info") this._openInfo();
    if (action === "add") this._addSchedule();
    if (action === "edit") this._editSchedule(JSON.parse(target.dataset.schedule));
    if (action === "remove" && window.confirm("Remover este agendamento?")) this._schedulerCall("remove_schedule", { id: target.dataset.id });
  }

  render() {
    if (!this.config || !this._hass) return;
    const state = this._hass.states[this.config.entity];
    const attrs = state?.attributes || {};
    const title = this.config.title || state?.attributes?.friendly_name?.replace(" Próxima execução", "") || "Sala";
    const lights = attrs.lights || [];
    const active = Boolean(attrs.active);
    const schedules = attrs.schedules || [];
    const next = state?.state ? new Date(state.state).toLocaleString("pt-BR", { hour: "2-digit", minute: "2-digit", weekday: "long" }) : "sem agendamento";
    const activeBlock = active ? `<div class="live"><div class="live-line"><span class="green">● Ligada desde ${new Date(attrs.started_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span><span class="blue">até ${new Date(attrs.finishes_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span></div><div class="bar"><i></i></div><button class="off" data-action="stop">■ Desligar</button></div>` : "";
    const lightCards = lights.length ? lights.map((light) => {
      const on = light.state === "on";
      const watts = light.power_w == null ? "—" : `${Number(light.power_w).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} W`;
      return `<button class="light" data-action="toggle" data-entity-id="${escapeHtml(light.entity_id)}" aria-label="Alternar ${escapeHtml(light.name)}"><span class="bulb">💡</span><span class="label"><span class="name">${escapeHtml(light.name)}</span><span class="state ${on ? "on" : "muted"}">${on ? "Ligada" : "Desligada"}</span></span><span class="watt ${on ? "on" : ""}">⚡ ${watts}</span></button>`;
    }).join("") : `<div class="empty">Nenhuma luz configurada. Abra o menu da integração Light Scheduler e escolha as tomadas ou lâmpadas desta zona.</div>`;
    const scheduleRows = schedules.map((schedule) => {
      const encoded = escapeHtml(JSON.stringify(schedule));
      return `<div class="schedule"><span>◷</span><span class="time">${timeValue(schedule.time)}</span><span>•</span><span>${Math.round(Number(schedule.duration) / 360) / 10}h</span><span>•</span><span class="days">${(schedule.days || []).map((day) => weekdays[day]).join(", ")}</span><button data-action="edit" data-schedule="${encoded}" aria-label="Editar agendamento">✎</button><button class="delete" data-action="remove" data-id="${escapeHtml(schedule.id)}" aria-label="Remover agendamento">✕</button></div>`;
    }).join("");
    this.innerHTML = `<style>${css}</style><ha-card><div class="top"><span class="bulb">💡</span><span class="room">${escapeHtml(title)}</span><span class="chip">▣ ${attrs.enabled === false ? "Pausada" : "Agendada"}</span><button class="icon-button" data-action="info" aria-label="Informações da zona">⚙</button></div><div class="summary"><div><div class="amount">${attrs.lights_on || 0} de ${lights.length} luzes ligadas</div><div class="next">Próxima ação: ${next}</div></div><div class="power"><span class="muted">Potência total</span><b>${Number(attrs.total_power_w || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} W</b></div></div>${activeBlock}<div class="section"><h3>Luzes da sala</h3><div class="grid">${lightCards}</div></div><div class="section"><h3>Agenda automática</h3>${scheduleRows}<div class="actions"><button class="add" data-action="add">＋ Agendamento</button><button class="add" data-action="run">▶ Ligar agora</button></div></div></ha-card>`;
  }

  getCardSize() { return 8; }
}

customElements.define(TAG, LightScheduleCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: TAG, name: "Light Schedule Card", description: "Sala, luzes, potência e agenda." });
