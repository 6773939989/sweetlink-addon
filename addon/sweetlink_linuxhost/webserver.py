import os
import time
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional

from sweetlink.commandhandler import CommandHandler
from sweetlink.interfaces import IAccountLinkStatusUpdateHandler
from sweetlink.sentry import Sentry
from sweetlink.Proto.AddonTypes import AddonTypes

from .config import Config


# Creates a simple web server for users to interact with the plugin from the Home Assistant UI.
class WebServer(IAccountLinkStatusUpdateHandler):

    # A static instance var for the handler class to access this class.
    Instance:"WebServer" = None # type: ignore[reportClassAttributeMissing]

    def __init__(self, logger:logging.Logger, pluginId:str, config:Config, devConfig:Optional[Dict[str,Any]], onboardBaseUrl:str, primaryMac:str) -> None:
        WebServer.Instance = self
        self.Logger = logger
        self.PluginId = pluginId
        self.Config = config
        # Il claim si chiude sul portale Sweetplace: il pannello ci manda il cliente con il MAC
        # gia' compilato, cosi' l'unica cosa che deve inserire e' la propria email.
        self.OnboardBaseUrl = onboardBaseUrl
        self.PrimaryMac = primaryMac
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


        def do_POST(self):
            # Check if the IP is allowed.
            if not self._isAllowedIp():
                self.send_response(401)
                self.end_headers()
                return

            # Il pannello non espone piu' nessuna azione che scriva sul server: l'unica rotta
            # POST che esisteva serviva all'interruttore dell'accesso remoto, ora rimosso.
            # L'accesso remoto resta governato dall'opzione di configurazione, letta all'avvio
            # in linuxhost.py.
            self.send_response(404)
            self.end_headers()


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
            onboardUrl = f"{WebServer.Instance.OnboardBaseUrl}/?mac={WebServer.Instance.PrimaryMac}"
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
                    <div class="featureDetails" style="word-break: break-all;">"""+WebServer.Instance.PrimaryMac+"""</div>
                </div>
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
        if("""+connectingTimerBool+""")
        {
            setInterval(()=> {location.reload();}, 1000)
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
