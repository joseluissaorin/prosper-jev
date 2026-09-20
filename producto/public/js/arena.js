/* La arena de la recepción, detrás de la portada.
 *
 * Un cuerpo de arena que toma forma con lo que hace la recepcionista. Cada grano conserva su identidad; lo que cambia
 * es la forma a la que tiende y cuánto se suelta o se asienta:
 *   reposo (duna) · escucha (masa que respira) · percibe (anillo) · identifica (núcleo) · busca (abanico)
 *   ofrece (gota) · hecho (sello azul) · alarma (dispersión roja) · declina (hueco)
 * Se maneja desde fuera con window.Arena.forma(nombre), .onda(), .energia(v).
 */
(() => {
  "use strict";
  const cv = document.getElementById("arena");
  if (!cv) return;
  const ctx = cv.getContext("2d", { alpha: false });
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const PAPEL = "#F4F1EA";
  const COLS = ["rgba(26,26,26,", "rgba(31,58,138,", "rgba(200,50,28,"];
  let W = 0, H = 0, DPR = 1, N = 0, visible = true;
  let X, Y, VX, VY, TX, TY, A, R, S, TINT;

  function resize() {
    const box = cv.getBoundingClientRect();
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = Math.max(320, box.width); H = Math.max(320, box.height);
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const want = Math.round(Math.min(3400, Math.max(1300, W * H / 190)));
    if (want !== N) alloc(want);
  }
  function alloc(n) {
    const old = N;
    const nx = new Float32Array(n), ny = new Float32Array(n), nvx = new Float32Array(n), nvy = new Float32Array(n);
    const ntx = new Float32Array(n), nty = new Float32Array(n), na = new Float32Array(n), nr = new Float32Array(n);
    const ns = new Float32Array(n), nt = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      if (i < old) { nx[i] = X[i]; ny[i] = Y[i]; nvx[i] = VX[i]; nvy[i] = VY[i]; na[i] = A[i]; nr[i] = R[i]; ns[i] = S[i]; nt[i] = TINT[i]; }
      else {
        nx[i] = Math.random() * W; ny[i] = H * (0.6 + Math.random() * 0.3);
        na[i] = Math.random() * Math.PI * 2; nr[i] = Math.sqrt(Math.random());
        ns[i] = 0.7 + Math.random() * 0.9; nt[i] = 0;
      }
    }
    X = nx; Y = ny; VX = nvx; VY = nvy; TX = ntx; TY = nty; A = na; R = nr; S = ns; TINT = nt; N = n;
  }
  function noise(x, y, t) {
    return Math.sin(x * 1.7 + t * 0.9) * 0.5 + Math.sin(y * 2.3 - t * 0.7) * 0.3 + Math.sin((x + y) * 3.1 + t * 1.3) * 0.2;
  }

  const state = { shape: "reposo", energy: 0, lado: 0.66 };
  const center = () => {
    const ancho = W > 900;
    return { cx: ancho ? W * state.lado : W / 2, cy: ancho ? H * 0.5 : H * 0.36, R: Math.min(W, H) * (ancho ? 0.27 : 0.3) };
  };

  function targets(t) {
    const { cx, cy, R: RR } = center();
    const sh = state.shape;
    for (let i = 0; i < N; i++) {
      const a = A[i], r = R[i];
      let x, y;
      if (sh === "reposo") {                                  // duna asentada, de lado a lado
        const u = (i / N) * 2 - 1;
        x = W / 2 + u * W * 0.52;
        const h = H * 0.2 * Math.exp(-((u - (state.lado - 0.5) * 1.6) ** 2) * 2.6) + H * 0.035 * Math.sin(u * 7);
        y = H * 0.93 - r * h;
      } else if (sh === "escucha") {
        const k = 1 + 0.10 * noise(Math.cos(a), Math.sin(a), t) + 0.10 * state.energy;
        x = cx + Math.cos(a) * r * RR * k; y = cy + Math.sin(a) * r * RR * k * 0.92;
      } else if (sh === "percibe") {
        const rr = RR * (0.78 + 0.22 * r) * 0.95, aa = a + t * 0.9;
        x = cx + Math.cos(aa) * rr; y = cy + Math.sin(aa) * rr;
      } else if (sh === "identifica") {
        const rr = RR * 0.42 * Math.pow(r, 0.8);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "busca") {
        const d = Math.pow(r, 1.6), ang = (a / (Math.PI * 2) - 0.5) * 1.15;
        const L = RR * 2.1, ox = cx - RR * 0.9;
        x = ox + Math.cos(ang) * d * L; y = cy + Math.sin(ang) * d * L * 0.9 + 0.08 * RR * noise(a, r, t);
      } else if (sh === "ofrece") {
        if (i % 10 < 7) {
          const rr = RR * 0.28 * Math.pow(r, 0.7);
          x = cx + RR * 0.55 + Math.cos(a) * rr; y = cy - RR * 0.1 + Math.sin(a) * rr;
        } else {
          const d = Math.pow(r, 1.6), ang = (a / (Math.PI * 2) - 0.5) * 1.15;
          x = cx - RR * 0.9 + Math.cos(ang) * d * RR * 1.6; y = cy + Math.sin(ang) * d * RR * 1.4;
        }
      } else if (sh === "hecho") {
        const edge = i % 7 === 0;
        const rr = edge ? RR * (0.66 + 0.03 * r) : RR * 0.6 * Math.pow(r, 0.55);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "alarma") {
        const rr = Math.max(W, H) * (0.45 + 0.35 * r);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else if (sh === "declina") {
        const rr = RR * (0.7 + 0.35 * r);
        x = cx + Math.cos(a) * rr; y = cy + Math.sin(a) * rr;
      } else { x = cx; y = cy; }
      TX[i] = x; TY[i] = y;
    }
  }

  const SHAPE = {
    reposo: { loose: 0.08, k: 0.020 }, escucha: { loose: 0.55, k: 0.030 }, percibe: { loose: 0.25, k: 0.045 },
    identifica: { loose: 0.10, k: 0.050 }, busca: { loose: 0.85, k: 0.028 }, ofrece: { loose: 0.30, k: 0.040 },
    hecho: { loose: 0.04, k: 0.060 }, alarma: { loose: 1.0, k: 0.020 }, declina: { loose: 0.30, k: 0.035 },
  };
  let back = null;
  function forma(name, holdMs = 0, then = null) {
    if (!SHAPE[name]) return;
    state.shape = name;
    if (back) clearTimeout(back);
    back = holdMs && then ? setTimeout(() => forma(then), holdMs) : null;
    const tint = name === "hecho" ? 1 : name === "alarma" ? 2 : 0;
    for (let i = 0; i < N; i++) TINT[i] = tint && (tint === 2 || i % 3 === 0) ? tint : 0;
  }

  const waves = [];
  function onda(strength = 1, x = null, y = null) {
    if (REDUCED) return;
    const { cx, cy } = center();
    waves.push({ t0: performance.now(), s: strength, x: x ?? cx, y: y ?? cy });
  }

  // el dedo y el cursor apartan la arena
  const ptr = { x: -1e4, y: -1e4, vx: 0, vy: 0, on: false, t: 0 };
  addEventListener("pointermove", (e) => {
    const box = cv.getBoundingClientRect();
    if (e.clientY > box.bottom || e.clientY < box.top) { ptr.on = false; return; }
    const now = performance.now(), dt = Math.max(8, now - ptr.t), x = e.clientX - box.left, y = e.clientY - box.top;
    if (ptr.on) { ptr.vx = (x - ptr.x) / dt * 16; ptr.vy = (y - ptr.y) / dt * 16; }
    ptr.x = x; ptr.y = y; ptr.t = now; ptr.on = true;
  }, { passive: true });
  addEventListener("pointerleave", () => { ptr.on = false; });

  let last = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    if (!visible) { last = now; return; }
    const dt = Math.min(48, now - last) / 16.67; last = now;
    const t = now / 1000;
    targets(t);
    const cfg = SHAPE[state.shape] || SHAPE.reposo;
    const loose = cfg.loose + state.energy * 0.6, k = cfg.k;
    state.energy *= 0.94; ptr.vx *= 0.85; ptr.vy *= 0.85;
    for (let w = waves.length - 1; w >= 0; w--) if (now - waves[w].t0 > 1400) waves.splice(w, 1);
    for (let i = 0; i < N; i++) {
      let ax = (TX[i] - X[i]) * k, ay = (TY[i] - Y[i]) * k;
      if (!REDUCED) {
        ax += loose * 0.35 * noise(X[i] * 0.01, Y[i] * 0.01, t + i * 0.0007);
        ay += loose * 0.35 * noise(Y[i] * 0.01, X[i] * 0.01, t * 1.1 - i * 0.0005);
      }
      if (ptr.on) {
        const dx = X[i] - ptr.x, dy = Y[i] - ptr.y, d2 = dx * dx + dy * dy;
        if (d2 < 6400) {
          const d = Math.sqrt(d2) + 1e-3, fall = 1 - d / 80;
          const sp = Math.min(3, 0.6 + Math.hypot(ptr.vx, ptr.vy) * 0.25);
          ax += (dx / d) * fall * sp + ptr.vx * fall * 0.08; ay += (dy / d) * fall * sp + ptr.vy * fall * 0.08;
        }
      }
      for (const wv of waves) {
        const age = (now - wv.t0) / 1000, front = age * Math.max(W, H) * 0.55;
        const dx = X[i] - wv.x, dy = Y[i] - wv.y, d = Math.hypot(dx, dy) + 1e-3;
        const g = Math.exp(-((d - front) ** 2) / 1800) * 0.9 * wv.s * (1 - age / 1.4);
        ax += (dx / d) * g; ay += (dy / d) * g;
      }
      VX[i] = (VX[i] + ax * dt) * 0.86; VY[i] = (VY[i] + ay * dt) * 0.86;
      X[i] += VX[i] * dt; Y[i] += VY[i] * dt;
    }
    ctx.fillStyle = PAPEL; ctx.fillRect(0, 0, W, H);
    for (let c = 0; c < 3; c++) {
      for (const alpha of [0.35, 0.62, 0.85]) {
        ctx.fillStyle = COLS[c] + alpha + ")";
        for (let i = 0; i < N; i++) {
          if (TINT[i] !== c) continue;
          const band = S[i] < 1.0 ? 0.35 : S[i] < 1.3 ? 0.62 : 0.85;
          if (band !== alpha) continue;
          const s = S[i] * (W < 600 ? 1.15 : 1.35);
          ctx.fillRect(X[i], Y[i], s, s);
        }
      }
    }
  }

  new IntersectionObserver((es) => { visible = es[0].isIntersecting; }, { threshold: 0.02 }).observe(cv);
  addEventListener("resize", resize);
  resize(); forma("reposo"); requestAnimationFrame(frame);

  window.Arena = {
    forma, onda,
    energia(v) { state.energy = Math.min(1, state.energy + v); },
    lado(v) { state.lado = v; },
    get actual() { return state.shape; },
  };
})();
