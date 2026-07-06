# Monitoring Playtomic — Club Ambrosiano Tennis

Controlla ogni ~10 minuti la disponibilità dei campi su Playtomic e avvisa via
**email** e **WhatsApp** quando si libera uno slot nelle fasce orarie che ti
interessano. Usa l'API pubblica di Playtomic (nessuna autenticazione), zero
dipendenze Python (solo stdlib, Python ≥ 3.9).

## Cosa monitora (config.json)

- Club: Club Ambrosiano Tennis (`tenant_id` già configurato)
- Sport: TENNIS, tutti i campi (`"courts": []`; per filtrare: `["Campo 5", "Campo 6"]`)
- Prossimi 7 giorni (`days_ahead`)
- Fasce orarie (`watch_windows`, ora italiana, inizio slot in `[from, to)`):
  - lun–ven 18:00–21:00
  - sab–dom 09:00–12:00
  - tutti i giorni 07:00–09:00

## Setup (una tantum)

### 1. WhatsApp via CallMeBot (gratuito)

1. Aggiungi ai contatti il numero **+34 611 01 16 37** (nome a piacere; numero
   aggiornato su [callmebot.com](https://www.callmebot.com/blog/free-api-whatsapp-messages/))
2. Mandagli su WhatsApp il messaggio: `I allow callmebot to send me messages`
3. Ricevi in risposta `API Activated for your phone number. Your APIKEY is …`
   (se non arriva entro 2 minuti, riprova dopo 24h)

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
| `CALLMEBOT_PHONE` | il tuo numero con prefisso, es. `+39333...` |
| `CALLMEBOT_APIKEY` | la apikey ricevuta da CallMeBot |

Il workflow parte da solo ogni ~10 minuti (i cron di GitHub possono ritardare
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
