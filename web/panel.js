/* Predicciones Fútbol · interfaz de inteligencia (v3)
   Vistas nuevas: Panel, Comparar, Combinadas y Mi banca; y bloques nuevos en el detalle de cada partido
   (veredicto, simulación de 10 000 escenarios, «¿qué pasa si…?», cancha con el once y radar de jugadores).
   Todo sale de lo que ya calculó el programa (m.intel, m.sim, m.alineacion): aquí no se inventa ninguna cifra. */

// ---------------------------------------------------------------- utilidades
const CLASES = { conservadora: ["Conservadora", "c-cons"], moderada: ["Moderada", "c-mod"], elevado: ["Riesgo elevado", "c-elev"], sin_valor: ["Sin valor estadístico", "c-sin"] };
const BONO_CLASE = { conservadora: 30, moderada: 15, elevado: 0, sin_valor: -100 };
const chipClase = c => CLASES[c] ? `<span class="clase ${CLASES[c][1]}">${CLASES[c][0]}</span>` : "";
const horasPara = m => (Date.parse(m.fecha) - Date.now()) / 36e5;
const porJugar = () => (DATOS?.partidos || []).filter(m => m.estado === "pre" && !empezado(m));
const partidoDe = id => DATOS.partidos.find(m => m.id === id);
const titulo = m => `${m.h.n} vs ${m.a.n}`;
const miles = x => String(Math.round(Number(x) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");   // 10 000 (sin confundir con decimales)
const leerLS = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const guardarLS = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } };
const diaCorto = iso => fmt(iso, { weekday: "short", day: "numeric", month: "short" });
const lista = (xs, vacio) => xs?.length ? `<ul>${xs.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="nota">${vacio}</p>`;
window.PF_chip = (m, k) => chipClase(m.intel?.perfiles?.[k]?.clase);

// la mejor de las 3 apuestas de un partido según calidad de la evidencia + clase (no es ganancia esperada)
function mejorPerfil(m) {
  let mejor = null;
  for (const [k, x] of Object.entries(m.intel?.perfiles || {})) {
    const f = pickDe(m, k), pts = x.evidencia + BONO_CLASE[x.clase];
    if (f && (!mejor || pts > mejor.pts)) mejor = { k, x, f, pts };
  }
  return mejor;
}
// intervalo de Wilson del 95 % (error del sorteo de la simulación)
function wilson(p, n) {
  const z = 1.96, d = 1 + z * z / n, c = (p + z * z / (2 * n)) / d, h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}
// matriz de marcadores Poisson con corrección Dixon-Coles (la misma fórmula del modelo en Python)
function matrizDC(lh, la, rho, G = 11) {
  const pm = l => { const v = [Math.exp(-l)]; for (let i = 1; i < G; i++) v.push(v[i - 1] * l / i); return v; };
  const a = pm(lh), b = pm(la), M = a.map(x => b.map(y => x * y));
  M[0][0] *= Math.max(1 - lh * la * rho, 0); M[0][1] *= Math.max(1 + lh * rho, 0);
  M[1][0] *= Math.max(1 + la * rho, 0); M[1][1] *= Math.max(1 - rho, 0);
  const s = M.flat().reduce((x, y) => x + y, 0);
  return M.map(f => f.map(v => v / s));
}
const pMatriz = (M, cond) => M.reduce((t, f, i) => t + f.reduce((u, v, j) => u + (cond(i, j) ? v : 0), 0), 0);
// ¿se cumple el mercado con el marcador a-b? (para calcular combinadas del mismo partido sin multiplicar)
function predicado(cod) {
  const fijo = { "1": (a, b) => a > b, X: (a, b) => a === b, "2": (a, b) => a < b, "1X": (a, b) => a >= b, X2: (a, b) => a <= b, "12": (a, b) => a !== b,
    BTTS_SI: (a, b) => a > 0 && b > 0, BTTS_NO: (a, b) => a === 0 || b === 0, CS_L: (a, b) => b === 0, CS_V: a => a === 0,
    PAR: (a, b) => (a + b) % 2 === 0, IMPAR: (a, b) => (a + b) % 2 === 1 };
  if (fijo[cod]) return fijo[cod];
  let r = /^([OU])(\d\.5)$/.exec(cod);
  if (r) { const x = +r[2]; return r[1] === "O" ? (a, b) => a + b > x : (a, b) => a + b < x; }
  r = /^([LV])_([OU])(\d\.5)$/.exec(cod);
  if (r) { const x = +r[3], g = r[1] === "L" ? a => a : (a, b) => b; return r[2] === "O" ? (a, b) => g(a, b) > x : (a, b) => g(a, b) < x; }
  return null;
}

// ---------------------------------------------------------------- panel principal
function pintarPanel() {
  const cont = $("#v-panel");
  if (!DATOS) { cont.innerHTML = ""; return; }
  const hoy = claveDia(new Date().toISOString()), pre = porJugar();
  const deHoy = DATOS.partidos.filter(m => claveDia(m.fecha) === hoy).sort((a, b) => a.fecha.localeCompare(b.fecha));
  const prox = pre.filter(m => horasPara(m) <= 48).sort((a, b) => a.fecha.localeCompare(b.fecha));
  const conSim = DATOS.partidos.filter(m => m.sim);
  const estables = conSim.filter(m => m.sim.estabilidad.estable).length;
  const errMedio = conSim.length ? conSim.reduce((t, m) => t + m.sim.estabilidad.error_vs_exacto, 0) / conSim.length : null;
  const pendXI = pre.filter(m => horasPara(m) <= 3 && m.alineacion?.estado !== "confirmada");
  const confXI = pre.filter(m => m.alineacion?.estado === "confirmada");
  const conBajas = prox.filter(m => ["h", "a"].some(s => (m.alineacion?.[s]?.jugadores || []).some(j => j.posible_baja)));
  const conCuotas = pre.filter(m => m.mercado);
  const sinApuesta = prox.filter(m => m.intel?.veredicto?.cod === "no").length;

  const opors = prox.filter(m => m.intel?.veredicto?.cod === "condicional").map(m => ({ m, b: mejorPerfil(m) }))
    .filter(o => o.b && o.b.x.clase !== "sin_valor").sort((a, b) => b.b.pts - a.b.pts || a.m.fecha.localeCompare(b.m.fecha)).slice(0, 8);
  const tarjetaOpor = ({ m, b }) => `<div class="opor" data-id="${m.id}">
      <div class="cab"><span>${esc(nombreLiga(m.lg))} · ${diaCorto(m.fecha)} ${horaLocal(m.fecha)}</span>${chipClase(b.x.clase)}</div>
      <div class="eqs">${logo(m.h.logo)}<span>${esc(m.h.n)}</span><span class="nota">vs</span><span>${esc(m.a.n)}</span>${logo(m.a.logo)}</div>
      <div class="pick">${esc(b.f.txt)}</div>
      <div class="nums"><span>Probabilidad <b>${pct(b.f.p)}</b></span><span>Cuota mínima <b>${b.f.minima ? num(b.f.minima) : "–"}</b></span><span>Evidencia <b>${b.x.evidencia}</b>/100</span></div>
      <div class="barra-ev" title="Calidad de los datos que respaldan la apuesta (no es probabilidad de acertar)"><i style="width:${b.x.evidencia}%"></i></div>
    </div>`;

  const alertas = [];
  prox.forEach(m => {
    (m.intel?.discrepancias || []).filter(d => d.nivel !== "info").forEach(d => alertas.push({ m, nivel: d.nivel, texto: d.texto }));
    (m.senales || []).filter(s => s.nivel === "rojo").forEach(s => alertas.push({ m, nivel: "rojo", texto: s.texto }));
  });
  alertas.sort((a, b) => (a.nivel === "rojo" ? 0 : 1) - (b.nivel === "rojo" ? 0 : 1) || a.m.fecha.localeCompare(b.m.fecha));
  const movs = pre.filter(m => m.mercado?.movimiento).sort((a, b) => b.mercado.movimiento.max - a.mercado.movimiento.max).slice(0, 6);
  const filaMov = m => {
    const v = m.mercado.movimiento, c = m.mercado.cuotas, s = x => `${x >= 0 ? "+" : "−"}${num(Math.abs(x) * 100, 1)}`;
    return `<div class="alerta-fila ${v.max >= 0.03 ? "amarillo" : "info"}" data-id="${m.id}"><div><b>${esc(titulo(m))}</b>
      Local ${num(v.antes[0])} → ${num(c.h)} (${s(v.dH)} pp) · Visita ${num(v.antes[2])} → ${num(c.a)} (${s(v.dA)} pp) · ${v.fotos} lecturas desde ${haceCuanto(v.desde)}</div></div>`;
  };
  const filaHoy = m => {
    const p = predDe(m), b = m.estado === "pre" ? mejorPerfil(m) : null;
    const hora = m.estado === "in" ? `<span style="color:var(--rojo)">● vivo</span>` : m.fin ? `${m.h.g}-${m.a.g}` : horaLocal(m.fecha);
    return `<div class="fila-mini" data-id="${m.id}"><span class="h">${hora}</span><span class="n">${esc(titulo(m))}</span>
      <div class="barra3"><div class="l" style="width:${p.pH * 100}%"></div><div class="x" style="width:${p.pD * 100}%"></div><div class="v" style="width:${p.pA * 100}%"></div></div>
      ${m.intel?.veredicto?.cod === "no" ? `<span class="sin-apuesta">sin apuesta</span>` : b ? chipClase(b.x.clase) : `<span class="nota">${m.fin ? "final" : ""}</span>`}</div>`;
  };

  const mo = DATOS.modelo || {}, v = mo.validacion?.global || {}, fuentes = DATOS.fuentes || [];
  cont.innerHTML = `
    <div class="hero">
      <div><h1>Centro de inteligencia futbolística</h1>
        <p>${miles(pre.length)} partidos analizados · ${miles(conSim.length * (DATOS.simulacion?.n || 10000))} escenarios simulados · modelo v${esc(DATOS.version_modelo || "–")}</p></div>
      <div class="der"><div><span class="pulso"></span>Datos actualizados ${haceCuanto(DATOS.generado)}</div>
        <div>${DATOS.alineaciones_actualizadas ? `Alineaciones revisadas ${haceCuanto(DATOS.alineaciones_actualizadas)}` : "Alineaciones: revisión cada 15 min"}</div></div>
    </div>
    <div class="kpis">
      ${kpi("Partidos de hoy", deHoy.length, `${deHoy.filter(m => m.estado === "in").length} en vivo · ${deHoy.filter(m => m.fin).length} terminados`)}
      ${kpi("Próximas 48 horas", prox.length, `${sinApuesta} sin apuesta recomendada`)}
      ${kpi("Simulaciones", miles(conSim.length), conSim.length ? `${pct(estables / conSim.length)} estables · error medio ${num(errMedio * 100, 2)} pp` : "aún sin simular")}
      <div class="kpi ${pendXI.length ? "alerta" : ""}"><div class="tit">Pendientes de alineación</div><div class="val">${pendXI.length}</div><div class="sub">empiezan en menos de 3 h · ${confXI.length} ya confirmadas</div></div>
      <div class="kpi ${conBajas.length ? "alerta" : ""}"><div class="tit">Alertas de bajas</div><div class="val">${conBajas.length}</div><div class="sub">partidos con titulares en duda (noticias)</div></div>
      ${kpi("Con cuotas reales", conCuotas.length, `de ${pre.length} · ${movs.filter(m => m.mercado.movimiento.max >= 0.03).length} con movimiento fuerte`)}
    </div>
    <div class="panel-dos">
      <div>
        <div class="bloque"><h3>Mejores oportunidades estadísticas <small>próximas 48 h · ordenadas por calidad de la evidencia, no por ganancia</small></h3>
          ${opors.length ? `<div class="opors">${opors.map(tarjetaOpor).join("")}</div>
            <p class="nota" style="margin-bottom:0">Solo valen si Betano paga la cuota mínima o más. En las pruebas con cuotas reales no hay ganancia demostrada frente a la casa: úsalas como referencia, no como ingreso seguro.</p>`
            : `<div class="veredicto-ia no"><b>NO HAY APUESTA RECOMENDADA</b><span>ningún partido de las próximas 48 horas tiene una opción suficientemente sólida.</span></div>`}
        </div>
        <div class="bloque"><h3>Partidos de hoy <button class="boton" data-ir="partidos">Ver todos</button></h3>
          ${deHoy.length ? deHoy.slice(0, 12).map(filaHoy).join("") + (deHoy.length > 12 ? `<p class="nota">y ${deHoy.length - 12} más en «Partidos».</p>` : "") : `<p class="nota">Hoy no hay partidos en las competiciones seguidas.</p>`}
        </div>
      </div>
      <div>
        <div class="bloque"><h3>Alertas <small>discrepancias y riesgos altos</small></h3>
          ${alertas.length ? alertas.slice(0, 9).map(a => `<div class="alerta-fila ${a.nivel}" data-id="${a.m.id}"><div><b>${esc(titulo(a.m))} · ${horaLocal(a.m.fecha)}</b>${esc(a.texto)}</div></div>`).join("") : `<p class="nota">Sin alertas en las próximas 48 horas.</p>`}
        </div>
        <div class="bloque"><h3>Movimiento de cuotas <small>${esc(conCuotas[0]?.mercado?.proveedor || "casa de referencia")} vía ESPN</small></h3>
          ${movs.length ? movs.map(filaMov).join("") : `<p class="nota">Todavía no hay movimientos: se guarda una lectura de las cuotas en cada actualización y aquí aparecen los cambios. Las cuotas de Betano no se leen: escríbelas tú en cada partido.</p>`}
        </div>
      </div>
    </div>
    <div class="bloque"><h3>Cómo se construye cada pronóstico <small>datos → variables → validación → pronóstico</small></h3>
      <div class="tuberia">
        <div class="etapa e1"><h4>Datos</h4><div class="sub">Información real, con fuente y hora</div><ul>
          <li><b>${miles(mo.n_partidos)}</b> partidos jugados</li><li><b>${miles(mo.n_equipos)}</b> equipos · <b>${miles((DATOS.ligas || []).length)}</b> competiciones</li>
          ${fuentes.map(f => `<li title="${esc(f.uso)}">${esc(f.nombre.split(" (")[0])}${f.fecha ? ` · ${haceCuanto(f.fecha)}` : ""}</li>`).join("")}</ul></div>
        <div class="etapa e2"><h4>Variables</h4><div class="sub">Lo que el modelo mide</div><ul>
          <li>Ataque y defensa de cada equipo</li><li>Localía por competición y estadio</li><li>Peso mayor a lo reciente (vida media <b>${DATOS.vida_media}</b> días)</li>
          <li>Altitud, descanso y calendario</li><li>Aporte de cada jugador del once${DATOS.jugadores?.beta ? ` (β <b>${num(DATOS.jugadores.beta)}</b>)` : ""}</li><li>Cuotas del mercado como ancla</li></ul></div>
        <div class="etapa e3"><h4>Validación</h4><div class="sub">Probado sin mirar el futuro</div><ul>
          <li><b>${miles(v.n)}</b> partidos, semana a semana (${mo.semanas_validacion || "–"} semanas)</li>
          <li>RPS <b>${num(v.rps, 4)}</b> · referencia ${num(v.rps_base, 4)}</li><li>Log-loss <b>${num(v.logloss, 3)}</b> · Brier <b>${num(v.brier, 3)}</b></li>
          <li>Acierta el resultado <b>${pct(v.acierto, 1)}</b></li>${mo.ancla ? `<li>Con cuotas: RPS <b>${num(mo.ancla.rps_anclado, 4)}</b></li>` : ""}</ul></div>
        <div class="etapa e4"><h4>Pronóstico</h4><div class="sub">Probabilidades con incertidumbre</div><ul>
          <li><b>${miles(pre.length)}</b> partidos por jugarse</li><li><b>${miles(DATOS.simulacion?.n || 10000)}</b> escenarios por partido</li>
          <li>Intervalo del 80 % en cada probabilidad</li><li>3 apuestas con clase de riesgo</li><li>O «no hay apuesta recomendada»</li></ul></div>
      </div>
      <p class="nota" style="margin-bottom:0">Detalle completo de la validación y la calibración en la pestaña «Modelo». Solo fútbol masculino.</p>
    </div>`;
  cont.onclick = e => {
    const ir = e.target.closest("[data-ir]"); if (ir) return cambiarVista(ir.dataset.ir);
    const f = e.target.closest("[data-id]"); if (f) abrirDetalle(f.dataset.id);
  };
}

// ---------------------------------------------------------------- comparador de partidos
const CMP = { a: "", b: "" };
function pintarComparar() {
  const cont = $("#v-comparar");
  if (!DATOS) return;
  const ps = porJugar().filter(m => horasPara(m) <= 24 * 7 && m.intel).sort((a, b) => a.fecha.localeCompare(b.fecha));
  if (ps.length < 2) { cont.innerHTML = `<h2>Comparador de partidos</h2><div class="vacio">Hacen falta al menos dos partidos por jugarse.</div>`; return; }
  if (!ps.some(m => m.id === CMP.a)) CMP.a = ps[0].id;
  if (!ps.some(m => m.id === CMP.b) || CMP.b === CMP.a) CMP.b = ps.find(m => m.id !== CMP.a).id;
  const A = partidoDe(CMP.a), B = partidoDe(CMP.b), ba = mejorPerfil(A), bb = mejorPerfil(B);
  const ops = sel => ps.map(m => `<option value="${m.id}"${m.id === sel ? " selected" : ""}>${diaCorto(m.fecha)} ${horaLocal(m.fecha)} · ${esc(titulo(m))} (${esc(nombreLiga(m.lg))})</option>`).join("");
  const ancho = b => b ? b.f.p90 - b.f.p10 : null;
  const alertas = m => (m.intel.discrepancias || []).filter(d => d.nivel !== "info").length;
  // [fila, valor A, valor B, quién sale mejor (-1 = A, 1 = B, 0 = igual)]
  const mejorDe = (x, y, mayorEsMejor = true) => x == null || y == null || x === y ? 0 : ((x > y) === mayorEsMejor ? -1 : 1);
  const filas = [
    ["Competición", esc(nombreLiga(A.lg)), esc(nombreLiga(B.lg)), 0],
    ["Probabilidades 1 · X · 2", `${pct(A.pred.pH)} · ${pct(A.pred.pD)} · ${pct(A.pred.pA)}`, `${pct(B.pred.pH)} · ${pct(B.pred.pD)} · ${pct(B.pred.pA)}`, 0],
    ["Veredicto", esc(A.intel.veredicto.txt), esc(B.intel.veredicto.txt), mejorDe(A.intel.veredicto.cod === "condicional" ? 1 : 0, B.intel.veredicto.cod === "condicional" ? 1 : 0)],
    ["Mejor apuesta", ba ? `<b>${esc(ba.f.txt)}</b> ${chipClase(ba.x.clase)}` : "–", bb ? `<b>${esc(bb.f.txt)}</b> ${chipClase(bb.x.clase)}` : "–", mejorDe(ba ? BONO_CLASE[ba.x.clase] : -200, bb ? BONO_CLASE[bb.x.clase] : -200)],
    ["Probabilidad de acierto", ba ? pct(ba.f.p, 1) : "–", bb ? pct(bb.f.p, 1) : "–", mejorDe(ba?.f.p, bb?.f.p)],
    ["Cuota mínima para apostar", ba?.f.minima ? num(ba.f.minima) : "–", bb?.f.minima ? num(bb.f.minima) : "–", 0],
    ["Índice de evidencia (0-100)", ba ? ba.x.evidencia : "–", bb ? bb.x.evidencia : "–", mejorDe(ba?.x.evidencia, bb?.x.evidencia)],
    ["Incertidumbre (ancho del intervalo)", ba ? `${num(ancho(ba) * 100, 0)} puntos` : "–", bb ? `${num(ancho(bb) * 100, 0)} puntos` : "–", mejorDe(ancho(ba), ancho(bb), false)],
    ["Confianza del modelo", `<span class="conf ${A.pred.conf}">${A.pred.conf}</span>`, `<span class="conf ${B.pred.conf}">${B.pred.conf}</span>`, 0],
    ["Cuotas reales de referencia", A.mercado ? `sí (${esc(A.mercado.proveedor || "")})` : "no", B.mercado ? `sí (${esc(B.mercado.proveedor || "")})` : "no", mejorDe(A.mercado ? 1 : 0, B.mercado ? 1 : 0)],
    ["Alineación", A.alineacion ? A.alineacion.estado : "sin datos", B.alineacion ? B.alineacion.estado : "sin datos", mejorDe(A.alineacion?.estado === "confirmada" ? 1 : 0, B.alineacion?.estado === "confirmada" ? 1 : 0)],
    ["Alertas y discrepancias", alertas(A), alertas(B), mejorDe(alertas(A), alertas(B), false)],
    ["Riesgos del partido", A.intel.riesgos.length, B.intel.riesgos.length, mejorDe(A.intel.riesgos.length, B.intel.riesgos.length, false)],
  ];
  const pa = ba && A.intel.veredicto.cod !== "no" ? ba.pts : -100, pb = bb && B.intel.veredicto.cod !== "no" ? bb.pts : -100;
  let final;
  if (pa < 0 && pb < 0) final = `<div class="veredicto-ia no"><b>NO HAY APUESTA RECOMENDADA</b><span>ninguno de los dos partidos tiene una opción sólida.</span></div>`;
  else if (Math.abs(pa - pb) < 5) final = `<div class="veredicto-ia condicional"><b>Empate técnico</b><span>la evidencia es parecida (${pa} y ${pb} puntos). Decide con la cuota que pague Betano frente a la mínima.</span></div>`;
  else {
    const G = pa > pb ? A : B, g = pa > pb ? ba : bb;
    final = `<div class="veredicto-ia condicional"><b>Mejor oportunidad estadística: ${esc(titulo(G))}</b><span>«${esc(g.f.txt)}», ${CLASES[g.x.clase][0].toLowerCase()}, evidencia ${g.x.evidencia}/100. Solo si Betano paga ${g.f.minima ? num(g.f.minima) : "la cuota mínima"} o más.</span></div>`;
  }
  cont.innerHTML = `<h2>Comparador de partidos</h2>
    <p class="nota">Elige dos partidos. Se comparan por calidad de la evidencia e incertidumbre, no por cuánto pagan.</p>
    <div class="cmp-sel"><select id="cmp-a">${ops(CMP.a)}</select><select id="cmp-b">${ops(CMP.b)}</select></div>
    ${final}
    <div class="bloque"><table class="t cmp"><tr><th></th><th>${esc(titulo(A))}</th><th>${esc(titulo(B))}</th></tr>
      ${filas.map(([n, x, y, g]) => `<tr><td>${n}</td><td class="${g === -1 ? "gana" : ""}">${x}</td><td class="${g === 1 ? "gana" : ""}">${y}</td></tr>`).join("")}</table>
      <div class="acciones"><button class="boton" data-id="${A.id}">Abrir ${esc(A.h.n)}</button><button class="boton" data-id="${B.id}">Abrir ${esc(B.h.n)}</button></div></div>`;
  $("#cmp-a").onchange = e => { CMP.a = e.target.value; pintarComparar(); };
  $("#cmp-b").onchange = e => { CMP.b = e.target.value; pintarComparar(); };
  cont.querySelectorAll("button[data-id]").forEach(b => b.onclick = () => abrirDetalle(b.dataset.id));
}

// ---------------------------------------------------------------- combinadas (con correlación)
let COMB = leerLS("pf_comb", []);
const filasCombinables = m => (m.apuesta?.mercados || []).filter(f => !f.no_calibrado && !(f.p_dev > 0));
function calcularCombinada(sel) {
  const grupos = {};
  sel.forEach(s => (grupos[s.id] = grupos[s.id] || []).push(s));
  let conj = 1, ingenua = 1, mismo = false, error = null;
  for (const [id, ss] of Object.entries(grupos)) {
    const m = partidoDe(id), fs = ss.map(s => m.apuesta.mercados.find(f => f.cod === s.cod));
    fs.forEach(f => ingenua *= f.p);
    if (ss.length === 1) { conj *= fs[0].p; continue; }
    // mismo partido: los resultados dependen unos de otros → probabilidad conjunta con la matriz de marcadores
    const M = m.alineacion?.matriz || m.matriz, ps = ss.map(s => predicado(s.cod));
    if (!M || ps.some(p => !p)) { error = `No se puede calcular la combinación de «${fs.map(f => f.txt).join("» + «")}» en un mismo partido.`; continue; }
    conj *= pMatriz(M, (a, b) => ps.every(p => p(a, b)));
    mismo = true;
  }
  return { conj, ingenua, mismo, error, partidos: Object.keys(grupos).length };
}
function pintarCombinada() {
  const cont = $("#v-combinada");
  if (!DATOS) return;
  const ps = porJugar().filter(m => horasPara(m) <= 24 * 7 && filasCombinables(m).length).sort((a, b) => a.fecha.localeCompare(b.fecha));
  COMB = COMB.filter(s => { const m = ps.find(x => x.id === s.id); return m && filasCombinables(m).some(f => f.cod === s.cod); });
  guardarLS("pf_comb", COMB);
  const selM = ps.find(m => m.id === pintarCombinada.m) || ps[0];
  let res = `<p class="nota">Añade dos o más selecciones para evaluar la combinada.</p>`;
  if (COMB.length >= 2) {
    const r = calcularCombinada(COMB);
    if (r.error) res = `<div class="aviso error">${esc(r.error)}</div>`;
    else if (r.conj <= 0) res = `<div class="aviso error">Estas selecciones no pueden cumplirse a la vez (probabilidad 0).</div>`;
    else {
      const dif = (r.conj - r.ingenua) * 100;
      res = `<div class="kpis">${kpi("Probabilidad conjunta", pct(r.conj, 1), `falla ~${Math.round((1 - r.conj) * 10)} de cada 10`)}
        ${kpi("Cuota justa", num(1 / r.conj), `mínima para apostar ${num(cuotaMinimaJS(r.conj))}`)}
        ${kpi("Si se multiplicara sin más", pct(r.ingenua, 1), r.mismo ? `${dif >= 0 ? "+" : "−"}${num(Math.abs(dif), 1)} pp de error por correlación` : "partidos distintos: coincide")}</div>
        ${r.mismo ? `<div class="aviso info"><b>Selecciones del mismo partido: están correlacionadas.</b> No se multiplicaron: la probabilidad conjunta sale de la matriz de marcadores (los ${miles(121)} resultados posibles). Betano puede no permitir esa combinación o pagarla distinto.</div>` : ""}
        ${r.partidos > 1 ? `<div class="aviso"><b>Partidos distintos</b> se tratan como independientes (aproximación razonable). Ojo: el margen de la casa se paga una vez por cada selección, así que una combinada de ${COMB.length} es más cara que ${COMB.length} apuestas simples.</div>` : ""}
        <div class="form-linea" style="max-width:520px"><label>Cuota de la combinada en Betano<input id="comb-cuota" type="number" step="0.01" min="1.01" placeholder="ej. 3,40"></label><div id="comb-ev" class="nota"></div></div>`;
    }
  }
  cont.innerHTML = `<h2>Evaluador de combinadas</h2>
    <p class="nota">Calcula la probabilidad de que se cumplan todas las selecciones a la vez. Solo admite mercados que pasaron la prueba de calibración y sin devolución.</p>
    <div class="bloque"><div class="form-linea">
      <label>Partido<select id="comb-m">${ps.map(m => `<option value="${m.id}"${m === selM ? " selected" : ""}>${diaCorto(m.fecha)} ${horaLocal(m.fecha)} · ${esc(titulo(m))}</option>`).join("")}</select></label>
      <label>Selección<select id="comb-f">${selM ? filasCombinables(selM).map(f => `<option value="${esc(f.cod)}">${esc(f.txt)} · ${pct(f.p)}</option>`).join("") : ""}</select></label>
      <button class="boton primario" id="comb-add">Añadir a la combinada</button></div>
      ${COMB.length ? `<table class="t"><tr><th>Partido</th><th>Selección</th><th class="num">Probabilidad</th><th class="num">Justa</th><th></th></tr>
        ${COMB.map((s, i) => { const m = partidoDe(s.id), f = m.apuesta.mercados.find(x => x.cod === s.cod); return `<tr><td>${esc(titulo(m))}</td><td>${esc(f.txt)}</td><td class="num">${pct(f.p, 1)}</td><td class="num">${num(f.justa)}</td><td class="num"><button class="x-btn" data-quitar="${i}" title="Quitar">✕</button></td></tr>`; }).join("")}</table>
        <div class="acciones"><button class="boton" id="comb-limpiar">Vaciar</button></div>` : ""}
    </div>
    <div class="bloque"><h3>Resultado</h3>${res}</div>`;
  $("#comb-m").onchange = e => { pintarCombinada.m = e.target.value; pintarCombinada(); };
  $("#comb-add").onclick = () => {
    const id = $("#comb-m").value, cod = $("#comb-f").value;
    if (!id || !cod) return;
    if (COMB.some(s => s.id === id && s.cod === cod)) return toast("Esa selección ya está en la combinada");
    if (COMB.length >= 8) return toast("Máximo 8 selecciones");
    COMB.push({ id, cod }); pintarCombinada.m = id; pintarCombinada();
  };
  cont.querySelectorAll("[data-quitar]").forEach(b => b.onclick = () => { COMB.splice(+b.dataset.quitar, 1); pintarCombinada(); });
  if ($("#comb-limpiar")) $("#comb-limpiar").onclick = () => { COMB = []; pintarCombinada(); };
  const inp = $("#comb-cuota");
  if (inp) inp.oninput = () => {
    const o = parseFloat(String(inp.value).replace(",", ".")), p = calcularCombinada(COMB).conj, out = $("#comb-ev");
    if (!(o > 1)) { out.textContent = ""; return; }
    const ev = p * (o - 1) - (1 - p), min = cuotaMinimaJS(p);
    out.innerHTML = `Probabilidad implícita de la cuota: <b>${pct(1 / o, 1)}</b> · valor esperado: <b class="${ev >= 0 ? "sube" : "baja"}">${ev >= 0 ? "+" : "−"}${num(Math.abs(ev) * 100, 1)} %</b> por sol apostado. `
      + (o >= min ? `<span class="ev-si">Supera la cuota mínima (${num(min)}).</span> No garantiza ganar: solo que la cuota compensa el riesgo si la probabilidad es correcta.` : `<span class="ev-no">No llega a la cuota mínima (${num(min)}): NO HAY APUESTA RECOMENDADA.</span>`);
  };
}

// ---------------------------------------------------------------- mi banca (gestión responsable)
let BANCA = Object.assign({ banca: 0, tope_apuesta: 0.5, tope_dia: 2, tope_perdida: 10, apuestas: [] }, leerLS("pf_banca", {}));
const guardarBanca = () => guardarLS("pf_banca", BANCA);
function resumenBanca() {
  const hoy = claveDia(new Date().toISOString()), aps = BANCA.apuestas;
  const gan = a => a.estado === "ganada" ? a.monto * (a.cuota - 1) : a.estado === "perdida" ? -a.monto : 0;
  const resueltas = aps.filter(a => a.estado === "ganada" || a.estado === "perdida");
  const pyg = aps.reduce((t, a) => t + gan(a), 0), apostado = resueltas.reduce((t, a) => t + a.monto, 0);
  let racha = 0;
  for (const a of [...resueltas].sort((x, y) => y.fecha.localeCompare(x.fecha))) { if (a.estado === "perdida") racha++; else break; }
  return { pyg, apostado, roi: apostado ? pyg / apostado : null, actual: BANCA.banca + pyg, racha, n: resueltas.length, aciertos: resueltas.filter(a => a.estado === "ganada").length,
    hoy: aps.filter(a => claveDia(a.fecha) === hoy).reduce((t, a) => t + a.monto, 0), pendiente: aps.filter(a => a.estado === "pendiente").reduce((t, a) => t + a.monto, 0),
    tope: BANCA.banca * BANCA.tope_perdida / 100, maxApuesta: Math.max(0, BANCA.banca + pyg) * BANCA.tope_apuesta / 100, maxDia: BANCA.banca * BANCA.tope_dia / 100 };
}
function pintarBanca() {
  const cont = $("#v-banca"), r = resumenBanca(), S = x => `S/ ${num(x)}`, detenido = BANCA.banca > 0 && -r.pyg >= r.tope;
  const pre = leerLS("pf_banca_pre", null);
  cont.innerHTML = `<h2>Mi banca <small class="nota">gestión responsable</small></h2>
    <div class="aviso">Las apuestas <b>no son una fuente segura de ingresos</b>. Este programa no ha demostrado ganarle a la casa. Apuesta solo dinero que puedas perder, solo si eres mayor de 18 y en casas autorizadas por el MINCETUR. Si te causa estrés o deudas: Línea 113, opción 5 (MINSA).</div>
    ${detenido ? `<div class="aviso error"><b>Llegaste a tu tope de pérdida (${S(r.tope)}).</b> El registro de nuevas apuestas queda bloqueado. Para seguir tendrías que subir el tope a propósito: piénsalo antes.</div>` : ""}
    ${r.racha >= 3 ? `<div class="aviso"><b>${r.racha} pérdidas seguidas.</b> Haz una pausa. No subas el monto para recuperar: es la forma más rápida de perder la banca.</div>` : ""}
    <div class="kpis">
      ${kpi("Banca actual", S(r.actual), `inicial ${S(BANCA.banca)}`)}
      <div class="kpi"><div class="tit">Ganancia o pérdida</div><div class="val" style="background:none;color:${r.pyg >= 0 ? "var(--verde)" : "var(--rojo)"}">${r.pyg >= 0 ? "+" : "−"}${S(Math.abs(r.pyg))}</div><div class="sub">${r.roi == null ? "sin apuestas resueltas" : `rendimiento ${num(r.roi * 100, 1)} % sobre ${S(r.apostado)}`}</div></div>
      ${kpi("Apuestas resueltas", r.n, r.n ? `${r.aciertos} ganadas (${pct(r.aciertos / r.n)})` : "")}
      ${kpi("Apostado hoy", S(r.hoy), `límite diario ${S(r.maxDia)}`)}
      ${kpi("En juego", S(r.pendiente), "apuestas pendientes")}
      ${kpi("Máximo por apuesta", S(r.maxApuesta), `${num(BANCA.tope_apuesta)} % de la banca actual`)}
    </div>
    <div class="bloque"><h3>Mis límites <small>se guardan solo en este dispositivo</small></h3><div class="form-linea">
      <label>Banca inicial (S/)<input id="b-banca" type="number" min="0" step="1" value="${BANCA.banca || ""}"></label>
      <label>Máximo por apuesta (% de la banca)<input id="b-ta" type="number" min="0.1" max="5" step="0.1" value="${BANCA.tope_apuesta}"></label>
      <label>Límite diario (% de la banca)<input id="b-td" type="number" min="0.5" max="20" step="0.5" value="${BANCA.tope_dia}"></label>
      <label>Tope de pérdida total (% de la banca)<input id="b-tp" type="number" min="1" max="100" step="1" value="${BANCA.tope_perdida}"></label>
      <button class="boton" id="b-guardar">Guardar límites</button></div></div>
    <div class="bloque"><h3>Registrar una apuesta</h3><div class="form-linea">
      <label>Partido<input id="b-partido" list="b-lista" value="${esc(pre?.partido || "")}" placeholder="Equipo A vs Equipo B"><datalist id="b-lista">${porJugar().filter(m => horasPara(m) <= 72).map(m => `<option value="${esc(titulo(m))}">`).join("")}</datalist></label>
      <label>Selección<input id="b-sel" value="${esc(pre?.sel || "")}" placeholder="ej. Más de 1,5 goles"></label>
      <label>Cuota de Betano<input id="b-cuota" type="number" min="1.01" step="0.01"></label>
      <label>Monto (S/)<input id="b-monto" type="number" min="0.1" step="0.1"></label>
      <button class="boton primario" id="b-add" ${detenido ? "disabled" : ""}>Registrar</button></div>
      ${pre?.minima ? `<p class="nota">Cuota mínima calculada para esta selección: <b>${num(pre.minima)}</b>. Con menos, no apuestes.</p>` : ""}</div>
    <div class="bloque"><h3>Registro de apuestas</h3>
      ${BANCA.apuestas.length ? `<table class="t"><tr><th>Fecha</th><th>Partido</th><th>Selección</th><th class="num">Cuota</th><th class="num">Monto</th><th>Estado</th><th></th></tr>
        ${[...BANCA.apuestas].sort((a, b) => b.fecha.localeCompare(a.fecha)).map(a => `<tr><td>${diaCorto(a.fecha)}</td><td>${esc(a.partido)}</td><td>${esc(a.sel)}</td><td class="num">${num(a.cuota)}</td><td class="num">${S(a.monto)}</td>
          <td>${a.estado === "pendiente" ? ["ganada", "perdida", "devuelta"].map(e => `<button class="mini-btn" data-res="${a.id}" data-e="${e}">${e}</button>`).join("") : `<span class="res-ap ${{ ganada: "gana", perdida: "pierde", devuelta: "devuelve" }[a.estado]}">${a.estado.toUpperCase()}</span>`}</td>
          <td class="num"><button class="x-btn" data-borrar="${a.id}" title="Borrar">✕</button></td></tr>`).join("")}</table>` : `<p class="nota">Aún no registras apuestas.</p>`}</div>`;
  const n = id => parseFloat(String($(id).value).replace(",", "."));
  $("#b-guardar").onclick = () => {
    Object.assign(BANCA, { banca: Math.max(0, n("#b-banca") || 0), tope_apuesta: Math.min(5, Math.max(0.1, n("#b-ta") || 0.5)), tope_dia: Math.min(20, Math.max(0.5, n("#b-td") || 2)), tope_perdida: Math.min(100, Math.max(1, n("#b-tp") || 10)) });
    guardarBanca(); pintarBanca(); toast("Límites guardados");
  };
  $("#b-add").onclick = () => {
    const a = { id: String(Date.now()), fecha: new Date().toISOString(), partido: $("#b-partido").value.trim(), sel: $("#b-sel").value.trim(), cuota: n("#b-cuota"), monto: n("#b-monto"), estado: "pendiente" };
    if (!(BANCA.banca > 0)) return toast("Primero define tu banca inicial y tus límites");
    if (!a.partido || !a.sel || !(a.cuota > 1) || !(a.monto > 0)) return toast("Completa partido, selección, cuota y monto");
    if (a.monto > r.maxApuesta + 1e-9) return toast(`Supera tu máximo por apuesta (${S(r.maxApuesta)})`);
    if (r.hoy + a.monto > r.maxDia + 1e-9) return toast(`Supera tu límite diario (${S(r.maxDia)})`);
    BANCA.apuestas.push(a); guardarBanca(); guardarLS("pf_banca_pre", null); pintarBanca(); toast("Apuesta registrada");
  };
  cont.querySelectorAll("[data-res]").forEach(b => b.onclick = () => { BANCA.apuestas.find(a => a.id === b.dataset.res).estado = b.dataset.e; guardarBanca(); pintarBanca(); });
  cont.querySelectorAll("[data-borrar]").forEach(b => b.onclick = () => { if (confirm("¿Borrar esta apuesta del registro?")) { BANCA.apuestas = BANCA.apuestas.filter(a => a.id !== b.dataset.borrar); guardarBanca(); pintarBanca(); } });
}

// ---------------------------------------------------------------- detalle: veredicto de inteligencia
function htmlIntel(m) {
  const it = m.intel;
  if (!it) return "";
  const act = perfilActual(), pf = DATOS.apuestas?.perfiles || {};
  const cajas = ordenPerfiles().map(k => {
    const x = it.perfiles[k], f = pickDe(m, k);
    if (!x || !f) return "";
    const ref = f.cuota_casa ? `<p class="nota">En ${esc(m.mercado?.proveedor || "la casa de referencia")} paga ${num(f.cuota_casa)}: valor esperado <b class="${f.ev_casa >= 0 ? "sube" : "baja"}">${f.ev_casa >= 0 ? "+" : "−"}${num(Math.abs(f.ev_casa) * 100, 1)} %</b>.</p>` : "";
    return `<div class="caja perfil-ia${k === act ? " activo" : ""}"><div class="tit">${esc(pf[k]?.nombre || k)}</div><div class="pick-ia">${esc(f.txt)}</div><div>${chipClase(x.clase)}</div>
      <div class="cif">Probabilidad <b>${pct(f.p)}</b> (${rango([f.p10, f.p90])}) · cuota mínima <b>${f.minima ? num(f.minima) : "–"}</b></div>
      <div class="cif">Evidencia <b>${x.evidencia}</b>/100</div><div class="barra-ev"><i style="width:${x.evidencia}%"></i></div>
      <p class="nota">${esc(x.motivo)}.</p>${ref}
      <button class="mini-btn" data-banca="${k}">Anotar en Mi banca</button></div>`;
  }).join("");
  const x = it.perfiles[act] || Object.values(it.perfiles)[0];
  const disc = (it.discrepancias || []).map(d => `<div class="senal ${d.nivel}"><span>${esc(d.texto)}</span></div>`).join("");
  const c = it.carga || {};
  const chips = [["firme", "Confirmado: calendario, tabla y resultados (ESPN)"],
    m.mercado && ["firme", `Confirmado: cuotas de ${m.mercado.proveedor || "la casa"} (vía ESPN)`],
    m.alineacion?.estado === "confirmada" && ["firme", "Confirmado: alineación oficial (ESPN)"],
    m.alineacion?.estado === "probable" && ["est", "Estimado: once probable (el habitual)"],
    m.clima && ["est", "Estimado: pronóstico del clima (Open-Meteo)"],
    m.noticias && ["est", "Sin verificar: titulares de Google Noticias"],
    ["pre", `Predicción: probabilidades del modelo v${DATOS.version_modelo || "–"}`]].filter(Boolean);
  return `<div class="seccion" id="sec-intel"><h3>Veredicto de inteligencia <small>riesgo, evidencia y condiciones · actualizado ${haceCuanto(DATOS.generado)}</small></h3>
    <div class="veredicto-ia ${it.veredicto.cod}"><b>${esc(it.veredicto.txt)}</b><span>${esc(it.veredicto.motivo)}.</span></div>
    <div class="tres">${cajas}</div>
    ${x ? `<div class="listas">
      <div class="caja lista-ia fav"><h4>A favor</h4>${lista(x.favorables, "Ningún dato destaca a favor: la apuesta se apoya solo en la probabilidad.")}</div>
      <div class="caja lista-ia rie"><h4>Riesgos</h4>${lista([...x.riesgos, ...it.riesgos], "Sin riesgos detectados en los datos.")}</div>
      <div class="caja lista-ia inv"><h4>Dejaría de valer si…</h4>${lista(x.invalidan, "Sin condiciones registradas.")}</div></div>
      <p class="nota">Listas de la apuesta «${esc(pf[act]?.nombre || act)}». La clase mide riesgo e incertidumbre, no promete ganancias. El índice de evidencia mide la calidad de los datos, no la probabilidad de acertar.</p>` : ""}
    ${disc ? `<div style="margin-top:8px"><div class="tit" style="color:var(--suave);font-size:12px;font-weight:600;margin-bottom:6px">Discrepancias detectadas</div>${disc}</div>` : ""}
    <div class="datos-ia">${chips.map(([t, s]) => `<span class="dato-chip ${t}">${esc(s)}</span>`).join("")}
      ${c.h ? `<span class="dato-chip">Partidos en 14 días: ${esc(m.h.c || m.h.n)} ${c.h.p14} · ${esc(m.a.c || m.a.n)} ${c.a.p14}</span>` : ""}
      ${c.viaje_km != null ? `<span class="dato-chip">Viaje del visitante: ~${miles(c.viaje_km)} km</span>` : ""}</div>
  </div>`;
}

// ---------------------------------------------------------------- detalle: simulación calculada por el programa
function htmlSimServidor(m) {
  const s = m.sim;
  if (!s) return "";
  const e = s.estabilidad, P = s.p, L = esc(m.h.n), V = esc(m.a.n);
  const fila = (txt, k) => {
    if (P[k] == null) return "";
    const ic = wilson(P[k], s.n);
    return `<tr><td>${txt}</td><td class="num"><b>${pct(P[k], 1)}</b></td><td class="num nota">${pct(ic[0], 1)}–${pct(ic[1], 1)}</td><td class="num">${P[k] > 0.005 ? num(1 / P[k]) : "–"}</td></tr>`;
  };
  const grupo = (t, filas) => filas.join("") ? `<tr class="grupo-fila"><td colspan="4">${t}</td></tr>${filas.join("")}` : "";
  return `<div class="seccion"><h3>Simulación de ${miles(s.n)} escenarios <small>calculada por el programa · se puede repetir (semilla ${s.semilla})</small></h3>
    <div class="caja">
      <div class="estab ${e.estable ? "ok" : "mal"}">${e.estable ? "✓ Estable" : "⚠ Variación algo alta (puede pasar por azar en ~1 de cada 100 partidos)"}: error máximo frente al valor exacto del modelo ${num(e.error_vs_exacto * 100, 2)} pp (tolerancia ${num(e.tolerancia * 100, 2)}) · variación entre ${e.lotes} lotes de ${miles(s.n / e.lotes)}: ±${num(e.desv_lotes * 100, 1)} pp</div>
      <table class="t"><tr><th>Escenario</th><th class="num">Probabilidad</th><th class="num">Intervalo 95 %</th><th class="num">Cuota justa</th></tr>
        ${grupo("Escenarios de dominio", [fila(`${L} domina (gana por 2 o más)`, "DOM_L"), fila(`${V} domina (gana por 2 o más)`, "DOM_V"), fila("Partido cerrado (diferencia de 1 o empate)", "CERRADO"), fila("Partido abierto (4 goles o más)", "ABIERTO"), fila("Partido trabado (1 gol o ninguno)", "TRABADO")])}
        ${grupo("Ganador de cualquier mitad", [fila(`${L} gana al menos una mitad`, "MIT_L"), fila(`${V} gana al menos una mitad`, "MIT_V"), fila("Algún equipo gana las dos mitades", "MIT_ALGUNO_AMBAS"), fila(`${L} gana las dos mitades`, "MIT_L_AMBAS"), fila(`${V} gana las dos mitades`, "MIT_V_AMBAS"), fila("Las dos mitades terminan empatadas", "MIT_EMPATES")])}
        ${grupo("Equipo que marca primero", [fila(L, "PRIMERO_L"), fila(V, "PRIMERO_V"), fila("Nadie (0-0)", "PRIMERO_NADIE")])}
        ${grupo("Segundo tiempo", [fila(`Gana ${L}`, "2T_1"), fila("Empate", "2T_X"), fila(`Gana ${V}`, "2T_2"), fila("Más de 0,5 goles", "2T_O0.5"), fila("Más de 1,5 goles", "2T_O1.5"), fila("Ambos marcan", "2T_BTTS")])}
      </table>
      <p class="nota">Cada escenario sale de los mismos ${miles(s.n)} partidos simulados: marcador sorteado con la matriz del modelo, goles repartidos entre tiempos con la proporción histórica y orden de los goles al azar. El intervalo es el error del sorteo; la incertidumbre del modelo está en «Probabilidades del resultado». Estos mercados no tienen cuota mínima validada: compara tu cuota con la cuota justa y exige un margen.</p>
    </div></div>`;
}

// ---------------------------------------------------------------- detalle: ¿qué pasa si…?
function htmlEscenarios(m) {
  if (!m.matriz) return "";
  const botones = (m.intel?.escenarios || []).map((e, i) => `<button class="chip" data-esc="py:${i}">${esc(e.txt)}</button>`).join("");
  return `<div class="seccion"><h3>¿Qué pasa si…? <small>análisis de escenarios</small></h3>
    <div class="caja"><div class="chips">${botones}
      <button class="chip" data-esc="diez:h">${esc(m.h.n)} con diez</button><button class="chip" data-esc="diez:a">${esc(m.a.n)} con diez</button>
      <button class="chip" data-esc="lluvia">Lluvia intensa</button><button class="chip" data-esc="libre">Supuesto propio</button></div>
      <div id="esc-ctl"></div><div id="esc-res"><p class="nota">Elige un escenario para ver cómo cambian las probabilidades.${botones ? "" : " Los escenarios de bajas (goleador, portero, rotación) aparecen cuando hay historial de suplentes del equipo."} No se ofrece «cambio de formación»: las fuentes actuales no dan la formación con fiabilidad.</p></div></div></div>`;
}
function conectarEscenarios(m) {
  const ctl = $("#esc-ctl"), res = $("#esc-res");
  if (!ctl) return;
  const rho = DATOS.modelo?.rho ?? 0, l0 = { h: m.pred.lh, a: m.pred.la }, M0 = matrizDC(l0.h, l0.a, rho);
  const cifras = M => ({ H: pMatriz(M, (a, b) => a > b), X: pMatriz(M, (a, b) => a === b), A: pMatriz(M, (a, b) => a < b), O: pMatriz(M, (a, b) => a + b > 2.5), B: pMatriz(M, (a, b) => a > 0 && b > 0) });
  const mostrar = (lh, la, txt, nota, fuera = {}, entran = {}) => {
    const M1 = matrizDC(lh, la, rho), x = cifras(M0), y = cifras(M1);
    const f = (n, a, b, esP = true) => { const d = (b - a) * (esP ? 100 : 1); return `<tr><td>${n}</td><td class="num">${esP ? pct(a, 1) : num(a)}</td><td class="num"><b>${esP ? pct(b, 1) : num(b)}</b></td><td class="num ${d > 0.05 ? "sube" : d < -0.05 ? "baja" : ""}">${d >= 0 ? "+" : "−"}${num(Math.abs(d), esP ? 1 : 2)}${esP ? " pp" : ""}</td></tr>`; };
    res.innerHTML = `<table class="t"><tr><th>${esc(txt)}</th><th class="num">Normal</th><th class="num">Escenario</th><th class="num">Cambio</th></tr>
      ${f(`Goles esperados ${esc(m.h.n)}`, l0.h, lh, false)}${f(`Goles esperados ${esc(m.a.n)}`, l0.a, la, false)}
      ${f(`Gana ${esc(m.h.n)}`, x.H, y.H)}${f("Empate", x.X, y.X)}${f(`Gana ${esc(m.a.n)}`, x.A, y.A)}${f("Más de 2,5 goles", x.O, y.O)}${f("Ambos marcan", x.B, y.B)}</table>
      <p class="nota">${nota}</p><div class="acciones"><button class="boton" id="esc-sim">Simular 10 000 partidos con este escenario</button></div>`;
    $("#esc-sim").onclick = () => pintarSimulacion(m, { lh, la, matriz: M1, txt, fuera: { h: fuera.h || [], a: fuera.a || [] }, entran: { h: entran.h || [], a: entran.a || [] } });
  };
  const libre = (fh, fa, txt, nota) => {
    ctl.innerHTML = `<div class="esc-ctl"><label>Goles de ${esc(m.h.n)}: ×<b id="esc-vh">${num(fh)}</b><input type="range" id="esc-fh" min="0.5" max="1.5" step="0.01" value="${fh}"></label>
      <label>Goles de ${esc(m.a.n)}: ×<b id="esc-va">${num(fa)}</b><input type="range" id="esc-fa" min="0.5" max="1.5" step="0.01" value="${fa}"></label></div>`;
    const ir = () => { const a = +$("#esc-fh").value, b = +$("#esc-fa").value; $("#esc-vh").textContent = num(a); $("#esc-va").textContent = num(b); mostrar(l0.h * a, l0.a * b, txt, nota); };
    $("#esc-fh").oninput = $("#esc-fa").oninput = ir; ir();
  };
  const diez = s => {
    ctl.innerHTML = `<div class="esc-ctl"><label>Minuto de la expulsión: <b id="esc-vm">30</b><input type="range" id="esc-min" min="0" max="85" step="5" value="30"></label>
      <label>Sus goles ×<input type="number" id="esc-fp" min="0.2" max="1" step="0.01" value="0.67"></label><label>Goles del rival ×<input type="number" id="esc-fr" min="1" max="2" step="0.01" value="1.25"></label></div>`;
    const ir = () => {
      const t = +$("#esc-min").value / 90, fp = +$("#esc-fp").value || 0.67, fr = +$("#esc-fr").value || 1.25;
      const propio = t + (1 - t) * fp, rival = t + (1 - t) * fr;      // el efecto solo cuenta desde la expulsión
      $("#esc-vm").textContent = $("#esc-min").value;
      mostrar(l0.h * (s === "h" ? propio : rival), l0.a * (s === "a" ? propio : rival), `${m[s].n} con diez desde el minuto ${$("#esc-min").value}`,
        "Valores de referencia de un estudio publicado (Vecer, Kopriva e Ichiba, 2009): el equipo con diez marca cerca de ×0,67 y su rival ×1,25 desde la expulsión. <b>No está validado con tus datos</b> (ESPN no da el minuto de las expulsiones pasadas): puedes cambiar los dos factores.");
    };
    ["#esc-min", "#esc-fp", "#esc-fr"].forEach(q => $(q).oninput = ir); ir();
  };
  $("#detalle").querySelectorAll("[data-esc]").forEach(b => b.onclick = () => {
    $("#detalle").querySelectorAll("[data-esc]").forEach(x => x.classList.toggle("activo", x === b));
    const [tipo, arg] = b.dataset.esc.split(":");
    if (tipo === "py") {
      const e = m.intel.escenarios[+arg], s = e.cod.slice(-1);
      ctl.innerHTML = "";
      mostrar(e.lh, e.la, e.txt, `Sale ${esc(e.fuera.join(", "))}${e.entran.filter(Boolean).length ? ` y entraría ${esc(e.entran.filter(Boolean).join(", "))}` : ""}. Calculado con el aporte medido de cada jugador y el mismo ajuste validado de las alineaciones (efecto pequeño y con tope).`, { [s]: e.fuera }, { [s]: e.entran.filter(Boolean) });
    } else if (tipo === "diez") diez(arg);
    else if (tipo === "lluvia") libre(1, 1, "Lluvia intensa (supuesto tuyo)", "El programa <b>no tiene evidencia propia</b> del efecto de la lluvia (no guarda el clima de los partidos pasados), así que por defecto no cambia nada. Mueve los controles si quieres probar tu propio supuesto.");
    else libre(1, 1, "Supuesto propio", "Escenario hipotético con los factores que tú elijas. No es una predicción comprobada.");
  });
}

// ---------------------------------------------------------------- detalle: cancha con el once y radar
const X_LINEA = { h: { POR: 6, DEF: 19, MED: 32, DEL: 44 }, a: { POR: 94, DEF: 81, MED: 68, DEL: 56 } };
const COLOR_RADAR = ["#38bdf8", "#a78bfa", "#fbbf24"];
let RADAR = [];
function htmlCancha(m) {
  const al = m.alineacion;
  if (!al?.h?.jugadores?.length || !al?.a?.jugadores?.length) return "";
  const claves = new Set(["h", "a"].flatMap(s => (m.intel?.claves?.[s] || []).map(c => c.id)));
  const equipo = s => {
    const porLinea = {};
    al[s].jugadores.forEach(j => (porLinea[X_LINEA[s][j.linea] ? j.linea : "MED"] ||= []).push(j));
    return Object.entries(porLinea).flatMap(([ln, js]) => js.map((j, i) => {
      const y = (i + 1) / (js.length + 1) * 100;
      const info = `${j.n} · ${j.pos || ln} · ${miles(j.minutos)} min · goles/90 ${num(j.g90)} · asist./90 ${num(j.a90)} · remates/90 ${num(j.rem90)}${j.prob_gol != null ? ` · marca ${pct(j.prob_gol)}` : ""}${j.posible_baja ? ` · POSIBLE BAJA (${j.posible_baja.fuente || "noticias"})` : ""}`;
      return `<div class="jug ${s}${claves.has(j.id) ? " clave" : ""}${j.posible_baja ? " duda" : ""}" style="left:${X_LINEA[s][ln]}%;top:${y}%" title="${esc(info)}"><i>${esc(j.pos || ln)}</i><span>${esc(j.n.split(" ").slice(-1)[0])}</span></div>`;
    })).join("");
  };
  const conf = al.estado === "confirmada";
  return `<div class="seccion"><h3>Alineaciones en la cancha <small>${conf ? "oficial confirmada (ESPN)" : "once probable: el habitual (estimación)"}</small></h3>
    <div class="campo"><div class="area izq"></div><div class="area der"></div>${equipo("h")}${equipo("a")}</div>
    <div class="campo-pie"><span><i class="m" style="background:var(--local)"></i>${esc(m.h.n)}${al.h.formacion ? " · " + esc(al.h.formacion) : ""}</span><span><i class="m" style="background:var(--violeta)"></i>${esc(m.a.n)}${al.a.formacion ? " · " + esc(al.a.formacion) : ""}</span>
      <span><i class="m" style="outline:2px solid var(--amarillo);background:none"></i>jugador determinante</span><span><i class="m" style="outline:2px dashed var(--rojo);background:none"></i>posible baja (noticias)</span>
      <span class="xi-badge">${conf ? "CONFIRMADA" : "PROBABLE"}</span></div>
    <p class="nota">Niveles: <b>probable</b> (el once habitual, estimación) y <b>confirmada</b> (oficial, cerca de 1 hora antes; se revisa cada 15 minutos). «Anunciada por medios» no se muestra como alineación: los titulares solo marcan posibles bajas. Pasa el cursor sobre un jugador para ver sus números.</p>
    <div class="caja" style="margin-top:10px"><div class="tit" style="margin-bottom:8px">Radar de jugadores determinantes <span class="nota">· toca hasta 3 para compararlos</span></div>
      <div class="radar-caja"><div id="radar"></div><div id="radar-lista"></div></div></div></div>`;
}
function pintarRadar(m) {
  const cont = $("#radar");
  if (!cont) return;
  const todos = ["h", "a"].flatMap(s => m.alineacion[s].jugadores.map(j => Object.assign({ lado: s }, j)));
  const EJES = [["g90", "Goles/90"], ["a90", "Asist./90"], ["rem90", "Remates/90"], ["ataque", "Aporte ataque"], ["defensa", "Aporte defensa"], ["minutos", "Minutos"]];
  const val = (j, k) => Math.max(0, k === "defensa" ? -(j.defensa || 0) : (j[k] || 0));      // defensa: negativo = recibe menos goles
  const max = Object.fromEntries(EJES.map(([k]) => [k, Math.max(...todos.map(j => val(j, k)), 1e-9)]));
  const cx = 150, cy = 128, R = 88, pt = (i, r) => { const a = -Math.PI / 2 + i * 2 * Math.PI / EJES.length; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; };
  const anillo = f => `<polygon points="${EJES.map((_, i) => pt(i, R * f).map(v => v.toFixed(1)).join(",")).join(" ")}" fill="none" stroke="rgba(255,255,255,.10)"/>`;
  const sel = RADAR.map(id => todos.find(j => j.id === id)).filter(Boolean);
  const polis = sel.map((j, n) => `<polygon points="${EJES.map(([k], i) => pt(i, R * val(j, k) / max[k]).map(v => v.toFixed(1)).join(",")).join(" ")}" fill="${COLOR_RADAR[n]}33" stroke="${COLOR_RADAR[n]}" stroke-width="2"/>`).join("");
  cont.innerHTML = `<svg viewBox="0 0 300 256" role="img" aria-label="Radar de jugadores">${[0.33, 0.66, 1].map(anillo).join("")}
    ${EJES.map((_, i) => { const [x, y] = pt(i, R); return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,.10)"/>`; }).join("")}${polis}
    ${EJES.map(([, n], i) => { const [x, y] = pt(i, R + 16); return `<text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="middle" font-size="10" fill="#9aa8cf">${n}</text>`; }).join("")}</svg>`;
  const chip = (c, s) => {
    const n = RADAR.indexOf(c.id), j = todos.find(x => x.id === c.id) || {};
    return `<div class="clave-chip${n >= 0 ? " sel" : ""}" style="--c:${COLOR_RADAR[n] || ""}" data-jug="${c.id}"><span class="linea-tag">${esc(c.linea || "")}</span><b>${esc(c.n)}</b>${j.posible_baja ? `<span class="baja-tag">posible baja</span>` : ""}<small>${esc(m[s].c || m[s].n)} · influencia ${num(c.influencia, 2)}</small></div>`;
  };
  $("#radar-lista").innerHTML = ["h", "a"].flatMap(s => (m.intel?.claves?.[s] || []).map(c => chip(c, s))).join("")
    + `<p class="nota">Cada eje se mide contra el mejor de los 22 titulares. «Influencia» suma su aporte en ataque y defensa y su probabilidad de gol: valora el rendimiento medido, no la fama.</p>`;
  $("#radar-lista").querySelectorAll("[data-jug]").forEach(el => el.onclick = () => {
    const id = el.dataset.jug;
    RADAR = RADAR.includes(id) ? RADAR.filter(x => x !== id) : [...RADAR, id].slice(-3);
    pintarRadar(m);
  });
}

// el detalle original ya está pintado: se insertan los bloques nuevos justo después de las probabilidades
window.PF_detalle = m => {
  const ancla = $("#detalle .det-cuerpo .seccion");
  if (!ancla || m.estado !== "pre") return;
  ancla.insertAdjacentHTML("afterend", htmlIntel(m) + htmlSimServidor(m) + htmlEscenarios(m) + htmlCancha(m));
  conectarEscenarios(m);
  RADAR = ["h", "a"].map(s => m.intel?.claves?.[s]?.[0]?.id).filter(Boolean);
  if (m.alineacion) pintarRadar(m);
  $("#detalle").querySelectorAll("[data-banca]").forEach(b => b.onclick = () => {
    const f = pickDe(m, b.dataset.banca);
    guardarLS("pf_banca_pre", { partido: titulo(m), sel: f.txt, minima: f.minima });
    cerrarDetalle(); cambiarVista("banca");
  });
};

window.PF_vistas = { panel: pintarPanel, comparar: pintarComparar, combinada: pintarCombinada, banca: pintarBanca };
if (DATOS) window.PF_vistas[UI.vista]?.();
