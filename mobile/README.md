# mobile/ — ABHEDYA phone app (React PWA, Phase 5)

The operator's controller. It connects to the same WebSocket as the 3D view, so anything done on a
phone shows on the big screen at once.

| Tab | What it does |
|---|---|
| **Live** | Plain-English status ("All clear", "Under attack, AI holding", "Link failing"), delivery gauge, AI OFF / ON switch, threat card (CNN verdict in plain words, jammer location estimate, the AI's last action), KPI tiles, alerts feed. Vibrates when a new jammer is detected. |
| **Map** | Top-down live map: links coloured by health, jammer reach, the AI's yellow estimate rings. Pick a jammer type and **tap to launch it** (appears on the big screen with your name). |
| **Signal** | Spectrum with our channel and the AI's blacklist, waterfall, CNN probabilities, Grad-CAM evidence. |
| **Missions** | The 7 guided scenarios: start one on the big screen, Start / Pause / speed / Replay / Exit, switch every screen between AI OFF and AI ON, live comparison and the end report. In the arena: pause, speed, new mission, AI OFF vs ON side by side. |
| **More** | Your name (leaderboard), the 5 security tests with real header and ciphertext bytes, benchmark table and a quick re-run, settings (vibration, laptop address), install. |

## Use it

The built app is committed in `dist/`, so the laptop needs **no Node.js and no internet**:
the server serves it at `http://<laptop>:8000/app/`. Open the 3D view and press **📱 Phone** (or `P`)
for a QR code, or open `/demo` for the judges' screen with a live phone frame and a QR code.

Phones must be on the same Wi-Fi or on the laptop's hotspot. If a phone cannot connect, allow Python
through Windows Firewall on private networks. The app reconnects by itself.

**Install:** browser menu → *Add to Home screen*. Browsers only enable the offline cache (service worker)
on `https://` or `localhost`; over a plain `http://192.168.x.x` address the app works the same but is
loaded from the laptop each time, which is fine because the laptop is the server anyway.

## Develop

```bash
cd mobile
npm install
npm run dev      # http://localhost:5173/app/ with /ws and /api proxied to the server on :8000
npm run build    # writes dist/ (and dist/sw.js listing every built file); commit dist/
```

Source: `src/store.js` (one WebSocket, auto-reconnect, plain-English alerts), `src/tabs/*.jsx`,
`src/ui.jsx` (gauge, AI switch, canvas helper), `src/styles.css`. Commands follow
`shared/schema.json` → `$defs/cmd`: `{type:"cmd", id, player, action, ...}`.
