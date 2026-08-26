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
                     claimUrl:Optional[str] = None) -> None:
        if isinstance(claimCode, str) and len(claimCode) > 0:
            self.ClaimCode = claimCode
        if isinstance(claimStatus, str) and len(claimStatus) > 0:
            self.ClaimStatus = claimStatus
        if isinstance(claimUrl, str) and claimUrl.startswith("https://"):
            # Solo https: questo indirizzo finisce dentro un QR che verra' stampato e messo in
            # mano a un cliente, e un'etichetta sbagliata non si corregge da remoto.
            self.ClaimUrl = claimUrl


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
            levelColors = {ImagePrep.c_LevelOk: "var(--success-color)",
                           ImagePrep.c_LevelWarn: "var(--warning-color)",
                           ImagePrep.c_LevelBlock: "var(--error-color)"}
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
            # Costruito qui e non dentro il modello: sono tre casi distinti e infilarli in
            # un'espressione dentro l'HTML renderebbe illeggibili entrambi.
            if codiceTesto is None:
                codiceHtml = ('<div class="featureDetails">In attesa della prima registrazione '
                              'sul backend.</div>')
            elif claimFatto:
                codiceHtml = ('<div class="fieldValue">' + codiceTesto + '</div>'
                              '<div class="featureDetails" style="margin-top:var(--ha-space-2);">'
                              'Questo hub e\' gia\' stato rivendicato: il codice non serve piu\'.'
                              '</div>')
            else:
                dominio = escape(claimUrl.split("/c/")[0].replace("https://", "")) if claimUrl else ""
                qrHtml = ('<div class="qrBox">' + qrSvg + '</div>') if qrSvg else ''
                codiceHtml = (qrHtml
                              + '<div class="fieldValue">' + codiceTesto + '</div>'
                              '<div class="featureDetails" style="margin-top:var(--ha-space-2);">'
                              'Stampa QR e codice sull\'etichetta dell\'apparecchio. Il cliente '
                              'inquadra il QR, oppure digita il codice su <b>' + dominio + '</b>: '
                              'senza, non puo\' rivendicare l\'hub nessun altro.</div>')

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

    .pageWrap {
        display: flex;
        justify-content: center;
        padding: var(--ha-space-6) var(--ha-space-4);
    }
    .panel {
        width: 100%;
        max-width: 480px;
    }

    .brand {
        text-align: center;
        font-size: var(--ha-font-size-xl);
        font-weight: var(--ha-font-weight-medium);
        line-height: var(--ha-line-height-condensed);
        margin-bottom: var(--ha-space-4);
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

    /* Angoli a pillola come i pulsanti di Home Assistant, e testo in medium senza maiuscole:
       il maiuscolo forzato e' stato tolto da Lovelace da parecchie versioni. */
    .featureButton {
        margin-top: var(--ha-space-3);
        background-color: var(--primary-color);
        color: var(--text-primary-color);
        border-radius: var(--ha-border-radius-pill);
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
    .qrBox {
        display: inline-block;
        background-color: #ffffff;
        border-radius: var(--ha-border-radius-sm);
        line-height: 0;
        margin-bottom: var(--ha-space-3);
    }
    .qrBox svg {
        display: block;
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
        width: 100%;
        box-sizing: border-box;
        margin-top: var(--ha-space-3);
        padding: var(--ha-space-3);
        border-radius: var(--ha-border-radius-sm);
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
</style>
</head>
<body>
<div class="pageWrap">
    <div class="panel">
        <div class="brand">
            <!-- this must target open blank or it won't open properly! -->
            <a href="https://sweetplace.me" target="_blank" class="whiteLink">Sweetplace</a>
        </div>

        <div>
            <div class="statusRow" style="color:"""+statusColor+""";">
                <div class="statusDot" style="background-color:"""+statusColor+""";"></div>
                <div>"""+statusText+"""</div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Configura il tuo impianto</div>
                    <div class="featureDetails">
                        Registra l'hub, imposta la posizione e gestisci gli utenti di casa.
                        Il tuo dispositivo e' gia' riconosciuto: ti serve solo la tua email.
                    </div>
                </div>
                <div class="featureButton" id="goToOnboarding">
                    Apri la configurazione
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Identificativo hardware</div>
                    <div class="fieldValue">"""+escape(macText)+"""</div>
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Indirizzo pubblico</div>
                    <div class="fieldValue">"""+publicUrlHtml+"""</div>
                </div>
            </div>

            <div class="featureHolder">
                <div>
                    <div class="featureHeader">Codice di rivendicazione</div>
                    """+codiceHtml+"""
                </div>
            </div>

            <div class="featureHolder">
                <details id="prepDetails">
                    <summary class="featureHeader">Preparazione immagine</summary>
                    <div class="prepSummary" style="color:"""+prepSummaryColor+""";">
                        """+prepSummaryText+"""
                    </div>
                    """+prepRows+"""
                    <div class="prepDanger">
                        Azzera identita' Sweetlink, vincolo hardware e chiave NetBird, poi ferma
                        l'add-on. Serve prima di clonare il disco su altri hub. Non si annulla.
                    </div>
                    <input id="prepConfirm" class="prepInput" type="text" autocomplete="off" spellcheck="false" placeholder="scrivi AZZERA per confermare">
                    <div class="featureButton redFeatureButton" id="prepButton">
                        Azzera e prepara la clonazione
                    </div>
                    <div id="prepResult" class="featureDetails" style="margin-top:var(--ha-space-3); white-space: pre-wrap; word-break: break-word;"></div>
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
