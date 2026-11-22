# Evidence: what we claim, what backs it, and where it stops

Everything below comes from **software simulation**. No radio hardware was used. Every number can be reproduced
with the command next to it. The results are deterministic: the same seed gives the same number. We list the
limits next to each claim, so a reviewer does not have to find them for us.

## 0. Key numbers (every other document uses these)

"Accuracy" and "latency" each mean several different measurements in this project. This table names each one,
gives its value and the file it comes from. `tests/test_docs_consistency.py` fails if the README, the demo script
or this page disagree with the result files.

| Figure | Value | Exactly what it measures | Source |
|---|---|---|---|
| Delivery under attack, **ABHEDYA AI** (CNN defender) | **94.8 %** (CI 93.9–95.6) | messages delivered after the jammer starts, mean of 90 runs | `benchmark_results.json`, `aegis` |
| Delivery under attack, **AI ON in the demo** (CNN + RL hybrid) | **95.1 %** (CI 94.3–96.0) | same, for the hybrid the demo uses as AI ON | `benchmark_results.json`, `hybrid` |
| Delivery under attack, **AI OFF** (fixed hopping) | **54.0 %** (CI 50.3–57.7) | same, conventional fixed hopping | `benchmark_results.json`, `fixed_hopping` |
| Gain of the AI over rules with the same tools | **+3.2 points** (paired CI +2.2 to +4.2) | same seed and attack, AI minus `afh_power`, 90 pairs | `benchmark_results.json`, `paired` |
| CNN accuracy, **held-out test set** | **95.4 %** | 1,680 spectrogram windows captured from live runs, never used in training | `ai/weights/report.json` |
| CNN accuracy, **benchmark decisions, pooled** | **95.4 %** | every CNN verdict during the benchmark counts once | `benchmark_results.json`, `aegis` |
| CNN accuracy, **per jammer type** (each type weighted equally) | **79.1 %** | mean of the per-type accuracies over 9 jammers × 5 seeds | `truck_speed_results.json` |
| CNN accuracy, fresh windows through start-up calibration | **98.0 %** | 560 new synthetic windows, unseen seed | `cnn_stress_results.json`, `clean` |
| Hop latency, ABHEDYA AI (benchmark) | **p50 20 ms, p95 50 ms** | first jammed slot until the whole network is on the new channel | `benchmark_results.json`, `aegis` |
| Hop latency, AI ON hybrid (benchmark) | **p50 20 ms, p95 110 ms** | same, hybrid | `benchmark_results.json`, `hybrid` |
| Response latency, hybrid, all stressed conditions | **p50 30 ms, p95 150 ms, p99 550 ms** | first jammed slot until any countermeasure is in force, 1,136-run robustness sweep | `robustness_results.json` |
| CNN inference time | **1.9 ms** per window (NumPy, laptop CPU) | median | `ai/weights/report.json` |

## 1. Headline

> Against nine jammer types, the ABHEDYA AI keeps **94.8 %** of messages flowing during an attack
> (95 % CI 93.9–95.6), against **54.0 %** for conventional fixed hopping. It beats the strongest *non-AI*
> defence we could build by **+3.2 points** (paired 95 % CI +2.2 to +4.2, 68 wins / 11 ties / 11 losses
> in 90 identical runs), while using **less than half its extra transmit power**.

`python -m sim.benchmark --seeds 10 --duration 20` · 810 runs · results in `docs/benchmark_results.json`

## 2. Is it intelligence, or just shouting louder?

The AI may hop, blacklist channels, raise power (up to +9 dB) and switch to a slower, more robust modulation
(BPSK). Fixed hopping can do none of that. Comparing only against fixed hopping would therefore flatter the AI.
So the benchmark also includes defences that have **the same tools without the AI**:

| Defence (10 seeds × 9 jammers, 20 s each) | Delivered | 95 % CI | Worst jammer | Outage | Extra power | Time at slow rate |
|---|---|---|---|---|---|---|
| No defence | 38.9 % | 35.6–42.2 | 14.3 % | 12.1 s | 0 dB | 0 % |
| Fixed hopping (conventional FHSS) | 54.0 % | 50.3–57.7 | 18.9 % | 9.0 s | 0 dB | 0 % |
| Energy detector (hop to quietest channel) | 49.0 % | 45.8–52.2 | 16.4 % | 10.0 s | 0 dB | 0 % |
| Secret fast hopping (keyed FHSS, the standard military baseline) | 67.8 % | 64.8–70.7 | 18.9 % | 6.5 s | 0 dB | 0 % |
| **Brute force**: fixed hopping at full power + BPSK, always | 88.8 % | 86.5–91.0 | 64.4 % | 2.3 s | 9.0 dB | 100 % |
| **Rules, no CNN**: AFH blacklisting → power → BPSK, by fixed rules | 91.6 % | 90.5–92.7 | 85.6 % | 1.7 s | 6.5 dB | 65 % |
| RL agent (tabular Q-learning) | 94.3 % | 93.6–95.1 | 85.5 % | 1.1 s | 5.8 dB | 60 % |
| **ABHEDYA AI** (CNN classifier + per-type response) | **94.8 %** | 93.9–95.6 | **86.6 %** | **0.9 s** | **2.9 dB** | **40 %** |
| ABHEDYA + RL (hybrid) | 95.1 % | 94.3–96.0 | 86.8 % | 0.8 s | 3.6 dB | 40 % |

*Outage* is the longest stretch with nothing delivered, averaged over runs. *Extra power* is the average boost
over nominal while jammed, in dB. *Time at slow rate* is the share of jammed time spent on BPSK, at half the
data rate.

**Paired comparison.** Same seed, same jammer, same position: only the defence differs. Each line is 90 pairs.

| ABHEDYA AI vs | Mean gain | 95 % CI | AI wins / ties / losses |
|---|---|---|---|
| Fixed hopping | +40.8 pts | +34.1 to +47.4 | 90 / 0 / 0 |
| Secret fast hopping | +27.0 pts | +21.0 to +33.0 | 90 / 0 / 0 |
| Brute force | +6.0 pts | +3.6 to +8.4 | 51 / 8 / 31 |
| **Rules, no CNN** (ablation) | **+3.2 pts** | **+2.2 to +4.2** | **68 / 11 / 11** |
| RL agent | +0.4 pts | −0.2 to +1.1 | *not significant* |
| Hybrid (ABHEDYA + RL) | −0.4 pts | −0.6 to −0.1 | 2 / 77 / 11 |

What this shows:

- **Most of the gain over fixed hopping comes from the toolbox** (power, modulation, blacklisting), not from
  the AI. Brute force alone reaches 88.8 %. We say this openly.
- **The classifier adds a real, statistically significant gain on top of the same toolbox**: +3.2 points over
  rule-based control, with a confidence interval that excludes zero. It also halves the outage (0.9 s vs 1.7 s).
- **It is efficient.** Brute force burns full power and half data rate all the time. The AI uses on average
  2.9 dB of extra power (about 1.9× nominal), against 6.5 dB (4.5×) for the rules and 9 dB (7.9×) for brute force.
  It is at the slow rate 40 % of the time, against 65 % and 100 %. Less power means longer battery life and a
  radio that is harder to detect.
- **Against the RL agent it is a tie on delivery.** The CNN's advantages there are lower power (2.9 vs 5.8 dB),
  a faster response (p95 hop 50 ms vs 160 ms) and a decision a human can read ("CNN sees sweep (97 %)").

Where the AI is **not** best:

- **Novel jammer** (a comb pattern the CNN never saw): brute force 90.8 %, rules 88.6 %, AI 86.6 %.
- **Smart jammer** (switches tactic every second): RL 94.8 %, AI 90.2 %. The hybrid, which hands over to RL
  when stuck, recovers to 93.1 %.

## 3. Does the classifier work on data it was not trained on?

The CNN is trained on simulated spectrograms. A model tested on data from the same generator can look better than
it is, so `python -m ai.stress_test` draws fresh windows from a seed never used in training. It then damages them
the way a real receiver could, and none of this damage appears in training. 560 windows per condition. Each
condition is scored raw and through the start-up calibration the system uses on a real receiver
(`ai/calibration.py`).

| Receiver problem | Raw | With start-up calibration | Weakest class (calibrated) |
|---|---|---|---|
| None (fresh windows) | 97.7 % | 98.0 % | deceptive 93.8 % |
| Noise floor 6 dB higher | 40.0 % | 93.4 % | deceptive 82.5 % |
| Noise floor 12 dB higher | 17.1 % | 82.9 % | reactive 62.5 % |
| Gain 6 dB too high | 22.1 % | 98.0 % | deceptive 93.8 % |
| Gain 6 dB too low | 88.4 % | 98.0 % | deceptive 93.8 % |
| Tuning off by 1 bin (0.6 MHz) | 91.6 % | 91.8 % | clean 58.8 % |
| Tuning off by 3 bins (1.9 MHz) | 93.6 % | 93.4 % | deceptive 81.2 % |
| 3 of 12 frames dropped | 65.7 % | 81.6 % | reactive 42.5 % |

What this shows:

- **Without calibration the classifier is fragile.** It reads absolute power levels, so a noisier or hotter
  receiver looks like a jammed band. **The start-up calibration is essential, not optional**: one second of a
  quiet band through the same receiver removes gain errors completely and most of a raised noise floor.
- **+12 dB of noise is a real loss** (83 %): weak jammers drop below the noise. No correction can recover that
  information.
- **Tuning error** of 260–780 ppm is far beyond a normal radio (a TCXO SDR is about 2 ppm). Even so, accuracy holds
  at 92–93 %. At 1 bin our own signal starts to look like an off-channel impostor, so "clean" drops to 59 %.
- **Dropped frames** were the worst problem. A lost frame looked like a random jammer switching off ("random"
  accuracy 2.5 %). Real radios report dropped frames (UHD and SoapySDR overflow flags), so the calibration now
  fills a reported gap from the neighbouring frame (81.6 % overall). Pulsed (reactive) jamming is still the weak
  class in that case.

## 4. Two ways to count "accuracy"

The benchmark reports **95.4 %** classification accuracy. That is *pooled*: every CNN decision counts once, so
the common jammer types dominate. With **every jammer type weighted equally** it is **79.1 %**. Deceptive,
accidental and follower jammers are identified correctly only 57–63 % of the time.
(`python scripts/truck_speed_check.py`, 5 seeds × 9 jammers)

**Delivery does not depend on the label being right.** The responses to those types overlap: a mislabelled
follower still gets keyed fast hopping. Delivery stays at 95 % either way.

**Truck speed.** In the live demo the convoy drives at 72 / 58 km/h, while the benchmark uses 32 / 25 km/h.
At demo speeds, delivery is unchanged (95.1 % vs 95.0 %). Pooled accuracy falls from 95.3 % to 92.4 %, and
per-type accuracy from 79 % to 68 %. The fast-moving links change faster than the CNN's 0.25 s windows can
average out. The published benchmark keeps the slower speeds its numbers were measured at.

## 5. Robustness, unseen jammers, latency

Covered in [`ROBUSTNESS.md`](ROBUSTNESS.md): 1,136 runs changing one condition at a time (jammer power and
distance, drone speed, network size, background interference, stacked jammers, two unseen jammer patterns).
AI ON beats AI OFF in all 71 cells. The weakest single case is a 46 dBm sweep (AI ON 50.7 %). Stacked
random + follower attacks are the hardest (61 %).

## 6. What we have *not* shown

- **No real radio.** The physics model has been cross-checked against a GNU Radio signal chain (`run_gnuradio.bat`,
  Phase 1), but there is no over-the-air measurement. `docs/HARDWARE_TEST_PLAN.md` and `hil/` contain a test kit
  ready for two SDRs. The SDR backend itself is not written yet.
- **Jammer models are ours.** The jammers, the channel model and the training data all come from the same
  simulator. Section 3 tests the classifier against distortions outside training, but a real adversary may
  behave in ways none of our models capture.
- **No comparison with fielded military systems** (for example SINCGARS or Link-16 anti-jam modes). Their
  details are not public. "Secret fast hopping" is our stand-in for that class of defence.
- **Classification of rare types is weak** (section 4). We report it rather than averaging it away.

## Reproduce everything

| What | Command | Output | Time on a laptop |
|---|---|---|---|
| Benchmark (all 9 defences) | `python -m sim.benchmark --seeds 10 --duration 20` | `results/benchmark_latest.json` | about 12 min |
| Chart | `python scripts/plot_benchmark.py results/benchmark_latest.json` | `docs/benchmark_chart.png` | seconds |
| Classifier stress test | `python -m ai.stress_test` | `docs/cnn_stress_results.json` | about 1 min |
| Truck speed check | `python scripts/truck_speed_check.py` | `docs/truck_speed_results.json` | about 3 min |
| Robustness sweep | `python -m sim.robustness --seeds 8 --duration 12` | `docs/robustness_results.json` | about 40 min |
| All tests | `pytest -q` | 200+ tests | about 4 min |
