/* Predicciones Fútbol · auditoría (v3.1)
   Añade: «Necesidad en la tabla», «Evolución del pronóstico» (tres momentos), estados de la alineación y la
   pestaña «Auditoría». Todo sale de lo que calculó el programa (m.necesidad, m.momentos, DATOS.auditoria):
   aquí no se inventa ninguna cifra. */

const pp = (x, d = 1) => `${x >= 0 ? "+" : "−"}${num(Math.abs(x) * 100, d)} pp`;
const ventaja = o => o.dentro ? `${o.dist} pts de ventaja` : `a ${-o.dist} pts`;

// ---------------------------------------------------------------- necesidad en la tabla
function htmlNecesidad(m) {
  const n = m.necesidad;
  if (!n) return "";
  const bloque = s => {
    const x = n[s];
    if (!x) return `<div class="caja"><div class="tit">${esc(m[s].n)}</div><p class="nota">Sin tabla de liga para este equipo en los datos.</p></div>`;
    const sim = x.modo === "simulado";
    const objs = (x.objetivos || []).map(o => sim
      ? `<div class="obj"><div class="obj-cab"><span>${esc(o.n)}</span><b>${pct(o.p)}</b></div><div class="barra-ev"><i style="width:${o.p * 100}%"></i></div>
          <div class="nota">${ventaja(o)} · ${esc(o.estado)}${o.gana != null && o.pierde != null ? ` · si gana ${pct(o.gana)} · empata ${o.empata != null ? pct(o.empata) : "–"} · pierde ${pct(o.pierde)}` : ""}</div></div>`
      : `<div class="dato"><span>${esc(o.n)}</span><b>${ventaja(o)}</b></div>`).join("");
    return `<div class="caja"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-weight:700">${logo(m[s].logo)}${esc(m[s].n)}
        ${x.nivel ? `<span class="nec-nivel n-${x.nivel.replace(" ", "-")}">${esc(x.nivel)}</span>` : ""}</div>
      <div class="nota">${x.otra_liga ? `En su liga (${esc(nombreLiga(x.otra_liga))}): ` : ""}${x.pos}.º de ${x.equipos} · ${x.pts} pts · ${x.pj} PJ${x.rest != null ? ` · le quedan ${x.rest}` : ""}${x.zona ? ` · zona: ${esc(x.zona)}` : ""}</div>
      <p style="margin:8px 0">${esc(x.lectura || "")}</p>${objs}
      ${sim ? "" : `<p class="nota">Sin porcentajes: ${esc(x.motivo || "esta liga no se puede simular")}.</p>`}</div>`;
  };
  const sim = ["h", "a"].some(s => n[s]?.modo === "simulado");
  const serie = n.serie ? `<div class="senal info"><span><b>Eliminatoria (global ${n.serie.global[0]}-${n.serie.global[1]}):</b> ${esc(n.serie.lectura)}</span></div>` : "";
  return `<div class="seccion" id="sec-necesidad"><h3>Necesidad en la tabla <small>qué se juega cada equipo${sim ? " · 4 000 finales de temporada simulados" : ""}</small></h3>
    ${serie}${n.h || n.a ? `<div class="dos">${bloque("h")}${bloque("a")}</div>` : ""}
    <p class="nota"><b>Necesitar ganar no significa tener más probabilidad de ganar:</b> la urgencia aumenta la iniciativa, pero también deja espacios atrás. Esto es contexto y no cambia las probabilidades del modelo. Las plazas por zona son las que publica ESPN y los empates en puntos se resuelven por diferencia de goles (aproximación).</p></div>`;
}
window.PF_iconos = m => {
  const d = ["h", "a"].filter(s => m.necesidad?.[s]?.nivel === "decisivo" && !m.necesidad[s].otra_liga);
  return d.length ? `<span class="ico amarillo" title="${esc(d.map(s => m[s].n + ": " + m.necesidad[s].lectura).join(" · "))}">DECISIVO</span>` : "";
};

// ---------------------------------------------------------------- tres momentos
const NOMBRE_MOMENTO = { "48h": "24-48 h antes", "6h": "3-6 h antes", oficial: "Con alineación oficial" };
function htmlMomentos(m) {
  const ms = m.momentos;
  if (!ms) return "";
  const filas = Object.keys(NOMBRE_MOMENTO).map(k => {
    const f = ms[k], c = (m.cambios || []).find(x => x.a === k);
    if (!f) return `<tr><td>${NOMBRE_MOMENTO[k]}</td><td colspan="5" class="nota">${m.estado === "pre" ? (k === "oficial" ? "aún no sale (se publica cerca de 1 hora antes)" : "todavía no llega ese momento") : "sin registro"}</td></tr>`;
    return `<tr><td>${NOMBRE_MOMENTO[k]}</td><td class="num">${pct(f.pH, 1)}</td><td class="num">${pct(f.pD, 1)}</td><td class="num">${pct(f.pA, 1)}</td><td class="nota">${haceCuanto(f.guardado)}</td>
      <td>${c ? `${esc(c.motivos.join("; "))} <span class="nota">(local ${pp(c.dH)}, visita ${pp(c.dA)})</span>` : `<span class="nota">primera foto guardada</span>`}</td></tr>`;
  }).join("");
  return `<div class="seccion"><h3>Evolución del pronóstico <small>tres momentos · qué cambió y por qué</small></h3>
    <div class="caja"><table class="t"><tr><th>Momento</th><th class="num">Local</th><th class="num">Empate</th><th class="num">Visita</th><th>Guardado</th><th>Qué cambió</th></tr>${filas}</table>
    <p class="nota">De cada tramo se conserva la última foto antes de pasar al siguiente. En tu historial, la última predicción acertó más que la primera: conviene mirar el pronóstico cerca del partido.</p></div></div>`;
}

// ---------------------------------------------------------------- estados de la alineación
function htmlEstadosXI(m) {
  const al = m.alineacion;
  if (!al?.h?.jugadores || !al?.a?.jugadores) return "";
  const conf = al.estado === "confirmada";
  const fila = s => {
    const js = al[s].jugadores, duda = js.filter(j => j.posible_baja);
    return `<tr><td>${esc(m[s].n)}</td><td class="num">${conf ? js.length : 0}</td><td class="num">${conf ? 0 : js.length - duda.length}</td>
      <td>${duda.length ? esc(duda.map(j => j.n).join(", ")) : "–"}</td><td>${conf && al[s].sale?.length ? esc(al[s].sale.join(", ")) : "–"}</td></tr>`;
  };
  return `<table class="t" style="margin-top:8px"><tr><th>Equipo</th><th class="num">Confirmados</th><th class="num">Probables</th><th>En duda (noticias)</th><th>Fuera del once oficial</th></tr>${fila("h")}${fila("a")}</table>
    <p class="nota">Fuentes: ESPN (alineación) y Google Noticias (dudas: titulares sin verificar) · consultado ${haceCuanto(DATOS.alineaciones_actualizadas || DATOS.generado)} · fiabilidad ${conf ? "<b>alta</b> (oficial)" : "<b>media</b> (once habitual: puede cambiar)"}. El estado «no disponible» por lesión o sanción confirmada no se puede dar: ninguna fuente gratuita autorizada lo publica; solo se sabe quién quedó fuera cuando sale el once oficial.</p>`;
}

// el detalle ya trae los bloques de panel.js: aquí se añaden necesidad, momentos y estados del once
const _detallePanel = window.PF_detalle;
window.PF_detalle = m => {
  _detallePanel?.(m);
  const cuerpo = $("#detalle .det-cuerpo");
  if (!cuerpo) return;
  const ancla = $("#sec-intel") || cuerpo.querySelector(".seccion");
  if (ancla) ancla.insertAdjacentHTML("afterend", (m.estado === "pre" ? htmlNecesidad(m) : "") + htmlMomentos(m));
  const pie = $("#detalle .campo-pie");
  if (pie) pie.insertAdjacentHTML("afterend", htmlEstadosXI(m));
};

// ---------------------------------------------------------------- pestaña «Auditoría»
function pintarAuditoria() {
  const cont = $("#v-auditoria"), a = DATOS?.auditoria;
  if (!DATOS) return;
  if (!a || !a.ultima) { cont.innerHTML = `<h2>Auditoría</h2><div class="vacio">Todavía no hay suficientes partidos jugados con predicción guardada (${a?.n ?? 0} de 50).</div>`; return; }
  const g = DATOS.goles, ex = DATOS.extras_prueba || {}, cc = a.con_cuotas || {};
  const mejora = (x, y) => x == null || y == null ? "" : x < y ? "sube" : "baja";
  const lecturaZ = z => Math.abs(z) >= 3 ? `<span class="baja">desvío claro</span>` : Math.abs(z) >= 2 ? `<span class="ev-duda">a vigilar</span>` : `<span class="nota">normal (azar)</span>`;
  const filaGrupo = (t, x) => x ? `<tr><td>${t}</td><td class="num">${miles(x.n)}</td><td class="num">${pct(x.acierto, 1)}</td><td class="num">${pct(x.esperado, 1)}</td>
      <td class="num ${x.acierto >= x.esperado - 0.02 ? "" : "baja"}">${pp(x.acierto - x.esperado)}</td><td class="num ${x.roi == null ? "" : x.roi >= 0 ? "sube" : "baja"}">${x.roi == null ? "–" : pp(x.roi).replace(" pp", " %")}</td><td class="num nota">${x.n_cuota || "–"}</td></tr>` : "";
  const pf = DATOS.apuestas?.perfiles || {};
  const tablaPerfil = k => {
    const x = a.perfiles?.[k];
    if (!x) return "";
    return `<tr class="grupo-fila"><td colspan="7">${esc(pf[k]?.nombre || k)}</td></tr>${filaGrupo("Todas", x)}
      ${Object.entries(x.por_clase || {}).map(([c, v]) => filaGrupo(`Clase: ${CLASES[c]?.[0] || c}`, v)).join("")}
      ${Object.entries(x.por_riesgo || {}).map(([c, v]) => filaGrupo(`Datos: riesgo ${c}`, v)).join("")}
      ${(x.por_rango || []).map(v => filaGrupo(`Cuota justa ${num(v.rango[0])}–${v.rango[1] > 50 ? "más" : num(v.rango[1])}`, v)).join("")}`;
  };
  const mom = Object.entries(a.momentos || {}).filter(([, v]) => v);
  cont.innerHTML = `<h2>Auditoría de precisión</h2>
    <p class="nota">Cómo le fue de verdad al programa con ${miles(a.n)} partidos ya jugados (del ${esc(a.desde)} al ${esc(a.hasta)}). Cada predicción se guardó <b>antes</b> del partido: no hay forma de mirar el futuro. RPS y log-loss: cuanto más bajos, mejor.</p>
    <div class="kpis">
      ${kpi("RPS del programa", num(a.ultima.rps, 4), `referencia simple ${num(a.ingenuo.rps, 4)}`)}
      ${kpi("Acierta el resultado", pct(a.ultima.acierto, 1), `la primera predicción: ${pct(a.primera?.acierto, 1)}`)}
      ${kpi("Con cuotas: programa", num(cc.modelo?.rps, 4), `el mercado solo: ${num(cc.mercado?.rps, 4)} (${miles(cc.modelo?.n)} partidos)`)}
      <div class="kpi ${a.sin_cuotas && a.sin_cuotas.rps >= a.ingenuo.rps ? "alerta" : ""}"><div class="tit">Sin cuotas</div><div class="val">${num(a.sin_cuotas?.rps, 4)}</div><div class="sub">${miles(a.sin_cuotas?.n)} partidos · ${a.sin_cuotas && a.sin_cuotas.rps >= a.ingenuo.rps ? "no supera la referencia" : "supera la referencia"}</div></div>
      ${kpi("CLV medio", a.clv?.n ? pp(a.clv.medio).replace(" pp", " %") : "–", a.clv?.n ? `${miles(a.clv.n)} apuestas · ${pct(a.clv.positivo)} mejoraron al cierre` : "se empieza a medir desde hoy")}
      ${kpi("Factor de goles", g?.aplicado ? "×" + num(g.factor, 3) : "×1,000", g ? (g.aplicado ? "aplicado: pasó la prueba" : "no aplicado: no mejora la prueba") : "sin evaluar")}
    </div>
    <div class="panel-dos"><div>
      <div class="bloque"><h3>Sesgos <small>lo predicho de media frente a lo que pasó</small></h3>
        <table class="t"><tr><th>Medida</th><th class="num">Predicho</th><th class="num">Real</th><th class="num">Diferencia</th><th>Lectura</th></tr>
        ${a.sesgos.map(s => `<tr><td>${esc(s.n)}</td><td class="num">${pct(s.pred, 1)}</td><td class="num">${pct(s.real, 1)}</td><td class="num">${pp(s.real - s.pred)}</td><td>${lecturaZ(s.z)} <span class="nota">(${num(s.z, 1)} errores estándar)</span></td></tr>`).join("")}</table>
        <p class="nota">Un desvío en pocas semanas puede ser azar. Los empates se comprobaron además con 23 340 partidos fuera de muestra y ahí no hay sesgo, por eso no se corrigen.</p></div>
      <div class="bloque"><h3>¿Se sobrevalora al favorito? <small>calibración por tramo de probabilidad</small></h3>
        <table class="t"><tr><th>Probabilidad del favorito</th><th class="num">Partidos</th><th class="num">Predicho</th><th class="num">Ganó de verdad</th><th class="num">Diferencia</th></tr>
        ${a.favorito.map(f => `<tr><td>desde ${pct(f.desde)}</td><td class="num">${miles(f.n)}</td><td class="num">${pct(f.pred, 1)}</td><td class="num">${pct(f.real, 1)}</td><td class="num ${f.real < f.pred - 0.05 ? "baja" : ""}">${pp(f.real - f.pred)}</td></tr>`).join("")}
        ${Object.entries(a.local_visita || {}).map(([k, f]) => `<tr><td>${esc(k)}</td><td class="num">${miles(f.n)}</td><td class="num">${pct(f.pred, 1)}</td><td class="num">${pct(f.real, 1)}</td><td class="num">${pp(f.real - f.pred)}</td></tr>`).join("")}</table>
        <p class="nota">Diferencia negativa grande = exceso de optimismo con el favorito. Positiva = el programa se queda corto.</p></div>
      <div class="bloque"><h3>Apuestas recomendadas <small>acierto real frente al prometido, y rendimiento con la cuota de referencia</small></h3>
        <table class="t"><tr><th></th><th class="num">Apuestas</th><th class="num">Acertó</th><th class="num">Prometía</th><th class="num">Diferencia</th><th class="num">Rendimiento</th><th class="num">Con cuota</th></tr>
        ${["conservador", "equilibrado", "arriesgado"].map(tablaPerfil).join("")}</table>
        <p class="nota">Rendimiento (yield) = ganancia por cada sol apostado a la cuota de ${esc(DATOS.partidos.find(m => m.mercado)?.mercado?.proveedor || "la casa de referencia")}, solo en las apuestas que tenían cuota. No es Betano. Negativo significa que, apostando siempre, se pierde.</p></div>
    </div><div>
      ${g ? `<div class="bloque"><h3>Factor de goles reciente</h3>
        <div class="dato"><span>Goles esperados / reales por partido</span><b>${num(g.goles_esperados)} / ${num(g.goles_reales)}</b></div>
        <div class="dato"><span>«Más de 2,5»: predicho sin factor → con factor</span><b>${g.o25_pred ? `${pct(g.o25_pred[0], 1)} → ${pct(g.o25_pred[1], 1)}` : "–"} (real ${pct(g.o25_real, 1)})</b></div>
        <div class="dato"><span>Log-loss de «más de 2,5» sin → con</span><b class="${mejora(g.logloss_o25?.[1], g.logloss_o25?.[0])}">${g.logloss_o25 ? `${num(g.logloss_o25[0], 4)} → ${num(g.logloss_o25[1], 4)}` : "–"}</b></div>
        <div class="dato"><span>Log-loss del 1X2 sin → con</span><b>${g.logloss_1x2 ? `${num(g.logloss_1x2[0], 4)} → ${num(g.logloss_1x2[1], 4)}` : "–"}</b></div>
        <p class="nota">${g.aplicado ? `El modelo se quedaba corto en goles. Cada semana se compara lo esperado con lo real de las ${g.ventana_semanas} semanas anteriores y ese factor corrige la siguiente. Probado en ${miles(g.n)} partidos sin mirar el futuro.` : "El factor no mejoró la prueba semana a semana, así que no se aplica (queda en 1)."} Si deja de mejorar, se apaga solo.</p></div>` : ""}
      <div class="bloque"><h3>Ligas donde peor rinde <small>frente a la referencia simple</small></h3>
        <table class="t"><tr><th>Competición</th><th class="num">Partidos</th><th class="num">RPS</th><th class="num">Referencia</th><th class="num">Con cuotas</th></tr>
        ${a.ligas.peores.map(l => `<tr><td>${esc(nombreLiga(l.lg))}</td><td class="num">${l.n}</td><td class="num ${l.rps >= l.rps_ingenuo ? "baja" : ""}">${num(l.rps, 4)}</td><td class="num">${num(l.rps_ingenuo, 4)}</td><td class="num nota">${l.con_cuotas}</td></tr>`).join("")}</table>
        <p class="nota">${a.abstener.length ? `<b>Abstención automática</b> (sin cuotas no se recomienda apostar): ${esc(a.abstener.map(nombreLiga).join(", "))}.` : "Ninguna competición con 30 partidos o más queda en abstención."} ${a.ligas.medidas} competiciones tienen al menos 25 partidos.</p></div>
      ${mom.length ? `<div class="bloque"><h3>Los tres momentos</h3><table class="t"><tr><th>Momento</th><th class="num">Partidos</th><th class="num">RPS</th><th class="num">Acierto</th></tr>
        ${mom.map(([k, v]) => `<tr><td>${NOMBRE_MOMENTO[k]}</td><td class="num">${miles(v.n)}</td><td class="num">${num(v.rps, 4)}</td><td class="num">${pct(v.acierto, 1)}</td></tr>`).join("")}</table>
        <p class="nota">Se registran desde el 22/11/2026: con pocos partidos todavía no dicen nada firme.</p></div>` : ""}
      <div class="bloque"><h3>Mercado y cierre</h3>
        <div class="dato"><span>CLV de las recomendaciones</span><b>${a.clv?.n ? `${pp(a.clv.medio).replace(" pp", " %")} en ${miles(a.clv.n)}` : "aún sin datos"}</b></div>
        <div class="dato"><span>El mercado se movió hacia el modelo</span><b>${a.hacia_modelo?.n ? `${pct(a.hacia_modelo.pct)} de ${miles(a.hacia_modelo.n)} casos` : "aún sin datos"}</b></div>
        <p class="nota">CLV = cuota al recomendar ÷ cuota al cierre − 1. Positivo de forma sostenida es la mejor señal de que se apuesta con ventaja; se mide con la casa de referencia de ESPN, no con Betano. Empieza a acumularse desde esta versión.</p></div>
      <div class="bloque"><h3>Córners, tarjetas y tiempos</h3>
        ${Object.entries({ cor: "Córners", tarj: "Tarjetas", tiempos: "Primer y segundo tiempo" }).map(([k, n]) => ex[k] ? `<div class="dato"><span>${n}</span><b class="${ex[k].pasan < ex[k].n / 2 ? "baja" : ""}">${ex[k].pasan} de ${ex[k].n} opciones pasan la prueba</b></div>` : "").join("")}
        <p class="nota">Las que no pasan quedan retiradas: se muestran plegadas en cada partido, sin cuota mínima.</p></div>
      <div class="bloque"><h3>Últimas semanas</h3><table class="t"><tr><th>Semana</th><th class="num">Partidos</th><th class="num">RPS</th><th class="num">Acierto</th></tr>
        ${a.semanas.map(s => `<tr><td>${esc(s.semana)}</td><td class="num">${miles(s.n)}</td><td class="num">${num(s.rps, 4)}</td><td class="num">${pct(s.acierto, 1)}</td></tr>`).join("")}</table></div>
    </div></div>
    <p class="nota">Lectura honesta: el programa informa bien las probabilidades, pero no ha demostrado ganarle a la casa. Ningún modelo garantiza ganancias.</p>`;
}
window.PF_vistas.auditoria = pintarAuditoria;

// página web: comparar y combinar necesitan el detalle de los partidos lejanos; se descarga una sola vez
for (const v of ["comparar", "combinada"]) {
  const pintar = window.PF_vistas[v];
  window.PF_vistas[v] = () => {
    pintar();
    if (ESTATICO && !pintarAuditoria.pidiendo && DATOS?.partidos.some(m => m._lejos)) {
      pintarAuditoria.pidiendo = true;
      cargarLejos().then(() => { if (UI.vista === v) pintar(); }).catch(() => { }).finally(() => { pintarAuditoria.pidiendo = false; });
    }
  };
}
if (DATOS && UI.vista === "auditoria") pintarAuditoria();
