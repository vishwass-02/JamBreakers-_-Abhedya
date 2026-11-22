# Security

ABHEDYA has two separate security layers:

1. **The radio network**: what stops a jammer or an enemy radio from reading, forging or replaying traffic
   between the drones, the trucks and the command post.
2. **The demo app**: who may watch the mission on a phone or a second screen, and who may control it.

All cryptography uses the audited `cryptography` library. The project implements no ciphers of its own.

## 1. The radio network (simulated radios, real cryptography)

Every packet in the simulation is a real encrypted frame. It is sealed at the sending node and verified at the
command post. Details: `sim/security.py` and `sim/keymgmt.py`.

| Threat | Defence | Tested in |
|---|---|---|
| Eavesdropping | AES-256-GCM on every frame (32 bytes of telemetry, 16-byte tag) | `tests/test_security.py` |
| Forged frames (deceptive jammer) | GCM tag must verify under the sender's session key; `frames_forged_accepted` stays 0 | `test_security.py`, benchmark |
| Replay | 64-frame replay window per sender, plus the epoch in the authenticated header | `test_security.py` |
| Predicting the hop pattern | Hop sequence = HMAC of the slot under a per-epoch key (HKDF-SHA256, new every 60 s) | `test_security.py` |
| Forged channel-switch orders | Orders signed with the command post's Ed25519 key; members cannot sign | `tests/test_keymgmt.py` |
| Captured radio | Revocation: its session is deleted and a new group key goes to the others only | `test_keymgmt.py` |
| Impersonation by a captured radio | Each radio has its own device key and session key; D3 cannot send as D1 | `test_keymgmt.py` |
| Nonce reuse after a reboot | A rejoining radio gets a fresh session salt, so nonce (src, epoch, seq) never repeats under one key | `test_keymgmt.py` |

**Limits.** In the simulation every random value comes from the seed, so the twin worlds and the benchmark replay
identically. On real radios, keys come from a hardware random generator and a key loader. The key-derivation
labels still read "AEGIS-LINK" (the project's name before the ABHEDYA rename). They are fixed protocol constants,
and changing them would change every key.

## 2. The demo app: sign-in

**Before (until v1.0):** anyone on the same Wi-Fi who opened the address could watch the mission. They could also
launch jammers, switch the AI off or restart the mission.

**Now:**

| | |
|---|---|
| Accounts | User ID + password, stored in `data/users.json` (git-ignored) as a salted PBKDF2-SHA256 hash, 260,000 rounds. Passwords are never stored or logged. |
| Roles | **commander**: may control missions, jammers and the AI. **observer**: watch only (judges, teammates). The server checks the role on every command, not just the screen. |
| Joining from a phone | The laptop's QR code carries a **join code that changes every 15 minutes**. A phone needs that code *and* a user ID and password: something only visible in the room, plus something only the user knows. |
| Sessions | Random 256-bit tokens, kept in server memory, valid for 12 hours. They end on sign-out, on a password change or when the server restarts. |
| Brute force | 5 wrong passwords for one user from one device lock that pair out for 60 s. Unknown user IDs take the same time to reject as wrong passwords, so user IDs cannot be guessed by timing. |
| What is protected | Every `/api/*` call, every download, and the live WebSocket. A connection without a valid session is closed with code 4401 and the screen goes to the sign-in page. |
| Audit | The server window logs every sign-in (or refusal) and every command, with who sent it. |
| Headers | `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN`. The join code travels in the URL fragment (`#j=`), which browsers never send to a server or a proxy. |
| The demo laptop | Requests from the laptop itself (127.0.0.1) are trusted as the operator's console, so presenting needs no sign-in. |
| Other websites | A WebSocket or sign-in request opened by a page from another site is refused (Origin check). Without it, any web page open on the demo laptop could connect to `ws://localhost:8000` and command the demo as the trusted laptop (cross-site WebSocket hijacking). |
| Tokens in logs | Session tokens are replaced by `***` in every server log line, because the server window is often on the projector. |
| Limits | Messages over 16 KB are refused, a screen may send at most 20 commands a second, sign-in requests over 4 KB are refused. Refused commands are logged with the account that sent them. |

### First start

On first start the server creates two accounts with random 12-character passwords (you can change them to simpler
ones for a demo on your own hotspot; the QR join code and the lockout still apply). It prints them in the server
window and saves them in `data/FIRST_LOGIN.txt`. Note them down, then delete that file.

```
python -m backend.users list                      # who has access
python -m backend.users add neha --role observer   # asks for the password twice (min. 8 characters)
python -m backend.users passwd commander           # change a password (signs that user out everywhere)
python -m backend.users remove neha
```

### Remaining risks (be ready for these questions)

- **Plain http on the Wi-Fi.** Over `http://` the password crosses the network unencrypted. On your own phone
  hotspot that risk is small. On shared Wi-Fi, run with a certificate: put `cert.pem` and `key.pem` in `data/`
  (for example made with `mkcert`) and `run_windows.bat` switches to HTTPS automatically. Phones will warn about
  a self-made certificate unless it is installed on them.
- **Sessions live in memory.** Restarting the server signs everyone out. This is deliberate: nothing to steal
  from disk.
- **The laptop is trusted.** Anyone with hands on the demo laptop is the commander. Lock it when you step away.
- **Sign-in can be switched off** with `AEGIS_AUTH=0` (the automated tests do this). `check_demo.bat` warns if it is off.
