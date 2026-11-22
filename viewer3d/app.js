// ABHEDYA 3D simulation view. A thin client: it draws whatever the server's /ws says.
// Sim coordinates (x east, y north, z up, metres) -> three.js (x, y up, -z north), centred on the arena.
// Quality: ?q=low drops grass and bloom for slow laptops.
import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { EffectComposer } from './vendor/postprocessing/EffectComposer.js';
import { RenderPass } from './vendor/postprocessing/RenderPass.js';
import { UnrealBloomPass } from './vendor/postprocessing/UnrealBloomPass.js';
import { OutputPass } from './vendor/postprocessing/OutputPass.js';

const HIGH = new URLSearchParams(location.search).get('q') !== 'low';
const CX = 450, CY = 250;
const toV = (x, y, z = 0) => new THREE.Vector3(x - CX, z, -(y - CY));
const fromV = (v) => [v.x + CX, -v.z + CY];
const UNIT_SCALE = 21;                 // units drawn ~14x real size so they read from a kilometre away
const COL = { ok: 0x34e38c, degraded: 0xf4c84b, down: 0xff4f66, rejoining: 0xf4c84b, returning: 0x7fb2ff, charging: 0x7fb2ff };
const $ = (id) => document.getElementById(id);

// ================================================================ renderer, camera, controls
const canvas = $('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, HIGH ? 1.75 : 1));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(48, 1, 1, 24000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.495;
controls.minDistance = 60;
controls.maxDistance = 5000;

const ARENA_CAM = { pos: toV(-420, -700, 46), target: toV(300, 190, 60) };
const CAMS = {
  Cinematic: { ...ARENA_CAM },
  Overview: { pos: new THREE.Vector3(-520, 280, 1180), target: new THREE.Vector3(-30, 30, -80) },
  Top: { pos: new THREE.Vector3(0, 2300, 40), target: new THREE.Vector3(0, 0, 0) },
};
let camName = 'Cinematic';
function setCam(name) {
  camName = name; camera.position.copy(CAMS[name].pos); controls.target.copy(CAMS[name].target); controls.update();
  $('b-cam').textContent = '🎥 ' + name;
}

// post-processing: bloom for LEDs, links, the dome rim and the moon
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.62, 0.55, 0.78);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// ================================================================ noise + terrain (one shape per environment)
function hash(x, y) { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); }
function vnoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function fbm(x, y, o = 5) { let s = 0, a = 0.5, f = 1; for (let i = 0; i < o; i++) { s += a * vnoise(x * f, y * f); a *= 0.5; f *= 2.03; } return s; }
const PLAY = { x0: -820, x1: 820, z0: -720, z1: 720 };
function playDist(x, z) { return Math.hypot(Math.max(PLAY.x0 - x, 0, x - PLAY.x1), Math.max(PLAY.z0 - z, 0, z - PLAY.z1)); }
let TERRAIN = 'field';
const SEA_Y = 640;                                   // coast: sea north of this (sim y)
function terrainH(x, z) {
  const d = playDist(x, z), r = Math.hypot(x, z), sy = -z + CY;
  const ridge = (sc = 900) => Math.pow(1 - Math.abs(fbm(x / sc + 7, z / sc + 3, 5) * 2 - 1), 2.4);
  const ease = (k) => { const t = Math.min(1, d / k); return t * t * (3 - 2 * t); };
  switch (TERRAIN) {
    case 'pass': {                                   // valley: steep snowy walls start close to the field
      if (d <= 0) return (fbm(x / 90, z / 90, 2) - 0.5) * 2;
      return ease(320) * (55 + fbm(x / 300, z / 300) * 90 + ridge(650) * 950 * Math.min(1, d / 1000));
    }
    case 'coast': {
      if (sy > SEA_Y + 30) return -10 - Math.min(40, (sy - SEA_Y) * 0.06);
      if (sy > SEA_Y - 40) return (SEA_Y + 30 - sy) / 70 * 2.0 - 0.6;      // beach slope into the water
      if (d <= 0) return (fbm(x / 90, z / 90, 2) - 0.5) * 1.2;
      return ease(450) * ((fbm(x / 520, z / 520) - 0.45) * 80 + 30) + Math.min(Math.max(0, (r - 2300) / 1300), 1.4) * ridge() * 500 * (sy < 0 ? 1 : 0);
    }
    case 'dunes': {
      const dune = Math.pow(Math.abs(Math.sin(x / 150 + z / 420 + fbm(x / 420, z / 420) * 3.2)), 1.6) * (16 + fbm(x / 600, z / 600) * 22);
      if (d <= 0) return dune * 0.07;
      return dune * (0.07 + 0.93 * ease(350)) + Math.min(Math.max(0, (r - 2500) / 1400), 1.5) * ridge() * 520;
    }
    case 'flat': {
      if (d <= 0) return 0;
      return ease(600) * ((fbm(x / 600, z / 600) - 0.4) * 40 + 10) + Math.min(Math.max(0, (r - 2600) / 1400), 1.6) * ridge() * 620;
    }
    default: {                                       // field
      if (d <= 0) return (fbm(x / 90, z / 90, 2) - 0.5) * 1.2;
      const rolling = (fbm(x / 520, z / 520) - 0.45) * 90, m = Math.max(0, (r - 2100) / 1300);
      return ease(450) * (Math.max(rolling, -8) + 40) + Math.min(m, 1.8) * ridge() * 780;
    }
  }
}

// ================================================================ sky, moon, stars, clouds, lights
const skyU = { top: { value: new THREE.Color() }, bottom: { value: new THREE.Color() }, glowDir: { value: new THREE.Vector3() },
               glowCol: { value: new THREE.Color() }, glowK: { value: 1 } };
const sky = new THREE.Mesh(new THREE.SphereGeometry(16000, 48, 24), new THREE.ShaderMaterial({
  side: THREE.BackSide, depthWrite: false, fog: false, uniforms: skyU,
  vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
  fragmentShader: `uniform vec3 top; uniform vec3 bottom; uniform vec3 glowDir; uniform vec3 glowCol; uniform float glowK; varying vec3 vDir;
    void main(){ float h = clamp(vDir.y*1.5+0.08,0.0,1.0); vec3 c = mix(bottom, top, pow(h,0.8));
      float d = max(dot(normalize(vDir), glowDir),0.0);
      c += glowCol*(pow(d,40.0)*0.8 + pow(d,6.0)*0.22 + pow(d,1.5)*0.05)*glowK;
      gl_FragColor = vec4(c,1.0); }`,
}));
scene.add(sky);

const starGeo = new THREE.BufferGeometry();
{ const p = [], s = []; for (let i = 0; i < 4200; i++) { const th = Math.random() * Math.PI * 2, ph = Math.acos(0.08 + Math.random() * 0.9);
    p.push(Math.sin(ph) * Math.cos(th) * 15000, Math.cos(ph) * 15000, Math.sin(ph) * Math.sin(th) * 15000); s.push(Math.random()); }
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(p, 3)); }
const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xdfe8ff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0.85, depthWrite: false, fog: false }));
scene.add(stars);

function tex(canvasFn, w = 256, h = 256) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; canvasFn(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const moonTex = tex((g, w) => {
  const r = w / 2;
  const gr = g.createRadialGradient(r * 0.85, r * 0.8, r * 0.1, r, r, r * 0.92);
  gr.addColorStop(0, '#fffdf5'); gr.addColorStop(0.75, '#ece9df'); gr.addColorStop(1, '#cfcabd');
  g.fillStyle = gr; g.beginPath(); g.arc(r, r, r * 0.92, 0, 7); g.fill();
  for (let i = 0; i < 22; i++) {                                  // maria and craters
    const a = Math.random() * 7, d = Math.random() * r * 0.7, cr = 6 + Math.random() * 26;
    g.fillStyle = `rgba(150,145,135,${0.12 + Math.random() * 0.2})`; g.beginPath(); g.arc(r + Math.cos(a) * d, r + Math.sin(a) * d, cr, 0, 7); g.fill();
  }
});
const haloTex = tex((g, w) => { const r = w / 2, gr = g.createRadialGradient(r, r, 0, r, r, r);
  gr.addColorStop(0, 'rgba(255,255,255,0.55)'); gr.addColorStop(0.2, 'rgba(210,225,255,0.22)'); gr.addColorStop(1, 'rgba(160,190,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, w, w); });
const sunTex = tex((g, w) => { const r = w / 2, gr = g.createRadialGradient(r, r, 0, r, r, r);
  gr.addColorStop(0, 'rgba(255,255,240,1)'); gr.addColorStop(0.18, 'rgba(255,240,200,0.9)'); gr.addColorStop(1, 'rgba(255,220,160,0)');
  g.fillStyle = gr; g.fillRect(0, 0, w, w); });
const moon = new THREE.Sprite(new THREE.SpriteMaterial({ map: moonTex, depthWrite: false, fog: false, color: 0xffffff }));
const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, depthWrite: false, fog: false, transparent: true, blending: THREE.AdditiveBlending }));
scene.add(moon, halo);

const hemi = new THREE.HemisphereLight(0x8899ff, 0x1a2a18, 0.6); scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 1.0);
sun.castShadow = true; sun.shadow.mapSize.set(HIGH ? 4096 : 2048, HIGH ? 4096 : 2048);
Object.assign(sun.shadow.camera, { left: -1100, right: 1100, top: 1100, bottom: -1100, near: 10, far: 6000 });
sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.6; scene.add(sun); scene.add(sun.target);
scene.fog = new THREE.FogExp2(0x000000, 0.00011);

// clouds: lit puffs, brighter on the side facing the moon
const cloudTex = tex((g, w, h) => {
  for (let i = 0; i < 60; i++) {
    const x = w * 0.12 + Math.random() * w * 0.76, y = h * 0.35 + Math.random() * h * 0.38, r = 14 + Math.random() * 40;
    const gr = g.createRadialGradient(x, y - r * 0.3, 0, x, y, r);
    gr.addColorStop(0, 'rgba(255,255,255,0.5)'); gr.addColorStop(0.6, 'rgba(225,232,245,0.25)'); gr.addColorStop(1, 'rgba(200,210,230,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, h);
  }
}, 512, 192);
const clouds = [];
for (let i = 0; i < 26; i++) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: cloudTex, transparent: true, opacity: 0.6, depthWrite: false, fog: false }));
  const a = Math.random() * Math.PI * 2, r = 2500 + Math.random() * 6500;
  s.position.set(Math.cos(a) * r, 1300 + Math.random() * 1500, Math.sin(a) * r);
  const k = 1 + Math.random() * 1.4; s.scale.set(2600 * k, 900 * k, 1);
  scene.add(s); clouds.push(s);
}

// ================================================================ time of day
const TOD = {
  night: { top: 0x02050f, bottom: 0x15244a, glow: 0x8ea6e6, glowK: 1, dir: [0.42, 0.33, -0.85], fog: 0x0f1a36, fogD: 0.00012,
           hemi: [0x8a9ee0, 0x16241a, 1.05], sun: [0xb7c8ff, 1.25], stars: true, moon: true, exp: 1.4, bloom: 0.6, cloud: [0x7385b4, 0.55], windows: 1 },
  dusk: { top: 0x1c2a5e, bottom: 0xe0946a, glow: 0xffb27a, glowK: 1.0, dir: [0.86, 0.1, -0.5], fog: 0x6a6078, fogD: 0.0001,
          hemi: [0xb9b0de, 0x2a2a22, 0.95], sun: [0xffb98a, 1.7], stars: false, moon: false, exp: 1.15, bloom: 0.45, cloud: [0xf2ab88, 0.7], windows: 0.8 },
  day: { top: 0x2a6ad6, bottom: 0xc2dcf4, glow: 0xffe2b0, glowK: 0.7, dir: [0.45, 0.42, -0.79], fog: 0xb7cde6, fogD: 0.00009,
         hemi: [0xe3edff, 0x4b5a2c, 1.0], sun: [0xffefd2, 2.6], stars: false, moon: false, exp: 1.0, bloom: 0.25, cloud: [0xffffff, 0.75], windows: 0 },
  sunset: { top: 0x2b3c84, bottom: 0xffa24c, glow: 0xff8a3a, glowK: 1.25, dir: [-0.84, 0.09, -0.53], fog: 0xc98d62, fogD: 0.0001,
            hemi: [0xffd4a4, 0x5c4228, 0.9], sun: [0xffa462, 2.1], stars: false, moon: false, exp: 1.1, bloom: 0.42, cloud: [0xffb488, 0.72], windows: 0.4 },
};
let night = true, tod = 'night', baseTod = 'night', fogMul = 1;
const themed = [];                     // materials whose look depends on the time of day
function applyTheme() {
  const P = TOD[tod];
  night = tod === 'night';
  skyU.top.value.set(P.top); skyU.bottom.value.set(P.bottom); skyU.glowCol.value.set(P.glow); skyU.glowK.value = P.glowK;
  skyU.glowDir.value.set(...P.dir).normalize();
  scene.fog.color.set(P.fog); scene.fog.density = P.fogD * fogMul;
  hemi.color.set(P.hemi[0]); hemi.groundColor.set(P.hemi[1]); hemi.intensity = P.hemi[2];
  sun.color.set(P.sun[0]); sun.intensity = P.sun[1];
  stars.visible = P.stars; moon.visible = P.moon;
  renderer.toneMappingExposure = P.exp; bloom.strength = P.bloom;
  clouds.forEach((c) => { c.material.color.set(P.cloud[0]); c.material.opacity = P.cloud[1]; });
  const d = skyU.glowDir.value;
  sun.position.set(d.x * 3000, d.y * 3000, d.z * 3000);
  moon.position.set(d.x * 12000, d.y * 12000, d.z * 12000); moon.scale.set(520, 520, 1);
  halo.position.copy(moon.position);
  if (P.moon) { halo.material.map = haloTex; halo.scale.set(3200, 3200, 1); } else { halo.material.map = sunTex; halo.scale.set(tod === 'day' ? 1100 : 1500, tod === 'day' ? 1100 : 1500, 1); }
  halo.material.opacity = P.moon ? 1 : 0.75; halo.material.needsUpdate = true;
  themed.forEach((f) => f(P));
  $('b-theme').textContent = { night: '☾ Night', dusk: '◐ Dusk', day: '☀ Day', sunset: '◒ Sunset' }[tod];
}

// ================================================================ environments (one per scenario theme)
const ENV = {
  field:         { terrain: 'field', ground: 'meadow', grass: 1.0, trees: 1.0, inside: 0.03, rocks: 1.0, lake: true },
  mountain_pass: { terrain: 'pass',  ground: 'alpine', grass: 0.25, trees: 0.6, inside: 0.0, rocks: 3.0, snow: 300 },
  city:          { terrain: 'flat',  ground: 'urban',  grass: 0.0, trees: 0.25, inside: 0.0, rocks: 0.0, skyline: true, streetlights: true },
  coast:         { terrain: 'coast', ground: 'coast',  grass: 0.55, trees: 0.45, inside: 0.0, rocks: 1.2, sea: true },
  forest:        { terrain: 'field', ground: 'forest', grass: 0.5, trees: 1.5, inside: 1.0, rocks: 1.4, fog: 2.4 },
  airbase:       { terrain: 'flat',  ground: 'meadow', grass: 0.45, trees: 0.4, inside: 0.0, rocks: 0.3, runway: true },
  camp:          { terrain: 'field', ground: 'meadow', grass: 0.8, trees: 0.7, inside: 0.01, rocks: 0.6, tents: true },
  desert:        { terrain: 'dunes', ground: 'sand',   grass: 0.0, trees: 0.0, inside: 0.0, rocks: 1.8 },
};
const grassU = { time: { value: 0 } };
let envGroup = null, envTheme = null, envSpec = null;

function groundColor(kind, x, z, h, c) {
  const n = fbm(x / 120, z / 120, 3), n2 = fbm(x / 35, z / 35, 2), d = playDist(x, z), sy = -z + CY;
  const snowLine = ENV[envTheme]?.snow ?? 520;
  if (h > snowLine + n * 120) return c.setRGB(0.86, 0.89, 0.95);
  if (h > 230) return c.setRGB(0.27 + n * 0.08, 0.29 + n * 0.08, 0.32 + n * 0.08);
  switch (kind) {
    case 'alpine': return d <= 0 ? c.setRGB(0.24 + n * 0.1, 0.33 + n * 0.1, 0.2 + n * 0.05) : c.setRGB(0.3 + n * 0.12, 0.32 + n * 0.1, 0.27 + n * 0.08);
    case 'urban': return d <= 0 ? c.setRGB(0.2 + n2 * 0.05, 0.21 + n2 * 0.05, 0.22 + n2 * 0.05) : c.setRGB(0.17 + n * 0.08, 0.27 + n * 0.1, 0.14 + n * 0.05);
    case 'coast':
      if (sy > SEA_Y - 45) return c.setRGB(0.76 + n2 * 0.08, 0.68 + n2 * 0.08, 0.5 + n2 * 0.06);          // sand
      return c.setRGB(0.2 + n * 0.08, 0.38 + n * 0.12, 0.15 + n * 0.04);
    case 'forest': return c.setRGB(0.12 + n * 0.07 + n2 * 0.03, 0.22 + n * 0.1, 0.09 + n * 0.04);
    case 'sand': return c.setRGB(0.78 + n * 0.1 + n2 * 0.04, 0.6 + n * 0.08 + n2 * 0.03, 0.38 + n * 0.06);
    default: return d <= 0 ? c.setRGB(0.22 + n * 0.08 + n2 * 0.04, 0.4 + n * 0.12 + n2 * 0.05, 0.15 + n * 0.04)
                           : c.setRGB(0.13 + n * 0.08, 0.26 + n * 0.12, 0.1 + n * 0.05);
  }
}

// convoy roads in the free arena (same polylines as sim/network.py ARENA_ROUTES)
const ARENA_ROUTES = {
  V1: [[-150, -250], [150, -285], [450, -235], [760, -265], [980, -200]],
  V2: [[60, 760], [320, 720], [600, 660], [820, 620], [1120, 580], [1170, 380], [1080, 140]],
};
// Chaikin corner cutting, identical to sim/trips.py smooth(): the truck drives exactly along this curve
function smoothRoute(route, iters = 3) {
  let p = route.map((q) => [q[0], q[1]]);
  for (let k = 0; k < iters && p.length >= 3; k++) {
    const q = [p[0]];
    for (let i = 0; i < p.length - 1; i++) {
      const a = p[i], b = p[i + 1];
      q.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]], [0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]);
    }
    q.push(p[p.length - 1]); p = q;
  }
  return p;
}
let routeSpec = null;
function vehicleRoute(id) {
  if (!routeSpec) return ARENA_ROUTES[id] ? smoothRoute(ARENA_ROUTES[id]) : null;
  const n = routeSpec.nodes.find((x) => x.id === id);
  return n && n.route.length > 1 ? (n.mode === 'trip' ? smoothRoute(n.route) : n.route) : null;
}
// a base camp sits behind each end of every truck road: the truck leaves one gate and drives to the other
const CAMP_DEPTH = 110, CAMP_WIDTH = 130, CAMP_SCALE = 1.4;   // drawn 1.4x so it reads next to the (oversized) truck
function campSites(spec) {
  const routes = !spec ? Object.values(ARENA_ROUTES) : spec.nodes.filter((n) => n.kind === 'vehicle' && n.mode === 'trip').map((n) => n.route);
  const out = [], seen = new Set();
  for (const r of routes) {
    const p = smoothRoute(r);
    for (const [end, nxt] of [[p[0], p[1]], [p[p.length - 1], p[p.length - 2]]]) {
      const key = `${Math.round(end[0])},${Math.round(end[1])}`; if (seen.has(key)) continue; seen.add(key);
      const dx = end[0] - nxt[0], dy = end[1] - nxt[1], l = Math.hypot(dx, dy) || 1;
      const off = CAMP_DEPTH * CAMP_SCALE / 2;
      out.push({ gate: end, dir: [dx / l, dy / l], c: [end[0] + dx / l * off, end[1] + dy / l * off] });
    }
  }
  return out;
}
function baseCamp(site) {
  // local frame: +x points away from the road (into the camp), the gate is in the wall at x = -D/2
  const g = new THREE.Group(), D = CAMP_DEPTH, W = CAMP_WIDTH;
  const sand = std(0xb39d72, 0.95, 0.0), sandDark = std(0x8f7c58, 0.95, 0.0), olive = std(0x4f5934, 0.85, 0.1), tent = std(0x5c6640, 0.95, 0.0);
  const steel = M.steel, dark = M.dark;
  const add = (geo, mat, x, y, z, ry = 0) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.rotation.y = ry; m.castShadow = true; m.receiveShadow = true; g.add(m); return m; };
  // gravel yard
  const yard = new THREE.Mesh(new THREE.PlaneGeometry(D, W), std(0x6e6656, 1.0, 0.0)); yard.rotation.x = -Math.PI / 2; yard.position.y = 0.4; yard.receiveShadow = true; g.add(yard);
  // perimeter: Hesco-style sand barriers (instanced), gap for the gate facing the road
  const blocks = [];
  for (let z = -W / 2 + 3; z <= W / 2 - 3; z += 6) { blocks.push([D / 2, z]); if (Math.abs(z) > 16) blocks.push([-D / 2, z]); }
  for (let x = -D / 2 + 3; x <= D / 2 - 3; x += 6) { blocks.push([x, -W / 2]); blocks.push([x, W / 2]); }
  const hesco = new THREE.InstancedMesh(new THREE.BoxGeometry(6, 7, 6), sand, blocks.length);
  const m4 = new THREE.Matrix4();
  blocks.forEach(([x, z], i) => { m4.makeTranslation(x, 3.5, z); hesco.setMatrixAt(i, m4); });
  hesco.castShadow = true; hesco.receiveShadow = true; g.add(hesco);
  for (const z of [-18, 18]) { add(new THREE.BoxGeometry(7, 12, 7), sandDark, -D / 2, 6, z); }            // gate posts
  add(new THREE.BoxGeometry(1, 1, 30), std(0xd23b2e, 0.6, 0.2), -D / 2 - 3, 9, 0);                      // gate barrier arm
  // watchtower in the back corner
  const tx = D / 2 - 14, tz = -W / 2 + 14;
  for (const [a, b] of [[-4, -4], [4, -4], [-4, 4], [4, 4]]) add(new THREE.CylinderGeometry(0.6, 0.6, 26, 6), dark, tx + a, 13, tz + b);
  add(new THREE.BoxGeometry(12, 1.2, 12), olive, tx, 26, tz);
  add(new THREE.BoxGeometry(12, 4, 0.6), sandDark, tx, 28.5, tz - 6); add(new THREE.BoxGeometry(12, 4, 0.6), sandDark, tx, 28.5, tz + 6);
  add(new THREE.BoxGeometry(0.6, 4, 12), sandDark, tx - 6, 28.5, tz); add(new THREE.BoxGeometry(0.6, 4, 12), sandDark, tx + 6, 28.5, tz);
  add(new THREE.ConeGeometry(9.5, 5, 4), olive, tx, 34, tz, Math.PI / 4);
  add(new THREE.SphereGeometry(0.9, 8, 6), glow(0xfff1c0), tx - 6, 31, tz);
  // tents (ridge roofs) and a prefab command hut
  const tentRoof = new THREE.CylinderGeometry(7, 7, 22, 3, 1).rotateZ(Math.PI / 2);
  for (const z of [8, 32]) { add(new THREE.BoxGeometry(22, 4, 12), tent, 26, 2, z); add(tentRoof, tent, 26, 6.4, z).rotation.x = Math.PI; }
  add(new THREE.BoxGeometry(14, 9, 26), std(0x6b7056, 0.8, 0.3), 30, 4.5, -22);
  add(new THREE.BoxGeometry(15, 0.8, 27), dark, 30, 9.3, -22);
  add(new THREE.CylinderGeometry(0.3, 0.3, 10, 6), steel, 32, 14, -30);                                 // radio whip on the hut
  // supply crates and fuel drums near the gate, flagpole
  for (const [x, z, h] of [[-28, 40, 0], [-21, 40, 0], [-28, 47, 0], [-24.5, 43.5, 1]]) add(new THREE.BoxGeometry(6, 5, 6), olive, x, 2.5 + h * 5, z);
  for (const [x, z] of [[-30, -40], [-26, -44], [-30, -48], [-34, -44]]) add(new THREE.CylinderGeometry(1.8, 1.8, 5, 10), std(0x3d4a2e, 0.6, 0.4), x, 2.5, z);
  add(new THREE.CylinderGeometry(0.5, 0.5, 30, 8), steel, -10, 15, -W / 2 + 12);
  const flag = add(new THREE.PlaneGeometry(10, 6), new THREE.MeshStandardMaterial({ color: 0x55603a, side: THREE.DoubleSide, roughness: 0.9 }), -4.6, 27, -W / 2 + 12);
  flag.castShadow = false;
  // place and turn the camp so the gate faces the road
  const c = toV(site.c[0], site.c[1], 0);
  let h = 0; for (const [a, b] of [[0, 0], [-D / 2, -W / 2], [D / 2, W / 2], [-D / 2, W / 2], [D / 2, -W / 2]]) h = Math.max(h, terrainH(c.x + a * CAMP_SCALE, c.z + b * CAMP_SCALE));
  g.scale.setScalar(CAMP_SCALE);
  g.position.set(c.x, Math.max(h, 0), c.z);
  g.rotation.y = Math.atan2(site.dir[1], site.dir[0]);
  return g;
}
function routesOf(spec) {
  // polylines the units travel along (vehicles get roads; trees keep clear of every route)
  if (!spec) return { vehicles: Object.values(ARENA_ROUTES).map((r) => smoothRoute(r)), all: Object.values(ARENA_ROUTES) };
  const vehicles = spec.nodes.filter((n) => n.kind === 'vehicle' && n.route.length > 1).map((n) => (n.mode === 'trip' ? smoothRoute(n.route) : n.route));
  const all = [...spec.nodes, ...(spec.jammers || [])].map((n) => n.route || [n.pos]).filter(Boolean);
  return { vehicles, all };
}
function distToRoutes(sx, sy, routes) {
  let best = Infinity;
  for (const r of routes) {
    if (r.length === 1) { best = Math.min(best, Math.hypot(sx - r[0][0], sy - r[0][1])); continue; }
    for (let i = 0; i < r.length - 1; i++) {
      const [ax, ay] = r[i], [bx, by] = r[i + 1], dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((sx - ax) * dx + (sy - ay) * dy) / L));
      best = Math.min(best, Math.hypot(sx - ax - t * dx, sy - ay - t * dy));
    }
  }
  return best;
}

function disposeGroup(g) {
  g.traverse((o) => {
    o.geometry?.dispose?.();
    const ms = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    ms.forEach((m) => { m.map?.dispose?.(); m.emissiveMap?.dispose?.(); m.dispose?.(); });
  });
  scene.remove(g);
}

const windowTex = () => tex((g, w, h) => { g.fillStyle = '#000'; g.fillRect(0, 0, w, h);
  for (let y = 6; y < h - 4; y += 10) for (let x = 5; x < w - 4; x += 9) if (Math.random() < 0.4) {
    g.fillStyle = `rgba(255,${190 + Math.random() * 50 | 0},130,${0.5 + Math.random() * 0.5})`; g.fillRect(x, y, 5, 5); } }, 64, 128);

function buildEnvironment(spec) {
  const theme = spec?.theme || 'field';
  if (envGroup && envTheme === theme && envSpec === (spec?.id || null)) return;
  if (envGroup) disposeGroup(envGroup);
  themed.length = 0;
  envTheme = theme; envSpec = spec?.id || null; routeSpec = spec || null;
  const E = ENV[theme] || ENV.field;
  TERRAIN = E.terrain; fogMul = E.fog || 1;
  const G = new THREE.Group(); envGroup = G; scene.add(G);
  const { vehicles, all } = routesOf(spec);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), col = new THREE.Color(), up = new THREE.Vector3(0, 1, 0);
  const camPos = spec?.camera ? toV(...spec.camera.pos) : CAMS.Cinematic.pos;

  // ground
  { const size = 13000, seg = HIGH ? 300 : 200;
    const geo = new THREE.PlaneGeometry(size, size, seg, seg); geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position, cols = [], c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) { const x = pos.getX(i), z = pos.getZ(i), h = terrainH(x, z); pos.setY(i, h); groundColor(E.ground, x, z, h, c); cols.push(c.r, c.g, c.b); }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3)); geo.computeVertexNormals();
    const m = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ vertexColors: true })); m.receiveShadow = true; G.add(m); }

  // water
  if (E.lake) { const lake = new THREE.Mesh(new THREE.CircleGeometry(420, 64), new THREE.MeshPhongMaterial({ color: 0x14365a, specular: 0x8899cc, shininess: 120, transparent: true, opacity: 0.94 }));
    lake.rotation.x = -Math.PI / 2; lake.position.set(-1600, 7, -1000); lake.scale.set(1.7, 1, 1); G.add(lake); }
  if (E.sea) {
    const geo = new THREE.PlaneGeometry(14000, 7000, 160, 80); geo.rotateX(-Math.PI / 2);
    const mat = new THREE.MeshPhongMaterial({ color: 0x0f3b5c, specular: 0xbfd6ff, shininess: 140, transparent: true, opacity: 0.95 });
    mat.onBeforeCompile = (sh) => { sh.uniforms.time = grassU.time;
      sh.vertexShader = 'uniform float time;\n' + sh.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>
        transformed.y += sin(position.x * 0.012 + time * 1.3) * 1.6 + sin(position.z * 0.02 + time * 0.9) * 1.1;`); };
    const sea = new THREE.Mesh(geo, mat); sea.position.set(0, 0, CY - SEA_Y - 3500 + 20); G.add(sea);
  }

  // tracks / roads along the vehicles' routes
  const trackMat = (asphalt) => { const t = tex((g, w, h) => {
      g.fillStyle = asphalt ? '#2b2d31' : '#4a4134'; g.fillRect(0, 0, w, h);
      for (let i = 0; i < 2500; i++) { const k = asphalt ? 40 + Math.random() * 30 : 90 + Math.random() * 60; g.fillStyle = `rgba(${k},${asphalt ? k : k * 0.88},${asphalt ? k : k * 0.66},0.5)`; g.fillRect(Math.random() * w, Math.random() * h, 2, 2); }
      if (asphalt) { g.fillStyle = 'rgba(230,220,160,0.85)'; for (let x = 0; x < w; x += 64) g.fillRect(x, h / 2 - 2, 36, 4); }
      else { g.fillStyle = 'rgba(30,26,20,0.5)'; g.fillRect(0, h * 0.22, w, h * 0.12); g.fillRect(0, h * 0.66, w, h * 0.12); } }, 512, 64);
    t.wrapS = THREE.RepeatWrapping; return t; };
  const asphalt = E.ground === 'urban' || E.runway;
  const roadTex = trackMat(asphalt);
  for (const r of vehicles) {                         // one continuous ribbon per road, textured along its length
    const W = asphalt ? 10 : 8, pos = [], uv = [], idx = [];
    let run = 0;
    for (let i = 0; i < r.length; i++) {
      const a = toV(r[Math.max(i - 1, 0)][0], r[Math.max(i - 1, 0)][1], 0), b = toV(r[Math.min(i + 1, r.length - 1)][0], r[Math.min(i + 1, r.length - 1)][1], 0);
      const c = toV(r[i][0], r[i][1], 0);
      if (i > 0) run += c.distanceTo(toV(r[i - 1][0], r[i - 1][1], 0));
      const dx = b.x - a.x, dz = b.z - a.z, l = Math.hypot(dx, dz) || 1, nx = -dz / l * W, nz = dx / l * W;
      for (const sgn of [1, -1]) { const x = c.x + nx * sgn, z = c.z + nz * sgn; pos.push(x, Math.max(terrainH(x, z), 0) + 0.6, z); uv.push(run / 120, sgn > 0 ? 1 : 0); }
      if (i > 0) { const k = i * 2; idx.push(k - 2, k - 1, k, k - 1, k + 1, k); }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.setIndex(idx); geo.computeVertexNormals();
    const m = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ map: roadTex, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }));
    m.receiveShadow = true; G.add(m);
  }

  // base camps at both ends of every truck road; keep trees, rocks and grass out of them
  const camps = campSites(spec);
  const inCamp = (sx, sy) => camps.some(({ c, dir }) => { const x = sx - c[0], y = sy - c[1], a = x * dir[0] + y * dir[1], b = -x * dir[1] + y * dir[0];
    return Math.abs(a) < CAMP_DEPTH * CAMP_SCALE / 2 + 6 && Math.abs(b) < CAMP_WIDTH * CAMP_SCALE / 2 + 6; });
  for (const site of camps) {
    G.add(baseCamp(site));
    const [cx, cy] = site.c, [dx, dy] = site.dir;
    for (const a of [-75, -45, -15, 15, 45, 75]) for (const b of [-90, -54, -18, 18, 54, 90]) all.push([[cx + dx * a - dy * b, cy + dy * a + dx * b]]);
  }

  // pines (3 tiers + trunk, instanced; never more instances than allocated)
  if (E.trees > 0) {
    const MAX = Math.round((HIGH ? 4200 : 2200) * Math.min(E.trees, 1.6));
    const tiers = [[1.0, 1.6, 1.9], [0.78, 1.4, 3.0], [0.52, 1.2, 4.0]].map(([r, h, y]) => { const g = new THREE.ConeGeometry(r, h, 8); g.translate(0, y, 0); return g; });
    const trunk = new THREE.CylinderGeometry(0.12, 0.2, 1.6, 6); trunk.translate(0, 0.8, 0);
    const leafMat = new THREE.MeshLambertMaterial({ color: 0xffffff });
    const meshes = [...tiers.map((g) => new THREE.InstancedMesh(g, leafMat, MAX)), new THREE.InstancedMesh(trunk, new THREE.MeshLambertMaterial({ color: 0x3b2a1c }), MAX)];
    meshes.forEach((m) => { m.castShadow = true; });
    const obstacles = spec?.obstacles || [];
    let n = 0, tries = 0;
    while (n < MAX && tries < MAX * 30) {
      tries++;
      const x = (Math.random() - 0.5) * 7600, z = (Math.random() - 0.5) * 7600, [sx, sy] = fromV(new THREE.Vector3(x, 0, z));
      const d = playDist(x, z), h = terrainH(x, z);
      if (h > (E.snow ?? 300) || h < 0.5 && E.sea) continue;
      if (Math.hypot(x - camPos.x, z - camPos.z) < 600) continue;                       // keep the camera's foreground open
      if (d <= 0) {
        if (Math.random() > E.inside) continue;
        if (distToRoutes(sx, sy, all) < 75) continue;                                    // never on a route or next to a unit
        if (obstacles.some((o) => Math.abs(sx - o.pos[0]) < o.size[0] / 2 + 25 && Math.abs(sy - o.pos[1]) < o.size[1] / 2 + 25)) continue;
      } else {
        if (d < (E.inside >= 1 ? 0 : 220)) continue;                                      // open meadow around the field
        if (fbm(x / 420 + 11, z / 420) < (E.trees > 1 ? 0.33 : 0.43)) continue;           // forests in clumps
      }
      const sc = 9 + Math.random() * 11;
      p.set(x, h - 0.6, z); q.setFromAxisAngle(up, Math.random() * 6.3); s.set(sc, sc * (1 + Math.random() * 0.5), sc);
      m4.compose(p, q, s);
      col.setHSL(0.33 + Math.random() * 0.06, 0.38 + Math.random() * 0.15, (E.ground === 'alpine' ? 0.11 : 0.13) + Math.random() * 0.09);
      meshes.forEach((m, k) => { m.setMatrixAt(n, m4); if (k < 3) m.setColorAt(n, col); });
      n++;
    }
    meshes.forEach((m) => { m.count = n; G.add(m); });
  }

  // rocks
  if (E.rocks > 0) {
    const N = Math.round(420 * E.rocks);
    const rocks = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(1, 1), new THREE.MeshStandardMaterial({ color: E.ground === 'sand' ? 0x9c7c5a : 0x8a8d90, roughness: 0.95 }), N);
    const e = new THREE.Euler();
    let n = 0;
    while (n < N) {
      const x = (Math.random() - 0.5) * 3000, z = (Math.random() - 0.5) * 2600, sc = 2 + Math.pow(Math.random(), 3) * 14, h = terrainH(x, z);
      if (E.sea && h < 0 || h > 70) continue;
      const [sx, sy] = fromV(new THREE.Vector3(x, 0, z)); if ((playDist(x, z) <= 0 && distToRoutes(sx, sy, all) < 40) || inCamp(sx, sy)) continue;
      p.set(x, h + sc * 0.15, z); e.set(Math.random() * 3, Math.random() * 3, Math.random() * 3); q.setFromEuler(e); s.set(sc * 1.4, sc * 0.7, sc);
      m4.compose(p, q, s); rocks.setMatrixAt(n++, m4);
    }
    rocks.castShadow = rocks.receiveShadow = true; G.add(rocks);
  }

  // grass blades swaying in the wind
  if (HIGH && E.grass > 0) {
    const N = Math.round(110000 * E.grass);
    const blade = new THREE.BufferGeometry();
    blade.setAttribute('position', new THREE.Float32BufferAttribute([-0.5, 0, 0, 0.5, 0, 0, -0.32, 0.45, 0, 0.32, 0.45, 0, 0, 1, 0], 3));
    blade.setIndex([0, 1, 2, 1, 3, 2, 2, 3, 4]); blade.computeVertexNormals();
    const mat = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide });
    mat.onBeforeCompile = (sh) => {
      sh.uniforms.time = grassU.time;
      sh.vertexShader = 'uniform float time;\nvarying float vH;\n' + sh.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>
        vH = position.y;
        vec3 ip = vec3(instanceMatrix[3][0], 0.0, instanceMatrix[3][2]);
        float w = sin(time * 1.6 + ip.x * 0.02 + ip.z * 0.015) * 0.5 + sin(time * 3.1 + ip.x * 0.07) * 0.18;
        transformed.x += w * position.y * position.y * 0.55;
        transformed.z += cos(time * 1.2 + ip.z * 0.03) * position.y * position.y * 0.25;`);
      sh.fragmentShader = 'varying float vH;\n' + sh.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>
        diffuseColor.rgb *= mix(0.45, 1.35, vH);`);
    };
    const grass = new THREE.InstancedMesh(blade, mat, N);
    const cx = camPos.x, cz = camPos.z;
    let n = 0, tries = 0;
    while (n < N && tries < N * 4) {
      tries++;
      const near = Math.random() < 0.55;                                     // denser towards the camera
      const x = near ? cx + (Math.random() - 0.2) * 1300 : -1100 + Math.random() * 2200;
      const z = near ? cz - Math.random() * 1100 : -900 + Math.random() * 2100;
      const h = terrainH(x, z); if (h > 60 || h < 0.3 && E.sea) continue;
      if (E.sea && -z + CY > SEA_Y - 60) continue;
      const bh = (E.ground === 'alpine' ? 2 : 3) + Math.random() * 4.5;
      p.set(x, h - 0.2, z); q.setFromAxisAngle(up, Math.random() * 6.3); s.set(0.9 + Math.random() * 0.8, bh, 1);
      m4.compose(p, q, s); grass.setMatrixAt(n, m4);
      col.setHSL(0.25 + Math.random() * 0.07, (E.ground === 'alpine' ? 0.3 : 0.45) + Math.random() * 0.2, 0.3 + Math.random() * 0.16); grass.setColorAt(n, col);
      n++;
    }
    grass.count = n; grass.frustumCulled = false; G.add(grass);
  }

  // city: skyline of distant blocks + street lights along the roads
  if (E.skyline) {
    const N = 700, wt = windowTex(); wt.wrapS = wt.wrapT = THREE.RepeatWrapping;
    const mat = new THREE.MeshStandardMaterial({ color: 0x737a86, roughness: 0.85, emissive: 0xffffff, emissiveMap: wt, emissiveIntensity: 1 });
    themed.push((P) => { mat.emissiveIntensity = P.windows; });
    const blocks = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0), mat, N);
    let n = 0;
    while (n < N) {
      const x = (Math.random() - 0.5) * 3800, z = (Math.random() - 0.5) * 3400, d = playDist(x, z);
      if (d < 60 || d > 1200) continue;
      const w = 30 + Math.random() * 60, h = 18 + Math.pow(Math.random(), 2) * 130 * (1 - d / 1600);
      p.set(x, terrainH(x, z) - 1, z); q.identity(); s.set(w, h, 30 + Math.random() * 60); m4.compose(p, q, s); blocks.setMatrixAt(n++, m4);
    }
    blocks.castShadow = blocks.receiveShadow = true; G.add(blocks);
  }
  const glowPoints = (pts, color, size) => {
    const mesh = new THREE.InstancedMesh(new THREE.SphereGeometry(size, 8, 6), new THREE.MeshBasicMaterial({ color, toneMapped: false }), pts.length);
    pts.forEach((v, i) => { m4.makeTranslation(v.x, v.y, v.z); mesh.setMatrixAt(i, m4); }); G.add(mesh); return mesh;
  };
  if (E.streetlights) {
    const pts = [], poles = [];
    for (const r of vehicles) for (let i = 0; i < r.length - 1; i++) {
      const a = toV(r[i][0], r[i][1]), b = toV(r[i + 1][0], r[i + 1][1]), len = a.distanceTo(b), side = new THREE.Vector3(-(b.z - a.z), 0, b.x - a.x).normalize().multiplyScalar(16);
      for (let k = 0; k <= len; k += 70) { const c = a.clone().lerp(b, k / len).add(side); poles.push(c.clone()); pts.push(c.clone().setY(13)); }
    }
    const pm = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.4, 0.5, 13).translate(0, 6.5, 0), new THREE.MeshStandardMaterial({ color: 0x3a3f46 }), poles.length);
    poles.forEach((v, i) => { m4.makeTranslation(v.x, 0, v.z); pm.setMatrixAt(i, m4); }); G.add(pm);
    const bulbs = glowPoints(pts, 0xffd9a0, 1.6);
    themed.push((P) => { bulbs.visible = P.windows > 0.3; });
  }

  // airbase: runway with centreline and edge lights
  if (E.runway) {
    const a = toV(-180, 300), b = toV(1180, 300), len = a.distanceTo(b);
    const rw = new THREE.Mesh(new THREE.PlaneGeometry(len, 46), new THREE.MeshLambertMaterial({ color: 0x2c2e33 }));
    rw.rotation.x = -Math.PI / 2; rw.position.copy(a).lerp(b, 0.5).setY(0.4); rw.receiveShadow = true; G.add(rw);
    for (let k = 20; k < len - 20; k += 60) { const dsh = new THREE.Mesh(new THREE.PlaneGeometry(30, 1.6), new THREE.MeshBasicMaterial({ color: 0xe8e8e0 }));
      dsh.rotation.x = -Math.PI / 2; dsh.position.set(a.x + k, 0.6, a.z); G.add(dsh); }
    const lights = [];
    for (let k = 0; k <= len; k += 45) for (const off of [-24, 24]) lights.push(new THREE.Vector3(a.x + k, 1.2, a.z + off));
    glowPoints(lights, 0xfff1c4, 1.1);
    glowPoints([0, 6, 12, 18].map((o) => new THREE.Vector3(a.x - 8, 1.2, a.z - 18 + o * 2)), 0x55ff88, 1.3);
    glowPoints([0, 6, 12, 18].map((o) => new THREE.Vector3(b.x + 8, 1.2, b.z - 18 + o * 2)), 0xff4444, 1.3);
  }

  // relief camp: rows of tents around the command post
  if (E.tents) {
    const tents = new THREE.InstancedMesh(new THREE.ConeGeometry(1, 1, 4).translate(0, 0.5, 0), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.9 }), 60);
    let n = 0;
    for (let i = 0; i < 6; i++) for (let k = 0; k < 10; k++) {
      const sx = 80 + k * 32, sy = -170 + i * 34; if (distToRoutes(sx, sy, all) < 25) continue;
      const v = toV(sx, sy); p.set(v.x, 0, v.z); q.setFromAxisAngle(up, Math.PI / 4); s.set(11, 8, 11); m4.compose(p, q, s); tents.setMatrixAt(n, m4);
      tents.setColorAt(n, col.set((i + k) % 3 ? 0xdfe3e8 : 0x5b7fb0)); n++;
    }
    tents.count = n; tents.castShadow = true; G.add(tents);
  }
}

// ================================================================ obstacles (from the server; they block signals in the simulation)
let obstacleGroup = null, obstacleKey = null, beam = null;
function buildObstacles(list, key) {
  if (!list) return;
  if (obstacleGroup && obstacleKey === key) return;
  if (obstacleGroup) disposeGroup(obstacleGroup);
  obstacleGroup = new THREE.Group(); obstacleKey = key; beam = null; scene.add(obstacleGroup);
  const winMats = [];
  for (const o of list) {
    const base = toV(o.pos[0], o.pos[1], 0), H = o.size[2];
    if (o.kind === 'hill') {
      const hill = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 24, 0, Math.PI * 2, 0, Math.PI / 2),
        new THREE.MeshLambertMaterial({ color: envTheme === 'desert' ? 0xa07a52 : envTheme === 'mountain_pass' ? 0x5d6658 : 0x3f7436 }));
      hill.scale.set(o.size[0] / 2, H, o.size[1] / 2); hill.position.copy(base); hill.receiveShadow = hill.castShadow = true;
      obstacleGroup.add(hill);
      obstacleGroup.add(label(`${o.id} · ${envTheme === 'mountain_pass' ? 'ridge' : 'hill'} ${H} m`, '#c9d6f0', 0.014, toV(o.pos[0], o.pos[1], H + 18)));
      continue;
    }
    if (o.id === 'LH') {                                         // lighthouse with a turning beam
      const tower = new THREE.Mesh(new THREE.CylinderGeometry(5, 8, H, 20), new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.6 }));
      tower.position.copy(base).setY(H / 2); tower.castShadow = true; obstacleGroup.add(tower);
      for (const y of [0.3, 0.62]) { const band = new THREE.Mesh(new THREE.CylinderGeometry(6.4, 7, H * 0.12, 20), new THREE.MeshStandardMaterial({ color: 0xc23a32 }));
        band.position.copy(base).setY(H * y); obstacleGroup.add(band); }
      const lamp = new THREE.Mesh(new THREE.SphereGeometry(3.2, 12, 10), new THREE.MeshBasicMaterial({ color: 0xfff3c0, toneMapped: false }));
      lamp.position.copy(base).setY(H + 3); obstacleGroup.add(lamp);
      beam = new THREE.Mesh(new THREE.ConeGeometry(40, 600, 24, 1, true).translate(0, -300, 0).rotateZ(Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0xfff3c0, transparent: true, opacity: 0.08, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }));
      beam.position.copy(lamp.position); obstacleGroup.add(beam);
      obstacleGroup.add(label('lighthouse', '#c9d6f0', 0.014, toV(o.pos[0], o.pos[1], H + 24)));
      continue;
    }
    if (envTheme === 'camp' && /^T\d/.test(o.id)) {             // big relief tents
      const t = new THREE.Mesh(new THREE.CylinderGeometry(0.01, 1, 1, 4, 1).rotateY(Math.PI / 4), new THREE.MeshStandardMaterial({ color: 0xe8ecef, roughness: 0.9 }));
      t.scale.set(o.size[0] * 0.75, H * 1.6, o.size[1] * 0.75); t.position.copy(base).setY(H * 0.8); t.castShadow = true; obstacleGroup.add(t);
      continue;
    }
    const hangar = envTheme === 'airbase' && /^H\d/.test(o.id);
    let body;
    if (hangar) {                                                 // arched hangar
      body = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 24, 1, false, 0, Math.PI).rotateZ(Math.PI / 2).rotateY(Math.PI / 2),
        new THREE.MeshStandardMaterial({ color: 0x6f776b, roughness: 0.7, metalness: 0.3, side: THREE.DoubleSide }));
      body.scale.set(o.size[1] / 2, H, o.size[0]); body.position.copy(base);
    } else {
      const mat = new THREE.MeshStandardMaterial({ color: envTheme === 'desert' ? 0xc9a77c : 0x8d939e, roughness: 0.85, emissive: 0xffffff, emissiveMap: windowTex(), emissiveIntensity: 1 });
      winMats.push(mat);
      body = new THREE.Mesh(new THREE.BoxGeometry(o.size[0], H, o.size[1]), mat); body.position.copy(base).setY(H / 2);
      const roof = new THREE.Mesh(new THREE.BoxGeometry(o.size[0] + 2, 1.5, o.size[1] + 2), new THREE.MeshStandardMaterial({ color: 0x3f454f, roughness: 0.9 }));
      roof.position.copy(base).setY(H + 0.7); obstacleGroup.add(roof);
    }
    body.castShadow = body.receiveShadow = true; obstacleGroup.add(body);
    obstacleGroup.add(label(`${o.id} · ${H} m`, '#c9d6f0', 0.014, toV(o.pos[0], o.pos[1], H + 18)));
  }
  themed.push((P) => winMats.forEach((m) => { m.emissiveIntensity = P.windows; }));
  applyTheme();
}

// ================================================================ labels (constant screen size, outlined text)
function label(text, color = '#ffffff', size = 0.022, pos = null) {
  const c = document.createElement('canvas'), g = c.getContext('2d'); g.font = 'bold 46px Segoe UI, sans-serif';
  const w = Math.ceil(g.measureText(text).width) + 24; c.width = w; c.height = 64;
  g.font = 'bold 46px Segoe UI, sans-serif'; g.textBaseline = 'middle'; g.lineJoin = 'round';
  g.strokeStyle = 'rgba(0,4,14,0.85)'; g.lineWidth = 9; g.strokeText(text, 12, 34);
  g.fillStyle = color; g.fillText(text, 12, 34);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, sizeAttenuation: false, transparent: true, fog: false }));
  s.scale.set(size * w / 64, size, 1); s.renderOrder = 10; if (pos) s.position.copy(pos);
  s.userData.text = text; return s;
}
const TACTIC_TXT = { follow: 'following us', predict: 'predicting our hops', wide: 'spreading over 3 channels', pulse: 'pulsing', spoof: 'spoofing frames' };
function jamText(j) {
  return `${j.id} · ${j.type}${j.tactic ? ' · ' + (TACTIC_TXT[j.tactic] || j.tactic) : ''}${j.owner ? ' · ' + j.owner : ''}`;
}
function relabel(sprite, text, color) {
  if (sprite.userData.text === text) return;
  const fresh = label(text, color, sprite.scale.y);
  sprite.material.map.dispose(); sprite.material.map = fresh.material.map; sprite.scale.copy(fresh.scale); sprite.userData.text = text;
}

// ================================================================ unit models
const std = (color, rough = 0.6, metal = 0.3, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal, ...extra });
const glow = (color) => new THREE.MeshBasicMaterial({ color, toneMapped: false });
const M = {
  body: std(0x2a2f36, 0.45, 0.55), arm: std(0x1a1d22, 0.5, 0.5), motor: std(0x6b7280, 0.35, 0.85),
  prop: new THREE.MeshStandardMaterial({ color: 0xcfd8e6, roughness: 0.4, transparent: true, opacity: 0.28, depthWrite: false }),
  blade: std(0xd7dde6, 0.5, 0.1), skid: std(0x15171b, 0.6, 0.4), lens: std(0x0a0a0a, 0.15, 0.9),
  olive: std(0x55603a, 0.8, 0.15), oliveDark: std(0x434b2e, 0.8, 0.15), tyre: std(0x101010, 0.9, 0.0),
  case: std(0x2e3a2c, 0.8, 0.2), steel: std(0x9aa3ad, 0.35, 0.85), dark: std(0x22262c, 0.7, 0.3), dish: std(0xdde2e8, 0.45, 0.3, { side: THREE.DoubleSide }),
};
// heavy-lift hexacopter, cinema-rig style: carbon centre plates, armoured canopy, six raised arms,
// 3-axis gimbal camera, tall T landing legs, GPS mast. +x is forward.
const CARBON = tex((g, w, h) => {
  g.fillStyle = '#1b1e23'; g.fillRect(0, 0, w, h);
  for (let y = 0; y < h; y += 8) for (let x = 0; x < w; x += 8) { g.fillStyle = (x + y) % 16 ? '#23272e' : '#16181c'; g.fillRect(x, y, 8, 4); }
}, 64, 64);
CARBON.wrapS = CARBON.wrapT = THREE.RepeatWrapping;
const DRONE_GEO = {
  plate: new THREE.CylinderGeometry(0.5, 0.5, 0.05, 8),
  arm: new THREE.CylinderGeometry(0.04, 0.05, 1.0, 8).rotateZ(Math.PI / 2).translate(0.5, 0, 0),
  motor: new THREE.CylinderGeometry(0.12, 0.13, 0.17, 14), bell: new THREE.CylinderGeometry(0.09, 0.12, 0.06, 14),
  disc: new THREE.CylinderGeometry(0.5, 0.5, 0.008, 28), blade: new THREE.BoxGeometry(0.98, 0.02, 0.08),
  led: new THREE.SphereGeometry(0.05, 8, 6),
};
function droneModel(relay, hostile = false) {
  const g = new THREE.Group();
  const shell = hostile ? std(0x7a1c24, 0.4, 0.45) : relay ? std(0x4f3f92, 0.4, 0.45) : std(0x3c4350, 0.42, 0.5);
  const carbon = std(0xffffff, 0.55, 0.35, { map: CARBON });
  const add = (geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); g.add(m); return m; };
  // centre: two carbon plates with stand-offs, canopy shell, battery on top
  add(DRONE_GEO.plate, carbon, 0, -0.06, 0); add(DRONE_GEO.plate, carbon, 0, 0.12, 0);
  for (let k = 0; k < 4; k++) { const a = k * Math.PI / 2 + Math.PI / 4; add(new THREE.CylinderGeometry(0.02, 0.02, 0.18, 6), M.steel, Math.cos(a) * 0.3, 0.03, Math.sin(a) * 0.3); }
  const canopy = add(new THREE.SphereGeometry(0.42, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), shell, 0.04, 0.14, 0);
  canopy.scale.set(1.25, 0.55, 0.85);
  add(new THREE.BoxGeometry(0.42, 0.13, 0.22), M.dark, -0.12, 0.33, 0);
  add(new THREE.BoxGeometry(0.44, 0.02, 0.24), glow(hostile ? 0xff5566 : relay ? 0xb59cff : 0x3ad1ff), -0.12, 0.27, 0).scale.set(1, 1, 1.02);
  // GPS puck on a short mast
  add(new THREE.CylinderGeometry(0.012, 0.012, 0.3, 6), M.steel, -0.3, 0.42, 0);
  add(new THREE.CylinderGeometry(0.09, 0.09, 0.035, 14), shell, -0.3, 0.58, 0);
  // six arms with a little dihedral, motors, props, nav lights
  const rotors = [];
  for (let k = 0; k < 6; k++) {
    const a = Math.PI / 6 + k * Math.PI / 3, cx = Math.cos(a), cz = Math.sin(a);
    const arm = add(DRONE_GEO.arm, carbon, cx * 0.32, 0.03, cz * 0.32);
    arm.rotation.set(0, -a, 0.12);
    const mx = cx * 1.3, mz = cz * 1.3, my = 0.15;
    add(DRONE_GEO.motor, M.motor, mx, my, mz); add(DRONE_GEO.bell, M.dark, mx, my + 0.11, mz);
    add(DRONE_GEO.disc, M.prop, mx, my + 0.16, mz);
    const blade = add(DRONE_GEO.blade, M.blade, mx, my + 0.16, mz); blade.rotation.y = k; rotors.push(blade);
    add(DRONE_GEO.led, glow(cx > 0.3 ? (cz > 0 ? 0x33ff66 : 0xff3344) : 0xffffff), mx, my - 0.12, mz);
  }
  // 3-axis gimbal + camera, forward under the body
  add(new THREE.CylinderGeometry(0.05, 0.05, 0.14, 10), M.dark, 0.28, -0.15, 0);
  const yoke = add(new THREE.TorusGeometry(0.17, 0.025, 6, 16, Math.PI), M.dark, 0.28, -0.24, 0); yoke.rotation.set(Math.PI, Math.PI / 2, 0);
  add(new THREE.BoxGeometry(0.26, 0.2, 0.22), M.dark, 0.3, -0.36, 0);
  const lens = add(new THREE.CylinderGeometry(0.08, 0.09, 0.14, 16), M.lens, 0.48, -0.36, 0); lens.rotation.z = Math.PI / 2;
  add(new THREE.CylinderGeometry(0.06, 0.06, 0.01, 16), std(0x2a5ea8, 0.05, 1.0), 0.555, -0.36, 0).rotation.z = Math.PI / 2;
  // tall T landing legs, splayed outward
  for (const z of [-1, 1]) {
    const leg = add(new THREE.CylinderGeometry(0.025, 0.03, 0.62, 8), carbon, 0, -0.34, z * 0.36); leg.rotation.x = -z * 0.35;
    const foot = add(new THREE.CylinderGeometry(0.03, 0.03, 0.9, 8), M.skid, 0, -0.63, z * 0.47); foot.rotation.z = Math.PI / 2;
  }
  const status = add(new THREE.SphereGeometry(0.08, 10, 8), glow(COL.ok), -0.52, 0.12, 0);
  const strobe = add(new THREE.SphereGeometry(0.06, 8, 6), glow(hostile ? 0xff2233 : relay ? 0xb59cff : 0xffffff), 0.04, 0.38, 0);
  g.traverse((o) => { if (o.isMesh && o.material !== M.prop) o.castShadow = true; });
  g.scale.setScalar(UNIT_SCALE * 0.95);
  g.userData = { rotors, led: status, strobe };
  return g;
}
// 6x6 tactical comms truck (long-nose cab, camo canvas cargo cover, mast + dish). +x is forward.
const CAMO = tex((g, w, h) => {
  g.fillStyle = '#5a6340'; g.fillRect(0, 0, w, h);
  let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (const c of ['#454d30', '#6f7350', '#2f3424', '#7c7552']) for (let i = 0; i < 22; i++) {
    g.fillStyle = c; g.beginPath(); const x = rnd() * w, y = rnd() * h, r = 8 + rnd() * 22;
    for (let k = 0; k < 9; k++) { const a = k / 9 * Math.PI * 2, rr = r * (0.6 + rnd() * 0.6); g.lineTo(x + Math.cos(a) * rr * 1.4, y + Math.sin(a) * rr); }
    g.closePath(); g.fill(); }
}, 256, 256);
CAMO.wrapS = CAMO.wrapT = THREE.RepeatWrapping;
function vehicleModel() {
  const g = new THREE.Group();
  const paint = std(0x4f5934, 0.75, 0.2), paintDark = std(0x3c4428, 0.8, 0.2);
  const canvasMat = std(0xffffff, 0.95, 0.0, { map: CAMO }), glass = std(0x1c2c3c, 0.08, 0.9);
  const add = (geo, mat, x, y, z, rx = 0, ry = 0, rz = 0) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.rotation.set(rx, ry, rz); g.add(m); return m; };
  const B = (sx, sy, sz) => new THREE.BoxGeometry(sx, sy, sz);
  // chassis rails, bumper, fuel tank, exhaust
  for (const z of [-0.36, 0.36]) add(B(3.2, 0.16, 0.1), M.dark, -0.05, 0.62, z);
  add(B(0.14, 0.16, 1.5), M.dark, 1.62, 0.7, 0);
  add(new THREE.CylinderGeometry(0.15, 0.15, 0.5, 14), paintDark, 0.32, 0.56, 0.62, 0, 0, Math.PI / 2);
  add(new THREE.CylinderGeometry(0.035, 0.04, 0.75, 8), M.dark, 0.42, 1.55, -0.7);
  // cab: extruded side profile with a long bonnet and raked windscreen
  const prof = new THREE.Shape([[0.42, 0.72], [1.58, 0.72], [1.58, 1.12], [1.16, 1.2], [1.0, 1.78], [0.42, 1.8]].map(([x, y]) => new THREE.Vector2(x, y)));
  add(new THREE.ExtrudeGeometry(prof, { depth: 1.3, bevelEnabled: true, bevelSize: 0.03, bevelThickness: 0.03, bevelSegments: 1 }), paint, 0, 0, -0.65);
  const ang = Math.atan2(0.16, 0.58);
  add(B(0.03, 0.5, 1.12), glass, 1.1, 1.49, 0, 0, 0, ang);
  for (const z of [-0.69, 0.69]) { add(B(0.34, 0.3, 0.02), glass, 0.74, 1.5, z); add(B(0.03, 0.18, 0.1), M.dark, 1.02, 1.5, z * 1.13); }
  add(B(0.03, 0.3, 0.9), M.dark, 1.6, 0.94, 0);
  for (let k = 0; k < 4; k++) add(B(0.035, 0.03, 0.86), M.steel, 1.62, 0.84 + k * 0.07, 0);
  add(new THREE.CylinderGeometry(0.17, 0.17, 0.05, 16), paintDark, 0.72, 1.85, 0);
  // bonnet-side fenders over the front axle
  for (const z of [-0.62, 0.62]) add(B(0.9, 0.06, 0.36), paintDark, 1.1, 0.83, z);
  // cargo bed: dropsides + camo canvas tilt on ribs
  add(B(2.05, 0.1, 1.42), paintDark, -0.62, 0.8, 0);
  for (const z of [-0.69, 0.69]) add(B(2.05, 0.26, 0.04), paint, -0.62, 0.97, z);
  add(B(0.04, 0.26, 1.42), paint, -1.63, 0.97, 0);
  const walls = add(B(1.98, 0.5, 1.36), canvasMat, -0.62, 1.32, 0);
  walls.geometry.attributes.uv.array.forEach((v, i, arr) => { arr[i] = v * 2.2; });
  const roof = add(new THREE.CylinderGeometry(0.68, 0.68, 1.98, 20, 1, false, 0, Math.PI), canvasMat, -0.62, 1.57, 0, 0, 0, Math.PI / 2);
  roof.scale.set(0.42, 1, 1);                         // flatten the arch (local x is world up after the turn)
  for (let k = 0; k < 4; k++) { const rib = add(new THREE.TorusGeometry(0.69, 0.018, 4, 14, Math.PI), M.dark, -1.5 + k * 0.58, 1.57, 0, 0, Math.PI / 2, 0); rib.scale.set(1, 0.42, 1); }
  // six wheels: tyre + hub + cap
  const tyreG = new THREE.CylinderGeometry(0.36, 0.36, 0.3, 20), hubG = new THREE.CylinderGeometry(0.2, 0.2, 0.31, 12), capG = new THREE.CylinderGeometry(0.08, 0.08, 0.33, 8);
  for (const x of [1.1, -0.42, -1.12]) for (const z of [-0.62, 0.62]) {
    add(tyreG, M.tyre, x, 0.36, z, Math.PI / 2); add(hubG, paintDark, x, 0.36, z, Math.PI / 2); add(capG, M.steel, x, 0.36, z, Math.PI / 2);
  }
  for (const z of [-0.62, 0.62]) add(B(0.03, 0.26, 0.3), M.dark, -1.58, 0.48, z);   // mud flaps
  // lights
  for (const z of [-0.5, 0.5]) { add(new THREE.CylinderGeometry(0.07, 0.07, 0.04, 12), glow(0xfff1c0), 1.6, 1.0, z, 0, 0, Math.PI / 2); add(B(0.03, 0.06, 0.12), glow(0xff3030), -1.66, 0.84, z); }
  // comms: telescopic mast with status LED, whip on the cab, small dish on the tilt
  add(new THREE.CylinderGeometry(0.035, 0.05, 2.0, 8), M.steel, -1.42, 2.4, 0.5);
  const led = add(new THREE.SphereGeometry(0.1, 10, 8), glow(COL.ok), -1.42, 3.45, 0.5);
  add(new THREE.CylinderGeometry(0.012, 0.012, 1.1, 6), M.steel, 0.55, 2.35, 0.55);
  add(new THREE.CylinderGeometry(0.05, 0.08, 0.18, 10), M.dark, -0.35, 1.9, -0.2);
  add(new THREE.SphereGeometry(0.32, 20, 8, 0, Math.PI * 2, 0, Math.PI / 2.6), M.dish, -0.35, 2.15, -0.2, 0, 0, -0.9);
  g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  g.scale.setScalar(UNIT_SCALE * 0.9);
  g.userData = { led };
  return g;
}
function commandPostModel() {
  const g = new THREE.Group();
  for (let i = 0; i < 3; i++) {                                   // tripod
    const a = i * 2.094, leg = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 2.2), M.dark);
    leg.position.set(Math.cos(a) * 0.45, 1.0, Math.sin(a) * 0.45); leg.lookAt(0, 2.2, 0); leg.rotateX(Math.PI / 2); g.add(leg);
  }
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.06, 2.6), M.steel); mast.position.y = 3.0; g.add(mast);
  const dish = new THREE.Mesh(new THREE.SphereGeometry(0.75, 24, 10, 0, Math.PI * 2, 0, Math.PI / 2.6), M.dish);
  dish.position.set(0.2, 2.3, 0); dish.rotation.set(0, 0, -1.1); g.add(dish);
  const feed = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.7), M.steel); feed.position.set(0.55, 2.45, 0); feed.rotation.z = -1.1; g.add(feed);
  const panel = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.35, 0.12), M.dark); panel.position.set(-0.3, 1.6, 0); g.add(panel);
  const led = new THREE.Mesh(new THREE.SphereGeometry(0.09, 10, 8), glow(0x45d6ff)); led.position.set(0, 4.35, 0); g.add(led);
  for (const [x, z, s] of [[1.2, 0.5, 1], [1.5, -0.6, 0.8], [-0.9, 0.9, 0.9]]) {   // equipment cases
    const c = new THREE.Mesh(new THREE.BoxGeometry(1.0 * s, 0.55 * s, 0.6 * s), M.case); c.position.set(x, 0.28 * s, z); g.add(c);
    const lid = new THREE.Mesh(new THREE.BoxGeometry(1.02 * s, 0.06, 0.62 * s), M.dark); lid.position.set(x, 0.56 * s, z); g.add(lid);
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.06, 0.02), glow(0x3b82ff)); l.position.set(x + 0.2 * s, 0.35 * s, z + 0.31 * s); g.add(l);
  }
  g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  g.scale.setScalar(UNIT_SCALE * 1.05);
  g.userData = { led, blink: true };
  return g;
}

// jammer: tripod emitter + dome (fresnel energy shield with scrolling bands) + ground glow
const domeVS = 'varying vec3 vN; varying vec3 vV; varying vec2 vUv; void main(){ vUv = uv; vec4 mv = modelViewMatrix*vec4(position,1.0); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }';
const domeFS = `uniform vec3 col; uniform float t; uniform float k; varying vec3 vN; varying vec3 vV; varying vec2 vUv;
  void main(){ float f = pow(1.0-abs(dot(vN,vV)), 2.6); float band = smoothstep(0.9,1.0, fract(vUv.y*16.0 - t*0.5));
    float pulse = 0.8 + 0.2*sin(t*2.6); float rim = smoothstep(0.0, 0.06, vUv.y) ;
    float a = (0.05 + f*0.62*pulse + band*0.05) * k * rim; gl_FragColor = vec4(col*(0.55+1.4*f), a); }`;
const HEAT = tex((g, w) => { const r = w / 2, gr = g.createRadialGradient(r, r, 0, r, r, r);
  gr.addColorStop(0, 'rgba(255,70,70,0.5)'); gr.addColorStop(0.55, 'rgba(255,80,50,0.16)'); gr.addColorStop(1, 'rgba(255,60,40,0)');
  g.fillStyle = gr; g.fillRect(0, 0, w, w); }, 128, 128);
const RIM = tex((g, w) => { const r = w / 2, gr = g.createRadialGradient(r, r, r * 0.86, r, r, r);
  gr.addColorStop(0, 'rgba(255,60,80,0)'); gr.addColorStop(0.6, 'rgba(255,60,80,0.9)'); gr.addColorStop(1, 'rgba(255,60,80,0)');
  g.fillStyle = gr; g.fillRect(0, 0, w, w); }, 256, 256);
function jammerReach(j) { return Math.max(60, 60 + (j.power_dbm - 18) * 9.5); }
function jammerModel(j) {
  const g = new THREE.Group();
  const friendly = j.type === 'accidental';
  const color = friendly ? 0xffa64d : 0xff3d55;
  const unit = new THREE.Group();
  for (let i = 0; i < 3; i++) { const a = i * 2.094, leg = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 1.4), M.dark);
    leg.position.set(Math.cos(a) * 0.35, 0.62, Math.sin(a) * 0.35); leg.lookAt(0, 1.3, 0); leg.rotateX(Math.PI / 2); unit.add(leg); }
  const box = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.5, 0.55), M.dark); box.position.y = 1.45; unit.add(box);
  for (const x of [-0.25, 0, 0.25]) { const ant = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 1.0), M.steel); ant.position.set(x, 2.15, 0); unit.add(ant); }
  const tip = new THREE.Mesh(new THREE.SphereGeometry(0.1, 10, 8), glow(color)); tip.position.set(0, 2.7, 0); unit.add(tip);
  const front = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.08, 0.02), glow(color)); front.position.set(0, 1.5, 0.28); unit.add(front);
  unit.scale.setScalar(UNIT_SCALE * 1.2); unit.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  let platform = unit;
  if (j.airborne) {                                   // jammer carried by a hostile drone
    platform = droneModel(false, true);
    const pod = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.25, 0.35), M.dark); pod.position.set(0, -0.45, 0); platform.add(pod);
    const ant = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.7), M.steel); ant.position.set(0, -0.85, 0); platform.add(ant);
    const tipj = new THREE.Mesh(new THREE.SphereGeometry(0.07, 8, 6), glow(color)); tipj.position.set(0, -1.2, 0); platform.add(tipj);
    platform.position.y = j.pos[2];
  } else if (envTheme === 'coast' && j.pos[1] > SEA_Y) {   // ship offshore
    const ship = new THREE.Group();
    const hull = new THREE.Mesh(new THREE.BoxGeometry(80, 10, 20), std(0x3b4149, 0.7, 0.4)); hull.position.y = 3; ship.add(hull);
    const bow = new THREE.Mesh(new THREE.ConeGeometry(10, 22, 4).rotateZ(-Math.PI / 2).rotateX(Math.PI / 4), std(0x3b4149, 0.7, 0.4)); bow.position.set(50, 3, 0); ship.add(bow);
    const deck = new THREE.Mesh(new THREE.BoxGeometry(26, 12, 14), std(0xd8dce2, 0.6, 0.2)); deck.position.set(-14, 14, 0); ship.add(deck);
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.6, 22), M.steel); mast.position.set(-14, 31, 0); ship.add(mast);
    ship.traverse((o) => { if (o.isMesh) o.castShadow = true; });
    g.add(ship); unit.position.set(10, 8, 0);
  } else if (j.pos[2] > 3) {
    unit.position.y = j.pos[2] - 4;                   // rooftop or ridge crest
  }
  g.add(platform === unit ? unit : platform);
  const R = jammerReach(j);
  const u = { col: { value: new THREE.Color(color) }, t: { value: 0 }, k: { value: friendly ? 0.55 : 1 } };
  const dome = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 32, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.ShaderMaterial({ uniforms: u, vertexShader: domeVS, fragmentShader: domeFS, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
  dome.scale.setScalar(R); g.add(dome);
  const heat = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: HEAT, transparent: true, depthWrite: false, color: friendly ? 0xffb070 : 0xffffff }));
  heat.rotation.x = -Math.PI / 2; heat.position.y = 1.5; heat.scale.setScalar(R * 2.1); g.add(heat);
  const rim = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: RIM, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: friendly ? 0xffb070 : 0xffffff, toneMapped: false }));
  rim.rotation.x = -Math.PI / 2; rim.position.y = 1.8; rim.scale.setScalar(R * 2.08); g.add(rim);
  const tag = label(jamText(j), friendly ? '#ffc58a' : '#ff9aa6', 0.019);
  tag.position.y = (j.airborne ? j.pos[2] : Math.max(0, j.pos[2] - 4)) + 130; g.add(tag);
  g.userData = { uniforms: u, dome, heat, rim, R, platform, tag, friendly };
  return g;
}

// ================================================================ per-world dynamic state
class WorldView {
  constructor(id) {
    this.id = id;
    this.group = new THREE.Group(); scene.add(this.group);
    this.nodes = new Map(); this.links = []; this.jammers = new Map(); this.packets = []; this.estimates = [];
    this.state = null; this.spectrum = null;
    this.linkGeo = new THREE.CylinderGeometry(1, 1, 1, 6, 1, true); this.linkGeo.translate(0, 0.5, 0); this.linkGeo.rotateX(Math.PI / 2);
  }
  dispose() { scene.remove(this.group); }
  // vehicle on a trip: glow the stretch of road still ahead of it
  trip(n, v) {
    const t = n.trip, moving = t.state === 'driving' || t.state === 'returning';
    if (!v.route) v.route = vehicleRoute(n.id);
    if (!v.route) return;
    if (!v.ahead) {
      v.ahead = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ color: 0x5ff0ff, transparent: true, opacity: 0.7, depthWrite: false, side: THREE.DoubleSide, toneMapped: false,
        polygonOffset: true, polygonOffsetFactor: -8, polygonOffsetUnits: -8 }));
      v.ahead.position.y = 2.5; v.ahead.renderOrder = 3;
      this.group.add(v.ahead);
    }
    v.ahead.visible = moving;
    if (!moving) return;
    const fwd = t.to === t.dst, R = v.route, [px, py] = n.pos;
    let best = 0, bd = Infinity;
    for (let i = 0; i < R.length; i++) { const d = (R[i][0] - px) ** 2 + (R[i][1] - py) ** 2; if (d < bd) { bd = d; best = i; } }
    const rest = fwd ? R.slice(best + 1) : R.slice(0, best).reverse();
    const P = [[px, py], ...rest].map(([x, y]) => toV(x, y, 0)), pos = [], idx = [], W = 3.2;   // glowing strip on the road
    for (let i = 0; i < P.length; i++) {
      const a = P[Math.max(i - 1, 0)], b = P[Math.min(i + 1, P.length - 1)], dx = b.x - a.x, dz = b.z - a.z, l = Math.hypot(dx, dz) || 1;
      const y = Math.max(terrainH(P[i].x, P[i].z), 0) + 1.2;
      pos.push(P[i].x - dz / l * W, y, P[i].z + dx / l * W, P[i].x + dz / l * W, y, P[i].z - dx / l * W);
      if (i > 0) { const k = i * 2; idx.push(k - 2, k - 1, k, k - 1, k + 1, k); }
    }
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); geo.setIndex(idx);
    v.ahead.geometry.dispose(); v.ahead.geometry = geo;
  }
  apply(s) {
    this.state = s;
    const seen = new Set();
    for (const n of s.nodes) {
      seen.add(n.id);
      let v = this.nodes.get(n.id);
      if (!v) {
        const model = n.kind === 'drone' ? droneModel(false) : n.kind === 'relay' ? droneModel(true) : n.kind === 'vehicle' ? vehicleModel() : commandPostModel();
        const tag = label(n.kind === 'command_post' ? 'CMD' : n.id, n.kind === 'relay' ? '#d2c4ff' : '#ffffff', 0.026);
        const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
          new THREE.LineBasicMaterial({ color: 0xa9c0e8, transparent: true, opacity: 0.28 }));
        model.position.copy(toV(...n.pos));
        this.group.add(model, tag, drop);
        v = { model, tag, drop, kind: n.kind };
        this.nodes.set(n.id, v);
      }
      v.target = toV(...n.pos);
      v.heading = n.heading_deg || 0;
      if (n.battery_pct != null) {
        const b = Math.round(n.battery_pct), extra = n.status === 'returning' ? ' · ↩ to base' : n.status === 'charging' ? ' · ⚡ swap' : n.status === 'rejoining' ? ' · re-sync' : '';
        relabel(v.tag, `${n.id} · ${b}%${extra}`, n.status === 'returning' || n.status === 'charging' ? '#9cc4ff' : b < 35 ? '#ffd27a' : '#ffffff');
      } else if (n.status === 'rejoining' || v.tag.userData.text?.includes('re-sync')) {
        const base = n.kind === 'command_post' ? 'CMD' : n.id;
        relabel(v.tag, n.status === 'rejoining' ? `${base} · re-sync` : base, n.kind === 'relay' ? '#d2c4ff' : '#ffffff');
      }
      if (v.model.userData.led && n.kind !== 'command_post') v.model.userData.led.material.color.setHex(COL[n.status] ?? COL.ok);
      if (n.trip) this.trip(n, v);
    }
    for (const [id, v] of this.nodes) if (!seen.has(id)) { this.group.remove(v.model, v.tag, v.drop); if (v.ahead) this.group.remove(v.ahead); this.nodes.delete(id); }

    const L = s.links;
    while (this.links.length < L.length) {
      const m = new THREE.Mesh(this.linkGeo, new THREE.MeshBasicMaterial({ color: COL.ok, transparent: true, opacity: 0.9, depthWrite: false, toneMapped: false }));
      this.group.add(m); this.links.push(m);
    }
    this.links.forEach((m, i) => {
      const l = L[i]; m.visible = !!l; if (!l) return;
      m.userData = { a: l.a, b: l.b, routed: l.routed, delivery: l.delivery_pct };
      m.material.color.setHex(COL[l.status]).multiplyScalar(l.routed ? 1.25 : 0.8);
      m.material.opacity = l.routed ? 0.95 : 0.16;
    });

    const js = new Set();
    for (const j of s.jammers) {
      js.add(j.id);
      if (!this.jammers.has(j.id)) { const g = jammerModel(j); g.position.copy(toV(j.pos[0], j.pos[1], 0)); g.userData.born = performance.now(); this.group.add(g); this.jammers.set(j.id, g); }
      const jg = this.jammers.get(j.id);
      jg.userData.active = j.active; jg.userData.target = toV(j.pos[0], j.pos[1], 0);
      if (j.tactic && jg.userData.tag) relabel(jg.userData.tag, jamText(j), '#ff9aa6');
      if (j.airborne) { jg.userData.alt = j.pos[2]; jg.userData.heading = Math.atan2(-(jg.userData.target.z - jg.position.z), jg.userData.target.x - jg.position.x); }
    }
    for (const [id, g] of this.jammers) if (!js.has(id)) { this.group.remove(g); this.jammers.delete(id); }

    const ests = s.estimates || [];
    while (this.estimates.length < ests.length) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.965, 1, 128), new THREE.MeshBasicMaterial({ color: 0xffd54a, transparent: true, opacity: 0.95, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }));
      ring.rotation.x = -Math.PI / 2;
      const dot = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, 2, 20), glow(0xffd54a));
      const tag = label('estimate', '#ffe27a', 0.016);
      const g = new THREE.Group(); g.add(ring, dot, tag); this.group.add(g); this.estimates.push({ g, ring, tag });
    }
    this.estimates.forEach((e, i) => {
      const est = ests[i]; e.g.visible = !!est; if (!est) return;
      e.target = toV(est.pos[0], est.pos[1], 3); if (!e.g.userData.placed) { e.g.position.copy(e.target); e.g.userData.placed = true; }
      e.radius = est.radius_m;
      relabel(e.tag, `AI estimate ±${Math.round(est.radius_m)} m`, '#ffe27a');
    });
  }
  frame(dt, t) {
    for (const v of this.nodes.values()) {
      v.model.position.lerp(v.target, Math.min(1, dt * 8));
      if (v.kind !== 'command_post') v.model.rotation.y = THREE.MathUtils.degToRad(90 - v.heading);
      const p = v.model.position;
      v.tag.position.set(p.x, p.y + (v.kind === 'command_post' ? 140 : v.kind === 'vehicle' ? 85 : 42), p.z);
      const arr = v.drop.geometry.attributes.position; arr.setXYZ(0, p.x, p.y, p.z); arr.setXYZ(1, p.x, 0.5, p.z); arr.needsUpdate = true;
      v.drop.visible = p.y > 15;
      if (v.model.userData.rotors) v.model.userData.rotors.forEach((r, i) => { r.rotation.y += dt * (i % 2 ? 45 : -45); });
      if (v.model.userData.strobe) v.model.userData.strobe.visible = (t % 1.1) < 0.08;
      if (v.model.userData.blink) v.model.userData.led.visible = (t % 1.4) < 0.7;
    }
    const pos = (id) => this.nodes.get(id)?.model.position;
    for (const m of this.links) {
      if (!m.visible) continue;
      const a = pos(m.userData.a), b = pos(m.userData.b); if (!a || !b) { m.visible = false; continue; }
      const r = m.userData.routed ? 1.5 : 0.5;
      m.position.copy(a); m.scale.set(r, r, a.distanceTo(b)); m.lookAt(b);
    }
    for (const g of this.jammers.values()) {
      const u = g.userData; u.uniforms.t.value = t;
      if (u.target) g.position.lerp(u.target, Math.min(1, dt * 6));
      if (u.alt != null && u.platform) { u.platform.position.y += (u.alt - u.platform.position.y) * Math.min(1, dt * 4);
        u.platform.userData.rotors?.forEach((r, i) => { r.rotation.y += dt * (i % 2 ? 45 : -45); });
        if (u.platform.userData.strobe) u.platform.userData.strobe.visible = (t % 0.6) < 0.12; }
      const age = Math.min(1, (performance.now() - u.born) / 900);
      const k = u.R * (0.15 + 0.85 * age);
      u.dome.scale.setScalar(k); u.rim.scale.setScalar(k * 2.08);
      u.heat.material.opacity = (u.active ? 1 : 0.35) * age; u.rim.material.opacity = (0.55 + 0.25 * Math.sin(t * 2.6)) * age;
    }
    for (const e of this.estimates) {
      if (!e.g.visible || !e.target) continue;
      e.g.position.lerp(e.target, Math.min(1, dt * 3));
      const cur = e.ring.scale.x || 1, r = cur + ((e.radius || 100) - cur) * Math.min(1, dt * 3);
      e.ring.scale.set(r, r, 1); e.tag.position.set(0, 14, r * 0.92);
      e.ring.material.opacity = 0.7 + 0.25 * Math.sin(t * 4);
    }
    this.flowPackets(dt);
  }
  flowPackets(dt) {
    const routed = this.links.filter((m) => m.visible && m.userData.routed);
    const want = Math.min(60, routed.length * 3);
    while (this.packets.length < want) {
      const p = new THREE.Mesh(new THREE.SphereGeometry(3.4, 10, 8), glow(0xbaf0ff));
      p.userData = { u: Math.random() }; this.group.add(p); this.packets.push(p);
    }
    const st = this.state; if (!st) return;
    const parentOf = Object.fromEntries(st.nodes.map((n) => [n.id, n.parent]));
    this.packets.forEach((p, i) => {
      const m = routed[i % Math.max(routed.length, 1)];
      if (!m || i >= want) { p.visible = false; return; }
      const { a, b, delivery } = m.userData;
      const child = parentOf[a] === b ? a : b, parent = child === a ? b : a;
      const A = this.nodes.get(child)?.model.position, B = this.nodes.get(parent)?.model.position;
      if (!A || !B) { p.visible = false; return; }
      p.userData.u += dt * 0.55;
      if (p.userData.u > 1) { p.userData.u -= 1; p.userData.lost = Math.random() * 100 > (delivery ?? 100); }
      p.visible = !(p.userData.lost && p.userData.u > 0.5);
      p.position.copy(A).lerp(B, p.userData.u);
      p.material.color.setHex(p.userData.lost ? 0xff4f66 : 0xbaf0ff);
    });
  }
}

// ================================================================ app state + panels
const worlds = new Map();
let view = 'single', hello = null, ws = null, aiOn = true;
const worldIds = () => (view === 'side_by_side' ? ['ai_off', 'ai_on'] : ['main']);
function ensureWorld(id) { if (!worlds.has(id)) worlds.set(id, new WorldView(id)); return worlds.get(id); }
window.__aegis = { camera: () => camera, controls: () => controls, worlds: () => worlds };   // console hook for close-up inspection
let scnShow = 'ai_on', scnManual = false;                   // scenario: which defence is on screen
const singleScn = () => !!scnId && view === 'side_by_side';  // both worlds run; one is shown full-screen
const shownIds = () => (singleScn() ? [scnShow] : worldIds());
const primary = () => worlds.get(singleScn() ? scnShow : view === 'side_by_side' ? 'ai_on' : 'main');
function showDefence(id, manual = false) {
  if (manual) scnManual = true;
  if (scnShow === id && !manual) return;
  scnShow = id;
  $('m-off').classList.toggle('active', id === 'ai_off'); $('m-on').classList.toggle('active', id === 'ai_on');
  const sp = worlds.get(id)?.spectrum; if (sp) { drawSpectrum(sp); drawCnn(sp); }
  lastPanel = 0;
}

function toast(text, level = 'info') {
  const d = document.createElement('div'); d.className = 'toast ' + level; d.textContent = text; $('toasts').prepend(d);
  setTimeout(() => d.remove(), 4600);
  while ($('toasts').childElementCount > 3) $('toasts').lastChild.remove();
}
function addDecision(text, level) {
  const list = $('dec-list'), d = document.createElement('div');
  d.innerHTML = `<span class="t">${new Date().toLocaleTimeString([], { hour12: false })}</span><span class="${level}"></span>`;
  d.lastChild.textContent = text; list.prepend(d);
  while (list.childElementCount > 5) list.lastChild.remove();
}

let lastPanel = 0;
function updatePanels(now) {
  if (now - lastPanel < 120) return;                  // DOM updates ~8x per second are plenty
  lastPanel = now;
  const w = primary(); if (!w?.state) return;
  const s = w.state, k = s.kpis;
  $('k-delivery').textContent = k.delivery_pct.toFixed(k.delivery_pct >= 99.95 ? 0 : 1);
  $('k-links').textContent = `${k.healthy_links}/${k.total_links}`;
  $('k-decision').textContent = k.decision_time_ms == null ? '—' : k.decision_time_ms.toFixed(2);
  $('k-hops').textContent = k.hops;
  $('k-delivery').parentElement.style.color = k.delivery_pct >= 90 ? '' : k.delivery_pct >= 60 ? 'var(--deg)' : 'var(--down)';
  $('k-sub').textContent = `hop latency ${k.hop_latency_ms == null ? '—' : k.hop_latency_ms.toFixed(0) + ' ms'} · outage ${(k.outage_ms / 1000).toFixed(1)} s · ${s.hop_mode} · ${s.nodes[0]?.modulation || ''}` +
    (k.resync_ms != null ? ` · re-sync ${k.resync_ms.toFixed(0)} ms` : '') + (k.clock_error_us != null ? ` · clocks ±${k.clock_error_us.toFixed(0)} µs (master ${k.time_master})` : '') + (k.qos?.command?.on_time_pct != null ? ` · orders on time ${k.qos.command.on_time_pct.toFixed(0)}%` : '') + (k.safety_fixes || k.safety_undos ? ` · guard ${k.safety_fixes} fixed / ${k.safety_undos} undone` : '');
  $('sec-ver').textContent = k.frames_verified.toLocaleString(); $('sec-rej').textContent = k.frames_rejected; $('sec-forged').textContent = k.frames_forged;
  $('sec-fa').textContent = k.frames_forged_accepted ?? 0; $('sec-sync').textContent = k.auth_syncs ?? 0; $('sec-epoch').textContent = k.key_epoch ?? 0;
  aiOn = s.ai_enabled;
  $('pill-ai').textContent = view === 'side_by_side' ? 'AI OFF vs ON' : aiOn ? 'AI ON' : 'AI OFF';
  $('pill-ai').className = 'pill ' + (view === 'side_by_side' || aiOn ? '' : 'off');
  const bAi = $('b-ai'); bAi.textContent = aiOn ? '⏻ AI on' : '⏻ AI off'; bAi.classList.toggle('off', !aiOn);
  $('b-split').classList.toggle('active', view === 'side_by_side');
  // status line
  const st = $('status'), hostile = s.jammers.filter((j) => j.type !== 'accidental');
  const verdict = w.spectrum?.verdict;
  let cls = '', text = 'Network stable';
  if (hostile.length) {
    text = k.delivery_pct >= 80 ? `Under attack · defended (${k.delivery_pct.toFixed(0)}%)` : `Under attack · ${k.delivery_pct.toFixed(0)}% delivered`;
    cls = k.delivery_pct >= 80 ? 'warn' : 'bad';
    if (verdict && verdict !== 'clean') text += ` · ${verdict}`;
  } else if (k.delivery_pct < 90) { text = 'Links degraded'; cls = 'warn'; }
  st.className = 'status ' + cls; st.lastChild.textContent = text;
  if (singleScn()) {
    const pct = (x) => x?.kpis.delivery_attack_pct ?? x?.kpis.delivery_pct, col = (v) => (v >= 90 ? 'var(--ok)' : v >= 60 ? 'var(--deg)' : 'var(--down)');
    const cur = worlds.get(scnShow)?.state, off = worlds.get('ai_off')?.state, on = worlds.get('ai_on')?.state;
    if (cur) {
      const k2 = cur.kpis, v = pct(cur), atk = k2.delivery_attack_pct != null;
      $('m-strip').innerHTML = `${atk ? 'under attack' : 'delivered'} <b style="color:${col(v)}">${v.toFixed(1)}%</b>${atk ? ' delivered' : ''} · outage ${(k2.outage_ms / 1000).toFixed(1)} s · hops ${k2.hops}` +
        (k2.hop_latency_ms != null ? ` · hop ${k2.hop_latency_ms.toFixed(0)} ms` : '') + ` · ${cur.hop_mode}${modTxt(cur.nodes[0])}`;
    }
    if (off && on && off.kpis.delivery_attack_pct != null) $('m-compare').textContent = `same attack, running together · AI OFF ${pct(off).toFixed(1)}% vs AI ON ${pct(on).toFixed(1)}%`;
    else $('m-compare').textContent = 'same seed, same attack · both defences running together';
  }
  if (view === 'side_by_side' && !singleScn()) for (const id of ['ai_off', 'ai_on']) {
    const x = worlds.get(id)?.state; if (!x) continue;
    const kk = x.kpis, c = kk.delivery_pct >= 90 ? 'var(--ok)' : kk.delivery_pct >= 60 ? 'var(--deg)' : 'var(--down)';
    if (scnId && kk.delivery_attack_pct != null) {
      const v = kk.delivery_attack_pct, cc = v >= 90 ? 'var(--ok)' : v >= 60 ? 'var(--deg)' : 'var(--down)';
      $('strip-' + id).innerHTML = `under attack <b style="color:${cc}">${v.toFixed(1)}%</b> delivered · outage ${(kk.outage_ms / 1000).toFixed(1)} s · hops ${kk.hops}`;
      continue;
    }
    $('strip-' + id).innerHTML = `delivered <b style="color:${c}">${kk.delivery_pct.toFixed(1)}%</b> · outage ${(kk.outage_ms / 1000).toFixed(1)} s · hops ${kk.hops}` +
      (kk.hop_latency_ms != null ? ` · hop ${kk.hop_latency_ms.toFixed(0)} ms` : '') + ` · ${x.hop_mode}${modTxt(x.nodes[0])}${x.nodes[0] && x.nodes[0].tx_power_dbm > 27 ? ' · +' + (x.nodes[0].tx_power_dbm - 27) + ' dB' : ''}`;
  }
}

// colour maps
function inferno(v) {
  const s = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]];
  const x = Math.max(0, Math.min(0.999, v)) * (s.length - 1), i = Math.floor(x), f = x - i, a = s[i], b = s[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
const css = (c) => `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;

// spectrum: a proper plot with axes, plus a thin waterfall
const F0 = 2405, F1 = 2485, Y0 = -120, Y1 = -40;
function drawSpectrum(m) {
  const c = $('spec'), g = c.getContext('2d'), W = c.width, H = c.height;
  const L = 50, R = 10, T = 10, B = 36, pw = W - L - R, ph = H - T - B;
  const X = (f) => L + (f - F0) / (F1 - F0) * pw, Y = (p) => T + (1 - (Math.max(Y0, Math.min(Y1, p)) - Y0) / (Y1 - Y0)) * ph;
  g.clearRect(0, 0, W, H);
  g.fillStyle = 'rgba(4,10,26,0.55)'; g.fillRect(L, T, pw, ph);
  g.strokeStyle = 'rgba(150,180,230,0.13)'; g.lineWidth = 1; g.font = '12px Segoe UI'; g.fillStyle = '#c7d3ee';
  for (let p = Y0; p <= Y1; p += 10) { g.beginPath(); g.moveTo(L, Y(p)); g.lineTo(L + pw, Y(p)); g.stroke(); g.fillText(String(p), L - 34, Y(p) + 4); }
  for (let f = 2410; f <= 2480; f += 10) { g.beginPath(); g.moveTo(X(f), T); g.lineTo(X(f), T + ph); g.stroke(); g.fillText(String(f), X(f) - 14, T + ph + 16); }
  g.fillText('Frequency (MHz)', L + pw / 2 - 46, H - 3);
  g.save(); g.translate(13, T + ph / 2 + 52); g.rotate(-Math.PI / 2); g.fillText('Power (dBm / bin)', 0, 0); g.restore();
  const cur = primary()?.state?.nodes?.[0]?.channel;
  if (cur != null) { g.fillStyle = 'rgba(201,209,222,0.10)'; g.fillRect(X(2405 + cur * 10), T, X(2415) - X(2405), ph); g.fillStyle = '#ffffff'; g.fillText('us', X(2410 + cur * 10) - 6, T + 13); }
  const n = m.bins.length, fx = (i) => X(F0 + (i + 0.5) / n * (F1 - F0));
  const grad = g.createLinearGradient(0, T, 0, T + ph); grad.addColorStop(0, 'rgba(52,227,170,0.55)'); grad.addColorStop(1, 'rgba(52,227,170,0.02)');
  g.beginPath(); g.moveTo(fx(0), T + ph); m.bins.forEach((p, i) => g.lineTo(fx(i), Y(p))); g.lineTo(fx(n - 1), T + ph); g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); m.bins.forEach((p, i) => (i ? g.lineTo(fx(i), Y(p)) : g.moveTo(fx(i), Y(p)))); g.strokeStyle = '#3ff0b8'; g.lineWidth = 1.6;
  g.shadowColor = 'rgba(63,240,184,0.6)'; g.shadowBlur = 6; g.stroke(); g.shadowBlur = 0;
  g.strokeStyle = 'rgba(200,215,245,0.5)'; g.strokeRect(L, T, pw, ph);
  // waterfall (newest on top)
  const wf = $('waterfall'), wg = wf.getContext('2d');
  wg.drawImage(wf, 0, 0, wf.width, wf.height - 1, 0, 1, wf.width, wf.height - 1);
  const bw = pw / n;
  m.bins.forEach((p, i) => { wg.fillStyle = css(inferno((p + 112) / 55)); wg.fillRect(L + i * bw, 0, bw + 0.6, 1); });
  wg.clearRect(0, 0, L, wf.height); wg.clearRect(L + pw, 0, R, wf.height);
}

// Grad-CAM: 12 x 32 values, drawn smooth (bilinear upscale + soft blur), inferno colours
const camSmall = document.createElement('canvas'); camSmall.width = 32; camSmall.height = 12;
const modTxt = (n) => (n?.modulation === 'BPSK_R2' ? ' · SURVIVAL mode' : n?.modulation === 'BPSK' ? ' · BPSK' : '');
function drawCnn(m) {
  const bars = $('cnn-bars'), v = $('cnn-verdict'), c = $('gradcam'), g = c.getContext('2d');
  if (!m.probs) {
    bars.innerHTML = ''; v.textContent = ''; $('cnn-hint').style.display = '';
    g.fillStyle = '#12082e'; g.fillRect(0, 0, c.width, c.height); return;
  }
  $('cnn-hint').style.display = 'none';
  bars.innerHTML = Object.entries(m.probs).map(([n, p]) =>
    `<div class="r"><span class="n">${n}</span><span class="b ${n === m.verdict ? (n === 'clean' ? 'clean' : 'top') : ''}" style="width:${Math.max(2, p * 60)}px"></span><span>${(p * 100).toFixed(0)}%</span></div>`).join('');
  const bh = $('cnn-behav'), B = m.behaviours;
  if (B) {
    const ALL = ['wideband', 'tracking', 'narrowband', 'intermittent', 'unknown'];
    const ref = m.referee ? ` <span class="ref">referee: ${m.referee.context} → ${m.referee.leader === 'rl' ? 'RL policy' : 'AEGIS'}</span>` : '';
    bh.innerHTML = ALL.map((n) => `<span class="chip ${B.detected.includes(n) ? 'on' : ''}" title="${(B.evidence && B.evidence[n]) || ''}">${n}</span>`).join('') + ref;
  } else bh.innerHTML = '';
  if (m.verdict === 'unknown') { v.textContent = `unknown pattern · ${(m.novelty ?? 0).toFixed(1)}× outside`; v.className = 'tag unknown'; }
  else { v.textContent = `${m.verdict} ${(m.confidence * 100).toFixed(0)}%`; v.className = 'tag' + (m.verdict === 'clean' ? ' clean' : ''); }
  const sg = camSmall.getContext('2d'), img = sg.createImageData(32, 12);
  for (let r = 0; r < 12; r++) for (let k = 0; k < 32; k++) {
    const val = m.gradcam ? m.gradcam[r][k] : 0.06 + 0.04 * Math.sin(r + k * 0.7), col = inferno(0.05 + val * 0.95), o = (r * 32 + k) * 4;
    img.data[o] = col[0]; img.data[o + 1] = col[1]; img.data[o + 2] = col[2]; img.data[o + 3] = 255;
  }
  sg.putImageData(img, 0, 0);
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high'; g.filter = 'blur(5px)';
  g.drawImage(camSmall, -8, -8, c.width + 16, c.height + 16); g.filter = 'none';
}

// ================================================================ benchmark panel
const DEF_LABEL = { none: 'No defence', fixed_hopping: 'Fixed hopping', energy: 'Energy detector', fhss_keyed: 'Secret fast hopping', max_power: 'Brute force (max power)', afh_power: 'Rules, no CNN', rl: 'RL agent', aegis: 'ABHEDYA AI', hybrid: 'ABHEDYA + RL' };
// validated categorical order on the dark surface (colour-blind and normal-vision separation checks pass)
const DEF_COL = { none: '#8d99ae', fixed_hopping: '#3987e5', energy: '#8d99ae', fhss_keyed: '#8d99ae', max_power: '#d95926', afh_power: '#199e70', rl: '#c98500', aegis: '#9085e9', hybrid: '#c9d1de' };
const BENCH_KEY = ['fixed_hopping', 'max_power', 'afh_power', 'aegis'];   // shown when a run has more defences than fit
let latestBench = null;
function showBenchmark(m) {
  $('bench').classList.remove('hidden');
  if (m.status === 'running') {
    $('bench-progress').classList.remove('hidden'); $('bench-result').classList.add('hidden');
    $('bench-bar').style.width = `${(m.progress * 100).toFixed(0)}%`;
    $('bench-now').textContent = `${(m.progress * 100).toFixed(0)}%${m.current ? ' · ' + m.current : ''}`;
    $('bench-sub').textContent = m.seeds ? `${m.seeds} seed${m.seeds > 1 ? 's' : ''} · identical runs per defender` : '';
    return;
  }
  if (m.status === 'failed') { $('bench-now').textContent = 'Benchmark failed: ' + (m.error || ''); return; }
  $('bench-progress').classList.add('hidden'); $('bench-result').classList.remove('hidden');
  $('bench-sub').textContent = `${m.seeds} seed${m.seeds > 1 ? 's' : ''} × ${m.duration_s}s runs · finished in ${m.elapsed_s}s`;
  let defs = m.overall.map((o) => o.defender);
  if (defs.length > 4 && BENCH_KEY.every((d) => defs.includes(d))) defs = BENCH_KEY;
  $('bench-overall').innerHTML = m.overall.filter((o) => defs.includes(o.defender)).map((o) => `<div class="o ${o.defender}"><span>${DEF_LABEL[o.defender] || o.defender}</span>
    <b style="color:${DEF_COL[o.defender]}">${o.delivery_pct.toFixed(1)}%</b>
    <span>worst ${o.worst_jammer_pct.toFixed(0)}% · outage ${(o.outage_ms / 1000).toFixed(1)} s${o.hop_latency_median_ms != null ? ` · hop ${o.hop_latency_median_ms.toFixed(0)}/${o.hop_latency_p95_ms.toFixed(0)} ms` : ''}${o.accuracy_pct != null ? ` · CNN ${o.accuracy_pct}%` : ''}</span></div>`).join('');
  const c = $('bench-chart'), g = c.getContext('2d'); g.clearRect(0, 0, c.width, c.height);
  const jams = [...new Set(m.rows.map((r) => r.jammer))], L = 38, B = 200, H = 170, gw = (c.width - L - 10) / jams.length, bw = (gw - 14) / defs.length;
  g.font = '11px Segoe UI'; g.fillStyle = '#6c80a6'; g.strokeStyle = 'rgba(120,160,255,.15)';
  for (const val of [0, 25, 50, 75, 100]) { const y = B - H * val / 100; g.beginPath(); g.moveTo(L, y); g.lineTo(c.width - 6, y); g.stroke(); g.fillText(val + '%', 4, y + 4); }
  g.strokeStyle = 'rgba(255,79,102,.6)'; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(L, B - H * 0.8); g.lineTo(c.width - 6, B - H * 0.8); g.stroke(); g.setLineDash([]);
  g.fillStyle = '#ff8f9c'; g.fillText('80% target (deck)', L + 4, B - H * 0.8 - 4);
  jams.forEach((j, i) => {
    defs.forEach((d, k) => {
      const r = m.rows.find((x) => x.jammer === j && x.defender === d); if (!r) return;
      const h = H * r.delivery_pct / 100, x = L + i * gw + 7 + k * bw;
      g.fillStyle = DEF_COL[d]; g.fillRect(x, B - h, bw - 2, h);
    });
    g.fillStyle = '#c8d6f5'; g.fillText(j, L + i * gw + gw / 2 - g.measureText(j).width / 2, B + 16);
  });
  defs.forEach((d, k) => { g.fillStyle = DEF_COL[d]; g.fillRect(L + k * 160, 6, 10, 10); g.fillStyle = '#c8d6f5'; g.fillText(DEF_LABEL[d] || d, L + 14 + k * 160, 15); });
  const head = `<tr><th>Jammer</th>${defs.map((d) => `<th>${DEF_LABEL[d]}</th>`).join('')}<th>AEGIS hop ms (med/p95)</th><th>CNN acc.</th></tr>`;
  const body = jams.map((j) => {
    const rs = defs.map((d) => m.rows.find((x) => x.jammer === j && x.defender === d));
    const best = Math.max(...rs.map((r) => r?.delivery_pct ?? 0)), a = m.rows.find((x) => x.jammer === j && x.defender === 'aegis');
    return `<tr><td>${j}</td>${rs.map((r) => `<td class="${r && r.delivery_pct === best ? 'best' : ''}">${r ? r.delivery_pct.toFixed(1) + '%' : '—'}</td>`).join('')}
      <td>${a?.hop_latency_median_ms != null ? a.hop_latency_median_ms.toFixed(0) + ' / ' + a.hop_latency_p95_ms.toFixed(0) : '—'}</td><td>${a?.accuracy_pct != null ? a.accuracy_pct + '%' : '—'}</td></tr>`;
  }).join('');
  $('bench-table').innerHTML = `<table>${head}${body}</table>`;
  $('bench-files').innerHTML = 'Delivery = packets reaching the command post after the jammer starts. Saved for the slides: ' +
    (m.files || []).map((f) => { const n = f.split(/[\\/]/).pop(); return `<a href="/results/${n}" target="_blank">${n}</a>`; }).join(' · ');
}
$('bench-close').onclick = () => $('bench').classList.add('hidden');
async function loadLatestBenchmark() {
  try {
    const r = await fetch('/api/benchmark/latest'); if (!r.ok) return;
    const d = await r.json();
    latestBench = { status: 'done', progress: 1, seeds: d.seeds, duration_s: d.duration_s, rows: d.rows, overall: d.overall, files: d.files, elapsed_s: d.elapsed_s };
    $('b-results').classList.remove('hidden');
  } catch (e) { /* no results yet */ }
}


// ================================================================ guided scenarios (gallery, briefing, captions, timeline, report)
let briefIntro = false;
// deep link from the mission deck (/intro/): /viewer/?start=<scenario id | arena> loads it and starts it
let deepStart = new URLSearchParams(location.search).get('start'), autoPlay = null;
let SCN = [], scnId = null, scnSpec = null, scnPaused = false, scnSpeed = 1, lastReport = null;
const JAM_EMOJI = { barrage: '▦', random: '⁂', sweep: '〰', reactive: '⚡', deceptive: '🎭', accidental: '📡', follower: '🎯' };
const THEME_NAME = { mountain_pass: 'Mountain pass', city: 'City', coast: 'Coast', forest: 'Forest', airbase: 'Airbase', camp: 'Relief camp', desert: 'Desert' };
async function loadScenarioCatalog() {
  try { const r = await fetch('/api/scenarios'); const d = await r.json(); SCN = (d.scenarios || d).sort((a, b) => a.order - b.order); renderGallery(); }
  catch (e) { /* older server: gallery stays empty */ }
}
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function renderGallery() {
  $('gallery-grid').innerHTML = SCN.map((sc, i) => `<div class="scard" data-id="${sc.id}">
      <span class="env">${THEME_NAME[sc.theme] || sc.theme} · ${sc.time_of_day}</span><span class="num">${i + 1} / ${SCN.length}</span>
      <b>${esc(sc.title)}</b><div class="sub">${esc(sc.subtitle)}</div>
      <span class="chip ${sc.jammer === 'accidental' ? 'accidental' : ''}">${JAM_EMOJI[sc.jammer] || ''} ${sc.jammer}</span>
      <p>${esc(sc.story).slice(0, 150)}${sc.story.length > 150 ? '…' : ''}</p></div>`).join('') +
    `<div class="scard arena" data-id=""><span class="num">free play</span><b>Live arena</b><div class="sub">Open field, launch any jammer by clicking the ground</div>
      <p>Toggle AI, compare side by side, run the benchmark and security tests.</p></div>`;
  document.querySelectorAll('.scard').forEach((c) => (c.onclick = () => { $('gallery').classList.add('hidden'); startScenario(c.dataset.id || null); }));
}
let wantScn;      // the mission this screen asked for (undefined until it asks): a result for another one is stale
function startScenario(id) {
  wantScn = id || '';
  $('report').classList.add('hidden');
  if (id) send({ action: 'load_scenario', name: id }); else if (scnId) send({ action: 'exit_scenario' });
}
function showBrief(sc, intro = false) {
  if (!sc) return;
  $('brief-chip').textContent = `${JAM_EMOJI[sc.jammer] || ''} ${sc.jammer} jammer`;
  $('brief-chip').className = 'chip ' + (sc.jammer === 'accidental' ? 'accidental' : '');
  $('brief-env').textContent = `${THEME_NAME[sc.theme] || sc.theme} · ${sc.time_of_day} · ${sc.duration_s} s`;
  $('brief-start').textContent = intro ? '▶ Start scenario' : scnPaused ? '▶ Resume' : 'Close';
  $('brief-title').textContent = sc.title;
  $('brief-story').textContent = sc.story;
  $('brief-watch').innerHTML = (sc.watch || []).map((w) => `<li>${esc(w)}</li>`).join('');
  $('brief-ai').textContent = sc.ai_response || '';
  $('brief-exp').textContent = sc.expected ? 'Expected: ' + sc.expected : '';
  $('brief').classList.remove('hidden');
}
function setScenarioUi(sc) {
  scnSpec = sc;
  document.body.classList.toggle('scenario', !!sc);
  document.body.classList.toggle('single-view', !!sc);
  $('modebar').classList.toggle('hidden', !sc);
  scnManual = false; showDefence('ai_on'); scnShow = 'ai_on';
  $('hud').classList.toggle('hidden', !sc);
  if (!sc) { $('brief').classList.add('hidden'); document.body.classList.remove('clean', 'signals'); return; }
  $('hud-chip').textContent = `${JAM_EMOJI[sc.jammer] || ''} ${sc.jammer}`;
  $('hud-chip').className = 'chip ' + (sc.jammer === 'accidental' ? 'accidental' : '');
  $('hud-title').textContent = sc.title;
  $('hud-step').textContent = ''; $('hud-text').textContent = sc.subtitle || '';
  $('hud-ticks').innerHTML = (sc.steps || []).map((st) => `<span style="left:${(st.t / sc.duration_s * 100).toFixed(2)}%" title="${esc(st.text)}"></span>`).join('');
  scnSpeed = 1; $('h-speed').textContent = '1×';
  document.body.classList.remove('clean', 'signals'); $('h-clean').classList.remove('active'); $('h-signals').classList.remove('active');
  scnPaused = true; $('h-pause').textContent = '▶';           // the server loads scenarios paused on the briefing
  briefIntro = true; showBrief(sc, true);
  if (autoPlay === sc.id) { autoPlay = null; setTimeout(() => { if (briefIntro) $('brief-start').click(); }, 1600); }
}
function onHello(m) {
  const id = m.scenario || null, sc = id ? SCN.find((x) => x.id === id) || null : null;
  const themeKey = `${m.theme}|${id}|${m.mission}`;
  if (onHello.key !== themeKey) {
    onHello.key = themeKey;
    for (const w of worlds.values()) w.dispose(); worlds.clear();     // fresh units for the new environment
    buildEnvironment(sc ? { ...sc, theme: m.theme } : null);
    buildObstacles(m.obstacles, themeKey);
    baseTod = tod = m.time_of_day || 'night';
    applyTheme();
    if (id !== scnId) {
      CAMS.Cinematic = sc?.camera ? { pos: toV(...sc.camera.pos), target: toV(...sc.camera.target) } : { ...ARENA_CAM };
      setCam('Cinematic');
      $('dec-list').innerHTML = '';
    }
  }
  if (id !== scnId || (id && !scnSpec)) setScenarioUi(sc);
  scnId = id;
}
function onScenarioState(m) {
  if (!scnSpec || m.scenario_t == null) return;
  const T = m.scenario_duration || scnSpec.duration_s, t = Math.min(m.scenario_t, T);
  $('hud-fill').style.width = `${(t / T * 100).toFixed(1)}%`;
  $('hud-time').textContent = `${t.toFixed(0)} / ${T.toFixed(0)} s${m.paused ? ' · paused' : m.speed > 1 ? ' · ' + m.speed + '×' : ''}`;
  if (m.paused !== undefined && m.paused !== scnPaused) { scnPaused = m.paused; $('h-pause').textContent = scnPaused ? '▶' : '⏸'; }
  if (!m.paused && briefIntro) { briefIntro = false; $('brief').classList.add('hidden'); }      // started from any screen or phone
  if (m.focus && m.focus !== scnShow) showDefence(m.focus);
}
function onCaption(m) {
  if (m.focus) showDefence(m.focus);
  $('hud-step').textContent = `STEP ${m.step + 1}/${m.steps}`;
  const el = $('hud-text'); el.textContent = scnSpec?.steps?.[m.step]?.text || m.text; el.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 450 });
}
function showReport(m) {
  // the server resends the last result to screens that (re)connect; if this screen has just asked for a different
  // mission, that result is stale and must not cover the new one (found by scripts/rehearse.py)
  if (wantScn !== undefined && wantScn !== m.scenario) return;
  lastReport = m;
  const on = m.ai_on || {}, off = m.ai_off || {}, pct = (k) => (k?.delivery_attack_pct ?? k?.delivery_pct);
  $('rep-title').textContent = `${m.title} · result`;
  $('rep-off').textContent = pct(off) != null ? pct(off).toFixed(1) + '%' : '—';
  $('rep-on').textContent = pct(on) != null ? pct(on).toFixed(1) + '%' : '—';
  const row = (name, f) => `<tr><td>${name}</td><td>${f(off)}</td><td>${f(on)}</td></tr>`;
  const num = (v, d = 0, u = '') => (v == null ? '—' : (+v).toFixed(d) + u);
  $('rep-table').innerHTML = `<tr><th></th><th>AI OFF</th><th>AI ON</th></tr>` +
    row('Delivered over the whole run', (k) => num(k.delivery_pct, 1, '%')) +
    row('Total outage', (k) => num(k.outage_ms != null ? k.outage_ms / 1000 : null, 1, ' s')) +
    row('Channel changes', (k) => num(k.hops)) +
    row('Hop latency (detect → switch)', (k) => num(k.hop_latency_ms, 0, ' ms')) +
    row('Forged frames accepted', (k) => num(k.frames_forged_accepted ?? 0)) +
    row('Healthy links at the end', (k) => k.total_links ? `${k.healthy_links}/${k.total_links}` : '—');
  const ds = m.decisions || [];
  $('rep-decisions').innerHTML = ds.length ? ds.slice(0, 6).map((d) => `<li><span class="muted">${(+d.t).toFixed(1)} s · ${esc(d.class)}</span> — ${esc(d.reason)}</li>`).join('')
    : '<li class="muted">No action needed — the AI judged this signal harmless and stayed put.</li>';
  $('rep-exp').textContent = (m.classes_seen?.length ? `CNN identified: ${m.classes_seen.join(', ')}. ` : '') + (m.expected ? 'Expected: ' + m.expected : '');
  const i = SCN.findIndex((x) => x.id === m.scenario);
  $('rep-next').textContent = i >= 0 && i < SCN.length - 1 ? `Next: ${SCN[i + 1].title} ▶` : 'Back to the gallery ▶';
  $('report').classList.remove('hidden'); $('brief').classList.add('hidden');
}
$('b-deck').onclick = () => { location.href = `/intro/?slide=${encodeURIComponent(scnId || 'arena')}`; };
$('b-scn').onclick = () => { $('gallery').classList.toggle('hidden'); $('report').classList.add('hidden'); };
$('gallery-close').onclick = () => $('gallery').classList.add('hidden');
const togglePause = () => send({ action: scnPaused ? 'play' : 'pause' });
$('brief-close').onclick = () => $('brief').classList.add('hidden');
$('brief-start').onclick = () => { briefIntro = false; $('brief').classList.add('hidden'); send({ action: 'play' }); };
$('h-brief').onclick = () => { if (!$('brief').classList.contains('hidden')) return $('brief').classList.add('hidden'); if (!scnPaused) send({ action: 'pause' }); scnPaused = true; showBrief(scnSpec); };
$('h-pause').onclick = togglePause;
$('h-speed').onclick = () => { scnSpeed = scnSpeed === 1 ? 2 : scnSpeed === 2 ? 4 : scnSpeed === 4 ? 10 : 1; $('h-speed').textContent = scnSpeed + '×'; send({ action: 'set_speed', speed: scnSpeed }); };
$('h-replay').onclick = () => { $('report').classList.add('hidden'); scnManual = false; showDefence('ai_on'); send({ action: 'restart' }); };
const pickDefence = (id) => { showDefence(id, true); send({ action: 'toggle_ai', enabled: id === 'ai_on' }); };   // shared with every screen
$('m-off').onclick = () => pickDefence('ai_off');
$('m-on').onclick = () => pickDefence('ai_on');
$('h-signals').onclick = () => { document.body.classList.toggle('signals'); $('h-signals').classList.toggle('active'); document.body.classList.remove('clean'); $('h-clean').classList.remove('active'); };
$('h-clean').onclick = () => { document.body.classList.toggle('clean'); $('h-clean').classList.toggle('active'); };
$('h-list').onclick = () => { $('gallery').classList.toggle('hidden'); $('report').classList.add('hidden'); };
$('h-exit').onclick = () => startScenario(null);
$('report-close').onclick = () => $('report').classList.add('hidden');
$('rep-download').onclick = () => {   // the server builds the file; Content-Disposition makes the browser save it
  const a = document.createElement('a');
  a.href = '/api/report?t=' + Date.now(); a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
  toast('Report saved to your Downloads folder.');
};
$('rep-replay').onclick = () => { $('report').classList.add('hidden'); send({ action: 'restart' }); };
$('rep-arena').onclick = () => startScenario(null);
$('rep-next').onclick = () => {
  const i = SCN.findIndex((x) => x.id === lastReport?.scenario);
  if (i >= 0 && i < SCN.length - 1) startScenario(SCN[i + 1].id); else { $('report').classList.add('hidden'); $('gallery').classList.remove('hidden'); }
};
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === ' ' && scnId) { e.preventDefault(); if (!$('brief').classList.contains('hidden')) $('brief-start').click(); else togglePause(); }
  else if (e.key.toLowerCase() === 'h' && scnId) $('h-clean').click();
  else if (e.key.toLowerCase() === 'a' && scnId) pickDefence(scnShow === 'ai_on' ? 'ai_off' : 'ai_on');
  else if (e.key.toLowerCase() === 'p') openPhone();
  else if (e.key.toLowerCase() === 'i' && scnId) $('h-brief').click();
  else if (e.key === 'Escape') ['gallery', 'report', 'brief', 'bench', 'qrcard'].forEach((id) => $(id).classList.add('hidden'));
  else if (e.key === 'ArrowRight' && scnId && !$('report').classList.contains('hidden')) $('rep-next').click();
  else if (e.key.toLowerCase() === 's') $('b-scn').click();
});

// ================================================================ leaderboard
function renderBoard(lb) {
  $('lb').classList.toggle('hidden', !lb.length);
  $('lb-list').innerHTML = lb.slice(0, 5).map((p) => `<li><b>${esc(p.player)}</b><span>${p.attacks} jammer${p.attacks > 1 ? 's' : ''}</span></li>`).join('');
}

// ================================================================ join from a phone (QR)
let lanUrls = [], lanPick = 0;
function drawQr(url) {
  const q = window.qrcode(0, 'M'); q.addData(url); q.make();
  $('qr-img').innerHTML = q.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
  $('qr-url').textContent = url.split('#')[0] + (lanCode ? `  ·  join code ${lanCode} · then sign in` : '');
  document.querySelectorAll('#qr-ips button').forEach((b, i) => b.classList.toggle('active', i === lanPick));
}
let lanCode = '';
async function openPhone() {
  $('qrcard').classList.remove('hidden');
  try {
    const r = await (await fetch('/api/lan')).json();
    lanCode = r.join_code || '';
    const here = location.hostname;
    lanUrls = (r.app_urls || []).filter((u) => !u.includes('//127.'));
    if (here !== 'localhost' && !here.startsWith('127.')) lanUrls = [`${location.protocol}//${location.host}/app/`, ...lanUrls.filter((u) => !u.includes(here))];
    $('qr-warn').textContent = lanUrls.length ? (lanUrls.length > 1 ? 'Several networks found: if the scan does not open, try another address.' : '')
      : 'No Wi-Fi or hotspot address found. Connect this laptop to Wi-Fi or turn on its hotspot, then reopen.';
    if (!lanUrls.length) lanUrls = [`http://localhost:${r.port}/app/`];
    lanPick = 0;
    $('qr-ips').innerHTML = lanUrls.length > 1 ? lanUrls.map((u, i) => `<button data-i="${i}">${u.split('//')[1].split(':')[0]}</button>`).join('') : '';
    document.querySelectorAll('#qr-ips button').forEach((b) => (b.onclick = () => { lanPick = +b.dataset.i; drawQr(lanUrls[lanPick]); }));
    drawQr(lanUrls[0]);
  } catch (e) { $('qr-warn').textContent = 'Could not ask the server for its address.'; }
}
$('b-phone').onclick = openPhone; $('h-phone').onclick = openPhone; $('qr-close').onclick = () => $('qrcard').classList.add('hidden');
if (new URLSearchParams(location.search).has('qr')) setTimeout(openPhone, 800);

// ================================================================ websocket
function send(obj) { if (ws?.readyState === 1) ws.send(JSON.stringify({ type: 'cmd', player: $('player').value.trim() || undefined, ...obj })); }
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { $('pill-conn').textContent = 'LIVE'; $('pill-conn').className = 'pill'; };
  ws.onclose = () => { $('pill-conn').textContent = 'RECONNECTING'; $('pill-conn').className = 'pill bad'; setTimeout(connect, 1000); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    switch (m.type) {
      case 'hello':
        hello = m;
        $('pill-src').textContent = m.world_source === 'sim' ? 'Live simulation' : 'Placeholder world';
        $('banner-backend').textContent = m.signal_backend === 'gnuradio' ? 'GNU Radio' : 'NumPy';
        $('sec-fp').textContent = (m.key_fingerprint || '—').slice(0, 9);
        if (!$('jtype').options.length) $('jtype').innerHTML = m.jammer_types.map((t) => `<option value="${t}">${t[0].toUpperCase() + t.slice(1)}</option>`).join('');
        onHello(m);
        if (deepStart) {
          const want = deepStart; deepStart = null;
          history.replaceState(null, '', location.pathname + location.search.replace(/[?&]start=[^&]*/, '').replace(/^&/, '?'));
          $('gallery').classList.add('hidden');
          if (want === 'arena') startScenario(null); else { autoPlay = want; startScenario(want); }
        }
        if (m.leaderboard) renderBoard(m.leaderboard);
        loadLatestBenchmark();
        break;
      case 'state':
        if (m.view !== view) {
          view = m.view;
          document.body.classList.toggle('split', view === 'side_by_side');
          $('split').classList.toggle('hidden', view !== 'side_by_side');
        }
        for (const [id, w] of worlds) if (!worldIds().includes(id)) { w.dispose(); worlds.delete(id); }
        ensureWorld(m.world).apply(m);
        if (m.world !== 'ai_off') onScenarioState(m);
        break;
      case 'spectrum': {
        const w = ensureWorld(m.world); w.spectrum = m;
        if (w === primary() && !replayOn) { drawSpectrum(m); drawCnn(m); }
        break;
      }
      case 'replay': {                                      // recorded spectrum through the same CNN (ai/sources.py)
        replayOn = !!m.active;
        const note = $('cnn-src-note');
        if (!m.active) { $('cnn-src').value = ''; note.textContent = ''; note.className = 'small'; break; }
        $('cnn-src').value = m.file;
        drawSpectrum(m); drawCnn(m);
        const acc = m.stats?.labelled ? ` · ${Math.round(100 * m.stats.correct / m.stats.labelled)}% right so far` : '';
        note.textContent = `${m.simulated ? 'SIMULATOR CAPTURE' : 'RECORDED MEASUREMENT'} · ${m.source || m.name} · window ${m.window + 1}/${m.windows}`
          + (m.label ? ` · on the air: ${m.label}${acc}` : '');
        note.className = 'small ' + (m.simulated ? 'muted' : 'rec');
        break;
      }
      case 'decision': {
        if (view === 'side_by_side' && m.world !== (singleScn() ? scnShow : 'ai_on')) break;
        const lat = m.latency_ms != null ? ` · ${m.latency_ms.toFixed(0)} ms` : '';
        addDecision(`${m.reason}${lat}`, m.class === 'accidental' || m.class === 'clean' ? 'info' : 'good');
        break;
      }
      case 'event':
        if (m.caption) { onCaption(m); break; }
        if (m.focus) { if (scnId) showDefence(m.focus); break; }
        if (m.world === 'ai_off') break;
        if (m.level === 'alert' || m.level === 'warn' || !m.world) toast(m.text, m.level);
        if (m.text.startsWith('Jammer located') || m.text.startsWith('Relay') || m.text.includes('lost contact') || m.text.includes('rejoined')) addDecision(m.text, 'info');
        break;
      case 'report': showReport(m); break;
      case 'benchmark':
        showBenchmark(m);
        if (m.status === 'done') { latestBench = m; $('b-results').classList.remove('hidden'); }
        break;
      case 'score': {
        renderBoard(m.leaderboard || []);
        break;
      }
      case 'security': {
        const good = m.test === 'legit' ? m.accepted : !m.accepted;
        if (m.test === 'desync') {
          const box = $('sec-result'); box.innerHTML = '';
          const vv = document.createElement('div'); vv.innerHTML = `<span class="verdict ${m.accepted ? 'okv' : 'bad'}">SYNC LOSS → ${m.accepted ? 'TRIGGERED' : 'SKIPPED'}</span> `;
          vv.appendChild(document.createTextNode(m.reason)); box.appendChild(vv); break;
        }
        const word = m.test === 'legit' ? (m.accepted ? 'VERIFIED' : 'FAILED') : m.test === 'eavesdrop' ? 'NOTHING LEARNT'
          : m.test === 'reboot' ? (m.accepted ? 'NEW SESSION KEY' : 'FAILED') : m.test === 'clock_loss' ? (m.accepted ? 'HOLDOVER' : 'SKIPPED') : m.test === 'capture' ? (m.accepted ? 'STILL ACCEPTED!' : 'REVOKED')
          : (m.accepted ? 'ACCEPTED!' : 'REJECTED');
        if (['impersonate', 'capture', 'reboot', 'clock_loss'].includes(m.test)) {
          const box = $('sec-result'); box.innerHTML = '';
          const good = ['reboot', 'clock_loss'].includes(m.test) ? m.accepted : !m.accepted;
          const vv = document.createElement('div'); vv.innerHTML = `<span class="verdict ${good ? 'okv' : 'bad'}">${m.test.toUpperCase()} → ${word}</span> `;
          vv.appendChild(document.createTextNode(m.reason)); box.appendChild(vv); break;
        }
        const f = m.fields || {}, box = $('sec-result'); box.innerHTML = '';
        const vv = document.createElement('div'); vv.innerHTML = `<span class="verdict ${good ? 'okv' : 'bad'}">${m.test.toUpperCase()} → ${word}</span> `;
        vv.appendChild(document.createTextNode(m.reason)); box.appendChild(vv);
        const c = document.createElement('code');
        c.innerHTML = `<b>header</b> ${m.header_hex} <span style="color:#6c80a6">(src ${f.src} → ${f.dst} · epoch ${f.epoch} · seq ${f.seq})</span><br><b>ciphertext+tag</b> ${m.ciphertext_hex}`;
        box.appendChild(c);
        break;
      }
      case 'error': toast(m.message, 'warn'); break;
    }
  };
}

// ================================================================ controls
$('b-ai').onclick = () => { if (view !== 'single') send({ action: 'set_view', view: 'single' }); send({ action: 'toggle_ai', enabled: !aiOn }); };
$('b-split').onclick = () => send({ action: 'set_view', view: view === 'side_by_side' ? 'single' : 'side_by_side' });
$('b-clear').onclick = () => send({ action: 'clear_jammers' });
$('b-bench').onclick = () => send({ action: 'run_benchmark' });
$('b-results').onclick = () => latestBench && showBenchmark(latestBench);
// spectrum source: live simulator, or any recording in recordings/ (listed by the server)
let replayOn = false;
async function loadRecordings() {
  try {
    const list = await fetch('/api/recordings').then((r) => r.json());
    const sel = $('cnn-src'), keep = sel.value;
    sel.innerHTML = '<option value="">Live simulator</option>' + list.filter((r) => !r.error)
      .map((r) => `<option value="${r.file}">${r.simulated ? 'Sim capture' : 'Recording'}: ${r.name}</option>`).join('');
    sel.value = keep;
  } catch { /* server without recordings: the picker keeps just the simulator */ }
}
$('cnn-src').onfocus = loadRecordings;
$('cnn-src').onchange = (e) => send({ action: 'replay', name: e.target.value || null });
loadRecordings();
$('b-mission').onclick = () => send({ action: 'restart' });
$('b-theme').onclick = () => { const order = ['night', 'dusk', 'day', 'sunset']; tod = order[(order.indexOf(tod) + 1) % order.length]; applyTheme(); };
$('b-cam').onclick = () => { const names = Object.keys(CAMS); setCam(names[(names.indexOf(camName) + 1) % names.length]); };
document.querySelectorAll('[data-sec]').forEach((b) => (b.onclick = () => send({ action: 'security_test', test: b.dataset.sec })));

// click the ground to launch (a drag is a camera move, not a click)
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(), groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
let down = null;
canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', (e) => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
  const r = canvas.getBoundingClientRect();
  let x = (e.clientX - r.left) / r.width; const y = (e.clientY - r.top) / r.height;
  if (view === 'side_by_side') x = (x % 0.5) * 2;
  ndc.set(x * 2 - 1, -(y * 2 - 1)); ray.setFromCamera(ndc, camera);
  const hit = new THREE.Vector3(); if (!ray.ray.intersectPlane(groundPlane, hit)) return;
  const [sx, sy] = fromV(hit), b = hello?.bounds || { x: [-300, 1200], y: [-400, 900] };
  if (sx < b.x[0] || sx > b.x[1] || sy < b.y[0] || sy > b.y[1]) { toast('Launch inside the arena (the open field).', 'warn'); return; }
  send({ action: 'launch_jammer', jammer_type: $('jtype').value, pos: [Math.round(sx), Math.round(sy)] });
});

// ================================================================ render loop (bloom in single view; two viewports in split view)
function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false); composer.setSize(w, h);
}
window.addEventListener('resize', resize); resize();
const clock = new THREE.Clock();
function render(now) {
  const dt = Math.min(clock.getDelta(), 0.1), t = clock.elapsedTime;
  controls.update();
  grassU.time.value = t;
  if (beam) beam.rotation.y = t * 0.9;
  for (const w of worlds.values()) w.frame(dt, t);
  clouds.forEach((c, i) => { c.position.x += dt * (5 + (i % 4)); if (c.position.x > 9000) c.position.x = -9000; });
  const W = window.innerWidth, H = window.innerHeight, ids = shownIds();
  if (ids.length === 1) {
    for (const [wid, w] of worlds) w.group.visible = wid === ids[0];
    camera.aspect = W / H; camera.fov = W / H < 1.15 ? 66 : 48; camera.updateProjectionMatrix();
    renderer.setScissorTest(false); renderer.setViewport(0, 0, W, H);
    if (HIGH) composer.render(); else renderer.render(scene, camera);
  } else {
    renderer.setScissorTest(true);
    ids.forEach((id, i) => {
      const vx = i * W / 2, vw = W / 2;
      for (const [wid, w] of worlds) w.group.visible = wid === id;
      camera.aspect = vw / H; camera.fov = 64; camera.updateProjectionMatrix();
      renderer.setViewport(vx, 0, vw, H); renderer.setScissor(vx, 0, vw, H);
      renderer.render(scene, camera);
    });
    renderer.setScissorTest(false);
  }
  updatePanels(now || 0);
  requestAnimationFrame(render);
}
setCam('Cinematic');
buildEnvironment(null);
applyTheme();
loadScenarioCatalog().then(connect);
requestAnimationFrame(render);

// while the result card is open, the live status strip and AI switch step aside (they used to sit on top of it)
new MutationObserver(() => document.body.classList.toggle('reporting', !$('report').classList.contains('hidden')))
  .observe($('report'), { attributes: true, attributeFilter: ['class'] });
