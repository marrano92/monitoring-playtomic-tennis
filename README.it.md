# Monitoring Playtomic — Club Ambrosiano Tennis

Controlla ogni ~30 minuti la disponibilità dei campi su Playtomic e avvisa su
**Telegram** quando si libera uno slot nelle fasce orarie che ti interessano.
Zero dipendenze Python (solo stdlib, Python ≥ 3.9).

Il controllo usa la vista pubblica di `playtomic.com`, che espone solo i prossimi
**~3 giorni**. Con una sessione **soci** (cookie `pt_auth_access_token`) lo stesso
endpoint restituisce **~10 giorni**: il monitor la ottiene rinnovando il token in
un **browser headless** ad ogni run (vedi §4, opzionale). Senza sessione soci
degrada in automatico alla vista pubblica, senza errori.

> **Perché serve il relay Cloudflare.** Da ~luglio 2026 la CloudFront WAF di
> Playtomic risponde **403** agli IP datacenter dei runner GitHub. Gli endpoint
> pubblici funzionano solo da IP residenziali / non-datacenter. Il monitor
> instrada quindi la GET di disponibilità attraverso un **Cloudflare Worker relay**
> (`PLAYTOMIC_BASE`), il cui IP egress non è bloccato. Vedi `relay/` e il §3.

## Cosa monitora (config.json)

- Club: Club Ambrosiano Tennis (`tenant_id` già configurato)
- Sport: TENNIS, tutti i campi (`"courts": []`; per filtrare: `["Campo 5", "Campo 6"]`)
- Prossimi 7 giorni (`days_ahead`)
- Fasce orarie (`watch_windows`, ora italiana, inizio slot in `[from, to)`):
  - lun–ven 18:00–21:00
  - sab–dom 09:00–12:00
  - tutti i giorni 07:00–09:00

## Com'è fatta la notifica

Un solo messaggio Telegram, raggruppato per **giorno → orario di inizio**: i campi
liberi alla stessa ora finiscono sulla stessa riga (con la superficie in comune
scritta una volta sola), così anche 40 slot restano poche righe. L'intestazione di
ogni giorno è un link diretto a quel giorno su Playtomic.

```
🎾 Club Ambrosiano Tennis
9 slot liberi · 3 giorni

📅 lunedì 27/07 · prenota
    18:30 · 60 min · Campo 1, Campo 2 (terra)
    18:30 · 90 min · Campo 3 (terra)
    19:00 · 60 min · Campo 5 (terra)

📅 martedì 28/07 · prenota
    09:00 · 60 min · Campo 7, Campo 8 (terra)
```

Se il messaggio supera il limite di Telegram (4096 caratteri) la coda viene
troncata e sostituita da `… e altri N slot`.

## Setup (una tantum)

### 1. Telegram (gratuito, consigliato)

1. Su Telegram cerca **@BotFather** → `/newbot` → scegli nome e username del
   bot → ricevi il **token** (formato `123456789:AAF...`)
2. Apri la chat col tuo nuovo bot e mandagli un messaggio qualsiasi (es. `/start`)
3. Recupera il tuo **chat_id**:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool | grep -A2 '"chat"'
   ```
   Il numero in `"id"` è il tuo `TELEGRAM_CHAT_ID`.

Telegram è l'**unico** canale: se `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` mancano
il run fallisce di proposito senza scrivere lo stato, così gli slot vengono
rinotificati al giro dopo invece di sparire in silenzio.

### 2. Repo GitHub e secrets

```bash
git init && git add -A && git commit -m "feat: playtomic slot monitor"
gh repo create monitoring-playtomic-tennis --private --source=. --push
```

Poi in **Settings → Secrets and variables → Actions** aggiungi:

| Secret | Valore |
|---|---|
| `TELEGRAM_BOT_TOKEN` | il token del bot da @BotFather |
| `TELEGRAM_CHAT_ID` | il tuo chat id (vedi sopra) |
| `PLAYTOMIC_RELAY_TOKEN` | token condiviso del relay Worker (vedi §3) |
| `PLAYTOMIC_REFRESH_TOKEN` | (opzionale, vista soci) refresh token della sessione — vedi §4 |
| `GH_PAT` | (solo con §4) PAT fine-grained con *Secrets: write* su questo repo — vedi §4 |
| `PLAYTOMIC_COOKIE` | (opzionale) cookie soci manuale `pt_auth_access_token=…`, fallback della vista soci |

### 3. Relay Cloudflare Worker (obbligatorio per GitHub Actions)

Gli IP dei runner GitHub ricevono 403 da Playtomic; il monitor gira solo se le
richieste passano da un IP non-datacenter. Un Cloudflare Worker (piano gratuito,
100k richieste/giorno) fa da relay trasparente verso `playtomic.com`.

```bash
cd relay
npx wrangler deploy                       # crea il Worker, stampa l'URL *.workers.dev
npx wrangler secret put RELAY_TOKEN       # incolla un token casuale (es. openssl rand -hex 24)
```

Poi:

1. Metti l'URL del Worker in `PLAYTOMIC_BASE` nel workflow (`.github/workflows/monitor.yml`).
2. Aggiungi lo **stesso** token del punto sopra come secret GitHub `PLAYTOMIC_RELAY_TOKEN`.

Il Worker inoltra solo il path `/api/clubs/availability` (con l'eventuale header
`Cookie` per la vista soci) e richiede il token via header `X-Relay-Token`, così
l'URL pubblico non è un proxy aperto. In locale non serve: senza `PLAYTOMIC_BASE`
il monitor va diretto a `playtomic.com`.

Il workflow parte da solo ogni ~30 minuti (i cron di GitHub possono ritardare
di qualche minuto). Per un test immediato: tab **Actions → playtomic-monitor →
Run workflow**.

### 4. (Opzionale) Vista soci: refresh headless del token

La vista pubblica mostra ~3 giorni; quella soci ~10. Per attivarla il workflow
rinnova il token di sessione in un **Chromium headless** (Playwright) ad ogni run
— il refresh gira come JS su `app.playtomic.com/refresh`, quindi non è replicabile
con una semplice HTTP/relay. Playtomic **ruota** il refresh token ad ogni uso, così
il workflow **cattura quello nuovo e lo riscrive nel secret** (mai in git: il repo
è pubblico).

Setup:

1. **Refresh token**: fai login su `playtomic.com` nel browser → DevTools →
   Application → Cookies → copia il valore di **`pt_auth_refresh_token`**. Mettilo
   nel secret `PLAYTOMIC_REFRESH_TOKEN`.
2. **PAT per ripersistere il token ruotato**: crea un
   [fine-grained PAT](https://github.com/settings/tokens?type=beta) limitato a
   **questo solo repo**, permesso **Secrets: Read and write**. Mettilo nel secret
   `GH_PAT`.

Note operative:

- Il costo è ~30–60s a run per Chromium (browser in cache dopo il primo run).
- Se lo step di refresh fallisce (token invalidato, logout altrove, challenge
  anti-bot), il monitor **degrada alla vista pubblica** senza rompersi; basta
  reincollare un `pt_auth_refresh_token` fresco nel secret per riattivarlo.
- In alternativa al refresh automatico, puoi impostare solo `PLAYTOMIC_COOKIE`
  (`pt_auth_access_token=…`) a mano: dà la vista soci per ~1 h, poi va reincollato.

## Test in locale

```bash
python3 monitor.py --selftest   # verifica la logica delle fasce orarie
python3 monitor.py --dry-run    # interroga l'API e stampa cosa notificherebbe
```

## Note

- Alla **prima esecuzione** tutti gli slot liberi che rientrano nelle fasce
  vengono notificati (è la fotografia iniziale); dalle run successive arriva
  solo ciò che si libera di nuovo.
- Lo stato (slot già notificati) è in `state.json`, committato dal workflow
  a ogni variazione.
- GitHub disabilita i cron dei repo senza attività da 60 giorni: i commit di
  `state.json` di fatto lo tengono vivo, ma se sospendi il workflow ricordati
  di riattivarlo dalla tab Actions.
