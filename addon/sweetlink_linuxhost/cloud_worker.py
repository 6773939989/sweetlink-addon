import os
import time
import json
import re
import threading
import requests
import socketio

from .backend import Backend
import urllib3

# Disabilita gli InsecureRequestWarning quando chiamiamo HTTPS interni (se usati)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CloudWorker:
    def __init__(self):
        self._thread = None
        self._running = False
        self.logger = None
        self.plugin_id = None
        self.private_key = None
        self.sio = socketio.Client(reconnection=True, reconnection_delay=5, reconnection_delay_max=30)
        
        # Registra i listener del SocketIO
        self.sio.on('connect', self._on_connect)
        self.sio.on('disconnect', self._on_disconnect)
        self.sio.on('command_fetch_users', self._on_fetch_users)
        self.sio.on('command_create_user', self._on_create_user)
        self.sio.on('command_update_user', self._on_update_user)
        self.sio.on('command_delete_user', self._on_delete_user)
        self.sio.on('command_ban_status', self._on_ban_status)
        self.sio.on('command_unban', self._on_unban)
        self.sio.on('command_set_location', self._on_set_location)
        self.sio.on('command_generate_password', self._on_generate_password)

    def Start(self, logger, plugin_id, private_key, ha_connection, storage_dir):
        self.logger = logger
        self.plugin_id = plugin_id
        self.private_key = private_key
        self.ha_connection = ha_connection
        self.storage_dir = storage_dir
        self.logger.info("Starting Secure Cloud Worker Demon for Zero-Touch Provisioning...")
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def Stop(self):
        self._running = False
        if self.sio.connected:
            self.sio.disconnect()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _get_ha_headers(self):
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def _get_ha_api_url(self):
        # L'URL di root per parlare col core da un AddOn
        return "http://supervisor/core/api"

    def _on_connect(self):
        self.logger.info("[CloudWorker] Securely Authenticated to Sweetplace Cloud WebSocket")

    def _on_disconnect(self):
        self.logger.warning("[CloudWorker] Disconnected from Sweetplace Cloud WebSocket")

    def _on_set_location(self, data):
        """
        Receives: { lat: float, lon: float, display: str, plugin_id: str }
        1. Calls HA WebSocket API config/core/update (live, no restart)
        2. Writes /homeassistant/home-zone.yaml   (zone: Home coords)
        """
        lat = data.get('lat')
        lon = data.get('lon')
        display = data.get('display', '')
        self.logger.info(f"[CloudWorker] Setting home location: lat={lat}, lon={lon} ({display})")

        errors = []

        # 1 — Live update via HA WebSocket API (no restart needed)
        try:
            ha_wait = 0
            while not getattr(self.ha_connection, 'IsConnected', False) and ha_wait < 10:
                time.sleep(1)
                ha_wait += 1
            if getattr(self.ha_connection, 'IsConnected', False):
                resp = self.ha_connection.SendAndReceiveMsg({
                    "type": "config/core/update",
                    "latitude": lat,
                    "longitude": lon,
                })
                if not resp or not resp.get('success'):
                    err = resp.get('error', {}).get('message', 'Unknown') if resp else 'Timeout'
                    self.logger.warning(f"[CloudWorker] HA WebSocket location update warning: {err}")
                    errors.append(f"HA WS: {err}")
                else:
                    self.logger.info("[CloudWorker] HA live location updated via WebSocket.")
            else:
                errors.append("HA WS offline")
        except Exception as e:
            self.logger.error(f"[CloudWorker] HA WS location error: {e}")
            errors.append(str(e))

        # 2 — Write /homeassistant/home-zone.yaml
        try:
            kasa_path = "/homeassistant/home-zone.yaml"
            kasa_content = (
                f"# Sweetplace auto-generated — NON MODIFICARE MANUALMENTE\n"
                f"# Questa è la zona Home/Casa di default.\n"
                f"# Non rinominare 'Home': è usato da HA per la rilevazione presenza.\n"
                f"name: Home\n"
                f"# Coordinate aggiornate automaticamente da Sweetplace Onboarding\n"
                f"latitude: {lat}\n"
                f"longitude: {lon}\n"
                f"radius: 10\n"
                f"icon: mdi:home\n"
            )
            with open(kasa_path, 'w') as f:
                f.write(kasa_content)
            self.logger.info(f"[CloudWorker] home-zone.yaml written: lat={lat}, lon={lon}")
        except Exception as e:
            self.logger.error(f"[CloudWorker] home-zone.yaml write error: {e}")
            errors.append(str(e))

        # Ack to backend
        self.sio.emit('command_set_location_result', {
            'success': len(errors) == 0,
            'error': '; '.join(errors) if errors else None
        })

    def _get_tracked_users(self):
        try:
            path = os.path.join(self.storage_dir, 'sweetplace_users.json')
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return json.load(f)
        except Exception: pass
        return []

    # L'identificativo della persona, sia che la voce sia una stringa (come le scriveva la
    # versione precedente) sia che sia un oggetto.
    @staticmethod
    def _tracked_id(entry):
        return entry.get('id') if isinstance(entry, dict) else entry

    # Da identificativo della persona a nome di accesso.
    #
    # QUESTO SCHEDARIO E' L'UNICA FONTE DEL NOME DI ACCESSO, e non e' un ripiego.
    # L'elenco delle persone si costruisce dalle entita' person di Home Assistant, che il nome
    # di accesso non ce l'hanno: la persona e l'utente sono due cose separate, legate solo da
    # user_id. Il nome di accesso lo scegliamo noi al momento della creazione ed e' l'unico
    # momento in cui lo sappiamo con certezza, quindi si scrive qui.
    def _tracked_usernames(self):
        indice = {}
        for entry in self._get_tracked_users():
            if isinstance(entry, dict):
                indice[entry.get('id')] = entry.get('username') or ''
        return indice

    def _add_tracked_user(self, user_id, username=''):
        try:
            tracked = self._get_tracked_users()
            if user_id not in [CloudWorker._tracked_id(e) for e in tracked]:
                tracked.append({'id': user_id, 'username': username})
                path = os.path.join(self.storage_dir, 'sweetplace_users.json')
                with open(path, 'w') as f:
                    json.dump(tracked, f)
        except Exception as e:
            self.logger.error(f"[CloudWorker] Warning: Failed to save tracked user: {e}")

    def _remove_tracked_user(self, user_id):
        try:
            tracked = self._get_tracked_users()
            rimasti = [e for e in tracked if CloudWorker._tracked_id(e) != user_id]
            if len(rimasti) != len(tracked):
                path = os.path.join(self.storage_dir, 'sweetplace_users.json')
                with open(path, 'w') as f:
                    json.dump(rimasti, f)
        except Exception as e:
            self.logger.error(f"[CloudWorker] Warning: Failed to remove tracked user: {e}")

    # IL BLOCCO PER INDIRIZZO IP DI HOME ASSISTANT.
    #
    # Dopo cinque accessi sbagliati HA scrive l'indirizzo in un file nella propria cartella di
    # configurazione e da li' in poi lo rifiuta PRIMA di guardare chi sia. Cancellare e ricreare
    # la persona non la fa rientrare, e l'indirizzo bloccato e' quello di casa: restano fuori
    # tutti quelli che si collegano da li'.
    #
    # Il file si CERCA, non si da' per noto. Un nome atteso che non c'e' produrrebbe un
    # controllo che non trova mai niente e un "nessun blocco" detto a vuoto, che e' la bugia
    # peggiore in un posto come questo.
    def _trova_file_blocchi(self):
        for cartella in ("/homeassistant", "/config", "/homeassistant_config"):
            if not os.path.isdir(cartella):
                continue
            for nome in os.listdir(cartella):
                if "ip_ban" in nome.lower():
                    return os.path.join(cartella, nome)
        return None

    def _leggi_blocchi(self):
        """Gli indirizzi bloccati, oppure None se non si e' potuto stabilirlo."""
        percorso = self._trova_file_blocchi()
        if percorso is None:
            # Nessun file: finche' non c'e' stato un blocco, HA non lo crea. E' un "nessuno",
            # non un "non lo so".
            return []
        try:
            import yaml
            with open(percorso, "r", encoding="utf-8") as f:
                dati = yaml.safe_load(f)
            if dati is None:
                return []
            if not isinstance(dati, dict):
                return None
            return [str(k) for k in dati.keys()]
        except Exception as e:
            self.logger.error(f"[CloudWorker] Non riesco a leggere {percorso}: {e}")
            return None

    def _on_ban_status(self, data):
        request_id = data.get('requestId')
        ip = str(data.get('ip') or '').strip()
        bloccati = self._leggi_blocchi()
        if bloccati is None:
            self.sio.emit('command_ban_status_result', {
                'requestId': request_id, 'bloccato': False,
                'error': "Non riesco a leggere l'elenco dei blocchi."})
            return
        self.logger.info(f"[CloudWorker] Blocchi presenti: {len(bloccati)}. Richiesto per {ip}.")
        self.sio.emit('command_ban_status_result', {
            'requestId': request_id, 'bloccato': ip in bloccati, 'error': None})

    def _on_unban(self, data):
        request_id = data.get('requestId')
        ip = str(data.get('ip') or '').strip()
        try:
            percorso = self._trova_file_blocchi()
            bloccati = self._leggi_blocchi()
            if percorso is None or bloccati is None:
                raise Exception("Non riesco a leggere l'elenco dei blocchi.")
            if ip not in bloccati:
                # Non e' un errore: e' la risposta giusta a "sbloccami" da chi non e' bloccato.
                self.sio.emit('command_unban_result', {
                    'requestId': request_id, 'success': True, 'riavvio': False, 'error': None})
                return

            # Si toglie SOLO la voce di questo indirizzo, riga per riga: riscrivere il file con
            # un serializzatore lo riformatterebbe tutto e butterebbe via quello che non e'
            # nostro. La voce e' una chiave a inizio riga seguita dalle proprie righe rientrate.
            with open(percorso, "r", encoding="utf-8") as f:
                righe = f.readlines()
            tenute = []
            dentro = False
            for riga in righe:
                if riga.startswith(ip + ":"):
                    dentro = True
                    continue
                if dentro:
                    if riga.strip() == "" or riga[:1].isspace():
                        continue
                    dentro = False
                tenute.append(riga)
            with open(percorso, "w", encoding="utf-8") as f:
                f.writelines(tenute)

            rimasti = self._leggi_blocchi()
            if rimasti is not None and ip in rimasti:
                raise Exception("L'indirizzo e' ancora nell'elenco dopo la cancellazione.")

            # IL FILE DA SOLO NON BASTA: Home Assistant tiene l'elenco anche in memoria e lo
            # rilegge solo all'avvio. Senza riavvio il blocco resterebbe in piedi e il
            # proprietario vedrebbe "fatto" mentre in casa nessuno rientra.
            self.logger.info(f"[CloudWorker] Sbloccato {ip}. Riavvio Home Assistant perche' rilegga l'elenco.")
            self.ha_connection.SendMsg({
                "type": "call_service", "domain": "homeassistant", "service": "restart"
            }, waitForResponse=False)

            self.sio.emit('command_unban_result', {
                'requestId': request_id, 'success': True, 'riavvio': True, 'error': None})
        except Exception as e:
            self.logger.error(f"[CloudWorker] Sblocco di {ip} fallito: {e}")
            self.sio.emit('command_unban_result', {
                'requestId': request_id, 'success': False, 'riavvio': False, 'error': str(e)})


    def _on_fetch_users(self, data):
        request_id = data.get('requestId')
        self.logger.info(f"[CloudWorker] Requested HA Users by Cloud. Request ID: {request_id}")
        
        try:
            if not self.ha_connection:
                raise Exception("HA WebSocket non inizializzato nel Worker")
                
            # Warm-up loop per gestire Race Conditions all'avvio dell'AddOn
            wait_time = 0
            while not getattr(self.ha_connection, 'IsConnected', False) and wait_time < 10:
                time.sleep(1)
                wait_time += 1
                
            if not getattr(self.ha_connection, 'IsConnected', False):
                raise Exception("HA WebSocket Auth is still pending or Offline.")
                
            response = self.ha_connection.SendAndReceiveMsg({"type": "get_states"})
            if not response or not response.get('success', False):
                err_msg = response.get('error', {}).get('message', 'Unknown Error') if response else 'Timeout Or Disconnected'
                raise Exception(f"Failed to fetch states from HA WebSocket: {err_msg}")
            
            all_states = response.get('result', [])
            if isinstance(all_states, dict):
                all_states = list(all_states.values())
            elif not isinstance(all_states, list):
                all_states = []
                
            tracked_users = [CloudWorker._tracked_id(e) for e in self._get_tracked_users()]
            nomi_accesso = self._tracked_usernames()
            filtered_users = []
            
            for state_obj in all_states:
                if not isinstance(state_obj, dict): continue
                
                entity_id = state_obj.get('entity_id', '')
                if not str(entity_id).startswith('person.'): continue
                
                attrs = state_obj.get('attributes', {})
                person_id = attrs.get('id') or attrs.get('user_id') or entity_id
                friendly_name = attrs.get('friendly_name', entity_id)
                
                if person_id not in tracked_users:
                    continue
                    
                filtered_users.append({
                    "id": person_id,
                    "auth_id": attrs.get('user_id'),
                    "name": friendly_name,
                    # Stringa vuota se non lo sappiamo. Chi disegna l'elenco NON deve ricavarlo
                    # dall'identificativo della persona: sono due cose diverse, e mostrare l'una
                    # al posto dell'altra dice al proprietario un nome di accesso che non esiste.
                    "username": nomi_accesso.get(person_id, ''),
                    "entity_id": entity_id
                })
                
            self.logger.info(f"[CloudWorker] Found {len(filtered_users)} standard users. Sending to Cloud.")
            self.sio.emit('command_fetch_users_result', {
                'requestId': request_id, 
                'users': filtered_users,
                'error': None
            })
        except Exception as e:
            self.logger.error(f"[CloudWorker] Error fetching users via HA Socket: {str(e)}")
            self.sio.emit('command_fetch_users_result', {
                'requestId': request_id, 
                'users': [],
                'error': f"Home Assistant Local WebSocket API Error: {str(e)}"
            })

    def _on_create_user(self, data):
        request_id = data.get('requestId')
        user_data = data.get('user_data', {})
        name = user_data.get('name', 'Nuovo Utente')
        # Il nome utente arriva dal portale, dove lo sceglie chi aggiunge la persona.
        #
        # Va letto ADESSO e non piu' avanti: qualche riga sotto user_data viene riassegnato con
        # la risposta di Home Assistant alla creazione dell'utenza, e da quel punto in poi
        # contiene tutt'altro. Leggerlo li' darebbe silenziosamente il valore sbagliato.
        nomeUtenteRichiesto = str(user_data.get('username') or '').strip()
        self.logger.info(f"[CloudWorker] Requested User Creation by Cloud: {name}")

        try:
            if not self.ha_connection:
                raise Exception("HA WebSocket non inizializzato")
                
            # Warm-up loop
            wait_time = 0
            while not getattr(self.ha_connection, 'IsConnected', False) and wait_time < 10:
                time.sleep(1)
                wait_time += 1
                
            if not getattr(self.ha_connection, 'IsConnected', False):
                raise Exception("HA WebSocket Auth is still pending or Offline.")
                
            # STEP 1: Creazione Utente di Sistema (NON Amministratore)
            auth_response = self.ha_connection.SendMsg({
                "type": "config/auth/create",
                "name": name,
                "group_ids": ["system-users"]
            }, waitForResponse=True)
            
            if not auth_response or not auth_response.get('success', False):
                err_msg = auth_response.get('error', {}).get('message', 'Unknown Error') if auth_response else 'Timeout Or Disconnected'
                raise Exception(f"Failed to create System User via HA WebSocket: {err_msg}")
                
            auth_result_raw = auth_response.get('result', {})
            user_data = auth_result_raw.get('user', auth_result_raw) if isinstance(auth_result_raw, dict) else {}
            auth_user_id = user_data.get('id')
            
            if not auth_user_id:
                raise Exception("System User creato ma ID mancante nella risposta!")
                
            # IMPORTANTE: Creiamo le credenziali atomiche SOLO quando siamo sicuri che l'utente sia stato "digerito"
            # da Home Assistant. Eseguiamo un polling della lista auth di HA.
            user_ready = False
            for _ in range(15):
                check_resp = self.ha_connection.SendAndReceiveMsg({"type": "config/auth/list"}, timeout=2.0)
                if check_resp and check_resp.get('success'):
                    users_list = check_resp.get('result', [])
                    if any(u.get('id') == auth_user_id for u in users_list):
                        user_ready = True
                        break
                time.sleep(0.3)
                
            if not user_ready:
                raise Exception("Home Assistant non ha persistito l'utente di sistema nei tempi previsti (Timeout).")
                
            # LA PASSWORD INIZIALE: LUNGA, CASUALE PER DAVVERO, E CHE NON ESCE DA QUI.
            #
            # Era un PIN di 8 cifre generato con random.choices, cioe' con il Mersenne Twister:
            # un generatore riproducibile, non crittografico. Dieci milioni di combinazioni su un
            # account raggiungibile da internet.
            #
            # E non e' un valore che vive un istante: resta sull'account finche' la persona non
            # apre il proprio invito. Se l'invito scade inutilizzato dopo 48 ore quella password
            # resta li', e non la sostituisce piu' nessuno.
            #
            # Qui non serve che sia leggibile o digitabile: nessuno la vede mai. La persona
            # ricevera' la sua dalla generazione dell'invito, che la sovrascrive. Quindi tanto
            # vale che sia lunga: 24 byte casuali in esadecimale, 192 bit.
            #
            # os.urandom E NON secrets.token_urlsafe, che sarebbe la forma idiomatica: in questo
            # pacchetto esiste un nostro secrets.py, e "import secrets" prende quello ogni volta
            # che la cartella del pacchetto finisce prima nel percorso di ricerca. Provato: da
            # dentro la cartella risolve al nostro, che token_urlsafe non ce l'ha. Oggi l'add-on
            # parte da /app e andrebbe bene lo stesso, ma il giorno che qualcuno lo lancia
            # altrimenti si romperebbe proprio qui, sulla generazione di una credenziale.
            password_iniziale = os.urandom(24).hex()
            # Il nome utente scelto, ripulito: minuscolo, spazi in punti, e solo caratteri che
            # Home Assistant accetta in un nome di accesso. Se non ne arriva uno, si ricade sul
            # nome reale come si e' sempre fatto.
            auth_username = re.sub(r'[^a-z0-9._-]', '', nomeUtenteRichiesto.lower().replace(" ", "."))
            if len(auth_username) == 0:
                auth_username = re.sub(r'[^a-z0-9._-]', '', name.lower().replace(" ", "."))
            
            self.logger.info(f"[CloudWorker] Setting initial credentials for {auth_username}...")
            
            # Utilizziamo SendAndReceiveMsg con un timeout breve per evitare blocchi permanenti se HA dovesse droppare di nuovo
            # Evita il blocco di 30s asincrono.
            cred_response = self.ha_connection.SendAndReceiveMsg({
                "type": "config/auth_provider/homeassistant/create",
                "user_id": auth_user_id,
                "username": auth_username,
                "password": password_iniziale
            }, timeout=3.0)
            
            if not cred_response or not cred_response.get('success'):
                err_msg = cred_response.get('error', {}).get('message', 'Timeout o Drop HA') if cred_response else 'Timeout'
                self.logger.error(f"[CloudWorker] Failed to set initial PIN for {auth_username}: {err_msg}")
                raise Exception(f"Impossibile creare le credenziali utente HA: {err_msg}")

            # STEP 2: Creazione Persona Esplicita collegata allo User e assegnabile a dispositivi
            person_response = self.ha_connection.SendMsg({
                "type": "person/create",
                "name": name,
                "user_id": auth_user_id,
                "device_trackers": []
            }, waitForResponse=True)
            
            if not person_response or not person_response.get('success', False):
                err_msg = person_response.get('error', {}).get('message', 'Unknown Error') if person_response else 'Timeout Or Disconnected'
                raise Exception(f"Auth Success, but Person creation via HA WebSocket failed: {err_msg}")
                
            person_result_raw = person_response.get('result', {})
            person_data = person_result_raw.get('person', person_result_raw) if isinstance(person_result_raw, dict) else {}
            person_id = person_data.get('id', auth_user_id) # Fallback su auth id
            
            # Tracciamo l'ID persona generato
            self._add_tracked_user(person_id, auth_username)
                
            self.logger.info(f"[CloudWorker] Successfully orchestrated User '{name}' -> Person '{person_id}' in HA.")
            
            self.sio.emit('command_create_user_result', {
                'requestId': request_id, 
                'success': True,
                # La password iniziale NON viaggia verso il cloud: nessuno la legge, e una
                # credenziale che attraversa la rete senza che serva a niente e' solo una
                # credenziale in piu' che puo' finire in un log.
                # id = la PERSONA, auth_id = l'UTENTE. Sono due cose diverse e servono
                # entrambe: la persona per rinominarla e cancellarla, l'utente per toccarne le
                # credenziali. Prima usciva solo la persona, e chi doveva impostare la password
                # si ritrovava un identificativo che l'anagrafica degli utenti non conosce.
                'result': {'name': name, 'id': person_id, 'auth_id': auth_user_id,
                           'username': auth_username},
                'error': None
            })
        except Exception as e:
            self.logger.error(f"[CloudWorker] Error creating user: {str(e)}")
            self.sio.emit('command_create_user_result', {
                'requestId': request_id, 
                'success': False,
                'error': str(e)
            })

    def _on_update_user(self, data):
        request_id = data.get('requestId')
        person_id = data.get('person_id')
        auth_id = data.get('auth_id')
        new_name = data.get('new_name', '')
        self.logger.info(f"[CloudWorker] Requested User Update: {person_id} -> {new_name}")

        try:
            if not self.ha_connection:
                raise Exception("HA WebSocket non inizializzato")
            
            # Auth alias update
            if auth_id:
                self.ha_connection.SendAndReceiveMsg({
                    "type": "config/auth/update",
                    "user_id": auth_id,
                    "name": new_name
                })
            
            # Person layer update
            person_response = self.ha_connection.SendAndReceiveMsg({
                "type": "person/update",
                "person_id": person_id,
                "name": new_name
            })
            
            if not person_response or not person_response.get('success', False):
                err_msg = person_response.get('error', {}).get('message', 'Unknown Error') if person_response else 'Timeout'
                raise Exception(f"Failed to update User via HA WebSocket: {err_msg}")

            self.sio.emit('command_update_user_result', {
                'requestId': request_id, 'success': True, 'error': None
            })
        except Exception as e:
            self.logger.error(f"[CloudWorker] Error updating user: {str(e)}")
            self.sio.emit('command_update_user_result', {
                'requestId': request_id, 'success': False, 'error': str(e)
            })

    def _on_delete_user(self, data):
        request_id = data.get('requestId')
        person_id = data.get('person_id')
        auth_id = data.get('auth_id')
        self.logger.info(f"[CloudWorker] Requested User Deletion: {person_id} / Auth: {auth_id}")

        try:
            if not self.ha_connection:
                raise Exception("HA WebSocket non inizializzato")
                
            self.logger.info('Purging Person Layer...')
            p_resp = self.ha_connection.SendAndReceiveMsg({
                "type": "person/delete",
                "person_id": person_id
            })
            if not p_resp or not p_resp.get('success'):
                err_msg = p_resp.get('error', {}) if p_resp else 'Timeout Person'
                self.logger.error(f"Failed to delete Person {person_id}: {err_msg}")

            if auth_id:
                self.logger.info('Purging System Auth Layer...')
                u_resp = self.ha_connection.SendAndReceiveMsg({
                    "type": "config/auth/delete",
                    "user_id": auth_id
                })
                if not u_resp or not u_resp.get('success'):
                    err_msg = u_resp.get('error', {}) if u_resp else 'Timeout Auth'
                    raise Exception(f"Failed to delete System User: {err_msg}")
            else:
                self.logger.warning(f"No auth_id provided for {person_id}. Attempting fallback lookup via config/auth/list...")
                # Fallback to search by name or ID
                a_resp = self.ha_connection.SendAndReceiveMsg({"type": "config/auth/list"})
                if a_resp and a_resp.get('success'):
                    # The name is often identical to the person ID string or name slug
                    found = False
                    for usr in a_resp.get('result', []):
                        if usr.get('name', '').lower() == str(person_id).replace('_', ' ').lower() or usr.get('id') == person_id:
                            self.logger.info(f"Fallback found matching user: {usr.get('name')} ({usr.get('id')}). Purging...")
                            fallback_u_resp = self.ha_connection.SendAndReceiveMsg({
                                "type": "config/auth/delete",
                                "user_id": usr.get('id')
                            })
                            if not fallback_u_resp or not fallback_u_resp.get('success'):
                                raise Exception(f"Fallback delete failed: {fallback_u_resp.get('error') if fallback_u_resp else 'Timeout'}")
                            found = True
                            break
                    if not found:
                        self.logger.warning(f"Could not find matching System User for {person_id}. It might have been already deleted.")
                else:
                    self.logger.error("Could not fetch user list for fallback delete.")

            self._remove_tracked_user(person_id)
            self.logger.info(f"[CloudWorker] Successfully expunged {person_id}")

            self.sio.emit('command_delete_user_result', {
                'requestId': request_id, 'success': True, 'error': None
            })
        except Exception as e:
            self.logger.error(f"[CloudWorker] Error deleting user: {str(e)}")
            self.sio.emit('command_delete_user_result', {
                'requestId': request_id, 'success': False, 'error': str(e)
            })

    def _on_generate_password(self, data):
        """
        Receives: { reqId, auth_id, password }
        Calls HA: auth/admin_change_password to set a new password for the user
        Acks back: command_generate_password_result { reqId, success, error }
        """
        req_id = data.get('reqId')
        auth_id = data.get('auth_id')
        password = data.get('password')
        username = data.get('username')
        
        self.logger.info(f"[CloudWorker] Generating one-time password for auth_id={auth_id}, username={username}, reqId={req_id}")
        
        try:
            if not self.ha_connection:
                raise Exception("HA WebSocket non inizializzato")
                
            wait_time = 0
            while not getattr(self.ha_connection, 'IsConnected', False) and wait_time < 10:
                time.sleep(1)
                wait_time += 1
            if not getattr(self.ha_connection, 'IsConnected', False):
                raise Exception("HA WebSocket not connected.")
                
            # L'IDENTIFICATIVO SI CERCA PRIMA PER CORRISPONDENZA ESATTA, POI PER NOME.
            #
            # Il ripiego per nome confrontava il nome VISUALIZZATO con il nome di ACCESSO, e
            # funzionava solo finche' il secondo veniva derivato dal primo. Da quando chi
            # aggiunge una persona sceglie il nome di accesso che vuole, "Tiberio" con accesso
            # "oneshot" non corrispondeva piu' a niente: restava l'identificativo della persona,
            # che l'anagrafica degli utenti non conosce, e la creazione delle credenziali
            # falliva con "User not found".
            #
            # Peggio: quel confronto veniva provato PRIMA di quello esatto, quindi un altro
            # utente il cui nome visualizzato somigliasse al nome di accesso cercato poteva
            # essere scelto al posto di quello giusto, e si sarebbe cambiata la password alla
            # persona sbagliata.
            resolved_user_id = None
            auth_list_msg = {"type": "config/auth/list"}
            list_response = self.ha_connection.SendAndReceiveMsg(auth_list_msg)
            utenti = []
            if list_response and list_response.get('success'):
                utenti = list_response.get('result', []) or []

            for u in utenti:
                if auth_id and u.get('id') == auth_id:
                    resolved_user_id = u.get('id')
                    self.logger.info(f"[CloudWorker] Utente trovato per identificativo: {resolved_user_id}")
                    break

            if resolved_user_id is None and username:
                target = username.lower().replace('_', '.')
                for u in utenti:
                    if u.get('name', '').lower().replace(' ', '.') == target:
                        resolved_user_id = u.get('id')
                        self.logger.warning(f"[CloudWorker] Utente trovato solo per nome ({target}): {resolved_user_id}")
                        break

            if resolved_user_id is None:
                # Non si prosegue con l'identificativo ricevuto sperando che vada bene: se non
                # e' un utente, la creazione fallisce piu' avanti con un messaggio che non dice
                # perche'. Meglio dirlo qui.
                raise Exception(f"Nessun utente di Home Assistant corrisponde a {auth_id!r} o al nome di accesso {username!r}.")

            # Dal momento che `admin_change_password` fallisce sempre con "Unauthorized" se il token dell'addon
            # non ha privilegi 'Owner' (gli Addon solitamente hanno solo 'Admin'), noi AGGIRIAMO il problema
            # eliminando esplicitamente le credenziali e ricreandole, dato che `create` e `delete` richiedono solo 'Admin'.
            
            if username:
                self.logger.info(f"[CloudWorker] Deleting old credentials for {username} if they exist...")
                self.ha_connection.SendAndReceiveMsg({
                    "type": "config/auth_provider/homeassistant/delete",
                    "username": username
                })
            else:
                self.logger.warning("[CloudWorker] Username is missing, skipping credential deletion. Re-create might fail with already_exists.")

            # Create new credentials
            self.logger.info(f"[CloudWorker] Creating fresh credentials for user_id {resolved_user_id}...")
            fallback_username = username if username else f"user.{resolved_user_id[:6]}"
            resp = self.ha_connection.SendAndReceiveMsg({
                "type": "config/auth_provider/homeassistant/create",
                "user_id": resolved_user_id,
                "username": fallback_username,
                "password": password
            })

            if not resp or not resp.get('success'):
                err_msg = resp.get('error', {}).get('message', 'Unknown') if resp else 'Timeout'
                raise Exception(f"HA credential creation failed: {err_msg}")

            self.logger.info(f"[CloudWorker] Password set successfully for {auth_id}")
            self.sio.emit('command_generate_password_result', {'reqId': req_id, 'success': True})
        except Exception as e:
            self.logger.error(f"[CloudWorker] _on_generate_password error: {e}")
            self.sio.emit('command_generate_password_result', {'reqId': req_id, 'success': False, 'error': str(e)})

    # Alzata dal reporter quando la registrazione dell'hub e' confermata dal backend.
    #
    # Serve perche' il worker e il reporter sono thread distinti e partono insieme: la socket
    # arrivava al backend PRIMA che la riga dell'apparecchio esistesse, il controllo delle
    # credenziali non la trovava, e ogni avvio produceva un errore che sembrava un guasto e non
    # lo era. Si risolveva da solo al ritentativo dopo dieci secondi, ma intanto lo scriveva.
    #
    # E' un'attesa con scadenza, non un blocco: se la registrazione non arriva entro il tempo
    # massimo si prova lo stesso, perche' un hub che non riesce a registrarsi deve comunque
    # tentare di collegarsi invece di restare fermo in silenzio.
    RegistrazioneConfermata = threading.Event()
    c_AttesaRegistrazioneSec = 90

    def _run_loop(self):
        # La socket del worker e il reporter devono parlare con lo STESSO backend:
        # quando erano due costanti separate, puntarne una sola a un ambiente di prova
        # lasciava l'hub registrato di qua e connesso di la', senza nessun errore.
        cloud_url = Backend.BaseUrl()
        self.logger.info(f"[CloudWorker] Connecting to {cloud_url}...")

        if not CloudWorker.RegistrazioneConfermata.wait(timeout=CloudWorker.c_AttesaRegistrazioneSec):
            self.logger.warning(f"[CloudWorker] Registrazione non confermata entro {CloudWorker.c_AttesaRegistrazioneSec}s: mi collego lo stesso.")

        while self._running:
            try:
                if not self.sio.connected:
                    self.sio.connect(cloud_url, transports=['websocket', 'polling'], auth={
                        'plugin_id': self.plugin_id,
                        'private_key': self.private_key
                    })
            except Exception as e:
                self.logger.warning(f"[CloudWorker] Connection to cloud failed, retrying in 10s... ({str(e)})")
                
            time.sleep(10)
            
# Globale Singleton
CloudWorkerInstance = CloudWorker()
