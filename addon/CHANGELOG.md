<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->
<!-- This is used in the sweetplace UI to show updates, so keep it up to date. -->

## 2.7.45

- 🌐 Il pannello mostra l'indirizzo pubblico con cui l'hub è raggiungibile da Internet, accanto all'identificativo hardware.
- 🔍 Riconoscimento del dispositivo più affidabile: vengono usati solo gli indirizzi di rete che il sistema dichiara permanenti, ignorando quelli generati da Docker e dalle reti virtuali.
- 🔁 La registrazione riprova anche quando all'avvio nessuna scheda di rete è ancora pronta, invece di rinunciare.

## 2.7.44

- 🧾 Cronologia delle versioni ripulita: restano le note che riguardano davvero questo add-on.
- 🔒 Rimosso un endpoint di scrittura sulla rete locale che nessuna pagina utilizzava più.

## 2.7.43

- 🧹 Rimossi i riferimenti e i servizi non utilizzati ereditati dal progetto di origine: l'assistente vocale integrato e l'iniezione di script nel frontend di Home Assistant, che contattava un servizio esterno dal browser.
- 🎛️ Pannello dell'add-on ridisegnato: stato dell'hub, identificativo hardware e un pulsante che apre direttamente la configurazione Sweetplace con il dispositivo già riconosciuto.
- 🔌 Rimossa la porta 11027, che serviva solo all'assistente vocale rimosso.

## 2.7.42

- 🔗 La registrazione dell'hub sul cloud Sweetplace parte all'avvio dell'add-on e non dipende più da servizi esterni: usa solo dati generati sul dispositivo.
- 🔁 Se la rete o il backend non sono ancora pronti all'avvio, la registrazione viene ritentata ogni minuto finché non riesce, e da lì in poi viene rinfrescata ogni 6 ore.
- ✅ L'esito della registrazione viene letto dalla risposta del backend e non più dal solo codice HTTP, così un endpoint configurato male non passa più per riuscito.
- 🛠️ Corretto il provisioning del tunnel Cloudflare, che al primo avvio di un dispositivo nuovo falliva e veniva ritentato all'infinito senza mai emettere il tunnel.

## 2.7.27

- 🔑 Nuovo wizard di invito utente a 3 step: scarica app, configura URL/username, genera password monouso.
- 🔒 La password viene generata on-demand al passo 3 e non viene mai salvata in memoria o nel database.
- 🔗 Il link di invito è valido 48 ore e si disattiva automaticamente dopo il primo utilizzo.
- ⚙️ Aggiunta chiamata HA `config/auth_provider/homeassistant/create` e fallback a `change_password` per impostare le credenziali del nuovo utente.

## 2.7.26

- 🏠 Persistenza sessione wizard: il token viene salvato in `localStorage` per non tornare allo step 1 dopo un refresh.
- ⚠️ Errori di connessione Home Assistant mostrati in forma soft con pulsante "Riprova" invece di alert aggressivi.
- 🗺️ Coordinate GPS visualizzate con 7 decimali nel footer della dashboard.
- 🎨 Miglioramenti UI: font label step uniformati, testo descrizione su due righe, overlay di caricamento con spinner durante creazione/aggiornamento utenti, pulsante "Annulla" al posto della X.
- 📍 Geolocalizzazione: lat/lon scritti in `kasa-gps.yaml` e `appdaem-gps.yaml` (non in `configuration.yaml`).

---

Le versioni precedenti alla 2.7.26 appartengono al progetto open source da cui
Sweetplace deriva e descrivono funzioni che questo add-on non offre. Il codice di
origine e la sua storia restano consultabili nel repository indicato nel README.
