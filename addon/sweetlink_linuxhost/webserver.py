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
    c_MaxRequestBodyBytes = 64 * 1024

    def __init__(self, logger:logging.Logger, pluginId:str, config:Config, devConfig:Optional[Dict[str,Any]], onboardBaseUrl:str, macProvider:Callable[[], str],
                 reportProvider:Callable[[], List[Dict[str, str]]], wipeAction:Callable[[], List[Dict[str, str]]],
                 adminCheck:Callable[[str], Optional[bool]]) -> None:
        WebServer.Instance = self
        self.Logger = logger
        self.PluginId = pluginId
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
        self.AccountConnected = False
        self.IsPendingStartup = True
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


    def RegisterForAccountStatusUpdates(self) -> None:
        # Register for account link callbacks.
        # This is called after startup, because the command handler isn't created until after the web server.
        CommandHandler.Get().RegisterAccountLinkStatusUpdateHandler(self)


    # Called when we are connected and we know if there's an account setup with this addon
    def OnPrimaryConnectionEstablished(self, hasConnectedAccount:bool) -> None:
        self.AccountConnected = hasConnectedAccount
        self.IsPendingStartup = False


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
                        self._sendJson(403, {"error": "solo un amministratore di Home Assistant puo' azzerare questo hub"})
                    else:
                        # Non sapere non e' un si'. Ma va distinto, perche' se e' un guasto della
                        # connessione verso Home Assistant l'operatore deve poterlo capire.
                        self._sendJson(403, {"error": "non sono riuscito a verificare con Home Assistant chi sei. Riprova quando l'hub e' collegato."})
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
            self.end_headers()
            mac = WebServer.Instance.MacProvider()
            onboardUrl = WebServer.Instance.OnboardBaseUrl + "/"
            if len(mac) > 0:
                onboardUrl += "?mac=" + quote(mac, safe="")
            macText = mac if len(mac) > 0 else "non rilevato"

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
            if WebServer.Instance.IsPendingStartup:
                statusColor = "#c0dd72"
                statusText = "Avvio in corso..."
                connectingTimerBool = "true"
            else:
                statusColor = "#31C591"
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
            levelColors = {ImagePrep.c_LevelOk: "#31C591", ImagePrep.c_LevelWarn: "#E9B949", ImagePrep.c_LevelBlock: "#F05252"}
            prepRows = ""
            for finding in report:
                dotColor = levelColors.get(finding.get("level", ""), "#939BA6")
                prepRows += ('<div class="prepRow"><div class="prepDot" style="background-color:' + dotColor + '"></div>'
                             '<div><b>' + escape(finding.get("title", "")) + '</b>'
                             '<div class="subtleText">' + escape(finding.get("detail", "")) + '</div></div></div>')
            if ImagePrep.HasBlockers(report):
                prepSummaryText = "Da azzerare prima di clonare"
                prepSummaryColor = "#F05252"
            else:
                prepSummaryText = "Pronta per la clonazione"
                prepSummaryColor = "#31C591"
            html = """
<html>
<head><title>Sweetplace Control</title>
<style>
    .whiteLink {
        color: white;
        text-decoration: none;
    }
    .whiteLink:hover {
        text-decoration: underline;
    }
    .blueLink {
        color: #0C7BFF;
        text-decoration: none;
        font-weight: bold;
    }
    .subtleText {
        color: #939BA6;
    }
    .featureHolder {
        display: flex;
        flex-direction: column;
        background-color: #282828;
        border-radius: 5px;
        padding: 15px;
        margin-bottom: 20px;
    }
    .featureHeader {
        font-size: 18px;
        font-weight: bold;
        margin-bottom: 5px;
    }
    .featureDetails {
        color: #b5becc;
        margin-bottom: 5px;
    }
    .featureButton {
        font-weight: bold;
        margin-top:10px;
        background-color: #3B82F6;
        color: white;
        border-radius: 5px;
        font-weight: bold; /* Needed for iOS, so the button text isn't bold. */
        transition: 0.5s;
        padding: 20px;
        padding-top:13px;
        padding-bottom:13px;
        text-align: center;
        /* Disable select for all buttons */
        user-select: none; /* supported by Chrome and Opera */
        -webkit-user-select: none; /* Safari */
        -khtml-user-select: none; /* Konqueror HTML */
        -moz-user-select: none; /* Firefox */
        -ms-user-select: none; /* Internet Explorer/Edge */
    }
    .featureButton:hover {
        background-color: #547DEB;
        cursor:pointer;
    }
    .pinkFeatureButton {
        background-color: #A855F7;
    }
    .featureButton:hover {
        background-color: #c689ff;
    }
    .switch {
        position: relative;
        display: inline-block;
        width: 50px;
        height: 27px;
        margin-bottom: 0px;
        margin-left: 10px;
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
        background-color: #6F6F6F;
        -webkit-transition: .4s;
        transition: .4s;
        border-radius: 34px;
    }
        .slider:before {
            position: absolute;
            content: "";
            height: 19px;
            width: 19px;
            left: 5px;
            bottom: 4px;
            background-color: white;
            -webkit-transition: .4s;
            transition: .4s;
            border-radius: 50%;
        }
    /* Can be applied to the "slider" span to show a disable state. */
    .sliderDisabled:before {
        background-color: #2A2C30;
        cursor:not-allowed;
    }
    input:checked + .slider {
        background-color: #7299ff;
    }
    input:focus + .slider {
        box-shadow: 0 0 1px #7299ff;
    }
    input:checked + .slider:before {
        -webkit-transform: translateX(21px);
        -ms-transform: translateX(21px);
        transform: translateX(21px);
    }
    .prepRow {
        display: flex;
        align-items: flex-start;
        margin-top: 10px;
    }
    .prepDot {
        width: 9px;
        height: 9px;
        border-radius: 50%;
        margin-right: 8px;
        margin-top: 6px;
        flex-shrink: 0;
    }
    .prepInput {
        width: 100%;
        box-sizing: border-box;
        margin-top: 10px;
        padding: 10px;
        border-radius: 5px;
        border: 1px solid #6F6F6F;
        background-color: #1C1C1C;
        color: white;
        font-family: inherit;
        letter-spacing: 2px;
    }
    /* Dopo le regole generiche del pulsante, altrimenti a parita' di specificita' vincerebbero
       loro e il pulsante distruttivo resterebbe blu come tutti gli altri. */
    .redFeatureButton {
        background-color: #B4232A;
    }
    .redFeatureButton:hover {
        background-color: #F05252;
    }
</style>
</head>
<body style="background-color: black; color: white; font-family: Roboto,Noto,Noto Sans,sans-serif;">
<div style="display: flex; align-content: center; justify-content: center; margin-top: 30px">
    <div style="background-color:#1C1C1C; border-radius: 5px; padding: 25px; min-width: 300px; max-width:450px">
        <div style="display: flex; justify-content: center; margin-bottom: 20px;">
            <div style="display: flex; flex-direction: column; align-items: center;">
                <div style="display: flex; justify-content: center; font-size: 28px; margin-bottom:10px; margin-top:10px">
                    <!-- this must target open blank or it won't open properly! -->
                    <a href="https://sweetplace.me" target="_blank" class="whiteLink">Sweetplace</a>
                </div>
            </div>
        </div>

        <div>
            <div style="display: flex; justify-content: center; align-items: baseline; margin-bottom:20px;">
                <div style="width:10px; height:10px; background-color:"""+statusColor+"""; border-radius:50%; margin-right:5px;"></div>
                <div style="margin-bottom:5px; text-align: center; color:"""+statusColor+"""; font-weight: bold;">
                    """+statusText+"""
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Configura il tuo impianto</div>
                    <div class="featureDetails">
                        Registra l'hub, imposta la posizione e gestisci gli utenti di casa.
                        Il tuo dispositivo e' gia' riconosciuto: ti serve solo la tua email.
                    </div>
                </div>
                <div class="pinkFeatureButton featureButton" id="goToOnboarding">
                    Apri la configurazione
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Identificativo hardware</div>
                    <div class="featureDetails" style="word-break: break-all;">"""+escape(macText)+"""</div>
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Indirizzo pubblico</div>
                    <div class="featureDetails" style="word-break: break-all;">"""+publicUrlHtml+"""</div>
                </div>
            </div>

            <div class="featureHolder">
                <details id="prepDetails">
                    <summary class="featureHeader" style="cursor:pointer;">Preparazione immagine</summary>
                    <div style="margin-top:8px; font-weight:bold; color:"""+prepSummaryColor+""";">
                        """+prepSummaryText+"""
                    </div>
                    """+prepRows+"""
                    <div class="featureDetails" style="margin-top:15px;">
                        Azzera identita' Sweetlink, vincolo hardware e chiave NetBird, poi ferma
                        l'add-on. Serve prima di clonare il disco su altri hub. Non si annulla.
                    </div>
                    <input id="prepConfirm" class="prepInput" type="text" autocomplete="off" spellcheck="false" placeholder="scrivi AZZERA per confermare">
                    <div class="featureButton redFeatureButton" id="prepButton">
                        Azzera e prepara la clonazione
                    </div>
                    <div id="prepResult" class="featureDetails" style="margin-top:12px; white-space: pre-wrap; word-break: break-word;"></div>
                </details>
            </div>
        </div>
    </div>
</div>
<script>
    // Wait for the document to be ready.
    (function() {
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
        if(prepButton && prepConfirm && prepResult)
        {
            prepButton.onclick = () => {
                const parola = prepConfirm.value.trim().toUpperCase();
                if(parola !== "AZZERA")
                {
                    prepResult.textContent = "Scrivi AZZERA nel campo qui sopra per confermare.";
                    return;
                }
                prepButton.textContent = "Azzeramento in corso...";
                // Il percorso e' relativo alla pagina: sotto l'Ingress la base cambia a ogni
                // sessione, quindi non si puo' scrivere un percorso assoluto.
                const base = window.location.pathname.endsWith("/") ? window.location.pathname : window.location.pathname + "/";
                fetch(base + "factory-reset", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ confirm: parola })
                })
                .then((r) => r.json())
                .then((d) => {
                    if(d.error)
                    {
                        prepResult.textContent = "Errore: " + d.error;
                        prepButton.textContent = "Azzera e prepara la clonazione";
                        return;
                    }
                    const righe = (d.actions || []).map((a) => a.level.toUpperCase() + "  " + a.title + ": " + a.detail);
                    const bloccato = (d.actions || []).some((a) => a.level === "block");
                    prepResult.textContent = righe.join("\\n");
                    prepButton.textContent = bloccato ? "Con errori: leggi qui sotto, NON clonare" : "Fatto. Ora spegni l'apparecchio.";
                })
                .catch((e) => {
                    // L'add-on si ferma da solo subito dopo l'azzeramento: la richiesta puo'
                    // morire a meta' proprio perche' e' andata a buon fine.
                    prepResult.textContent = "Risposta non ricevuta (" + e + "). L'add-on potrebbe essersi gia' fermato: controlla i log prima di clonare.";
                    prepButton.textContent = "Azzera e prepara la clonazione";
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
