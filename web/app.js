"use strict";
/* Predicciones Fútbol — interfaz. Lee los datos que genera actualizar.py (no necesita a Claude). */

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p, d = 0) => (p == null || isNaN(p)) ? "–" : (p * 100).toFixed(d).replace(".", ",") + "%";
const num = (x, d = 2) => (x == null || isNaN(x)) ? "–" : Number(x).toFixed(d).replace(".", ",");
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

let DATOS = null, ESTADO = {}, LIGAS = {}, ORDEN = {}, PROMPT = null, ULTIMA_CARGA = null, SERVIDOR_OK = true;
const UI = Object.assign({ vista: "partidos", dia: "hoy", buscar: "", liga: "", soloDest: false, ligaHist: "", tabla: "", rank: "", perfil: "", banca: "" },
  leerPref());

function leerPref() { try { return JSON.parse(localStorage.getItem("pf_ui") || "{}"); } catch { return {}; } }
function guardarPref() { try { localStorage.setItem("pf_ui", JSON.stringify({ liga: UI.liga, soloDest: UI.soloDest, tabla: UI.tabla, rank: UI.rank, perfil: UI.perfil, banca: UI.banca })); } catch { } }

function toast(t) { const el = $("#toast"); el.textContent = t; el.classList.add("ver"); setTimeout(() => el.classList.remove("ver"), 2200); }

// ---------------------------------------------------------------- fechas
const fmt = (iso, op, tz) => new Intl.DateTimeFormat("es-PE", Object.assign({ timeZone: tz || TZ }, op)).format(new Date(iso));
const horaLocal = iso => fmt(iso, { hour: "2-digit", minute: "2-digit", hour12: false });
const claveDia = iso => fmt(iso, { year: "numeric", month: "2-digit", day: "2-digit" }).split("/").reverse().join("-");
function etiquetaDia(clave) {
  const hoy = claveDia(new Date().toISOString());
  const man = claveDia(new Date(Date.now() + 864e5).toISOString());
  const aye = claveDia(new Date(Date.now() - 864e5).toISOString());
  if (clave === hoy) return "Hoy";
  if (clave === man) return "Mañana";
  if (clave === aye) return "Ayer";
  const [y, m, d] = clave.split("-").map(Number);
  return new Intl.DateTimeFormat("es-PE", { weekday: "short", day: "numeric", month: "short" }).format(new Date(y, m - 1, d, 12));
}
function haceCuanto(iso) {
  if (!iso) return "nunca";
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} días`;
}

// ---------------------------------------------------------------- modo página web (GitHub Pages)
// En la página web no hay servidor: los datos están en «datos.bin», cifrados con AES-256-GCM. La clave sale de tu
// contraseña (PBKDF2, 310 000 vueltas) y nunca viaja por internet: el descifrado ocurre dentro de tu navegador.
const ESTATICO = !!window.PF_ESTATICO;
let CLAVE_WEB = null, PAQUETE = null, VERSION_PAQUETE = null;

async function descifrarPaquete(buf, pass) {
  const b = new Uint8Array(buf), sal = b.slice(0, 16), iv = b.slice(16, 28), ct = b.slice(28);
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  const k = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: sal, iterations: 310000, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const gz = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, k, ct);       // falla si la clave es incorrecta
  const txt = await new Response(new Blob([gz]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  return JSON.parse(txt);
}
async function versionWeb() {
  try { return (await (await fetch("version.txt?t=" + Date.now(), { cache: "no-store" })).text()).trim(); } catch { return null; }
}
async function paqueteWeb() {
  const v = await versionWeb();
  if (PAQUETE && v === VERSION_PAQUETE) return PAQUETE;
  const r = await fetch("datos.bin?v=" + encodeURIComponent(v || Date.now()), { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  PAQUETE = await descifrarPaquete(await r.arrayBuffer(), CLAVE_WEB);
  VERSION_PAQUETE = v;
  return PAQUETE;
}
async function apiWeb(ruta) {
  if (ruta === "/api/ping") return { ok: true };
  if (ruta === "/api/actualizar") return { iniciado: false, web: true };
  if (ruta === "/api/abrir_pdf") return { ok: false };
  const p = await paqueteWeb();
  if (ruta === "/api/datos") return p.datos;
  if (ruta === "/api/estado") return Object.assign({}, p.estado, { corriendo: false });
  if (ruta === "/api/prompt") return p.prompt || "";
  throw new Error("ruta");
}

function pantallaClave(error) {
  document.body.insertAdjacentHTML("beforeend", `<div class="acceso" id="acceso"><form class="acceso-caja" id="acceso-form">
    <h2>Predicciones Fútbol</h2><p class="nota">Página privada. Escribe tu clave (está en tu escritorio, en «Acceso pagina predicciones.txt»).</p>
    <input type="password" id="acceso-clave" autocomplete="current-password" placeholder="Clave" required>
    <label class="interruptor"><input type="checkbox" id="acceso-recordar" checked> Recordar en este dispositivo</label>
    <button class="boton primario" type="submit">Entrar</button>
    <p class="nota" id="acceso-msg">${error ? "Clave incorrecta. Inténtalo de nuevo." : ""}</p></form></div>`);
  $("#acceso-clave").focus();
  $("#acceso-form").onsubmit = async ev => {
    ev.preventDefault();
    const pass = $("#acceso-clave").value;
    $("#acceso-msg").textContent = "Abriendo…";
    if (await probarClave(pass)) {
      try { if ($("#acceso-recordar").checked) localStorage.setItem("pf_clave", pass); } catch { }
      $("#acceso").remove();
      iniciar();
    } else $("#acceso-msg").textContent = "Clave incorrecta. Inténtalo de nuevo.";
  };
}
async function probarClave(pass) {
  CLAVE_WEB = pass; PAQUETE = null;
  try { await paqueteWeb(); return true; } catch { CLAVE_WEB = null; return false; }
}

// ---------------------------------------------------------------- servidor
async function api(ruta, op) {
  if (ESTATICO) return apiWeb(ruta);
  const r = await fetch(ruta, op);
  if (!r.ok) throw new Error(r.status);
  return ruta === "/api/prompt" ? r.text() : r.json();
}

async function cargarDatos() {
  try {
    DATOS = await api("/api/datos");
  } catch {
    DATOS = null;
  }
  LIGAS = {}; ORDEN = {};
  (DATOS?.ligas || []).forEach((l, i) => { LIGAS[l.codigo] = l; ORDEN[l.codigo] = i; });
  ULTIMA_CARGA = DATOS?.generado || null;
  pintarTodo();
}

async function consultarEstado() {
  try {
    ESTADO = await api("/api/estado");
    SERVIDOR_OK = true;
  } catch {
    SERVIDOR_OK = false;
  }
  pintarEstado();
  if (SERVIDOR_OK && ESTADO.ultima_ok && DATOS && ESTADO.ultima_ok !== ULTIMA_CARGA && !ESTADO.corriendo) {
    if (new Date(ESTADO.ultima_ok) > new Date(ULTIMA_CARGA)) await cargarDatos();
  } else if (SERVIDOR_OK && !DATOS && ESTADO.ultima_ok && !ESTADO.corriendo) {
    await cargarDatos();
  }
  const ocupado = ESTADO.corriendo && esReciente(ESTADO.actualizado, 15);
  setTimeout(consultarEstado, ocupado ? 2500 : 30000);
}
const esReciente = (iso, min) => iso && (Date.now() - new Date(iso)) < min * 60000;

function pintarEstado() {
  const p = $("#punto"), t = $("#estado-txt"), b = $("#barra"), bi = $("#barra-in"), btn = $("#btn-actualizar");
  p.className = "punto";
  b.hidden = true;
  const ocupado = ESTADO.corriendo && esReciente(ESTADO.actualizado, 15);
  if (!SERVIDOR_OK) {
    p.classList.add("rojo"); t.textContent = "App cerrada: ábrela desde el escritorio"; btn.disabled = true; return;
  }
  btn.disabled = !!ocupado;
  if (ocupado) {
    p.classList.add("girando"); t.textContent = ESTADO.fase || "Actualizando…"; b.hidden = false; bi.style.width = (ESTADO.progreso || 0) + "%";
  } else if (ESTADO.fase === "Sin internet") {
    p.classList.add("amarillo"); t.textContent = `Sin internet · datos de ${haceCuanto(ESTADO.ultima_ok)}`;
  } else if (ESTADO.fase === "Error") {
    p.classList.add("rojo"); t.textContent = `Error al actualizar · datos de ${haceCuanto(ESTADO.ultima_ok)}`;
  } else {
    t.textContent = `Actualizado ${haceCuanto(ESTADO.ultima_ok || DATOS?.generado)}`;
  }
  const av = [];
  if (ESTADO.error && !ocupado) av.push(`<div class="aviso ${ESTADO.fase === "Error" ? "error" : ""}">${esc(ESTADO.error)}${ESTADO.fase === "Error" ? " Detalles en la carpeta <b>logs</b>." : ""}</div>`);
  if (!DATOS && !ocupado && SERVIDOR_OK) av.push(`<div class="aviso">Todavía no hay predicciones. Pulsa <b>Actualizar ahora</b> (la primera vez tarda unos minutos).</div>`);
  $("#avisos").innerHTML = av.join("");
}

async function actualizar() {
  if (ESTATICO) {         // en la página web se actualiza sola cada hora: el botón solo trae lo último publicado
    try { PAQUETE = null; await cargarDatos(); ESTADO = await api("/api/estado"); pintarEstado(); toast("Datos al día (la página se actualiza sola cada hora)"); }
    catch { toast("No se pudo descargar: revisa tu internet"); }
    return;
  }
  try {
    const r = await api("/api/actualizar", { method: "POST" });
    toast(r.iniciado ? "Actualizando… puedes seguir usando la app" : "Ya hay una actualización en curso");
    ESTADO.corriendo = true; ESTADO.actualizado = new Date().toISOString(); ESTADO.fase = "Iniciando…"; ESTADO.progreso = 1;
    pintarEstado();
  } catch { toast("No se pudo iniciar la actualización"); }
}

// ---------------------------------------------------------------- utilidades de partido
const nombreLiga = c => LIGAS[c]?.nombre || c;
const logo = (u, alt = "") => u ? `<img src="${esc(u)}" alt="${esc(alt)}" loading="lazy" onerror="this.style.visibility='hidden'">` : `<img alt="" style="visibility:hidden">`;
const forma = f => `<div class="forma" title="Últimos 5 (más reciente a la izquierda)">${[...(f || "")].map(r => `<i class="${r}"></i>`).join("")}</div>`;
function predDe(m) { return (m.fin || m.estado === "in") && m.pred_previa ? Object.assign({}, m.pred, m.pred_previa) : m.pred; }

// ---------------------------------------------------------------- apuestas
const perfilActual = () => UI.perfil || DATOS?.apuestas?.por_defecto || "equilibrado";
const NOMBRE_RES = { gana: "GANÓ", pierde: "PERDIÓ", devuelve: "DEVUELTA" };
function pickDe(m, perfil = perfilActual()) {
  const ap = m.apuesta;
  if (!ap) return null;
  const x = ap.perfiles?.[perfil];
  if (!x) return null;
  const f = ap.mercados.find(r => r.cod === x.cod);
  return f ? Object.assign({}, f, { riesgo: x.riesgo, motivos: x.motivos || [], respaldo: x.respaldo, stake_tope: x.stake_tope ?? 1 }) : null;
}
// los datos pueden tener hasta una hora: un partido "pre" cuya hora ya pasó se trata como empezado
const empezado = m => m.estado !== "pre" || Date.parse(m.fecha) <= Date.now();
// el nivel mide qué tan fiables son los datos, no la probabilidad de perder (eso lo dice "falla ~N de cada 10")
const ETQ_RIESGO = { bajo: "datos fiables", medio: "datos dudosos", alto: "mejor no apostar" };
const falla = p => `falla ~${Math.round((1 - p) * 10)} de cada 10`;
function etiquetaRiesgo(f) {
  return `<span class="riesgo ${f.riesgo}" title="${esc(f.motivos?.length ? f.motivos.join("; ") : "Mide qué tan fiables son los datos de este partido, no la probabilidad de perder")}">${ETQ_RIESGO[f.riesgo] || f.riesgo}</span>`;
}
function lineaApuesta(m) {
  if (m.estado === "pre") {
    const f = pickDe(m);
    if (!f) return "";
    if (empezado(m)) {
      return `<div class="apuesta-linea"><span class="etq">APUESTA</span><span class="txt">${esc(f.txt)}</span>
        <span class="nota">ya empezó: la recomendación previa no sirve para apostar en vivo</span></div>`;
    }
    // las 3 recomendaciones juntas (una por perfil), la del perfil elegido resaltada
    const tres = ordenPerfiles().map(k => {
      const g = pickDe(m, k);
      return g ? `<span class="ap3${k === perfilActual() ? " activo" : ""}" title="${esc(DATOS.apuestas.perfiles[k].texto || "")}"><small>${esc(DATOS.apuestas.perfiles[k].nombre)}</small><b>${esc(g.txt)}</b> ${pct(g.p)} · mín. <b>${num(g.minima)}</b></span>` : "";
    }).join("");
    return `<div class="apuesta-linea"><span class="etq">APUESTAS</span>${tres}${etiquetaRiesgo(f)}
      ${m.apuesta.base === "mercado" ? `<span class="nota" title="Probabilidades ajustadas a las cuotas reales de la casa">según cuotas</span>` : ""}</div>`;
  }
  const pp = m.apuesta_previa?.perfiles;
  if (!pp) return "";
  const tres = ordenPerfiles().filter(k => pp[k]).map(k => {
    const prev = pp[k];
    const res = prev.res ? `<span class="res-ap ${prev.res}">${NOMBRE_RES[prev.res]}</span>` : `<span class="nota">${m.fin ? "sin liquidar" : "en juego"}</span>`;
    return `<span class="ap3${k === perfilActual() ? " activo" : ""}"><small>${esc(DATOS.apuestas?.perfiles?.[k]?.nombre || k)}</small><b>${esc(prev.txt)}</b> ${pct(prev.p)} ${res}</span>`;
  }).join("");
  return tres ? `<div class="apuesta-linea"><span class="etq">APUESTAS</span>${tres}</div>` : "";
}
// orden fijo: Más probable, Recomendada, Mayor pago (de más segura a más arriesgada)
function ordenPerfiles() {
  const pf = DATOS?.apuestas?.perfiles || {};
  return Object.keys(pf).sort((a, b) => (pf[b].rango?.[0] ?? 0) - (pf[a].rango?.[0] ?? 0));
}

function evaluarCuota(f, o, base) {
  // valor esperado por sol apostado con la probabilidad central (las devoluciones no ganan ni pierden)
  const pl = 1 - f.p_gana - f.p_dev;
  const ev = f.p_gana * (o - 1) - pl;
  // tamaño: ¼ de Kelly con la probabilidad PRUDENTE, tope del perfil × riesgo, pasos de 0,25%
  const pg = f.prud * (1 - f.p_dev), pp = 1 - pg - f.p_dev, b = o - 1;
  const k = b > 0 && (pg + pp) > 0 ? Math.max(0, (pg * b - pp) / (b * (pg + pp))) : 0;
  const frac = DATOS?.apuestas?.kelly ?? 0.25;
  const stake = Math.floor(Math.min(f.stake_tope ?? 0.5, 100 * frac * k) / 0.25 + 1e-9) * 0.25;
  const soloModelo = base === "modelo" ? " Solo según el modelo, sin contraste con cuotas de mercado." : "";
  // rango del valor esperado con los límites del intervalo del 80% (R17)
  const evCon = q => { const g = q * (1 - f.p_dev); return g * (o - 1) - (1 - g - f.p_dev); };
  const rangoEv = [evCon(f.p10), evCon(f.p90)];
  const out = { ev, rangoEv, stake: 0, clase: "no" };
  if (f.riesgo === "alto") return Object.assign(out, { veredicto: `Mejor no apostar: ${f.motivos.join("; ") || "datos poco fiables"}. Aunque la cuota parezca buena, no apuestes.` });
  if (o >= f.alerta) return Object.assign(out, { veredicto: `Cuota demasiado alta (${num(f.alerta)} o más): puede haber una noticia que el modelo no conoce (lesión, alineación, rotación). No apuestes.` });
  if (o >= f.minima) {
    let motivo0 = "";
    if (stake === 0) motivo0 = (f.stake_tope ?? 0) < 0.25
      ? ` Con datos dudosos este tipo de apuesta no lleva monto: su tope (${num(f.stake_tope, 3)}%) queda bajo el mínimo de 0,25%.`
      : " La ventaja es tan pequeña que el monto sugerido es 0.";
    return Object.assign(out, { stake, clase: "si", veredicto: `Tu cuota ${num(o)} supera la mínima ${num(f.minima)}.${motivo0}${soloModelo}` });
  }
  if (o >= f.justa) return Object.assign(out, { clase: "duda", veredicto: "Por debajo de la mínima: la ventaja es muy chica para lo que el modelo duda. No apuestes." });
  return Object.assign(out, { veredicto: "Tu casa paga menos de lo justo: con esta cuota se espera perder. No apuestes." });
}
function favorito(p) {
  const o = [["L", p.pH], ["X", p.pD], ["V", p.pA]].sort((a, b) => b[1] - a[1]);
  return o[0][0];
}
function acerto(m) {
  if (!m.fin || m.h.g == null) return null;
  const p = m.pred_previa; if (!p) return null;
  const real = m.h.g > m.a.g ? "L" : m.h.g < m.a.g ? "V" : "X";
  return favorito(p) === real;
}

// ---------------------------------------------------------------- vista: partidos
function pintarDias(lista) {
  const dias = {};
  lista.forEach(m => { const k = claveDia(m.fecha); dias[k] = (dias[k] || 0) + 1; });
  const claves = Object.keys(dias).sort();
  const hoy = claveDia(new Date().toISOString());
  if (UI.dia !== "todos" && UI.dia !== "vivo" && !dias[UI.dia]) {
    UI.dia = dias[hoy] ? hoy : (claves.find(k => k >= hoy) || claves[claves.length - 1] || "todos");
  }
  const vivos = lista.filter(m => m.estado === "in").length;
  let h = "";
  if (vivos) h += `<button class="chip ${UI.dia === "vivo" ? "activo" : ""}" data-dia="vivo">● En vivo<small>${vivos}</small></button>`;
  claves.forEach(k => { h += `<button class="chip ${UI.dia === k ? "activo" : ""}" data-dia="${k}">${etiquetaDia(k)}<small>${dias[k]}</small></button>`; });
  h += `<button class="chip ${UI.dia === "todos" ? "activo" : ""}" data-dia="todos">Próximos 10 días</button>`;
  $("#dias").innerHTML = h;
}

function pintarFiltroLigas() {
  const sel = $("#filtro-liga");
  const presentes = new Set((DATOS?.partidos || []).map(m => m.lg));
  const op = ['<option value="">Todas las ligas</option>'];
  (DATOS?.ligas || []).filter(l => presentes.has(l.codigo)).forEach(l =>
    op.push(`<option value="${l.codigo}" ${UI.liga === l.codigo ? "selected" : ""}>${l.nivel === 1 ? "★ " : ""}${esc(l.nombre)} · ${esc(l.pais)}</option>`));
  sel.innerHTML = op.join("");
  $("#solo-dest").checked = !!UI.soloDest;
  const pf = DATOS?.apuestas?.perfiles || {};
  if (Object.keys(pf).length) {
    $("#perfil").innerHTML = Object.entries(pf).map(([k, v]) =>
      `<option value="${k}">${esc(v.nombre)} (${pct(v.rango[0])}–${pct(v.rango[1])})</option>`).join("");
  }
  $("#perfil").value = perfilActual();
}

function filaPartido(m) {
  const p = predDe(m);
  let hora;
  if (m.estado === "in") hora = `<span class="vivo">● ${esc(m.detalle || "En vivo")}</span>`;
  else if (m.fin) hora = `<span class="final">Final</span><br>${horaLocal(m.fecha)}`;
  else hora = horaLocal(m.fecha);
  const ac = acerto(m);
  const goles = lado => (m.estado === "in" || m.fin) && m[lado].g != null ? `<span class="goles">${m[lado].g}</span>` : "";
  const iconos = (m.senales || []).filter(s => s.nivel !== "info").slice(0, 4).map(s => {
    const t = { altitud: "ALT", descanso: "DESC", rotacion: "ROT", clima: "CLIMA", datos: "DATOS", mercado: "MERC" }[s.tipo] || s.tipo;
    return `<span class="ico ${s.nivel}" title="${esc(s.texto)}">${t}</span>`;
  }).join("");
  const top = Array.isArray(p.top?.[0]) ? p.top[0] : p.top;   // predicción nueva: lista; guardada: un marcador
  return `<div class="partido" data-id="${m.id}">
    <div class="hora">${hora}</div>
    <div class="equipo">${logo(m.h.logo, m.h.n)}<div class="col"><span class="nombre" title="${esc(m.h.n)}">${esc(m.h.n)}</span>${forma(m.ctx?.h?.forma)}</div>${goles("h")}</div>
    <div class="probs">
      <div class="probs-num"><span class="l">${pct(p.pH)}</span><span class="x">${pct(p.pD)}</span><span class="v">${pct(p.pA)}</span></div>
      <div class="barra3"><div class="l" style="width:${p.pH * 100}%"></div><div class="x" style="width:${p.pD * 100}%"></div><div class="v" style="width:${p.pA * 100}%"></div></div>
      <div class="iconos">${ac === null ? "" : `<span class="acierto-badge ${ac ? "si" : "no"}">${ac ? "ACERTÓ" : "FALLÓ"}</span>`}${m.alineacion?.estado === "confirmada" ? `<span class="xi-badge" title="Alineación confirmada">XI</span>` : ""}${iconos}</div>
    </div>
    <div class="equipo visita">${logo(m.a.logo, m.a.n)}<div class="col"><span class="nombre" title="${esc(m.a.n)}">${esc(m.a.n)}</span>${forma(m.ctx?.a?.forma)}</div>${goles("a")}</div>
    <div class="extra extra-col">
      <span>+2,5 goles</span><b>${pct(p.o25)}</b>
      <span>Ambos marcan</span><b>${pct(p.btts)}</b>
      <span>Marcador</span><b>${Array.isArray(top) ? `${top[0]}-${top[1]}` : "–"}</b>
      <span>Confianza</span><span class="conf ${m.pred.conf}">${m.pred.conf}</span>
    </div>
    <div class="flecha">›</div>
    ${lineaApuesta(m)}
  </div>`;
}

function pintarPartidos() {
  const cont = $("#lista");
  if (!DATOS) { cont.innerHTML = ""; return; }
  let lista = DATOS.partidos.slice();
  const q = UI.buscar.trim().toLowerCase();
  if (UI.liga) lista = lista.filter(m => m.lg === UI.liga);
  if (UI.soloDest) lista = lista.filter(m => LIGAS[m.lg]?.nivel === 1);
  if (q) lista = lista.filter(m => (m.h.n + " " + m.a.n).toLowerCase().includes(q));
  pintarDias(lista);
  if (UI.dia === "vivo") lista = lista.filter(m => m.estado === "in");
  else if (UI.dia === "todos") lista = lista.filter(m => m.estado !== "post");
  else lista = lista.filter(m => claveDia(m.fecha) === UI.dia);
  if (!lista.length) { cont.innerHTML = `<div class="vacio">No hay partidos con esos filtros.</div>`; return; }
  const grupos = {};
  lista.forEach(m => (grupos[m.lg] = grupos[m.lg] || []).push(m));
  const orden = Object.keys(grupos).sort((a, b) => ((LIGAS[a]?.nivel || 9) - (LIGAS[b]?.nivel || 9)) || ((ORDEN[a] ?? 99) - (ORDEN[b] ?? 99)));
  cont.innerHTML = orden.map(lg => {
    const l = LIGAS[lg] || { nombre: lg, pais: "" };
    const ms = grupos[lg].sort((a, b) => a.fecha.localeCompare(b.fecha));
    return `<div class="liga-bloque"><h3 class="liga-titulo">${l.nivel === 1 ? '<span class="estrella">★</span>' : ""}${esc(l.nombre)}<span class="pais">${esc(l.pais)} · ${ms.length} partido${ms.length > 1 ? "s" : ""}</span></h3>${ms.map(filaPartido).join("")}</div>`;
  }).join("");
}

// ---------------------------------------------------------------- detalle
function abrirDetalle(id) {
  const m = DATOS.partidos.find(x => x.id === id);
  if (!m) return;
  $("#detalle").innerHTML = htmlDetalle(m);
  $("#detalle").classList.add("abierto");
  $("#velo").classList.add("abierto");
  $("#detalle").scrollTop = 0;
  $("#detalle .cerrar").onclick = cerrarDetalle;
  conectarCalculadora(m);
  if (m.extras) conectarExtras(m);
  const bs = $("#btn-simular");
  if (bs) bs.onclick = () => pintarSimulacion(m);
  const bb = $("#btn-sim-bajas");
  if (bb) bb.onclick = () => pintarSimulacion(m, m.alineacion.escenario_bajas);
  $("#det-copiar").onclick = () => copiarPrompt(m);
  $("#det-guardar").onclick = () => guardarPrompt(m);
  if ($("#det-pdf")) $("#det-pdf").onclick = () => api("/api/abrir_pdf", { method: "POST" })
    .then(r => r.web ? window.open("/api/pdf", "_blank") : toast(r.ok ? "Abriendo el PDF del prompt" : "No se encontró el PDF")).catch(() => { });
}
function cerrarDetalle() { $("#detalle").classList.remove("abierto"); $("#velo").classList.remove("abierto"); }

function rango(ic) { return ic ? `${pct(ic[0])}–${pct(ic[1])}` : "–"; }

function htmlApuesta(m) {
  const conf = DATOS.apuestas || {};
  const medido = conf.prueba?.perfiles?.[perfilActual()];
  const responsable = `<div class="caja" style="margin-top:10px"><div class="tit">Uso responsable (R20)</div>
    <p class="nota">Ningún modelo garantiza acertar ni ganar dinero a largo plazo: la casa siempre cobra un margen a su favor. Aún no hay pruebas de que estas recomendaciones ganen dinero.${medido ? ` Lo que sí se midió: en la prueba retrospectiva (${medido.n} partidos que no se usaron para ajustar nada), este tipo de apuesta acertó ${pct(medido.acierto, 1)} frente a ${pct(medido.esperado, 1)} prometido.` : ""}</p>
    <p class="nota">Apuesta solo dinero que puedas perder sin afectar tus gastos, como máximo 3 apuestas al día, y nunca subas el monto para recuperar pérdidas. No combines apuestas: cada selección suma la comisión de la casa y multiplica el riesgo.</p>
    <p class="nota">Solo mayores de 18 años, en casas autorizadas por el MINCETUR. ¿Apostar te causa estrés o deudas? Detente y busca ayuda: Línea 113, opción 5 (salud mental, MINSA).</p></div>`;
  if (m.estado !== "pre") {
    const prev = m.apuesta_previa?.perfiles;
    if (!prev) return `<div class="seccion"><h3>Apuesta recomendada</h3><p class="nota">El partido empezó antes de que el programa registrara una recomendación.</p></div>`;
    const filas = Object.entries(prev).map(([k, x]) => `<tr><td>${esc(conf.perfiles?.[k]?.nombre || k)}</td><td>${esc(x.txt)}</td><td class="num">${pct(x.p)}${x.p90 != null ? ` <span class="nota">(${pct(x.p10)}–${pct(x.p90)})</span>` : ""}</td><td class="num">${num(x.minima)}</td><td>${x.res ? `<span class="res-ap ${x.res}">${NOMBRE_RES[x.res]}</span>` : (m.fin ? "sin liquidar" : "en juego")}</td></tr>`).join("");
    const m90 = m.apuesta_previa.marcador_90;
    const nota90 = m90 && m.h.g != null && (m90[0] !== m.h.g || m90[1] !== m.a.g) ? `<p class="nota">Se liquida con el marcador a los 90' (${m90[0]}-${m90[1]}), como hacen las casas: la prórroga y los penales no cuentan.</p>` : "";
    return `<div class="seccion"><h3>Apuesta recomendada antes del partido</h3><table class="t"><tr><th>Perfil</th><th>Apuesta</th><th class="num">Prob.</th><th class="num">Cuota mín.</th><th>Resultado</th></tr>${filas}</table>${nota90}</div>`;
  }
  const ap = m.apuesta;
  if (!ap) return "";
  const perfil = perfilActual();
  const f = pickDe(m, perfil);
  if (!f) return "";
  if (empezado(m)) {
    return `<div class="seccion"><h3>Apuesta recomendada</h3><div class="caja-apuesta"><div class="pick">${esc(f.txt)}</div>
      <p class="nota">El partido ya empezó (los datos se actualizan cada hora): esta recomendación era para antes del inicio y no sirve para apostar en vivo.</p></div></div>`;
  }
  const base = ap.base === "mercado"
    ? `Probabilidades ajustadas a las cuotas de ${esc(ap.ajuste?.proveedor || "la casa")} (margen ${pct(ap.ajuste?.margen, 1)} quitado). En 6.459 partidos reales la cuota de cierre fue más precisa que el modelo, así que aquí manda el mercado.`
    : `Probabilidades del modelo: ESPN no publica cuotas de este partido.`;
  const casa = f.cuota_casa ? `<div class="cifra"><div class="t">${esc(ap.ajuste?.proveedor || "Casa")} paga</div><div class="v">${num(f.cuota_casa)}</div><div class="ic nota">${f.cuota_casa >= f.minima ? "llega a la mínima" : `necesitarías ${num(f.minima)}: no conviene`}</div></div>` : "";
  // las 3 apuestas (una por perfil) juntas, arriba de todo
  const tresCajas = ordenPerfiles().map(k => {
    const g = pickDe(m, k);
    return g ? `<div class="caja tres-ap${k === perfil ? " activo" : ""}"><div class="tit">${esc(conf.perfiles[k].nombre)}${k === perfil ? " · tu perfil" : ""}</div>
      <div class="pick" style="font-size:1.05em">${esc(g.txt)}</div>
      <div class="nota">${pct(g.p)} · ${falla(g.p)}</div>
      <div>cuota mínima <b style="color:var(--acento)">${num(g.minima)}</b>${g.cuota_casa ? ` <span class="nota">· casa ${num(g.cuota_casa)}</span>` : ""}</div></div>` : "";
  }).join("");
  const otros = "";
  const valor = ap.valor ? (() => { const v = ap.mercados.find(r => r.cod === ap.valor.cod); return `<div class="senal info"><b>Apuesta de valor (experimental):</b> ${esc(v.txt)} a ${num(v.cuota_casa)} (+${num(ap.valor.ev * 100, 1)}% esperado si la probabilidad es correcta, monto ${num(ap.valor.stake, 2)}% de la banca)</div>`; })()
    : conf.prueba?.valor_habilitado ? `<p class="nota">La etiqueta «apuesta de valor» está encendida, pero en este partido ninguna opción cumple el umbral.</p>`
    : (conf.prueba?.valor ? `<p class="nota">La etiqueta «apuesta de valor» está apagada: en la prueba con cuotas reales esa estrategia rindió ${pct(conf.prueba.valor.roi, 1)} en ${conf.prueba.valor.n} apuestas. Se vuelve a comprobar cada día.</p>` : "");
  const avisos = (ap.avisos || []).map(t => `<div class="senal amarillo">${esc(t)}</div>`).join("");
  const grupos = {};
  ap.mercados.forEach(r => (grupos[r.grupo] = grupos[r.grupo] || []).push(r));
  const tabla = Object.entries(grupos).map(([g, rs]) => rs.map((r, i) => `<tr${r.cod === f.cod ? ' style="outline:1px solid var(--acento)"' : ""}><td class="nota">${i ? "" : esc(g)}</td><td>${esc(r.txt)}${r.no_calibrado ? ` <span class="nota">(no pasó la prueba de calibración)</span>` : ""}</td><td class="num">${pct(r.p)} <span class="nota">(${pct(r.p10)}–${pct(r.p90)})</span></td><td class="num">${num(r.justa)}</td><td class="num"><b>${r.no_calibrado ? "–" : num(r.minima)}</b></td><td class="num">${r.cuota_casa ? `${num(r.cuota_casa)} <span class="nota">(${num(r.ev_casa * 100, 1)}%)</span>` : "–"}</td></tr>`).join("")).join("");
  const rangoP = conf.perfiles?.[perfil]?.rango;
  return `<div class="seccion"><h3>Las 3 apuestas recomendadas <small>una por perfil, de la más segura a la que más paga</small></h3>
    <div class="tres">${tresCajas}</div>
    <p class="nota">Abajo, el detalle y la calculadora del perfil elegido arriba (${esc(conf.perfiles?.[perfil]?.nombre || perfil)}). Cambia el perfil en el selector para ver el detalle de otra.</p></div>
    <div class="seccion"><h3>Detalle: ${esc(conf.perfiles?.[perfil]?.nombre || perfil)} <small>${rangoP ? `opciones entre ${pct(rangoP[0])} y ${pct(rangoP[1])}` : ""}</small></h3>
    <div class="caja-apuesta">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${etiquetaRiesgo(f)}<span class="nota">${esc(f.grupo)}${f.respaldo ? " · no había opciones en el rango de tu perfil: se amplió" : ""}</span></div>
      ${f.motivos.length ? `<p class="nota">Motivos: ${esc(f.motivos.join("; "))}.</p>` : ""}
      <div class="pick">${esc(f.txt)}</div>
      <div class="cifras">
        <div class="cifra"><div class="t">Probabilidad</div><div class="v">${pct(f.p)}</div><div class="nota">80%: ${pct(f.p10)}–${pct(f.p90)} · ${falla(f.p)}</div></div>
        <div class="cifra"><div class="t">Cuota justa</div><div class="v">${num(f.justa)}</div><div class="nota">= 1 / probabilidad</div></div>
        <div class="cifra"><div class="t">Cuota mínima</div><div class="v" style="color:var(--acento)">${num(f.minima)}</div><div class="nota">apuesta solo si te pagan esto o más</div></div>
        ${casa}
      </div>
      ${f.p_dev > 0.001 ? `<p class="nota">Si termina en empate se te devuelve lo apostado (${pct(f.p_dev)} de probabilidad).</p>` : ""}
      <div class="calc">
        <label>Cuota de tu casa<input id="calc-cuota" type="number" step="0.01" min="1.01" placeholder="ej. ${num(Math.max(f.minima, 1.01) + 0.05).replace(",", ".")}"></label>
        <label>Tu banca (S/)<input id="calc-banca" type="number" step="10" min="0" value="${esc(UI.banca)}" placeholder="opcional"></label>
      </div>
      <div id="calc-res"><p class="nota">${f.riesgo === "alto" ? `<b>Mejor no apostar:</b> ${esc(f.motivos.join("; ") || "datos poco fiables")}. Se muestra igual la opción menos arriesgada. ` : ""}Escribe la cuota que te ofrece tu casa para saber si conviene y cuánto apostar (¼ de Kelly, ${f.stake_tope >= 0.25 ? `máximo ${num(f.stake_tope, 2)}% de tu banca` : "sin monto en este caso: su tope queda bajo el mínimo de 0,25%"}).</p></div>
    </div>
    <p class="nota">${base} ${esc(conf.perfiles?.[perfil]?.texto || "")} Regla: entre las opciones de este perfil se elige la de mayor probabilidad prudente (${pct(f.prud)} aquí${ap.base === "modelo" ? "; entre la central y el límite bajo del rango" : ""}). Cuota mínima = la mayor entre 1,25; ${ap.base === "mercado" ? "1,03" : "1,04"} / probabilidad; 1 / (probabilidad − 0,02) y 1 / probabilidad prudente. Si tu casa paga menos de ${num(f.minima)}, no apuestes.</p>
    ${avisos}${valor}
    ${otros ? `<div class="caja" style="margin-top:8px"><div class="tit">Con otros perfiles</div>${otros}</div>` : ""}
    <details class="todas"><summary>Ver todas las opciones de apuesta (${ap.mercados.length})</summary>
      <table class="t" style="margin-top:8px"><tr><th>Mercado</th><th>Opción</th><th class="num">Prob. (80%)</th><th class="num">Justa</th><th class="num">Mínima</th><th class="num">Casa (esperado)</th></tr>${tabla}</table></details>
    ${responsable}
  </div>`;
}

// ---------------------------------------------------------------- alineaciones y simulación
const NOMBRE_LINEA = { POR: "Portero", DEF: "Defensa", MED: "Medio", DEL: "Ataque" };
const signoPct = x => x == null ? "–" : `${x >= 0 ? "+" : ""}${num(x * 100, 1)}%`;

function htmlOnce(m, lado) {
  const al = m.alineacion, x = al[lado], eq = m[lado];
  const filas = x.jugadores.map(j => `<tr><td><span class="linea-tag">${j.linea}</span> <span class="pos">${esc(j.pos)}</span></td>
    <td>${esc(j.n)}${j.posible_baja ? ` <a class="baja-tag" href="${esc(j.posible_baja.link)}" target="_blank" rel="noopener noreferrer" title="${esc(j.posible_baja.titulo)} (${esc(j.posible_baja.fuente)})">posible baja · ${esc(TIPO_NOTICIA[j.posible_baja.tipo] || j.posible_baja.tipo)}</a>` : ""}</td>
    <td class="num ${j.ataque > 0 ? "mas" : j.ataque < 0 ? "menos" : ""}" title="Goles de su equipo por encima de lo esperado cuando juega (contraído hacia 0)">${signoPct(j.ataque)}</td>
    <td class="num ${j.defensa < 0 ? "mas" : j.defensa > 0 ? "menos" : ""}" title="Goles recibidos por encima de lo esperado cuando juega (negativo = mejor)">${signoPct(j.defensa)}</td>
    <td class="num">${j.g90 == null ? "–" : num(j.g90, 2)}</td><td class="num">${j.prob_gol == null ? "–" : pct(j.prob_gol)}</td></tr>`).join("");
  const cambios = al.estado === "confirmada" && (x.sale.length || x.entra.length)
    ? `<p class="nota">Frente a su once habitual: ${x.sale.length ? `no son titulares <b>${esc(x.sale.join(", "))}</b>` : ""}${x.sale.length && x.entra.length ? "; " : ""}${x.entra.length ? `entran ${esc(x.entra.join(", "))}` : ""}.</p>` : "";
  return `<div class="once caja"><h4>${logo(eq.logo)}${esc(eq.n)} ${x.formacion ? `<span class="xi-badge">${esc(x.formacion)}</span>` : ""}</h4>
    <table class="t"><tr><th>Pos.</th><th>Jugador</th><th class="num" title="aporte en ataque">Atq.</th><th class="num" title="aporte en defensa">Def.</th><th class="num">Goles/90</th><th class="num">Marca</th></tr>${filas}</table>${cambios}</div>`;
}

function htmlComparativa(m) {
  const al = m.alineacion;
  const L = al.h.lineas, V = al.a.lineas;
  const fila = (txt, a, b) => {
    const max = Math.max(Math.abs(a), Math.abs(b), 0.05);
    return `<span class="lbl">${txt}</span><div class="b l"><div style="width:${Math.abs(a) / max * 100}%"></div></div><div class="b v"><div style="width:${Math.abs(b) / max * 100}%"></div></div>
      <span></span><span class="nota" style="text-align:right">${signoPct(a)}</span><span class="nota">${signoPct(b)}</span>`;
  };
  const atq = l => (l.DEL?.ataque || 0) + (l.MED?.ataque || 0);
  const def = l => -((l.DEF?.defensa || 0) + (l.POR?.defensa || 0));      // positivo = defiende mejor
  const est = (lado) => al[lado].estilo;
  const VAR = [["posesion", "Posesión", v => `${num(v, 0)}%`], ["pases", "Pases por partido", v => num(v, 0)], ["precision", "Precisión de pase", v => pct(v)],
    ["remates", "Remates", v => num(v, 1)], ["centros", "Centros", v => num(v, 1)], ["directo", "Juego directo (balones largos)", v => pct(v)],
    ["presion", "Presión (entradas + intercepciones)", v => num(v, 1)], ["despejes", "Despejes", v => num(v, 1)]];
  const estilo = est("h") && est("a") ? VAR.map(([k, n, f]) => {
    const a = est("h")[k], b = est("a")[k], max = Math.max(a, b, 1e-9);
    return `<span class="lbl">${n}</span><div class="b l"><div style="width:${a / max * 100}%"></div></div><div class="b v"><div style="width:${b / max * 100}%"></div></div>
      <span></span><span class="nota" style="text-align:right">${f(a)}</span><span class="nota">${f(b)}</span>`;
  }).join("") : "";
  const sim = lado => {
    const s = al[lado].vs_similares, eq = m[lado];
    if (!s) return `<p class="nota">${esc(eq.n)}: sin partidos contra rivales de estilo «${esc(al[lado].estilo_rival || "?")}».</p>`;
    return `<div class="dato"><span>${esc(eq.n)} contra rivales «${esc(al[lado].estilo_rival)}»</span><b>${s.g}-${s.e}-${s.p} en ${s.n} · goles ${num(s.gf, 2)} (esperaba ${num(s.gf_esperado, 2)}) · recibe ${num(s.gc, 2)} (esperaba ${num(s.gc_esperado, 2)})</b></div>`;
  };
  const casa = (lado, donde) => {
    const c = al.casa?.[lado]?.[donde];
    return c && c.n ? `<div class="dato"><span>${esc(m[lado].n)} de ${donde}</span><b>${c.g}-${c.e}-${c.p} en ${c.n} · ${num(c.puntos, 2)} pts/partido · goles ${num(c.gf, 2)} a favor, ${num(c.gc, 2)} en contra</b></div>` : "";
  };
  const previos = (al.previos || []).map(p => `<tr><td>${fmt(p.fecha, { day: "numeric", month: "short", year: "numeric" })}</td><td>${esc(p.local)} <b>${p.gl}-${p.gv}</b> ${esc(p.visita)}</td><td class="num">${p.coincide_h}/11</td><td class="num">${p.coincide_a}/11</td></tr>`).join("");
  return `<div class="caja" style="margin-top:10px"><div class="tit">Línea contra línea (aporte sumado de los titulares)</div>
      <div class="comp" style="margin-top:6px"><span></span><b style="text-align:right">${esc(m.h.n)}</b><b>${esc(m.a.n)}</b>
      ${fila("Ataque (medios + delanteros)", atq(L), atq(V))}${fila("Defensa (defensas + portero)", def(L), def(V))}</div>
      <p class="nota">Positivo = su equipo marca más (o recibe menos) de lo esperado cuando juegan estos jugadores. Con pocos minutos jugados el valor se acerca a 0.</p></div>
    ${estilo ? `<div class="caja" style="margin-top:10px"><div class="tit">Estilo de juego (promedio de sus últimos partidos)</div>
      <p class="nota">${esc(m.h.n)}: <b>${esc(al.h.grupo_estilo?.nombre || "?")}</b> · ${esc(m.a.n)}: <b>${esc(al.a.grupo_estilo?.nombre || "?")}</b></p>
      <div class="comp"><span></span><b style="text-align:right">${esc(m.h.n)}</b><b>${esc(m.a.n)}</b>${estilo}</div></div>` : ""}
    <div class="caja" style="margin-top:10px"><div class="tit">Contra rivales de estilo parecido · de local y de visita</div>${sim("h")}${sim("a")}${casa("h", "local")}${casa("a", "visita")}
      <p class="nota">Solo informativo: con pocos partidos estas diferencias son en gran parte azar (R15, R19), así que no cambian las probabilidades.</p></div>
    ${previos ? `<div class="caja" style="margin-top:10px"><div class="tit">Enfrentamientos anteriores y cuántos titulares de hoy jugaron</div><table class="t"><tr><th>Fecha</th><th>Partido</th><th class="num">${esc(m.h.c || "Local")}</th><th class="num">${esc(m.a.c || "Visita")}</th></tr>${previos}</table></div>` : ""}`;
}

function htmlAlineacion(m) {
  const al = m.alineacion;
  if (!al) {
    const liga = (DATOS.ligas_alineacion || []).includes(m.lg);
    return liga ? "" : `<div class="seccion"><h3>Alineaciones y simulación</h3><p class="nota">Esta liga no está entre las que tienen análisis de jugadores (grandes ligas, copas sudamericanas y Liga 1).</p></div>`;
  }
  const J = DATOS.jugadores || {};
  const efecto = al.estado === "confirmada"
    ? (al.beta > 0 ? `Con esta alineación los goles esperados cambian ×${num(al.factor_local, 3)} (${esc(m.h.n)}) y ×${num(al.factor_visita, 3)} (${esc(m.a.n)}), con tope ±25% (D24.5).`
      : `La alineación se muestra, pero no cambia las probabilidades: en la prueba con partidos pasados el ajuste por alineación no mejoró las predicciones (${J.n_verificacion ? `log-loss ${num(J.logloss_con, 4)} con ajuste frente a ${num(J.logloss_sin, 4)} sin él, ${J.n_verificacion} partidos` : "sin datos suficientes"}).`)
    : "Alineación probable = el once habitual de sus últimos partidos. La confirmada aparece más o menos 1 hora antes: la app la revisa cada 15 minutos.";
  const cal = J.goleadores;
  return `<div class="seccion"><h3>Alineaciones y simulación <small>${al.estado === "confirmada" ? "alineación confirmada" : "alineación probable"}</small></h3>
    <p class="nota">${efecto}</p>
    <div class="cancha">${htmlOnce(m, "h")}${htmlOnce(m, "a")}</div>
    <p class="nota">«Marca» = probabilidad de que el jugador anote si es titular${cal?.n ? `. En la prueba (${cal.n} titulares), el modelo de goleadores prometía ${pct(cal.pred_media, 1)} y marcaron ${pct(cal.real_media, 1)}` : ""}.</p>
    ${htmlComparativa(m)}
    </div>`;
}

// ---------------------------------------------------------------- noticias (Google Noticias)
const TIPO_NOTICIA = { lesion: "lesión", sancion: "sanción", duda: "duda / baja", dt: "entrenador", convocatoria: "convocatoria" };

function htmlNoticias(m) {
  const n = m.noticias;
  if (!n || m.estado !== "pre") return "";
  const lista = lado => (n[lado] || []).map(x => `<li><span class="not-tag ${esc(x.tipo)}">${esc(TIPO_NOTICIA[x.tipo] || x.tipo)}</span>
      <a href="${esc(x.link)}" target="_blank" rel="noopener noreferrer">${esc(x.titulo)}</a>
      <span class="nota">· ${esc(x.fuente)}${x.fecha ? ` · ${haceCuanto(x.fecha)}` : ""}</span></li>`).join("") || `<li class="nota">Sin noticias de bajas en los últimos días.</li>`;
  const esc_ = m.alineacion?.escenario_bajas;
  const escenario = esc_ ? `<div class="caja" style="margin-top:10px"><div class="tit">Escenario sin las posibles bajas</div>
      <p class="nota">Sin ${esc([...(esc_.fuera.h || []), ...(esc_.fuera.a || [])].join(", "))}${[...(esc_.entran.h || []), ...(esc_.entran.a || [])].length ? `; entrarían ${esc([...(esc_.entran.h || []), ...(esc_.entran.a || [])].join(", "))}` : ""} (el siguiente con más minutos de su misma línea).</p>
      <div class="tres"><div class="caja l"><div class="tit">${esc(m.h.n)}</div><div class="grande">${pct(esc_.pH, 1)}</div><div class="nota">ahora ${pct(m.pred.pH, 1)}</div></div>
        <div class="caja x"><div class="tit">Empate</div><div class="grande">${pct(esc_.pD, 1)}</div><div class="nota">ahora ${pct(m.pred.pD, 1)}</div></div>
        <div class="caja v"><div class="tit">${esc(m.a.n)}</div><div class="grande">${pct(esc_.pA, 1)}</div><div class="nota">ahora ${pct(m.pred.pA, 1)}</div></div></div>
      <p class="nota">Usa el aporte medido de cada jugador con el mismo ajuste validado de las alineaciones (×${num(esc_.factor_local, 3)} local, ×${num(esc_.factor_visita, 3)} visita). Es hipotético: la noticia puede ser falsa o de otra competición, por eso NO cambia la predicción principal.</p>
      <div class="acciones"><button class="boton" id="btn-sim-bajas">Simular este escenario</button></div></div>` : "";
  return `<div class="seccion"><h3>Noticias y bajas <small>Google Noticias · últimos 5 días · se revisa cada 3 horas</small></h3>
    <div class="dos-noticias"><div class="caja"><div class="tit">${logo(m.h.logo)}${esc(m.h.n)}</div><ul class="noticias">${lista("h")}</ul></div>
    <div class="caja"><div class="tit">${logo(m.a.logo)}${esc(m.a.n)}</div><ul class="noticias">${lista("a")}</ul></div></div>
    ${escenario}
    <p class="nota">Titulares de Marca, AS, ESPN, Depor, Líbero, Infobae y otros medios, filtrados por palabras como lesión, baja o sancionado. Se muestran como información: no cambian solas las probabilidades (no se pueden comprobar con partidos pasados, R19). Toca un titular para leer la nota completa.</p></div>`;
}

function htmlSimulacion(m) {
  const M = m.alineacion?.matriz || m.matriz;
  if (!M || m.estado !== "pre") return "";
  const al = m.alineacion;
  const quien = al?.goleadores?.length ? " y reparte cada gol entre los jugadores según su aporte goleador" : "";
  return `<div class="seccion"><h3>Simulación del partido <small>10 000 partidos simulados</small></h3>
    <div class="caja"><p class="nota">Juega el partido 10 000 veces con los goles esperados del modelo${al?.estado === "confirmada" ? " ajustados a la alineación confirmada" : ""}${quien}${m.extras?.corners ? ", y también los córners" : ""}${m.extras?.tarjetas ? " y las tarjetas" : ""}. Luego lo traduce a los mercados de Betano.</p>
      <div class="acciones"><button class="boton primario" id="btn-simular">Simular 10 000 partidos</button></div>
      <div class="sim-res" id="sim-res"></div></div></div>`;
}

// conteo con sobredispersión (binomial negativa = Poisson con media gamma), para córners y tarjetas
function gammaJS(k) {
  if (k < 1) return gammaJS(k + 1) * Math.pow(Math.random(), 1 / k);
  const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
  for (;;) {
    let x, v;
    do { const u1 = Math.random(), u2 = Math.random(); x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2); v = 1 + c * x; } while (v <= 0);
    v = v * v * v; const u = Math.random();
    if (u < 1 - 0.0331 * x ** 4 || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
}
function poissonJS(l) { const L = Math.exp(-l); let k = 0, p = 1; do { k++; p *= Math.random(); } while (p > L); return k - 1; }
function conteoJS(media, phi) { return phi > 1.02 ? poissonJS(gammaJS(media / (phi - 1)) * (phi - 1)) : poissonJS(media); }

function simularConteos(c, n) {
  if (!c) return null;
  const tot = new Array(n), dif = new Array(n);
  for (let k = 0; k < n; k++) { const a = conteoJS(c.local, c.phi || 1), b = conteoJS(c.visita, c.phi || 1); tot[k] = a + b; dif[k] = a - b; }
  const media = tot.reduce((x, y) => x + y, 0) / n;
  const p = f => tot.filter(f).length / n;
  return { media, mas: x => p(t => t > x), local: dif.filter(d => d > 0).length / n, igual: dif.filter(d => d === 0).length / n, visita: dif.filter(d => d < 0).length / n };
}

function simular(m, n = 10000, esc_ = null) {
  const al0 = m.alineacion || { goleadores: [], lh: m.pred.lh, la: m.pred.la };
  // escenario «sin posibles bajas»: otra matriz y sin los goles de los jugadores que no estarían
  const sinNombres = new Set(esc_ ? [...(esc_.fuera.h || []), ...(esc_.fuera.a || [])] : []);
  const al = esc_ ? Object.assign({}, al0, { lh: esc_.lh, la: esc_.la, goleadores: (al0.goleadores || []).filter(g => !sinNombres.has(g.n)) }) : al0;
  const M = esc_ ? esc_.matriz : (m.alineacion?.matriz || m.matriz);
  const celdas = [], acum = [];
  let s = 0;
  M.forEach((fila, i) => fila.forEach((p, j) => { s += p; celdas.push([i, j]); acum.push(s); }));
  const elegir = r => { let lo = 0, hi = acum.length - 1; while (lo < hi) { const mid = (lo + hi) >> 1; if (acum[mid] < r) lo = mid + 1; else hi = mid; } return celdas[lo]; };
  const gol = { h: al.goleadores.filter(g => g.lado === "h"), a: al.goleadores.filter(g => g.lado === "a") };
  const pesos = lado => {
    const lam = lado === "h" ? al.lh : al.la, js = gol[lado];
    const resto = Math.max(lam - js.reduce((t, g) => t + g.xg, 0), lam * 0.08);   // suplentes, autogoles
    const w = js.map(g => g.xg).concat([resto]), tot = w.reduce((a, b) => a + b, 0);
    let c = 0; return w.map(x => (c += x / tot));
  };
  const cum = { h: pesos("h"), a: pesos("a") };
  const marcadores = {}, marca = {};
  let H = 0, X = 0, A = 0;
  for (let k = 0; k < n; k++) {
    const [gh, ga] = elegir(Math.random() * s);
    marcadores[gh + "-" + ga] = (marcadores[gh + "-" + ga] || 0) + 1;
    if (gh > ga) H++; else if (gh === ga) X++; else A++;
    for (const [lado, g] of [["h", gh], ["a", ga]]) {
      const ya = new Set();
      for (let t = 0; t < g; t++) {
        const r = Math.random(), c = cum[lado]; let i = 0; while (i < c.length - 1 && c[i] < r) i++;
        if (i < gol[lado].length) ya.add(i);
      }
      ya.forEach(i => { const key = lado + i; marca[key] = (marca[key] || 0) + 1; });
    }
  }
  const tabla = Object.entries(marcadores).map(([k, v]) => [k, v / n]);
  const p = cond => tabla.reduce((t, [k, v]) => { const [a, b] = k.split("-").map(Number); return t + (cond(a, b) ? v : 0); }, 0);
  return { n, pH: H / n, pD: X / n, pA: A / n, tabla: tabla.sort((a, b) => b[1] - a[1]), p,
    goleadores: ["h", "a"].flatMap(l => gol[l].map((g, i) => ({ ...g, sim: (marca[l + i] || 0) / n }))).sort((a, b) => b.sim - a.sim) };
}

function cuotaMinimaJS(p) { return Math.ceil(Math.max(1.25, 1.04 / p, 1 / Math.max(p - 0.02, 1e-6)) * 100 - 1e-9) / 100; }

function mercadosBetano(m, r, escenario = false) {
  const L = m.h.n, V = m.a.n, P = r.p;
  // [mercado, selección, prob. simulada, prob. de devolución, código del mercado validado]
  const filas = [
    ["Resultado Final", `1 · ${L}`, r.pH, 0, "1"], ["Resultado Final", "X · Empate", r.pD, 0, "X"], ["Resultado Final", `2 · ${V}`, r.pA, 0, "2"],
    ["Doble Oportunidad", "1X", r.pH + r.pD, 0, "1X"], ["Doble Oportunidad", "X2", r.pD + r.pA, 0, "X2"], ["Doble Oportunidad", "12", r.pH + r.pA, 0, "12"],
    ["Apuesta sin Empate", L, r.pH / (r.pH + r.pA), r.pD, "DNB1"], ["Apuesta sin Empate", V, r.pA / (r.pH + r.pA), r.pD, "DNB2"],
    ...[1.5, 2.5, 3.5].flatMap(x => [["Total de Goles", `Más de ${num(x, 1)}`, P((a, b) => a + b > x), 0, `O${x}`], ["Total de Goles", `Menos de ${num(x, 1)}`, P((a, b) => a + b < x), 0, `U${x}`]]),
    ["Ambos Equipos Marcan", "Sí", P((a, b) => a > 0 && b > 0), 0, "BTTS_SI"], ["Ambos Equipos Marcan", "No", P((a, b) => a === 0 || b === 0), 0, "BTTS_NO"],
    [`Total de Goles ${L}`, "Más de 0,5", P(a => a > 0), 0, "L_O0.5"], [`Total de Goles ${L}`, "Más de 1,5", P(a => a > 1), 0, "L_O1.5"],
    [`Total de Goles ${V}`, "Más de 0,5", P((a, b) => b > 0), 0, "V_O0.5"], [`Total de Goles ${V}`, "Más de 1,5", P((a, b) => b > 1), 0, "V_O1.5"],
    ["Hándicap Asiático", `${L} -1,5`, P((a, b) => a - b >= 2), 0, "AH1-1.5"], ["Hándicap Asiático", `${V} +1,5`, P((a, b) => a - b <= 1), 0, "AH2+1.5"],
    ["Hándicap Asiático", `${V} -1,5`, P((a, b) => b - a >= 2), 0, "AH2-1.5"], ["Hándicap Asiático", `${L} +1,5`, P((a, b) => b - a <= 1), 0, "AH1+1.5"],
    ["Resultado Final y Ambos Marcan", `${L} y Sí`, P((a, b) => a > b && b > 0), 0, "1&SI"], ["Resultado Final y Ambos Marcan", `${L} y No`, P((a, b) => a > b && b === 0), 0, "1&NO"],
    ["Resultado Final y Ambos Marcan", "Empate y Sí", P((a, b) => a === b && a > 0), 0, "X&SI"], ["Resultado Final y Ambos Marcan", `${V} y Sí`, P((a, b) => b > a && a > 0), 0, "2&SI"],
    ["Resultado Final y Ambos Marcan", `${V} y No`, P((a, b) => b > a && a === 0), 0, "2&NO"],
    ["Resultado Final y Total de Goles", `${L} y Más de 2,5`, P((a, b) => a > b && a + b > 2.5), 0, "1&O2.5"], ["Resultado Final y Total de Goles", `${L} y Menos de 2,5`, P((a, b) => a > b && a + b < 2.5), 0, "1&U2.5"],
    ["Resultado Final y Total de Goles", `${V} y Más de 2,5`, P((a, b) => b > a && a + b > 2.5), 0, "2&O2.5"], ["Resultado Final y Total de Goles", `${V} y Menos de 2,5`, P((a, b) => b > a && a + b < 2.5), 0, "2&U2.5"],
    ["Portería a Cero", L, P((a, b) => b === 0), 0, "CS_L"], ["Portería a Cero", V, P(a => a === 0), 0, "CS_V"],
    ["Ganar sin Recibir Goles", L, P((a, b) => a > b && b === 0), 0, "WTN_L"], ["Ganar sin Recibir Goles", V, P((a, b) => b > a && a === 0), 0, "WTN_V"],
    ["Margen de Victoria", `${L} por 1`, P((a, b) => a - b === 1), 0, "M1_1"], ["Margen de Victoria", `${L} por 2`, P((a, b) => a - b === 2), 0, "M1_2"], ["Margen de Victoria", `${L} por 3 o más`, P((a, b) => a - b >= 3), 0, "M1_3+"],
    ["Margen de Victoria", `${V} por 1`, P((a, b) => b - a === 1), 0, "M2_1"], ["Margen de Victoria", `${V} por 2`, P((a, b) => b - a === 2), 0, "M2_2"], ["Margen de Victoria", `${V} por 3 o más`, P((a, b) => b - a >= 3), 0, "M2_3+"],
    ...[0, 1, 2, 3].map(t => ["Número Exacto de Goles", `${t} goles`, P((a, b) => a + b === t), 0, `T${t}`]), ["Número Exacto de Goles", "4 o más", P((a, b) => a + b >= 4), 0, "T4+"],
    ["Goles Par/Impar", "Par", P((a, b) => (a + b) % 2 === 0), 0, "PAR"], ["Goles Par/Impar", "Impar", P((a, b) => (a + b) % 2 === 1), 0, "IMPAR"],
    ...r.tabla.slice(0, 5).map(([k, v]) => ["Resultado Correcto", k.replace("-", " - "), v, 0, null]),
    ...r.goleadores.filter(g => g.historia).slice(0, 8).map(g => ["Goleador en cualquier momento", g.n, g.sim, 0, null]),
  ];
  // para los mercados que la app ya valida se decide con SU probabilidad y SU cuota mínima (con cuotas,
  // recalibración y puerta de calibración), no con la simulación: así nunca se contradicen
  // en un escenario hipotético no se usan las probabilidades validadas (son del partido tal como está)
  const validados = escenario ? {} : Object.fromEntries((m.apuesta?.mercados || []).map(x => [x.cod, x]));
  return filas.map(([mer, sel, psim, dev, cod]) => {
    const v = cod && validados[cod];
    return v ? { mer, sel, psim, p: v.p, dev: v.p_dev || 0, minima: v.no_calibrado ? null : v.minima, fuente: m.apuesta.base, bloqueado: !!v.no_calibrado }
      : { mer, sel, psim, p: psim, dev, minima: psim < 0.03 ? null : cuotaMinimaJS(psim), fuente: "simulación", bloqueado: false };
  });
}

function pintarSimulacion(m, esc_ = null) {
  const out = $("#sim-res"), btn = $("#btn-simular");
  btn.disabled = true; btn.textContent = "Simulando…";
  if (esc_) out.scrollIntoView({ behavior: "smooth", block: "start" });
  setTimeout(() => {
    const r = simular(m, 10000, esc_);
    const filas = mercadosBetano(m, r, !!esc_);
    const conteo = (c, nombre, lineas) => {
      const s = simularConteos(c, r.n);
      if (!s) return "";
      return `<div class="caja" style="margin-top:8px"><div class="tit">${nombre} simulados · media ${num(s.media, 1)}</div>
        <table class="t"><tr><th>Selección</th><th class="num">Simulación</th><th class="num">Justa</th></tr>
        ${lineas.map(x => `<tr><td>Más de ${num(x, 1)}</td><td class="num">${pct(s.mas(x), 1)}</td><td class="num">${num(1 / Math.max(s.mas(x), 1e-6))}</td></tr>
          <tr><td>Menos de ${num(x, 1)}</td><td class="num">${pct(1 - s.mas(x), 1)}</td><td class="num">${num(1 / Math.max(1 - s.mas(x), 1e-6))}</td></tr>`).join("")}
        <tr><td>Más ${nombre.toLowerCase()}: ${esc(m.h.n)}</td><td class="num">${pct(s.local, 1)}</td><td class="num">${num(1 / Math.max(s.local, 1e-6))}</td></tr>
        <tr><td>Igual número</td><td class="num">${pct(s.igual, 1)}</td><td class="num">${num(1 / Math.max(s.igual, 1e-6))}</td></tr>
        <tr><td>Más ${nombre.toLowerCase()}: ${esc(m.a.n)}</td><td class="num">${pct(s.visita, 1)}</td><td class="num">${num(1 / Math.max(s.visita, 1e-6))}</td></tr></table>
        <p class="nota">Para decidir usa la cuota mínima de la sección «Córners, tarjetas y tiempos»: esa sí está probada con partidos pasados.</p></div>`;
    };
    const lin = c => { const b = Math.round(c.total) - 0.5; return [b - 1, b, b + 1, b + 2].filter(x => x > 0); };
    const extrasSim = (m.extras?.corners ? conteo(m.extras.corners, "Córners", lin(m.extras.corners)) : "")
      + (m.extras?.tarjetas ? conteo(m.extras.tarjetas, "Tarjetas", lin(m.extras.tarjetas)) : "");
    const reco = pickDe(m);
    const html = filas.map((f, i) => `<tr><td class="nota">${esc(f.mer)}</td><td>${esc(f.sel)}${f.bloqueado ? ` <span class="nota">(no pasó la prueba de calibración)</span>` : ""}</td>
      <td class="num nota">${pct(f.psim, 1)}</td><td class="num" title="Probabilidad con la que se decide: ${f.fuente === "mercado" ? "ajustada a las cuotas del mercado" : f.fuente === "modelo" ? "modelo recalibrado" : "simulación"}">${pct(f.p, 1)}</td>
      <td class="num">${num(1 / Math.max(f.p, 1e-6))}</td><td class="num"><b>${f.minima ? num(f.minima) : "–"}</b></td>
      <td>${f.minima ? `<input type="number" step="0.01" min="1.01" data-i="${i}" placeholder="cuota">` : ""}</td><td class="nota" id="ev-${i}"></td></tr>`).join("");
    out.innerHTML = (esc_ ? `<div class="senal amarillo"><b>Escenario hipotético:</b> sin ${esc([...(esc_.fuera.h || []), ...(esc_.fuera.a || [])].join(", "))}${[...(esc_.entran.h || []), ...(esc_.entran.a || [])].length ? ` (entrarían ${esc([...(esc_.entran.h || []), ...(esc_.entran.a || [])].join(", "))})` : ""}. Es una simulación con lo que dicen las noticias, no una predicción comprobada: la tabla usa solo la simulación. Pulsa «Simular 10 000 partidos» para volver al partido normal.</div>` : "") + `<div class="tres">
        <div class="caja l"><div class="tit">Gana ${esc(m.h.n)}</div><div class="grande">${pct(r.pH, 1)}</div></div>
        <div class="caja x"><div class="tit">Empate</div><div class="grande">${pct(r.pD, 1)}</div></div>
        <div class="caja v"><div class="tit">Gana ${esc(m.a.n)}</div><div class="grande">${pct(r.pA, 1)}</div></div></div>
      <p class="nota">${r.n.toLocaleString("es-PE")} partidos simulados. Marcadores más repetidos: ${r.tabla.slice(0, 5).map(([k, v]) => `${k} (${pct(v, 1)})`).join(", ")}. La simulación usa la misma matriz del modelo, así que coincide con las probabilidades de arriba salvo por el azar de la simulación (±0,5 puntos).</p>
      ${reco ? `<p class="nota">Apuesta recomendada (perfil ${esc(DATOS.apuestas?.perfiles?.[perfilActual()]?.nombre || "")}): <b>${esc(reco.txt)}</b>${m.apuesta?.base === "mercado" ? " — usa las cuotas de la casa, que en las pruebas fueron más precisas que el modelo; por eso puede diferir un poco de la simulación." : "."}</p>` : ""}
      <h3 style="margin-top:12px">Mercados de Betano <small>escribe la cuota que ves en Betano para saber si conviene</small></h3>
      <table class="t"><tr><th>Mercado</th><th>Selección</th><th class="num">Simulación</th><th class="num">Prob. usada</th><th class="num">Justa</th><th class="num">Mínima</th><th>Cuota Betano</th><th></th></tr>${html}</table>
      <p class="nota">«Prob. usada» es la misma de la apuesta recomendada${m.apuesta?.base === "mercado" ? " (ajustada a las cuotas del mercado, que en las pruebas fueron más precisas que el modelo)" : " (modelo recalibrado)"}; en resultado correcto y goleadores se usa la simulación. Nombres de mercado como suelen aparecer en Betano (pueden variar un poco). Resultado correcto y goleador tienen mucho azar y la casa suele cobrar más margen en ellos. Aunque diga «conviene», no hay ganancia demostrada: es solo que tu cuota supera la mínima.</p>
      ${extrasSim}`;
    out.querySelectorAll("input[data-i]").forEach(inp => inp.oninput = () => {
      const i = +inp.dataset.i, f = filas[i], o = parseFloat(String(inp.value).replace(",", "."));
      const celda = $("#ev-" + i);
      if (!(o > 1)) { celda.textContent = ""; return; }
      const g = f.p * (1 - f.dev), ev = g * (o - 1) - (1 - g - f.dev);
      const alerta = Math.max(1.25 / f.p, f.minima + 0.15);
      celda.innerHTML = o >= alerta ? `<span class="ev-no">cuota demasiado alta: revisa noticias</span>`
        : o >= f.minima ? `<span class="ev-si">supera la mínima (${ev >= 0 ? "+" : ""}${num(ev * 100, 1)}% si la prob. es correcta)</span>`
        : o >= 1 / f.p ? `<span class="ev-duda">bajo la mínima: no apuestes</span>` : `<span class="ev-no">no conviene (${num(ev * 100, 1)}%)</span>`;
    });
    btn.disabled = false; btn.textContent = "Volver a simular";
  }, 30);
}

// ---------------------------------------------------------------- córners, tarjetas y tiempos
const NOMBRES_BETANO_EXTRA = { cor: "Córners", tarj: "Tarjetas", "1T": "1.er tiempo", "2T": "2.º tiempo" };

function filasExtra(lista, pref) {
  return lista.map(f => `<tr><td>${esc(f.txt)}${f.no_calibrado ? ` <span class="nota">(no pasó la prueba)</span>` : ""}</td>
    <td class="num">${pct(f.p, 1)}</td><td class="num">${num(f.justa)}</td><td class="num"><b>${f.minima ? num(f.minima) : "–"}</b></td>
    <td>${f.minima ? `<input type="number" step="0.01" min="1.01" data-x="${pref}|${esc(f.cod)}" placeholder="cuota">` : ""}</td>
    <td class="nota" id="x-${pref}-${esc(f.cod).replace(/\./g, "_")}"></td></tr>`).join("");
}

function htmlExtras(m) {
  const x = m.extras;
  if (!x || m.estado !== "pre") return "";
  const tabla = (lista, pref) => `<table class="t" style="margin-top:6px"><tr><th>Opción</th><th class="num">Prob.</th><th class="num">Justa</th><th class="num">Mínima</th><th>Tu cuota</th><th></th></tr>${filasExtra(lista, pref)}</table>`;
  let h = `<div class="seccion"><h3>Córners, tarjetas y tiempos <small>con los nombres de mercado de Betano</small></h3>`;
  if (x.corners) {
    const c = x.corners;
    h += `<details class="todas" open><summary>Córners · se esperan ${num(c.total, 1)} (${esc(m.h.n)} ${num(c.local, 1)} · ${esc(m.a.n)} ${num(c.visita, 1)}; media de la liga ${num(c.media_liga, 1)})</summary>${tabla(c.mercados, "cor")}</details>`;
  } else h += `<p class="nota">ESPN no publica córners de esta liga.</p>`;
  if (x.tarjetas) {
    const t = x.tarjetas, a = t.arbitro;
    const arb = a ? (a.n ? `Árbitro: <b>${esc(a.nombre)}</b>: ${num(a.por_partido, 2)} tarjetas por partido frente a ${num(a.media_liga, 2)} de media de su liga, en ${a.n} partidos → factor ×${num(a.factor, 2)} (contraído hacia 1 porque con pocos partidos no dice casi nada, R15).`
      : `Árbitro: <b>${esc(a.nombre)}</b> (sin partidos previos en los datos: no cambia el cálculo).`)
      : "Árbitro: aún no publicado o liga sin dato de árbitros (solo hay datos gratuitos de Inglaterra y Escocia).";
    h += `<details class="todas" open><summary>Tarjetas (amarillas + rojas) · se esperan ${num(t.total, 1)} (${esc(m.h.n)} ${num(t.local, 1)} · ${esc(m.a.n)} ${num(t.visita, 1)}; media de la liga ${num(t.media_liga, 1)})</summary>
      <p class="nota">${arb} Ojo: algunas casas cuentan la roja como 2 tarjetas o usan «puntos de tarjeta»; revisa la regla del mercado en Betano.</p>${tabla(t.mercados, "tarj")}</details>`;
  } else h += `<p class="nota">ESPN no detalla tarjetas de esta liga.</p>`;
  if (x.tiempos) {
    const t = x.tiempos;
    h += `<details class="todas"><summary>Primer y segundo tiempo · en esta liga el ${pct(t.proporcion_1t)} de los goles cae en el 1.er tiempo (goles esperados al descanso: ${num(t.lh_1t, 2)} – ${num(t.la_1t, 2)})</summary>${tabla(t.mercados, "tiempos")}</details>`;
  }
  const cal = (lista) => lista.filter(f => f.prueba).length;
  h += `<p class="nota">Cada mercado se probó con partidos pasados que el modelo no había visto: solo los que acertaron lo que prometían tienen cuota mínima. ${x.corners ? `Córners: ${cal(x.corners.mercados)} opciones probadas.` : ""} Nombres como suelen aparecer en Betano (pueden variar un poco).</p></div>`;
  return h;
}

function conectarExtras(m) {
  document.querySelectorAll("#detalle input[data-x]").forEach(inp => inp.oninput = () => {
    const [pref, cod] = inp.dataset.x.split("|");
    const grupo = pref === "cor" ? m.extras.corners : pref === "tarj" ? m.extras.tarjetas : m.extras.tiempos;
    const f = grupo.mercados.find(r => r.cod === cod);
    const celda = document.getElementById(`x-${pref}-${cod.replace(/\./g, "_")}`);
    const o = parseFloat(String(inp.value).replace(",", "."));
    if (!f || !(o > 1)) { celda.textContent = ""; return; }
    const ev = f.p * o - 1;
    celda.innerHTML = o >= f.alerta ? `<span class="ev-no">cuota demasiado alta: revisa noticias</span>`
      : o >= f.minima ? `<span class="ev-si">supera la mínima (${ev >= 0 ? "+" : ""}${num(ev * 100, 1)}% si la prob. es correcta)</span>`
      : o >= f.justa ? `<span class="ev-duda">bajo la mínima: no apuestes</span>` : `<span class="ev-no">no conviene (${num(ev * 100, 1)}%)</span>`;
  });
}

function conectarCalculadora(m) {
  const ci = $("#calc-cuota"), bi = $("#calc-banca"), out = $("#calc-res");
  if (!ci || !out) return;
  const inicial = out.innerHTML;
  const calcular = () => {
    const f = pickDe(m);
    const o = parseFloat(String(ci.value).replace(",", "."));
    UI.banca = bi.value; guardarPref();
    if (!f || !(o > 1)) { out.innerHTML = inicial; return; }
    const r = evaluarCuota(f, o, m.apuesta?.base);
    const banca = parseFloat(bi.value);
    const monto = banca > 0 && r.stake > 0 ? ` = <b>S/ ${num(banca * r.stake / 100, 2)}</b>` : "";
    const signo = x => `${x >= 0 ? "+" : ""}${num(x * 100, 1)}%`;
    out.innerHTML = `<div class="veredicto ${r.clase}">${r.veredicto}</div>
      <div class="cifras"><div class="cifra"><div class="t">Valor esperado por sol apostado</div><div class="v">${signo(r.ev)}</div><div class="nota">si la probabilidad central es correcta (no comprobado); con el rango del 80%: ${signo(r.rangoEv[0])} a ${signo(r.rangoEv[1])}</div></div>
      <div class="cifra"><div class="t">Cuánto apostar</div><div class="v">${num(r.stake, 2)}%</div><div class="nota">de tu banca${monto}</div></div></div>`;
  };
  ci.oninput = calcular; bi.oninput = calcular;
}

function htmlDetalle(m) {
  const p = predDe(m), c = m.pred.calc, ctx = m.ctx || {};
  const l = LIGAS[m.lg] || {};
  const fechaTxt = fmt(m.fecha, { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", hour12: false });
  const horaEstadio = m.tz && m.tz !== TZ ? ` · hora del estadio ${fmt(m.fecha, { hour: "2-digit", minute: "2-digit", hour12: false }, m.tz)}` : "";
  const marcador = (m.estado === "in" || m.fin) && m.h.g != null ? `<div class="marcador">${m.h.g} – ${m.a.g}</div>` : `<div class="marcador" style="color:var(--tenue)">vs</div>`;
  const aviso = m.fin ? (m.pred_previa ? `<p class="nota">Se muestra la predicción guardada antes del partido (${haceCuanto(m.pred_previa.guardado)} de su registro).</p>` :
    `<p class="nota">Este partido terminó antes de que el programa guardara una predicción; los números son recalculados con datos posteriores y no cuentan para los aciertos.</p>`) : "";

  // goles
  const dist = m.pred.dist_goles || [];
  const maxd = Math.max(...dist, 0.01);
  const barras = dist.map((v, i) => `<div class="col"><span class="p">${pct(v)}</span><div class="b" style="height:${v / maxd * 70}%"></div><span class="n">${i}${i === 6 ? "+" : ""}</span></div>`).join("");

  // disciplina
  const d = m.disciplina;
  const disc = d ? `<div class="rejilla">
      <div class="caja"><div class="tit">Tarjetas amarillas esperadas</div><div class="grande">${num(d.esp, 1)}</div><div class="ic">80%: ${d.ic[0]}–${d.ic[1]} · local ${num(d.local, 1)} · visita ${num(d.visita, 1)}</div></div>
      <div class="caja"><div class="tit">Gol de penal en el partido</div><div class="grande">${pct(d.p_penal)}</div><div class="ic">80%: ${rango(d.ic_penal)}</div></div>
      <div class="caja"><div class="tit">Al menos una expulsión</div><div class="grande">${pct(d.p_roja)}</div><div class="ic">80%: ${rango(d.ic_roja)}</div></div>
    </div><p class="nota">Calculado con el historial de cada equipo y la media de la competición, con regresión a la media (D22.16, R15). <b>No incluye el efecto del árbitro</b>: ESPN no publica la designación. ESPN solo registra penales convertidos, por eso se muestra «gol de penal».</p>`
    : `<p class="nota">Esta competición no tiene suficiente detalle de tarjetas en ESPN para estimarlas sin inventar (R13).</p>`;

  // mercado
  const mer = m.mercado;
  let mercado = `<p class="nota">ESPN no publica cuotas para este partido.</p>`;
  if (mer) {
    const fila = (n, a, b, ap) => `<tr><td>${n}</td><td class="num">${pct(a, 1)}</td><td class="num">${pct(b, 1)}</td><td class="num" style="color:${Math.abs(a - b) >= .1 ? "var(--amarillo)" : "inherit"}">${a - b >= 0 ? "+" : ""}${num((a - b) * 100, 1)}</td><td class="num">${ap != null ? pct(ap, 1) + " → " + pct(b, 1) : "–"}</td></tr>`;
    mercado = `<table class="t"><tr><th></th><th class="num">Modelo</th><th class="num">Mercado</th><th class="num">Dif. (pp)</th><th class="num">Apertura → cierre</th></tr>
      ${fila("Local", p.pH, mer.pH, mer.apertura?.pH)}${fila("Empate", p.pD, mer.pD, mer.apertura?.pD)}${fila("Visita", p.pA, mer.pA, mer.apertura?.pA)}</table>
      <p class="nota">Cuotas de ${esc(mer.proveedor || "la casa")} vía ESPN: ${num(mer.cuotas.h)} / ${num(mer.cuotas.x)} / ${num(mer.cuotas.a)}. Margen de la casa ${pct(mer.margen, 1)}, eliminado de forma proporcional. Si el modelo difiere mucho del mercado, revisa primero el modelo: el mercado suele conocer bajas y alineaciones (D24.15).</p>`;
  }

  // contexto
  const bloqueEquipo = (lado, eq) => {
    const x = ctx[lado] || {};
    const t = x.tabla ? `<div class="dato"><span>Tabla${x.tabla.grupo ? " (" + esc(x.tabla.grupo) + ")" : ""}</span><b>${x.tabla.pos}.º de ${x.tabla.total} · ${x.tabla.pts} pts · ${x.tabla.pj} PJ · DG ${x.tabla.dg > 0 ? "+" : ""}${x.tabla.dg}</b></div>${x.tabla.nota ? `<div class="dato"><span>Zona</span><b>${esc(x.tabla.nota)}</b></div>` : ""}` : "";
    const desc = x.descanso != null ? `<div class="dato"><span>Días desde su último partido</span><b>${num(x.descanso, 1)}</b></div>` : "";
    const ult = (x.ultimos || []).map(r => `<div class="mini-res"><span class="r ${r.r}">${r.r}</span>${r.loc ? "vs" : "en"} ${esc(r.rival)} <b>${r.gf}-${r.gc}</b><span class="s">${esc(nombreLiga(r.lg))} · ${fmt(r.fecha, { day: "numeric", month: "short" })}</span></div>`).join("") || `<p class="nota">Sin partidos previos en los datos.</p>`;
    const prox = (x.proximos || []).map(r => `<div class="mini-res">${r.loc ? "vs" : "en"} ${esc(r.rival)}<span class="s">${esc(nombreLiga(r.lg))} · ${fmt(r.fecha, { day: "numeric", month: "short" })}</span></div>`).join("");
    return `<div class="caja"><div style="display:flex;align-items:center;gap:8px;font-weight:700;margin-bottom:6px">${logo(eq.logo)}${esc(eq.n)}</div>${t}${desc}
      <div class="tit" style="margin-top:8px">Últimos partidos</div>${ult}${prox ? `<div class="tit" style="margin-top:8px">Siguientes</div>${prox}` : ""}</div>`;
  };
  const h2h = (ctx.h2h || []).map(r => `<tr><td>${fmt(r.fecha, { day: "numeric", month: "short", year: "numeric" })}</td><td>${esc(nombreLiga(r.lg))}</td><td>${esc(r.local)}</td><td class="num"><b>${r.gl}-${r.gv}</b></td><td>${esc(r.visita)}</td></tr>`).join("");

  // clima
  const cl = m.clima;
  const clima = cl ? `<div class="rejilla">
      <div class="dato"><span>Temperatura</span><b>${num(cl.temp, 0)} °C</b></div>
      <div class="dato"><span>Humedad</span><b>${num(cl.humedad, 0)}%</b></div>
      <div class="dato"><span>Lluvia</span><b>${num(cl.lluvia_mm, 1)} mm/h · ${num(cl.prob_lluvia, 0)}%</b></div>
      <div class="dato"><span>Viento</span><b>${num(cl.viento, 0)} km/h</b></div></div>
      <p class="nota">Pronóstico de Open-Meteo para la hora del partido. Se muestra como información y no cambia las probabilidades: no hay evidencia suficiente para cuantificar su efecto sin inventarlo (R19).</p>`
    : `<p class="nota">Sin pronóstico (partido a más de 8 días, ya jugado o estadio sin ubicar).</p>`;

  // cálculo transparente
  const neutral = m.neutral;
  const fH = neutral ? `√(H×V) = ${num(Math.sqrt(c.H_comp * c.V_comp), 3)}` : `H_comp ${num(c.H_comp, 3)} × h_estadio ${num(c.h_estadio, 3)}`;
  const fV = neutral ? `√(H×V) = ${num(Math.sqrt(c.H_comp * c.V_comp), 3)}` : `V_comp ${num(c.V_comp, 3)} × v_estadio ${num(c.v_estadio, 3)}`;
  const facs = (c.factores || []).map(f => `  · ${f.nombre}: coef ${num(f.coef, 4)} → ×${num(f.mult_local, 3)} local, ×${num(f.mult_visita, 3)} visita`).join("\n");
  const formula = `λ_local  = μ ${num(c.mu, 3)} × ${fH} × A_local ${num(c.A_local, 3)} × D_visita ${num(c.D_visita, 3)} × f ${num(c.f_local, 3)} = ${num(m.pred.lh, 3)}
λ_visita = μ ${num(c.mu, 3)} × ${fV} × A_visita ${num(c.A_visita, 3)} × D_local ${num(c.D_local, 3)} × g ${num(c.f_visita, 3)} = ${num(m.pred.la, 3)}

P(i, j) = Poisson(i; λ_local) × Poisson(j; λ_visita) × τ(i, j)   con ρ Dixon-Coles = ${num(c.rho, 3)}
P(local) = Σ P(i>j)   P(empate) = Σ P(i=j)   P(visita) = Σ P(i<j)

Partidos efectivos en la muestra: local ${num(c.n_local, 1)}, visita ${num(c.n_visita, 1)} (vida media ${DATOS.vida_media} días)
Media de su liga hacia la que se regresa A: local ${num(c.media_liga_A_local, 3)}, visita ${num(c.media_liga_A_visita, 3)}
Diferencia de altitud que sube el visitante: ${num(c.dalt_km * 1000, 0)} m
Factores acotados aplicados (estimados con los datos, R19):
${facs || "  · ninguno"}${c.mezcla ? `

Conjunto (ligas de nivel 3-4; probado semana a semana, mejora el 1X2 ~1%):
  · modelo principal (arriba, antes de mezclar): ${num(c.mezcla.lh_principal, 3)} – ${num(c.mezcla.la_principal, 3)}  peso ${num(c.mezcla.pesos.principal, 2)}
  · mismo modelo con memoria corta (120 días):   ${num(c.mezcla.lh_corta, 3)} – ${num(c.mezcla.la_corta, 3)}  peso ${num(c.mezcla.pesos.corta, 2)}
  · Elo (diferencia ${num(c.mezcla.elo_dif, 0)} puntos con localía): 1 ${pct(c.mezcla.elo_1x2[0], 1)} · X ${pct(c.mezcla.elo_1x2[1], 1)} · 2 ${pct(c.mezcla.elo_1x2[2], 1)}  peso ${num(c.mezcla.pesos.elo, 2)}
  · el TOTAL de goles se mantiene del modelo principal; la mezcla solo reparte entre local y visita → ${num(m.pred.lh, 3)} – ${num(m.pred.la, 3)}` : ""}`;

  const senales = (m.senales || []).map(s => `<div class="senal ${s.nivel}">${esc(s.texto)}</div>`).join("") || `<p class="nota">Sin señales especiales.</p>`;

  return `<div class="det-cab">
    <button class="cerrar" title="Cerrar">✕</button>
    <div class="meta">${l.nivel === 1 ? "★ " : ""}${esc(nombreLiga(m.lg))} · ${esc(l.pais || "")}${m.notas?.length ? " · " + esc(m.notas.join(" · ")) : ""}</div>
    <div class="meta">${esc(fechaTxt)}${horaEstadio}${m.estadio ? " · " + esc(m.estadio) : ""}${m.ciudad ? ", " + esc(m.ciudad) : ""}${m.alt != null ? ` · ${Math.round(m.alt)} m s. n. m.` : ""}${m.neutral ? " · sede neutral" : ""}</div>
    <div class="enfrentamiento">
      <div class="eq">${logo(m.h.logo)}<span>${esc(m.h.n)}</span></div>${marcador}<div class="eq v"><span>${esc(m.a.n)}</span>${logo(m.a.logo)}</div>
    </div>
  </div>
  <div class="det-cuerpo">
    ${aviso}
    <div class="seccion"><h3>Probabilidades del resultado <small>D24.14 · intervalo de incertidumbre del 80%</small></h3>
      <div class="tres">
        <div class="caja l"><div class="tit">Gana ${esc(m.h.n)}</div><div class="grande">${pct(p.pH)}</div><div class="ic">${rango(p.ic?.pH)}</div></div>
        <div class="caja x"><div class="tit">Empate</div><div class="grande">${pct(p.pD)}</div><div class="ic">${rango(p.ic?.pD)}</div></div>
        <div class="caja v"><div class="tit">Gana ${esc(m.a.n)}</div><div class="grande">${pct(p.pA)}</div><div class="ic">${rango(p.ic?.pA)}</div></div>
      </div>
      <p class="nota">Confianza <span class="conf ${m.pred.conf}">${m.pred.conf}</span>. Son probabilidades, no certezas (R17): un 60% falla 4 de cada 10 veces.</p>
    </div>
    ${htmlApuesta(m)}
    ${htmlNoticias(m)}
    ${htmlSimulacion(m)}
    ${htmlExtras(m)}
    ${htmlAlineacion(m)}
    <div class="seccion"><h3>Goles</h3>
      <div class="dos">
        <div class="caja">
          <div class="dato"><span>Goles esperados ${esc(m.h.n)}</span><b>${num(m.pred.lh)}</b></div>
          <div class="dato"><span>Goles esperados ${esc(m.a.n)}</span><b>${num(m.pred.la)}</b></div>
          <div class="dato"><span>Más de 1,5 goles</span><b>${pct(p.o15)}</b></div>
          <div class="dato"><span>Más de 2,5 goles</span><b>${pct(p.o25)} <span class="nota">(${rango(p.ic?.o25)})</span></b></div>
          <div class="dato"><span>Más de 3,5 goles</span><b>${pct(p.o35)}</b></div>
          <div class="dato"><span>Ambos marcan</span><b>${pct(p.btts)} <span class="nota">(${rango(p.ic?.btts)})</span></b></div>
        </div>
        <div class="caja"><div class="tit">Goles totales del partido</div><div class="barras-goles">${barras}</div></div>
      </div>
    </div>
    <div class="seccion"><h3>Marcadores más probables</h3>
      <div class="rejilla">${(m.pred.top || []).map(t => `<div class="caja"><div class="grande" style="font-size:22px">${t[0]} – ${t[1]}</div><div class="ic">${pct(t[2], 1)}</div></div>`).join("")}</div>
    </div>
    <div class="seccion"><h3>Señales del partido</h3>${senales}</div>
    <div class="seccion"><h3>Disciplina <small>tarjetas, penales y expulsiones</small></h3>${disc}</div>
    <div class="seccion"><h3>Comparación con el mercado <small>D24.15</small></h3>${mercado}</div>
    <div class="seccion"><h3>Contexto de cada equipo</h3><div class="dos">${bloqueEquipo("h", m.h)}${bloqueEquipo("a", m.a)}</div></div>
    ${ctx.ida ? `<div class="seccion"><h3>Partido de ida</h3><p>${esc(ctx.ida.local)} <b>${ctx.ida.gl}-${ctx.ida.gv}</b> ${esc(ctx.ida.visita)}</p></div>` : ""}
    <div class="seccion"><h3>Historial directo <small>D15.6 · solo informativo, no entra al modelo</small></h3>${h2h ? `<table class="t"><tr><th>Fecha</th><th>Competición</th><th>Local</th><th class="num"></th><th>Visita</th></tr>${h2h}</table>` : `<p class="nota">No se enfrentaron en el periodo con datos.</p>`}</div>
    <div class="seccion"><h3>Clima <small>D18 · D24.10</small></h3>${clima}</div>
    <div class="seccion"><h3>Cálculo transparente <small>R18 · replicable en Excel con POISSON.DIST</small></h3><div class="formula">${esc(formula)}</div></div>
    <div class="seccion"><h3>Análisis completo con tu Prompt Maestro</h3>
      <p class="nota">Copia el prompt en modo aplicado con la tabla de la sección 0 ya completada con estos datos. Lo que el programa no puede verificar (árbitro, alineaciones, lesionados, sanciones) queda marcado como «búscalo», como exige R13. Pégalo en Claude u otra IA para obtener las entregas 0 a 15.</p>
      <div class="acciones"><button class="boton primario" id="det-copiar">Copiar prompt aplicado</button><button class="boton" id="det-guardar">Guardar como .txt</button>${ESTATICO ? "" : `<button class="boton" id="det-pdf">Abrir el PDF del prompt</button>`}</div>
    </div>
    <div class="seccion"><h3>Límites <small>R20</small></h3><p class="nota">Ningún modelo garantiza acertar ni ganar dinero en apuestas. Este modelo usa resultados, localía, altitud, calendario, fuerza de los rivales y, en las ligas principales, la alineación (con un efecto pequeño y acotado, porque así lo indicó la prueba). No conoce lesiones no reflejadas en la alineación, cambios de entrenador ni el estado anímico del plantel. Úsalo como una referencia calibrada, no como una certeza.</p></div>
  </div>`;
}

// ---------------------------------------------------------------- prompt aplicado
async function textoPrompt(m) {
  if (PROMPT === null) {
    try { PROMPT = await api("/api/prompt"); } catch { PROMPT = ""; }
  }
  const p = predDe(m), c = m.pred.calc, ctx = m.ctx || {}, l = LIGAS[m.lg] || {};
  const B = "búscalo";
  const fechaEstadio = m.tz ? fmt(m.fecha, { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }, m.tz) + ` (hora local del estadio, ${m.tz})` : B;
  const fechaPeru = fmt(m.fecha, { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }, "America/Lima") + " (hora de Perú)";
  const tabla = lado => {
    const t = ctx[lado]?.tabla;
    return t ? `${t.pos}.º de ${t.total}${t.grupo ? " en " + t.grupo : ""}, ${t.pts} pts en ${t.pj} PJ, DG ${t.dg}${t.nota ? ", zona: " + t.nota : ""}` : "sin tabla en ESPN";
  };
  const ult = lado => (ctx[lado]?.ultimos || []).map(r => `${fmt(r.fecha, { day: "2-digit", month: "2-digit" })} ${r.loc ? "vs" : "en"} ${r.rival} ${r.gf}-${r.gc} (${nombreLiga(r.lg)})`).join("; ") || "sin datos";
  const prox = lado => (ctx[lado]?.proximos || []).map(r => `${fmt(r.fecha, { day: "2-digit", month: "2-digit" })} ${r.loc ? "vs" : "en"} ${r.rival} (${nombreLiga(r.lg)})`).join("; ") || "sin datos";
  const cl = m.clima;
  const mer = m.mercado;
  const dias = Math.max(0, Math.round((new Date(m.fecha) - Date.now()) / 864e5));
  const filas = [
    ["Competición, fase y jornada", `${l.nombre || m.lg} (${l.pais || ""})${m.notas?.length ? " — " + m.notas.join(" — ") : ""}${ctx.h?.tabla?.grupo ? " — " + ctx.h.tabla.grupo : ""}; jornada: ${B}`],
    ["Fecha y hora local", `${fechaEstadio}; ${fechaPeru}`],
    ["Estadio, ciudad y altitud", `${m.estadio || B}, ${m.ciudad || B} (${m.pais || ""}); altitud ${m.alt != null ? Math.round(m.alt) + " m s. n. m. (altitud de la ciudad según Open-Meteo)" : B}${m.neutral ? "; sede neutral" : ""}`],
    ["Equipo local", m.h.n],
    ["Equipo visitante", m.a.n],
    ["Situación en la tabla y objetivo de cada equipo", `${m.h.n}: ${tabla("h")}. ${m.a.n}: ${tabla("a")}. Objetivo de cada equipo: ${B}`],
    ["Partidos previos y siguientes de cada equipo (calendario y competiciones)", `${m.h.n} — previos: ${ult("h")} | siguientes: ${prox("h")} | días de descanso: ${ctx.h?.descanso ?? "?"}. ${m.a.n} — previos: ${ult("a")} | siguientes: ${prox("a")} | días de descanso: ${ctx.a?.descanso ?? "?"}`],
    ["Resultado de la ida (si es eliminatoria)", ctx.ida ? `${ctx.ida.local} ${ctx.ida.gl}-${ctx.ida.gv} ${ctx.ida.visita}`
      : (l.tipo !== "copa" ? "no aplica" : ctx.h?.tabla ? "no aplica (fase de liga o de grupos)" : B)],
    ["Árbitro principal", `${B} (ESPN no publica la designación)`],
    ["Asistentes 1 y 2", B], ["Cuarto árbitro", B], ["VAR y AVAR", B],
    ["Alineaciones probables o confirmadas", "búscalas"],
    ["Lesionados, dudas y sancionados", "búscalos"],
    ["Jugadores al borde de suspensión por tarjetas", "búscalos"],
    ["Cambios recientes (entrenador nuevo, fichajes, ascenso)", "búscalos"],
    ["Pronóstico del clima y estado del césped", cl ? `Open-Meteo para la hora del partido: ${num(cl.temp, 0)} °C, humedad ${num(cl.humedad, 0)}%, lluvia ${num(cl.lluvia_mm, 1)} mm/h (probabilidad ${num(cl.prob_lluvia, 0)}%), viento ${num(cl.viento, 0)} km/h. Estado del césped: ${B}` : B],
    ["Cuotas de referencia del mercado (opcional)", mer ? `${mer.proveedor} vía ESPN (cuotas decimales 1/X/2): ${num(mer.cuotas.h)} / ${num(mer.cuotas.x)} / ${num(mer.cuotas.a)}; margen ${pct(mer.margen, 1)}; probabilidades implícitas sin margen: ${pct(mer.pH, 1)} / ${pct(mer.pD, 1)} / ${pct(mer.pA, 1)}${mer.apertura ? `; en la apertura: ${pct(mer.apertura.pH, 1)} / ${pct(mer.apertura.pD, 1)} / ${pct(mer.apertura.pA, 1)}` : ""}${mer.linea_goles != null ? `; línea de goles ${mer.linea_goles}` : ""}` : "búscalas"],
    ["Momento de la predicción", `${dias} días antes; alineaciones no confirmadas`],
    ["Punto de vista del análisis", "neutral"],
    ["Tipo de análisis", "pre-partido"],
    ["Datos o archivos que adjunto", "Salida del modelo D24 calculada con código (ver bloque siguiente)"],
  ];
  const d = m.disciplina;
  const salida = `SALIDA DEL MODELO D24 (calculada en Python por el programa; R18)
Método: Poisson bivariado con corrección Dixon-Coles (ρ = ${c.rho}), fuerzas de ataque (A) y defensa (D) ponderadas por antigüedad (vida media ${DATOS.vida_media} días), regresión a la media de la liga, localía propia de la competición y del estadio, y ajustes acotados estimados con datos (altitud, descanso, rotación). Intervalos del 80% por simulación de la incertidumbre de A y D.
- Goles esperados: ${m.h.n} ${num(m.pred.lh, 2)} | ${m.a.n} ${num(m.pred.la, 2)}
- 1X2: local ${pct(p.pH, 1)} (${rango(p.ic?.pH)}), empate ${pct(p.pD, 1)} (${rango(p.ic?.pD)}), visita ${pct(p.pA, 1)} (${rango(p.ic?.pA)})
- Más de 1,5 / 2,5 / 3,5 goles: ${pct(p.o15, 1)} / ${pct(p.o25, 1)} / ${pct(p.o35, 1)}; ambos marcan: ${pct(p.btts, 1)}
- 5 marcadores más probables: ${(m.pred.top || []).map(t => `${t[0]}-${t[1]} (${pct(t[2], 1)})`).join(", ")}
- Disciplina (sin efecto árbitro): ${d ? `amarillas esperadas ${num(d.esp, 1)} (80%: ${d.ic[0]}-${d.ic[1]}); gol de penal ${pct(d.p_penal)}; al menos una expulsión ${pct(d.p_roja)}` : "sin datos suficientes en esta competición"}
- Parámetros: μ ${c.mu}; H_comp ${c.H_comp}; V_comp ${c.V_comp}; h_estadio ${c.h_estadio}; A_local ${c.A_local}; D_local ${c.D_local}; A_visita ${c.A_visita}; D_visita ${c.D_visita}; partidos efectivos ${c.n_local} / ${c.n_visita}; diferencia de altitud ${Math.round(c.dalt_km * 1000)} m
- Validación retrospectiva del programa (últimas ${DATOS.modelo?.semanas_validacion ?? "–"} semanas): ${DATOS.modelo?.validacion?.global ? `RPS ${DATOS.modelo.validacion.global.rps} (referencia ingenua ${DATOS.modelo.validacion.global.rps_base}), acierto del favorito ${pct(DATOS.modelo.validacion.global.acierto, 1)} en ${DATOS.modelo.validacion.global.n} partidos` : "pendiente"}
- Señales detectadas: ${(m.senales || []).map(s => s.texto).join(" | ") || "ninguna"}
- Apuesta recomendada por el programa (perfil ${perfilActual()}): ${(() => { const f = pickDe(m); return f ? `${f.txt}; probabilidad ${pct(f.p, 1)} (80%: ${pct(f.p10, 1)}-${pct(f.p90, 1)}); cuota justa ${num(f.justa)}; cuota mínima ${num(f.minima)}; base: ${m.apuesta.base === "mercado" ? "probabilidades ajustadas a las cuotas" : "modelo"}` : "no disponible"; })()}`;
  const tablaTxt = filas.map(([a, b]) => `| ${a} | ${String(b).replace(/\|/g, "/")} |`).join("\n");
  return `PROMPT MAESTRO v3 — MODO APLICADO
Partido: ${m.h.n} vs ${m.a.n} — ${nombreLiga(m.lg)} — ${fechaPeru}
Datos completados automáticamente por el programa «Predicciones Fútbol» (${fmt(DATOS.generado, { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}).
Fuentes: ESPN (site.api.espn.com: resultados, calendario, tablas, cuotas) y Open-Meteo (open-meteo.com: altitud y clima), consultadas en esa fecha. Los campos marcados «búscalo» no están disponibles en esas fuentes: verifícalos antes de usarlos (R13).

TABLA DE ENTRADA (SECCIÓN 0) COMPLETADA
| Campo | Dato |
|---|---|
${tablaTxt}

${salida}

────────────────────────────────────────
${PROMPT}`;
}

async function copiarPrompt(m) {
  const t = await textoPrompt(m);
  try { await navigator.clipboard.writeText(t); toast("Prompt copiado: pégalo en Claude"); }
  catch {
    const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove(); toast("Prompt copiado");
  }
}
async function guardarPrompt(m) {
  const t = await textoPrompt(m);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([t], { type: "text/plain;charset=utf-8" }));
  a.download = `Prompt aplicado - ${m.h.n} vs ${m.a.n}.txt`.replace(/[\\/:*?"<>|]/g, "");
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

// ---------------------------------------------------------------- vista: resultados
function kpi(tit, val, sub) { return `<div class="kpi"><div class="tit">${tit}</div><div class="val">${val}</div><div class="sub">${sub || ""}</div></div>`; }

function pintarResultados() {
  if (!DATOS) return;
  const r = DATOS.historial?.resumen || {};
  const k = [];
  if (r.n) {
    const t = r.total, u = r.ult30;
    k.push(kpi("Acierto del favorito (total)", pct(t.acierto, 1), `${t.n} partidos registrados`));
    if (u) k.push(kpi("Acierto últimos 30 días", pct(u.acierto, 1), `${u.n} partidos`));
    k.push(kpi("RPS (más bajo es mejor)", num(t.rps, 3), `Brier ${num(t.brier, 3)} · log-loss ${num(t.logloss, 3)}`));
    k.push(kpi("Más/menos de 2,5 goles", pct(t.o25, 1), `Ambos marcan: ${pct(t.btts, 1)}`));
    if (t.n_mercado) k.push(kpi("Modelo vs mercado (RPS)", `${num(t.rps_modelo_vs, 3)} vs ${num(t.rps_mercado, 3)}`, `${t.n_mercado} partidos con cuotas`));
  } else {
    k.push(kpi("Aún sin partidos cerrados", "–", "El registro empieza hoy: cada predicción se guarda antes del partido y se evalúa al terminar."));
  }
  const v = DATOS.modelo?.validacion?.global;
  if (v) k.push(kpi("Prueba retrospectiva", pct(v.acierto, 1), `RPS ${num(v.rps, 3)} vs ${num(v.rps_base, 3)} ingenuo · ${v.n} partidos`));
  $("#kpis-hist").innerHTML = k.join("") + htmlRendimientoApuestas();

  const lista = DATOS.historial?.lista || [];
  const ligas = [...new Set(lista.map(x => x.lg))].sort((a, b) => (ORDEN[a] ?? 99) - (ORDEN[b] ?? 99));
  $("#filtro-hist").innerHTML = `<option value="">Todas las ligas</option>` + ligas.map(l => `<option value="${l}" ${UI.ligaHist === l ? "selected" : ""}>${esc(nombreLiga(l))}</option>`).join("");
  const filtrada = lista.filter(x => !UI.ligaHist || x.lg === UI.ligaHist);
  const porLiga = r.por_liga || {};
  let resumenLigas = "";
  const filasLigas = Object.entries(porLiga).sort((a, b) => b[1].n - a[1].n).map(([lg, b]) =>
    `<tr><td>${esc(nombreLiga(lg))}</td><td class="num">${b.n}</td><td class="num">${pct(b.acierto, 1)}</td><td class="num">${num(b.rps, 3)}</td><td class="num">${pct(b.o25, 1)}</td><td class="num">${pct(b.btts, 1)}</td></tr>`).join("");
  if (filasLigas) resumenLigas = `<h3>Por liga</h3><table class="t"><tr><th>Liga</th><th class="num">Partidos</th><th class="num">Acierto</th><th class="num">RPS</th><th class="num">+/-2,5</th><th class="num">Ambos marcan</th></tr>${filasLigas}</table>`;
  const filas = filtrada.map(x => {
    const u = x.ultima, rr = x.res, mt = x.met || {};
    const fav = { H: "Local", D: "Empate", A: "Visita" }[[["H", u.pH], ["D", u.pD], ["A", u.pA]].sort((a, b) => b[1] - a[1])[0][0]];
    return `<tr><td>${fmt(x.fecha, { day: "numeric", month: "short" })}</td><td>${esc(nombreLiga(x.lg))}</td><td>${esc(x.h)} – ${esc(x.a)}</td>
      <td class="num">${pct(u.pH)} / ${pct(u.pD)} / ${pct(u.pA)}</td><td>${fav}</td><td class="num"><b>${rr[0]}-${rr[1]}</b></td>
      <td><span class="acierto-badge ${mt.acierto ? "si" : "no"}">${mt.acierto ? "SÍ" : "NO"}</span></td><td class="num">${num(mt.rps, 3)}</td></tr>`;
  }).join("");
  $("#tabla-hist").innerHTML = resumenLigas + `<h3>Detalle</h3>` + (filas ? `<table class="t"><tr><th>Fecha</th><th>Liga</th><th>Partido</th><th class="num">Pred. 1/X/2</th><th>Favorito</th><th class="num">Resultado</th><th>Acertó</th><th class="num">RPS</th></tr>${filas}</table>` : `<p class="nota">Todavía no hay partidos evaluados. Vuelve después de la primera jornada.</p>`);
}

function htmlRendimientoApuestas() {
  const A = DATOS.apuestas || {}, pr = A.prueba, vivo = DATOS.historial?.resumen?.apuestas || {};
  const nombres = A.perfiles || {};
  const filas = Object.keys(nombres).map(k => {
    const b = pr?.perfiles?.[k], h = vivo[k], c = b?.con_cuota_real;
    return `<tr${k === perfilActual() ? ' style="outline:1px solid var(--acento)"' : ""}><td><b>${esc(nombres[k].nombre)}</b></td>
      <td class="num">${b ? `${pct(b.acierto, 1)} <span class="nota">(esperado ${pct(b.esperado, 1)}, n=${b.n})</span>` : "–"}</td>
      <td class="num">${c ? `${c.ic[0] > 0 ? "+" : ""}${pct(c.roi, 1)}${c.n < 300 || (c.ic[0] <= 0 && c.ic[1] >= 0) ? " · sin evidencia de ganancia" : ""} <span class="nota">(80%: ${pct(c.ic[0], 1)} a ${pct(c.ic[1], 1)}; n=${c.n} de ${b.n}, ${pct(b.fraccion_con_cuota, 1)} con cuota real)</span>` : "–"}</td>
      <td class="num">${h ? `${pct(h.acierto, 1)} <span class="nota">(esperado ${pct(h.esperado, 1)}, n=${h.n})</span>` : "aún sin datos"}</td>
      <td class="num">${h?.roi != null ? `${h.roi >= 0 ? "+" : ""}${pct(h.roi, 1)} <span class="nota">(n=${h.n_cuota})</span>` : "–"}</td></tr>`;
  }).join("");
  const peso = pr?.peso, fav = pr?.favorito_modelo, val = pr?.valor;
  return `<div style="grid-column:1/-1"><h3>Apuestas recomendadas</h3>
    <table class="t"><tr><th>Perfil</th><th class="num">Acierto en la prueba</th><th class="num">Ganancia con cuota real de cierre</th><th class="num">Acierto en vivo</th><th class="num">Ganancia en vivo (cuotas ESPN)</th></tr>${filas}</table>
    <p class="nota">El acierto se mide en los ${pr?.n_verificacion ?? "–"} partidos más recientes de la prueba retrospectiva (el último 30%), que no se usaron para ajustar nada: la recalibración se aprendió con el 40% más antiguo y la puerta de calibración y el escenario prudente se decidieron con el 30% intermedio. La ganancia solo se puede medir cuando la apuesta elegida tiene cuota real de cierre (1X2 o más/menos 2,5 de Bet365 o del promedio del mercado, football-data.co.uk; ${pr?.emparejados ?? "–"} partidos con cuota histórica disponible).
    ${peso ? `Precisión del 1X2 (log-loss, más bajo es mejor): modelo ${num(peso.logloss_modelo, 4)}, cuota de la casa ${num(peso.logloss_mercado, 4)} en ${peso.n} partidos.` : ""}
    ${fav ? `Apostar siempre al favorito del modelo habría rendido ${pct(fav.roi, 1)}.` : ""}
    ${val ? `La estrategia «apuesta de valor» rindió ${pct(val.roi, 1)} en ${val.n} apuestas: por eso está ${pr.valor_habilitado ? "encendida" : "apagada"}.` : ""}
    La ganancia real depende de la cuota que consigas: si tu casa paga por encima de la cuota mínima, mejora; si paga menos, empeora.</p></div>`;
}

// ---------------------------------------------------------------- vista: tablas
function pintarTablas() {
  if (!DATOS) return;
  const t = DATOS.tablas || {};
  const codigos = Object.keys(t).sort((a, b) => ((LIGAS[a]?.nivel || 9) - (LIGAS[b]?.nivel || 9)) || ((ORDEN[a] ?? 99) - (ORDEN[b] ?? 99)));
  if (!UI.tabla || !t[UI.tabla]) UI.tabla = codigos[0] || "";
  $("#sel-tabla").innerHTML = codigos.map(c => `<option value="${c}" ${UI.tabla === c ? "selected" : ""}>${LIGAS[c]?.nivel === 1 ? "★ " : ""}${esc(nombreLiga(c))} · ${esc(LIGAS[c]?.pais || "")}</option>`).join("");
  const grupos = t[UI.tabla] || [];
  $("#tabla-pos").innerHTML = grupos.map(g => `<h3>${esc(g.nombre)}</h3><table class="t">
    <tr><th>#</th><th>Equipo</th><th class="num">PJ</th><th class="num">G</th><th class="num">E</th><th class="num">P</th><th class="num">GF</th><th class="num">GC</th><th class="num">DG</th><th class="num">Pts</th><th>Zona</th></tr>
    ${g.filas.map(f => `<tr><td>${f.color ? `<span class="zona" style="background:${/^#?[0-9a-f]{6}$/i.test(f.color) ? (f.color[0] === "#" ? f.color : "#" + f.color) : "transparent"}"></span>` : ""}${f.pos}</td><td>${logo(f.logo || `https://a.espncdn.com/i/teamlogos/soccer/500/${f.id}.png`)}${esc(f.n)}</td><td class="num">${f.pj}</td><td class="num">${f.g}</td><td class="num">${f.e}</td><td class="num">${f.p}</td><td class="num">${f.gf}</td><td class="num">${f.gc}</td><td class="num">${f.dg > 0 ? "+" : ""}${f.dg}</td><td class="num"><b>${f.pts}</b></td><td class="nota">${esc(f.nota)}</td></tr>`).join("")}
  </table>`).join("") || `<p class="nota">Sin tabla disponible.</p>`;
}

// ---------------------------------------------------------------- vista: modelo
function svgCalibracion(cal) {
  if (!cal?.length) return "";
  const W = 320, H = 320, m = 36;
  const x = v => m + v * (W - 2 * m), y = v => H - m - v * (H - 2 * m);
  const maxn = Math.max(...cal.map(c => c.n));
  const ticks = [0, .2, .4, .6, .8, 1];
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Curva de calibración">
    ${ticks.map(t => `<line x1="${x(t)}" y1="${y(0)}" x2="${x(t)}" y2="${y(1)}" stroke="#1f2a40"/><line x1="${x(0)}" y1="${y(t)}" x2="${x(1)}" y2="${y(t)}" stroke="#1f2a40"/>
      <text x="${x(t)}" y="${H - m + 16}" fill="#93a0ba" font-size="10" text-anchor="middle">${t * 100}%</text><text x="${m - 6}" y="${y(t) + 3}" fill="#93a0ba" font-size="10" text-anchor="end">${t * 100}%</text>`).join("")}
    <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="#64708a" stroke-dasharray="4 4"/>
    <polyline fill="none" stroke="#10b981" stroke-width="2" points="${cal.map(c => `${x(c.pred)},${y(c.real)}`).join(" ")}"/>
    ${cal.map(c => `<circle cx="${x(c.pred)}" cy="${y(c.real)}" r="${3 + 5 * Math.sqrt(c.n / maxn)}" fill="#10b981"><title>Predicho ${pct(c.pred, 1)} → ocurrió ${pct(c.real, 1)} (n=${c.n})</title></circle>`).join("")}
    <text x="${W / 2}" y="${H - 4}" fill="#93a0ba" font-size="11" text-anchor="middle">Probabilidad predicha</text>
    <text x="11" y="${H / 2}" fill="#93a0ba" font-size="11" text-anchor="middle" transform="rotate(-90 11 ${H / 2})">Frecuencia real</text>
  </svg>`;
}

function pintarModelo() {
  if (!DATOS) return;
  const M = DATOS.modelo || {}, v = M.validacion;
  let h = `<h2>Cómo rinde el modelo</h2>`;
  if (v?.global) {
    const g = v.global;
    h += `<div class="kpis">
      ${kpi("RPS del modelo", num(g.rps, 4), `Referencia ingenua: ${num(g.rps_base, 4)} (más bajo es mejor)`)}
      ${kpi("Acierto del favorito", pct(g.acierto, 1), `Ingenuo: ${pct(g.acierto_base, 1)}`)}
      ${kpi("Brier · log-loss", `${num(g.brier, 3)} · ${num(g.logloss, 3)}`, `${g.n} partidos de prueba`)}
      ${kpi("Memoria del modelo", `${DATOS.vida_media} días`, "Vida media elegida por validación")}
    </div>
    <p class="nota">Prueba retrospectiva (D24.17): cada semana de las últimas ${M.semanas_validacion ?? "–"} se predijo usando solo los partidos anteriores a esa semana. Fecha: ${M.fecha_validacion ? fmt(M.fecha_validacion, { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", hour12: false }) : "–"}.</p>
    <div class="dos"><div class="caja"><div class="tit">Curva de calibración: si dice 60%, ¿ocurre el 60%?</div>${svgCalibracion(v.calibracion)}</div>
    <div class="caja"><div class="tit">Por competición (prueba retrospectiva)</div><table class="t"><tr><th>Liga</th><th class="num">n</th><th class="num">RPS</th><th class="num">Ingenuo</th><th class="num">Acierto</th></tr>
    ${Object.entries(v.por_liga || {}).sort((a, b) => (ORDEN[a[0]] ?? 99) - (ORDEN[b[0]] ?? 99)).map(([lg, b]) => `<tr><td>${esc(nombreLiga(lg))}</td><td class="num">${b.n}</td><td class="num" style="color:${b.rps < b.rps_base ? "var(--acento)" : "var(--rojo)"}">${num(b.rps, 3)}</td><td class="num">${num(b.rps_base, 3)}</td><td class="num">${pct(b.acierto, 1)}</td></tr>`).join("")}</table></div></div>`;
  }
  if (M.candidatas) {
    h += `<h3>Elección de la memoria (vida media)</h3><table class="t"><tr><th>Vida media</th><th class="num">RPS</th><th class="num">Acierto</th><th class="num">n</th></tr>
      ${Object.entries(M.candidatas).map(([vd, b]) => `<tr><td>${vd} días${Number(vd) === DATOS.vida_media ? " ✓" : ""}</td><td class="num">${num(b.rps, 4)}</td><td class="num">${pct(b.acierto, 1)}</td><td class="num">${b.n}</td></tr>`).join("")}</table>`;
  }
  h += `<h3>Ajustes acotados estimados con datos (R19)</h3><p class="nota">Coeficientes en escala logarítmica: 0 = sin efecto; 0,05 ≈ +5% de goles. Cada uno tiene un prior centrado en 0, así que solo se alejan de 0 si los datos lo respaldan.</p>
    <table class="t"><tr><th>Factor</th><th class="num">Coeficiente</th><th class="num">Efecto</th></tr>
    ${Object.entries(M.coef || {}).map(([n, b]) => `<tr><td>${esc(n)}</td><td class="num">${num(b, 4)}</td><td class="num">${b >= 0 ? "+" : ""}${num((Math.exp(b) - 1) * 100, 1)}%</td></tr>`).join("")}
    <tr><td>ρ Dixon-Coles (ajuste de marcadores bajos)</td><td class="num">${num(M.rho, 3)}</td><td></td></tr>
    <tr><td>μ (goles por equipo en cancha neutral)</td><td class="num">${num(M.mu, 3)}</td><td></td></tr></table>`;
  const comps = Object.entries(M.competiciones || {}).sort((a, b) => (ORDEN[a[0]] ?? 99) - (ORDEN[b[0]] ?? 99));
  h += `<h3>Localía por competición (D24.4)</h3><table class="t"><tr><th>Competición</th><th class="num">Factor local (H)</th><th class="num">Factor visita (V)</th><th class="num">H/V</th><th class="num">Partidos último año</th><th class="num">Cobertura de tarjetas</th></tr>
    ${comps.map(([c, x]) => `<tr><td>${esc(nombreLiga(c))}</td><td class="num">${num(x.H, 3)}</td><td class="num">${num(x.V, 3)}</td><td class="num">${num(x.H / x.V, 2)}</td><td class="num">${x.n_365}</td><td class="num">${pct(x.cobertura_tarjetas)}</td></tr>`).join("")}</table>`;
  const grupos = [...new Set((M.ranking || []).map(r => r.grupo).filter(g => !g.startsWith("orf:")))].sort((a, b) => (ORDEN[a] ?? 99) - (ORDEN[b] ?? 99));
  if (!UI.rank || !grupos.includes(UI.rank)) UI.rank = grupos[0] || "";
  const rk = (M.ranking || []).filter(r => r.grupo === UI.rank);
  h += `<h3>Fuerza de los equipos (D24.2)</h3><div class="barra-herr"><select id="sel-rank">${grupos.map(g => `<option value="${g}" ${g === UI.rank ? "selected" : ""}>${esc(nombreLiga(g))} · ${esc(LIGAS[g]?.pais || "")}</option>`).join("")}</select></div>
    <p class="nota">A = ataque (1 = promedio), D = debilidad defensiva (menos es mejor). Fuerza = ln A − ln D. Ajustado por la calidad de los rivales enfrentados.</p>
    <table class="t"><tr><th>#</th><th>Equipo</th><th class="num">Ataque A</th><th class="num">Defensa D</th><th class="num">Fuerza</th><th class="num">Partidos efectivos</th></tr>
    ${rk.map((r, i) => `<tr><td>${i + 1}</td><td>${logo(r.logo)}${esc(r.n)}</td><td class="num">${num(r.A, 2)}</td><td class="num">${num(r.D, 2)}</td><td class="num"><b>${num(r.fuerza, 2)}</b></td><td class="num">${num(r.n_ef, 1)}</td></tr>`).join("")}</table>`;
  h += `<h3>Fuentes de datos (R13)</h3><table class="t"><tr><th>Fuente</th><th>Uso</th><th>Consultada</th></tr>${(DATOS.fuentes || []).map(f => `<tr><td>${esc(f.nombre)}</td><td>${esc(f.uso)}</td><td>${fmt(f.fecha, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</td></tr>`).join("")}</table>
    <p class="nota">${M.n_partidos} partidos jugados en la muestra · ${M.n_equipos} equipos · ${M.estadios_ubicados} ciudades ubicadas.</p>`;
  $("#v-modelo").innerHTML = h;
  const sr = $("#sel-rank");
  if (sr) sr.onchange = e => { UI.rank = e.target.value; guardarPref(); pintarModelo(); };
}

// ---------------------------------------------------------------- vista: ayuda
function pintarAyuda() {
  const dims = [
    ["D1–D4", "Constitución, fisiología, físico y técnica", "Solo explicativo", "Se reflejan de forma indirecta en la fuerza de ataque y defensa; no se pueden medir desde fuera del club."],
    ["D5", "Lesiones", "Ajuste (no disponible)", "ESPN no publica bajas: quedan como «búscalos» en el prompt aplicado."],
    ["D6–D7", "Mente del jugador", "Solo explicativo", "No observable desde fuera; la forma reciente ya refleja parte (R19)."],
    ["D8", "Cargas y recuperación", "Ajuste acotado", "Días de descanso entre partidos, efecto estimado con datos."],
    ["D9–D11", "Entrenamiento, vestuario y DT", "Solo explicativo", "El cambio de entrenador no está en las fuentes."],
    ["D12–D14", "Táctica, portero y balón parado", "Solo explicativo", "Reflejados en los goles a favor y en contra."],
    ["D15", "Rival", "Entrada cuantitativa", "Fuerza relativa ajustada por la calidad de los rivales; el historial directo es solo informativo."],
    ["D16", "Reglamento y disciplina", "Ajuste acotado", "Tarjetas, penales y expulsiones por equipo y competición."],
    ["D17", "Dinámica del partido", "Entrada cuantitativa", "Corrección Dixon-Coles para marcadores bajos."],
    ["D18", "Ambiente y localía", "Entrada + ajuste", "Localía por competición y por estadio, altitud estimada con datos; clima informativo."],
    ["D19", "Club e institución", "Solo explicativo", "Se refleja a largo plazo en la fuerza del equipo."],
    ["D20–D21", "Datos y azar", "Núcleo del modelo", "Poisson, ponderación temporal, intervalos y validación."],
    ["D22–D23", "Árbitro y jugador-árbitro", "No disponible", "ESPN no publica la designación: «búscalo» en el prompt aplicado."],
    ["D24", "Modelo de predicción", "Implementado", "Salidas obligatorias de D24.14, mercado (D24.15) y validación (D24.17)."],
    ["D25", "Perfil de errores", "No disponible", "Requiere datos de eventos por jugador que las fuentes gratuitas no dan."],
  ];
  $("#v-ayuda").innerHTML = `<h2>Cómo funciona</h2>
  <h3>Se actualiza solo</h3>
  <ul>
    <li>Una tarea de Windows llamada <b>PrediccionesFutbol</b> ejecuta la actualización <b>cada hora</b>, <b>al encender la laptop</b> y <b>cada vez que Windows se conecta a una red</b>. Solo corre si hay internet.</li>
    <li>Al abrir la app, si los datos tienen más de una hora, se actualizan en segundo plano.</li>
    <li>Sin internet, la app muestra las últimas predicciones guardadas y te avisa desde cuándo.</li>
    <li>No necesita a Claude, ni cuentas, ni claves. Todo corre en tu laptop.</li>
  </ul>
  <h3>Qué hace con tu Prompt Maestro</h3>
  <p>El programa ejecuta automáticamente el <b>modelo cuantitativo D24</b> y clasifica cada dimensión como pide D24.12:</p>
  <table class="t"><tr><th>Código</th><th>Dimensión</th><th>Papel en el modelo</th><th>Motivo</th></tr>${dims.map(d => `<tr><td><b>${d[0]}</b></td><td>${d[1]}</td><td>${d[2]}</td><td class="nota">${d[3]}</td></tr>`).join("")}</table>
  <p>Para el tratado completo (entregas 0 a 15), abre un partido y usa <b>Copiar prompt aplicado</b>. El prompt ya lleva completada la tabla de la sección 0 y la salida del modelo.</p>
  <h3>Cómo leer las predicciones</h3>
  <ul>
    <li><b>Azul</b> = gana el local, <b>gris</b> = empate, <b>naranja</b> = gana el visitante.</li>
    <li>El rango entre paréntesis es el <b>intervalo del 80%</b>: cuanto más ancho, menos datos tiene el modelo de esos equipos.</li>
    <li><b>Confianza</b> alta, media o baja según los partidos recientes de cada equipo y el ancho del intervalo.</li>
    <li>Etiquetas: ALT (altitud), DESC (descanso corto), ROT (posible rotación por copa), CLIMA, MERC (el mercado difiere o se movió), DATOS (pocos partidos).</li>
  </ul>
  <h3>Apuesta recomendada</h3>
  <ul>
    <li>Cada partido muestra <b>una apuesta</b> según el tipo que elijas arriba en la lista: <b>Más probable</b> (entre 75% y 88%: aciertas más seguido, pero paga poco), <b>Recomendada</b> (entre 55% y 75%) o <b>Mayor pago</b> (entre 35% y 55%). Se elige entre 23 opciones (1X2, doble oportunidad, empate no válido, más/menos goles, ambos marcan, goles por equipo y hándicap); «gana cualquiera» y «menos de 4,5» quedan solo como información.</li>
    <li>Se elige la opción con <b>mayor probabilidad prudente</b> dentro de ese rango, con una pequeña penalización a los mercados más complicados. Sin cuotas, la probabilidad prudente está entre la central y el límite bajo del rango del 80%; con cuotas, es la del mercado.</li>
    <li><b>Cuota justa</b> = 1 / probabilidad: lo que pagaría una casa sin comisión si la probabilidad es correcta. <b>Cuota mínima</b> = la mayor entre 1,25; 1,04 / probabilidad (1,03 con cuotas de mercado); 1 / (probabilidad − 0,02) y 1 / probabilidad prudente. Apuesta solo si tu casa paga eso o más. Si paga mucho más (la <b>cuota de alerta</b>), probablemente hay una noticia que el modelo no conoce: no apuestes.</li>
    <li>La calculadora del detalle te dice si conviene y cuánto apostar: ¼ de Kelly con la probabilidad prudente, en pasos de 0,25% de tu banca, con tope de 0,5% (0,25% en «Mayor pago»), a la mitad si el riesgo es medio y 0 si es alto.</li>
    <li>Las probabilidades de goles (más/menos, ambos marcan, goles por equipo, hándicap) se <b>recalibran</b> con los resultados reales: la corrección se aprende con partidos antiguos y se comprueba con los recientes. Solo pueden recomendarse los mercados que pasan esa prueba de calibración.</li>
    <li>Con <b>riesgo alto</b> (pocos datos, mucha incertidumbre, modelo y casa muy distintos o cuota que se movió en contra) verás «mejor no apostar», pero igual se muestra la opción menos arriesgada.</li>
    <li>Cuando ESPN tiene cuotas, las probabilidades de las apuestas se <b>ajustan al mercado</b>, porque en 6.459 partidos reales la cuota de cierre fue más precisa que el modelo.</li>
    <li>La etiqueta <b>«apuesta de valor»</b> solo se enciende si la prueba diaria con cuotas reales muestra ganancia con un intervalo del 80% por encima de cero. Hoy está ${DATOS?.apuestas?.prueba?.valor_habilitado ? "encendida" : "apagada"}.</li>
    <li>Las etiquetas <b>datos fiables / datos dudosos / mejor no apostar</b> miden qué tan confiables son los datos de ese partido, no la probabilidad de perder. Esa la dice la línea «falla ~N de cada 10».</li>
    <li>Las apuestas se liquidan con el <b>marcador a los 90'</b>, como hacen las casas: la prórroga y los penales no cuentan. Una recomendación se registra antes del inicio y no cambia después.</li>
    <li>Todo queda registrado antes del partido y se liquida al final: en «Resultados y aciertos» ves cuánto acierta cada perfil y cuánto habría ganado o perdido.</li>
  </ul>
  <h3>Alineaciones y simulación</h3>
  <ul>
    <li>En grandes ligas, copas sudamericanas y Liga 1, cada partido muestra los dos onces: el <b>probable</b> (el habitual) y, desde ~1 hora antes, el <b>confirmado</b>. La app lo revisa cada 15 minutos.</li>
    <li>El <b>aporte</b> de cada jugador mide cuántos goles marca (o recibe) su equipo por encima de lo esperado cuando él juega, usando solo partidos anteriores y acercándolo a 0 si jugó poco.</li>
    <li>Con la alineación confirmada se compara ese once con el habitual. El ajuste solo cambia las probabilidades si la prueba con partidos pasados demostró que mejora la predicción; si no, se muestra como información.</li>
    <li>También verás el estilo de juego de cada equipo (posesión, juego directo, centros, presión), cómo le fue contra rivales de estilo parecido, de local y de visita, y los enfrentamientos anteriores con cuántos titulares de hoy jugaron.</li>
    <li>El botón <b>Simular 10 000 partidos</b> juega el partido muchas veces, reparte los goles entre los jugadores y lo traduce a los mercados de Betano. Escribe la cuota que ves en Betano y te dice si conviene.</li>
  </ul>
  <h3>Uso responsable (R20)</h3>
  <p>Son probabilidades, no certezas. En la prueba con partidos pasados, el favorito del modelo gana alrededor de la mitad de las veces; ningún modelo garantiza acertar ni ganar dinero en apuestas.</p>
  <h3>Archivos</h3>
  <p class="nota">Programa: C:\\Users\\MSI-KATANA\\PrediccionesFutbol · Datos: carpeta <b>datos</b> · Registro de errores: carpeta <b>logs</b> · Para desinstalar: ejecuta <b>desinstalar.ps1</b>.</p>`;
}

// ---------------------------------------------------------------- general
function pintarTodo() {
  pintarEstado();
  if (!DATOS) { $("#lista").innerHTML = ""; return; }
  pintarFiltroLigas();
  pintarPartidos();
  if (UI.vista === "resultados") pintarResultados();
  if (UI.vista === "tablas") pintarTablas();
  if (UI.vista === "modelo") pintarModelo();
  if (UI.vista === "ayuda") pintarAyuda();
}

function cambiarVista(v) {
  UI.vista = v;
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("activo", b.dataset.vista === v));
  document.querySelectorAll(".vista").forEach(s => s.classList.toggle("activa", s.id === "v-" + v));
  pintarTodo();
  if (v === "ayuda") pintarAyuda();
}

function iniciar() {
  $("#nav").onclick = e => { const b = e.target.closest("button"); if (b) cambiarVista(b.dataset.vista); };
  $("#dias").onclick = e => { const b = e.target.closest("[data-dia]"); if (b) { UI.dia = b.dataset.dia; pintarPartidos(); } };
  $("#buscar").oninput = e => { UI.buscar = e.target.value; pintarPartidos(); };
  $("#filtro-liga").onchange = e => { UI.liga = e.target.value; guardarPref(); pintarPartidos(); };
  $("#solo-dest").onchange = e => { UI.soloDest = e.target.checked; guardarPref(); pintarPartidos(); };
  $("#perfil").onchange = e => { UI.perfil = e.target.value; guardarPref(); pintarPartidos(); if (UI.vista === "resultados") pintarResultados(); };
  $("#filtro-hist").onchange = e => { UI.ligaHist = e.target.value; pintarResultados(); };
  $("#sel-tabla").onchange = e => { UI.tabla = e.target.value; guardarPref(); pintarTablas(); };
  $("#lista").onclick = e => { const f = e.target.closest(".partido"); if (f) abrirDetalle(f.dataset.id); };
  $("#velo").onclick = cerrarDetalle;
  document.addEventListener("keydown", e => { if (e.key === "Escape") cerrarDetalle(); });
  $("#btn-actualizar").onclick = actualizar;
  if (ESTATICO) { $("#btn-actualizar").textContent = "Traer lo último"; $("#btn-actualizar").title = "La página se actualiza sola cada hora"; }
  const ping = () => api("/api/ping").then(() => { if (!SERVIDOR_OK) { SERVIDOR_OK = true; pintarEstado(); } }).catch(() => { SERVIDOR_OK = false; pintarEstado(); });
  ping(); setInterval(ping, 15000);
  setInterval(() => { if (!ESTADO.corriendo) pintarEstado(); if (DATOS && UI.vista === "partidos") pintarPartidos(); }, 60000);
  cargarDatos().then(consultarEstado);
}
if (ESTATICO) {
  let guardada = null;
  try { guardada = localStorage.getItem("pf_clave"); } catch { }
  if (guardada) probarClave(guardada).then(ok => ok ? iniciar() : pantallaClave(true));
  else pantallaClave(false);
} else iniciar();
