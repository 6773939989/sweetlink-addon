# SPDX-License-Identifier: AGPL-3.0-or-later
# Derivato dall'add-on Home Assistant di Homeway.io (https://github.com/homewayio/AddOn),
# a sua volta derivato da OctoEverywhere. Vedi NOTICE.md.
# Modificato da Sweetplace (M2R S.r.l.), 2026.

import os
import time
import logging
import threading
import traceback
import requests
from typing import Any, Dict, List, Optional, Set

from sweetlink.mdns import MDns
from sweetlink.sentry import Sentry
from sweetlink.hostcommon import HostCommon
from sweetlink.telemetry import Telemetry
from sweetlink.sweetlinkcore import Sweetlink
from sweetlink.localip import LocalIpHelper
from sweetlink.httprequest import HttpRequest
from sweetlink.compression import Compression
from sweetlink.httpsessions import HttpSessions
from sweetlink.Proto.AddonTypes import AddonTypes
from sweetlink.commandhandler import CommandHandler
from sweetlink.interfaces import IStateChangeHandler

from .backend import Backend
from .config import Config
from .secrets import Secrets
from .version import Version
from .logger import LoggerInit
from .webserver import WebServer
from .ha.configmanager import ConfigManager
from .ha.webrtcmanager import WebRtcManager
from .ha.connection import Connection
from .ha.eventhandler import EventHandler
from .ha.serverinfo import ServerInfo
from .ha.serverdiscovery import ServerDiscovery
from .ha.homecontext import HomeContext
from .ha.trackerinterceptor import TrackerInterceptor
from .cloud_worker import CloudWorkerInstance
from .cloudflaremanager import CloudflareManager
from .haadmin import HaAdmin
from .imageprep import ImagePrep
from .supervisorapi import SupervisorApi


# This file is the main host for the linux service.
class LinuxHost(IStateChangeHandler):

    # Bit meno significativo del primo ottetto (I/G in IEEE 802): se acceso, l'indirizzo non
    # designa una singola scheda e non puo' essere l'indirizzo di una stazione.
    c_MacBitMulticast = 0x01

    # Valore di /sys/class/net/<if>/addr_assign_type che il kernel usa per NET_ADDR_PERM:
    # l'indirizzo viene dall'hardware ed e' permanente. Gli altri valori (1 random, 2 stolen,
    # 3 set via software) indicano indirizzi che possono cambiare fra un avvio e l'altro.
    c_MacAssignPermanent = "0"


    # Chiede al Supervisor di riavviare questo add-on, e dice se la richiesta e' stata accettata.
    #
    # Serve dopo aver rigenerato l'identita': plugin_id e private_key vengono letti all'avvio da
    # una decina di componenti (worker cloud, manager del tunnel, connessione remota, telemetria)
    # che tengono la propria copia. Cambiarli sotto i piedi senza ripartire lascerebbe l'hub
    # registrato con l'identita' nuova e operativo con quella vecchia: peggio del problema.
    #
    # Non si esce e basta: il riavvio automatico dipende dal watchdog, che e' un'impostazione
    # dell'utente ed e' spenta di default. Un add-on che esce contando su quello resta spento.
    @staticmethod
    def RequestSelfRestart(logger:logging.Logger) -> bool:
        return SupervisorApi.RestartSelf(logger)


    # Vero se la stringa ha la forma di un indirizzo MAC di stazione.
    # Non dice nulla sulla stabilita': quella la stabilisce il kernel, vedi GetHardwareMacs.
    @staticmethod
    def IsWellFormedMac(mac:str) -> bool:
        if len(mac) != 17:
            return False
        parts = mac.split(":")
        if len(parts) != 6:
            return False
        for p in parts:
            if len(p) != 2:
                return False
            try:
                int(p, 16)
            except ValueError:
                return False
        if mac == "00:00:00:00:00:00":
            return False
        # Un indirizzo multicast non e' l'indirizzo di una scheda. E' anche il segno che
        # uuid.getnode() ha inventato un numero casuale invece di leggere l'hardware.
        if int(parts[0], 16) & LinuxHost.c_MacBitMulticast:
            return False
        return True


    # Il MAC che il pannello mostra e passa al portale. E' quello con cui il reporter si e'
    # registrato davvero; prima che il reporter parta si ripiega sulla scansione, cosi' il
    # pannello e' utile fin dal primo secondo. Stringa vuota se non c'e' nulla di determinabile.
    def GetPanelMac(self) -> str:
        reported = self.ReportedMacs
        if len(reported) > 0:
            return reported[0]
        hardware = LinuxHost.GetHardwareMacs()
        if len(hardware) > 0:
            return hardware[0]
        return ""


    # Dice se l'utente di Home Assistant indicato e' un amministratore.
    # None quando non e' determinabile: chi chiama deve trattarlo come un no.
    def IsHaUserAdmin(self, userId:str) -> Optional[bool]:
        return HaAdmin.IsUserAdmin(self.Logger, self.HaConnection, userId)


    # L'identificativo dell'utente proprietario, ricavato dal suo nome di accesso.
    #
    # Passa dal worker perche' li' c'e' la connessione a Home Assistant, la sola che possa dire
    # quale utente porta quel nome di accesso.
    def RisolviProprietario(self, nomeAccesso:Optional[str]) -> Optional[str]:
        try:
            from .cloud_worker import CloudWorkerInstance
            return CloudWorkerInstance.RisolviProprietario(nomeAccesso)
        except Exception as e:
            self.Logger.warning(f"Proprietario non risolvibile: {e}")
            return None


    # Dice al backend l'identificativo giusto quando quello che ci aveva mandato non lo era.
    def CorreggiProprietario(self, authId:str) -> None:
        try:
            from .cloud_worker import CloudWorkerInstance
            CloudWorkerInstance.CorreggiProprietario(authId)
        except Exception as e:
            self.Logger.warning(f"Correzione del proprietario non spedita: {e}")


    # Le persone di casa, o None se non si e' potuto chiedere.
    #
    # Passa dal worker perche' li' c'e' la connessione a Home Assistant e lo schedario dei nomi di
    # accesso. Il server web non ha ne' l'una ne' l'altro, e non deve averli: disegna e basta.
    def ElencoMembri(self):
        try:
            from .cloud_worker import CloudWorkerInstance
            return CloudWorkerInstance.ElencoMembri()
        except Exception as e:
            self.Logger.warning(f"Elenco dei membri non disponibile: {e}")
            return None


    # L'indirizzo con cui aprire il portale gia' dentro, oppure None se non e' ottenibile.
    #
    # Sta qui e non nel server web perche' qui ci sono i segreti: il backend riconosce l'hub dalla
    # coppia plugin_id + private_key, la stessa con cui l'add-on apre la propria socket. Il server
    # web la chiave non ce l'ha e non deve averla.
    #
    # Nessun ripiego silenzioso: se la richiesta non riesce si restituisce None e chi ha premuto
    # il pulsante vede un errore. Aprire comunque la radice del portale lo rimanderebbe al primo
    # passo della rivendicazione, cioe' esattamente il difetto che questa strada esiste per
    # togliere, e sembrerebbe un difetto invece di un guasto momentaneo.
    def ChiediLinkPannello(self) -> Optional[str]:
        try:
            pluginId = self.GetPluginId()
            privateKey = self.GetPrivateKey()
            if pluginId is None or privateKey is None:
                self.Logger.warning("Ingresso dal pannello: identita' non disponibile.")
                return None
            risposta = requests.post(Backend.BaseUrl() + "/device/panel-link",
                                     json={"plugin_id": pluginId, "private_key": privateKey},
                                     timeout=10)
            if risposta.status_code != 200:
                self.Logger.warning(f"Ingresso dal pannello: il backend ha risposto {risposta.status_code}.")
                return None
            url = risposta.json().get("url")
            # Il valore arriva dalla rete: si controlla qui, non dove viene aperto.
            if isinstance(url, str) and url.startswith("https://"):
                return url
            self.Logger.warning(f"Ingresso dal pannello: indirizzo inatteso dal backend: {url!r}")
            return None
        except Exception as e:
            self.Logger.warning(f"Ingresso dal pannello fallito: {e}")
            return None


    # Il referto che il pannello mostra prima di clonare: cosa c'e' ancora sul disco che non
    # deve finire dentro l'immagine. Non modifica niente.
    def BuildImagePrepReport(self) -> List[Dict[str, str]]:
        return ImagePrep.BuildReport(self.Logger, self.Secrets, LinuxHost.GetHardwareMacs(), self.HaConnection)


    # Azzera l'identita' di questo apparecchio e ferma l'add-on.
    #
    # L'arresto e' rimandato di qualche secondo perche' la risposta HTTP deve arrivare al
    # pannello prima che il processo se ne vada: altrimenti chi ha premuto il pulsante vede una
    # connessione caduta e non sa se l'operazione e' riuscita.
    #
    # Fermarsi e' necessario, non estetico: se l'add-on restasse vivo dopo l'azzeramento,
    # rigenererebbe subito plugin_id e chiave privata, e l'immagine che si sta per clonare ne
    # conterrebbe una nuova invece di nessuna.
    def WipeForCloning(self) -> List[Dict[str, str]]:
        if len(self.StorageDir) == 0:
            # Non e' mai capitato e non dovrebbe capitare, ma un azzeramento a meta' e' molto
            # peggio di un azzeramento che si rifiuta di partire.
            self.Logger.error("Azzeramento rifiutato: la cartella dati non e' nota.")
            return [{"level": ImagePrep.c_LevelBlock, "title": "Azzeramento",
                     "detail": "Cartella dati non nota: non ho toccato niente. Riavvia l'add-on e riprova."}]

        # Alzata PRIMA di cancellare, non dopo: fra la cancellazione e l'arresto il reporter e'
        # ancora vivo, e sul suo percorso di collisione rigenererebbe l'identita' appena tolta.
        self.WipedForCloning = True
        actions = ImagePrep.Wipe(self.Logger, self.Secrets, self.StorageDir, self.HaConnection)

        # Ci si ferma SOLO se e' andato tutto bene. Se qualcosa e' rimasto sul disco, l'unico
        # strumento che l'operatore ha per capire cosa e' questo pannello, e spegnersi glielo
        # toglierebbe di mano proprio nel momento in cui gli serve. Diversi messaggi gli dicono
        # di ricontrollare il referto: quell'istruzione dev'essere eseguibile.
        if ImagePrep.HasBlockers(actions):
            self.Logger.error("Azzeramento incompleto: NON mi fermo, cosi' il referto resta consultabile.")
            actions.append({"level": ImagePrep.c_LevelWarn, "title": "Arresto",
                            "detail": "L'add-on resta acceso perche' tu possa rileggere il referto. Risolvi, riazzera, e spegni solo quando e' tutto verde."})
            return actions

        actions.append({"level": ImagePrep.c_LevelOk, "title": "Arresto",
                        "detail": "L'add-on si sta fermando. Se questa pagina resta viva, l'arresto non e' riuscito: fermalo a mano prima di spegnere."})
        threading.Timer(3.0, self._StopAfterWipe).start()
        return actions


    # Arresto differito, perche' la risposta HTTP deve arrivare al pannello prima che il
    # processo se ne vada: altrimenti chi ha premuto il pulsante vede una connessione caduta e
    # non sa se l'operazione e' riuscita.
    def _StopAfterWipe(self) -> None:
        if not SupervisorApi.StopSelf(self.Logger):
            self.Logger.error("Azzeramento: l'add-on NON e' riuscito a fermarsi. Fermalo a mano prima di spegnere l'apparecchio.")


    # Restituisce i MAC delle schede di rete fisiche, in maiuscolo, ordinati e senza duplicati.
    #
    # Con includeRemovable=False esclude le schede su bus rimovibile. E' la forma da usare
    # SEMPRE — sia per il vincolo hardware sia per la registrazione — e le due cose devono
    # guardare lo stesso insieme, altrimenti divergono senza che nessuna delle due sia sbagliata.
    #
    # L'apparecchio e' un Raspberry Pi e ha due schede saldate, Ethernet e Wi-Fi: quelle sono
    # tutta l'identita' hardware che esiste, non si spostano e non si tolgono. Non ci sono
    # dongle da censire, quindi il ripiego a includeRemovable=True qui sopra e' solo il valore
    # storico della firma e nessun chiamante dovrebbe appoggiarcisi.
    #
    # Puo' restituire una lista VUOTA: e' un esito legittimo e va gestito da chi chiama.
    #
    # E' l'unica identita' che NON si copia insieme all'immagine della scheda SD, quindi e' cio'
    # su cui si appoggiano la registrazione dell'hub e il riconoscimento del dispositivo. Per
    # questo qui non si tira mai a indovinare: meglio nessun MAC che uno inventato.
    @staticmethod
    def GetHardwareMacs(includeRemovable:bool=True) -> List[str]:
        macs:Set[str] = set()
        if os.path.exists('/sys/class/net/'):
            for interface in os.listdir('/sys/class/net/'):
                if not interface.startswith(('eth', 'wlan', 'en', 'wl')):
                    continue
                basePath = os.path.join('/sys/class/net/', interface)
                try:
                    with open(os.path.join(basePath, 'address'), 'r', encoding="utf-8") as f:
                        mac = f.read().strip().upper()
                except Exception:
                    continue
                if not LinuxHost.IsWellFormedMac(mac):
                    continue

                # Chiediamo al kernel se l'indirizzo viene dall'hardware o se e' stato generato.
                # E' la distinzione che serve davvero: "localmente amministrato" NON vuol dire
                # instabile. Il MAC 52:54:00:xx di una macchina virtuale QEMU e' localmente
                # amministrato ma sta scritto nella configurazione della VM e non cambia mai,
                # mentre un'interfaccia con indirizzo casuale cambia a ogni avvio.
                # Se l'attributo non c'e' (kernel molto vecchio) non escludiamo nulla: meglio
                # accettare un indirizzo in piu' che rifiutare l'unico che il dispositivo ha.
                assignPath = os.path.join(basePath, 'addr_assign_type')
                if os.path.exists(assignPath):
                    try:
                        with open(assignPath, 'r', encoding="utf-8") as f:
                            if f.read().strip() != LinuxHost.c_MacAssignPermanent:
                                continue
                    except Exception:
                        pass
                if includeRemovable is False and LinuxHost.IsRemovableInterface(basePath):
                    continue
                macs.add(mac)
        return sorted(macs)


    # Vero se l'interfaccia sta su un bus rimovibile, in pratica USB.
    #
    # Serve per il vincolo hardware, non per la registrazione. Un dongle USB spostato da un hub
    # all'altro porta con se' il proprio MAC, e per il vincolo sembrerebbe continuita' hardware:
    # e' esattamente il caso in cui, in laboratorio, si prepara un apparecchio e poi si usa lo
    # stesso dongle sul successivo. Legandosi solo alle schede integrate quella confusione non
    # nasce, e chi guarda da fuori puo' distinguere senza ambiguita' un hardware sostituito da
    # due apparecchi distinti.
    @staticmethod
    def IsRemovableInterface(basePath:str) -> bool:
        try:
            devicePath = os.path.join(basePath, 'device')
            if not os.path.exists(devicePath):
                # Nessun dispositivo dietro l'interfaccia: non e' hardware integrato.
                return True
            return "/usb" in os.path.realpath(devicePath).replace("\\", "/").lower()
        except Exception:
            # Nel dubbio la trattiamo come integrata: escludere per errore l'unica scheda di un
            # apparecchio lo lascerebbe senza vincolo.
            return False


    # Ripiego usato SOLO per la registrazione, quando la scansione non trova nessuna scheda
    # fisica: senza almeno un identificativo il backend rifiuta la chiamata e l'hub non
    # esisterebbe affatto.
    #
    # Non va usato per riconoscere il dispositivo: la documentazione di uuid.getnode() dice che
    # quando non riesce a leggere un indirizzo hardware ne inventa uno casuale con il bit
    # multicast acceso, e quel valore cambia a ogni avvio. Restituiamo None in quel caso, cosi'
    # chi cerca un'identita' stabile non riceve un numero casuale scambiandolo per hardware.
    @staticmethod
    def GetFallbackMac() -> Optional[str]:
        import uuid
        macNum = hex(uuid.getnode()).replace('0x', '').upper().zfill(12)
        mac = ':'.join(macNum[i:i + 2] for i in range(0, 12, 2))
        if not LinuxHost.IsWellFormedMac(mac):
            return None
        return mac


    def __init__(self, addonDataRootDir:str, logsDir:str, addonType:int, devConfig:Optional[Dict[str,Any]]) -> None:
        # When we create our class, make sure all of our core requirements are created.
        self.Secrets:Secrets = None #pyright: ignore[reportAttributeAccessIssue]
        self.WebServer:WebServer = None #pyright: ignore[reportAttributeAccessIssue]
        self.HaEventHandler:EventHandler = None #pyright: ignore[reportAttributeAccessIssue]
        self.WebRtcManager:WebRtcManager = None #pyright: ignore[reportAttributeAccessIssue]

        # Indicates if we are running as the Home Assistant addon, or standalone docker or cli.
        self.AddonType = addonType

        # I MAC con cui l'hub si e' effettivamente registrato, aggiornati a ogni giro del
        # reporter. Il pannello legge questi e non una copia presa all'avvio, altrimenti
        # mostrerebbe un valore diverso da quello con cui il dispositivo e' nel database.
        self.ReportedMacs:List[str] = []

        # Valorizzata in RunBlocking, prima che il pannello esista: e' la cartella che
        # l'azzeramento pre-clonazione svuota.
        self.StorageDir:str = ""

        # La connessione WebSocket verso Home Assistant, valorizzata in RunBlocking. Serve al
        # pannello per chiedere se chi ha bussato e' un amministratore.
        self.HaConnection:Optional[Connection] = None

        # Vero dal momento in cui si azzera l'identita' per preparare la clonazione. Da li' in
        # poi nessuna parte del programma deve piu' scrivere un'identita' sul disco, altrimenti
        # finirebbe dentro l'immagine che si sta per duplicare.
        self.WipedForCloning:bool = False

        try:
            # First, we need to load our config.
            # Note that the config MUST BE WRITTEN into this folder, that's where the setup installer is going to look for it.
            # If this fails, it will throw.
            self.Config = Config(addonDataRootDir)

            # Next, setup the logger.
            logLevelOverride_CanBeNone = self.GetDevConfigStr(devConfig, "LogLevel")
            self.Logger = LoggerInit.GetLogger(self.Config, logsDir, logLevelOverride_CanBeNone)
            self.Config.SetLogger(self.Logger)

            # Give Sentry the logger ASAP, since it's used for exceptions.
            Sentry.SetLogger(self.Logger)

        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Linux Host! "+str(e) + "; "+str(tb))
            # Raise the exception so we don't continue.
            raise


    def RunBlocking(self, storageDir:str, versionFileDir:str, devConfig:Optional[Dict[str,Any]]):
        # Do all of this in a try catch, so we can log any issues before exiting
        try:
            self.Logger.info("Sweetplace addon starting...")

            # Find the version of the plugin, this is required and it will throw if it fails.
            pluginVersionStr = Version.GetPluginVersion(versionFileDir)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            # Setup the HttpSession cache early, so it can be used whenever
            HttpSessions.Init(self.Logger)

            # Setup Sentry as soon as we know the plugin version.
            addonTypeStr = "HomeAssistantAddon"
            if self.AddonType is AddonTypes.StandaloneDocker:
                addonTypeStr = "StandaloneDocker"
            elif self.AddonType is AddonTypes.StandaloneCli:
                addonTypeStr = "StandaloneCli"
            self.Logger.info("Plugin Type: %s", addonTypeStr)
            Sentry.Setup(pluginVersionStr, addonTypeStr, devConfig is not None)

            self.Secrets = Secrets(self.Logger, storageDir)

            # La cartella dati serve al pannello per l'azzeramento pre-clonazione, che prima era
            # un interruttore nel tab di configurazione e ora sta nella pagina dell'add-on.
            self.StorageDir = storageDir

            # Prima di usare l'identita', verifica che appartenga a questo apparecchio: se
            # l'immagine e' stata clonata, la cancella qui e la riga sotto ne genera una nuova.
            self.EnforceHardwareBondIfNeeded()

            # Now, detect if this is a new instance and we need to init our global vars. If so, the setup script will be waiting on this.
            self.DoFirstTimeSetupIfNeeded()


            # Get our required vars
            pluginId = self.GetPluginId()
            privateKey = self.GetPrivateKey()
            if pluginId is None or privateKey is None:
                raise Exception("Plugin ID or Private Key is None! This should never happen, please report this issue to the OctoEverywhere team.")

            # Set the plugin id when we know it.
            Sentry.SetAddonId(pluginId)

            # Start the web server, which allows the user to interact with the plugin.
            # We start it as early as possible so the user can load the web page ASAP.
            # We always create the class, but only start the server for the in HA addon.
            # Il pannello ha bisogno di sapere a quale indirizzo mandare il cliente per il claim
            # e con quale MAC identificarsi: il MAC lo conosciamo gia', cosi' il cliente non deve
            # trascriverlo a mano, che e' il passaggio piu' fragile dell'onboarding.
            onboardApiUrl = Backend.DevicePingUrl()
            onboardBaseUrl = onboardApiUrl.rsplit('/device', 1)[0]
            self.WebServer = WebServer(self.Logger, pluginId, self.Config, devConfig, onboardBaseUrl, self.GetPanelMac,
                                       self.BuildImagePrepReport, self.WipeForCloning, self.IsHaUserAdmin,
                                       self.ChiediLinkPannello, self.ElencoMembri,
                                       self.RisolviProprietario, self.CorreggiProprietario)
            self.WebServer.Start(self.AddonType)

            # Set if remote access is enabled from the config.
            enableRemoteAccess = self.Config.GetBoolRequired(Config.HomeAssistantSection, Config.HaEnableRemoteAccess, True)
            HttpRequest.SetRemoteAccessEnabled(enableRemoteAccess)
            self.Logger.info("Remote Access Enabled: %s", str(enableRemoteAccess))

            # Unpack any dev vars that might exist
            devLocalSweetplaceServerAddress = self.GetDevConfigStr(devConfig, "LocalSweetplaceServerAddress")
            if devLocalSweetplaceServerAddress is not None:
                self.Logger.warning("~~~ Using Local Dev Server Address: %s ~~~", devLocalSweetplaceServerAddress)
            # This is mostly just used to not allow the dev plugin to fallback to port 80
            if self.GetDevConfigStr(devConfig, "HomeAssistantProxyPort") is not None:
                portStr = self.GetDevConfigStr(devConfig, "HomeAssistantProxyPort")
                if portStr is not None:
                    HttpRequest.SetLocalHttpProxyPort(int(portStr))

            # Init Sentry, but it won't report since we are in dev mode.
            Telemetry.Init(self.Logger)
            if devLocalSweetplaceServerAddress is not None:
                Telemetry.SetServerProtocolAndDomain("http://"+devLocalSweetplaceServerAddress)

            # Init compression
            Compression.Init(self.Logger, storageDir)

            # Init the mdns client
            MDns.Init(self.Logger, storageDir)

            # Setup the command handler
            # This must be setup before the config manager.
            CommandHandler.Init(self.Logger)

            # Setup the Home Assistant config manager
            configManager = ConfigManager(self.Logger)
            self.WebServer.RegisterForAccountStatusUpdates()

            # Use the discovery class to find the correct port for Home Assistant.
            # For addons running in the Home Assistant docker ecosystem, this will return the optimal docker direct resolve hostnames and configs.
            # For standalone plugin installs, the installer will get the port set correctly with the user's help.
            serverDiscovery = ServerDiscovery(self.Logger, configManager)
            result = serverDiscovery.GetHomeAssistantServerInfo(self.Config)

            # Set the final ips, port, and access token.
            self.Logger.info("Setting up Home Assistant connection to [%s:%s] https:%s", result.HostnameOrIp, str(result.Port), str(result.IsHttps))
            HttpRequest.SetDirectServicePort(result.Port)
            HttpRequest.SetDirectServiceAddress(result.HostnameOrIp)
            HttpRequest.SetDirectServiceUseHttps(result.IsHttps)
            ServerInfo.SetServerInfo(result.HostnameOrIp, result.Port, result.IsHttps, result.AccessToken)
            # If this isn't running in the special Home Assistant addon mode, set the local IP override.
            if result.IsSpecialHomeAssistantAddonMode is False:
                LocalIpHelper.SetConnectionTargetIpOverride(result.HostnameOrIp)

            # QUI SI AVVIAVA LA MISURA DI LATENZA, E NON SI AVVIA PIU'.
            #
            # PingPong.Init() faceva partire un thread che, quindici minuti dopo il primo avvio e
            # poi ogni cinquanta ore, contattava i server del progetto di origine per misurare
            # quale fosse il piu' vicino (pingpong.py:_DoPing). Non dipendeva da nessun
            # handshake: partiva da solo, su ogni hub, e usciva in rete verso un dominio che non
            # e' nostro. E' stata l'ultima cosa che lo faceva ancora.
            #
            # Quella misura serviva a una decisione sola: a quale server puntare la connessione
            # permanente. Senza quella connessione non c'e' piu' niente da decidere, e nessuno
            # legge quei numeri — l'unico lettore era il codice del server connection, ormai
            # irraggiungibile.
            #
            # Insieme a Init() se ne va anche DisablePrimaryOverride(), che era un interruttore
            # da sviluppo per la stessa scelta: senza Init(), PingPong.Get() e' None e chiamarlo
            # sarebbe stato un AttributeError all'avvio.

            # Setup the HA state change handler
            self.HaEventHandler = EventHandler(self.Logger, pluginId, devLocalSweetplaceServerAddress)

            # Setup the HA connection object
            haConnection = Connection(self.Logger, self.HaEventHandler)
            self.HaConnection = haConnection
            haConnection.Start()
            CommandHandler.Get().RegisterHomeAssistantWebsocketCon(haConnection)
            self.HaEventHandler.RegisterHomeAssistantWebsocketCon(haConnection)

            # Setup the Tracker Interceptor
            self.TrackerInterceptorInstance = TrackerInterceptor(self.Logger, haConnection)
            self.HaEventHandler.TrackerInterceptorCallback = self.TrackerInterceptorInstance.HandleEntityRegistryUpdate

            # Set the ha connection object and try to update the config if needed.
            configManager.SetHaConnection(haConnection)
            configManager.UpdateConfigIfNeeded()

            # SONDA TEMPORANEA, passo 0(a) del lavoro sugli assistenti vocali. Da togliere
            # quando la risposta e' annotata in sweetlink/ASSISTENTI-VOCALI.md.
            # Sta QUI, dopo UpdateConfigIfNeeded, perche' e' quella chiamata a scrivere le
            # sezioni alexa: e google_assistant: nella configurazione: prima di lei le rotte
            # che la sonda interroga potrebbero non esistere ancora, su un hub appena nato.
            from .sondavocale import Avvia as AvviaSondaVocale
            AvviaSondaVocale(self.Logger)

            # I DUE FILE DEGLI ASSISTENTI VOCALI, RIGENERATI SE SERVE.
            #
            # Sta qui, dopo UpdateConfigIfNeeded, perche' li' si stabilisce la connessione a Home
            # Assistant da cui si legge la versione — che e' uno dei tre valori del controllo
            # incrociato.
            #
            # Scrivere questi due file NON ha effetto finche' il configuration.yaml non li
            # aggancia con un !include. Su un hub che non e' stato ancora preparato, questa
            # chiamata produce due file che non legge nessuno: e' voluto, cosi' si puo' guardare
            # cosa verrebbe generato prima di collegarli davvero.
            try:
                from .ha.assistentivocali import AggiornaSeServe
                # La versione di Home Assistant la tiene la connessione, che l'ha appena letta
                # nel messaggio di benvenuto (ha/connection.py:209). Puo' essere ancora vuota se
                # l'autenticazione non e' finita: in quel caso l'intestazione dice "?" e al giro
                # dopo i file vengono rifatti, che e' esattamente quello che deve succedere.
                haVersione = getattr(haConnection, "HaVersionString", None)
                cambiato, righe = AggiornaSeServe(haVersione, pluginVersionStr)
                for riga in righe:
                    self.Logger.info(f"[AssistentiVocali] {riga}")
                if cambiato:
                    self.Logger.info("[AssistentiVocali] I file sono cambiati. Avranno effetto al prossimo riavvio di Home Assistant.")
            except Exception as e:
                self.Logger.warning(f"[AssistentiVocali] Non aggiornati: {e}")

            # Setup the WebRTC manager
            self.WebRtcManager = WebRtcManager(self.Logger, pluginId, storageDir, self.Config, configManager)

            # Setup and start the home context
            homeContext = HomeContext(self.Logger, haConnection, self.HaEventHandler)
            homeContext.Start()
            CommandHandler.Get().RegisterHomeContext(homeContext)

            # Now start the main runner!

            # --- SWEETPLACE CLOUD WORKER ---
            privateKey = self.GetPrivateKey()
            CloudWorkerInstance.Start(self.Logger, pluginId, privateKey, haConnection, storageDir)

            # --- SWEETPLACE ONBOARDING REPORTER ---
            # Registra l'hub nel database Sweetplace, su un thread suo perche' e' una chiamata
            # di rete che non deve ritardare l'avvio.
            #
            # Sta qui e non dentro OnPrimaryConnectionEstablished perche' non dipende da
            # Sweetplace: dei quattro valori che spedisce (macs, plugin_id, private_key, app_url)
            # nessuno viene dal remoto, e la funzione non usava ne' apiKey ne' connectedAccounts,
            # cioe' i suoi due soli argomenti di provenienza remota. Legato all'handshake, un hub
            # non si registrava affatto quando homeway.io non rispondeva.
            #
            # Il CloudflareManager qui sotto ha bisogno che questa registrazione sia gia' arrivata:
            # il backend consegna il gettone del tunnel solo a chi presenta plugin_id e chiave
            # privata combacianti con la riga dell'apparecchio, e quella riga la scrive /device/ping.
            # Fra i due NON c'e' ordinamento garantito: sono due thread e le richieste possono
            # essere in volo insieme. Se il provisioning arriva prima prende 403 e ritenta dopo 60s
            # (il ciclo in cloudflaremanager.py): il costo e' un ritardo all'avvio, non una rottura.
            def _ReportToSweetplaceDB():
                try:
                    import requests, time

                    # Submit an empty URL to let the cloud backend preserve any pre-configured custom tunnel domain (Zero-Touch AppURL)
                    app_url = ""

                    # Check for explicit API or fallback to presumed production URL
                    api_url = Backend.DevicePingUrl()

                    # Ritenta finche' la registrazione non va a buon fine, poi la ripete a bassa
                    # frequenza. Servono entrambe le cose, per due motivi diversi.
                    #
                    # RITENTATIVO: all'avvio la rete o il backend possono non essere ancora
                    # pronti. Prima quella garanzia la dava implicitamente l'handshake con
                    # Sweetplace, che qui non c'e' piu'.
                    #
                    # RIPETIZIONE: prima questo codice girava a ogni handshake primario, e la
                    # connessione primaria viene riciclata ogni 47h (sweetlinkcore.py:20), quindi
                    # l'upsert rigirava almeno una volta al giorno e mezzo. E' quella ripetizione
                    # a rimediare ai due casi in cui uno sparo solo non basta: un MAC che compare
                    # dopo l'avvio (dongle USB, Wi-Fi alzato dopo) non avrebbe mai la sua riga,
                    # e una riga persa lato server non verrebbe mai ricreata.
                    #
                    # La scansione dei MAC sta DENTRO il ciclo, non fuori: se stesse fuori, un
                    # avvio senza schede rilevate non riproverebbe mai, e il rimedio descritto
                    # sopra resterebbe una promessa del commento invece di un comportamento.
                    RIPETIZIONE_SEC = 6 * 60 * 60
                    RITENTATIVO_SEC = 60
                    # Tetto ai rifacimenti dell'identita': se dopo qualche tentativo il backend
                    # continua a vedere una collisione il problema non e' l'identita', ed e'
                    # meglio fermarsi che generare identita' nuove all'infinito.
                    MAX_RIGENERAZIONI = 3
                    rigenerazioni = 0
                    # Il plugin_id che avevamo PRIMA di rigenerarlo.
                    #
                    # E' la prova che il backend chiede per riassegnarci la riga che gia' ci
                    # apparteneva: senza, non adotta e ci registra come apparecchio nuovo,
                    # lasciando orfana la rivendicazione del cliente. Serve perche' il ferro da
                    # solo non prova niente — il MAC lo conosce chiunque abbia visto la scatola
                    # o la rete locale, e accettarlo come prova voleva dire lasciare che
                    # chiunque si facesse assegnare un hub altrui.
                    #
                    # Resta in memoria e non sul disco: sul disco verrebbe clonato insieme
                    # all'immagine, e ogni copia si presenterebbe con la prova di un apparecchio
                    # che non e' lei.
                    chiavePrecedente = None
                    identitaPrecedente = None
                    while True:
                        # Un apparecchio azzerato per la clonazione non deve registrarsi: non ha
                        # piu' un'identita' propria, e il percorso di collisione qui sotto ne
                        # rigenererebbe una scrivendola sul disco che sta per essere duplicato.
                        if self.WipedForCloning:
                            self.Logger.info("Sweetplace Onboarding: identita' azzerata per la clonazione, il reporter si ferma.")
                            return
                        attesaSec = RITENTATIVO_SEC
                        try:
                            # Solo le schede integrate, cioe' le due del Raspberry Pi: Ethernet e
                            # Wi-Fi. E' lo STESSO insieme su cui si lega il sigillo hardware
                            # (SetBoundMacs a riga 580 e il controllo a riga 695), e devono
                            # coincidere per forza.
                            #
                            # Prima qui si mandavano tutte le schede, rimovibili comprese, mentre
                            # il sigillo ne guardava un sottoinsieme. Da quell'asimmetria nasceva
                            # un guasto vero: un indirizzo presente nella registrazione ma
                            # invisibile al sigillo, o comparso su un altro apparecchio, faceva
                            # divergere i due insiemi senza che nessuno dei due fosse sbagliato.
                            #
                            # Le due schede del Raspberry sono saldate: non si spostano, non si
                            # tolgono, e sono le sole che questo prodotto avra' mai. Censirle
                            # entrambe e sempre le stesse due e' cio' che permette all'insieme
                            # registrato di restare stabile per tutta la vita dell'apparecchio.
                            macs = LinuxHost.GetHardwareMacs(includeRemovable=False)
                            if len(macs) == 0:
                                # Nessuna scheda con indirizzo permanente. Puo' essere transitorio
                                # (Wi-Fi non ancora associato, dongle non ancora enumerato), quindi
                                # si riprova al giro dopo invece di rinunciare.
                                fallback = LinuxHost.GetFallbackMac()
                                if fallback is None:
                                    self.Logger.warning(f"Sweetplace Onboarding: nessun indirizzo hardware determinabile, riprovo fra {RITENTATIVO_SEC}s...")
                                    time.sleep(RITENTATIVO_SEC)
                                    continue
                                self.Logger.warning("Sweetplace Onboarding: nessuna scheda con indirizzo permanente, uso l'indirizzo di ripiego.")
                                macs = [fallback]

                            self.ReportedMacs = macs
                            # Rilette a ogni giro: se il backend segnala una collisione le
                            # rigeneriamo, e il giro successivo deve usare quelle nuove.
                            currentPluginId = self.GetPluginId()
                            currentPrivateKey = self.GetPrivateKey()
                            payload = {"macs": macs, "plugin_id": currentPluginId, "app_url": app_url, "private_key": currentPrivateKey}
                            if identitaPrecedente is not None:
                                payload["previous_plugin_id"] = identitaPrecedente
                                # LA PROVA E' LA CHIAVE, NON L'IDENTIFICATIVO.
                                # Il plugin_id precedente non e' un segreto: compare nei registri,
                                # nel pannello e nelle email di assistenza. La chiave privata si',
                                # e ce l'abbiamo ancora in mano nel momento in cui rigeneriamo.
                                payload["previous_private_key"] = chiavePrecedente
                            self.Logger.info(f"Sweetplace Onboarding: Reporting MAC Array {macs} and AppURL [{app_url}] to {api_url}")

                            response = requests.post(api_url, json=payload, timeout=10)
                            registrato = False
                            if response.ok:
                                # Non basta il 2xx: il backend serve la SPA con HTTP 200 su
                                # qualunque path non instradato (onboarding/src/index.ts:842-844),
                                # quindi un SWEETPLACE_ONBOARD_API sbagliato darebbe 200 senza
                                # aver registrato niente. La conferma sta nel corpo.
                                try:
                                    corpo = response.json()
                                    registrato = corpo.get("success") is True
                                    # Il codice di rivendicazione torna indietro qui, ed e'
                                    # l'unico posto da cui lo si puo' leggere: non sta sul
                                    # disco dell'hub, perche' altrimenti verrebbe clonato
                                    # insieme all'immagine e tutti gli apparecchi nati da
                                    # quella copia si rivendicherebbero a vicenda.
                                    if registrato and WebServer.Instance is not None:
                                        righe = corpo.get("records") or []
                                        if len(righe) > 0 and isinstance(righe[0], dict):
                                            WebServer.Instance.SetClaimInfo(
                                                righe[0].get("claim_code"),
                                                righe[0].get("claim_status"),
                                                righe[0].get("claim_url"),
                                                righe[0].get("owner_auth_id"),
                                                righe[0].get("owner_username"))
                                except Exception:
                                    registrato = False

                            if registrato:
                                self.Logger.info(f"Sweetplace Onboarding: hub registrato. Prossimo aggiornamento fra {RIPETIZIONE_SEC // 3600}h.")
                                # Da qui in poi la riga dell'apparecchio esiste sul backend, e il
                                # worker cloud puo' autenticarsi. Senza questo segnale partiva in
                                # parallelo e la sua socket arrivava prima della registrazione:
                                # credenziali non trovate, errore a ogni avvio, e recupero solo
                                # al ritentativo dieci secondi dopo.
                                CloudWorkerInstance.RegistrazioneConfermata.set()
                                attesaSec = RIPETIZIONE_SEC

                                # CHI E' IL PROPRIETARIO SI RISOLVE QUI, NON QUANDO QUALCUNO APRE
                                # IL PANNELLO.
                                #
                                # Il caso che lo impone e' proprio quello in cui serve: il
                                # proprietario chiuso fuori da Home Assistant. In Home Assistant
                                # non ci entra — e' il motivo per cui sta cercando di rientrare —
                                # quindi il pannello non lo apre nessuno e la correzione non
                                # partirebbe mai. Il portale, intanto, mostra il comando che
                                # rigenera la sua password solo se il backend ha un
                                # identificativo: senza questo giro, l'unica strada di rientro
                                # resta nascosta esattamente nel caso per cui e' stata fatta.
                                #
                                # DOPO il segnale al worker e in un try suo: il giro puo' aspettare
                                # fino a dieci secondi che Home Assistant finisca di autenticarsi,
                                # e dentro il blocco sopra quell'attesa avrebbe ritardato l'avvio
                                # della socket mentre un'eccezione avrebbe fatto credere all'add-on
                                # di non essere registrato.
                                try:
                                    if WebServer.Instance is not None:
                                        WebServer.Instance.ProprietarioAuthId()
                                except Exception as e:
                                    self.Logger.warning(f"Proprietario non risolto a questo giro: {e}")
                            elif response.ok:
                                self.Logger.error(f"Sweetplace Onboarding: {api_url} ha risposto HTTP {response.status_code} ma senza conferma di registrazione. Controllare che SWEETPLACE_ONBOARD_API punti a /device/ping.")
                            elif response.status_code == 409 and self.WipedForCloning:
                                # Il controllo in cima al ciclo non basta: questa iterazione era
                                # gia' in volo quando l'azzeramento e' avvenuto, e il percorso
                                # qui sotto scriverebbe sul disco un'identita' nuova proprio
                                # mentre lo si sta preparando per la clonazione.
                                self.Logger.info("Sweetplace Onboarding: collisione ignorata, l'apparecchio e' stato azzerato per la clonazione.")
                                return
                            elif response.status_code == 409 and rigenerazioni < MAX_RIGENERAZIONI:
                                # Il backend ha riconosciuto che questa identita' appartiene gia'
                                # a un altro apparecchio: l'immagine e' stata clonata e il sigillo
                                # hardware non se n'e' accorto. Il server e' l'unico che puo'
                                # saperlo con certezza, perche' e' l'unico che li vede tutti.
                                rigenerazioni += 1
                                self.Logger.error("Sweetplace Onboarding: il backend segnala che questa identita' appartiene gia' a un altro apparecchio. La rigenero e riprovo.")
                                # Prima di cancellarle: sono le due cose che potranno dimostrare al
                                # backend che la riga esistente e' nostra.
                                #
                                # Il plugin_id da solo NON basta e non bastava: e' un
                                # identificativo, non un segreto — sta nei registri, nel pannello
                                # e nelle email di assistenza. Chi lo conosceva, insieme a un MAC
                                # dell'apparecchio, poteva far riassegnare la riga a se' stesso e
                                # da li' chiedere il gettone del tunnel.
                                identitaPrecedente = currentPluginId
                                chiavePrecedente = currentPrivateKey
                                self.Secrets.SetPluginId(None)
                                self.Secrets.SetPrivateKey(None)
                                self.DoFirstTimeSetupIfNeeded()
                                # Il vincolo si lega alle sole schede integrate, come fa il
                                # controllo all'avvio: legare qui un indirizzo che quel controllo
                                # non vedra' mai gli farebbe rigenerare tutto un'altra volta.
                                self.Secrets.SetBoundMacs(LinuxHost.GetHardwareMacs(includeRemovable=False))
                                self.Logger.error("Sweetplace Onboarding: identita' rigenerata. Riavvio l'add-on per applicarla ovunque.")
                                if LinuxHost.RequestSelfRestart(self.Logger):
                                    # Il Supervisor sta per fermarci: non serve altro.
                                    return
                                # Se il riavvio non e' possibile continuiamo comunque a
                                # registrarci: l'hub resta a meta' strada fino al prossimo avvio,
                                # ma almeno risulta censito e il backend puo' segnalarlo.
                                self.Logger.error("Sweetplace Onboarding: riavvio non riuscito. L'identita' nuova sara' pienamente attiva solo dopo un riavvio manuale.")
                                continue
                            elif response.status_code == 409:
                                self.Logger.error(f"Sweetplace Onboarding: identita' ancora in collisione dopo {MAX_RIGENERAZIONI} tentativi di rigenerazione. Mi fermo per non ciclare.")
                                return
                            elif response.status_code == 400:
                                # L'unico 4xx che il backend produce per un payload sbagliato
                                # (index.ts:136-138). Ritentarlo identico non puo' funzionare.
                                # Gli altri 4xx arrivano dalla piattaforma prima di Express
                                # (404 senza deployment attivo, 408, 429) e vanno ritentati.
                                self.Logger.error("Sweetplace Onboarding: payload rifiutato dal backend (HTTP 400), non ritento.")
                                return
                            else:
                                self.Logger.warning(f"Sweetplace Onboarding: backend HTTP {response.status_code}, riprovo fra {RITENTATIVO_SEC}s...")
                        except Exception as e:
                            self.Logger.warning(f"Sweetplace Onboarding: invio fallito ({e}), riprovo fra {RITENTATIVO_SEC}s...")
                        time.sleep(attesaSec)
                except Exception as e:
                    self.Logger.error(f"Sweetplace Onboarding Reporter failed: {e}")

            threading.Thread(target=_ReportToSweetplaceDB, daemon=True).start()
            # --------------------------------------

            # --- SWEETPLACE CLOUDFLARE MANAGER ---
            # Start the manager thread that requests the JWT Token and spawns cloudflared
            # We pass the plugin_id so the backend can resolve the correct MAC/tunnel.
            # Using uuid.getnode() was unreliable on multi-NIC devices (picked wrong MAC).
            apiURLString = Backend.DevicePingUrl()
            baseApiUrl = apiURLString.rsplit('/device', 1)[0]

            self.CloudflareInstance = CloudflareManager(self.Logger)
            self.CloudflareInstance.Start(baseApiUrl, plugin_id=pluginId, private_key=self.GetPrivateKey())
            # Il pannello mostra l'indirizzo pubblico dell'hub, che solo il manager conosce.
            self.WebServer.SetCloudflareManager(self.CloudflareInstance)


            # QUI FINIVA L'AVVIO, E QUI C'ERA LA CONNESSIONE A UN SERVIZIO DI TERZI.
            #
            # Il progetto di origine teneva aperta una websocket permanente verso i propri
            # server, sulla quale viaggiavano messaggi WebStreamMsg: era un reverse proxy, cioe'
            # il modo in cui le richieste HTTP arrivavano dall'edge di quel servizio fino a Home
            # Assistant. Esattamente cio' che fa il tunnel Cloudflare avviato qui sopra.
            #
            # Tenerle entrambe voleva dire due strade verso lo stesso Home Assistant, di cui una
            # attraverso l'infrastruttura di qualcun altro, e un log che diceva "connesso a
            # Sweetplace" mentre parlava con un dominio che non e' nostro.
            #
            # Il ciclo qui sotto prende il posto di quella chiamata perche' era LEI a tenere in
            # vita il processo: era bloccante e non tornava mai. Tutto il lavoro vero gira su
            # thread propri — il tunnel, il reporter, il worker cloud, il web server — e senza
            # qualcosa che trattenga il thread principale l'add-on uscirebbe subito dopo l'avvio.
            self.Logger.info("Avvio completato: l'hub e' raggiungibile solo attraverso il proprio tunnel.")
            while True:
                time.sleep(60 * 60)
        except Exception as e:
            Sentry.OnException("!! Exception thrown out of main host run function.", e)

        # Allow the loggers to flush before we exit
        try:
            self.Logger.info("##################################")
            self.Logger.info("#### Sweetplace Exiting ######")
            self.Logger.info("##################################")
            logging.shutdown()
        except Exception as e:
            print("Exception in logging.shutdown "+str(e))


    # Ensures all required values are setup and valid before starting.
    # Verifica che l'identita' salvata appartenga a QUESTO apparecchio, e la rigenera se no.
    #
    # plugin_id e private_key nascono al primo avvio e finiscono dentro l'immagine della scheda
    # SD. Se quell'immagine viene clonata su altri hub senza azzerarli, tutti si presentano al
    # backend con la stessa identita' e con la stessa chiave privata.
    #
    # Il danno non e' che manchi qualche registrazione: /device/ping fa upsert per MAC, quindi
    # ogni clone si registra regolarmente. Il danno e' che il backend usa plugin_id per decidere
    # la proprieta': /device/verify propaga claim_status, email e session_token a TUTTE le righe
    # con lo stesso plugin_id, e /api/cloudflare/provision propaga tunnel e URL pubblico allo
    # stesso modo. Con due cloni, il primo cliente che rivendica il proprio hub si prende anche
    # l'altro e ne condivide il token di sessione.
    #
    # La procedura di azzeramento manuale esiste gia', ma dipende da chi prepara le schede, e
    # basta un avvio di troppo prima della clonazione per vanificarla.
    #
    # Qui il rimedio non chiede disciplina a nessuno: l'identita' viene legata agli indirizzi
    # hardware visti sull'apparecchio, che sono l'unica cosa che NON si copia con l'immagine.
    # Se al riavvio nessuno di quegli indirizzi e' piu' presente, l'immagine sta girando su un
    # altro apparecchio e l'identita' va rifatta.
    #
    # Due regole di prudenza, perche' un falso positivo qui costa la registrazione dell'hub:
    #  - se non si legge nessun indirizzo permanente non si conclude nulla. L'assenza di prove
    #    non e' una prova.
    #  - quando l'apparecchio e' riconosciuto, il vincolo viene ALLARGATO agli indirizzi visti
    #    adesso invece di essere sostituito. Cosi' sostituzioni graduali dell'hardware (oggi si
    #    aggiunge un dongle, domani si guasta la scheda di bordo) non arrivano mai al punto in
    #    cui non resta piu' niente in comune.
    def EnforceHardwareBondIfNeeded(self) -> None:
        try:
            self._EnforceHardwareBondIfNeeded()
        except Exception as e:
            # Deve fallire in apertura: questo e' un controllo di sicurezza, non un requisito
            # di avvio. Se /data e' pieno o in sola lettura la scrittura del vincolo lancia, e
            # senza questa rete l'add-on non partirebbe per un guasto che prima era invisibile.
            self.Logger.error(f"Hardware bond: controllo non riuscito, proseguo comunque. {e}")


    def _EnforceHardwareBondIfNeeded(self) -> None:
        currentMacs = LinuxHost.GetHardwareMacs(includeRemovable=False)
        if len(currentMacs) == 0:
            self.Logger.warning("Hardware bond: nessun indirizzo di rete permanente leggibile, controllo saltato.")
            return

        boundMacs = self.Secrets.GetBoundMacs()

        # Nessun vincolo salvato: primo avvio del dispositivo, oppure identita' creata da una
        # versione dell'add-on precedente a questo meccanismo. In entrambi i casi si lega e basta.
        if len(boundMacs) == 0:
            if self.GetPluginId() is not None:
                self.Logger.info("Hardware bond: identita' esistente senza vincolo, la lego a questo apparecchio.")
            self.Secrets.SetBoundMacs(currentMacs)
            return

        if len(set(boundMacs) & set(currentMacs)) > 0:
            allargato = sorted(set(boundMacs) | set(currentMacs))
            if allargato != boundMacs:
                self.Logger.info("Hardware bond: nuovo indirizzo di rete su questo apparecchio, vincolo aggiornato.")
                self.Secrets.SetBoundMacs(allargato)
            return

        # Nessuna corrispondenza: questa identita' e' nata su un altro apparecchio.
        self.Logger.error("Hardware bond: l'identita' salvata appartiene a un altro apparecchio. Attesi %s, presenti %s. La rigenero.",
                            ",".join(boundMacs), ",".join(currentMacs))
        self.Secrets.SetPluginId(None)
        self.Secrets.SetPrivateKey(None)
        # Il vincolo viene SOSTITUITO, non allargato: gli indirizzi dell'apparecchio di origine
        # non hanno piu' niente a che fare con questo.
        self.Secrets.SetBoundMacs(currentMacs)


    def DoFirstTimeSetupIfNeeded(self):
        # Dopo un azzeramento per la clonazione l'identita' NON va rigenerata: e' l'unico punto
        # del programma che ne crea una, e questa e' la finestra in cui verrebbe scritta dentro
        # l'immagine che sta per essere duplicata. L'add-on si sta fermando comunque; se la
        # richiesta di arresto fallisce, questo guard e' cio' che tiene il disco pulito.
        if self.WipedForCloning:
            self.Logger.warning("Generazione dell'identita' rifiutata: l'apparecchio e' stato azzerato per la clonazione.")
            return

        # Try to get the plugin id from the config.
        pluginId = self.GetPluginId()
        if HostCommon.IsPluginIdValid(pluginId) is False:
            if pluginId is None:
                self.Logger.info("No plugin id was found, generating one now!")
            else:
                self.Logger.info("An invalid pluginId id was found [%s], regenerating!", str(pluginId))

            # Make a new, valid, key
            pluginId = HostCommon.GeneratePluginId()

            # Save it
            self.Secrets.SetPluginId(pluginId)
            self.Logger.info("New plugin id created: %s", pluginId)

        privateKey = self.GetPrivateKey()
        if HostCommon.IsPrivateKeyValid(privateKey) is False:
            if privateKey is None:
                self.Logger.info("No private key was found, generating one now!")
            else:
                self.Logger.info("An invalid private key was found [%s], regenerating!", str(privateKey))

            # Make a new, valid, key
            privateKey = HostCommon.GeneratePrivateKey()

            # Save it
            self.Secrets.SetPrivateKey(privateKey)
            self.Logger.info("New private key created.")


    # Returns None if no plugin id has been set.
    def GetPluginId(self) -> Optional[str]:
        return self.Secrets.GetPluginId()


    # Returns None if no private id has been set.
    def GetPrivateKey(self) -> Optional[str]:
        return self.Secrets.GetPrivateKey()


    # Tries to load a dev config option as a string.
    # If not found or it fails, this return None
    def GetDevConfigStr(self, devConfig:Optional[Dict[str, str]], value:str) -> Optional[str]:
        if devConfig is None:
            return None
        if value in devConfig:
            v = devConfig[value]
            if v is not None and len(v) > 0 and v != "None":
                return v
        return None


    # StatusChangeHandler Interface - Called by the Sweetplace logic when the server connection has been established.
    #
    def OnPrimaryConnectionEstablished(self, apiKey:str, connectedAccounts:List[str]) -> None:
        self.Logger.info("Primary Connection To Sweetplace Established - We Are Ready To Go!")

        # Ensure we have a valid plugin id
        pluginId = self.GetPluginId()
        if pluginId is None:
            raise Exception("Plugin ID is None in OnPrimaryConnectionEstablished, this should never happen!")

        # Set the current API key to the event handler
        self.HaEventHandler.SetSweetplaceApiKey(apiKey)

        # Let the WebRTC manager know the connection is established.
        self.WebRtcManager.OnPrimaryConnectionEstablished(apiKey)

        # Tell the web server if there's a connect user or not.
        hasConnectedAccount = connectedAccounts is not None and len(connectedAccounts) > 0
        self.WebServer.OnPrimaryConnectionEstablished(hasConnectedAccount)

        # Check if this plugin is unlinked, if so add a message to the log to help the user setup the plugin if desired.
        # This would be if the skipped the plugin link or missed it in the setup script.
        if hasConnectedAccount is False:
            self.Logger.info("Addon Hardware ID: %s", pluginId)


    #
    # StatusChangeHandler Interface - Called by the Sweetplace logic when a plugin update is required for this client.
    #
    def OnPluginUpdateRequired(self):
        self.Logger.error("!!! A Plugin Update Is Required -- If This Plugin Isn't Updated It Might Stop Working !!!")
