import os
import time
import subprocess
import threading
import logging
import requests
import json
from enum import Enum
from typing import Optional

class CloudflareStatus(Enum):
    STOPPED = 1
    REQUESTING_TOKEN = 2
    RETRY_WAIT = 3
    RUNNING = 4

class CloudflareManager:
    """
    Manages the lifecycle of the zero-trust cloudflared daemon.
    Requests a token from the Sweetplace Backend API upon boot, and passes it to the local CLI binary.
    """

    def __init__(self, logger: logging.Logger):
        self.Logger = logger
        self.Status = CloudflareStatus.STOPPED
        self.Lock = threading.Lock()

        self.Thread = None
        self.Subprocess = None

        self.PluginId = None
        self.ApiUrl = None

        # Indirizzo pubblico con cui l'hub e' raggiungibile da Internet. Lo assegna il backend
        # nella risposta al provisioning: senza tenerlo, l'hub conosce il proprio indirizzo e non
        # lo dice a nessuno, e per saperlo bisogna interrogare il database. Lo legge il pannello.
        # Scritto dal thread del manager e letto dal thread del web server: e' una sola
        # assegnazione di stringa, non serve il lock.
        self.PublicUrl:Optional[str] = None

        # Vero da quando cloudflared ha registrato almeno una connessione, cioe' da quando
        # l'hub e' davvero raggiungibile da Internet.
        #
        # E' il segnale che il pannello usa per dire "hub attivo". Prima lo diceva l'handshake
        # con il servizio di terzi da cui passava l'accesso remoto: adesso l'accesso remoto e'
        # questo tunnel, quindi lo stato lo deve dare chi lo gestisce. Scritto dal thread del
        # manager e letto da quello del web server: e' un booleano, non serve il lock.
        self.TunnelActive = False

        self._shutdown_event = threading.Event()

        # Verify binary exists (downloaded by Dockerfile)
        self.BinaryPath = "/usr/local/bin/cloudflared"

    # SERVE LA CHIAVE PRIVATA, NON PIU' UN IDENTIFICATIVO.
    #
    # Il gettone del tunnel e' la cosa piu' pesante che il backend consegni: chi ce l'ha diventa
    # l'altro capo dell'indirizzo pubblico di quella casa. Prima bastava presentare il plugin_id o
    # il MAC, che segreti non sono — il primo compare nei registri e nel pannello, il secondo lo
    # legge chiunque sia stato una volta sulla rete di casa.
    #
    # La strada del MAC e' tolta anche da qui: tenerla come ripiego avrebbe rimesso in piedi
    # esattamente il modo di identificarsi che il backend ha smesso di accettare.
    def Start(self, api_url: str, plugin_id: str = None, private_key: str = None):
        with self.Lock:
            if self.Thread is not None:
                return

            if not plugin_id or not private_key:
                self.Logger.error("[CloudflareManager] Servono plugin_id e chiave privata per chiedere il tunnel.")
                return

            self.PluginId = plugin_id
            self.PrivateKey = private_key
            self.ApiUrl = api_url
            self.Status = CloudflareStatus.REQUESTING_TOKEN
            self._shutdown_event.clear()

            self.Thread = threading.Thread(target=self._run_loop, name="CloudflareManagerThread", daemon=True)
            self.Thread.start()
            self.Logger.info("[CloudflareManager] Orchestration thread started.")

    def Stop(self):
        with self.Lock:
            if self.Thread is None:
                return

            self.Logger.info("[CloudflareManager] Stopping Zero Trust orchestration...")
            self._shutdown_event.set()

            if self.Subprocess is not None:
                try:
                    self.Subprocess.terminate()
                    self.Subprocess.wait(timeout=5)
                except Exception as e:
                    self.Logger.error(f"[CloudflareManager] Failed to gracefully terminate cloudflared: {e}")
                finally:
                    self.Subprocess = None

            self.Status = CloudflareStatus.STOPPED
            self.Thread = None

    def _run_loop(self):
        if not os.path.exists(self.BinaryPath):
            self.Logger.error(f"[CloudflareManager] CRITICAL ERROR: cloudflared binary not found at {self.BinaryPath}. Check Dockerfile build.")
            return

        while not self._shutdown_event.is_set():
            token = self._request_token()

            if not token:
                # If token was not retrieved (e.g. backend error, or backend not configured)
                self.Logger.warning("[CloudflareManager] Token not retrieved. Retrying in 60s...")
                self.Status = CloudflareStatus.RETRY_WAIT
                self._shutdown_event.wait(60)
                continue

            # If backend explicitly returned empty but HTTP 200/503 (feature disabled)
            if token == "__DISABLED__":
                self.Logger.info("[CloudflareManager] Cloudflare integration disabled by backend. Manager sleeping indefinitely.")
                self.Status = CloudflareStatus.STOPPED
                break

            # Token received. Spawn the daemon.
            self.Logger.info("[CloudflareManager] Tunnel Token acquired. Spawning cloudflared daemon...")
            self.Status = CloudflareStatus.RUNNING

            try:
                # Spawn cloudflared run --token [TOKEN] --no-autoupdate
                self.Subprocess = subprocess.Popen(
                    [self.BinaryPath, "tunnel", "--no-autoupdate", "run", "--token", token],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1
                )

                # Consume output silently or log warnings
                for line in self.Subprocess.stdout:
                    if self._shutdown_event.is_set():
                        break

                    if "Registered tunnel connection" in line:
                        # cloudflared apre quattro connessioni verso Cloudflare per ridondanza e
                        # ne registra una alla volta: la riga arriva quattro volte di fila.
                        # Interessa la transizione, non ogni singola connessione — le altre
                        # finiscono in debug, dove servono a chi sta diagnosticando.
                        if not self.TunnelActive:
                            self.TunnelActive = True
                            self.Logger.info("[CloudflareManager] Tunnel attivo: l'hub e' raggiungibile da Internet.")
                        else:
                            self.Logger.debug("[CloudflareManager] Connessione aggiuntiva del tunnel registrata.")
                    elif "ERR" in line:
                        self.Logger.error(f"[cloudflared] {line.strip()}")

                # Wait for subprocess finish
                self.Subprocess.wait()
                self.Subprocess = None
                # Il processo del tunnel e' uscito: da qui l'hub non e' piu' raggiungibile, e il
                # pannello deve tornare a dirlo invece di restare fermo sull'ultimo esito buono.
                self.TunnelActive = False

            except Exception as e:
                self.Logger.error(f"[CloudflareManager] Subprocess runtime exception: {e}")

            if not self._shutdown_event.is_set():
                self.Logger.warning("[CloudflareManager] cloudflared daemon exited unexpectedly. Respawning in 10s...")
                self._shutdown_event.wait(10)


    def _request_token(self) -> str:
        self.Status = CloudflareStatus.REQUESTING_TOKEN
        endpoint = f"{self.ApiUrl}/api/cloudflare/provision"

        try:
            # La stessa coppia con cui l'apparecchio si registra e apre la propria socket.
            payload = {"plugin_id": self.PluginId, "private_key": self.PrivateKey}

            res = requests.post(endpoint, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                # Il valore arriva dalla rete: va validato qui, non dove viene mostrato.
                domain = data.get("domain", None)
                if isinstance(domain, str) and domain.startswith("https://"):
                    self.PublicUrl = domain
                    self.Logger.info(f"[CloudflareManager] Indirizzo pubblico dell'hub: {domain}")
                elif domain is not None:
                    self.Logger.warning(f"[CloudflareManager] Indirizzo pubblico inatteso dal backend, ignorato: {domain!r}")
                return data.get("token", "")
            elif res.status_code == 503:
                # Disabled administratively
                return "__DISABLED__"
            elif res.status_code in (400, 403):
                # ALL'ACCENSIONE QUESTO E' NORMALE, E IL MESSAGGIO NON DEVE DIRE IL CONTRARIO.
                #
                # La registrazione dell'apparecchio e questa richiesta partono da due thread
                # diversi, senza ordinamento fra loro: finche' la riga non e' scritta, il backend
                # non ha nessuna chiave con cui confrontare e rifiuta. Si risolve da solo al
                # ritentativo dopo 60 secondi.
                #
                # Diventa un problema vero solo se non smette. Il livello e' quindi un avviso e
                # non un errore, e la frase dice tutte e due le cose: cosa aspettarsi adesso, e
                # cosa guardare se continua.
                self.Logger.warning(
                    f"[CloudflareManager] Il backend non ha consegnato il tunnel (HTTP {res.status_code}). "
                    "All'avvio e' normale: la registrazione dell'apparecchio puo' non essere ancora "
                    "arrivata, e si ritenta fra 60 secondi. Se non smette, l'apparecchio sta "
                    "presentando una chiave privata diversa da quella che il backend ha in archivio.")
                return None
            else:
                self.Logger.error(f"[CloudflareManager] Backend refused token request: HTTP {res.status_code}")
                return None

        except Exception as e:
            self.Logger.error(f"[CloudflareManager] Network error contacting {endpoint}: {e}")
            return None
