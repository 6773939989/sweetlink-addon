<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->
<!-- This is used in the sweetplace UI to show updates, so keep it up to date. -->

## 2.7.80

- 📝 La descrizione e la documentazione dell'add-on sono riscritte: quelle di prima erano i testi del progetto da cui questo deriva, con il marchio sostituito, e mandavano nel Discord di un altro progetto chiamandolo nostro.
- 📝 Un file NOTICE dichiara da cosa deriva l'add-on, cosa è stato cambiato e quando, e cosa viene incorporato nell'immagine senza essere scritto da noi.

## 2.7.79

- 🔗 L'indirizzo dell'add-on e quello dell'archivio puntano al repository pubblico, da cui si arriva al sorgente. Prima puntavano al sito del prodotto, che in una scheda tecnica non serve a nessuno.

## 2.7.78

- 📐 Le due colonne del pannello sono più distanti e partono dalla stessa riga di testo, non più per coincidenza dei corpi del tema.
- 📐 L'elenco delle persone ha le intestazioni «Nome reale» e «Account», e il nome di accesso sta accanto al nome invece che dall'altra parte della pagina.

## 2.7.77

- 🔒 Riprendersi la propria riga dopo aver rigenerato l'identità richiede ora anche la chiave precedente, non il solo identificativo: quello non è un segreto, e bastava per farsi consegnare il tunnel di una casa altrui.
- 🔒 Il gettone del tunnel non passa più dalla riga di comando, dove lo leggeva chiunque avesse una shell sull'hub.
- 🐛 Lo schedario degli utenti si scrive in modo atomico: un'interruzione a metà lo azzerava, e da lì l'add-on non riconosceva più nessuno degli account creati.

## 2.7.76

- 🔒 Per ottenere il tunnel l'apparecchio deve presentare la propria chiave privata. Prima bastava un identificativo, e chi otteneva quel gettone poteva diventare l'altro capo dell'indirizzo pubblico della casa.
- 🔒 `cloudflared` non si scarica più da «l'ultima versione»: versione fissa e impronta verificata prima di renderlo eseguibile.

## 2.7.75

- 🐛 L'elenco delle persone di casa riportava, come identificativo dell'utente, quello della persona: la rigenerazione della password moriva con «nessun utente corrisponde». Adesso l'add-on ricorda il legame da quando crea l'account, e qualunque identificativo esca viene verificato contro l'anagrafica di Home Assistant.

## 2.7.74

- 🔒 La password di un account amministratore non si cambia piu' dal portale. Gli account che creiamo noi stanno fra gli utenti normali; quello dell'installatore no, e adesso c'e' un confine che lo separa.

## 2.7.73

- 📐 Via il contorno rosso attorno all'azzeramento, e il campo di conferma prende la stessa larghezza del pulsante.

## 2.7.72

- ✍️ «Non si annulla» diventa «l'operazione non è reversibile: non si torna indietro», e il riquadro rosso dice cosa succede davvero: l'apparecchio torna come appena uscito di fabbrica e chi lo aveva rivendicato deve rifare la registrazione.
- ✍️ La spiegazione dei dettagli tecnici diceva una cosa non vera. Adesso dice cosa sono quei valori.
- 📐 Più aria in fondo a ogni sezione.

## 2.7.71

- 📐 I due pulsanti della pagina hanno la stessa larghezza. Finché quello che azzera l'apparecchio era più grande di quello che apre la configurazione, il più pericoloso era anche il primo che l'occhio raggiungeva.

## 2.7.70

- 👥 La prima sezione elenca le persone di casa, con il nome e il nome di accesso, e il pulsante per gestirle sta in fondo all'elenco: prima si guarda chi c'è, poi si aggiunge. Era l'unica informazione della pagina che stava dietro un clic e un'altra scheda.
- 📐 I margini della pagina sono una frazione della larghezza: il 5% per lato sul telefono, un sesto per lato sullo schermo grande.

## 2.7.69

- 🧭 La pagina dell'add-on è stata rifatta: in cima l'indirizzo dell'hub con accanto lo stato, sotto tre sezioni con il titolo e la spiegazione a sinistra e la cosa a destra. I dettagli tecnici non sono più dentro un pannello da aprire: sono i valori che si cercano quando qualcosa non va, e nasconderli li rendeva introvabili proprio allora.
- 🛑 L'azzeramento è l'unica cosa che resta chiusa, in un riquadro rosso in fondo: è l'unica che distrugge qualcosa.

## 2.7.68

- 📏 L'icona nella barra laterale diventa un righello, la stessa cosa che disegna il marchio Sweetplace.
- 💬 Chi abita la casa senza averla registrata, aprendo la pagina, trova scritto cosa si gestisce da lì, cosa usa lui al posto di questa pagina e a chi chiedere se gli serve qualcosa.

## 2.7.67

- 🧭 Tre pagine distinte a seconda di chi apre il pannello: chi installa vede tutto, chi ha registrato la casa vede il proprio impianto e la gestione delle persone, chiunque altro una riga che dice che lì non c'è niente per lui. La scelta sta in un punto solo del codice invece che sparsa in due.

## 2.7.66

- 🏠 Chi ha registrato la casa trova la voce Sweetplace nella propria barra laterale, e da lì apre la gestione delle persone. Prima la vedevano solo gli amministratori, e il proprietario è un utente standard: non aveva nessun posto da cui farlo.
- 🧰 Il pannello mostra a ciascuno quello che gli compete: gli strumenti di fabbrica — dettagli tecnici e preparazione dell'immagine — restano a chi installa.
- 🖥️ La pagina usa tutta la larghezza dello schermo e dispone le schede affiancate quando c'è spazio, invece di incolonnarle in una striscia da telefono.

## 2.7.65

- 🚪 «Apri la configurazione» su un hub già registrato non fa più ripartire la registrazione da zero: apre il portale già dentro, alla gestione di casa. Il pulsante lo mostra solo a chi è amministratore su questo sistema.
- 🏷️ Su un hub non ancora registrato il pulsante dice «Registra il tuo Sweetplace», invece di «Rivendicalo da qui».

## 2.7.64

- 🔑 La password non veniva impostata quando il nome di accesso era diverso dal nome visualizzato: «Tiberio» con accesso «oneshot» falliva con «utente non trovato». L'invito conservava l'identificativo della persona invece di quello dell'utente, e il ripiego cercava di rimediare confrontando i due nomi — cosa che funzionava solo finché coincidevano.
- 🔑 L'utente si cerca prima per corrispondenza esatta e solo dopo per nome. Prima l'ordine era invertito, e una persona con un nome simile poteva vedersi cambiare la password al posto di un'altra.

## 2.7.63

- 🔓 Se da casa non si riesce più a entrare dopo troppi tentativi sbagliati, il portale se ne accorge da solo e mostra «Sblocca casa mia». Il blocco è sull'indirizzo di rete, non sulla persona: cancellare e ricreare l'utente non serviva a niente.
- 🔓 Si sblocca soltanto l'indirizzo da cui il proprietario sta chiedendo in quel momento — lo riconosce il server, a lui non viene chiesto niente. Gli altri blocchi restano dove sono.

## 2.7.62

- 🛡️ Dopo cinque tentativi di accesso sbagliati l'indirizzo da cui arrivano viene bloccato. La pagina di accesso dell'hub è raggiungibile da internet e la robustezza della password non è in nostro potere — chi entra può cambiarsela come vuole — ma i tentativi sì: una password debole resta debole e smette di essere forzabile.
- 🛡️ I due valori si scrivono solo se mancano: se li hai già impostati a mano, restano come li hai messi.

## 2.7.61

- 🔐 La password provvisoria che accompagna un account appena creato passa da 8 cifre a 192 bit di casualità vera. Prima era generata con un generatore riproducibile e non crittografico, e restava sull'account finché la persona non apriva il proprio invito: se l'invito scadeva inutilizzato, restava per sempre.
- 🔐 Quella password non viaggia più verso il cloud: nessuno la leggeva, e una credenziale che attraversa la rete senza servire a niente è solo una credenziale in più che può finire in un registro.

## 2.7.60

- 🎨 Il resoconto dopo l'azzeramento distingue i tre esiti a colpo d'occhio: OK verde, ATTENZIONE giallo, ERRORE arancione. Prima era un blocco di testo tutto uguale, in cui l'unica riga che chiedeva un intervento si leggeva come le altre.
- 🔒 Quel resoconto viene costruito come nodi e non come HTML: nei dettagli finiscono i nomi degli account letti dal sistema, cioè testo scritto da altri, e un nome costruito ad arte non deve poter eseguire niente dentro il pannello di amministrazione.

## 2.7.59

- 🔑 L'elenco dei membri mostra il nome di accesso vero. Prima lo fabbricava dall'identificativo della persona sostituendo i trattini bassi con i punti: chi registrava «Mario Rossi» con nome di accesso `marior` se lo vedeva scritto `mario.rossi`, e lo comunicava sbagliato alla persona, che non riusciva a entrare. Ora il nome viene dall'add-on, che lo registra quando crea l'utente; se non lo sa, non scrive niente invece di inventarlo.
- 📏 Le righe dei membri sono alte quanto i pulsanti. Erano più alte in visualizzazione e tornavano dell'altezza giusta in modifica: lo stesso elemento aveva due altezze.
- 📍 L'indirizzo di casa parte dalla stessa verticale del nome nel logo, e c'è più aria fra l'intestazione e l'elenco.

## 2.7.58

- 🧬 Il referto prima della clonazione conta gli account del sistema operativo e si blocca se ne trova. Vivono sul disco insieme a tutto il resto: un'immagine preparata su un apparecchio già configurato consegnerebbe a ogni cliente l'account di chi l'ha preparata, con la sua password e i telefoni già accoppiati.
- 🧬 Il referto segnala anche i file di database e quelli di segreti trovati nella cartella del sistema operativo, elencando quello che c'è invece di cercare nomi attesi.
- 🧬 Quando non riesce a controllare gli account, il referto lo dichiara invece di tacere: un controllo silenzioso si legge come «a posto».

## 2.7.57

- 👤 Chi aggiunge una persona può scegliere il suo nome utente, invece di lasciarlo derivare dal nome reale. È il nome con cui quella persona entrerà nel sistema, e chi lo assegna deve poi comunicarglielo: sceglierlo è meglio che indovinarlo.

## 2.7.56

- 🛑 Il pulsante di azzeramento si spegne dopo aver fatto il suo lavoro. Prima l'etichetta diventava «Fatto. Ora spegni l'apparecchio.» ma restava un pulsante armato: chi la leggeva come una conferma e la premeva faceva ripartire l'azzeramento.
- 🔎 Quando l'add-on si è già fermato, il pannello lo dice invece di mostrare un errore di sintassi incomprensibile.

## 2.7.55

- 🏷️ L'add-on si chiama **Sweetlink**, anche nel menu laterale.
- 🎯 QR e codice stanno al centro della scheda: sono la ragione per cui quella scheda esiste, e allineati a sinistra sembravano due campi qualunque.
- 🔘 I pulsanti hanno lo stesso stondo delle schede. Due forme diverse sulla stessa superficie si notano prima del contenuto.
- 📝 I testi non nominano piu' i componenti interni: si parla di «sistema operativo» e di «tunnel protetto», che e' quello che il cliente ha davanti.

## 2.7.54

- 🔄 Il pannello non resta piu' indietro dopo un aggiornamento: si serviva senza dire al browser di non conservarlo, e una copia vecchia faceva sembrare che l'aggiornamento non fosse arrivato.
- 🧭 Il pannello mostra quello che serve adesso. Finche' l'hub non e' stato rivendicato, in cima ci sono QR e codice da stampare; dopo, l'indirizzo del proprio impianto. Il resto sta sotto «Dettagli tecnici».

## 2.7.53

- 🏷️ Il pannello mostra il codice di rivendicazione dell'hub e il suo QR: sono quelli da stampare sull'etichetta dell'apparecchio, ed e' cio' che permette al cliente di rivendicare il proprio impianto da un indirizzo pubblico, senza dover prima entrare in Home Assistant.
- 🔐 Rivendicare un hub non si fa piu' con il solo indirizzo di rete: quello e' scritto sulla scatola e si legge dalla rete locale, quindi chiunque lo conoscesse poteva prendersi l'apparecchio di un altro prima del suo proprietario. Adesso serve il codice stampato.
- 🔌 L'hub si registra con le sole schede di rete integrate, Ethernet e Wi-Fi. Prima ne dichiarava anche altre, mentre il vincolo hardware guardava solo quelle integrate: i due elenchi potevano divergere senza che nessuno dei due fosse sbagliato.
- ♻️ Se l'hub rigenera la propria identita' non perde piu' la rivendicazione del cliente: si riprende la riga che gia' gli apparteneva, dimostrando di essere lo stesso apparecchio. Senza quella prova non se la prende nessun altro.
- 🎨 Il pannello usa i caratteri, i colori e le forme di Home Assistant, e segue il tema chiaro o scuro invece di imporne uno proprio.

## 2.7.52

- 🛰️ Tolta l'ultima cosa che usciva verso i server del progetto di origine: una misura di latenza che partiva da sola quindici minuti dopo il primo avvio e poi ogni due giorni, e i cui risultati non leggeva piu' nessuno.
- 🧾 Il tunnel annuncia di essere attivo una volta sola invece di quattro: cloudflared apre quattro connessioni per ridondanza, ma quello che conta e' il passaggio di stato.
- ⏱️ All'avvio spariva un errore che sembrava un guasto e non lo era: il canale di gestione si presentava al cloud prima che la registrazione dell'hub fosse arrivata, e ora la aspetta.

## 2.7.51

- 🔇 L'hub non accumula piu' eventi che non ha dove mandare: dopo il distacco dal servizio esterno restava un thread che ci riprovava ogni dodici secondi, riempiendo la memoria e i log senza mai riuscirci.

## 2.7.50

- 🔒 L'hub non parla piu' con nessun servizio di terzi: l'accesso remoto passa solo dal tunnel Sweetplace, e la segnalazione errori e la telemetria verso l'esterno sono spente.
- 🟢 Il pannello dice «hub attivo» guardando il tunnel vero: prima lo diceva un servizio esterno, e poteva dirlo anche quando l'hub non era raggiungibile.

## 2.7.49

- 🌐 L'hub parla con il cloud Sweetplace attraverso un indirizzo unico, deciso in un punto solo: prima era scritto a mano in quattro posti e bastava dimenticarne uno perché metà dell'add-on parlasse con un server e metà con un altro.

## 2.7.48

- 🧯 La preparazione dell'immagine da clonare è passata dal tab di configurazione alla pagina dell'add-on, e non è più un interruttore: mostra prima cosa c'è ancora sul disco che non deve essere duplicato, e chiede di scrivere una parola di conferma.
- 🔒 L'azzeramento è riservato agli amministratori di Home Assistant: gli utenti creati con la configurazione guidata non possono raggiungerlo in nessun modo.
- 🔑 L'azzeramento cancella anche la chiave privata di NetBird, che altrimenti finirebbe identica su tutti gli hub e li farebbe contendere lo stesso indirizzo nella rete privata.
- 🏷️ Il pannello segnala quando il nome con cui l'hub compare nella rete privata NetBird non è quello previsto, così chi prepara l'apparecchio se ne accorge prima di consegnarlo.
- 💾 Il file dei segreti viene ora sostituito in un colpo solo: un'interruzione di corrente durante il salvataggio non può più lasciare l'hub senza identità.

## 2.7.47

- 🔁 Se l'identità dell'hub risulta duplicata, la ricostruzione ora si completa da sola: l'add-on rigenera le proprie credenziali e si riavvia perché tutto le adotti, senza bisogno di interventi.
- 🔌 Il riconoscimento del dispositivo ignora le schede di rete rimovibili: un adattatore USB spostato da un apparecchio all'altro non lo confonde più.

## 2.7.46

- 🔐 L'identità dell'hub è ora legata all'apparecchio su cui è nata: se l'immagine della scheda viene clonata su un altro dispositivo, il clone se ne accorge al primo avvio e si rigenera identità e chiave da solo.

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
