---
titolo: Sweetlink parla con i server di Homeway: cosa cambia se smettiamo
stato: in-review
revisione: 2
in-carico-a:
passo-a:
issue: 5
aggiornato: 2026-08-22
---

# Sweetlink parla con i server di Homeway: cosa cambia se smettiamo

**In una riga:** il traffico degli hub si divide in due piani con esigenze opposte — controllo leggero
e media pesante — e trattarli separatamente risolve la dipendenza da terzi meglio di qualunque
sostituzione del tunnel.

## Sintesi

### I due piani

Tutto il ragionamento poggia su una separazione che dal codice è netta:

| Piano | Cosa contiene | Peso | Dove passa |
|---|---|---|---|
| **Controllo** | interfaccia HA, API, signaling WebRTC, Alexa, Google | testo, kilobyte | il tunnel |
| **Media** | video delle telecamere | megabyte al minuto | WebRTC: diretto o via TURN, **mai nel tunnel** |

La prova è nel sorgente di Home Assistant, `homeassistant/components/camera/webrtc.py:270`:

> *"The actual streaming is handled entirely between the client and camera device."*

Dal tunnel passano solo l'offerta SDP e i candidati ICE, sul WebSocket di HA. Il flusso video negozia
un percorso proprio verso `turn.<dominio>:3478` e non tocca l'hostname pubblicato dal tunnel.

Trattare i due piani come uno solo è ciò che rendeva il problema grande. Separati, ciascuno ha una
soluzione piccola.

### Cosa dipende ancora da Homeway

Dei 46 servizi remoti mappati leggendo il codice, **12 sono indispensabili** e, uniti i doppioni, si
riducono a due blocchi: il tunnel e Sage. Sage è fuori perimetro per decisione di prodotto e da solo
occupava sei delle dodici voci.

**Resta il tunnel, e nient'altro.**

Il vincolo che invece era grave — la registrazione degli hub a valle dell'handshake di Homeway — **è
rimosso**. Il reporter Sweetplace è stato spostato da `OnPrimaryConnectionEstablished` a
`RunBlocking`, prima dell'avvio della connessione remota. Non era una dipendenza reale: dei quattro
valori che spedisce (`macs`, `plugin_id`, `private_key`, `app_url`) nessuno proviene da Homeway, e la
funzione non usava né `apiKey` né `connectedAccounts`, cioè i due soli argomenti di provenienza
remota. Con lo spostamento è stato aggiunto un ciclo di ritentativi, perché all'avvio la rete può non
essere pronta e prima quella garanzia la dava implicitamente l'handshake.

Il resto dell'identità era già in casa: `plugin_id` e `private_key` nascono sul dispositivo
(`linuxhost.py:292` e `:306`), il claim passa dal token e dalla mail del backend Sweetplace
(`onboarding/src/index.ts:430`), il dominio è proprio.

### Alexa e Google non sono funzioni di Homeway

L'add-on non implementa gli assistenti: scrive in `configuration.yaml` le integrazioni **native** di
Home Assistant, `alexa: smart_home:` e `google_assistant:` (`configmanager.py:177-190`). Discovery,
esecuzione e stato li fa HA sui propri endpoint. Homeway è solo la porta pubblica, ed è sostituibile.

Riaccenderle su un altro canale richiede una skill Alexa Smart Home propria e una Action Google
propria: lavoro di piattaforma, con i tempi di approvazione di Amazon e Google, non lavoro tecnico.

Vincolo che il disegno deve rispettare: Amazon e Google sono client HTTP generici e non fanno login
interattivo. Qualunque protezione con SSO davanti agli hostname li romperebbe. Serve un percorso a
token separato.

### Cloudflare: cosa dicono davvero i contratti

Tre timori iniziali, verificati sulla documentazione ufficiale, e due cadono:

- **Costo del tunnel**: nullo. È *"Available on all plans"*, e non esiste alcun metering della banda
  documentato; la pagina della billing policy non tratta né banda né overage.
- **Limite dei 50 utenti Zero Trust**: non si applica. Un seat si consuma quando *"a user performs an
  authentication event"*, e la documentazione dice che *"you can use Access service tokens to allow
  access to applications without consuming seats"*. Oggi di seat se ne consumano **zero**. Superata la
  soglia, comunque, *"additional users who attempt to log in are blocked"*.
- **Divieto di rivendita di Zero Trust** (Service-Specific Terms §2.2, sanzione dichiarata: *"immediate
  termination of your account"*): non si applica, perché Zero Trust non viene usato. Il backend chiama
  tre soli endpoint Cloudflare — `cfd_tunnel`, le sue `configurations` e `dns_records` — e **nessuna
  API `access/*`**. Il cliente finale non riceve identità Cloudflare: si autentica a Home Assistant.

Resta **una sola** clausola viva, nei Service-Specific Terms della CDN: Cloudflare si riserva di
limitare l'accesso a chi la usa *"to serve video or a disproportionate percentage of pictures, audio
files, or other large files"*. Riguarda il traffico, non la configurazione, e non si evita per
disegno — ma il video, se va in WebRTC, dalla CDN non passa. È questo il punto in cui la separazione
dei due piani smette di essere teoria e diventa la ragione per cui Cloudflare resta praticabile.

### L'alternativa open source: cosa regge il vincolo Railway

Railway pubblica su Internet **solo HTTP/HTTPS**, più una singola porta TCP grezza via TCP Proxy;
la sua stessa documentazione di template dichiara *"Railway only proxies TCP publicly"*. Nessun UDP
pubblico, e i container non hanno `NET_ADMIN` né `/dev/net/tun`.

Sopravvive una sola famiglia: client che apre una WebSocket in uscita sulla 443, server che smista
per hostname. E dentro quella famiglia sopravvive un solo progetto: **frp** (Apache-2.0), perché
supporta `websocket`/`wss` come trasporto, sa collassare tutto su una porta sola, ha routing per
sottodominio nativo e, soprattutto, i **Server Manage Plugins** — RPC su HTTP sugli hook `Login` e
`NewProxy` con cui il backend Sweetplace autorizzerebbe ogni hub, che è il sostituto diretto delle API
di provisioning Cloudflare usate oggi.

I WebSocket su Railway *"are exempt from these duration and inactivity limits, and can stay open
indefinitely"*, e i wildcard domain sono supportati: le due condizioni necessarie ci sono entrambe.

Restano due riserve su frp, entrambe reali: è un **progetto a manutentore singolo** (1249 contributi
del primo, 20 del secondo, su 108.933 stelle), ed è ancora **0.x** dopo dieci anni.

### Il TURN, e perché non può stare su Railway

WebRTC da remoto ha bisogno di attraversare due NAT. La documentazione di go2rtc dice che per un
accesso esterno stabile *"you need to open the 8555 port on your router for both TCP and UDP"* — su un
prodotto consumer non è proponibile. Resta il TURN, che funziona con lo stesso principio del tunnel:
entrambi i capi si connettono **in uscita** a una macchina con IP pubblico, che rilancia.

coturn alloca i relay su un intervallo **UDP**: `--min-port` 49152, `--max-port` 65535 per default.
Railway di porte UDP pubbliche non ne espone. L'opzione `--no-udp-relay` esiste ma è inutile, perché
i relay TCP di RFC 6062 in WebRTC sono opzionali — RFC 8835: *"TURN TCP candidates... MAY be
supported. However, such candidates are not seen as providing any significant benefit"* — e i browser
non li implementano.

**Il TURN richiede quindi una VPS con IP pubblico e UDP.** Non è un ripiego: è anche l'unico posto
dove potrebbe stare, un domani, un tunnel self-hosted. Un pezzo di infrastruttura, due problemi
risolti. Fornitore previsto: Hetzner.

### La direzione

**Canale di controllo: Cloudflare Tunnel**, come già oggi, su dominio proprio, senza Access sugli
hostname dei clienti. Costa zero, non ha clausole applicabili, e il traffico che vi passa è leggero.

**Canale media: WebRTC**, con STUN e TURN propri. È ciò che tiene il video fuori dal tunnel e
disinnesca l'unica clausola rimasta.

**Il tunnel non si riscrive adesso.** Il caso per frp era il costo e i vincoli contrattuali di
Cloudflare: verificati, quei due argomenti sono caduti. Resta l'indipendenza in sé, che è un valore
reale ma non urgente, e che costa 5-8 settimane più manutenzione perpetua.

**Prima di tutto si misura.** Il numero che manca è uno solo: quanto spesso WebRTC ripiega sul relay
invece di andare diretto. Se i fallback sono rari, il TURN è marginale e Cloudflare basta. Se sono la
norma — tipico con CGNAT — il TURN diventa infrastruttura critica e va dimensionato.

## Configurazione di riferimento

Schemi di base della direzione scelta. Sono la configurazione bersaglio, non un runbook: i valori
vanno verificati sul campo.

### Architettura

```
BROWSER / APP                                         HUB (Raspberry Pi)
   │                                                       │
   │── HTTPS ─► xxxx.<dominio> ─► Cloudflare ─► cloudflared ─► HA :8123
   │            PIANO DI CONTROLLO: UI, API, signaling, Alexa, Google
   │            kilobyte
   │                                                       │
   │                   turn.<dominio>  (VPS Hetzner)       │
   │◄──── SRTP ────►   coturn, IP pubblico   ◄──── SRTP ───►│
   │                   PIANO MEDIA: video telecamere        │
   │            due connessioni USCENTI, nessuna porta aperta
```

Il video non viene mai indirizzato all'hostname del tunnel: va a `turn.<dominio>`, altra
destinazione, altro socket. Se ICE trova un percorso diretto, non tocca nemmeno il TURN.

### Piano di controllo — Cloudflare (già in produzione)

Il backend provisiona tutto via API con tre chiamate (`onboarding/src/index.ts`):

```
POST /accounts/{account}/cfd_tunnel                      crea il tunnel, restituisce il token
PUT  /accounts/{account}/cfd_tunnel/{id}/configurations  regole di ingress
POST /zones/{zone}/dns_records                           CNAME del sottodominio
```

Regola di ingress applicata (`index.ts:326`):

```json
{ "config": { "ingress": [
    { "hostname": "<sub>.<ACTIVE_DOMAIN>", "service": "http://127.0.0.1:8123" },
    { "service": "http_status:404" }
] } }
```

Il servizio è **Home Assistant diretto**, non l'ingress dell'add-on: è la ragione per cui Alexa e
Google possono funzionare su questo canale.

Sull'hub gira `cloudflared tunnel --no-autoupdate run --token <token>`, avviato da
`cloudflaremanager.py`, che chiede il token al backend passando il solo `plugin_id` e ritenta ogni
60 s finché non lo ottiene.

**Da non fare, per disegno:** nessuna Access application, nessuna policy, nessun service token sugli
hostname dei clienti. È ciò che tiene fuori la §2.2 e ciò che permette ad Alexa e Google di entrare.

### Piano media — coturn e Home Assistant

**Sulla VPS**, configurazione minima di coturn:

```conf
listening-port=3478
tls-listening-port=5349
min-port=49152            # intervallo UDP dei relay: va aperto sul firewall
max-port=65535
realm=<dominio>
fingerprint
lt-cred-mech              # credenziali a lungo termine, oppure use-auth-secret per token a scadenza
cert=/etc/letsencrypt/live/turn.<dominio>/fullchain.pem
pkey=/etc/letsencrypt/live/turn.<dominio>/privkey.pem
```

Porte da aprire: **3478 UDP e TCP**, **5349 TCP** per TLS, e l'intervallo **49152-65535 UDP**.
La tratta client→server può essere TCP o TLS — RFC 8835 le rende obbligatorie per i client WebRTC,
il che aiuta sugli hub con firewall domestici restrittivi. La tratta server→peer resta UDP.

**Sull'hub**, l'integrazione nativa di Home Assistant:

```yaml
web_rtc:
  ice_servers:
    - url:
        - "stun:turn.<dominio>:3478"
    - url: "turn:turn.<dominio>:3478"
      username: "<utente>"
      credential: "<segreto>"
```

**Questo blocco l'add-on lo scrive già.** `webrtcmanager.py:106-107` lo dichiara nel commento e usa il
formato dell'integrazione `web_rtc`; oggi riempie i valori con quelli scaricati da
`homeway.io/api/webrtc/config` (`webrtcmanager.py:68`), con cache locale in `webrtc_cache.json`.
Cambiare fornitore di TURN significa cambiare quella sorgente, non riscrivere il sottosistema.

Entrambi i capi ricevono gli stessi ICE server: HA li inoltra a go2rtc sull'hub, non solo al browser
— `homeassistant/components/go2rtc/__init__.py:344-345`:

```python
config = camera.async_get_webrtc_client_configuration()
await ws_client.send(WebRTCOffer(offer_sdp, config.configuration.ice_servers))
```

### Come si verifica che il video sia davvero fuori dal tunnel

1. TURN attivo sulla VPS, suo URL nel blocco `web_rtc:` di un hub di test.
2. Da fuori casa, aprire la telecamera nell'interfaccia di Home Assistant.
3. In `chrome://webrtc-internals`, leggere il **tipo della candidate pair selezionata**:
   `host`/`srflx` = collegamento diretto, TURN non usato; `relay` = passa dal TURN.
4. Nei log di coturn deve comparire un'allocation dall'IP dell'hub.
5. **Il contatore del traffico del tunnel deve restare piatto** mentre il video scorre. È il punto che
   dimostra la separazione dei due piani.
6. Ripetere su linee diverse — fibra, FWA, e una con CGNAT — e registrare la percentuale di sessioni
   che finiscono in `relay`. È il numero che dimensiona tutto il resto.

Attenzione: se WebRTC non si stabilisce, Home Assistant ripiega su HLS, che è HTTP servito da HA e
quindi **passa dal tunnel**. La misura del punto 6 serve anche a sapere quanto spesso accade.

## Cosa va costruito

1. ~~Sganciare la registrazione dall'handshake Homeway~~ — **fatto**, in review.
2. **Misurare il rapporto diretto/relay** su linee reali. Non richiede infrastruttura nuova oltre a un
   TURN di prova, e decide i punti 3 e 5.
3. **TURN proprio su VPS Hetzner**, con provisioning delle credenziali dal backend Sweetplace al posto
   di `homeway.io/api/webrtc/config`.
4. **Riesprimere il filtro entità.** Oggi il filtro Sweetplace per Alexa e Google agisce solo sul flusso
   verso Homeway: gli unici chiamanti sono in `eventhandler.py:233-234` e `317-320`. Cambiando porta
   d'ingresso va espresso nei blocchi `filter:` nativi di HA, che `configmanager.py` già sa scrivere.
5. **Ridisegnare la UI dell'add-on**, che dà per scontata la connessione a Homeway e resta bloccata su
   *"Connecting To Homeway.io..."* (`webserver.py:337`) ricaricandosi ogni secondo (`webserver.py:495-498`).
6. **Skill Alexa e Action Google proprie**, quando si decide di riaccenderle. Tempi dettati da Amazon e
   Google, non comprimibili.

## Scelte fatte

| Scelta | Perché | Rev |
|---|---|---|
| Cloudflare Tunnel come canale primario, su dominio proprio | Già presente nel fork, già provisionato dal backend Sweetplace, già punta a `127.0.0.1:8123`. Nessun protocollo da scrivere e nessuna porta chiusa verso Alexa e Google | r1 |
| Sage e WebRTC fuori perimetro | Sage occupa sei delle dodici dipendenze obbligatorie; escluderlo riduce l'unica dipendenza indispensabile al solo tunnel | r1, WebRTC superato in r2 |
| Non replicare il protocollo `PluginWebsocketConnection` | Lo schema è ricostruibile da `Proto/`, ma il reverse proxy pubblico va progettato da zero comunque: il protocollo su misura serve solo a restare compatibili con il server che si abbandona | r1 |
| Sganciare `device/ping` da `OnPrimaryConnectionEstablished` come primo intervento | Finché la registrazione Sweetplace è a valle dell'handshake Homeway, nessun hub nuovo si provisiona senza permesso di terzi (`linuxhost.py:337` + `index.ts:203`) | r1 |
| Alexa e Google restano possibili, come skill e Action proprie | Le integrazioni sono native di Home Assistant (`configmanager.py:177-190`); Homeway è solo la porta pubblica, e la porta pubblica è sostituibile | r1 |
| **WebRTC rientra nel perimetro** | È il meccanismo che tiene il video fuori dal tunnel (`camera/webrtc.py:270`), e quindi ciò che disinnesca l'unica clausola Cloudflare applicabile. Escluderlo lasciava il video nel tunnel senza motivo | r2, supera r1 |
| **Separare piano di controllo e piano media** | Hanno peso e vincoli opposti: il controllo è leggero e vuole un hostname pubblico, il media è pesante e vuole solo attraversare i NAT. Trattarli insieme rendeva il problema grande | r2 |
| **TURN proprio su VPS Hetzner, non su Railway** | coturn alloca i relay su UDP 49152-65535 e Railway non espone UDP; i relay TCP di RFC 6062 non sono usati dai browser | r2 |
| **Non costruire frp adesso: prima misurare** | I due argomenti che lo giustificavano — costo e vincoli contrattuali Cloudflare — sono caduti alla verifica. Resta l'indipendenza in sé, che non è urgente | r2 |
| **Non usare Cloudflare Access sugli hostname dei clienti** | Tiene fuori la §2.2 sulla rivendita di Zero Trust, non consuma seat, e non rompe Alexa e Google che non fanno login interattivo | r2 |

## Alternative scartate

| Alternativa | Perché no | Rev |
|---|---|---|
| Replicare il protocollo Homeway con un server proprio | Costo alto senza vantaggio: la parte davvero difficile (reverse proxy pubblico che traduce richieste in web stream) non è visibile dal codice dell'add-on e va progettata comunque. La compatibilità con il client non è un vincolo, perché il client è di proprietà | r1 |
| Tunnel open source self-hosted su VPS propria (frp, rathole, WireGuard) | Elimina ogni terzo dal percorso dati, ma sposta su di sé ferro, banda, certificati, aggiornamenti e reperibilità, e non evita di progettare la porta pubblica. Resta la strada se il vincolo diventa *nessun fornitore terzo* | r1 |
| Restare su Homeway e accettare la dipendenza | Il rischio non è la perdita dell'accesso remoto, è che l'onboarding degli hub Sweetplace non parta affatto | r1 |
| Portarsi in casa anche Sage | Fuori perimetro per decisione di prodotto. L'operazione Chat è l'unica voce a difficoltà molto alta oltre al tunnel | r1 |
| **coturn su Railway** | I relay sono UDP su 49152-65535 e Railway non espone UDP (*"Railway only proxies TCP publicly"*). `--no-udp-relay` non salva: i relay TCP di RFC 6062 sono `MAY` in RFC 8835 e i browser non li implementano | r2 |
| **Famiglia WireGuard e mesh (WireGuard, Nebula, NetBird, Headscale)** | UDP in ingresso obbligatorio, e i container Railway non hanno `NET_ADMIN` né `/dev/net/tun`. WireGuard esclude esplicitamente il tunneling su TCP. In più NetBird ha i componenti server sotto AGPLv3, non BSD-3 come si legge in giro | r2 |
| **Pangolin / Newt** | Non è open source nella parte che conta: 207 file sotto Fossorial Commercial License, incluso tutto `server/private/` con SSO, rate limiting e audit. Uso gratuito limitato sotto i 100.000 $ di ricavi lordi e divieto di far operare i componenti a terzi | r2 |
| **rathole** | Tutti i binari ARM ufficiali sono compilati senza TLS e senza websocket (*"Cross-compiling with tls is hard. So we don't :("*), quindi inutilizzabili sugli hub. Nessuna release stabile da ottobre 2023 | r2 |
| **Chisel, wstunnel, boringproxy, sish, zrok, OpenZiti** | Chisel e wstunnel non fanno routing per hostname: risolvono il trasporto e lasciano da costruire "un hostname per hub". Gli altri pretendono più porte TCP/TLS grezze, che Railway non dà, o sono abbandonati | r2 |

## Questioni aperte

- [ ] **Quale percentuale di sessioni WebRTC finisce in `relay`** invece che diretta, su fibra, FWA e CGNAT. È il numero che dimensiona il TURN e che decide se serve davvero — *sollevata in r2 da @6773939989*
- [ ] Quanto spesso Home Assistant ripiega su HLS quando WebRTC non si stabilisce: in quel caso il video torna nel tunnel e la clausola CDN torna viva — *sollevata in r2 da @6773939989*
- [ ] Dimensionamento della VPS Hetzner: banda di picco del relay in funzione del numero di sessioni concorrenti — *sollevata in r2 da @6773939989*
- [ ] Come il backend Sweetplace emette le credenziali TURN: statiche per hub, o a scadenza con `use-auth-secret` — *sollevata in r2 da @6773939989*
- [ ] Chi gestisce dominio, sottodomini e certificati degli hub, e con quale criterio si assegnano i nomi — oggi sono `crypto.randomBytes(4)` (`index.ts:246`) — *sollevata in r1 da @6773939989*
- [ ] Il blocco `google_assistant:` scritto dall'add-on contiene segnaposto: `private_key: "nokey"`, `client_email: relay@sweetplace.it`, `project_id: sweetplace-relay` (`configmanager.py:186-190`). Va chiarito se il report proattivo di stato verso Google sia mai stato attivo — *sollevata in r1 da @6773939989*
- [ ] Esposizione diretta di Home Assistant su Internet: l'ingress punta a `127.0.0.1:8123` senza filtri applicativi, e su questo canale non può stare una protezione interattiva perché romperebbe Alexa e Google. Va deciso cosa sta davanti — *sollevata in r1, riformulata in r2*
- [ ] Se un domani si costruisse frp: resta da provare che l'edge di Railway consegni al backend l'upgrade in HTTP/1.1 con i byte iniziali `GET /~!frp` intatti. Mezza giornata di test, oggi non urgente — *sollevata in r2 da @6773939989*

### Questioni chiuse in r2

- [x] ~~Il tunnel Homeway va spento o resta acceso in parallelo durante la transizione?~~ Resta acceso finché Sage è in uso; il canale di accesso remoto è già Cloudflare e i due sono indipendenti.
- [x] ~~Se l'add-on smette di collegarsi a Homeway, cosa mostra il pannello Ingress?~~ Confluita nel punto 5 di *Cosa va costruito*.
- [x] ~~Quando aprire i fascicoli skill Alexa e Action Google?~~ Non prima che il piano di controllo sia stabile: sono l'ultimo passo, non il primo.

## Revisioni

Append-only. Non si riscrive mai una voce già scritta.

### r1 — 2026-08-21 — @6773939989 — POV: primo inquadramento, misurare prima di decidere

**Messo in discussione:** l'assunto che uscire da Homeway significhi replicarne l'infrastruttura.
La superficie remota è stata mappata leggendo il codice (46 voci, con verifica avversariale su ogni
flag "obbligatorio" e su ogni stima di difficoltà) invece di stimarla. Nessun flag è stato
contestato; sei stime di difficoltà sono state riviste. È emerso che la dipendenza indispensabile è
una sola, che Alexa e Google sono integrazioni native di Home Assistant e non funzioni di Homeway, e
che il canale alternativo esiste già dentro il fork.

**Cambiato nella sintesi:** la domanda passa da *"come si replica Homeway"* a *"come si cambia
trasporto"*. Il tunnel Cloudflare, già presente e già puntato a Home Assistant, diventa il canale
primario. Il protocollo `PluginWebsocketConnection` non si replica.

**Lasciato aperto:** la sequenza di transizione, la protezione davanti a Home Assistant esposto, la
gestione dei nomi a dominio, e il momento in cui aprire skill Alexa e Action Google.

### r2 — 2026-08-22 — @6773939989 — POV: separare i piani, e verificare i contratti invece di temerli

**Messo in discussione:** tre cose date per assodate in r1. Primo, che il traffico fosse una cosa
sola: dal sorgente di Home Assistant risulta che il video WebRTC non passa da HA e quindi nemmeno dal
tunnel, il che spacca il problema in due parti indipendenti. Secondo, che Cloudflare comportasse costi
e vincoli contrattuali: verificati sulla documentazione ufficiale, il tunnel è gratuito, il limite dei
50 seat non si applica perché non se ne consuma nessuno, e il divieto di rivendita riguarda Zero
Trust, che il backend non usa — tre grep sulle chiamate API lo dimostrano. Terzo, che WebRTC fosse
fuori perimetro: è invece il meccanismo che risolve l'unica clausola rimasta.

In parallelo è stata verificata su documentazione ufficiale l'intera famiglia dei tunnel open source
self-hosted rispetto ai vincoli di Railway. Ne sopravvive uno solo, frp; due progetti molto citati si
sono rivelati inadatti per ragioni non tecniche (licenza di Pangolin, binari ARM di rathole).

**Cambiato nella sintesi:** l'architettura non è più "un tunnel" ma **due piani separati**, controllo
e media. Cloudflare resta sul controllo perché i suoi argomenti contro sono caduti; il media esce dal
tunnel via WebRTC e richiede un TURN proprio, che per vincolo UDP non può stare su Railway e andrà su
VPS. La costruzione di frp è rimandata: non ha più una motivazione economica o contrattuale, solo di
principio. È stato inoltre eseguito e messo in review l'intervento che sgancia la registrazione degli
hub dall'handshake di Homeway.

**Lasciato aperto:** il numero che decide il dimensionamento di tutto, cioè quanto spesso WebRTC
ripiega sul relay invece di andare diretto, e con quale frequenza HA ripiega su HLS riportando il
video dentro il tunnel.
