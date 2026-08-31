# SPDX-License-Identifier: AGPL-3.0-or-later
# Derivato dall'add-on Home Assistant di Homeway.io (https://github.com/homewayio/AddOn),
# a sua volta derivato da OctoEverywhere. Vedi NOTICE.md.
# Modificato da Sweetplace (M2R S.r.l.), 2026.

import io
import os
import json
import time
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler
from html import escape
from typing import Any, Callable, Dict, List, Optional, cast
from urllib.parse import quote

from sweetlink.commandhandler import CommandHandler
from sweetlink.interfaces import IAccountLinkStatusUpdateHandler
from sweetlink.sentry import Sentry
from sweetlink.Proto.AddonTypes import AddonTypes

from .config import Config
from .haadmin import HaAdmin
from .imageprep import ImagePrep


# Creates a simple web server for users to interact with the plugin from the Home Assistant UI.
class WebServer(IAccountLinkStatusUpdateHandler):

    # A static instance var for the handler class to access this class.
    Instance:"WebServer" = None # type: ignore[reportClassAttributeMissing]

    # Tetto al corpo di una richiesta. Il pannello manda un oggetto JSON con un campo: qualunque
    # cosa piu' grande di questo non e' il pannello, e il server e' a thread singolo.
    # I TRE COLORI DELLA GRAVITA', CON IL TESTO CHE CI STA SOPRA.
    #
    # Servono etichette con il fondo colorato e non parole colorate, e non e' una scelta di
    # gusto: questo pannello segue il tema di Home Assistant, quindi lo stesso colore deve
    # reggere sia su scheda chiara sia su scheda scura. Nessuno ci riesce. Misurato, il giallo
    # sta a 1.68:1 su bianco (illeggibile) e a 10.14:1 su scuro; il verde fa 5.13 su bianco ma
    # 3.32 su scuro. Con il fondo colorato il contrasto va verificato solo DENTRO l'etichetta,
    # dove non dipende dal tema: verde con bianco 5.13, giallo con nero 11.23, arancione con
    # nero 6.11. Tutti sopra il 4.5 richiesto.
    #
    # L'arancione, e non il rosso, per il livello bloccante: e' la richiesta di chi usa questo
    # pannello, ed e' comunque distinguibile dal giallo (2.5:1 fra i due fondi).
    c_ColoriEsito = {
        "ok":    ("#2E7D32", "#FFFFFF", "OK"),
        "warn":  ("#F2C200", "#111111", "ATTENZIONE"),
        "block": ("#E8710A", "#111111", "ERRORE"),
    }

    c_MaxRequestBodyBytes = 64 * 1024

    def __init__(self, logger:logging.Logger, pluginId:str, config:Config, devConfig:Optional[Dict[str,Any]], onboardBaseUrl:str, macProvider:Callable[[], str],
                 reportProvider:Callable[[], List[Dict[str, str]]], wipeAction:Callable[[], List[Dict[str, str]]],
                 adminCheck:Callable[[str], Optional[bool]],
                 panelLinkProvider:Callable[[], Optional[str]],
                 membersProvider:Callable[[], Optional[List[Dict[str, str]]]],
                 ownerResolver:Callable[[Optional[str]], Optional[str]],
                 ownerFixer:Callable[[str], None]) -> None:
        WebServer.Instance = self
        self.Logger = logger
        self.PluginId = pluginId
        # Chiede al backend un indirizzo con cui aprire il portale gia' dentro. Sta fuori di qui
        # perche' richiede la chiave privata, che questo server non ha e non deve avere.
        self.PanelLinkProvider = panelLinkProvider
        # Le persone di casa. Il server web non sa comporle: le chiede a chi ha in mano la
        # connessione a Home Assistant e lo schedario dei nomi di accesso.
        self.MembersProvider = membersProvider
        # Vuoto finche' il backend non lo comunica: prima della rivendicazione non esiste nessun
        # proprietario, e trattare "non lo so ancora" come "sei tu" aprirebbe il pannello al
        # primo che passa.
        self.OwnerAuthId:Optional[str] = None
        # Il nome di accesso del proprietario, che e' il dato con cui l'identificativo qui sopra
        # si puo' controllare: in Home Assistant e' unico, e l'anagrafica sta su questa macchina.
        self.OwnerUsername:Optional[str] = None
        # Quello ricavato dall'anagrafica, tenuto da parte: il giro costa una domanda a Home
        # Assistant e il risultato non cambia. Resta None finche' non si e' potuto ricavare, cosi'
        # al caricamento successivo si riprova — all'avvio Home Assistant puo' non essere ancora
        # raggiungibile, ed e' una condizione che passa da sola.
        self.OwnerAuthIdRisolto:Optional[str] = None
        # Da nome di accesso a identificativo dell'utente, chiedendolo a Home Assistant.
        self.OwnerResolver = ownerResolver
        # Dice al backend l'identificativo giusto, quando quello che ci aveva mandato non lo era.
        self.OwnerFixer = ownerFixer
        self.Config = config
        # Il referto sulla preparazione dell'immagine e l'azzeramento vero e proprio. Stanno qui
        # e non nel tab di configurazione perche' chi prepara l'immagine deve vedere cosa c'e'
        # ancora sul disco nello stesso posto in cui preme il pulsante: un interruttore che
        # azzera l'identita' senza mostrare lo stato e' un incidente in attesa di accadere.
        self.ReportProvider = reportProvider
        self.WipeAction = wipeAction
        # Chiede a Home Assistant se l'utente che ha originato la richiesta e' amministratore.
        self.AdminCheck = adminCheck
        # Il claim si chiude sul portale Sweetplace: il pannello ci manda il cliente con il MAC
        # gia' compilato, cosi' l'unica cosa che deve inserire e' la propria email.
        self.OnboardBaseUrl = onboardBaseUrl
        # Letto a ogni disegno della pagina, non copiato all'avvio: il MAC con cui l'hub si
        # registra puo' comparire dopo (Wi-Fi associato piu' tardi, dongle USB inserito), e il
        # pannello deve mostrare quello vero, non quello che c'era al secondo zero.
        self.MacProvider = macProvider
        # Impostato dopo l'avvio: il manager del tunnel nasce dopo il web server ed e' l'unico
        # a conoscere l'indirizzo pubblico dell'hub, che riceve dal backend.
        self.CloudflareManager:Optional[Any] = None
        # Il codice che il cliente usa per rivendicare l'hub, generato dal backend alla prima
        # registrazione e riportato indietro nella risposta al ping.
        #
        # E' qui perche' il pannello e' l'unico posto dove lo si puo' leggere prima che
        # l'etichetta esista: al collaudo l'operatore lo legge da qui e lo stampa. Il cliente
        # invece non vedra' mai questa pagina — se ci arrivasse avrebbe gia' accesso all'hub, e
        # il codice non gli servirebbe.
        self.ClaimCode:Optional[str] = None
        self.ClaimStatus:Optional[str] = None
        # L'indirizzo completo che finisce dentro il QR, composto dal backend e non da noi:
        # l'add-on conosce il nome tecnico con cui ci parla, non quello su cui va il cliente.
        self.ClaimUrl:Optional[str] = None
        self.AccountConnected = False
        self.webServerThread:Optional[threading.Thread] = None

        # This indicates if we are running in dev mode.
        self.RunDevWebServer = self.GetDevConfigContains(devConfig, "RunWebServer")

        # We bind to the default docker ips and use port 45120.
        # The default port for Home Assistant is 8099, but that's used already by some more major software.
        self.HostName = "0.0.0.0"
        self.Port = 45120


    def Start(self, addonType:int) -> None:
        # If we aren't running as an addon and we aren't in dev mode, don't start the web server.
        if addonType != AddonTypes.HaAddon and self.RunDevWebServer is False:
            self.Logger.info("Web server not started, not running in HA addon mode.")
            return

        # Start the web server worker thread.
        self.webServerThread = threading.Thread(target=self._WebServerWorker)
        self.webServerThread.start()


    def SetCloudflareManager(self, manager:Any) -> None:
        self.CloudflareManager = manager


    # Chiamato dal reporter a ogni registrazione riuscita: il codice non cambia mai, ma lo stato
    # della rivendicazione si', e il pannello deve dire se l'hub e' ancora da consegnare.
    def SetClaimInfo(self, claimCode:Optional[str], claimStatus:Optional[str],
                     claimUrl:Optional[str] = None, ownerAuthId:Optional[str] = None,
                     ownerUsername:Optional[str] = None) -> None:
        if isinstance(claimCode, str) and len(claimCode) > 0:
            self.ClaimCode = claimCode
        if isinstance(claimStatus, str) and len(claimStatus) > 0:
            self.ClaimStatus = claimStatus
        if isinstance(claimUrl, str) and claimUrl.startswith("https://"):
            # Solo https: questo indirizzo finisce dentro un QR che verra' stampato e messo in
            # mano a un cliente, e un'etichetta sbagliata non si corregge da remoto.
            self.ClaimUrl = claimUrl
        # L'utente di Home Assistant a cui appartiene la casa. Il pannello ne ha bisogno perche'
        # il proprietario e' un utente standard: senza questo dato non c'e' modo di distinguerlo
        # dagli altri membri, e il pannello dovrebbe o nascondersi anche a lui o aprirsi a tutti.
        if isinstance(ownerAuthId, str) and len(ownerAuthId) > 0:
            self.OwnerAuthId = ownerAuthId
        # Il nome di accesso e' il dato con cui l'identificativo qui sopra si puo' CONTROLLARE:
        # in Home Assistant e' unico, e l'anagrafica sta su questa macchina.
        if isinstance(ownerUsername, str) and len(ownerUsername) > 0:
            self.OwnerUsername = ownerUsername


    # L'IDENTIFICATIVO DEL PROPRIETARIO, RICAVATO E NON CREDUTO.
    #
    # Il backend ce ne manda uno, ma e' un valore che da qui non si puo' controllare — e per un
    # periodo e' stato sbagliato: ci finiva l'identificativo della PERSONA invece di quello
    # dell'UTENTE, sono due cose diverse, e nessun percorso lo ricalcolava. Il risultato e' che
    # chi ha registrato la casa in quella finestra apre questo pannello e legge che non e' pagina
    # per lui. Per sempre, perche' niente lo correggeva.
    #
    # Il nome di accesso invece si controlla: in Home Assistant e' unico, e l'anagrafica sta su
    # questa macchina. Si parte da li' e si ricava l'identificativo vero.
    #
    # Quando i due non combaciano vince quello ricavato, e lo si dice al backend: la colonna
    # sbagliata va corretta una volta, non aggirata a ogni caricamento di pagina.
    #
    # Il risultato si tiene in memoria perche' questo giro chiede l'anagrafica a Home Assistant, e
    # farlo a ogni caricamento sarebbe un giro di rete per una cosa che non cambia.
    def ProprietarioAuthId(self) -> Optional[str]:
        if self.OwnerAuthIdRisolto is not None:
            return self.OwnerAuthIdRisolto

        nome = self.OwnerUsername
        if isinstance(nome, str) and len(nome) > 0:
            trovato = self.OwnerResolver(nome)
            if isinstance(trovato, str) and len(trovato) > 0:
                if trovato != self.OwnerAuthId:
                    self.Logger.warning(
                        "L'identificativo del proprietario che ci ha mandato il backend non e' "
                        "quello dell'utente %r su questo hub: lo correggo." % nome)
                    try:
                        self.OwnerFixer(trovato)
                    except Exception as e:
                        self.Logger.warning(f"Correzione del proprietario non spedita: {e}")
                self.OwnerAuthIdRisolto = trovato
                return trovato

        # Non si e' potuto ricavare: resta quello che ci hanno mandato, che e' meglio di niente
        # ma NON si mette in memoria — al prossimo giro si riprova, perche' il motivo per cui non
        # si e' potuto ricavare (Home Assistant non ancora raggiungibile all'avvio) passa da solo.
        return self.OwnerAuthId


    # Il QR dell'etichetta, come SVG da mettere nella pagina.
    #
    # Disegnato qui e non da uno strumento a parte perche' al collaudo il pannello e' l'unico
    # posto in cui l'apparecchio, il suo codice e chi stampa l'etichetta si trovano insieme.
    #
    # Sempre nero su bianco, anche quando il pannello e' scuro: un QR chiaro su fondo scuro e'
    # leggibile da alcuni lettori e non da altri, e l'etichetta va comunque stampata su carta
    # bianca. Il bordo di quattro moduli e' la zona di quiete prevista dallo standard — senza,
    # molti lettori non agganciano il simbolo.
    #
    # Restituisce None se qualcosa non va: il QR e' un aiuto, e il pannello deve restare
    # utilizzabile anche senza. Il codice in chiaro sotto resta comunque leggibile.
    @staticmethod
    def _QrSvg(url:str) -> Optional[str]:
        try:
            import segno
            simbolo = segno.make(url, error='m')
            buffer = io.BytesIO()
            simbolo.save(buffer, kind='svg', scale=4, border=4,
                         dark='#000000', light='#ffffff',
                         xmldecl=False, svgns=False, nl=False)
            return buffer.getvalue().decode('utf-8')
        except Exception:
            return None


    def RegisterForAccountStatusUpdates(self) -> None:
        # Register for account link callbacks.
        # This is called after startup, because the command handler isn't created until after the web server.
        CommandHandler.Get().RegisterAccountLinkStatusUpdateHandler(self)


    # Vero finche' l'hub non e' raggiungibile dall'esterno.
    #
    # Prima era un campo che veniva spento dall'handshake con il servizio di terzi da cui
    # passava l'accesso remoto. Adesso l'accesso remoto e' il nostro tunnel, quindi lo stato
    # lo chiediamo a chi lo gestisce: se il pannello dicesse "attivo" mentre il tunnel e' giu',
    # direbbe una cosa falsa proprio a chi lo apre per capire perche' non riesce a collegarsi.
    @property
    def IsPendingStartup(self) -> bool:
        manager = self.CloudflareManager
        if manager is None:
            return True
        return getattr(manager, "TunnelActive", False) is not True


    # Called when we are connected and we know if there's an account setup with this addon
    def OnPrimaryConnectionEstablished(self, hasConnectedAccount:bool) -> None:
        self.AccountConnected = hasConnectedAccount


    # Interface function
    # Called from the command handler the account link status changes.
    def OnAccountLinkStatusUpdate(self, isLinked:bool) -> None:
        self.AccountConnected = isLinked


    def _WebServerWorker(self) -> None:
        backoff:int = 0
        while True:
            # Try to run the webserver forever.
            webServer:Optional[HTTPServer] = None
            try:
                self.Logger.info(f"Web Server Starting {self.HostName}:{self.Port}")
                webServer = HTTPServer((self.HostName, self.Port), WebServer.WebServerHandler)
                self.Logger.info(f"Web Server Started {self.HostName}:{self.Port}")
                webServer.serve_forever()
            except Exception as e:
                self.Logger.error("Web server exception. "+str(e))

            # If we fail, close it.
            try:
                if webServer is not None:
                    webServer.server_close()
            except Exception as e:
                Sentry.OnException("Failed to close the addon webserver.", e)

            # Try again after some time.
            backoff = min(backoff + 1, 20)
            time.sleep(backoff * 0.5)


    class WebServerHandler(BaseHTTPRequestHandler):

        def _isAllowedIp(self) -> bool:
            if WebServer.Instance.RunDevWebServer:
                return True
            # Check if the IP is the authenticated IP from home assistant. If not, deny it.
            # This IP is brokered by Home Assistant, and it does auth checks before forwarding the requests.
            # Requests must come from 172.30.32.2 IP, they are authenticated by Home Assistant atomically, cool!
            if len(self.client_address) == 0:
                WebServer.Instance.Logger.error("Webserver got a request but we can't find the ip. Denying")
                self.send_response(401)
                self.end_headers()
                return False
            if self.client_address[0] != "172.30.32.2":
                WebServer.Instance.Logger.error(f"Webserver got a request from an invalid ip [{self.client_address[0]}]. Denying")
                self.send_response(401)
                self.end_headers()
                return False
            return True


        def _sendJson(self, code:int, payload:Dict[str, Any]) -> None:
            body = bytes(json.dumps(payload), "utf-8")
            self.send_response(code)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


        # Legge il corpo della richiesta, con un tetto.
        #
        # Va letto SEMPRE, anche quando la risposta e' un 404: con keep-alive un corpo lasciato
        # nel socket manda fuori sincrono la richiesta successiva e la connessione cade. Il tetto
        # evita che un Content-Length dichiarato enorme tenga occupato l'unico thread del server.
        def _readBody(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return b""
            if length <= 0:
                return b""
            return self.rfile.read(min(length, WebServer.c_MaxRequestBodyBytes))


        def do_POST(self):
            # Check if the IP is allowed.
            if not self._isAllowedIp():
                self._readBody()
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            raw = self._readBody()

            # L'unica rotta che scrive e' l'azzeramento per la clonazione. Il percorso arriva
            # gia' privato del prefisso dell'Ingress, ma lo confrontiamo per suffisso: cosi'
            # regge anche se un giorno il Supervisor cambia come inoltra. La query si scarta,
            # altrimenti un "?x=1" in coda trasformerebbe la rotta in un 404.
            route = self.path.split("?", 1)[0].split("#", 1)[0].rstrip("/")

            # L'INGRESSO NEL PORTALE, PER CHI E' GIA' AMMINISTRATORE QUI.
            #
            # Il pulsante apriva la radice del portale, e il browser del telefono non ha nessuna
            # sessione: la procedura ripartiva dal primo passo anche su un hub gia' registrato.
            # Passare il MAC non risolve, perche' rispondere "questo hub e' di mario@..." a
            # chiunque conosca un indirizzo hardware sarebbe una fuga.
            #
            # Qui a chiederlo e' l'hub, che si autentica col proprio segreto, e lo fa solo dopo
            # aver verificato che chi ha premuto sia amministratore su questo sistema. Chi lo e'
            # ha gia' piu' potere di quanto il portale gliene dia: non gli si sta concedendo
            # niente di nuovo, gli si sta risparmiando un giro di email.
            if route.endswith("/panel-link"):
                if not WebServer.Instance.RunDevWebServer:
                    # L'amministratore O il proprietario di casa. Il secondo e' un utente
                    # standard e resterebbe fuori da un controllo sul solo ruolo, che e'
                    # esattamente il motivo per cui questa strada esiste: e' casa sua.
                    userId = self.headers.get(HaAdmin.c_UserIdHeader, "")
                    # Lo stesso valore ricavato che usa la pagina: se qui si guardasse quello
                    # grezzo, il proprietario vedrebbe la sezione delle persone di casa e poi si
                    # sentirebbe rifiutare il pulsante che ci sta dentro.
                    proprietario = WebServer.Instance.ProprietarioAuthId()
                    eProprietario = (isinstance(proprietario, str) and len(proprietario) > 0
                                     and userId == proprietario)
                    if not eProprietario and WebServer.Instance.AdminCheck(userId) is not True:
                        WebServer.Instance.Logger.warning(f"Ingresso nel portale rifiutato per [{userId}].")
                        self._sendJson(403, {"error": "questa configurazione la puo' aprire chi ha registrato la casa"})
                        return
                url = WebServer.Instance.PanelLinkProvider()
                if not url:
                    self._sendJson(502, {"error": "non riesco a parlare con Sweetplace. Riprova fra un minuto."})
                    return
                self._sendJson(200, {"url": url})
                return

            if not route.endswith("/factory-reset"):
                self._sendJson(404, {"error": "rotta sconosciuta"})
                return

            # SOLO gli amministratori di Home Assistant.
            #
            # Il pannello non compare nella barra laterale di un utente normale, ma questa rotta
            # gli risponderebbe lo stesso: Home Assistant lascia apposta aperte ai non
            # amministratori sia /ingress/session sia /addons/<slug>/info, che restituisce
            # l'indirizzo ingress con il suo token, e il Supervisor sulla rotta convalida solo
            # che la sessione esista. Nascondere il pulsante non chiude la porta.
            #
            # Il controllo va PRIMA della parola di conferma: e' un'autorizzazione, non una
            # validazione, e chi non ha diritto non deve nemmeno sapere se ha indovinato.
            if not WebServer.Instance.RunDevWebServer:
                userId = self.headers.get(HaAdmin.c_UserIdHeader, "")
                esito = WebServer.Instance.AdminCheck(userId)
                if esito is not True:
                    WebServer.Instance.Logger.warning(f"Azzeramento rifiutato: l'utente [{userId}] non risulta amministratore (esito {esito}).")
                    if esito is False:
                        self._sendJson(403, {"error": "solo un amministratore del sistema operativo puo' azzerare questo hub"})
                    else:
                        # Non sapere non e' un si'. Ma va distinto, perche' se e' un guasto della
                        # connessione verso Home Assistant l'operatore deve poterlo capire.
                        self._sendJson(403, {"error": "non sono riuscito a verificare con il sistema operativo chi sei. Riprova quando l'hub e' collegato."})
                    return

            # La parola di conferma non e' teatro. Il pulsante e' dentro l'iframe di Home
            # Assistant, dove basta un clic sbagliato per distruggere l'identita' di un hub
            # in produzione, e non c'e' modo di annullare.
            try:
                body:Any = json.loads(raw.decode("utf-8")) if len(raw) > 0 else {}
                confirm = cast(Dict[str, Any], body).get("confirm") if isinstance(body, dict) else None
            except Exception:
                confirm = None
            if confirm != ImagePrep.c_ConfirmWord:
                WebServer.Instance.Logger.warning("Azzeramento rifiutato: parola di conferma mancante o errata.")
                self._sendJson(400, {"error": "conferma mancante"})
                return

            WebServer.Instance.Logger.info("Azzeramento per la clonazione richiesto dal pannello.")
            try:
                actions = WebServer.Instance.WipeAction()
            except Exception as e:
                Sentry.OnException("Azzeramento per la clonazione fallito.", e)
                self._sendJson(500, {"error": str(e)})
                return
            self._sendJson(200, {"actions": actions})


        def do_GET(self):

            # Check if the IP is allowed.
            if not self._isAllowedIp():
                self.send_response(401)
                self.end_headers()
                return

            # Send the basic HTML
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            # QUESTA PAGINA NON SI METTE IN CACHE, MAI.
            #
            # Non e' un documento: e' lo stato dell'apparecchio in questo istante — se il tunnel
            # e' su, il codice di rivendicazione, l'indirizzo pubblico, cosa c'e' ancora sul
            # disco da azzerare. Una copia vecchia non e' una pagina un po' datata, e' una
            # pagina che dice il falso.
            #
            # Senza queste intestazioni il browser era libero di tenersela, e infatti se l'e'
            # tenuta: dopo un aggiornamento dell'add-on il pannello continuava a mostrare la
            # versione precedente, facendo sembrare che l'aggiornamento non fosse arrivato.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            mac = WebServer.Instance.MacProvider()
            onboardUrl = WebServer.Instance.OnboardBaseUrl + "/"
            if len(mac) > 0:
                onboardUrl += "?mac=" + quote(mac, safe="")
            macText = mac if len(mac) > 0 else "non rilevato"

            # Il codice di rivendicazione, mostrato raggruppato in due blocchi di quattro:
            # e' cosi' che va stampato sull'etichetta e cosi' che il portale lo accetta. La
            # normalizzazione lato backend toglie comunque i trattini, quindi il raggruppamento
            # e' solo un aiuto per chi legge e trascrive.
            codice = WebServer.Instance.ClaimCode
            if isinstance(codice, str) and len(codice) == 8:
                codiceTesto = escape(codice[:4] + "-" + codice[4:])
            elif isinstance(codice, str) and len(codice) > 0:
                codiceTesto = escape(codice)
            else:
                codiceTesto = None

            # Se l'hub e' gia' stato rivendicato il codice ha esaurito il suo compito: si dice,
            # invece di lasciar credere che serva ancora consegnarlo a qualcuno.
            claimFatto = WebServer.Instance.ClaimStatus == "VERIFIED"
            claimUrl = WebServer.Instance.ClaimUrl
            qrSvg = None
            if isinstance(claimUrl, str) and not claimFatto:
                # Non si disegna il QR di un hub gia' rivendicato: non serve piu' a nessuno, e
                # lasciarlo li' fa credere che ci sia ancora una consegna da fare.
                qrSvg = WebServer._QrSvg(claimUrl)

            # CHI STA GUARDANDO QUESTA PAGINA.
            #
            # Tre casi, e tre pagine diverse. L'amministratore vede tutto, compresa la
            # preparazione dell'immagine. Il proprietario di casa e' un utente standard e non
            # deve vedere gli strumenti di fabbrica, ma deve avere da qualche parte il modo di
            # gestire le persone di casa: fino a ieri quel posto non esisteva, perche' la voce
            # nella barra laterale la vedevano solo gli amministratori. Gli altri membri della
            # casa non hanno niente da fare qui.
            #
            # In sviluppo non c'e' nessun Home Assistant a cui chiedere, quindi si mostra tutto:
            # e' lo stesso ripiego che usa la rotta dell'azzeramento.
            utenteId = self.headers.get(HaAdmin.c_UserIdHeader, "")
            if WebServer.Instance.RunDevWebServer:
                sonoAmministratore = True
                sonoProprietario = True
            else:
                sonoAmministratore = WebServer.Instance.AdminCheck(utenteId) is True
                proprietario = WebServer.Instance.ProprietarioAuthId()
                sonoProprietario = (isinstance(proprietario, str) and len(proprietario) > 0
                                    and utenteId == proprietario)

            # L'indirizzo pubblico arriva dal backend insieme al token del tunnel: finche' il
            # tunnel non e' salito non lo conosciamo.
            publicUrl = None
            manager = WebServer.Instance.CloudflareManager
            if manager is not None:
                publicUrl = manager.PublicUrl
            if not isinstance(publicUrl, str) or len(publicUrl) == 0:
                publicUrlHtml = "in attesa del tunnel..."
            elif publicUrl.startswith("https://"):
                publicUrlHtml = f'<a href="{escape(publicUrl, quote=True)}" target="_blank" class="blueLink">{escape(publicUrl)}</a>'
            else:
                # Indirizzo inatteso: lo mostriamo come testo, mai come link.
                publicUrlHtml = escape(publicUrl)
            # Il MAC e il pulsante di configurazione sono dati locali e non dipendono da nessun
            # servizio remoto: restano sempre visibili. Lo stato di avvio governa solo la riga di
            # stato e la ricarica automatica, altrimenti un hub che non riesce a collegarsi
            # resterebbe senza l'unica azione che puo' compiere.
            # I colori sono quelli semantici di Home Assistant, dichiarati nel foglio di stile
            # piu' sotto. Passare "var(--warning-color)" invece di un esadecimale funziona anche
            # dentro un attributo style, e fa si' che il pannello segua il tema chiaro o scuro
            # senza doppioni da tenere allineati a mano.
            if WebServer.Instance.IsPendingStartup:
                statusColor = "var(--warning-color)"
                statusText = "Avvio in corso..."
                connectingTimerBool = "true"
            else:
                statusColor = "var(--success-color)"
                statusText = "Hub attivo"
                connectingTimerBool = "false"

            # Referto sulla preparazione dell'immagine. Se la lettura fallisce il pannello deve
            # restare utilizzabile: la configurazione del cliente non dipende da questa sezione.
            try:
                report = WebServer.Instance.ReportProvider()
            except Exception as e:
                WebServer.Instance.Logger.warning(f"Referto sulla preparazione non disponibile: {e}")
                report = [{"level": ImagePrep.c_LevelWarn, "title": "Referto",
                           "detail": "Non disponibile in questo momento. Guarda i log dell'add-on."}]
            # Gli stessi tre colori del resoconto dopo l'azzeramento (c_ColoriEsito): la
            # stessa gravita' deve avere lo stesso colore nei due riquadri, che stanno uno
            # sopra l'altro nella stessa pagina.
            levelColors = {k: v[0] for k, v in WebServer.c_ColoriEsito.items()}
            # I colori li serve il server, cosi' esistono in un posto solo: la pagina non li
            # ridichiara e non possono divergere dai pallini del referto qui sopra.
            coloriEsitoJs = json.dumps(WebServer.c_ColoriEsito)
            prepRows = ""
            for finding in report:
                dotColor = levelColors.get(finding.get("level", ""), "var(--secondary-text-color)")
                prepRows += ('<div class="prepRow"><div class="prepDot" style="background-color:' + dotColor + '"></div>'
                             '<div><b>' + escape(finding.get("title", "")) + '</b>'
                             '<div class="subtleText">' + escape(finding.get("detail", "")) + '</div></div></div>')
            if ImagePrep.HasBlockers(report):
                prepSummaryText = "Da azzerare prima di clonare"
                prepSummaryColor = "var(--error-color)"
            else:
                prepSummaryText = "Pronta per la clonazione"
                prepSummaryColor = "var(--success-color)"
            # IL PANNELLO CAMBIA CON LA FASE DI VITA DELL'APPARECCHIO.
            #
            # Le due persone che aprono questa pagina non cercano la stessa cosa. In fabbrica
            # serve il codice da stampare sull'etichetta; a casa del cliente serve l'indirizzo a
            # cui aprire il proprio impianto, e il codice ha gia' esaurito il suo compito.
            #
            # Prima erano cinque schede tutte allo stesso peso, quindi entrambi dovevano
            # cercare. Adesso in cima c'e' solo quello che serve ADESSO, e il resto sta sotto
            # "Dettagli tecnici", che si apre se lo si cerca. Lo spartiacque e' la
            # rivendicazione, perche' e' esattamente il momento in cui l'apparecchio smette di
            # essere merce e diventa l'impianto di qualcuno.
            dominio = escape(claimUrl.split("/c/")[0].replace("https://", "")) if claimUrl else ""

            # IL TITOLO E' L'INDIRIZZO DELL'APPARECCHIO, NON IL NOSTRO NOME.
            # Chi apre questa pagina sa gia' di che prodotto si tratta; quello che non sa, e per
            # cui e' venuto, e' QUALE hub sta guardando e se risponde. Finche' il tunnel non e'
            # salito quell'indirizzo non lo conosciamo, e si dice quello invece di lasciare un
            # titolo vuoto.
            titoloPagina = escape(publicUrl.replace("https://", "")) if publicUrl else "Indirizzo non ancora assegnato"

            if codiceTesto is None:
                # Prima dell'ottenimento del codice non si sa ancora in quale fase siamo.
                sezionePrincipale = (
                    '<section class="sezione"><div>'
                    '<h2 class="sezioneTitolo">Registrazione in corso</h2>'
                    '<p class="sezioneNota">L\'apparecchio si sta annunciando.</p>'
                    '</div><div>'
                    '<p class="sezioneNota">Il codice di rivendicazione compare qui appena la '
                    'registrazione riesce. Se resta cosi\' per piu\' di qualche minuto, guarda '
                    'lo stato qui sopra: senza rete non si registra.</p>'
                    '</div></section>')
                datiCodice = ""

            elif claimFatto:
                # ── L'APPARECCHIO E' DI QUALCUNO ────────────────────────────────────────────
                # L'ELENCO STA QUI, NON SOLO DIETRO IL PULSANTE.
                #
                # Questa pagina mostra gia' l'indirizzo hardware, quello pubblico e il codice:
                # la cosa che a chi guarda interessa di piu', cioe' chi puo' entrare in questa casa,
                # era l'unica dietro un clic e un'altra scheda. Il pulsante resta, e sta in fondo
                # all'elenco: prima si guarda chi c'e', poi si aggiunge.
                #
                # None vuol dire "non ho potuto chiedere", che non e' "non c'e' nessuno": la
                # differenza si dice, altrimenti un hub scollegato sembra una casa vuota.
                membri = WebServer.Instance.MembersProvider()
                if membri is None:
                    elenco = ('<p class="sezioneNota">Non riesco a leggere l\'elenco adesso. '
                              'Guarda lo stato qui sopra.</p>')
                elif len(membri) == 0:
                    elenco = ('<p class="sezioneNota">Ancora nessuno oltre a chi ha registrato '
                              'la casa.</p>')
                else:
                    # L'intestazione si disegna solo quando c'e' un elenco sotto: due nomi di
                    # colonna sopra il nulla sono una tabella vuota, non una spiegazione.
                    elenco = ('<div class="membriIntestazione">'
                              '<div>Nome reale</div><div>Account</div></div>')
                    for m in membri:
                        accesso = escape(str(m.get("accesso") or ""))
                        elenco += ('<div class="membro"><div>' + escape(str(m.get("nome") or "")) + '</div>'
                                   + ('<div class="membroAccesso">' + accesso + '</div>' if accesso else '')
                                   + '</div>')

                sezionePrincipale = (
                    '<section class="sezione"><div>'
                    '<h2 class="sezioneTitolo">Le persone di casa</h2>'
                    '<p class="sezioneNota">Chi puo\' entrare, e con quale nome. Si aggiungono e '
                    'si tolgono dal portale, che si apre in una scheda nuova.</p>'
                    '</div><div>'
                    + elenco +
                    '<div class="featureButton" id="apriPortale" style="margin-top:var(--ha-space-4);">'
                    'Apri la configurazione</div>'
                    '</div></section>')
                # Il codice resta consultabile, ma fra i dati tecnici: serve solo all'assistenza.
                datiCodice = ('<div class="datiEtichettaRiga">Codice</div>'
                              '<div class="fieldValue">' + codiceTesto + '</div>')

            else:
                # ── L'APPARECCHIO E' ANCORA MERCE ───────────────────────────────────────────
                qrHtml = ('<div class="qrBox">' + qrSvg + '</div>') if qrSvg else ''
                sezionePrincipale = (
                    '<section class="sezione"><div>'
                    '<h2 class="sezioneTitolo">Da consegnare</h2>'
                    '<p class="sezioneNota">Stampa questi due sull\'etichetta sotto '
                    'l\'apparecchio. Il cliente inquadra il QR, oppure digita il codice su '
                    '<b>' + dominio + '</b>: senza, non puo\' rivendicarlo nessun altro.</p>'
                    '</div><div>'
                    '<div class="consegna">' + qrHtml +
                    '<div class="codiceGrande">' + codiceTesto + '</div></div>'
                    '<div class="featureButton featureButtonQuieto" id="goToOnboarding">'
                    'Registra il tuo Sweetplace</div>'
                    '</div></section>')
                datiCodice = ""

            # Gli strumenti di chi installa: l'indirizzo hardware, il codice dell'etichetta e
            # il pulsante che distrugge l'identita' dell'apparecchio. Chi li vede lo decide la
            # scelta piu' sotto, non questa variabile.
            sezioniDiServizio = """
            <section class="sezione">
                <div>
                    <h2 class="sezioneTitolo">Dettagli tecnici</h2>
                    <p class="sezioneNota">Come si chiama questo apparecchio, dove risponde e
                    con che codice si registra. Servono a chi lo installa e a chi da' assistenza.</p>
                </div>
                <div class="dati">
                    <div class="datiEtichettaRiga">Indirizzo hardware</div>
                    <div class="fieldValue">"""+escape(macText)+"""</div>
                    <div class="datiEtichettaRiga">Indirizzo pubblico</div>
                    <div class="fieldValue">"""+publicUrlHtml+"""</div>
                    """+datiCodice+"""
                </div>
            </section>

            <section class="sezione">
                <div>
                    <h2 class="sezioneTitolo">Preparazione immagine</h2>
                    <p class="sezioneNota">Da fare prima di clonare il disco su altri hub.
                    Cancella l'identita' di questo apparecchio.
                    <b>L'operazione non e' reversibile: non si torna indietro.</b></p>
                </div>
                <div>
                    <div class="prepSummary" style="color:"""+prepSummaryColor+""";">
                        """+prepSummaryText+"""
                    </div>
                    """+prepRows+"""

                    <!-- L'azzeramento sta dentro un pannello da aprire, e non e' un ripiego:
                         e' l'unica cosa in questa pagina che distrugge qualcosa, e un campo
                         di testo con accanto un pulsante rosso, sempre aperto, e' un invito.
                         Il referto qui sopra invece resta visibile: e' quello che si viene a
                         leggere, e nasconderlo renderebbe il pannello inutile. -->
                    <details id="prepDetails" class="zonaPericolo">
                        <summary class="featureHeader">Azzera questo apparecchio</summary>
                        <div class="prepDanger">
                            Azzera identita' Sweetlink, vincolo hardware e chiave del tunnel protetto,
                            poi ferma l'add-on. Quello che viene cancellato non si recupera:
                            questo apparecchio torna come appena uscito di fabbrica, e chi lo
                            aveva rivendicato deve rifare la registrazione da capo.
                        </div>
                        <input id="prepConfirm" class="prepInput" type="text" autocomplete="off" spellcheck="false" placeholder="scrivi AZZERA per confermare">
                        <div class="featureButton redFeatureButton" id="prepButton">
                            Azzera e prepara la clonazione
                        </div>
                        <div id="prepResult" class="featureDetails" style="margin-top:var(--ha-space-3); word-break: break-word;"></div>
                    </details>
                </div>
            </section>
            """

            # ── CHI VEDE COSA. TRE CASI, TRE PAGINE, DECISI QUI E SOLO QUI. ────────────────
            #
            # Prima la scelta era sparsa: una condizione sulla variabile delle schede, una
            # riscrittura della sezione principale piu' sotto. Due punti che decidevano la
            # stessa cosa sono due punti da tenere d'accordo, e il giorno che se ne aggiunge un
            # terzo nessuno se ne accorge finche' qualcuno non vede quello che non deve.
            #
            # Questo decide cosa si VEDE. Cosa si puo' FARE lo decidono le rotte, ognuna per
            # conto proprio: azzerare vuole un amministratore, aprire il portale vuole un
            # amministratore o il proprietario. Home Assistant lascia raggiungibile la rotta
            # ingress a chiunque abbia un account su questo hub, quindi una pagina che non
            # mostra un pulsante non e' una porta chiusa: e' solo una porta non indicata.
            if sonoAmministratore:
                # Chi installa: tutto, comprese le due schede di servizio.
                corpoPagina = sezionePrincipale + sezioniDiServizio
            elif sonoProprietario:
                # Chi ha registrato la casa: il proprio impianto e da li' la gestione delle
                # persone. Niente strumenti di fabbrica: non deve poter azzerare l'apparecchio
                # ne' leggere il codice che serviva a consegnarlo.
                corpoPagina = sezionePrincipale
            else:
                # Chiunque altro abiti in questa casa. La voce nella barra laterale la vede
                # comunque, perche' Home Assistant sa distinguere solo fra amministratori e
                # tutti gli altri: se compare, tanto vale che dica perche' non serve a lui.
                corpoPagina = (
                    '<div class="featureHolder"><div>'
                    '<div class="featureHeader">Questa pagina non serve a te</div>'
                    '<div class="featureDetails" style="line-height:1.6;">'
                    "Da qui si gestisce la casa nel suo insieme: chi puo' entrare, l'indirizzo, "
                    "le impostazioni dell'apparecchio. Sono cose che valgono per tutti quelli che "
                    "ci abitano, e le tiene chi ha registrato la casa."
                    '<br><br>'
                    "Tu la casa la usi dall'app Home Assistant, con il nome di accesso e la "
                    "password che ti sono stati consegnati: da li' vedi e comandi tutto quello a "
                    "cui hai accesso, da qualunque posto."
                    '<br><br>'
                    "Se ti serve qualcosa che da qui non ottieni, un nome diverso, una password "
                    "nuova, l'accesso a qualcosa che non vedi, chiedilo a chi ha registrato la "
                    "casa: e' l'unico che puo' farlo."
                    '</div>'
                    '</div></div>')

            html = """
<html>
<head><title>Sweetplace Control</title>
<style>
    /* I VALORI DEL TEMA DI HOME ASSISTANT, RIDICHIARATI QUI.

       Il pannello di un add-on gira dentro un iframe dell'Ingress, e le proprieta' CSS
       personalizzate del tema NON attraversano il confine di un iframe: un
       var(--primary-text-color) preso in prestito da Home Assistant sarebbe semplicemente
       indefinito, e ogni regola che ci si appoggia cadrebbe sul valore di ripiego. L'unico
       modo di somigliare a Lovelace e' dichiarare gli stessi valori da questa parte.

       Non sono scelti a occhio: vengono dal sorgente del frontend (home-assistant/frontend,
       branch dev), e se cambiano si aggiornano da li'.
         src/resources/theme/color/color.globals.ts   colori del tema chiaro e di quello scuro
         src/resources/theme/color/core.globals.ts    scala dei neutri, raggi, spaziature
         src/resources/theme/typography.globals.ts    famiglia, scala dei corpi, pesi */
    :root {
        color-scheme: dark light;

        --ha-font-family-body: Roboto, Noto, sans-serif;
        --ha-font-size-s: 12px;
        --ha-font-size-m: 14px;
        --ha-font-size-l: 16px;
        --ha-font-size-xl: 20px;
        --ha-font-weight-normal: 400;
        --ha-font-weight-medium: 500;
        --ha-line-height-condensed: 1.2;
        --ha-line-height-normal: 1.6;

        --ha-space-1: 4px;
        --ha-space-2: 8px;
        --ha-space-3: 12px;
        --ha-space-4: 16px;
        --ha-space-5: 20px;
        --ha-space-6: 24px;

        --ha-border-radius-sm: 4px;
        --ha-border-radius-lg: 12px;
        --ha-border-radius-pill: 9999px;

        /* Semantici: gli stessi nei due temi. */
        --primary-color: #009ac7;
        --dark-primary-color: #0288d1;
        --text-primary-color: #ffffff;
        --error-color: #db4437;
        --warning-color: #ffa600;
        --success-color: #43a047;

        /* Scuro come predefinito: e' il tema con cui il pannello e' nato, e mostrarlo chiaro
           per sbaglio su un impianto scuro si nota molto piu' del contrario. */
        --primary-background-color: #111111;
        --card-background-color: #1c1c1c;
        --secondary-background-color: #282828;
        --primary-text-color: #e1e1e1;
        --secondary-text-color: #9b9b9b;
        --disabled-text-color: #6f6f6f;
        --divider-color: rgba(225, 225, 225, 0.12);
    }

    /* L'Ingress non dice quale tema abbia scelto l'utente dentro Home Assistant, quindi si
       segue la preferenza del sistema: e' l'unico segnale che attraversa l'iframe. */
    @media (prefers-color-scheme: light) {
        :root {
            --primary-background-color: #fafafa;
            --card-background-color: #ffffff;
            --secondary-background-color: #e5e5e5;
            --primary-text-color: #141414;
            --secondary-text-color: #5e5e5e;
            --disabled-text-color: #bdbdbd;
            --divider-color: rgba(0, 0, 0, 0.12);
        }
    }

    body {
        margin: 0;
        background-color: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--ha-font-family-body);
        font-size: var(--ha-font-size-m);
        font-weight: var(--ha-font-weight-normal);
        line-height: var(--ha-line-height-normal);
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    /* I MARGINI SONO UNA FRAZIONE DELLA LARGHEZZA, NON UN NUMERO DI PIXEL.
       Su un telefono il 5% per lato: piu' stretto e il testo tocca il bordo, piu' largo e su uno
       schermo da 360px si perde un terzo dello spazio utile.
       Su schermo grande un sesto per lato, cioe' due terzi al contenuto: una misura fissa in
       pixel lascerebbe il contenuto appiccicato a sinistra su un monitor largo e stretto su un
       portatile. */
    .pageWrap {
        padding: var(--ha-space-6) 5%;
    }
    @media (min-width: 721px) {
        .pageWrap { padding: var(--ha-space-6) 16.6667%; }
    }
    .panel {
        width: 100%;
        /* Nessun limite di larghezza: a decidere quanto e' largo il contenuto sono i margini
           qui sopra, e due regole che decidono la stessa cosa finiscono per litigare. */
    }

    /* Le schede si dispongono da sole: affiancate se c'e' spazio, in colonna se non ce n'e'.
       Nessun punto di rottura scritto a mano: la soglia e' la larghezza minima di una scheda,
       che e' l'unica cosa che conta davvero. */
    .griglia {
        display: grid;
        /* Fra 340 e 520: sotto i 340 una scheda non si legge, sopra i 520 una riga di testo
           diventa troppo lunga per l'occhio, e con una scheda sola, quello che vede il
           proprietario di casa, si allargherebbe per tutto lo schermo. */
        grid-template-columns: repeat(auto-fit, minmax(340px, 520px));
        justify-content: center;
        gap: var(--ha-space-3);
        align-items: start;
    }
    /* Dentro la griglia le schede non hanno bisogno del proprio margine sotto: lo spazio lo
       mette la griglia, e sommarli lascerebbe un vuoto doppio fra due righe di schede. */
    .griglia > .featureHolder {
        margin-bottom: 0;
    }
    /* La preparazione dell'immagine occupa tutta la riga: dentro c'e' un referto che puo'
       essere lungo, e a meta' larghezza si spezzerebbe in una colonna di parole. */
    .grigliaIntera {
        grid-column: 1 / -1;
    }

    /* L'INTESTAZIONE E' QUELLA DI UNA PAGINA DI CONTROLLO, NON DI UNA COPERTINA.
       Marchio centrato e stato centrato sotto facevano una pagina di presentazione: la prima
       cosa che si legge era il nostro nome, che chi apre questa pagina conosce gia'. Qui la
       prima cosa e' QUALE apparecchio si sta guardando e se risponde, che e' l'unica domanda
       con cui si arriva. E' la forma che hanno le pagine di un dispositivo in Tailscale e
       Twingate: nome grande, stato accanto, tutto a filo di sinistra. */
    .brand {
        font-size: var(--ha-font-size-s);
        color: var(--secondary-text-color);
        margin-bottom: var(--ha-space-1);
    }
    .testata {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--ha-space-3);
        margin-bottom: var(--ha-space-6);
    }
    .titoloPagina {
        margin: 0;
        font-size: var(--ha-font-size-xl);
        font-weight: var(--ha-font-weight-medium);
        line-height: var(--ha-line-height-condensed);
        word-break: break-all;
    }
    /* La spia diventa una pastiglia accanto al nome: una riga di stato per conto suo, centrata,
       si legge come un titolo e non come una proprieta' dell'apparecchio. */
    .pastiglia {
        display: inline-flex;
        align-items: center;
        gap: var(--ha-space-2);
        padding: var(--ha-space-1) var(--ha-space-3);
        border-radius: var(--ha-border-radius-pill);
        background-color: var(--secondary-background-color);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
        white-space: nowrap;
    }

    /* Una sezione: a sinistra cosa e' e a cosa serve, a destra la cosa. Su schermo stretto le
       due colonne diventano una, senza punti di rottura scritti a mano. */
    .sezione {
        display: grid;
        grid-template-columns: minmax(220px, 1fr) minmax(320px, 2fr);
        /* DUE SPAZI DIVERSI, PERCHE' SEPARANO DUE COSE DIVERSE.
           In orizzontale dividono la spiegazione dal contenuto: 16px li lasciavano cosi' vicini
           che la riga di sinistra sembrava la prima colonna di una tabella. In verticale — su
           uno schermo stretto, dove le due colonne diventano una — dividono un blocco dal
           successivo, e li' 16px bastano perche' c'e' gia' il cambio di corpo a separarli. */
        column-gap: 32px;
        row-gap: var(--ha-space-4);
        /* LE DUE COLONNE PARTONO DALLA STESSA RIGA DI TESTO, NON DALLO STESSO PIXEL.
           Allineate in cima ai riquadri, il titolo di sinistra (16px) e la prima riga di destra
           (12 o 14px) cadono a altezze diverse: la riga piu' piccola galleggia dentro la propria
           riga di testo, e le due partenze si scostano. Erano vicine per caso, ed era un caso
           che dipende dai corpi che il tema di Home Assistant assegna a quelle variabili: basta
           un tema con proporzioni diverse e si vede.
           baseline allinea la PRIMA LINEA DI BASE dei due riquadri, che e' esattamente quello
           che l'occhio legge come "partono insieme", e lo fa qualunque corpo abbiano. */
        align-items: baseline;
        /* Piu' aria sotto che sopra: il filetto separa due sezioni, e con lo stesso spazio da
           una parte e dall'altra non si capisce a quale delle due appartenga cio' che gli sta
           vicino. Il contenuto respira dopo l'ultima riga, non prima della successiva. */
        padding: var(--ha-space-5) 0 calc(var(--ha-space-5) + 24px);
        border-top: 1px solid var(--divider-color);
    }
    @media (max-width: 720px) {
        .sezione { grid-template-columns: 1fr; }
    }
    .sezioneTitolo {
        margin: 0 0 var(--ha-space-2);
        font-size: var(--ha-font-size-l);
        font-weight: var(--ha-font-weight-medium);
    }
    .sezioneNota {
        margin: 0;
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
        line-height: 1.5;
    }
    /* L'elenco dei dati: etichetta a sinistra, valore a destra, una riga per volta. Sempre
       visibile, non dentro un pannello da aprire: sono i tre valori che si cercano quando
       qualcosa non va, e nasconderli dietro un clic li rende introvabili proprio allora. */
    .dati {
        display: grid;
        grid-template-columns: max-content 1fr;
        gap: var(--ha-space-2) var(--ha-space-4);
        align-items: baseline;
    }
    .datiEtichettaRiga {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
    }
    /* L'elenco delle persone: una riga per persona, il nome sopra e il nome di accesso sotto.
       Nessuna scheda intorno a ognuna, perche' sono elementi di una lista e non oggetti
       separati, e il filetto le divide come nelle tabelle di dispositivi di Tailscale e
       Twingate. */
    .membro {
        display: grid;
        /* IL NOME DI ACCESSO STA ACCANTO AL NOME, NON DALL'ALTRA PARTE DELLA PAGINA.
           Con space-between i due finivano ai due estremi della colonna: su uno schermo largo si
           misuravano 579px di vuoto in mezzo, e per leggere con che nome entra una persona
           bisognava attraversarli. Sono due dati della stessa riga, non due colonne di una
           tabella larga quanto lo schermo.
           La prima colonna ha un tetto: fin dove serve al nome piu' lungo, poi il nome di accesso
           comincia. Cio' che avanza resta vuoto a destra, che e' il posto giusto per il vuoto. */
        grid-template-columns: minmax(0, 18rem) max-content;
        align-items: baseline;
        gap: var(--ha-space-3);
        padding: var(--ha-space-3) 0;
        border-bottom: 1px solid var(--divider-color);
    }
    .membro:first-child { padding-top: 0; }
    /* L'intestazione dice cosa sono le due colonne. Senza, "pipoo" accanto a "Mariotti Pippo"
       poteva essere un soprannome, un ruolo o un identificativo: e' il nome con cui quella
       persona entra in casa, e va detto. */
    .membriIntestazione {
        display: grid;
        grid-template-columns: minmax(0, 18rem) max-content;
        gap: var(--ha-space-3);
        padding-bottom: var(--ha-space-2);
        border-bottom: 1px solid var(--divider-color);
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .membroAccesso {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
        font-family: monospace;
    }

    /* La zona da cui si torna indietro solo con un cacciavite. */
    /* I DUE PULSANTI HANNO LA STESSA LARGHEZZA, E NON E' UN VEZZO.
       Uno apre la configurazione, l'altro distrugge l'apparecchio: sono i due comandi della
       pagina, e finche' uno era largo quanto il suo testo e l'altro quanto la colonna, il piu'
       pericoloso era anche il piu' grande — cioe' quello che l'occhio raggiunge per primo.
       La misura viene dal testo piu' lungo: "Azzera e prepara la clonazione" occupa 206px, che
       con il riempimento fanno 246. Diciassette rem sono 272: entrambi ci stanno su una riga, e
       ne restano abbastanza perche' una traduzione piu' lunga non vada a capo.
       E' un minimo e non una larghezza fissa: se un giorno un testo cresce oltre, il pulsante si
       allarga invece di tagliarlo. */
    .sezione .featureButton {
        display: inline-block;
        min-width: 17rem;
        max-width: 100%;
        box-sizing: border-box;
        margin-top: 0;
    }
    .sezione .zonaPericolo .featureButton {
        margin-top: var(--ha-space-3);
    }

    /* Nessun contorno rosso: il pulsante e' gia' rosso, il testo dice cosa succede, e la
       cornice non aggiungeva niente se non un riquadro dentro un altro riquadro. */
    .zonaPericolo {
        margin-top: var(--ha-space-5);
    }
    .whiteLink {
        color: var(--primary-text-color);
        text-decoration: none;
    }
    .whiteLink:hover {
        text-decoration: underline;
    }
    .blueLink {
        color: var(--primary-color);
        text-decoration: none;
        font-weight: var(--ha-font-weight-medium);
    }
    .blueLink:hover {
        text-decoration: underline;
    }
    .subtleText {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
    }

    .statusRow {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: var(--ha-space-2);
        margin-bottom: var(--ha-space-4);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
    }
    .statusDot {
        width: 8px;
        height: 8px;
        border-radius: var(--ha-border-radius-pill);
        flex-shrink: 0;
    }

    /* La scheda di Lovelace: fondo, raggio 12px e un bordo di 1px del colore del divisore.
       Nessuna ombra, perche' ha-card.ts la lascia a "none" quando il tema non la definisce. */
    .featureHolder {
        display: flex;
        flex-direction: column;
        background-color: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: var(--ha-border-radius-lg);
        padding: var(--ha-space-4);
        margin-bottom: var(--ha-space-3);
    }
    .featureHeader {
        font-size: var(--ha-font-size-l);
        font-weight: var(--ha-font-weight-medium);
        line-height: var(--ha-line-height-condensed);
        margin-bottom: var(--ha-space-1);
    }
    .featureDetails {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
    }
    /* Il valore di un campo, distinto dalla descrizione che gli sta intorno: corpo pieno e
       colore primario invece del grigio secondario. */
    .fieldValue {
        font-size: var(--ha-font-size-m);
        color: var(--primary-text-color);
        word-break: break-all;
    }

    /* Lo STESSO stondo delle schede, non la pillola.
       Un pulsante a pillola dentro una scheda a raggio 12 introduce una seconda forma senza
       che nulla la giustifichi: due raggi diversi sulla stessa superficie si notano prima
       del contenuto. Meglio una forma sola, ripetuta.
       Il testo resta in medium e senza maiuscole forzate, come i pulsanti di Home Assistant:
       il maiuscolo forzato e' stato tolto da Lovelace da parecchie versioni. */
    .featureButton {
        margin-top: var(--ha-space-3);
        background-color: var(--primary-color);
        color: var(--text-primary-color);
        border-radius: var(--ha-border-radius-lg);
        font-size: var(--ha-font-size-m);
        font-weight: var(--ha-font-weight-medium);
        transition: background-color 0.2s ease-in-out;
        padding: var(--ha-space-3) var(--ha-space-5);
        text-align: center;
        cursor: pointer;
        /* Disable select for all buttons */
        user-select: none; /* supported by Chrome and Opera */
        -webkit-user-select: none; /* Safari */
        -khtml-user-select: none; /* Konqueror HTML */
        -moz-user-select: none; /* Firefox */
        -ms-user-select: none; /* Internet Explorer/Edge */
    }
    .featureButton:hover {
        background-color: var(--dark-primary-color);
    }
    .switch {
        position: relative;
        display: inline-block;
        width: 50px;
        height: 27px;
        margin-bottom: 0px;
        margin-left: var(--ha-space-3);
    }
        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
    .slider {
        position: absolute;
        cursor: pointer;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: var(--disabled-text-color);
        -webkit-transition: .4s;
        transition: .4s;
        border-radius: var(--ha-border-radius-pill);
    }
        .slider:before {
            position: absolute;
            content: "";
            height: 19px;
            width: 19px;
            left: 5px;
            bottom: 4px;
            background-color: var(--text-primary-color);
            -webkit-transition: .4s;
            transition: .4s;
            border-radius: 50%;
        }
    /* Can be applied to the "slider" span to show a disable state. */
    .sliderDisabled:before {
        background-color: var(--secondary-background-color);
        cursor:not-allowed;
    }
    input:checked + .slider {
        background-color: var(--primary-color);
    }
    input:focus + .slider {
        box-shadow: 0 0 1px var(--primary-color);
    }
    input:checked + .slider:before {
        -webkit-transform: translateX(21px);
        -ms-transform: translateX(21px);
        transform: translateX(21px);
    }
    .prepRow {
        display: flex;
        align-items: flex-start;
        margin-top: var(--ha-space-3);
    }
    /* Le righe del resoconto dopo l'azzeramento. */
    .esitoRiga {
        display: flex;
        align-items: flex-start;
        gap: var(--ha-space-2);
        margin-top: var(--ha-space-3);
    }
    .esitoEtichetta {
        flex-shrink: 0;
        border-radius: var(--ha-border-radius-pill);
        padding: 0 var(--ha-space-2);
        font-size: var(--ha-font-size-s);
        font-weight: 700;
        line-height: 1.7;
        letter-spacing: 0.02em;
    }
    .prepDot {
        width: 8px;
        height: 8px;
        border-radius: var(--ha-border-radius-pill);
        margin-right: var(--ha-space-2);
        margin-top: 6px;
        flex-shrink: 0;
    }
    /* La cornice del QR e' bianca in entrambi i temi, come il simbolo che contiene: e' cio'
       che verra' stampato, e un QR va letto nero su bianco. */
    /* Il QR e il codice stanno al CENTRO della scheda: sono una cosa sola — la si inquadra o
       la si legge — e allineati a sinistra sembravano due campi qualunque in colonna con il
       testo intorno, invece dell'oggetto per cui quella scheda esiste. */
    .consegna {
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
    }
    .qrBox {
        background-color: #ffffff;
        border-radius: var(--ha-border-radius-lg);
        padding: var(--ha-space-2);
        line-height: 0;
        margin-bottom: var(--ha-space-3);
    }
    .qrBox svg {
        display: block;
    }
    /* I dati tecnici come coppie etichetta/valore invece che come una scheda ciascuno:
       sono righe di riferimento, non azioni, e una scheda intera per una riga di testo faceva
       sembrare importante quanto il resto una cosa che si guarda una volta l'anno. */
    .datiGriglia {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: var(--ha-space-2) var(--ha-space-4);
        align-items: baseline;
        margin-top: var(--ha-space-3);
    }
    .datiEtichetta {
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
        white-space: nowrap;
    }
    /* Il codice si legge a voce e si trascrive su un'etichetta: va letto senza sforzo, e
       spaziato perche' i caratteri si distinguano uno per uno. */
    .codiceGrande {
        font-size: var(--ha-font-size-xl);
        font-weight: var(--ha-font-weight-medium);
        letter-spacing: 0.14em;
        line-height: var(--ha-line-height-condensed);
    }
    /* L'azione secondaria: c'e', ma non compete con il QR che le sta sopra. */
    .featureButtonQuieto {
        background-color: transparent;
        color: var(--primary-color);
        border: 1px solid var(--divider-color);
    }
    .featureButtonQuieto:hover {
        background-color: var(--secondary-background-color);
        color: var(--primary-color);
    }
    .prepSummary {
        margin-top: var(--ha-space-2);
        font-size: var(--ha-font-size-s);
        font-weight: var(--ha-font-weight-medium);
    }
    summary {
        cursor: pointer;
    }
    summary::marker {
        color: var(--secondary-text-color);
    }
    .prepInput {
        /* Larga quanto il pulsante che le sta sotto: sono i due passaggi della stessa azione —
           si scrive la parola, si preme — e due larghezze diverse li facevano sembrare due cose.
           La misura e' la stessa costante dei pulsanti. */
        width: 17rem;
        max-width: 100%;
        /* In colonna, non accanto al pulsante: prima il campo occupava tutta la riga e il
           pulsante andava a capo per forza. Adesso che sono larghi uguali ci starebbero
           affiancati, e la parola di conferma finirebbe accanto al comando che arma. */
        display: block;
        box-sizing: border-box;
        margin-top: var(--ha-space-3);
        padding: var(--ha-space-3);
        border-radius: var(--ha-border-radius-lg);
        border: 1px solid var(--divider-color);
        background-color: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: inherit;
        font-size: var(--ha-font-size-m);
        /* Spaziato perche' qui si scrive una parola esatta e va riletta carattere per
           carattere prima di premere: e' l'ultima difesa prima di un'azione che non si annulla. */
        letter-spacing: 2px;
    }
    /* Il segnaposto no: e' una frase, e spaziata diventa piu' difficile da leggere invece che
       piu' facile. */
    .prepInput::placeholder {
        letter-spacing: normal;
        color: var(--secondary-text-color);
    }
    .prepInput:focus {
        outline: none;
        border-color: var(--primary-color);
    }
    /* Dopo le regole generiche del pulsante, altrimenti a parita' di specificita' vincerebbero
       loro e il pulsante distruttivo resterebbe del colore primario come tutti gli altri. */
    /* La parte distruttiva si stacca dall'elenco dei rilievi con un divisore, come fanno le
       schede di Lovelace fra intestazione e azioni: leggere lo stato e distruggerlo sono due
       cose diverse e non devono sembrare la continuazione l'una dell'altra. */
    .prepDanger {
        margin-top: var(--ha-space-4);
        padding-top: var(--ha-space-4);
        border-top: 1px solid var(--divider-color);
        color: var(--secondary-text-color);
        font-size: var(--ha-font-size-s);
    }
    .redFeatureButton {
        background-color: var(--error-color);
    }
    .redFeatureButton:hover {
        background-color: #b23025;
    }
    /* Il pulsante che ha gia' fatto il suo lavoro: resta leggibile, ma smette di sembrare
       qualcosa da premere. Deve stare DOPO redFeatureButton, altrimenti a parita' di
       specificita' vincerebbe il rosso e il pulsante esaurito sembrerebbe ancora armato. */
    .featureButtonSpento,
    .featureButtonSpento:hover {
        background-color: var(--secondary-background-color);
        color: var(--secondary-text-color);
        cursor: default;
    }

    /* ── SU UNO SCHERMO STRETTO I DUE COMANDI PRENDONO TUTTA LA RIGA ──────────────────
       La misura fissa di 17rem serve su schermo largo, dove tiene della stessa larghezza il
       pulsante che apre la configurazione e quello che azzera l'apparecchio, dentro una colonna
       molto piu' ampia di loro: senza, avrebbero misure diverse a seconda del testo.
       Su uno schermo stretto la colonna e' gia' larga quanto lo schermo, quindi quel vincolo non
       allinea piu' niente — lascia solo una striscia di vuoto a destra che sembra un errore di
       impaginazione. A tutta riga sono anche bersagli piu' facili da centrare col pollice.
       Il campo di conferma segue il suo pulsante: erano stati resi uguali apposta, e lasciarlo
       indietro spezzerebbe proprio quell'allineamento.

       STA IN FONDO AL FOGLIO, E NON E' INDIFFERENTE. Le regole che fissano quelle larghezze
       vengono piu' avanti nel foglio: messo dov'era, "width: 100%" sul campo veniva riscritto
       da "width: 17rem" poche righe dopo, e il campo restava stretto mentre il pulsante si
       allargava. A parita' di specificita' vince l'ultima. */
    @media (max-width: 720px) {
        .featureButton { width: 100%; min-width: 0; box-sizing: border-box; }
        .prepInput { width: 100%; min-width: 0; }
    }
</style>
</head>
<body>
<div class="pageWrap">
    <div class="panel">
        <div class="brand">
            <a href="https://sweetplace.me" target="_blank" class="whiteLink">Sweetplace</a>
        </div>
        <div class="testata">
            <h1 class="titoloPagina">"""+titoloPagina+"""</h1>
            <span class="pastiglia" style="color:"""+statusColor+""";">
                <span class="statusDot" style="background-color:"""+statusColor+""";"></span>
                """+statusText+"""
            </span>
        </div>

        """+corpoPagina+"""
    </div>
</div>
<script>
    // Wait for the document to be ready.
    (function() {
        // L'HUB GIA' REGISTRATO NON RIPARTE DALLA RIVENDICAZIONE.
        //
        // Si chiede all'add-on un indirizzo che apre il portale gia' dentro. E' una richiesta di
        // rete, quindi il pulsante lo dice mentre aspetta: aprire una scheda vuota e poi
        // riempirla sembra un blocco.
        const apriPortale = document.getElementById("apriPortale");
        if(apriPortale)
        {
            apriPortale.onclick = () => {
                if(apriPortale.dataset.inCorso === "1") return;
                apriPortale.dataset.inCorso = "1";
                const testo = apriPortale.textContent;
                apriPortale.textContent = "Apro\u2026";
                // Percorso relativo alla pagina, come per l'azzeramento: sotto l'Ingress la
                // base cambia a ogni sessione e un percorso assoluto non esiste.
                const base = window.location.pathname.endsWith("/") ? window.location.pathname : window.location.pathname + "/";
                fetch(base + "panel-link", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
                    .then((r) => r.text().then((t) => ({ stato: r.status, testo: t })))
                    .then((risposta) => {
                        let d = null;
                        try { d = JSON.parse(risposta.testo); } catch (e) { d = null; }
                        apriPortale.dataset.inCorso = "";
                        apriPortale.textContent = testo;
                        if(d && d.url) { window.open(d.url, "_blank").focus(); return; }
                        // Non si apre comunque la radice del portale: rimanderebbe al primo
                        // passo della rivendicazione, che e' esattamente cio' che questa strada
                        // esiste per evitare, e sembrerebbe un difetto invece di un guasto.
                        alert((d && d.error) || "Non riesco ad aprire la configurazione adesso. Riprova fra un minuto.");
                    })
                    .catch((e) => {
                        apriPortale.dataset.inCorso = "";
                        apriPortale.textContent = testo;
                        alert("Non riesco ad aprire la configurazione (" + e + ").");
                    });
            };
        }

        const onboardingButton = document.getElementById("goToOnboarding");
        if(onboardingButton)
        {
            // Deve aprirsi in una scheda nuova: dentro l'iframe dell'Ingress di Home Assistant
            // la navigazione e' vincolata, e il portale usa localStorage per la sessione, che in
            // un iframe di terze parti i browser bloccano o partizionano.
            onboardingButton.onclick = (event) => { window.open('"""+onboardUrl+"""', '_blank').focus(); };
        }

        const prepDetails = document.getElementById("prepDetails");
        const prepButton = document.getElementById("prepButton");
        const prepConfirm = document.getElementById("prepConfirm");
        const prepResult = document.getElementById("prepResult");

        // I COLORI ARRIVANO DAL SERVER, IL TESTO DIVENTA NODI E NON HTML.
        // Nei dettagli finiscono i nomi degli account letti da Home Assistant, cioe' testo che
        // decide chi crea quegli account e non noi. Con innerHTML basterebbe un account chiamato
        // "<img onerror=...>" per far eseguire qualcosa in questa pagina, che gira dentro il
        // pannello di amministrazione. Con createElement e textContent il problema non esiste:
        // qualunque cosa ci sia dentro resta testo.
        const coloriEsito = """+coloriEsitoJs+""";
        function disegnaEsiti(actions)
        {
            prepResult.textContent = "";
            (actions || []).forEach((a) => {
                const c = coloriEsito[a.level]
                       || ["var(--secondary-text-color)", "#FFFFFF", String(a.level || "?").toUpperCase()];
                const riga = document.createElement("div");
                riga.className = "esitoRiga";

                const etichetta = document.createElement("span");
                etichetta.className = "esitoEtichetta";
                etichetta.style.backgroundColor = c[0];
                etichetta.style.color = c[1];
                etichetta.textContent = c[2];

                const corpo = document.createElement("div");
                const titolo = document.createElement("b");
                titolo.textContent = a.title;
                const dettaglio = document.createElement("div");
                dettaglio.className = "subtleText";
                dettaglio.textContent = a.detail;
                corpo.appendChild(titolo);
                corpo.appendChild(dettaglio);

                riga.appendChild(etichetta);
                riga.appendChild(corpo);
                prepResult.appendChild(riga);
            });
        }
        if(prepButton && prepConfirm && prepResult)
        {
            // Una volta sola. Dopo l'azzeramento il pulsante si spegne e non si riarma.
            //
            // Prima l'etichetta diventava "Fatto. Ora spegni l'apparecchio." ma il pulsante
            // restava un pulsante, con lo stesso gestore e la parola di conferma ancora nel
            // campo: chi la leggeva come una conferma e la premeva faceva ripartire
            // l'azzeramento. E a quel punto l'add-on si era gia' fermato, quindi la seconda
            // richiesta moriva contro l'Ingress e l'unica cosa che il pannello sapeva dire era
            // un errore di sintassi JSON.
            let esaurito = false;

            const spegniIlPulsante = (testo) => {
                esaurito = true;
                prepButton.textContent = testo;
                prepButton.classList.add("featureButtonSpento");
                prepConfirm.value = "";
                prepConfirm.disabled = true;
            };

            prepButton.onclick = () => {
                if(esaurito) { return; }
                const parola = prepConfirm.value.trim().toUpperCase();
                if(parola !== "AZZERA")
                {
                    prepResult.textContent = "Scrivi AZZERA nel campo qui sopra per confermare.";
                    return;
                }
                esaurito = true;   // gia' da adesso: due clic rapidi sono due azzeramenti.
                prepButton.textContent = "Azzeramento in corso...";
                prepButton.classList.add("featureButtonSpento");
                // Il percorso e' relativo alla pagina: sotto l'Ingress la base cambia a ogni
                // sessione, quindi non si puo' scrivere un percorso assoluto.
                const base = window.location.pathname.endsWith("/") ? window.location.pathname : window.location.pathname + "/";
                fetch(base + "factory-reset", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ confirm: parola })
                })
                // Il corpo si legge come TESTO e si prova a interpretarlo, invece di darlo per
                // buono con r.json(). Quando l'add-on si e' fermato, l'Ingress risponde con un
                // testo tipo "502 Bad Gateway": r.json() ci legge il numero 502 e poi inciampa
                // sulla B, e l'utente si ritrova un errore di sintassi JSON al posto di una
                // spiegazione.
                .then((r) => r.text().then((testo) => ({ stato: r.status, testo: testo })))
                .then((risposta) => {
                    let d = null;
                    try { d = JSON.parse(risposta.testo); } catch (e) { d = null; }

                    if(d === null)
                    {
                        prepResult.textContent = "L'add-on non ha risposto in modo leggibile (HTTP "
                            + risposta.stato + "). Succede quando si e' gia' fermato, che dopo un "
                            + "azzeramento riuscito e' quello che deve fare. Controlla i log prima di clonare.";
                        spegniIlPulsante("Esito da verificare nei log");
                        return;
                    }
                    if(d.error)
                    {
                        prepResult.textContent = "Errore: " + d.error;
                        // Questo NON e' un azzeramento avvenuto: il pulsante si riarma.
                        esaurito = false;
                        prepButton.classList.remove("featureButtonSpento");
                        prepButton.textContent = "Azzera e prepara la clonazione";
                        return;
                    }
                    const bloccato = (d.actions || []).some((a) => a.level === "block");
                    disegnaEsiti(d.actions);
                    spegniIlPulsante(bloccato ? "Con errori: leggi qui sotto, NON clonare" : "Fatto. Ora spegni l'apparecchio.");
                })
                .catch((e) => {
                    // La richiesta non e' nemmeno arrivata a destinazione. Anche qui non si
                    // riarma: l'azzeramento potrebbe essere avvenuto lo stesso, e ripeterlo alla
                    // cieca non aiuta nessuno.
                    prepResult.textContent = "Richiesta interrotta (" + e + "). L'add-on potrebbe "
                        + "essersi gia' fermato: controlla i log prima di clonare.";
                    spegniIlPulsante("Esito da verificare nei log");
                });
            };
        }

        if("""+connectingTimerBool+""")
        {
            // Non ricaricare mentre la sezione di preparazione e' aperta: la ricarica
            // cancellerebbe la parola di conferma appena scritta e il referto appena letto.
            setInterval(()=> { if(!prepDetails || !prepDetails.open) { location.reload(); } }, 1000)
        }
    })();
</script>
</body>
</html>
"""
            self.wfile.write(bytes(html, 'utf-8'))

    # Tries to load a dev config option as a string.
    # If not found or it fails, this return None
    def GetDevConfigContains(self, devConfig:Optional[Dict[str, str]], value:str) -> bool:
        if devConfig is None:
            return False
        if value in devConfig:
            return True
        return False
