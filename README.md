# Monitoring Playtomic — Club Ambrosiano Tennis

Controlla ogni ~30 minuti la disponibilità dei campi su Playtomic e avvisa via
**email**, **Telegram** e opzionalmente **WhatsApp** quando si libera uno slot
nelle fasce orarie che ti interessano. Zero dipendenze Python (solo stdlib,
Python ≥ 3.9).

Se configuri le credenziali Playtomic (`PLAYTOMIC_EMAIL`/`PLAYTOMIC_PASSWORD`)
il controllo avviene **da account loggato**: vede anche i giorni in prelazione
soci non ancora aperti al pubblico. Senza credenziali (o se il login fallisce)
degrada automaticamente alla vista anonima pubblica.

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
| `PLAYTOMIC_EMAIL` | (opzionale) email account Playtomic per la vista soci |
| `PLAYTOMIC_PASSWORD` | (opzionale) password Playtomic — inseriscila SOLO qui |
| `CALLMEBOT_PHONE` | (opzionale) il tuo numero con prefisso, es. `+39333...` |
| `CALLMEBOT_APIKEY` | (opzionale) la apikey ricevuta da CallMeBot |

Nota sul login Playtomic: il cron a 30 minuti è scelto apposta per tenere basso
il volume di login automatici (~48/giorno). Non abbassarlo con le credenziali
configurate: pattern di login più fitti da IP datacenter possono far scattare
i sistemi anti-bot ed esporre l'account a blocchi (ed è comunque un uso non
previsto dai ToS di Playtomic).

Il workflow parte da solo ogni ~30 minuti (i cron di GitHub possono ritardare
di qualche minuto). Per un test immediato: tab **Actions → playtomic-monitor →
Run workflow**.

## Prenotazioni → Google Calendar

Un secondo workflow (`playtomic-bookings`, una volta al giorno alle ~06:30
italiane) legge le prenotazioni del tuo account Playtomic e per ogni nuova
prenotazione manda a `MAIL_TO` una **email con markup schema.org
(EventReservation)**: Gmail la riconosce come conferma di prenotazione e
mostra la card con campo, orario e club proponendoti di aggiungerla al
calendario (o la aggiunge da solo, vedi sotto). Richiede i secrets
`PLAYTOMIC_EMAIL`/`PLAYTOMIC_PASSWORD` + quelli Gmail già configurati;
nessuna API Google da attivare.

- Il markup viene processato da Google **senza registrazione del mittente**
  solo per email inviate a sé stessi: `MAIL_TO` deve essere lo stesso
  indirizzo di `GMAIL_USER` (o non impostato, che è la stessa cosa).
- L'aggiunta automatica dipende da Google Calendar → Impostazioni →
  "Eventi da Gmail" → "Mostra eventi da Gmail": se attiva l'evento si crea da
  solo, altrimenti resta la card nella mail con l'azione per aggiungerlo.
- Le prenotazioni già inviate sono tracciate in `calendar_state.json`
  (committato dal workflow). Le cancellazioni non vengono sincronizzate:
  l'evento resta sul calendario e va tolto a mano.

## Test in locale

```bash
python3 monitor.py --selftest              # verifica fasce orarie + formato ICS
python3 monitor.py --dry-run               # interroga l'API e stampa cosa notificherebbe
python3 monitor.py --sync-bookings --dry-run  # stampa le prenotazioni che invierebbe
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
