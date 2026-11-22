# ABHEDYA

[![CI](https://github.com/vishwass-02/JamBreakers-_-Abhedya/actions/workflows/ci.yml/badge.svg)](https://github.com/vishwass-02/JamBreakers-_-Abhedya/actions/workflows/ci.yml)

**AI-driven adaptive anti-jamming for tactical drone and vehicle radio networks.**
Team JamBreakers · BITS2WAVE (IEEE ComSoc, BMS College of Engineering) · problem statement SW-26 *AEGIS-LINK* · software track.

A simulated battlefield radio network (drones, trucks, a command post) is attacked by nine kinds of jammer.
ABHEDYA's neural network looks at the radio spectrum, identifies the type of jammer, and picks the right
countermeasure in about 20 ms: hop, blacklist channels, raise power or switch to a more robust modulation.
Every packet is really encrypted (AES-256-GCM). You watch it live in 3D, control it from a phone, and
download a report at the end.

> **Result (simulated, 10 seeds × 9 jammers, 810 runs):** the AI keeps **94.8 %** of messages flowing under
> attack, against **54.0 %** for conventional fixed hopping. It also beats a rule-based defence that has the
> *same* tools but no AI, by **+3.2 points** (paired 95 % CI +2.2 to +4.2), while using **less than half its
> extra transmit power**. What this does and does not prove: **[docs/EVIDENCE.md](docs/EVIDENCE.md)**.

| Read this | For |
|---|---|
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | Claims, the evidence behind each one, and their limits |
| [docs/BENCHMARK.md](docs/BENCHMARK.md) | Full benchmark tables (9 defences × 9 jammers) |
| [docs/ROBUSTNESS.md](docs/ROBUSTNESS.md) | 1,136 runs under changed conditions, stacked and unseen jammers |
| [docs/SECURITY.md](docs/SECURITY.md) | Radio encryption and keys; sign-in, roles and the QR join code |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The demo, minute by minute, and answers to hard questions |
| [docs/HARDWARE_TEST_PLAN.md](docs/HARDWARE_TEST_PLAN.md) | How we will test on real SDRs |

## Quick start (demo laptop)

1. Double-click **`run_windows.bat`**. The first run installs everything (needs internet once).
   **On the first start the server window prints two sign-in accounts** (`commander`, `judge`); they are also
   saved in `data/FIRST_LOGIN.txt`. Note them down.
2. Double-click **`check_demo.bat`**. It checks power, Wi-Fi, firewall, ports and how fast this laptop runs 10×.
   (Or run **`verify.bat`** once: installs, lints, runs every test and the check, then starts the server.)
3. Open **http://localhost:8000/intro/**, the mission deck. Click any mission to start it in 3D.
4. Phones: press **📱 Phone** in the 3D view, scan the QR, then sign in (`judge` can watch, `commander` can control).

## Check everything with one command

| Command | What it does |
|---|---|
| **`verify.bat`** (Windows) · `scripts/verify.sh` (macOS / Linux) | Installs everything, runs the lint, all 240+ tests and the pre-flight check; **only if all of it passes** it starts the server |
| `check_demo.bat` | Pre-flight check only (power, Wi-Fi, firewall, port, accounts, real 10× speed). Read-only |
| `python scripts/rehearse.py --rounds 3` | Automated rehearsal: drives the deck, 3D view, judges' screen, console and phone (QR + sign-in, roles), runs all 7 missions at 10× and downloads every report, failing on any browser error. Needs `pip install playwright` once |

The same lint and tests run on GitHub for every push (Python 3.11 and 3.12), plus both front-end builds.

## Features

- **Simulated radio world**: 8 channels at 2.4 GHz, 10 ms slots, path loss, fading, terrain and buildings; drones on
  patrol paths and trucks on roads; a command post and a relay.
- **Nine jammers**: barrage, random, sweep, reactive, deceptive (forged frames), accidental, follower, a learning
  "smart" jammer, and an unseen "novel" pattern. Several can run at once.
- **The AI**: a CNN reads the spectrum and names the jammer (1.9 ms per window), then picks the response for that
  type: keyed fast hopping, blacklisting, power, BPSK, relay or survival mode. It says "unknown" instead of guessing,
  hands over to an RL agent when stuck, and a safety guard undoes any move that makes things worse.
- **Real security**: AES-256-GCM on every packet, keyed hop sequences, signed orders, per-device keys, revocation,
  replay protection; sign-in with roles for every screen that is not the demo laptop.
- **Screens**: mission deck (3D voxel worlds), live 3D view with AI OFF / AI ON side by side, judges' screen, phone
  app (PWA, joined by QR), debug console. Speeds 1×, 2×, 4×, 10×.
- **Evidence**: seeded benchmark (9 defences × 9 jammers × 10 seeds), robustness sweep (1,136 runs), classifier
  stress test, paired statistics, a downloadable HTML report for every mission, a CSV mission log.

## Limitations (read these before judging)

- **Everything is simulated.** No over-the-air measurement yet; the GNU Radio chain is a software cross-check. The
  hardware test plan and kit are ready (`docs/HARDWARE_TEST_PLAN.md`, `hil/`), the SDR backend is not written.
- **The jammers and the training data come from our own simulator.** The stress test (`docs/EVIDENCE.md` §3)
  checks distortions the CNN never saw, but a real adversary may behave in ways none of our models capture.
- **Most of the gain over fixed hopping comes from the tools** (power, modulation, blacklisting). The AI adds
  +3.2 points over the same tools driven by rules, and uses far less power. It ties the RL agent on delivery.
- **Rare jammer types are named poorly** (about 60 % for deceptive, accidental, follower); delivery stays high
  because their responses overlap.
- **One laptop, one Python process.** At 10× a slow or battery-powered laptop runs slower than 10×
  (`check_demo.bat` measures it). The phone app over plain http sends the password unencrypted unless a
  certificate is added (`docs/SECURITY.md`).

## Run

Needs **Python 3.11 or newer** (python.org; tick "Add python.exe to PATH" when installing). No Node.js,
no internet at demo time, no GPU.

**Windows (main path)**
1. Double-click `run_windows.bat`. The first run creates `.venv` and installs the packages (needs internet
   once, 1-3 min). Later runs start in seconds and need no internet.
2. Open **http://localhost:8000/demo** (judges' screen) or **http://localhost:8000/viewer/** (3D view).
3. Phones: join the laptop's Wi-Fi or its Mobile hotspot, press **📱 Phone** and scan the QR code.

Running it again is always safe: it stops any older ABHEDYA server on ports 8000/8001 first.

**macOS / Linux:** `./run.sh` (same first-run behaviour; `AEGIS_PORT=8100 ./run.sh` for another port).

**Docker:** `docker build -t abhedya . && docker run --rm -p 8000:8000 abhedya`
(NumPy signal chain, runtime packages only. The image contents were verified by running exactly the files it
copies with only `requirements.txt`; build it once with internet before the event.)

**GNU Radio signal chain (optional):** install radioconda, then double-click `run_gnuradio.bat`. It looks for
radioconda in the usual places; NumPy stays the default because it is ~6x faster and gives the same numbers
(`scripts/check_gnuradio.py` compares them: within 0.5 dB).

**If something goes wrong**

| Symptom | Fix |
|---|---|
| `python` not found / venv fails | Install Python 3.11+ from python.org with "Add to PATH", delete `.venv`, run again. |
| Phone can't open the page | Same Wi-Fi or the laptop's hotspot; allow Python through Windows Firewall on **private** networks; in the QR card try the other address buttons. |
| QR shows "no Wi-Fi or hotspot address" | Connect the laptop to Wi-Fi or turn on Mobile hotspot, then reopen the QR card. |
| Port 8000 busy by another program | `run_8001.bat`, then use port 8001 in every URL. |
| Slow laptop at 4x with many jammers | **Plug the laptop in** (on battery Windows throttles the CPU 2-3x) and set Power mode to Best performance. The server slows simulated time a little by design so screens stay live; `/health` shows `tick_cpu_ms` and `overrun_ticks`. Use 1x or 2x. |
| Server slow while the 3D view is open | Show the 3D view in **Edge or Chrome** (they use the graphics card). Embedded or remote-desktop browsers may render 3D on the CPU and starve the server. |
| 3D view blank or very slow | Add `?q=low` to the URL (lighter graphics). |
| Old page after an update | Ctrl+F5 (the server already sends no-cache headers). |

**Tests:** `.venv\Scripts\python -m pytest -q` (Windows, or double-click `run_tests.bat`) or `.venv/bin/python -m pytest -q`: 200+ tests, plus `ruff check .` for lint (both run on every push, see the CI badge); PyTorch
(training only) and GNU Radio tests skip themselves when not installed.

Then open:

| URL | What |
|---|---|
| http://localhost:8000/ | Debug console: live map, spectrum, KPIs, every command |
| http://localhost:8000/health | Server health |
| http://localhost:8000/api/schema | The message contract (`shared/schema.json`) |
| http://localhost:8000/api/lan | The laptop's LAN URLs (for phones and the QR code) |
| ws://localhost:8000/ws | The one WebSocket: ~20 Hz broadcast + commands |
| http://localhost:8000/view/ | **3D simulation view** (Three.js, vendored, works offline) |
| http://localhost:8000/app/ | **Phone app** (React PWA); phones join by QR from the 📱 Phone button |
| http://localhost:8000/demo | **Judges' screen**: 3D view + live phone frame + join QR |

**Phones:** use the same Wi-Fi or hotspot. The server binds to `0.0.0.0`. On Windows, allow Python
through the firewall for **private** networks.

**Probe a running server:** `python scripts/ws_probe.py --cmd '{"action":"launch_jammer","jammer_type":"barrage","pos":[100,100]}'`

### Environment variables (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `AEGIS_WORLD` | `sim` | `sim` (real simulation) or `fake` (lightweight placeholder for UI work) |
| `SIGNAL_BACKEND` | `numpy` | `gnuradio` or `numpy`. If GNU Radio fails to load, the server falls back to NumPy and says so in an event |
| `AEGIS_SEED` | `42` | Seed for replayable runs |
| `AEGIS_TICK_HZ` | `20` | Broadcast rate |
| `AEGIS_SLOT_MS` | `10` | Radio slot length inside the simulation |
| `AEGIS_AUTH` | `1` | `0` switches sign-in off (anyone on the Wi-Fi can then control the demo) |
| `AEGIS_JOIN_CODE` | `0` | `1` also asks phones for the join code shown under the laptop's QR |
| `AEGIS_USERS_FILE` | `data/users.json` | Where accounts are stored |

The variables keep their `AEGIS_` prefix from the project's working name, so existing setups keep working.

## Message contract (summary)

The full contract is `shared/schema.json` (JSON Schema 2020-12). Every server message has
`{v:"1.0", type, t, src}`.

- **Server → client:** `hello` (once on connect), `state` (20 Hz, per world), `spectrum` (128 bins,
  CNN probabilities, optional 12×32 Grad-CAM), `event` (plain English, level info/warn/alert/good),
  `decision`, `security`, `score`, `benchmark`, `report`, `error`.
- **Client → server:** `{type:"cmd", id?, player?, action, ...}` with these actions: `launch_jammer`
  {jammer_type, pos:[x,y], power_dbm?}, `clear_jammers`, `toggle_ai` {enabled?}, `set_view`
  {view: single|side_by_side}, `run_benchmark` {seed?, duration_s?}, `security_test`
  {test: tamper|spoof|replay|eavesdrop}, `load_scenario` {name}, `exit_scenario`, `play`, `pause`,
  `set_speed` {1|2|4|10}, `restart`.
- **Side-by-side:** the server sends two `state` and two `spectrum` messages per tick, with `world` set
  to `ai_off` and `ai_on`. In single view, `world` is `main`.
- **Errors:** an `error` goes only to the client that sent the bad command, with `req_id` echoed back.
  Everything else is broadcast, so every screen stays in sync.
- **Units:** metres, with x east, y north, z up. The arena is x −300..1200 and y −400..900. There are
  8 channels (0–7) at 2410–2480 MHz.

## GNU Radio

The receiver chain is a GNU Radio flowgraph (`sim/phy_gnuradio.py`). Each transmitter runs through
the QPSK Constellation Modulator (`digital.generic_mod`, RRC 0.35), a rotator onto its channel, and the
Rician `fading_model`. The receiver then adds the interference-engine waveforms and thermal noise,
followed by `stream_to_vector`, a 128-point Hann `fft_vcc`, `complex_to_mag_squared`, and a vector
sink. The output matches the NumPy reference to within 0.5 dB on every emitter type.

On Windows, GNU Radio comes from radioconda. Run the server with the radioconda Python:

```
<radioconda>\python.exe -m pip install -r requirements.txt
set SIGNAL_BACKEND=gnuradio
<radioconda>\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

- `scripts/check_gnuradio.py` compares the two backends and times them (about 18 ms per window on GNU Radio vs 4 ms on NumPy).
- This is a **software cross-check of the signal chain, not RF validation**. Nothing in this project has been measured on real radios yet; see *Hardware-in-the-loop* below for how we will.
- `scripts/gr_live_demo.py [narrowband|barrage|sweep]` opens a live Qt waterfall of the chain, for showing judges.

## How the simulation works (Phase 1)

- **Radio:** 8 channels at 2410–2480 MHz, 10 ms TDMA slots, QPSK at 2 Msym/s (2.7 MHz wide), 27 dBm
  radios. Path loss is log-distance from a 1 m free-space reference (exponent 2.2 for air links, 2.9
  for ground links) plus Rician block fading (K = 9 dB air, 3 dB ground). Buildings cost −22 dB and
  hills −18 dB, using a 3D ray–box test. Each 58-byte packet passes or fails on coded BER, drawn per
  slot. BPSK fallback is worth +3 dB.
- **Mesh:** each node routes to the command post along the widest path (best weakest-link SINR), with
  3 dB hysteresis. Delivery means packets that reach the command post end to end.
- **Jammers:** behaviour lives in `sim/jammers.py`. A follower re-tunes about 25 ms after each hop
  *and learns repeating hop patterns*, which is why fixed hopping collapses against it. A reactive
  jammer listens on one channel and jams only the packets it hears.
- **Control plane:** a defence takes effect only when its control frame is delivered. Control frames
  go out on a key-derived rendezvous channel that moves on every retry, with back-off. Hop latency is
  measured from the first jammed slot to the slot when the whole network is on the new channel.
- **Determinism:** separate random streams (fading, packets, jammers, control, spectrum, defender)
  come from one seed. Twin worlds share them, so the AI OFF and AI ON sides see the same fading and
  the same jammer randomness.

Baselines (12 s, jammer from t = 2 s, seed 7; `python -m sim.experiment` style runs via `sim/experiment.py`):

| Jammer | No defence | Fixed hopping (AI OFF) | Energy detector |
|---|---|---|---|
| random | 31 % | 65 % | 90–95 % |
| deceptive | 42 % | 92 % | 99.8 % |
| reactive | 3 % | 88 % | 17 % |
| follower | 3 % | 5 % | 5 % |
| barrage / sweep | 25 % / 10 % | same | same |

The CNN defender has to beat every column. Barrage and sweep need power and rate backups, and the
follower needs keyed hopping.

## The AI (Phase 2)

**Classifier.** A 3-layer CNN (19k parameters) reads a 12 × 128 spectrogram window (0.3 ms ×
80 MHz) and outputs 7 classes. It is trained in PyTorch on 16,800 synthetic windows plus 6,300
windows captured from the running world, then exported to `ai/weights/cnn_v1.npz` and served in pure
NumPy, so the server needs no PyTorch. The global-average-pool head gives Grad-CAM in closed form:
a 12 × 32 heat map showing where the CNN looked.

| Held-out live windows (other seeds) | 1,680 |
|---|---|
| Accuracy (held-out test set, 1,680 windows captured live) | **95.4 %** (clean 100, barrage 100, sweep 100, reactive 97.9, deceptive 95.8, random 88.3, accidental 85.8) |
| NumPy inference | 1.9 ms per window (target < 20 ms) |

Rebuild with `python -m ai.dataset` and then `python -m ai.train` (about 2 + 4 min on a laptop CPU).
Full report: `ai/weights/report.json`.

**Defender** (`ai/defender.py`). It triggers at more than 10 % loss, captures 3 windows at the
receiver that is losing packets, and decides on the average of the 3 classifications:

| Class | Response |
|---|---|
| barrage | Raise power +3 dB per step (cap +9 dB) and drop to BPSK; hopping is pointless |
| sweep | Raise power and hold, then BPSK; hopping only thrashes |
| random | Blacklist the occupied channels and hop to the cleanest one |
| reactive | Keyed fast hopping, and blacklist the channel it listens on |
| deceptive | Hop away and blacklist the channel; authentication rejects the forged frames |
| accidental | No alarm; quietly move to a clean channel |
| clean but losing packets | A link is blocked, so dispatch a relay drone above the tallest obstacle + 30 m |

On top of the per-class responses:

- **Follower detection, from behaviour:** the jammer re-finds us under 300 ms after a hop, or, while fast hopping, the jammed channel matches where we were 1–4 slots earlier. Response: keyed fast hopping that never reuses the last 3 channels.
- **Hop guard and recovery:** at most 4 hops in 5 s; when the band is quiet, emergency measures are stepped back down one at a time.
- **Localisation:** wideband RSSI least squares with per-node path-loss exponents, a leave-one-out NLOS rejection pass (8 dB) and a 95 % uncertainty radius. Estimates are published under 300 m and announced under 150 m. Measured: published in 23 of 30 runs, median error 67 m on a 1.5 × 1.3 km map.

**Results:** delivery, accuracy and latency figures for every defence are in [docs/EVIDENCE.md](docs/EVIDENCE.md) (section 0 defines each number) and [docs/BENCHMARK.md](docs/BENCHMARK.md).

**Lessons this build taught us** (good answers for judges):

- **Keyed hopping needs real choices.** With 4 channels blacklisted and 3 avoided, only one channel was left each slot, so the "keyed" sequence turned into a 4-channel loop that the follower learned. The sequence now always keeps at least 3 candidates, and the blacklist gives way first.
- **The control link must be robust.** Barrage and sweep blocked control frames for 8 s until they were sent like real control traffic: max power, BPSK, repetition coding, and a rendezvous channel that moves on every retry.
- **Don't re-classify every 10 ms.** Under heavy jamming that cost 27 ms per slot and stalled the server. The defender now waits 50 ms after acting and 250 ms after holding, and the server never does more than 2× the normal work per tick.

## Security (Phase 3)

Full threat model, including the v1.0 sign-in for phones and second screens: [docs/SECURITY.md](docs/SECURITY.md).

`sim/security.py` uses the audited `cryptography` library and implements no crypto of its own.

- **Mission key:** 32 bytes, pre-loaded before deployment and rotated per mission (↺ in the UI starts a new mission with a new key). Only its fingerprint is ever shown.
- **HKDF epochs:** every 60 s, HKDF-SHA256 derives fresh per-epoch keys from the mission key: `enc` for AES-256-GCM, `hop` for the HMAC hop sequence, `rdv` for the rendezvous channel. The hop sequence is therefore keyed per epoch.
- **Frames:** 58 bytes, the same size the link budget uses. A 10-byte authenticated header (version, type, src, dst, epoch, seq), then 32 bytes of encrypted telemetry, then a 16-byte tag. The nonce is src + epoch + seq, so it is unique per key.
- **Receiver checks:** version, then known sender, then epoch (current or previous, for clock skew), then a 64-frame replay window, then the GCM tag.
- **Every packet in the simulation is a real frame,** sealed at the node and verified at the command post. `frames_verified` counts real verifications.
- **Deceptive jammers inject real forged frames:** spoofed with a wrong key, or replays of recorded frames. All are rejected (`frames_forged_accepted` is tested to stay 0). In a 70 s run against fixed hopping: 491 forged, 491 rejected.
- **Defence decisions are authenticated control frames.** They take effect only after they verify (`auth_syncs`). Lost nodes listen on the key-derived rendezvous channel to rejoin.

| Button | What happens |
|---|---|
| Show packet | A genuine frame from D1 is verified and its telemetry decrypted |
| Tamper | One bit flipped in the ciphertext: the GCM tag does not verify |
| Spoof | A command "from D1" forged without the mission key: the tag does not verify |
| Replay | An exact copy of a genuine frame resent: caught by the replay window |
| Eavesdrop | The attacker sees only ciphertext; decrypting with a guessed key fails |

Each test shows the real header and ciphertext bytes.

## Benchmark (Phase 4, extended in v1.0)

`python -m sim.benchmark` runs every jammer type against nine defences: no defence, fixed hopping, an energy
detector, secret fast hopping, brute force (max power + BPSK), rule-based adaptive control with no AI, the RL
agent, the ABHEDYA AI and the hybrid. For each seed, every defence faces an identical seeded run, so only the
defence differs. **▶ Benchmark** in the 3D view runs the fair line-up of four (fixed hopping, brute force, rules
without AI, ABHEDYA) in worker processes, so the live demo never stutters. Results land in `results/` and
**📊 Results** reopens the latest.

Current numbers, paired statistics and limits: [docs/EVIDENCE.md](docs/EVIDENCE.md). Full tables:
[docs/BENCHMARK.md](docs/BENCHMARK.md).

## Smart jammer, RL agent and the full benchmark (Phase 6)

- **Smart jammer** (`smart` in the jammer menu, the phone's Map tab and the benchmark): an adaptive adversary.
  Every second a UCB1 bandit picks one of five tactics: follow, predict (it learns our hop transitions), wide,
  pulse or spoof. It scores each by how often its energy landed on our channel, using only channels it has
  heard us on. Its label in the 3D view and on the phone shows the tactic it is using right now. It learns to
  *predict* fixed hopping and to *follow* the energy detector; against ABHEDYA nothing works.
- **RL hopping agent** (`ai/rl.py`, defender `rl`): tabular Q-learning (256 states × 5 moves) trained in this
  simulator from packet loss and channel energy only. Retrain with `python -m ai.rl --train` (~2 min).
- **Full benchmark**: 9 attacks × 9 defences × 10 seeds × 20 s, identical seeded runs, with SD, 95 % CI, worst run
  and paired comparisons. Numbers and their limits: [`docs/EVIDENCE.md`](docs/EVIDENCE.md); re-run with
  `python -m sim.benchmark --seeds 10 --duration 20` and redraw with `python scripts/plot_benchmark.py`.
- **Leaderboard**: everyone who launches jammers from a phone (with a name set) appears on the big screen.

## What-if handling (Phase 8)

Six questions a judge will ask, each answered in code and covered by `tests/test_phase8.py`.
Plain-English answers for the stage are in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

| Question | What the system does | Where |
|---|---|---|
| Battery runs out? | Battery model per drone (hover + radio power). Below 25 % it flies home, 20 s swap, relaunch; the mesh reroutes. Below 35 % the AI caps its power boost at +6 dB. | `sim/battery.py` |
| Only 7 jammer classes? | Open-set check on the CNN's fingerprint → verdict **unknown**. Tries safe moves one by one, stores what worked in a signature memory and recalls it next time (86.8 % → 94.6 %). | `ai/ood.py`, `ai/defender.py` |
| A drone misses the channel switch? | It notices in 30 ms and rejoins on a key-derived rendezvous channel via an authenticated beacon (~50 ms). Security → *Sync loss* shows it. | `sim/world.py` |
| Every channel jammed? | Fallback ladder: power → BPSK → 2 s keyed-hopping test → **survival mode** (BPSK + heavy coding), no more hopping. Steps back up as the jamming eases. | `ai/defender.py` |
| Combine the two AIs? | **Hybrid** (AI ON default): AEGIS leads, a referee gives RL a 4 s trial when delivery over 6 s drops below 93 % and keeps whichever does better. | `ai/defender.py` |
| AI gives wrong / out-of-order orders? | **Safety guard** on every order: fixed order, contradictions removed, jammed targets corrected, limits enforced, ≤ 6 changes/s, automatic undo if delivery gets worse. | `sim/safety.py` |

## Real spectrum and the mission log (Phase 9)

- **Spectrum sources** (`ai/sources.py`): the CNN and the unknown-detector take a 12 x 128 dBm window over
  2405-2485 MHz from any source. The live arena uses the simulator; the CNN panel's **Spectrum from** picker
  replays a recording from `recordings/*.npz` instead (ten windows a second, with the running accuracy when the
  recording is labelled). A recording is a capture, so it exercises the classifier only; the closed-loop
  AI OFF vs AI ON benchmark stays in the simulator, where both defences face the identical attack.
  - `python -m ai.sources convert-sweep hackrf.csv --out recordings/x.npz --label barrage --source "HackRF, lab"`
    (`hackrf_sweep` output) · `convert-wide` (time, then one column per MHz) · `eval recordings/x.npz`
    (verdicts per label, accuracy) · `capture --jammer sweep --out recordings/sim_x.npz` (simulator capture,
    marked simulated). `LiveHackRF` streams a HackRF One through the same path.
  - Every recording is resampled onto our 128 bins and its noise floor (10th percentile) aligned to our receiver.
  - The two shipped files are **simulator captures** (sweep, and the never-seen comb → "unknown"), labelled as
    such everywhere. Measured public datasets to add: WiFiSpectralJam (Kaggle), RF Jamming Dataset (IEEE DataPort).
- **Mission log** (`sim/missionlog.py`): every AI decision with its reason and latency, safety-guard corrections,
  events, security tests and a per-second score snapshot per world. **⬇ Log** in the 3D view, More → Mission
  log on the phone, or `GET /api/mission-log` (`?format=json`). A new log starts with every scenario, restart or
  view change; capped at 20,000 rows.
- **Mission report** (`sim/report_html.py`): when a scenario finishes, **⬇ Download report** on the result card
  (3D view and phone) saves `ABHEDYA_<scenario>_<date>.html` — one self-contained file that opens offline:
  AI OFF vs AI ON headline, delivery-over-time chart, side-by-side numbers, every AI decision with its reason,
  the raw log as a CSV inside, and **Print / Save as PDF**. Also `GET /api/report` (`?download=0` to view).
- **Speed**: scenarios run at 1×, 2×, 4× or 10×. At 10× the spectrum panel refreshes 5 times a second instead of
  20 and each tick may use a little more CPU; on a slow laptop simulated time slows slightly rather than freezing.

## Reviewer critiques A–J (Phase 10)

Ten points from the review, what we built for each, and what it measured. All numbers are simulated.

| | Critique | What we built | Result |
|---|---|---|---|
| A | No hardware in the loop | `hil/`: one radio interface (SimBackend today, SdrBackend wiring guide), bench test, measured-vs-predicted comparison, `docs/HARDWARE_TEST_PLAN.md` (two SDRs, cabled through attenuators, nothing radiated) | Scripts run end to end on the simulator backend. **Nothing hardware-validated yet**; GNU Radio is a software cross-check only |
| B | RF calibration | `ai/calibration.py`: start-up quiet-band calibration (gain + ripple), AGC gain taken from the radio, DC-spike removal, slow rate-limited floor tracking from quiet bins only, occupancy over the real 2.7 MHz channel (4 bins) | CNN on a receiver with ±15 dB gain error, ripple, DC spike and AGC steps: 78–91 % → 100 % correct, same as a clean receiver |
| C | Time sync | `sim/timesync.py`: ±20 ppm clocks, 1 s beacons with drift estimation, holdover, backup master after 3 s, reboot → rendezvous resync | No sync: outside the 0.5 ms guard after 25 s. Synced ≤ 19 µs; 120 s holdover 70 µs; master loss → D1 takes over ≤ 12 µs; reboot (47 ms off) resynced in 50 ms |
| D | Benchmark statistics | 10-seed benchmark with SD, 95 % CI and worst run; `sim/robustness.py` sweeps power, distance, speed, network size, background, stacked and unseen jammers (1,136 runs) | AI ON (hybrid) 95.1 % (CI 94.3–96.0) vs 54.0 % for fixed hopping; AI ON wins all 71 conditions; weakest: 46 dBm sweep 51 %, stacked 61–89 %. Hop latency p50 20 ms, p95 110 ms (misses 100 ms at p95). `docs/EVIDENCE.md`, `docs/ROBUSTNESS.md` |
| E | Several / unseen jammers | `ai/behaviours.py`: multi-label tracker (wideband / tracking / narrowband / intermittent / unknown); the follower under a sweep is found with a t-test; a second unseen pattern (`novel_staircase`) | AEGIS: sweep + follower 74.9 ± 6.6 → 78.2 ± 1.8 %; three stacked 70.6 ± 9.0 → 75.0 ± 3.4 % (8 seeds). Staircase 77.8 %, flagged unknown |
| F | Hybrid referee | Context-aware referee: stacked / volatile / steady, a separate scoreboard per context, faster judging when the attack keeps changing | smart 92.9 (AEGIS 89.0, RL 94.5); novel 93.2 (best of three) |
| G | QoS routing | `sim/qos.py`: command / position / battery / telemetry classes with deadlines, strict priority, store-and-forward, FIFO baseline on identical links | Under sweep + follower: commands on time 87.9 vs 65.8 %; position 97.6 vs 70.6 %; 100 % of commands delivered |
| H | Keys | `sim/keymgmt.py`: per-device keys, signed group-key grants, per-session keys with a fresh salt per join (no nonce reuse after a reboot), revocation rotates the group key | Impersonation, captured device, reboot replay: all rejected, 0 forged frames accepted |
| I | Localisation validation | `sim/loc_eval.py`: error by jammer, position and terrain; honest F-distribution radius; fusion over time | Open field median 58 m, p95 264 m; ring correct 87 %. Fails outside the formation (≈270 m bias) — reported, not hidden |
| J | Battery realism | Radio power counted (4 W base), return threshold from distance home + 10 % reserve (never below 25 %), low-battery drones avoided as relays | Modelled values, stated as such |

Encryption does not stop jamming: it stops forged and replayed commands. Jamming is fought with hopping,
power, modulation, routing and QoS.

## Phone app and judges' screen (Phase 5)

- **Phone app** (`/app/`, React PWA in `mobile/`, built copy in `mobile/dist/` so no Node.js is needed to run it):
  Live (plain-English status, delivery gauge, AI OFF/ON, threat card, KPIs, alerts with vibration), Map (tap to launch
  jammers, AI estimate rings), Signal (spectrum, waterfall, CNN bars, Grad-CAM), Missions (the 7 scenarios with full
  playback control and the report), More (name, security tests with real bytes, benchmark, settings). Auto-reconnects.
- **Join by QR:** press **📱 Phone** (or `P`) in the 3D view. Phones must be on the same Wi-Fi or the laptop's hotspot.
- **Judges' screen:** open **`/demo`**: the 3D view, a live phone frame and a QR code side by side, plus an
  AI OFF vs ON button and a big-QR mode (`Q`).
- **Every screen stays in sync:** a mission started on any phone switches the big screen; scenarios load paused on the
  briefing until someone presses Start (on any screen); the AI OFF / ON switch during a scenario flips every screen.

## Guided scenarios (for the demo)

Press **🎬 Scenarios** (or `S`) in the 3D view. Each scenario is one jammer type in its own environment, with its own
obstacles (that really block signals in the simulation), unit routes, camera and time of day. It always runs side by side:
left = AI OFF (fixed hopping), right = AI ON (ABHEDYA), same seed and same attack.

| # | Scenario | Jammer | Environment | Expected |
|---|---|---|---|---|
| 1 | Border convoy | barrage | mountain pass · night | Fixed hopping ≈ 30–40 % delivered · ABHEDYA ≈ 90 %+ |
| 2 | Urban patrol | random | city · dusk | Fixed hopping ≈ 65–70 % · ABHEDYA ≈ 97 %+ |
| 3 | Coastal watch | sweep | coast · dusk | Fixed hopping ≈ 25–40 % · ABHEDYA ≈ 85–95 % |
| 4 | Forest ambush | reactive | forest · night | Fixed hopping ≈ 85–88 % · ABHEDYA ≈ 93–95 % |
| 5 | Airbase spoofing | deceptive | airbase · night | Fixed hopping ≈ 90 % (but forged frames keep arriving) · ABHEDYA ≈ 99.8 % · forged frames accepted: 0 |
| 6 | Relief camp | accidental | camp · day | Both stay high (≈ 98–100 %). The point: the AI does not over-react. |
| 7 | Drone delivery | follower | desert · sunset | Fixed hopping ≈ 10–15 % · ABHEDYA ≈ 99 % |

While a scenario runs: a briefing card (story, what to watch, how the AI responds), a caption bar that narrates each step
with a timeline, and **Pause / 1×-2×-4× / Replay / Exit** (Space pauses). At the end a report compares AI OFF and AI ON
(delivery under attack, outage, hops, hop latency, forged frames accepted) and lists what the AI did, with **Next scenario**.

Scenarios are data, not code: edit `shared/scenarios.json` (routes, jammers, timings, captions). `tests/test_scenarios.py`
checks that every scenario loads, narrates every step, reports, and that AI ON beats AI OFF.

## 3D view

Open `http://localhost:8000/view/`. It shows:

- **Scene:** night or day sky, terrain with forests, a lake and mountains, the arena's buildings and hill, and the vehicles' roads.
- **Units:** drones with spinning rotors and status LEDs, vehicles, the command post, and relay drones.
- **Links and packets:** links coloured by health, with packets flowing to the command post (red ones are dropped).
- **Jammers and the AI's estimate:** each jammer has an energy-shield dome sized to its reach and a ground heat map. The AI's estimate is a yellow ring sized by its uncertainty.
- **Panels:** KPIs, legend, security, live decisions, the CNN's probabilities with Grad-CAM, and the spectrum with a waterfall.
- **Controls:** pick a jammer type and click the ground to launch it. AI ON, AI OFF and **Side by side** (two viewports in one renderer: AI OFF left, AI ON right, each with its own KPI strip). Day/night toggle.

Drag to orbit, scroll to zoom, right-drag to pan. Units are drawn about 14× real size so they can be read from 1 km away.

## Layout

```
backend/   FastAPI server, sign-in (auth.py, users.py), command models, World interface, fake world, debug console
shared/    schema.json, the single message contract
sim/       radio world: GNU Radio chain + NumPy fallback, jammers, baselines   (Phase 1)
ai/        data generator, CNN training/inference, defender, localisation, classifier stress test (Phase 2, v1.0)
viewer3d/  Three.js 3D view, vendored, offline                               (Phase 5)
intro/     mission deck: 3D voxel worlds, one slide per mission (React + three.js) (Phase 11)
mobile/    React PWA built with Vite, served at /app/                         (Phase 5)
hil/       radio interface, bench test, measured-vs-predicted (not yet run on hardware) (Phase 10)
recordings/ spectrum recordings for replay (the shipped ones are simulator captures)  (Phase 9)
tests/     pytest (200+ tests; run on every push by .github/workflows/ci.yml)
scripts/   preflight.py (check_demo.bat), plot_benchmark.py, truck_speed_check.py, ws_probe.py
docs/      evidence, benchmark, robustness, security, architecture, demo script, hardware test plan
data/      created on first start: accounts (git-ignored, never committed)
```

To plug in the real simulation, implement `backend/world_api.World` (`hello_info`, `step`, `handle`)
in `sim/world.py` as `SimArena`, then run with `AEGIS_WORLD=sim`. The server, screens and tests stay
the same.

## Build phases

| Phase | Content | Owner | Status |
|---|---|---|---|
| 0 | Skeleton, schema, server, fake world | Kodandarama | ✅ |
| 1 | GNU Radio (software cross-check) + NumPy radio world, 7 jammers, baselines | Kodandarama | ✅ |
| 2 | Data + CNN, intelligent hopping, Grad-CAM, localisation | Vishwas | ✅ |
| 3 | AES-256-GCM, keyed hop sequence, security tests | Neha + Vishwas | ✅ |
| 4 | Twin worlds (AI OFF/ON) + benchmark vs fixed hopping | Vishwas + Kodandarama | ✅ |
| 5 | 3D view, React PWA, judges' screen, QR join | Akshatha, Neha | ✅ |
| 6 | Stretch (scenarios, smart jammer, RL) + hardening | all | ✅ |
| 7 | Hardening, demo script | all | ✅ |
| 8 | What-if handling: battery, unknown jammer, sync loss, survival mode, hybrid AI, safety guard | all | ✅ |
| 9 | Truck trips and base camps, real-spectrum replay, mission log | all | ✅ |
| 10 | Review fixes A–J: keys, time sync, multi-jammer, referee, QoS, calibration, localisation check, statistics, hardware test kit | all | ✅ (hardware run pending) |
| 11 | ABHEDYA: mission deck, rebrand, downloadable report, 10× speed | all | ✅ |
| 12 | v1.0: sign-in and roles, stronger baselines + paired statistics, classifier stress test, evidence doc, CI, demo pre-flight check | all | ✅ |
