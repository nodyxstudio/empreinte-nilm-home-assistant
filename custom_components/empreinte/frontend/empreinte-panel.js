// Panneau « Empreinte » dans la barre latérale de Home Assistant.
// HA le charge dans une iframe (embed_iframe) : la page entière est à nous.
const V = new URL(import.meta.url).search;

const MARKUP = `
<div class="app">
  <aside class="side">
    <div class="brand"><div class="brand-mark" id="logo1"></div><div><b>Empreinte</b><span>Détection d'appareils</span></div></div>
    <nav class="nav" id="nav"></nav>
    <div class="status" id="status"></div>
    <div class="side-foot"><span id="home"></span><div style="display:flex;gap:4px"><button class="icon-btn" id="help1" title="Comment ça marche"></button><button class="icon-btn" id="theme" title="Thème"></button></div></div>
  </aside>
  <main>
    <div class="mtop"><div style="display:flex;align-items:center;gap:6px"><button class="icon-btn menu-btn" id="hamenu" title="Menu Home Assistant"></button><div class="brand" style="padding:0"><div class="brand-mark" id="logo2"></div><div><b>Empreinte</b><span id="mstat"></span></div></div></div><div style="display:flex;gap:4px"><button class="icon-btn" id="help2" title="Comment ça marche"></button><button class="icon-btn" id="theme2" title="Thème"></button></div></div>
    <div id="view"><div class="loading"><div class="spin"></div>Chargement…</div></div>
  </main>
</div>
<nav class="tabbar" id="tabbar"></nav>
<div id="tip"></div>
<div id="toast"></div>`;

class EmpreintePanel extends HTMLElement {
  set hass(h) {
    this._hass = h;
    if (this._app) this._app.setHass(h);
    else if (!this._starting) this._start();
  }
  get hass() { return this._hass; }
  set narrow(n) { this._narrow = n; }

  async _start() {
    this._starting = true;
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = new URL("app.css" + V, import.meta.url).href;
    document.head.appendChild(css);
    const meta = document.createElement("meta");
    meta.name = "viewport"; meta.content = "width=device-width, initial-scale=1, viewport-fit=cover";
    document.head.appendChild(meta);
    document.body.style.margin = "0";
    this.innerHTML = MARKUP;
    this.querySelector("#home").textContent = (this._hass && this._hass.config && this._hass.config.location_name) || "Ma maison";
    const mod = await import(new URL("app.js" + V, import.meta.url).href);
    this._app = mod.start(this._hass);
  }
}
customElements.define("empreinte-panel", EmpreintePanel);
