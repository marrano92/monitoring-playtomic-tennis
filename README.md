# Monitoring Playtomic — Club Ambrosiano Tennis

Controlla ogni ~30 minuti la disponibilità dei campi su Playtomic e avvisa via
**email**, **Telegram** e opzionalmente **WhatsApp** quando si libera uno slot
nelle fasce orarie che ti interessano. Zero dipendenze Python (solo stdlib,
Python ≥ 3.9).

Il controllo usa la vista anonima pubblica di `playtomic.com`. La vista soci via
login (`api.playtomic.io`) è attualmente **non disponibile**: quell'API risponde
403 a qualunque client non ufficiale, quindi il monitor degrada sempre alla
vista pubblica (che espone comunque gli stessi slot prenotabili).

> **Perché serve il relay Cloudflare.** Da ~luglio 2026 la CloudFront WAF di
> Playtomic risponde **403** agli IP datacenter dei runner GitHub. Gli endpoint
> pubblici funzionano solo da IP residenziali / non-datacenter. Il monitor
> instrada quindi le due GET pubbliche attraverso un **Cloudflare Worker relay**
> (`PLAYTOMIC_BASE`), il cui IP egress non è bloccato. Vedi `relay/` e il §4.

## Cosa monitora (config.json)

- Club: Club Ambrosiano Tennis (`tenant_id` già configurato)
- Sport: TENNIS, tutti i campi (`"courts": []`; per filtrare: `["Campo 5", "Campo 6"]`)
- Prossimi 7 giorni (`days_ahead`)
- Fasce orarie (`watch_windows`, ora italiana, inizio slot in `[from, to)`):
  - lun–ven 18:00–21:00
  - sab–dom 09:00–12:00
  - tutti i giorni 07:00–09:00

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

### 1-bis. (Opzionale) WhatsApp via CallMeBot

1. Aggiungi ai contatti il numero **+34 611 01 16 37** (nome a piacere; numero
   aggiornato su [callmebot.com](https://www.callmebot.com/blog/free-api-whatsapp-messages/))
2. Mandagli su WhatsApp il messaggio: `I allow callmebot to send me messages`
3. Ricevi in risposta `API Activated for your phone number. Your APIKEY is …`
   (se non arriva entro 2 minuti, riprova dopo 24h)

Ogni canale è indipendente: se i suoi secrets mancano viene saltato con un
warning, gli altri funzionano comunque.

### 2. Gmail app password

1. Vai su [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   (serve la verifica in 2 passaggi attiva)
2. Crea una password per l'app (nome libero, es. "playtomic monitor")
3. Conserva la password di 16 caratteri generata

### 3. Repo GitHub e secrets

```bash
git init && git add -A && git commit -m "feat: playtomic slot monitor"
gh repo create monitoring-playtomic-tennis --private --source=. --push
```

Poi in **Settings → Secrets and variables → Actions** aggiungi:

| Secret | Valore |
|---|---|
| `GMAIL_USER` | il tuo indirizzo Gmail |
| `GMAIL_APP_PASSWORD` | la app password di 16 caratteri |
| `MAIL_TO` | destinatario (opzionale, default = GMAIL_USER) |
| `TELEGRAM_BOT_TOKEN` | il token del bot da @BotFather |
| `TELEGRAM_CHAT_ID` | il tuo chat id (vedi sopra) |
| `PLAYTOMIC_RELAY_TOKEN` | token condiviso del relay Worker (vedi §4) |
| `PLAYTOMIC_EMAIL` | (opzionale, oggi inutile) email account Playtomic |
| `PLAYTOMIC_PASSWORD` | (opzionale, oggi inutile) password Playtomic |
| `CALLMEBOT_PHONE` | (opzionale) il tuo numero con prefisso, es. `+39333...` |
| `CALLMEBOT_APIKEY` | (opzionale) la apikey ricevuta da CallMeBot |

### 4. Relay Cloudflare Worker (obbligatorio per GitHub Actions)

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

Il Worker inoltra solo i path `/api/clubs/availability` e `/clubs/` e richiede il
token via header `X-Relay-Token`, così l'URL pubblico non è un proxy aperto.
In locale non serve: senza `PLAYTOMIC_BASE` il monitor va diretto a `playtomic.com`.

Il workflow parte da solo ogni ~30 minuti (i cron di GitHub possono ritardare
di qualche minuto). Per un test immediato: tab **Actions → playtomic-monitor →
Run workflow**.

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
